"""Arbitrage detection and monitoring tools."""

from .live_arb import (
    LiveArbMonitor,
    all_in_buy_cost,
    all_in_sell_proceeds,
    fee_per_contract,
    kalshi_fee_total,
    live_plot_kalshi_pair,
    live_plot_monitor,
)
from .spread_detector import (
    Platform,
    FeeStructure,
    PLATFORM_FEES,
    MarketQuote,
    MatchedMarketPair,
    SpreadOpportunity,
    SpreadAlert,
    SpreadDetector,
    create_detector,
    calculate_fee,
)
from .config import ArbitrageConfig
from .fee_calculator import FeeCalculator
from .circuit_breaker import CircuitBreaker, CircuitBreakerState
from .detector import OpportunityDetector, RankedOpportunity
from .orchestrator import ArbitrageOrchestrator
from .preflight import PreflightChecker

__all__ = [
    # Live arb monitor (single exchange)
    "LiveArbMonitor",
    "live_plot_monitor",
    "live_plot_kalshi_pair",
    "kalshi_fee_total",
    "fee_per_contract",
    "all_in_buy_cost",
    "all_in_sell_proceeds",
    # Cross-platform spread detector
    "Platform",
    "FeeStructure",
    "PLATFORM_FEES",
    "MarketQuote",
    "MatchedMarketPair",
    "SpreadOpportunity",
    "SpreadAlert",
    "SpreadDetector",
    "create_detector",
    "calculate_fee",
    # Arbitrage orchestration
    "ArbitrageConfig",
    "FeeCalculator",
    "CircuitBreaker",
    "CircuitBreakerState",
    "OpportunityDetector",
    "RankedOpportunity",
    "ArbitrageOrchestrator",
    "PreflightChecker",
]
