#!/usr/bin/env python3
"""
Portfolio Analysis Tool

Analyzes portfolio performance, generates visualizations, and exports reports.

Usage:
    python scripts/analyze_portfolio.py --db data/portfolio_trades.db
    python scripts/analyze_portfolio.py --days 60 --export portfolio_report.csv
"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timedelta
import logging

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.portfolio import PerformanceTracker, CorrelationEstimator, PortfolioConfig


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze portfolio performance"
    )
    parser.add_argument(
        "--db",
        default="data/portfolio_trades.db",
        help="Path to portfolio trades database"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Lookback period in days (default 30)"
    )
    parser.add_argument(
        "--export",
        help="Export stats to CSV file"
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate visualizations (requires matplotlib)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Check database exists
    db_path = Path(args.db)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    # Initialize tracker
    tracker = PerformanceTracker(str(db_path))

    # Get all strategies
    strategy_names = tracker.get_all_strategy_names()

    if not strategy_names:
        logger.warning("No strategies found in database")
        return 0

    logger.info(f"Found {len(strategy_names)} strategies")

    # Get performance stats
    print("\n" + "=" * 100)
    print(f"PORTFOLIO ANALYSIS (Last {args.days} days)")
    print("=" * 100)
    print()

    strategy_stats = {}
    for strategy_name in strategy_names:
        stats = tracker.get_strategy_stats(strategy_name, lookback_days=args.days)

        if stats and stats.num_trades > 0:
            strategy_stats[strategy_name] = stats

    if not strategy_stats:
        logger.warning("No strategies with settled trades")
        return 0

    # Display individual performance
    print("Individual Strategy Performance:")
    print("-" * 100)
    print(
        f"{'Strategy':<30s} {'Total PnL':>12s} {'Trades':>8s} "
        f"{'Edge':>10s} {'Std Dev':>10s} {'Sharpe':>8s} {'Win %':>7s} "
        f"{'Avg Win':>10s} {'Avg Loss':>10s}"
    )
    print("-" * 100)

    total_pnl = 0.0
    total_trades = 0

    for name in sorted(strategy_stats.keys()):
        stats = strategy_stats[name]
        total_pnl += stats.total_pnl
        total_trades += stats.num_trades

        print(
            f"{name:<30s} "
            f"${stats.total_pnl:11.2f} "
            f"{stats.num_trades:8d} "
            f"${stats.edge:9.2f} "
            f"${stats.std_dev:9.2f} "
            f"{stats.sharpe_ratio:8.2f} "
            f"{stats.win_rate*100:6.1f}% "
            f"${stats.avg_win:9.2f} "
            f"${stats.avg_loss:9.2f}"
        )

    print("-" * 100)
    print(
        f"{'TOTAL':<30s} "
        f"${total_pnl:11.2f} "
        f"{total_trades:8d}"
    )
    print()

    # Calculate portfolio-level metrics
    if len(strategy_stats) > 1:
        # Correlation matrix
        config = PortfolioConfig(
            trade_db_path=str(db_path),
            lookback_days=args.days,
        )
        estimator = CorrelationEstimator(config)

        active_names = sorted(strategy_stats.keys())
        strategy_trades = tracker.get_trades_for_correlation(
            active_names,
            lookback_days=args.days
        )

        corr_matrix = estimator.estimate_correlation_matrix(strategy_trades)

        print("Correlation Matrix:")
        print("-" * 100)

        # Header
        print(f"{'':>30s}", end="")
        for name in active_names:
            print(f"{name[:12]:>14s}", end="")
        print()

        # Rows
        for i, name_i in enumerate(active_names):
            print(f"{name_i:<30s}", end="")
            for j in range(len(active_names)):
                print(f"{corr_matrix[i, j]:14.2f}", end="")
            print()

        print()

        # Diversification benefit
        avg_correlation = 0.0
        count = 0
        for i in range(len(active_names)):
            for j in range(i + 1, len(active_names)):
                avg_correlation += corr_matrix[i, j]
                count += 1

        if count > 0:
            avg_correlation /= count
            print(f"Average Pairwise Correlation: {avg_correlation:.2f}")
            print()

    # Export if requested
    if args.export:
        import csv
        export_path = Path(args.export)

        with open(export_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "strategy",
                "total_pnl",
                "num_trades",
                "edge",
                "variance",
                "std_dev",
                "sharpe_ratio",
                "win_rate",
                "avg_win",
                "avg_loss",
            ])

            for name in sorted(strategy_stats.keys()):
                stats = strategy_stats[name]
                writer.writerow([
                    name,
                    stats.total_pnl,
                    stats.num_trades,
                    stats.edge,
                    stats.variance,
                    stats.std_dev,
                    stats.sharpe_ratio,
                    stats.win_rate,
                    stats.avg_win,
                    stats.avg_loss,
                ])

        logger.info(f"✓ Exported to {export_path}")

    # Plot if requested
    if args.plot:
        try:
            import matplotlib.pyplot as plt
            import numpy as np

            _generate_plots(strategy_stats, corr_matrix if len(strategy_stats) > 1 else None, active_names if len(strategy_stats) > 1 else None)
            logger.info("✓ Generated visualizations")

        except ImportError:
            logger.error("matplotlib not installed (pip install matplotlib)")
            return 1

    print("=" * 100)
    return 0


def _generate_plots(strategy_stats, corr_matrix, strategy_names):
    """Generate portfolio visualizations."""
    import matplotlib.pyplot as plt
    import numpy as np

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))

    # 1. PnL by strategy
    ax = axes[0, 0]
    names = sorted(strategy_stats.keys())
    pnls = [strategy_stats[n].total_pnl for n in names]
    colors = ['green' if p > 0 else 'red' for p in pnls]

    ax.barh(names, pnls, color=colors, alpha=0.7)
    ax.set_xlabel("Total PnL ($)")
    ax.set_title("Strategy Performance")
    ax.axvline(0, color='black', linewidth=0.5)

    # 2. Sharpe ratios
    ax = axes[0, 1]
    sharpes = [strategy_stats[n].sharpe_ratio for n in names]

    ax.barh(names, sharpes, alpha=0.7, color='steelblue')
    ax.set_xlabel("Sharpe Ratio")
    ax.set_title("Risk-Adjusted Returns")
    ax.axvline(0, color='black', linewidth=0.5)

    # 3. Win rate vs edge
    ax = axes[1, 0]
    win_rates = [strategy_stats[n].win_rate * 100 for n in names]
    edges = [strategy_stats[n].edge for n in names]

    ax.scatter(win_rates, edges, s=100, alpha=0.7, color='purple')
    for i, name in enumerate(names):
        ax.annotate(name, (win_rates[i], edges[i]), fontsize=8, alpha=0.7)

    ax.set_xlabel("Win Rate (%)")
    ax.set_ylabel("Edge ($)")
    ax.set_title("Win Rate vs Edge")
    ax.grid(alpha=0.3)

    # 4. Correlation heatmap
    ax = axes[1, 1]
    if corr_matrix is not None and strategy_names:
        im = ax.imshow(corr_matrix, cmap='RdYlGn', vmin=-1, vmax=1)
        ax.set_xticks(range(len(strategy_names)))
        ax.set_yticks(range(len(strategy_names)))
        ax.set_xticklabels([n[:10] for n in strategy_names], rotation=45, ha='right')
        ax.set_yticklabels([n[:10] for n in strategy_names])
        ax.set_title("Correlation Matrix")

        # Add values
        for i in range(len(strategy_names)):
            for j in range(len(strategy_names)):
                text = ax.text(j, i, f"{corr_matrix[i, j]:.2f}",
                             ha="center", va="center", color="black", fontsize=8)

        plt.colorbar(im, ax=ax)
    else:
        ax.text(0.5, 0.5, "N/A (single strategy)", ha='center', va='center')
        ax.set_title("Correlation Matrix")

    plt.tight_layout()
    plt.savefig("portfolio_analysis.png", dpi=150)
    plt.show()


if __name__ == "__main__":
    sys.exit(main())
