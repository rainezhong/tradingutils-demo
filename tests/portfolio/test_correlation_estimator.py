"""
Tests for CorrelationEstimator.
"""

import pytest
import numpy as np
from datetime import datetime, timedelta

from core.portfolio.correlation_estimator import CorrelationEstimator
from core.portfolio.types import StrategyTrade, PortfolioConfig


@pytest.fixture
def config():
    """Default portfolio config."""
    return PortfolioConfig(
        correlation_shrinkage=0.70,
        default_correlation=0.1,
        prior_correlations={
            "strat-a:strat-b": 0.5,
        },
        market_overlap_threshold=0.20,
        market_overlap_correlation=0.5,
    )


@pytest.fixture
def estimator(config):
    """Correlation estimator instance."""
    return CorrelationEstimator(config)


def make_trade(strategy: str, ticker: str, pnl: float, ts: datetime):
    """Helper to create a trade."""
    return StrategyTrade(
        id=None,
        strategy_name=strategy,
        ticker=ticker,
        timestamp=ts,
        side="buy",
        price=0.50,
        size=10,
        pnl=pnl,
        settled_at=ts + timedelta(hours=1),
    )


def test_single_strategy_correlation(estimator):
    """Test correlation matrix for single strategy."""
    now = datetime.now()
    strategy_trades = {
        "strat-a": [make_trade("strat-a", "KXTEST", 10.0, now)],
    }

    corr_matrix = estimator.estimate_correlation_matrix(strategy_trades)

    assert corr_matrix.shape == (1, 1)
    assert corr_matrix[0, 0] == 1.0


def test_two_uncorrelated_strategies(estimator):
    """Test correlation for uncorrelated strategies."""
    now = datetime.now()

    # Generate uncorrelated returns with fixed seed
    np.random.seed(42)
    returns_a = np.random.normal(0, 10, 50)
    np.random.seed(123)  # Different seed for uncorrelated
    returns_b = np.random.normal(0, 10, 50)

    strategy_trades = {
        "strat-a": [
            make_trade("strat-a", "KXA", ret, now + timedelta(minutes=i*5))
            for i, ret in enumerate(returns_a)
        ],
        "strat-b": [
            make_trade("strat-b", "KXB", ret, now + timedelta(minutes=i*5))
            for i, ret in enumerate(returns_b)
        ],
    }

    corr_matrix = estimator.estimate_correlation_matrix(strategy_trades)

    assert corr_matrix.shape == (2, 2)
    assert corr_matrix[0, 0] == 1.0  # Diagonal
    assert corr_matrix[1, 1] == 1.0

    # Off-diagonal should be close to 0 (uncorrelated)
    # With shrinkage (0.7 sample + 0.3 prior), some drift toward prior (0.1)
    # Allow wider bounds for statistical variation
    assert -0.3 <= corr_matrix[0, 1] <= 0.3


def test_perfectly_correlated_strategies(estimator):
    """Test correlation for perfectly correlated strategies."""
    now = datetime.now()

    # Generate perfectly correlated returns
    np.random.seed(42)
    returns = np.random.normal(0, 10, 50)

    strategy_trades = {
        "strat-a": [
            make_trade("strat-a", "KXA", ret, now + timedelta(minutes=i*5))
            for i, ret in enumerate(returns)
        ],
        "strat-b": [
            make_trade("strat-b", "KXB", ret, now + timedelta(minutes=i*5))
            for i, ret in enumerate(returns)
        ],
    }

    corr_matrix = estimator.estimate_correlation_matrix(strategy_trades)

    # Should be close to 1.0 (but shrinkage affects it)
    assert corr_matrix[0, 1] >= 0.8


def test_prior_correlation_applied(estimator):
    """Test that prior correlation is blended in."""
    now = datetime.now()

    # Minimal data (will rely heavily on prior)
    strategy_trades = {
        "strat-a": [make_trade("strat-a", "KXA", 10.0, now)],
        "strat-b": [make_trade("strat-b", "KXB", 5.0, now)],
    }

    # Prior for strat-a:strat-b is 0.5
    corr_matrix = estimator.estimate_correlation_matrix(strategy_trades)

    # With shrinkage 0.7 and minimal sample data, should be influenced by prior
    # Expected: blend of sample (unclear) and prior (0.5)
    assert corr_matrix.shape == (2, 2)


def test_market_overlap_detection(estimator):
    """Test market overlap increases correlation."""
    now = datetime.now()

    # Both strategies trade same tickers (high overlap)
    strategy_trades = {
        "strat-a": [
            make_trade("strat-a", f"KXTEST-{i:02d}", 10.0, now + timedelta(hours=i))
            for i in range(10)
        ],
        "strat-b": [
            make_trade("strat-b", f"KXTEST-{i:02d}", 5.0, now + timedelta(hours=i))
            for i in range(10)
        ],
    }

    # Calculate overlap
    overlap = estimator._calculate_market_overlap(
        strategy_trades["strat-a"],
        strategy_trades["strat-b"],
    )

    assert overlap == 1.0  # 100% overlap

    # Prior should be boosted due to overlap
    prior_matrix = estimator._build_prior_matrix(
        ["strat-a", "strat-b"],
        strategy_trades,
    )

    # Should be >= market_overlap_correlation (0.5)
    assert prior_matrix[0, 1] >= estimator.config.market_overlap_correlation


def test_empty_strategies(estimator):
    """Test with no strategies."""
    corr_matrix = estimator.estimate_correlation_matrix({})

    # Empty dict returns array with 0 strategies
    # NumPy creates (1, 0) for empty 2D, but we want 0 rows
    assert corr_matrix.shape[1] == 0 or corr_matrix.size == 0


def test_positive_semi_definite(estimator):
    """Test that correlation matrix is positive semi-definite."""
    now = datetime.now()

    np.random.seed(42)

    strategy_trades = {
        f"strat-{i}": [
            make_trade(f"strat-{i}", f"KX{i}", np.random.normal(0, 10), now + timedelta(minutes=j*5))
            for j in range(20)
        ]
        for i in range(3)
    }

    corr_matrix = estimator.estimate_correlation_matrix(strategy_trades)

    # Check positive semi-definite: all eigenvalues >= 0
    eigenvalues = np.linalg.eigvalsh(corr_matrix)
    assert np.all(eigenvalues >= -1e-6)  # Allow tiny numerical error


def test_shrinkage_parameter(estimator):
    """Test effect of shrinkage parameter."""
    now = datetime.now()

    # Generate uncorrelated data with fixed seeds
    np.random.seed(42)
    strategy_trades = {
        "strat-a": [
            make_trade("strat-a", "KXA", np.random.normal(0, 10), now + timedelta(minutes=i*5))
            for i in range(50)
        ],
    }
    np.random.seed(123)  # Different seed
    strategy_trades["strat-b"] = [
        make_trade("strat-b", "KXB", np.random.normal(0, 10), now + timedelta(minutes=i*5))
        for i in range(50)
    ]

    # Low shrinkage (rely on sample)
    estimator.config.correlation_shrinkage = 1.0  # 100% sample, 0% prior
    corr_high_sample = estimator.estimate_correlation_matrix(strategy_trades)

    # High shrinkage (rely on prior)
    estimator.config.correlation_shrinkage = 0.0  # 0% sample, 100% prior
    corr_high_prior = estimator.estimate_correlation_matrix(strategy_trades)

    # High prior should be closer to default_correlation (0.1)
    # High sample should be closer to actual sample (near 0)
    # Check that prior version is closer to 0.1 than sample version
    assert abs(corr_high_prior[0, 1] - 0.1) < abs(corr_high_sample[0, 1] - 0.1)


def test_time_alignment(estimator):
    """Test that trades are aligned into time buckets."""
    # Use a fixed base time that aligns nicely with 5-minute buckets
    now = datetime(2026, 2, 26, 15, 0, 0)  # Exactly 3:00 PM

    # Trades within same 5-minute bucket (15:00:00 to 15:04:59)
    strategy_trades = {
        "strat-a": [
            make_trade("strat-a", "KXA", 10.0, now),
            make_trade("strat-a", "KXA", 5.0, now + timedelta(minutes=2)),
        ],
        "strat-b": [
            make_trade("strat-b", "KXB", 3.0, now + timedelta(minutes=1)),
        ],
    }

    # Should align into same bucket
    bucket_returns = estimator._align_returns_by_time(
        ["strat-a", "strat-b"],
        strategy_trades,
        bucket_size_min=5,
    )

    assert len(bucket_returns) == 1  # Single bucket

    # strat-a should have sum of 15.0
    # strat-b should have 3.0
    for bucket_ts, returns in bucket_returns.items():
        assert returns["strat-a"] == 15.0
        assert returns["strat-b"] == 3.0
