"""Backtest validation suite.

Provides statistical validation tools for backtest results:
- ExtendedMetrics: Sharpe, Sortino, Calmar, profit factor, etc.
- MonteCarloSimulator: shuffle/resample/null hypothesis testing
- BootstrapAnalyzer: confidence intervals via bootstrap resampling
- PermutationTester: p-values for PnL and win rate
- WalkForwardRunner: time-series cross-validation
- ValidationSuite: orchestrator that runs all analyses
"""

from .trade_analysis import TradePnL, TradeDistribution, compute_trade_pnls, compute_trade_distribution
from .extended_metrics import ExtendedMetrics
from .monte_carlo import MonteCarloConfig, MonteCarloMode, MonteCarloResult, MonteCarloSimulator
from .bootstrap import BootstrapAnalyzer, BootstrapConfig, BootstrapResult, ConfidenceInterval
from .permutation_test import PermutationConfig, PermutationResult, PermutationTester
from .walk_forward import (
    SlicedDataFeed,
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardResult,
    WalkForwardRunner,
)
from .report import ValidationSuite, run_validation_suite

__all__ = [
    # Trade analysis
    "TradePnL",
    "TradeDistribution",
    "compute_trade_pnls",
    "compute_trade_distribution",
    # Extended metrics
    "ExtendedMetrics",
    # Monte Carlo
    "MonteCarloConfig",
    "MonteCarloMode",
    "MonteCarloResult",
    "MonteCarloSimulator",
    # Bootstrap
    "BootstrapAnalyzer",
    "BootstrapConfig",
    "BootstrapResult",
    "ConfidenceInterval",
    # Permutation
    "PermutationConfig",
    "PermutationResult",
    "PermutationTester",
    # Walk-forward
    "SlicedDataFeed",
    "WalkForwardConfig",
    "WalkForwardFold",
    "WalkForwardResult",
    "WalkForwardRunner",
    # Report
    "ValidationSuite",
    "run_validation_suite",
]
