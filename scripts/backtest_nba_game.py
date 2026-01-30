#!/usr/bin/env python3
"""
Backtest NBA mispricing strategy against recorded game data.

Usage:
    # Run backtest on a single game
    python scripts/backtest_nba_game.py data/recordings/LAL_vs_BOS.json

    # Run with different parameters
    python scripts/backtest_nba_game.py data/recordings/game.json --min-edge 5 --max-period 2

    # Run on all recordings in a directory
    python scripts/backtest_nba_game.py data/recordings/ --all

    # Compare different configurations
    python scripts/backtest_nba_game.py data/recordings/game.json --sweep-edge

    # Export results to CSV
    python scripts/backtest_nba_game.py data/recordings/game.json --output results.csv
"""

import argparse
import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path
from typing import List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.simulation.nba_recorder import NBAGameRecorder
from src.simulation.nba_backtester import (
    NBAStrategyBacktester,
    BacktestResult,
    format_backtest_report,
    run_backtest,
)


async def run_single_backtest(
    recording_path: str,
    min_edge: float,
    max_period: int,
    position_size: int,
    verbose: bool,
) -> BacktestResult:
    """Run backtest on a single recording."""
    print(f"\nLoading: {recording_path}")
    result = await run_backtest(
        recording_path=recording_path,
        min_edge_cents=min_edge,
        max_period=max_period,
        position_size=position_size,
        speed=1000.0,  # Run fast
        verbose=verbose,
    )
    return result


async def run_edge_sweep(
    recording_path: str,
    max_period: int,
    position_size: int,
) -> List[BacktestResult]:
    """Sweep through different edge thresholds."""
    edge_values = [1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0]
    results = []

    print("\nEdge Sensitivity Analysis")
    print("=" * 60)

    for edge in edge_values:
        result = await run_backtest(
            recording_path=recording_path,
            min_edge_cents=edge,
            max_period=max_period,
            position_size=position_size,
            speed=1000.0,
            verbose=False,
        )
        results.append(result)

        m = result.metrics
        print(f"Edge {edge:4.1f}¢: {m.total_signals:3d} signals, "
              f"{m.orders_filled:3d} fills, {m.accuracy_pct:5.1f}% acc, "
              f"${m.net_pnl:+7.2f} P&L")

    return results


async def run_batch_backtest(
    directory: str,
    min_edge: float,
    max_period: int,
    position_size: int,
) -> List[BacktestResult]:
    """Run backtest on all recordings in a directory."""
    path = Path(directory)
    recordings = list(path.glob("*.json"))

    if not recordings:
        print(f"No recordings found in {directory}")
        return []

    print(f"\nFound {len(recordings)} recordings")
    print("=" * 60)

    results = []
    for recording_path in sorted(recordings):
        try:
            result = await run_backtest(
                recording_path=str(recording_path),
                min_edge_cents=min_edge,
                max_period=max_period,
                position_size=position_size,
                speed=1000.0,
                verbose=False,
            )
            results.append(result)

            m = result.metrics
            print(f"{recording_path.name:<40} | "
                  f"{m.total_signals:3d} sig, {m.accuracy_pct:5.1f}% acc, "
                  f"${m.net_pnl:+7.2f}")

        except Exception as e:
            print(f"{recording_path.name:<40} | ERROR: {e}")

    # Summary
    if results:
        print("\n" + "=" * 60)
        print("BATCH SUMMARY")
        print("=" * 60)

        total_signals = sum(r.metrics.total_signals for r in results)
        total_fills = sum(r.metrics.orders_filled for r in results)
        total_correct = sum(r.metrics.correct_signals for r in results)
        total_incorrect = sum(r.metrics.incorrect_signals for r in results)
        total_pnl = sum(r.metrics.net_pnl for r in results)

        acc = total_correct / (total_correct + total_incorrect) * 100 if (total_correct + total_incorrect) > 0 else 0

        print(f"Games: {len(results)}")
        print(f"Total Signals: {total_signals}")
        print(f"Total Fills: {total_fills}")
        print(f"Overall Accuracy: {acc:.1f}%")
        print(f"Total Net P&L: ${total_pnl:+.2f}")
        print(f"Avg P&L per Game: ${total_pnl/len(results):+.2f}")

    return results


def export_to_csv(results: List[BacktestResult], output_path: str) -> None:
    """Export results to CSV."""
    with open(output_path, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'recording', 'game_id', 'matchup', 'winner',
            'final_score', 'min_edge', 'max_period', 'position_size',
            'total_signals', 'signals_traded', 'orders_filled',
            'accuracy_pct', 'avg_edge', 'gross_pnl', 'net_pnl'
        ])

        # Data
        for r in results:
            m = r.metrics
            writer.writerow([
                r.recording_path,
                r.game_id,
                f"{r.away_team} @ {r.home_team}",
                r.winner,
                f"{r.final_away_score}-{r.final_home_score}",
                r.min_edge_cents,
                r.max_period,
                r.position_size,
                m.total_signals,
                m.signals_traded,
                m.orders_filled,
                f"{m.accuracy_pct:.1f}",
                f"{m.avg_edge_cents:.1f}",
                f"{m.gross_pnl:.2f}",
                f"{m.net_pnl:.2f}",
            ])

    print(f"\nResults exported to: {output_path}")


async def main():
    parser = argparse.ArgumentParser(
        description="Backtest NBA mispricing strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "path",
        type=str,
        help="Path to recording file (.json) or directory",
    )

    parser.add_argument(
        "--min-edge",
        type=float,
        default=3.0,
        help="Minimum edge in cents to trade (default: 3.0)",
    )

    parser.add_argument(
        "--max-period",
        type=int,
        default=2,
        help="Maximum period to trade in (default: 2, first half only)",
    )

    parser.add_argument(
        "--position-size",
        type=int,
        default=10,
        help="Contracts per trade (default: 10)",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        help="Run on all .json files in directory",
    )

    parser.add_argument(
        "--sweep-edge",
        action="store_true",
        help="Test multiple edge thresholds (1-10¢)",
    )

    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Export results to CSV file",
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print detailed progress",
    )

    args = parser.parse_args()

    path = Path(args.path)

    if args.sweep_edge and path.is_file():
        # Edge sensitivity sweep
        results = await run_edge_sweep(
            recording_path=str(path),
            max_period=args.max_period,
            position_size=args.position_size,
        )

    elif args.all or path.is_dir():
        # Batch mode
        if path.is_file():
            directory = str(path.parent)
        else:
            directory = str(path)

        results = await run_batch_backtest(
            directory=directory,
            min_edge=args.min_edge,
            max_period=args.max_period,
            position_size=args.position_size,
        )

    elif path.is_file():
        # Single file
        result = await run_single_backtest(
            recording_path=str(path),
            min_edge=args.min_edge,
            max_period=args.max_period,
            position_size=args.position_size,
            verbose=args.verbose,
        )

        # Print full report
        print(format_backtest_report(result))
        results = [result]

    else:
        print(f"Error: Path not found: {path}")
        return

    # Export if requested
    if args.output and results:
        export_to_csv(results, args.output)


if __name__ == "__main__":
    asyncio.run(main())
