#!/usr/bin/env python3
"""Monitor crypto scalp edge in real-time.

Compares live paper trading performance to backtest expectations.
Alerts if edge is degrading.
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_probe_stats(db_path: str) -> dict:
    """Get current probe statistics."""
    try:
        with sqlite3.connect(db_path) as conn:
            # Check data collection
            kalshi = conn.execute(
                "SELECT count(*), min(ts), max(ts) FROM kalshi_snapshots"
            ).fetchone()

            binance = conn.execute(
                "SELECT count(*), min(ts), max(ts) FROM binance_trades"
            ).fetchone()

            # Calculate disagreement rate (quick proxy for edge)
            disagreements = conn.execute("""
                SELECT
                    count(*) as total,
                    sum(CASE
                        WHEN (k.yes_mid > 50 AND b.price < k.floor_strike)
                          OR (k.yes_mid < 50 AND b.price > k.floor_strike)
                        THEN 1 ELSE 0
                    END) as disagree
                FROM kalshi_snapshots k
                JOIN (
                    SELECT ts, avg(price) as price
                    FROM binance_trades
                    GROUP BY cast(ts as int)
                ) b ON cast(k.ts as int) = cast(b.ts as int)
                WHERE k.yes_mid IS NOT NULL
                  AND k.floor_strike IS NOT NULL
                  AND abs(k.ts - b.ts) < 2
                LIMIT 10000
            """).fetchone()

            if kalshi[0] == 0:
                return {"status": "no_data", "message": "No data collected yet"}

            duration_hours = (kalshi[2] - kalshi[1]) / 3600 if kalshi[2] else 0

            disagree_rate = 0
            if disagreements and disagreements[0] > 0:
                disagree_rate = (disagreements[1] / disagreements[0]) * 100

            return {
                "status": "collecting",
                "kalshi_snapshots": kalshi[0],
                "binance_trades": binance[0],
                "duration_hours": duration_hours,
                "disagreement_rate": disagree_rate,
                "start_time": datetime.fromtimestamp(kalshi[1]).strftime("%Y-%m-%d %H:%M:%S") if kalshi[1] else "N/A",
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def monitor_edge(probe_db: str, check_interval: int = 300):
    """Monitor edge degradation over time."""

    print("=" * 70)
    print("🔍 CRYPTO SCALP EDGE MONITOR")
    print("=" * 70)
    print(f"Probe DB: {probe_db}")
    print(f"Check interval: {check_interval}s")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    print()

    # Backtest baseline
    BASELINE = {
        "win_rate": 54,
        "disagreement_rate": 7.4,
        "trades_per_hour": 15.7,
        "avg_pnl_cents": 10.3,
    }

    iteration = 0

    while True:
        iteration += 1
        stats = get_probe_stats(probe_db)

        timestamp = datetime.now().strftime("%H:%M:%S")

        if stats["status"] == "no_data":
            print(f"[{timestamp}] ⏳ Waiting for data collection to start...")

        elif stats["status"] == "collecting":
            duration = stats["duration_hours"]
            kalshi = stats["kalshi_snapshots"]
            binance = stats["binance_trades"]
            disagree = stats["disagreement_rate"]

            # Calculate edge health
            if disagree > 5:
                edge_status = "✅ HEALTHY"
            elif disagree > 3:
                edge_status = "⚠️  WEAK"
            else:
                edge_status = "🔴 DEGRADED"

            print(f"[{timestamp}] Probe: {duration:.1f}h | "
                  f"Kalshi: {kalshi:,} | Binance: {binance:,} | "
                  f"Disagree: {disagree:.1f}% {edge_status}")

            # Alert if edge degrading
            if disagree < 3 and iteration > 2:
                print(f"⚠️  WARNING: Disagreement rate ({disagree:.1f}%) below threshold (3%)")
                print(f"   Expected: {BASELINE['disagreement_rate']}% (from backtest)")
                print(f"   Action: Edge may be degrading - consider stopping trading")

            # Every hour, print detailed stats
            if iteration % 12 == 0 and duration > 0:
                print()
                print("-" * 70)
                print(f"📊 PROBE STATISTICS ({duration:.1f} hours)")
                print("-" * 70)
                print(f"  Kalshi snapshots:      {kalshi:,}")
                print(f"  Binance trades:        {binance:,}")
                print(f"  Disagreement rate:     {disagree:.1f}% (expect: {BASELINE['disagreement_rate']}%)")
                print(f"  Start time:            {stats['start_time']}")
                print(f"  Collection rate:       {kalshi/duration:.0f} snapshots/hour")
                print("-" * 70)
                print()

        elif stats["status"] == "error":
            print(f"[{timestamp}] ❌ ERROR: {stats['message']}")

        # Sleep until next check
        time.sleep(check_interval)


def main():
    parser = argparse.ArgumentParser(description="Monitor crypto scalp edge")
    parser.add_argument(
        "--probe-db",
        default=f"data/btc_probe_{datetime.now().strftime('%Y%m%d')}.db",
        help="Path to probe database"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Check interval in seconds (default: 300 = 5min)"
    )

    args = parser.parse_args()

    if not Path(args.probe_db).exists():
        print(f"❌ Probe database not found: {args.probe_db}")
        print("\nTip: Check if probe is running:")
        print("  ps aux | grep btc_latency_probe")
        sys.exit(1)

    try:
        monitor_edge(args.probe_db, args.interval)
    except KeyboardInterrupt:
        print("\n\n👋 Monitor stopped")
        sys.exit(0)


if __name__ == "__main__":
    main()
