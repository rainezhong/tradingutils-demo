#!/usr/bin/env python3
"""
Kalshi Sports Market Discovery Script (Phase 0)

Scans all Kalshi sports series to identify:
1. Which sports have active markets
2. Market liquidity (volume, open interest, spread)
3. Ticker format per sport
4. Which sports are worth targeting for latency arb

Usage:
    python3 scripts/discover_kalshi_sports.py
"""

import sys
import os
import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from core.exchange_client.kalshi import KalshiExchangeClient
except ImportError:
    print("Error: Could not import KalshiExchangeClient")
    print("Make sure KALSHI_EMAIL and KALSHI_PASSWORD are set in your environment")
    sys.exit(1)


@dataclass
class MarketSnapshot:
    """Snapshot of a Kalshi market for analysis."""
    ticker: str
    title: str
    series: str
    status: str
    close_time: datetime
    volume: int
    open_interest: int
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int

    @property
    def spread_cents(self) -> int:
        """Best available spread in cents."""
        return min(self.yes_ask - self.yes_bid, self.no_ask - self.no_bid)

    @property
    def mid_price(self) -> float:
        """Mid price (0-1)."""
        return (self.yes_bid + self.yes_ask) / 200.0

    @property
    def time_to_close_hours(self) -> float:
        """Hours until market closes."""
        now = datetime.now(timezone.utc)
        delta = self.close_time - now
        return delta.total_seconds() / 3600.0

    @property
    def is_live(self) -> bool:
        """Heuristic: is this an in-play market (closes soon)?"""
        # In-play markets typically close within 4 hours
        return 0 < self.time_to_close_hours < 4.0


# Kalshi sports series to scan
SPORTS_SERIES = {
    "KXNBAGAME": "NBA Game Winner",
    "KXNBATOTAL": "NBA Total Points",
    "KXNBASPREAD": "NBA Point Spread",
    "KXNCAAMBGAME": "NCAAB Game Winner",
    "KXNCAAMBTOTAL": "NCAAB Total Points",
    "KXNHLGAME": "NHL Game Winner",
    "KXNFLGAME": "NFL Game Winner",
    "KXMLBGAME": "MLB Game Winner",
    "KXSOCCER": "Soccer Markets",
}


async def fetch_markets(client: KalshiExchangeClient, series: str) -> List[MarketSnapshot]:
    """Fetch all active markets for a given series."""
    markets = []

    try:
        response = await client._request(
            "GET",
            "/markets",
            params={
                "series_ticker": series,
                "status": "open",
                "limit": 100,
            }
        )

        for data in response.get("markets", []):
            try:
                ticker = data.get("ticker", "")
                title = data.get("title", "")
                status = data.get("status", "")

                # Parse close time
                close_time_str = data.get("close_time", data.get("expiration_time", ""))
                if not close_time_str:
                    continue

                try:
                    close_time = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                except ValueError:
                    continue

                # Extract market data
                volume = int(data.get("volume", 0) or 0)
                open_interest = int(data.get("open_interest", 0) or 0)
                yes_bid = int(data.get("yes_bid", 0) or 0)
                yes_ask = int(data.get("yes_ask", 100) or 100)
                no_bid = int(data.get("no_bid", 0) or 0)
                no_ask = int(data.get("no_ask", 100) or 100)

                markets.append(MarketSnapshot(
                    ticker=ticker,
                    title=title,
                    series=series,
                    status=status,
                    close_time=close_time,
                    volume=volume,
                    open_interest=open_interest,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    no_bid=no_bid,
                    no_ask=no_ask,
                ))
            except Exception as e:
                print(f"Warning: Failed to parse market: {e}")
                continue

    except Exception as e:
        print(f"Error fetching {series}: {e}")

    return markets


def print_sport_summary(sport_name: str, markets: List[MarketSnapshot]) -> None:
    """Print summary statistics for a sport."""
    if not markets:
        print(f"\n{'='*80}")
        print(f"{sport_name}: NO ACTIVE MARKETS")
        print(f"{'='*80}\n")
        return

    # Calculate stats
    total_markets = len(markets)
    live_markets = [m for m in markets if m.is_live]
    total_volume = sum(m.volume for m in markets)
    total_oi = sum(m.open_interest for m in markets)
    avg_spread = sum(m.spread_cents for m in markets) / len(markets)

    # Find most liquid market
    most_liquid = max(markets, key=lambda m: m.volume + m.open_interest)

    print(f"\n{'='*80}")
    print(f"{sport_name}")
    print(f"{'='*80}")
    print(f"Total Markets:        {total_markets}")
    print(f"In-Play Markets:      {len(live_markets)} (close within 4 hours)")
    print(f"Total Volume:         {total_volume:,} contracts")
    print(f"Total Open Interest:  {total_oi:,} contracts")
    print(f"Avg Spread:           {avg_spread:.1f}¢")
    print()

    if live_markets:
        print(f"IN-PLAY MARKETS ({len(live_markets)}):")
        print(f"{'Ticker':<35} {'TTX (hrs)':<10} {'Vol':<8} {'OI':<8} {'Spread':<8}")
        print("-" * 80)
        for m in sorted(live_markets, key=lambda x: x.time_to_close_hours):
            print(f"{m.ticker:<35} {m.time_to_close_hours:>9.1f} {m.volume:>7,} {m.open_interest:>7,} {m.spread_cents:>7}¢")
        print()

    print("MOST LIQUID MARKET:")
    print(f"  Ticker:      {most_liquid.ticker}")
    print(f"  Title:       {most_liquid.title}")
    print(f"  Volume:      {most_liquid.volume:,}")
    print(f"  Open Int:    {most_liquid.open_interest:,}")
    print(f"  Spread:      {most_liquid.spread_cents}¢")
    print(f"  Yes Bid/Ask: {most_liquid.yes_bid}¢ / {most_liquid.yes_ask}¢")
    print(f"  No Bid/Ask:  {most_liquid.no_bid}¢ / {most_liquid.no_ask}¢")
    print(f"  TTX:         {most_liquid.time_to_close_hours:.1f} hours")

    # Show ticker format examples (first 3 markets)
    print("\nTICKER FORMAT EXAMPLES:")
    for m in markets[:3]:
        print(f"  {m.ticker}")

    print(f"{'='*80}\n")


def print_overall_summary(all_markets: Dict[str, List[MarketSnapshot]]) -> None:
    """Print overall summary across all sports."""
    print("\n" + "="*80)
    print("OVERALL SUMMARY")
    print("="*80)

    total_markets = sum(len(markets) for markets in all_markets.values())
    total_live = sum(len([m for m in markets if m.is_live]) for markets in all_markets.values())
    total_volume = sum(sum(m.volume for m in markets) for markets in all_markets.values())
    total_oi = sum(sum(m.open_interest for m in markets) for markets in all_markets.values())

    print(f"Total Active Markets:  {total_markets:,}")
    print(f"Total In-Play Markets: {total_live:,}")
    print(f"Total Volume:          {total_volume:,} contracts")
    print(f"Total Open Interest:   {total_oi:,} contracts")
    print()

    print("BY SPORT:")
    print(f"{'Sport':<25} {'Markets':<10} {'In-Play':<10} {'Volume':<12} {'OI':<12}")
    print("-" * 80)

    for series, name in SPORTS_SERIES.items():
        markets = all_markets.get(series, [])
        live = [m for m in markets if m.is_live]
        vol = sum(m.volume for m in markets)
        oi = sum(m.open_interest for m in markets)

        print(f"{name:<25} {len(markets):<10} {len(live):<10} {vol:>11,} {oi:>11,}")

    print()
    print("RECOMMENDATION:")

    # Find sports with in-play markets
    sports_with_live = {
        series: [m for m in markets if m.is_live]
        for series, markets in all_markets.items()
        if any(m.is_live for m in markets)
    }

    if not sports_with_live:
        print("  ⚠️  NO IN-PLAY MARKETS FOUND")
        print("  All markets appear to be futures (close >4 hours away)")
        print("  Latency arb requires in-play markets that settle soon")
    else:
        print("  ✓ Sports with in-play markets:")
        for series, live_markets in sports_with_live.items():
            total_vol = sum(m.volume for m in live_markets)
            avg_spread = sum(m.spread_cents for m in live_markets) / len(live_markets)
            print(f"    - {SPORTS_SERIES[series]}: {len(live_markets)} markets, {total_vol:,} vol, {avg_spread:.1f}¢ avg spread")

        # Priority recommendation
        best_sport = max(
            sports_with_live.items(),
            key=lambda x: sum(m.volume + m.open_interest for m in x[1])
        )
        print(f"\n  🎯 HIGHEST PRIORITY: {SPORTS_SERIES[best_sport[0]]}")
        print(f"     ({len(best_sport[1])} in-play markets, highest liquidity)")

    print("="*80)


async def main():
    """Main discovery script."""
    print("\n" + "="*80)
    print("KALSHI SPORTS MARKET DISCOVERY (Phase 0)")
    print("="*80)
    print()

    # Initialize Kalshi client
    try:
        client = KalshiExchangeClient.from_env()
        await client.connect()
        print("✓ Connected to Kalshi API")
    except Exception as e:
        print(f"✗ Failed to connect to Kalshi: {e}")
        print("\nMake sure KALSHI_EMAIL and KALSHI_PASSWORD are set:")
        print("  export KALSHI_EMAIL='user@example.com'")
        print("  export KALSHI_PASSWORD='your_password'")
        return 1

    try:
        # Scan all sports series
        all_markets: Dict[str, List[MarketSnapshot]] = {}

        for series, name in SPORTS_SERIES.items():
            print(f"\nScanning {name} ({series})...", end=" ", flush=True)
            markets = await fetch_markets(client, series)
            all_markets[series] = markets
            print(f"✓ Found {len(markets)} active markets")

        # Print detailed summaries per sport
        for series, name in SPORTS_SERIES.items():
            markets = all_markets[series]
            print_sport_summary(name, markets)

        # Print overall summary and recommendations
        print_overall_summary(all_markets)

        print("\nNEXT STEP:")
        print("  If any sport has in-play markets, run latency measurement:")
        print("  python3 scripts/measure_kalshi_sports_lag.py --sport <SERIES>")
        print()

    finally:
        await client.exit()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
