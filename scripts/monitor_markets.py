#!/usr/bin/env python3
"""
Focused Market Monitor

Monitor specific markets you care about, with fast polling.

Usage:
    # NFL playoffs only
    python scripts/monitor_markets.py --nfl

    # Specific tickers
    python scripts/monitor_markets.py TICKER1:TICKER2 TICKER3:TICKER4

    # From a file
    python scripts/monitor_markets.py --file my_markets.txt
"""

import sys
import os
import time
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pre-defined market groups
MARKETS = {
    "nfl": [
        ("KXNFLAFCCHAMP-25-NE", "KXNFLAFCCHAMP-25-DEN"),
        ("KXNFLNFCCHAMP-25-SEA", "KXNFLNFCCHAMP-25-LA"),
    ],
    "nba": [
        ("KXNBAGAME-26JAN21TORSAC-TOR", "KXNBAGAME-26JAN21TORSAC-SAC"),
        ("KXNBAGAME-26JAN22DENWAS-DEN", "KXNBAGAME-26JAN22DENWAS-WAS"),
        ("KXNBAGAME-26JAN21OKCMIL-OKC", "KXNBAGAME-26JAN21OKCMIL-MIL"),
    ],
    "nhl": [
        ("KXNHLGAME-26JAN21ANACOL-COL", "KXNHLGAME-26JAN21ANACOL-ANA"),
        ("KXNHLGAME-26JAN21NYISEA-SEA", "KXNHLGAME-26JAN21NYISEA-NYI"),
    ],
}


def parse_pair(s: str):
    """Parse 'TICKER1:TICKER2' into tuple."""
    parts = s.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid pair format: {s} (expected TICKER1:TICKER2)")
    return (parts[0], parts[1])


def fetch_pair(api, ticker_a, ticker_b):
    """Fetch quotes for a pair."""
    try:
        m1 = api.get_market(ticker_a).get("market", {})
        time.sleep(0.2)
        m2 = api.get_market(ticker_b).get("market", {})

        def to_dollars(v):
            if v is None:
                return None
            return v / 100.0 if v > 1 else float(v)

        a_ask = to_dollars(m1.get("yes_ask"))
        b_ask = to_dollars(m2.get("yes_ask"))

        if a_ask is None or b_ask is None:
            return None

        combined = a_ask + b_ask
        edge = 1.0 - combined

        return {
            "a_ticker": ticker_a,
            "b_ticker": ticker_b,
            "a_name": m1.get("yes_sub_title", ticker_a.split("-")[-1]),
            "b_name": m2.get("yes_sub_title", ticker_b.split("-")[-1]),
            "a_ask": a_ask,
            "b_ask": b_ask,
            "combined": combined,
            "edge": edge,
        }
    except Exception as e:
        return None


def main():
    parser = argparse.ArgumentParser(description="Focused Market Monitor")
    parser.add_argument("pairs", nargs="*", help="Pairs as TICKER1:TICKER2")
    parser.add_argument("--nfl", action="store_true", help="Monitor NFL playoffs")
    parser.add_argument("--nba", action="store_true", help="Monitor NBA games")
    parser.add_argument("--nhl", action="store_true", help="Monitor NHL games")
    parser.add_argument("--file", type=str, help="File with pairs (one per line)")
    parser.add_argument("--interval", type=int, default=10, help="Poll interval seconds")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--alert", type=float, default=0.0, help="Alert when edge > this")
    args = parser.parse_args()

    # Collect pairs
    pairs = []

    if args.nfl:
        pairs.extend(MARKETS["nfl"])
    if args.nba:
        pairs.extend(MARKETS["nba"])
    if args.nhl:
        pairs.extend(MARKETS["nhl"])

    if args.file:
        with open(args.file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    pairs.append(parse_pair(line))

    for p in args.pairs:
        pairs.append(parse_pair(p))

    if not pairs:
        print("No markets specified. Use --nfl, --nba, --nhl, or provide pairs.")
        print("Example: python scripts/monitor_markets.py --nfl")
        return

    # Connect
    from src.core.api_client import KalshiClient
    from src.core.config import get_config

    config = get_config()
    api = KalshiClient(config)

    print(f"Monitoring {len(pairs)} pairs")
    print(f"Interval: {args.interval}s")
    if args.alert > 0:
        print(f"Alert threshold: edge > ${args.alert:.4f}")
    print()

    iteration = 0
    try:
        while True:
            iteration += 1
            ts = datetime.now().strftime("%H:%M:%S")

            print(f"[{ts}] Scan #{iteration}")
            print("-" * 60)

            best_edge = -999
            best_pair = None

            for ticker_a, ticker_b in pairs:
                result = fetch_pair(api, ticker_a, ticker_b)
                if result is None:
                    print(f"  {ticker_a[:20]}... - FAILED")
                    continue

                edge = result["edge"]
                name = f"{result['a_name']} vs {result['b_name']}"

                # Track best
                if edge > best_edge:
                    best_edge = edge
                    best_pair = name

                # Display
                if edge > 0:
                    status = f"** OPPORTUNITY +${edge:.4f} **"
                elif edge > -0.01:
                    status = f"TIGHT {edge:+.4f}"
                else:
                    status = f"{edge:+.4f}"

                print(f"  {name:<30} ${result['combined']:.2f}  {status}")

                time.sleep(0.3)

            print("-" * 60)
            print(f"  Best: {best_pair} (edge: ${best_edge:+.4f})")

            # Alert
            if args.alert > 0 and best_edge > args.alert:
                print(f"\n  *** ALERT: Edge ${best_edge:.4f} > threshold ${args.alert:.4f} ***\n")
                # Could add sound/notification here

            if args.once:
                break

            print(f"\n  Next scan in {args.interval}s... (Ctrl+C to stop)\n")
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
