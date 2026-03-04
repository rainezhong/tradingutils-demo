"""Backtest adapters for specific strategy families."""

from .nba_adapter import (
    NBADataFeed,
    NBAMispricingAdapter,
    BlowoutAdapter,
    TotalPointsAdapter,
)
from .crypto_adapter import CryptoLatencyDataFeed, CryptoLatencyAdapter
from .arb_adapter import ArbPairDataFeed, ArbAdapter
from .generic_adapter import TradingStrategyAdapter

__all__ = [
    "NBADataFeed",
    "NBAMispricingAdapter",
    "BlowoutAdapter",
    "TotalPointsAdapter",
    "CryptoLatencyDataFeed",
    "CryptoLatencyAdapter",
    "ArbPairDataFeed",
    "ArbAdapter",
    "TradingStrategyAdapter",
]
