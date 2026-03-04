"""Unified backtest framework.

Provides:
- BacktestEngine: main loop (iterate frames, evaluate signals, fill, track P&L)
- BacktestAdapter: ABC for strategy-specific signal generation
- DataFeed / BacktestFrame: data source abstraction
- FillModel: order fill simulation
- PositionTracker: position and bankroll accounting
- BacktestMetrics / BacktestResult: reporting
- validation: statistical validation suite (Sharpe, MC, bootstrap, permutation, walk-forward)
"""

from .data_feed import BacktestFrame, DataFeed
from .fill_model import (
    FillModel,
    ImmediateFillModel,
    kalshi_taker_fee,
)
from .depth_estimation import (
    estimate_depth_from_spread,
    get_orderbook_depth_with_fallback,
    DEFAULT_LOW_DEPTH,
    DEFAULT_WIDE_SPREAD_CENTS,
    DEFAULT_BASE_DEPTH,
)
from .repricing_lag import KalshiRepricingConfig, check_kalshi_staleness
from .realism_config import (
    BacktestRealismConfig,
    RepricingLagConfig,
    QueuePriorityConfig,
    NetworkLatencyConfig,
    OrderbookStalenessConfig,
    MarketImpactConfig,
)
from .portfolio import PositionTracker
from .metrics import BacktestMetadata, BacktestMetrics, BacktestResult
from .engine import BacktestEngine, BacktestAdapter, BacktestConfig

__all__ = [
    "BacktestFrame",
    "DataFeed",
    "FillModel",
    "ImmediateFillModel",
    "kalshi_taker_fee",
    "KalshiRepricingConfig",
    "check_kalshi_staleness",
    "BacktestRealismConfig",
    "RepricingLagConfig",
    "QueuePriorityConfig",
    "NetworkLatencyConfig",
    "OrderbookStalenessConfig",
    "MarketImpactConfig",
    "estimate_depth_from_spread",
    "get_orderbook_depth_with_fallback",
    "DEFAULT_LOW_DEPTH",
    "DEFAULT_WIDE_SPREAD_CENTS",
    "DEFAULT_BASE_DEPTH",
    "PositionTracker",
    "BacktestMetadata",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestEngine",
    "BacktestAdapter",
    "BacktestConfig",
]
