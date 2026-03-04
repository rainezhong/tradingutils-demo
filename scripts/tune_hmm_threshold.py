#!/usr/bin/env python3
"""Tune HMM trending probability threshold for optimal performance.

Tests multiple thresholds and finds the one with best win rate / P&L.

Usage:
    python3 scripts/tune_hmm_threshold.py --db data/btc_ob_48h.db
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.backtest_hmm_vs_threshold import BacktestEngine


def main():
    parser = argparse.ArgumentParser(description="Tune HMM threshold")
    parser.add_argument("--db", required=True, help="Path to probe database")
    parser.add_argument(
        "--hmm",
        default="models/crypto_regime_hmm.pkl",
        help="Path to HMM model"
    )
    parser.add_argument(
        "--min-threshold",
        type=float,
        default=0.5,
        help="Min trending probability to test"
    )
    parser.add_argument(
        "--max-threshold",
        type=float,
        default=0.9,
        help="Max trending probability to test"
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.05,
        help="Step size for threshold sweep"
    )

    args = parser.parse_args()

    # Create engine
    engine = BacktestEngine(db_path=args.db, hmm_path=args.hmm)

    if engine.hmm is None:
        print("❌ HMM not loaded, cannot tune")
        sys.exit(1)

    # Sweep thresholds
    print("="*70)
    print("HMM THRESHOLD TUNING")
    print("="*70)
    print()

    thresholds = []
    current = args.min_threshold
    while current <= args.max_threshold:
        thresholds.append(current)
        current += args.step

    results = []

    print(f"Testing {len(thresholds)} thresholds: {thresholds[0]:.2f} to {thresholds[-1]:.2f}")
    print()
    print(f"{'Threshold':>10} {'Trades':>8} {'WR':>8} {'Net P&L':>10} {'Avg/Trade':>10}")
    print("-" * 70)

    for threshold in thresholds:
        result = engine.backtest_hmm(
            trending_threshold=threshold,
            min_volume=0.0,
        )

        if result:
            results.append((threshold, result))
            print(
                f"{threshold:>10.2f} {result.total_trades:>8,} "
                f"{result.win_rate*100:>7.1f}% "
                f"${result.net_pnl:>9.2f} "
                f"${result.avg_pnl_per_trade:>9.3f}"
            )

    if not results:
        print("❌ No results")
        return

    print()
    print("="*70)
    print("BEST THRESHOLDS")
    print("="*70)
    print()

    # Best by win rate
    best_wr = max(results, key=lambda x: x[1].win_rate)
    print(f"Best Win Rate: {best_wr[0]:.2f}")
    print(f"  Win rate: {best_wr[1].win_rate*100:.1f}%")
    print(f"  Trades: {best_wr[1].total_trades:,}")
    print(f"  Net P&L: ${best_wr[1].net_pnl:.2f}")
    print()

    # Best by net P&L
    best_pnl = max(results, key=lambda x: x[1].net_pnl)
    print(f"Best Net P&L: {best_pnl[0]:.2f}")
    print(f"  Net P&L: ${best_pnl[1].net_pnl:.2f}")
    print(f"  Win rate: {best_pnl[1].win_rate*100:.1f}%")
    print(f"  Trades: {best_pnl[1].total_trades:,}")
    print()

    # Best by avg P&L/trade
    best_avg = max(results, key=lambda x: x[1].avg_pnl_per_trade)
    print(f"Best Avg P&L/Trade: {best_avg[0]:.2f}")
    print(f"  Avg P&L: ${best_avg[1].avg_pnl_per_trade:.3f}")
    print(f"  Win rate: {best_avg[1].win_rate*100:.1f}%")
    print(f"  Trades: {best_avg[1].total_trades:,}")
    print()

    # Recommendation
    print("="*70)
    print("RECOMMENDATION")
    print("="*70)
    print()

    # Prefer win rate if net P&L is positive
    if best_wr[1].net_pnl > 0:
        print(f"Use threshold: {best_wr[0]:.2f} (best win rate)")
        print(f"  Expected: {best_wr[1].win_rate*100:.1f}% WR, ${best_wr[1].net_pnl:.2f} net P&L")
    else:
        print(f"Use threshold: {best_pnl[0]:.2f} (best net P&L)")
        print(f"  Expected: {best_pnl[1].win_rate*100:.1f}% WR, ${best_pnl[1].net_pnl:.2f} net P&L")


if __name__ == "__main__":
    main()
