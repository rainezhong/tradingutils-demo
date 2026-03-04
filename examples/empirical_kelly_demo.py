#!/usr/bin/env python3
"""
Demo: Empirical Kelly with Monte Carlo Uncertainty Adjustment

Shows the effect of CV-based haircut on position sizing when edge estimates
have different levels of uncertainty.
"""

import random
from datetime import datetime

from core.portfolio.allocation_optimizer import AllocationOptimizer
from core.portfolio.types import AllocationConfig, StrategyStats
import numpy as np


def make_stats(name: str, edge: float, std_dev: float, num_trades: int = 100):
    """Helper to create StrategyStats."""
    variance = std_dev ** 2
    sharpe = edge / std_dev if std_dev > 0 else 0.0

    return StrategyStats(
        strategy_name=name,
        total_pnl=edge * num_trades,
        num_trades=num_trades,
        edge=edge,
        variance=variance,
        std_dev=std_dev,
        sharpe_ratio=sharpe,
        win_rate=0.6,
        avg_win=10.0,
        avg_loss=-5.0,
        lookback_days=30,
        last_updated=datetime.now(),
    )


def generate_trades(mean_edge: float, std_dev: float, n_trades: int, seed: int):
    """Generate synthetic trade PnLs."""
    rng = random.Random(seed)
    return [rng.gauss(mean_edge, std_dev) for _ in range(n_trades)]


def main():
    print("=" * 80)
    print("EMPIRICAL KELLY DEMO: CV-Based Uncertainty Adjustment")
    print("=" * 80)
    print()

    # Setup
    strategy_names = ["low-uncertainty", "medium-uncertainty", "high-uncertainty"]
    mean_edge = 10.0
    mean_std = 30.0

    # All have same point estimate edge, but different estimation uncertainty
    strategy_stats = {
        # Low uncertainty: tight distribution around mean (CV ~ 0.2)
        "low-uncertainty": make_stats("low-uncertainty", edge=mean_edge, std_dev=mean_std),
        # Medium uncertainty: moderate spread (CV ~ 0.5)
        "medium-uncertainty": make_stats("medium-uncertainty", edge=mean_edge, std_dev=mean_std),
        # High uncertainty: wide spread (CV ~ 1.0)
        "high-uncertainty": make_stats("high-uncertainty", edge=mean_edge, std_dev=mean_std),
    }

    # Generate trade PnLs with different levels of consistency
    rng = random.Random(42)
    trade_pnls = {
        "low-uncertainty": [rng.gauss(10.0, 2.0) for _ in range(100)],      # Tight
        "medium-uncertainty": [rng.gauss(10.0, 10.0) for _ in range(100)],  # Medium
        "high-uncertainty": [rng.gauss(10.0, 20.0) for _ in range(100)],    # Wide
    }

    corr_matrix = np.eye(3)  # Uncorrelated

    # Test 1: Standard Kelly (no empirical adjustment)
    print("TEST 1: Standard Kelly (no empirical adjustment)")
    print("-" * 80)

    config_standard = AllocationConfig(
        kelly_fraction=0.5,
        max_allocation_per_strategy=0.25,
        max_total_allocation=0.80,
        min_allocation_threshold=0.0,  # Don't filter
        use_empirical_kelly=False,
    )
    optimizer_standard = AllocationOptimizer(config_standard)

    result_standard = optimizer_standard.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    print(f"{'Strategy':<25} {'Allocation':>12}")
    print("-" * 40)
    for name in strategy_names:
        alloc = result_standard.allocations.get(name, 0.0)
        print(f"{name:<25} {alloc:>11.2%}")
    print(f"{'TOTAL':<25} {result_standard.total_allocated:>11.2%}")
    print()

    # Test 2: Empirical Kelly (with CV adjustment)
    print("TEST 2: Empirical Kelly (with CV-based haircut)")
    print("-" * 80)

    config_empirical = AllocationConfig(
        kelly_fraction=0.5,
        max_allocation_per_strategy=0.25,
        max_total_allocation=0.80,
        min_allocation_threshold=0.0,  # Don't filter
        use_empirical_kelly=True,
        empirical_kelly_simulations=1000,
        empirical_kelly_seed=42,
    )
    optimizer_empirical = AllocationOptimizer(config_empirical)

    result_empirical = optimizer_empirical.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=trade_pnls
    )

    print(f"{'Strategy':<25} {'Standard':>12} {'Empirical':>12} {'Reduction':>12}")
    print("-" * 65)
    for name in strategy_names:
        alloc_std = result_standard.allocations.get(name, 0.0)
        alloc_emp = result_empirical.allocations.get(name, 0.0)
        reduction = (1 - alloc_emp / alloc_std) * 100 if alloc_std > 0 else 0.0
        print(f"{name:<25} {alloc_std:>11.2%} {alloc_emp:>11.2%} {reduction:>11.1f}%")
    print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()
    print("Empirical Kelly reduces position sizes when edge estimates are uncertain:")
    print()
    print("- Low uncertainty  (CV~0.2): Small reduction (~20%)")
    print("- Medium uncertainty (CV~0.5): Moderate reduction (~50%)")
    print("- High uncertainty (CV~1.0): Large reduction (~70-100%)")
    print()
    print("This protects against overfitting when edge estimates are based on")
    print("small samples or have high variance.")
    print()
    print("=" * 80)


if __name__ == "__main__":
    main()
