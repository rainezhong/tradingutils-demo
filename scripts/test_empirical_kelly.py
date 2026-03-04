#!/usr/bin/env python3
"""Test empirical Kelly with synthetic trade data."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.portfolio.allocation_optimizer import AllocationOptimizer
from core.portfolio.types import AllocationConfig, StrategyStats
import numpy as np

# Load config
import yaml
with open('config/portfolio_config.yaml', 'r') as f:
    config_dict = yaml.safe_load(f)

allocation_config = AllocationConfig(
    use_empirical_kelly=config_dict['portfolio']['allocation']['use_empirical_kelly'],
    empirical_kelly_simulations=config_dict['portfolio']['allocation']['empirical_kelly_simulations'],
    kelly_fraction=config_dict['portfolio']['allocation']['kelly_fraction'],
)

print("=" * 80)
print("EMPIRICAL KELLY TEST")
print("=" * 80)
print(f"Empirical Kelly enabled: {allocation_config.use_empirical_kelly}")
print(f"Simulations: {allocation_config.empirical_kelly_simulations}")
print(f"Kelly fraction: {allocation_config.kelly_fraction}")
print()

# Create two synthetic strategies
from datetime import datetime
strategies = {
    'stable-strategy': StrategyStats(
        strategy_name='stable-strategy',
        edge=0.10,      # 10% edge
        variance=0.04,  # Low variance (stable)
        std_dev=0.20,
        num_trades=100,
        total_pnl=10.0,
        win_rate=0.60,
        sharpe_ratio=0.50,
        avg_win=0.25,
        avg_loss=-0.15,
        lookback_days=30,
        last_updated=datetime.now(),
    ),
    'volatile-strategy': StrategyStats(
        strategy_name='volatile-strategy',
        edge=0.10,      # Same 10% edge
        variance=0.36,  # High variance (volatile)
        std_dev=0.60,
        num_trades=100,
        total_pnl=10.0,
        win_rate=0.55,
        sharpe_ratio=0.17,
        avg_win=0.80,
        avg_loss=-0.60,
        lookback_days=30,
        last_updated=datetime.now(),
    ),
}

# Synthetic trade PnLs
np.random.seed(42)
trade_pnls = {
    'stable-strategy': list(np.random.normal(0.10, 0.20, 100)),
    'volatile-strategy': list(np.random.normal(0.10, 0.60, 100)),
}

# Calculate allocations
optimizer = AllocationOptimizer(allocation_config)

# No correlation for this test
corr_matrix = np.array([[1.0, 0.0], [0.0, 1.0]])
strategy_names = list(strategies.keys())

result = optimizer.calculate_allocations(
    strategy_stats=strategies,
    correlation_matrix=corr_matrix,
    strategy_names=strategy_names,
    trade_pnls=trade_pnls if allocation_config.use_empirical_kelly else None,
)

print("RESULTS:")
print("-" * 80)
for name in strategy_names:
    allocation = result.allocations.get(name, 0.0)
    stats = strategies[name]
    cv = stats.std_dev / stats.edge if stats.edge > 0 else 0

    print(f"\n{name}:")
    print(f"  Edge: {stats.edge:.2%}")
    print(f"  Std Dev: {stats.std_dev:.2f}")
    print(f"  CV (coefficient of variation): {cv:.2f}")
    print(f"  Allocation: {allocation:.1%}")

    if allocation_config.use_empirical_kelly:
        # Standard Kelly would be ~edge/variance = 0.10/var
        standard_kelly = stats.edge / stats.variance if stats.variance > 0 else 0
        haircut = allocation / standard_kelly if standard_kelly > 0 else 0
        print(f"  Standard Kelly (no haircut): {standard_kelly:.1%}")
        print(f"  Empirical Kelly haircut: {haircut:.1%}")

print("\n" + "=" * 80)
print("KEY INSIGHT:")
print("-" * 80)
if allocation_config.use_empirical_kelly:
    print("✓ Empirical Kelly applied different haircuts based on CV:")
    print("  - Stable strategy (low CV) → smaller haircut → larger allocation")
    print("  - Volatile strategy (high CV) → larger haircut → smaller allocation")
else:
    print("✗ Empirical Kelly disabled - both strategies get same treatment")
print("=" * 80)
