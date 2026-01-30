"""Trading strategies module.

This module provides the base class and data structures for implementing
trading strategies that integrate with the existing infrastructure.

Example:
    >>> from src.strategies import TradingStrategy, StrategyConfig, Signal
    >>>
    >>> class MyStrategy(TradingStrategy):
    ...     def evaluate(self, market):
    ...         return []
    ...     def on_fill(self, fill):
    ...         pass
    >>>
    >>> config = StrategyConfig(name="my_strategy", tickers=["TICKER-A"])
    >>> strategy = MyStrategy(client, config)
    >>> strategy.start()
"""

from src.strategies.base import (
    Signal,
    StrategyConfig,
    StrategyState,
    TradingStrategy,
)
from src.strategies.nba_mispricing import (
    DualOrderState,
    GameContext,
    NBAMispricingConfig,
    NBAMispricingStrategy,
)
from src.strategies.crypto_latency import (
    CryptoLatencyConfig,
    CryptoLatencyOrchestrator,
    CryptoMarket,
    CryptoMarketScanner,
    LatencyDetector,
    LatencyExecutor,
    Opportunity,
)

__all__ = [
    # Base strategy
    "Signal",
    "StrategyConfig",
    "StrategyState",
    "TradingStrategy",
    # NBA mispricing
    "DualOrderState",
    "GameContext",
    "NBAMispricingConfig",
    "NBAMispricingStrategy",
    # Crypto latency
    "CryptoLatencyConfig",
    "CryptoLatencyOrchestrator",
    "CryptoMarket",
    "CryptoMarketScanner",
    "LatencyDetector",
    "LatencyExecutor",
    "Opportunity",
]
