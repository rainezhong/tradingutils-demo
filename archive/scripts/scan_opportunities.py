#!/usr/bin/env python3
"""
Opportunity Scanner - Find high-spread, low-volume markets

Scans for markets matching criteria:
- High spread (configurable, default 5c+)
- Low volume (configurable, default <1000)
- Optionally filter by sport/event type

Outputs to:
- Console (live updates)
- Dashboard (via state aggregator)
- JSON file (for analysis)

Usage:
    python scripts/scan_opportunities.py                    # Scan once
    python scripts/scan_opportunities.py --watch            # Continuous
    python scripts/scan_opportunities.py --min-spread 10    # 10c+ spread
    python scripts/scan_opportunities.py --max-volume 500   # <500 volume
    python scripts/scan_opportunities.py --dashboard        # Push to dashboard
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalshi_utils.client_wrapper import KalshiWrapped


@dataclass
class MarketOpportunity:
    """A market opportunity matching scan criteria."""

    ticker: str
    event_ticker: str
    spread_cents: int
    volume: int
    yes_bid: int
    yes_ask: int
    mid_price: float
    category: str  # 'nba_totals', 'ncaab', etc.
    volatility_score: float  # spread / mid_price
    scanned_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScanResult:
    """Result of a market scan."""

    timestamp: str
    total_markets_scanned: int
    opportunities_found: int
    opportunities: List[MarketOpportunity]
    scan_duration_ms: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_markets_scanned": self.total_markets_scanned,
            "opportunities_found": self.opportunities_found,
            "opportunities": [o.to_dict() for o in self.opportunities],
            "scan_duration_ms": self.scan_duration_ms,
        }


class OpportunityScanner:
    """Scans markets for trading opportunities."""

    def __init__(
        self,
        min_spread_cents: int = 5,
        max_volume: int = 1000,
        min_volatility: float = 0.0,
    ):
        self.min_spread_cents = min_spread_cents
        self.max_volume = max_volume
        self.min_volatility = min_volatility
        self.wrapper = KalshiWrapped()

    def scan(self) -> ScanResult:
        """Run a scan and return results."""
        start = time.time()
        timestamp = datetime.now().isoformat()

        opportunities = []
        total_scanned = 0

        # Scan NBA Totals
        nba_opps, nba_count = self._scan_nba_totals()
        opportunities.extend(nba_opps)
        total_scanned += nba_count

        # Scan NBA Winner markets
        nba_winner_opps, nba_winner_count = self._scan_nba_winners()
        opportunities.extend(nba_winner_opps)
        total_scanned += nba_winner_count

        # Sort by spread descending
        opportunities.sort(key=lambda x: -x.spread_cents)

        duration_ms = (time.time() - start) * 1000

        return ScanResult(
            timestamp=timestamp,
            total_markets_scanned=total_scanned,
            opportunities_found=len(opportunities),
            opportunities=opportunities,
            scan_duration_ms=duration_ms,
        )

    def _scan_nba_totals(self) -> tuple[List[MarketOpportunity], int]:
        """Scan NBA totals markets."""
        opportunities = []

        try:
            markets = self.wrapper.GetAllNBATotalMarkets(status="open")
        except Exception as e:
            print(f"Error scanning NBA totals: {e}")
            return [], 0

        for m in markets:
            opp = self._evaluate_market(m, "nba_totals")
            if opp:
                opportunities.append(opp)

        return opportunities, len(markets)

    def _scan_nba_winners(self) -> tuple[List[MarketOpportunity], int]:
        """Scan NBA winner markets."""
        opportunities = []

        try:
            markets = self.wrapper.GetAllNBAMarkets(status="open")
        except Exception as e:
            print(f"Error scanning NBA winners: {e}")
            return [], 0

        for m in markets:
            opp = self._evaluate_market(m, "nba_winner")
            if opp:
                opportunities.append(opp)

        return opportunities, len(markets)

    def _evaluate_market(self, market, category: str) -> Optional[MarketOpportunity]:
        """Evaluate a single market against criteria."""
        ticker = getattr(market, "ticker", "")
        event_ticker = getattr(market, "event_ticker", "")
        yes_bid = getattr(market, "yes_bid", 0) or 0
        yes_ask = getattr(market, "yes_ask", 0) or 0
        volume = getattr(market, "volume", 0) or 0

        if yes_bid <= 0 or yes_ask <= 0:
            return None

        spread = yes_ask - yes_bid
        mid = (yes_bid + yes_ask) / 2
        volatility = spread / mid if mid > 0 else 0

        # Check criteria
        if spread < self.min_spread_cents:
            return None
        if volume > self.max_volume:
            return None
        if volatility < self.min_volatility:
            return None

        return MarketOpportunity(
            ticker=ticker,
            event_ticker=event_ticker,
            spread_cents=spread,
            volume=volume,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            mid_price=mid,
            category=category,
            volatility_score=round(volatility, 3),
            scanned_at=datetime.now().isoformat(),
        )


def print_results(result: ScanResult, verbose: bool = False):
    """Print scan results to console."""
    print(f"\n{'=' * 80}")
    print(f"  OPPORTUNITY SCAN - {result.timestamp[:19]}")
    print(
        f"  Scanned: {result.total_markets_scanned} markets in {result.scan_duration_ms:.0f}ms"
    )
    print(f"  Found: {result.opportunities_found} opportunities")
    print(f"{'=' * 80}\n")

    if not result.opportunities:
        print("  No opportunities found matching criteria.\n")
        return

    print(f"{'Ticker':<50} {'Bid':>4} {'Ask':>4} {'Sprd':>5} {'Vol':>6} {'Vol%':>6}")
    print("-" * 80)

    for opp in result.opportunities[:30]:  # Top 30
        vol_pct = f"{opp.volatility_score:.1%}"
        print(
            f"{opp.ticker:<50} {opp.yes_bid:>4} {opp.yes_ask:>4} "
            f"{opp.spread_cents:>4}c {opp.volume:>6} {vol_pct:>6}"
        )

    if len(result.opportunities) > 30:
        print(f"\n  ... and {len(result.opportunities) - 30} more")

    print()


def push_to_dashboard(result: ScanResult):
    """Push results to the dashboard state aggregator."""
    try:
        from dashboard.state import state_aggregator

        # Format for dashboard
        dashboard_data = {
            "timestamp": result.timestamp,
            "scan_duration_ms": result.scan_duration_ms,
            "opportunities": [o.to_dict() for o in result.opportunities[:50]],
            "summary": {
                "total_scanned": result.total_markets_scanned,
                "found": result.opportunities_found,
                "top_spread": result.opportunities[0].spread_cents
                if result.opportunities
                else 0,
            },
        }

        # Publish to dashboard
        state_aggregator.publish_scanner_update(dashboard_data)
        print(
            f"[Dashboard] Pushed {result.opportunities_found} opportunities (top spread: {dashboard_data['summary']['top_spread']}c)"
        )

    except ImportError:
        print("[Dashboard] State aggregator not available - saving to file instead")
        save_results(result)
    except Exception as e:
        print(f"[Dashboard] Error: {e} - saving to file instead")
        save_results(result)


def save_results(result: ScanResult, output_dir: str = "data/scanner"):
    """Save results to JSON file."""
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = Path(output_dir) / f"scan_{timestamp}.json"

    with open(filepath, "w") as f:
        json.dump(result.to_dict(), f, indent=2)

    print(f"[Saved] {filepath}")

    # Also save latest.json for easy access
    latest_path = Path(output_dir) / "latest.json"
    with open(latest_path, "w") as f:
        json.dump(result.to_dict(), f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Opportunity Scanner")
    parser.add_argument(
        "--min-spread", type=int, default=5, help="Minimum spread in cents (default: 5)"
    )
    parser.add_argument(
        "--max-volume", type=int, default=1000, help="Maximum volume (default: 1000)"
    )
    parser.add_argument(
        "--min-volatility",
        type=float,
        default=0.0,
        help="Minimum volatility score (default: 0)",
    )
    parser.add_argument(
        "--watch", "-w", action="store_true", help="Continuous scanning"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Scan interval in seconds for watch mode (default: 30)",
    )
    parser.add_argument(
        "--dashboard", "-d", action="store_true", help="Push results to dashboard"
    )
    parser.add_argument(
        "--save", "-s", action="store_true", help="Save results to JSON file"
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="Minimal output")

    args = parser.parse_args()

    scanner = OpportunityScanner(
        min_spread_cents=args.min_spread,
        max_volume=args.max_volume,
        min_volatility=args.min_volatility,
    )

    print("Opportunity Scanner")
    print(f"  Min spread: {args.min_spread}c")
    print(f"  Max volume: {args.max_volume}")
    if args.watch:
        print(f"  Mode: Continuous (every {args.interval}s)")

    try:
        while True:
            result = scanner.scan()

            if not args.quiet:
                print_results(result)
            else:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"Found {result.opportunities_found} opportunities"
                )

            if args.dashboard:
                push_to_dashboard(result)

            if args.save:
                save_results(result)

            if not args.watch:
                break

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n\nScanner stopped.")


if __name__ == "__main__":
    main()
