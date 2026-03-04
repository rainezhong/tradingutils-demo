"""Market module.

Provides abstract interface and concrete implementations for market data.
"""

from .i_market import I_Market
from .market_types import OrderBook
from .kalshi_market import KalshiMarket

__all__ = [
    # Interface
    "I_Market",
    # Types
    "OrderBook",
    # Kalshi Implementation
    "KalshiMarket",
]
