#!/usr/bin/env python3
"""
Live Arbitrage Scanner

Scans Kalshi and Polymarket for real arbitrage opportunities.
Read-only - does not execute trades.

Usage:
    python scripts/scan_live.py              # Scan once
    python scripts/scan_live.py --watch      # Continuous scanning
    python scripts/scan_live.py --verbose    # Show all matched pairs
"""

import sys
import os
import time
import argparse
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")


def print_opportunity(opp, index: int):
    """Print a single opportunity."""
    print(f"  [{index}] {opp.pair.event_description[:60]}")
    print(f"      Type: {opp.opportunity_type}")
    print(f"      BUY:  {opp.buy_platform.value:12} {opp.buy_outcome:3} @ ${opp.buy_price:.3f}")
    print(f"      SELL: {opp.sell_platform.value:12} {opp.sell_outcome:3} @ ${opp.sell_price:.3f}")
    print(f"      Edge: ${opp.net_edge_per_contract:.4f}/contract (gross: ${opp.gross_edge_per_contract:.4f})")
    print(f"      Size: {opp.max_contracts} contracts, ${opp.available_liquidity_usd:.0f} liquidity")
    print(f"      Est. Profit: ${opp.estimated_profit_usd:.2f}")
    print()


def scan_once(verbose: bool = False, min_edge: float = 2.0):
    """Run a single scan for opportunities."""
    from src.exchanges.kalshi import KalshiExchange
    from src.matching import MarketMatcher

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting scan...")

    # Connect to Kalshi
    try:
        kalshi = KalshiExchange()
        print("  Kalshi: Connected")
    except Exception as e:
        print(f"  Kalshi: Failed - {e}")
        return []

    # Try to connect to Polymarket
    poly_markets = []
    try:
        from src.exchanges.polymarket import PolymarketExchange
        poly = PolymarketExchange()
        if poly._initialized:
            print("  Polymarket: Connected")
            # Fetch Poly markets
            try:
                poly_tradable = poly.get_markets(status="active", limit=100)
                poly_markets = [
                    {
                        "token_id": m.ticker,
                        "question": m.title,
                        "category": m.category,
                    }
                    for m in poly_tradable
                ]
                print(f"  Polymarket markets: {len(poly_markets)}")
            except Exception as e:
                print(f"  Polymarket markets: Failed - {e}")
        else:
            print("  Polymarket: Not initialized (missing credentials)")
    except Exception as e:
        print(f"  Polymarket: Failed - {e}")

    # Fetch Kalshi markets
    try:
        kalshi_tradable = kalshi.get_markets(status="open", limit=100)
        kalshi_markets = [
            {
                "ticker": m.ticker,
                "title": m.title,
                "category": m.category,
                "status": m.status,
            }
            for m in kalshi_tradable
        ]
        print(f"  Kalshi markets: {len(kalshi_markets)}")
    except Exception as e:
        print(f"  Kalshi markets: Failed - {e}")
        kalshi_markets = []

    if not kalshi_markets:
        print("\n  No markets available to scan.")
        return []

    if not poly_markets:
        print("\n  Polymarket not available - cannot detect cross-platform arbitrage.")
        print("  To enable Polymarket, set POLYGON_WALLET_PRIVATE_KEY environment variable.")

        # Still show Kalshi markets for reference
        if verbose:
            print("\n  Sample Kalshi markets:")
            for m in kalshi_markets[:10]:
                title = m['title'][:55] + "..." if len(m['title']) > 55 else m['title']
                print(f"    - {m['ticker']}: {title}")
        return []

    # Match markets
    print("\n  Matching markets across platforms...")
    matcher = MarketMatcher()
    pairs = matcher.match_markets(kalshi_markets, poly_markets, min_confidence=0.75)
    print(f"  Found {len(pairs)} matched pairs")

    if verbose and pairs:
        print("\n  Matched pairs:")
        for pair in pairs[:10]:
            print(f"    - {pair.kalshi_ticker} <-> {pair.poly_token_id}")
            print(f"      Confidence: {pair.confidence:.2f}")
            print(f"      Event: {pair.event_description[:50]}...")

    if not pairs:
        print("  No matched pairs found.")
        return []

    # Scan for opportunities
    print("\n  Scanning for arbitrage opportunities...")

    from src.matching import LiveQuoteMarketMatcher
    from arb.spread_detector import SpreadDetector

    # Create live quote matcher
    live_matcher = LiveQuoteMarketMatcher(kalshi, poly, matcher)
    live_matcher._provider._matched_pairs = pairs
    live_matcher._provider._spread_pairs = [
        live_matcher._provider._convert_to_spread_pair(p) for p in pairs
    ]

    # Create detector
    detector = SpreadDetector(
        market_matcher=live_matcher,
        min_edge_cents=min_edge,
        min_liquidity_usd=100.0,
        max_quote_age_ms=30000.0,  # 30 second tolerance
    )

    # Check for opportunities
    opportunities = detector.check_once()

    return opportunities


def main():
    parser = argparse.ArgumentParser(description="Live Arbitrage Scanner")
    parser.add_argument("--watch", action="store_true", help="Continuous scanning")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--interval", type=int, default=30, help="Scan interval in seconds")
    parser.add_argument("--min-edge", type=float, default=2.0, help="Minimum edge in cents")
    args = parser.parse_args()

    print_header("LIVE ARBITRAGE SCANNER")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Min edge: {args.min_edge} cents")
    print(f"Mode: {'Continuous' if args.watch else 'Single scan'}")

    if args.watch:
        print(f"Interval: {args.interval} seconds")
        print("\nPress Ctrl+C to stop\n")

        scan_count = 0
        total_opportunities = 0

        try:
            while True:
                scan_count += 1
                print(f"\n--- Scan #{scan_count} ---")

                opportunities = scan_once(args.verbose, args.min_edge)

                if opportunities:
                    total_opportunities += len(opportunities)
                    print_header(f"FOUND {len(opportunities)} OPPORTUNITIES!")
                    for i, opp in enumerate(opportunities, 1):
                        print_opportunity(opp, i)
                else:
                    print("  No opportunities found this scan.")

                print(f"\n  Total opportunities found: {total_opportunities}")
                print(f"  Next scan in {args.interval} seconds...")
                time.sleep(args.interval)

        except KeyboardInterrupt:
            print(f"\n\nStopped after {scan_count} scans.")
            print(f"Total opportunities found: {total_opportunities}")
    else:
        opportunities = scan_once(args.verbose, args.min_edge)

        if opportunities:
            print_header(f"FOUND {len(opportunities)} OPPORTUNITIES!")
            for i, opp in enumerate(opportunities, 1):
                print_opportunity(opp, i)
        else:
            print("\n  No opportunities found.")

        print(f"\n  Scan completed at {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
