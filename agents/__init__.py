"""Autonomous trading agents.

This package contains agents for automated strategy development,
backtesting, and validation.
"""

from .backtest_runner import (
    BacktestRunnerAgent,
    BacktestResults,
    ValidationMetrics,
    WalkForwardResults,
)
from .data_scout import DataScoutAgent, Hypothesis
from .hypothesis_generator import (
    HypothesisGeneratorAgent,
    TradingHypothesis,
    MarketType,
    HypothesisConfidence,
)
from .report_generator import HypothesisInfo, ReportGeneratorAgent

__all__ = [
    "BacktestRunnerAgent",
    "BacktestResults",
    "DataScoutAgent",
    "Hypothesis",
    "HypothesisInfo",
    "ReportGeneratorAgent",
    "ValidationMetrics",
    "WalkForwardResults",
    "HypothesisGeneratorAgent",
    "TradingHypothesis",
    "MarketType",
    "HypothesisConfidence",
]
