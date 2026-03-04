#!/usr/bin/env python3
"""
Depth Snapshot Collector

Periodically fetches full orderbook depth for open markets and appends
to daily JSONL files for replay by backtest_spread_capture.py.

Usage:
    # One-shot collection
    python scripts/collect_depth_snapshots.py --once --sport ncaab -v

    # Continuous collection every 5 minutes
    python scripts/collect_depth_snapshots.py --interval 300 --sport ncaab

    # Custom output directory
    python scripts/collect_depth_snapshots.py --once --output-dir data/depth_snapshots
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_data.client import KalshiPublicClient


# Graceful shutdown
_running = True


def _handle_signal(signum, frame):
    global _running
    _running = False
    print("\nShutting down...")


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


SPORT_SERIES = {
    "nba": "KXNBAGAME",
    "nba_totals": "KXNBATOTAL",
    "ncaab": "KXNCAAMBGAME",
    "nhl": "KXNHLGAME",
    "ucl": "KXUCL",
    "tennis": "KXWTA",
    "soccer": "KXSOCCER",
}


def collect_snapshots(
    client: KalshiPublicClient,
    sport: Optional[str] = None,
    min_volume: int = 0,
    verbose: bool = False,
) -> list:
    """Fetch orderbooks for all matching markets.

    Returns list of snapshot dicts ready for JSONL serialization.
    """
    series_ticker = SPORT_SERIES.get(sport.lower(), None) if sport else None
    markets = client.get_all_markets(
        min_volume=min_volume,
        series_ticker=series_ticker,
    )

    # Client-side fallback if sport didn't match a known series
    if sport and not series_ticker:
        sport_upper = sport.upper()
        markets = [
            m
            for m in markets
            if m.get("ticker", "").upper().startswith(sport_upper)
            or m.get("event_ticker", "").upper().startswith(sport_upper)
            or sport_upper in m.get("ticker", "").upper()
        ]

    if verbose:
        print(f"  Found {len(markets)} markets, fetching orderbooks...")

    ts = datetime.now(timezone.utc).isoformat()
    snapshots = []

    for i, market in enumerate(markets):
        if not _running:
            break

        ticker = market.get("ticker", "")
        if not ticker:
            continue

        try:
            raw = client.get_orderbook(ticker)
            ob = raw.get("orderbook", raw)
            yes_levels = ob.get("yes", [])
            no_levels = ob.get("no", [])

            # Only save if both sides have depth
            if not yes_levels or not no_levels:
                continue

            snapshots.append(
                {
                    "ticker": ticker,
                    "timestamp": ts,
                    "yes": yes_levels,
                    "no": no_levels,
                }
            )

            if verbose and (i + 1) % 25 == 0:
                print(
                    f"    Fetched {i + 1}/{len(markets)} ({len(snapshots)} with depth)"
                )

        except Exception as e:
            if verbose:
                print(f"    Error fetching {ticker}: {e}")

        # Rate limit
        time.sleep(0.15)

    return snapshots


def write_snapshots(snapshots: list[dict], output_dir: str) -> str:
    """Append snapshots to daily JSONL file.

    Returns the path written to.
    """
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = Path(output_dir) / f"{date_str}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "a") as f:
        for snap in snapshots:
            f.write(json.dumps(snap) + "\n")

    return str(path)


def main():
    parser = argparse.ArgumentParser(
        description="Collect orderbook depth snapshots to JSONL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/collect_depth_snapshots.py --once --sport ncaab -v
  python scripts/collect_depth_snapshots.py --interval 300 --sport nba
  python scripts/collect_depth_snapshots.py --once --output-dir data/depth_snapshots
        """,
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect once and exit (default: loop with --interval)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Seconds between collection rounds (default: 300)",
    )
    parser.add_argument(
        "--sport",
        type=str,
        help="Filter markets by sport prefix (e.g. ncaab, nba)",
    )
    parser.add_argument(
        "--min-volume",
        type=int,
        default=0,
        help="Minimum 24h volume (default: 0)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/depth_snapshots",
        help="Output directory for JSONL files (default: data/depth_snapshots)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()
    client = KalshiPublicClient()

    round_num = 0
    while _running:
        round_num += 1
        now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        if args.verbose or round_num == 1:
            print(f"\n[Round {round_num}] {now} - Collecting snapshots...")

        snapshots = collect_snapshots(
            client,
            sport=args.sport,
            min_volume=args.min_volume,
            verbose=args.verbose,
        )

        if snapshots:
            path = write_snapshots(snapshots, args.output_dir)
            print(f"[Round {round_num}] Saved {len(snapshots)} snapshots to {path}")
        else:
            print(f"[Round {round_num}] No snapshots collected")

        if args.once:
            break

        if args.verbose:
            print(f"  Sleeping {args.interval}s until next round...")

        # Sleep in small increments for responsive shutdown
        for _ in range(args.interval):
            if not _running:
                break
            time.sleep(1)

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
