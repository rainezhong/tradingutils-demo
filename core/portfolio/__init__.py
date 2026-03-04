"""
Portfolio allocation optimizer for multi-strategy execution.

Implements multi-variate Kelly criterion to optimally allocate capital across
strategies accounting for edge, variance, and correlation. Supports copula-based
modeling for tail dependence.
"""

from core.portfolio.types import (
    StrategyStats,
    AllocationResult,
    AllocationConfig,
    PortfolioConfig,
)
from core.portfolio.performance_tracker import PerformanceTracker
from core.portfolio.correlation_estimator import CorrelationEstimator
from core.portfolio.allocation_optimizer import AllocationOptimizer
from core.portfolio.portfolio_manager import PortfolioManager
from core.portfolio.copula import (
    GaussianCopula,
    StudentTCopula,
    estimate_t_copula_df,
    estimate_tail_dependence_empirical,
)

__all__ = [
    "StrategyStats",
    "AllocationResult",
    "AllocationConfig",
    "PortfolioConfig",
    "PerformanceTracker",
    "CorrelationEstimator",
    "AllocationOptimizer",
    "PortfolioManager",
    "GaussianCopula",
    "StudentTCopula",
    "estimate_t_copula_df",
    "estimate_tail_dependence_empirical",
]
