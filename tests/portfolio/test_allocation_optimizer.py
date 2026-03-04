"""
Tests for AllocationOptimizer.
"""

import pytest
import numpy as np
from datetime import datetime

from core.portfolio.allocation_optimizer import AllocationOptimizer
from core.portfolio.types import StrategyStats, AllocationConfig


@pytest.fixture
def config():
    """Default allocation config."""
    return AllocationConfig(
        kelly_fraction=0.5,
        max_allocation_per_strategy=0.25,
        max_total_allocation=0.80,
        min_allocation_threshold=0.05,
        min_trades_per_strategy=10,
    )


@pytest.fixture
def optimizer(config):
    """Optimizer instance."""
    return AllocationOptimizer(config)


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


def test_single_strategy_uncorrelated(optimizer):
    """Test allocation for single strategy."""
    strategy_names = ["strat-a"]
    strategy_stats = {
        "strat-a": make_stats("strat-a", edge=10.0, std_dev=30.0),
    }
    corr_matrix = np.array([[1.0]])

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Kelly: f = edge / variance = 10 / 900 ≈ 0.011
    # Half Kelly: 0.0055
    # But capped at max_allocation_per_strategy = 0.25
    assert "strat-a" in result.allocations
    assert result.total_allocated <= 0.25


def test_two_uncorrelated_strategies(optimizer):
    """Test allocation for two uncorrelated strategies."""
    strategy_names = ["strat-a", "strat-b"]
    strategy_stats = {
        # Use edge/std ratios that produce allocations clearly above 5% threshold
        # Kelly = edge/variance, so edge=11, std=10 → Kelly = 11/100 = 0.11 → half = 0.055 (5.5%)
        "strat-a": make_stats("strat-a", edge=11.0, std_dev=10.0),
        "strat-b": make_stats("strat-b", edge=15.0, std_dev=12.0),
    }
    corr_matrix = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
    ])

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Both should get allocations (above min threshold)
    assert "strat-a" in result.allocations
    assert "strat-b" in result.allocations

    # Verify allocations are reasonable
    assert result.total_allocated > 0
    assert result.total_allocated <= optimizer.config.max_total_allocation


def test_perfectly_correlated_strategies(optimizer):
    """Test allocation for perfectly correlated strategies."""
    strategy_names = ["strat-a", "strat-b"]
    strategy_stats = {
        "strat-a": make_stats("strat-a", edge=10.0, std_dev=30.0),
        "strat-b": make_stats("strat-b", edge=15.0, std_dev=40.0),
    }
    corr_matrix = np.array([
        [1.0, 1.0],
        [1.0, 1.0],
    ])

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # With perfect correlation, only one strategy should dominate
    # The one with higher Sharpe ratio should get more
    total_a = result.allocations.get("strat-a", 0.0)
    total_b = result.allocations.get("strat-b", 0.0)

    # Higher Sharpe should dominate
    assert total_b >= total_a


def test_negative_edge_zero_allocation(optimizer):
    """Test that negative edge results in zero allocation."""
    strategy_names = ["good", "bad"]
    strategy_stats = {
        "good": make_stats("good", edge=11.0, std_dev=10.0),
        "bad": make_stats("bad", edge=-5.0, std_dev=20.0),
    }
    corr_matrix = np.array([
        [1.0, 0.0],
        [0.0, 1.0],
    ])

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Good strategy gets allocation
    assert "good" in result.allocations
    assert result.allocations["good"] > 0

    # Bad strategy gets no allocation
    assert result.allocations.get("bad", 0.0) == 0.0


def test_individual_allocation_cap(optimizer):
    """Test individual allocation cap is enforced."""
    strategy_names = ["mega-edge"]
    strategy_stats = {
        # Huge edge, would want large allocation without cap
        "mega-edge": make_stats("mega-edge", edge=100.0, std_dev=50.0),
    }
    corr_matrix = np.array([[1.0]])

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Should be capped at max_allocation_per_strategy = 0.25
    assert result.allocations["mega-edge"] <= optimizer.config.max_allocation_per_strategy


def test_total_allocation_cap(optimizer):
    """Test total allocation cap is enforced."""
    # 5 strategies, each would want 20% → 100% total
    # Should be capped at max_total_allocation = 80%
    strategy_names = [f"strat-{i}" for i in range(5)]
    strategy_stats = {
        name: make_stats(name, edge=10.0, std_dev=30.0)
        for name in strategy_names
    }
    # Uncorrelated
    corr_matrix = np.eye(5)

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Total should respect cap
    assert result.total_allocated <= optimizer.config.max_total_allocation


def test_minimum_threshold(optimizer):
    """Test minimum allocation threshold."""
    # Two strategies, one with edge that would produce allocation below threshold
    strategy_names = ["good", "tiny-edge"]
    strategy_stats = {
        "good": make_stats("good", edge=11.0, std_dev=10.0),
        # Tiny edge: Kelly = 0.5/100 = 0.005 → half = 0.0025 (0.25%) << 5% threshold
        "tiny-edge": make_stats("tiny-edge", edge=0.5, std_dev=10.0),
    }
    corr_matrix = np.eye(2)

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Good strategy should get allocation
    assert "good" in result.allocations

    # Tiny edge below min_threshold = 0.05 should be zeroed out
    # (not in allocations dict, or if present, >= threshold)
    if "tiny-edge" in result.allocations:
        assert result.allocations["tiny-edge"] >= optimizer.config.min_allocation_threshold


def test_empty_strategies(optimizer):
    """Test with no strategies."""
    result = optimizer.calculate_allocations({}, np.array([[]]), [])

    assert result.allocations == {}
    assert result.total_allocated == 0.0


def test_portfolio_metrics(optimizer):
    """Test portfolio-level metrics calculation."""
    strategy_names = ["strat-a", "strat-b"]
    strategy_stats = {
        "strat-a": make_stats("strat-a", edge=10.0, std_dev=30.0),
        "strat-b": make_stats("strat-b", edge=15.0, std_dev=40.0),
    }
    corr_matrix = np.array([
        [1.0, 0.5],
        [0.5, 1.0],
    ])

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Portfolio metrics should be calculated
    assert result.portfolio_variance >= 0
    assert result.portfolio_sharpe >= 0
    assert result.expected_growth_rate is not None


def test_covariance_matrix_construction(optimizer):
    """Test covariance matrix construction from correlation + stds."""
    corr = np.array([
        [1.0, 0.5],
        [0.5, 1.0],
    ])
    stds = np.array([10.0, 20.0])

    cov = optimizer._build_covariance_matrix(corr, stds)

    # Cov[i,j] = ρ[i,j] * σ[i] * σ[j]
    assert cov[0, 0] == pytest.approx(100.0)  # 10 * 10
    assert cov[1, 1] == pytest.approx(400.0)  # 20 * 20
    assert cov[0, 1] == pytest.approx(100.0)  # 0.5 * 10 * 20
    assert cov[1, 0] == pytest.approx(100.0)  # Symmetric


def test_kelly_fraction_applied(optimizer):
    """Test that Kelly fraction scales allocations."""
    strategy_names = ["strat"]
    strategy_stats = {
        "strat": make_stats("strat", edge=10.0, std_dev=30.0),
    }
    corr_matrix = np.array([[1.0]])

    # Calculate with full Kelly
    optimizer.config.kelly_fraction = 1.0
    result_full = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Calculate with half Kelly
    optimizer.config.kelly_fraction = 0.5
    result_half = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Half Kelly should be approximately half of full Kelly
    # (approximately because of rounding/constraints)
    alloc_full = result_full.allocations.get("strat", 0.0)
    alloc_half = result_half.allocations.get("strat", 0.0)

    if alloc_full > 0:
        ratio = alloc_half / alloc_full
        assert 0.4 <= ratio <= 0.6  # Should be close to 0.5


# Empirical Kelly tests


def test_empirical_kelly_disabled_by_default(optimizer):
    """Test that empirical Kelly is disabled by default."""
    assert optimizer.config.use_empirical_kelly is False


def test_empirical_kelly_cv_adjustment(config):
    """Test that empirical Kelly applies CV-based haircut."""
    config.use_empirical_kelly = True
    config.empirical_kelly_simulations = 500
    config.empirical_kelly_seed = 42  # Reproducibility
    optimizer = AllocationOptimizer(config)

    strategy_names = ["strat-a"]
    strategy_stats = {
        "strat-a": make_stats("strat-a", edge=10.0, std_dev=30.0),
    }
    corr_matrix = np.array([[1.0]])

    # Generate synthetic trade PnLs with known variance
    # Mean = 10, but with high variance to create CV > 0
    import random
    rng = random.Random(42)
    trade_pnls = {
        "strat-a": [rng.gauss(10.0, 15.0) for _ in range(100)]
    }

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=trade_pnls
    )

    # With CV adjustment, allocation should be reduced vs no adjustment
    # Run without empirical Kelly for comparison
    config_baseline = AllocationConfig(
        kelly_fraction=0.5,
        max_allocation_per_strategy=0.25,
        max_total_allocation=0.80,
        min_allocation_threshold=0.05,
        use_empirical_kelly=False,
    )
    optimizer_baseline = AllocationOptimizer(config_baseline)

    result_baseline = optimizer_baseline.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names
    )

    # Empirical Kelly should reduce allocation
    assert result.allocations.get("strat-a", 0.0) <= result_baseline.allocations.get("strat-a", 0.0)


def test_empirical_kelly_insufficient_data(config):
    """Test empirical Kelly handles insufficient data gracefully."""
    config.use_empirical_kelly = True
    config.empirical_kelly_simulations = 500
    optimizer = AllocationOptimizer(config)

    strategy_names = ["strat-a"]
    strategy_stats = {
        "strat-a": make_stats("strat-a", edge=10.0, std_dev=30.0),
    }
    corr_matrix = np.array([[1.0]])

    # Only 5 trades (below 10 threshold)
    trade_pnls = {
        "strat-a": [10.0, 12.0, 8.0, 11.0, 9.0]
    }

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=trade_pnls
    )

    # Should still produce a result (falls back to point estimate)
    assert "strat-a" in result.allocations or result.allocations == {}


def test_empirical_kelly_no_trade_pnls(config):
    """Test empirical Kelly when trade_pnls=None (disabled)."""
    config.use_empirical_kelly = True
    optimizer = AllocationOptimizer(config)

    strategy_names = ["strat-a"]
    strategy_stats = {
        "strat-a": make_stats("strat-a", edge=10.0, std_dev=30.0),
    }
    corr_matrix = np.array([[1.0]])

    # Call without trade_pnls (should use point estimate)
    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=None
    )

    # Should still work (falls back to point estimate)
    assert result.allocations.get("strat-a", 0.0) >= 0.0


def test_empirical_kelly_high_cv_reduces_allocation():
    """Test that high CV significantly reduces allocation."""
    config = AllocationConfig(
        kelly_fraction=0.5,
        max_allocation_per_strategy=0.25,
        max_total_allocation=0.80,
        min_allocation_threshold=0.0,  # Don't filter small allocations
        use_empirical_kelly=True,
        empirical_kelly_simulations=500,
        empirical_kelly_seed=42,
    )
    optimizer = AllocationOptimizer(config)

    strategy_names = ["low-cv", "high-cv"]
    strategy_stats = {
        # Both have same point estimate edge
        "low-cv": make_stats("low-cv", edge=10.0, std_dev=30.0),
        "high-cv": make_stats("high-cv", edge=10.0, std_dev=30.0),
    }
    corr_matrix = np.eye(2)

    import random
    rng = random.Random(42)

    # Low CV: tight distribution around mean
    low_cv_pnls = [rng.gauss(10.0, 2.0) for _ in range(100)]

    # High CV: wide distribution around mean
    high_cv_pnls = [rng.gauss(10.0, 20.0) for _ in range(100)]

    trade_pnls = {
        "low-cv": low_cv_pnls,
        "high-cv": high_cv_pnls,
    }

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=trade_pnls
    )

    # Low CV strategy should get higher allocation than high CV
    alloc_low = result.allocations.get("low-cv", 0.0)
    alloc_high = result.allocations.get("high-cv", 0.0)

    assert alloc_low > alloc_high


def test_empirical_kelly_negative_edge():
    """Test empirical Kelly with negative edge (should get zero allocation)."""
    config = AllocationConfig(
        kelly_fraction=0.5,
        use_empirical_kelly=True,
        empirical_kelly_simulations=500,
        empirical_kelly_seed=42,
    )
    optimizer = AllocationOptimizer(config)

    strategy_names = ["bad-strat"]
    strategy_stats = {
        "bad-strat": make_stats("bad-strat", edge=-5.0, std_dev=20.0),
    }
    corr_matrix = np.array([[1.0]])

    import random
    rng = random.Random(42)
    trade_pnls = {
        "bad-strat": [rng.gauss(-5.0, 10.0) for _ in range(100)]
    }

    result = optimizer.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=trade_pnls
    )

    # Negative edge should get zero allocation
    assert result.allocations.get("bad-strat", 0.0) == 0.0


def test_empirical_kelly_reproducibility():
    """Test that empirical Kelly gives same results with same seed."""
    config = AllocationConfig(
        kelly_fraction=0.5,
        use_empirical_kelly=True,
        empirical_kelly_simulations=500,
        empirical_kelly_seed=12345,  # Fixed seed
    )

    optimizer1 = AllocationOptimizer(config)
    optimizer2 = AllocationOptimizer(config)

    strategy_names = ["strat"]
    strategy_stats = {
        "strat": make_stats("strat", edge=10.0, std_dev=30.0),
    }
    corr_matrix = np.array([[1.0]])

    import random
    rng = random.Random(99)
    trade_pnls = {
        "strat": [rng.gauss(10.0, 15.0) for _ in range(100)]
    }

    result1 = optimizer1.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=trade_pnls
    )

    result2 = optimizer2.calculate_allocations(
        strategy_stats, corr_matrix, strategy_names, trade_pnls=trade_pnls
    )

    # Should be identical with same seed
    assert result1.allocations == result2.allocations


def test_estimate_edge_uncertainty():
    """Test Monte Carlo edge uncertainty estimation."""
    config = AllocationConfig(
        empirical_kelly_simulations=1000,
        empirical_kelly_seed=42,
    )
    optimizer = AllocationOptimizer(config)

    # Create trades with known mean and variance
    import random
    rng = random.Random(42)
    mean = 10.0
    std = 5.0
    pnls = [rng.gauss(mean, std) for _ in range(200)]

    mean_edge, std_edge = optimizer._estimate_edge_uncertainty(pnls)

    # Mean should be close to true mean
    assert abs(mean_edge - mean) < 1.0

    # Std should be small (bootstrapping reduces uncertainty)
    # Std of sample mean ≈ population_std / sqrt(n) = 5 / sqrt(200) ≈ 0.35
    assert std_edge < 1.0  # Conservative bound
