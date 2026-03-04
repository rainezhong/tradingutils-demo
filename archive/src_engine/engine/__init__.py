"""Engine layer for integrating market-making components.

This module provides the glue that ties together:
- MarketMaker (strategy)
- QuoteManager (execution)
- RiskManager (safety)
"""

from .market_making_engine import MarketMakingEngine
from .multi_market_engine import MultiMarketEngine

__all__ = [
    "MarketMakingEngine",
    "MultiMarketEngine",
]
