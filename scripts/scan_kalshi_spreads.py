#!/usr/bin/env python3
"""
Kalshi Single-Exchange Spread Scanner

Scans Kalshi for complementary market pairs (e.g., sports games with
"Team A wins" and "Team B wins" as separate markets) and identifies
spread trading opportunities.

Usage:
    python scripts/scan_kalshi_spreads.py              # Scan and print pairs
    python scripts/scan_kalshi_spreads.py --sports     # Sports pairs only
    python scripts/scan_kalshi_spreads.py --monitor N  # Monitor pair N with live plot
    python scripts/scan_kalshi_spreads.py --watch      # Continuous scanning
    python scripts/scan_kalshi_spreads.py --min-edge 1 # Only show >= 1 cent edge
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


def scan_once(args) -> tuple:
    """Run a single scan. Returns (pairs, opportunities)."""
    from src.core.api_client import KalshiClient
    from src.core.config import get_config
    from arb.kalshi_scanner import (
        KalshiSpreadScanner,
        quick_scan,
        full_scan,
        discover_complementary_pairs,
        get_all_known_pairs,
        get_todays_nba_games,
        get_todays_nhl_games,
        get_todays_college_basketball,
        get_nfl_playoffs,
    )

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Connecting to Kalshi...")

    try:
        config = get_config()
        client = KalshiClient(config)
        print("  Kalshi: Connected")
    except Exception as e:
        print(f"  Kalshi: Failed - {e}")
        return [], []

    scanner = KalshiSpreadScanner(client, min_volume=args.min_volume)

    # Determine scan mode
    if args.discover:
        # Auto-discover pairs from parlay markets
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Auto-discovering pairs...")
        print("  (This scans parlay markets to find simple game tickers)")
        ticker_pairs = discover_complementary_pairs(client, max_pages=5, delay=1.0)
        print(f"  Discovered {len(ticker_pairs)} potential pairs")
    else:
        # Quick scan with known tickers
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Using known ticker pairs...")
        if args.sports:
            # Only game winner markets
            ticker_pairs = []
            ticker_pairs.extend(get_todays_nba_games())
            ticker_pairs.extend(get_todays_nhl_games())
            ticker_pairs.extend(get_todays_college_basketball())
            ticker_pairs.extend(get_nfl_playoffs())
        else:
            ticker_pairs = get_all_known_pairs()
        print(f"  {len(ticker_pairs)} known pairs to scan")

    if not ticker_pairs:
        print("  No pairs to scan.")
        return [], []

    # Fetch quotes for pairs
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching quotes...")
    print(f"  (Rate limited - ~2 sec per pair)")
    pairs = scanner.scan_known_pairs(ticker_pairs, delay_seconds=2.0)
    print(f"  Got quotes for {len(pairs)} pairs")

    # Filter valid pairs
    valid_pairs = [p for p in pairs if p.combined_yes_ask is not None and p.combined_yes_ask < 1.5]
    print(f"  {len(valid_pairs)} pairs with valid quotes")

    # Scan for opportunities
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Analyzing opportunities...")
    opportunities = scanner.scan_opportunities(
        pairs=valid_pairs,
        min_edge_cents=args.min_edge,
        contract_size=args.contract_size,
    )

    return valid_pairs, opportunities


def print_pairs(pairs, limit: int = 20):
    """Print discovered pairs."""
    print_header("COMPLEMENTARY MARKET PAIRS")

    if not pairs:
        print("  No complementary pairs found.\n")
        return

    for i, pair in enumerate(pairs[:limit], 1):
        edge = pair.dutch_book_edge
        edge_str = f"${edge:+.4f}" if edge is not None else "N/A"
        combined = pair.combined_yes_ask
        combined_str = f"${combined:.2f}" if combined is not None else "N/A"

        print(f"[{i}] {pair.event_title}")
        print(f"    Market A: {pair.market_a.ticker}")
        print(f"             {pair.market_a.title[:60]}")
        if pair.market_a.yes_ask:
            print(f"             YES: bid={pair.market_a.yes_bid:.2f} ask={pair.market_a.yes_ask:.2f}")
        print(f"    Market B: {pair.market_b.ticker}")
        print(f"             {pair.market_b.title[:60]}")
        if pair.market_b.yes_ask:
            print(f"             YES: bid={pair.market_b.yes_bid:.2f} ask={pair.market_b.yes_ask:.2f}")
        print(f"    Type: {pair.match_type}, Confidence: {pair.confidence:.2f}")
        print(f"    Combined YES ask: {combined_str}, Dutch edge: {edge_str}")
        print()

    if len(pairs) > limit:
        print(f"  ... and {len(pairs) - limit} more pairs\n")


def print_opportunities(opportunities, limit: int = 10):
    """Print opportunities."""
    print_header("SPREAD OPPORTUNITIES")

    if not opportunities:
        print("  No opportunities above threshold.\n")
        return

    for i, opp in enumerate(opportunities[:limit], 1):
        pair = opp["pair"]
        print(f"[{i}] {pair['event_title']}")
        print(f"    Tickers: {pair['market_a']['ticker']} vs {pair['market_b']['ticker']}")
        print(f"    Combined cost: ${opp['combined_cost']:.4f}")
        print(f"    Dutch profit: ${opp['dutch_profit_per_contract']:.4f}/contract")
        print(f"    Best edge: {opp['best_edge_cents']:.2f} cents/contract")
        print(f"    Leg 1: {opp['t1_via']} (cost: ${opp['t1_cost']:.4f})")
        print(f"    Leg 2: {opp['t2_via']} (cost: ${opp['t2_cost']:.4f})")
        print(f"    ACTION: {opp['recommended_action']}")
        print()


def monitor_pair(pairs, index: int, args):
    """Start monitoring a specific pair."""
    if index < 1 or index > len(pairs):
        print(f"Invalid pair index. Choose 1-{len(pairs)}")
        return

    pair = pairs[index - 1]
    print_header(f"MONITORING: {pair.event_title}")
    print(f"  Market A: {pair.market_a.ticker}")
    print(f"  Market B: {pair.market_b.ticker}")
    print(f"  Poll interval: {args.poll_interval}ms")
    print(f"  Contract size: {args.contract_size}")
    print("\n  Press Ctrl+C to stop\n")

    from src.core.api_client import KalshiClient
    from src.core.config import get_config
    from arb.kalshi_scanner import KalshiSpreadScanner

    config = get_config()
    client = KalshiClient(config)
    scanner = KalshiSpreadScanner(client)

    try:
        monitor, fig, ani = scanner.monitor_pair(
            pair,
            poll_period_ms=args.poll_interval,
            contract_size=args.contract_size,
            entry_maker=False,
            exit_maker=False,
            min_edge=args.min_edge / 100.0,  # Convert cents to dollars
            arb_floor=0.002,
            profit_floor=0.002,
            plot=True,
        )
    except KeyboardInterrupt:
        print("\nStopped monitoring.")


def main():
    parser = argparse.ArgumentParser(description="Kalshi Single-Exchange Spread Scanner")
    parser.add_argument("--sports", action="store_true", help="Only scan sports game winners")
    parser.add_argument("--discover", action="store_true", help="Auto-discover pairs (slower)")
    parser.add_argument("--quick", action="store_true", help="Quick scan with known tickers (default)")
    parser.add_argument("--monitor", type=int, metavar="N", help="Monitor pair N with live plot")
    parser.add_argument("--watch", action="store_true", help="Continuous scanning mode")
    parser.add_argument("--interval", type=int, default=60, help="Scan interval in seconds (for --watch)")
    parser.add_argument("--poll-interval", type=int, default=500, help="Poll interval in ms (for --monitor)")
    parser.add_argument("--min-edge", type=float, default=0.0, help="Minimum edge in cents")
    parser.add_argument("--min-volume", type=int, default=0, help="Minimum 24h volume")
    parser.add_argument("--contract-size", type=int, default=100, help="Contract size for fee calculations")
    parser.add_argument("--limit", type=int, default=20, help="Max pairs to display")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    print_header("KALSHI SPREAD SCANNER")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: {'Sports only' if args.sports else 'All markets'}")
    print(f"Min edge: {args.min_edge} cents")
    if args.min_volume > 0:
        print(f"Min volume: {args.min_volume}")

    # Single scan
    pairs, opportunities = scan_once(args)

    if not pairs:
        print("\n  No pairs found. Check your API credentials.\n")
        return

    # Print results
    print_pairs(pairs, limit=args.limit)
    print_opportunities(opportunities, limit=args.limit)

    # Monitor mode
    if args.monitor:
        monitor_pair(pairs, args.monitor, args)
        return

    # Watch mode
    if args.watch:
        print(f"\nContinuous scanning mode. Interval: {args.interval}s")
        print("Press Ctrl+C to stop\n")

        scan_count = 1
        try:
            while True:
                print(f"\n--- Scan #{scan_count + 1} in {args.interval}s ---")
                time.sleep(args.interval)
                scan_count += 1

                pairs, opportunities = scan_once(args)

                if opportunities:
                    print_header(f"FOUND {len(opportunities)} OPPORTUNITIES!")
                    print_opportunities(opportunities, limit=5)
                else:
                    print("  No opportunities this scan.")

        except KeyboardInterrupt:
            print(f"\n\nStopped after {scan_count} scans.")
    else:
        print(f"\nScan completed at {datetime.now().strftime('%H:%M:%S')}")
        if pairs:
            print(f"\nTo monitor a pair: python scripts/scan_kalshi_spreads.py --monitor N")
            print(f"  where N is the pair number (1-{len(pairs)})")


if __name__ == "__main__":
    main()
