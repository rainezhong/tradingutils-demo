"""Analysis module for market evaluation and ranking.

This module provides tools for analyzing prediction market data:
- MarketMetrics: Calculate spread, volume, volatility, and depth metrics
- MarketScorer: Score markets based on trading opportunity criteria
- MarketRanker: Rank and compare markets across the database
- CorrelationDetector: Identify potentially correlated markets
- StrategyLabeler: Label markets with applicable trading strategies
- TradabilityFilter: Configure filters for untradeable markets
"""

from .correlation import CorrelationDetector, CorrelationMatch
from .metrics import MarketMetrics
from .ranker import MarketRanker, TradabilityFilter
from .scorer import MarketScorer
from .strategy import StrategyLabeler, StrategyLabel, TradingStrategy

__all__ = [
    "MarketMetrics",
    "MarketScorer",
    "MarketRanker",
    "TradabilityFilter",
    "CorrelationDetector",
    "CorrelationMatch",
    "StrategyLabeler",
    "StrategyLabel",
    "TradingStrategy",
]
