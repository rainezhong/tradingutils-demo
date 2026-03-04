"""
Type definitions for portfolio allocation system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class StrategyStats:
    """Performance statistics for a single strategy."""

    strategy_name: str
    total_pnl: float
    num_trades: int
    edge: float  # Mean PnL per trade
    variance: float  # Variance of returns
    std_dev: float  # Standard deviation
    sharpe_ratio: float
    win_rate: float
    avg_win: float
    avg_loss: float
    lookback_days: int
    last_updated: datetime


@dataclass
class AllocationResult:
    """Result of portfolio allocation optimization."""

    allocations: Dict[str, float]  # strategy_name -> fraction of bankroll
    total_allocated: float
    expected_growth_rate: float  # Portfolio geometric growth rate
    portfolio_variance: float
    portfolio_sharpe: float
    timestamp: datetime
    rebalance_reason: str  # "scheduled" | "performance_trigger" | "manual"

    def __post_init__(self):
        """Validate allocations."""
        if not 0.0 <= self.total_allocated <= 1.0:
            raise ValueError(f"Invalid total allocation: {self.total_allocated}")

        for strategy, fraction in self.allocations.items():
            if not 0.0 <= fraction <= 1.0:
                raise ValueError(
                    f"Invalid allocation for {strategy}: {fraction}"
                )


@dataclass
class AllocationConfig:
    """Configuration for allocation optimizer."""

    kelly_fraction: float = 0.5  # Half Kelly (conservative)
    max_allocation_per_strategy: float = 0.25  # Max 25% per strategy
    max_total_allocation: float = 0.80  # Max 80% deployed (20% reserve)
    min_allocation_threshold: float = 0.05  # Ignore allocations < 5%
    min_trades_per_strategy: int = 10  # Need ≥10 trades for allocation
    ridge_regularization: float = 1e-6  # Covariance matrix regularization

    # Empirical Kelly with Monte Carlo uncertainty adjustment
    use_empirical_kelly: bool = False  # Enable CV-based haircut
    empirical_kelly_simulations: int = 1000  # Monte Carlo simulations
    empirical_kelly_seed: Optional[int] = None  # Random seed for reproducibility

    def __post_init__(self):
        """Validate configuration."""
        if not 0.0 < self.kelly_fraction <= 1.0:
            raise ValueError(f"Invalid Kelly fraction: {self.kelly_fraction}")

        if not 0.0 < self.max_allocation_per_strategy <= 1.0:
            raise ValueError(
                f"Invalid max per-strategy: {self.max_allocation_per_strategy}"
            )

        if not 0.0 < self.max_total_allocation <= 1.0:
            raise ValueError(
                f"Invalid max total: {self.max_total_allocation}"
            )

        if self.empirical_kelly_simulations < 100:
            raise ValueError(
                f"empirical_kelly_simulations must be >= 100: {self.empirical_kelly_simulations}"
            )


@dataclass
class PortfolioConfig:
    """Configuration for portfolio manager."""

    enabled: bool = False

    # Rebalancing
    rebalance_interval_sec: int = 86400  # Daily
    rebalance_on_pnl_change_pct: float = 0.20  # ±20% bankroll change
    rebalance_min_interval_sec: int = 43200  # Rate limit: 12 hours

    # Allocation
    allocation: AllocationConfig = field(default_factory=AllocationConfig)

    # Performance estimation
    lookback_days: int = 30

    # Correlation
    correlation_shrinkage: float = 0.70  # 70% sample, 30% prior
    prior_correlations: Dict[str, float] = field(default_factory=dict)
    default_correlation: float = 0.1
    market_overlap_threshold: float = 0.20  # 20% ticker overlap
    market_overlap_correlation: float = 0.5  # Force correlation if overlap

    # Copula (tail dependence modeling)
    copula_type: str = "gaussian"  # "gaussian" | "student-t"
    copula_df: Optional[float] = None  # Degrees of freedom for student-t (None = auto-estimate)

    # Data
    trade_db_path: str = "data/portfolio_trades.db"

    def get_prior_correlation(self, strategy1: str, strategy2: str) -> float:
        """Get prior correlation between two strategies."""
        if strategy1 == strategy2:
            return 1.0

        # Check both orderings
        key1 = f"{strategy1}:{strategy2}"
        key2 = f"{strategy2}:{strategy1}"

        return self.prior_correlations.get(
            key1,
            self.prior_correlations.get(key2, self.default_correlation)
        )


@dataclass
class StrategyTrade:
    """Individual trade record."""

    id: Optional[int]
    strategy_name: str
    ticker: str
    timestamp: datetime
    side: str  # "buy" | "sell"
    price: float
    size: int
    pnl: Optional[float]  # None until settled
    settled_at: Optional[datetime]  # When position closed
