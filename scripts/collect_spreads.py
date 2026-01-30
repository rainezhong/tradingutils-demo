#!/usr/bin/env python3
"""
Spread Data Collector

Continuously collects spread pair quotes and stores them for backtesting.

Usage:
    python scripts/collect_spreads.py                    # Collect every 60s
    python scripts/collect_spreads.py --interval 30     # Collect every 30s
    python scripts/collect_spreads.py --once            # Collect once and exit
    python scripts/collect_spreads.py --discover        # Auto-discover pairs
    python scripts/collect_spreads.py --stats           # Show database stats

Data is stored in: data/spreads.db
"""

import sys
import os
import argparse
import signal
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_header(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Spread Data Collector")
    parser.add_argument("--interval", type=int, default=60, help="Collection interval in seconds")
    parser.add_argument("--once", action="store_true", help="Collect once and exit")
    parser.add_argument("--discover", action="store_true", help="Auto-discover pairs from parlays")
    parser.add_argument("--db", type=str, default="data/spreads.db", help="Database path")
    parser.add_argument("--stats", action="store_true", help="Show database stats and exit")
    parser.add_argument("--list", action="store_true", help="List tracked pairs and exit")

    args = parser.parse_args()

    # Stats only
    if args.stats:
        from arb.spread_collector import get_collection_stats
        print_header("DATABASE STATISTICS")
        stats = get_collection_stats(args.db)
        print(f"  Pairs tracked: {stats['num_pairs']}")
        print(f"  Total snapshots: {stats['num_snapshots']}")
        print(f"  First snapshot: {stats['first_snapshot']}")
        print(f"  Last snapshot: {stats['last_snapshot']}")
        return

    # List pairs
    if args.list:
        from arb.spread_collector import list_collected_pairs
        print_header("TRACKED PAIRS")
        pairs = list_collected_pairs(args.db)
        if not pairs:
            print("  No pairs in database yet.")
        else:
            for p in pairs:
                print(f"  {p['pair_id']}")
                print(f"    Event: {p['event_title']}")
                print(f"    Type: {p['match_type']}")
                print()
        return

    # Collect mode
    print_header("SPREAD DATA COLLECTOR")
    print(f"Database: {args.db}")
    print(f"Mode: {'Once' if args.once else f'Continuous (every {args.interval}s)'}")
    print(f"Discovery: {'Auto-discover' if args.discover else 'Known pairs'}")
    print()

    from src.core.api_client import KalshiClient
    from src.core.config import get_config
    from arb.spread_collector import SpreadCollector

    config = get_config()
    client = KalshiClient(config)

    collector = SpreadCollector(
        client,
        db_path=args.db,
        auto_discover=args.discover,
    )

    if args.once:
        print("Collecting snapshots...")
        count = collector.collect_once()
        print(f"Collected {count} snapshots")
        stats = collector.get_stats()
        print(f"Database now has {stats['num_snapshots']} total snapshots")
    else:
        # Continuous collection
        print("Starting continuous collection...")
        print("Press Ctrl+C to stop\n")

        # Handle graceful shutdown
        def signal_handler(sig, frame):
            print("\nStopping collector...")
            collector.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)

        collector.start(interval_seconds=args.interval)

        # Keep main thread alive
        try:
            while True:
                signal.pause()
        except AttributeError:
            # Windows doesn't have signal.pause()
            import time
            while True:
                time.sleep(1)


if __name__ == "__main__":
    main()
