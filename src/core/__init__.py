"""Core module for trading utilities.

Most submodules were consolidated into the top-level core/ package.
This legacy src/core/ package re-exports models used by the backtest
framework and other src.core consumers.
"""

# Models — restored for backtest framework compatibility
from .models import (
    Fill,
    Market,
    MarketState,
    Position,
    Quote,
    Snapshot,
    SummaryStats,
    ValidationError,
)

# Cross-package re-export (top-level core.trading_state)
try:
    from core.trading_state import TradingState, get_trading_state
except ImportError:
    TradingState = None  # type: ignore[assignment,misc]
    get_trading_state = None  # type: ignore[assignment]

__all__ = [
    "Fill",
    "Market",
    "MarketState",
    "Position",
    "Quote",
    "Snapshot",
    "SummaryStats",
    "ValidationError",
    "TradingState",
    "get_trading_state",
]
