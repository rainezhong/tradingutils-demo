"""Arbitrage execution algorithms and infrastructure.

This module provides execution algorithms for spread opportunities detected
by the SpreadDetector. It includes three execution strategies, leg risk
management, and metrics collection.

Execution Algorithms:
- SimultaneousLimitExecutor: Post limit orders on both platforms at once
- SequentialMarketExecutor: Execute fast side first, then slow side
- AdaptiveExecutor: Start with limits, escalate to aggressive prices

Usage:
    from arb.execution import ArbExecutionHandler, ExecutionConfig
    from arb.spread_detector import SpreadDetector

    # Configure execution
    config = ExecutionConfig(
        max_contracts=100,
        min_edge_to_execute=0.02,
    )

    # Create handler with exchange clients
    handler = ArbExecutionHandler(
        exchange_clients={
            Platform.KALSHI: kalshi_client,
            Platform.POLYMARKET: poly_client,
        },
        algorithm="adaptive",
        config=config,
    )

    # Connect to SpreadDetector
    detector = SpreadDetector(
        market_matcher=matcher,
        on_alert=handler.on_alert,
    )
    detector.start()
"""

# Base classes and data structures
from .base import (
    ArbExecutor,
    ExecutionConfig,
    ExecutionResult,
    ExecutionState,
    LegExecution,
)

# Execution algorithms
from .algorithms import (
    AdaptiveExecutor,
    SequentialMarketExecutor,
    SimultaneousLimitExecutor,
)

# Leg risk management
from .leg_tracker import (
    LegRiskAction,
    LegRiskEvent,
    LegRiskManager,
)

# Metrics collection
from .metrics import (
    ExecutionMetrics,
    MetricsCollector,
    PlatformMetrics,
)

# SpreadDetector integration
from .handler import ArbExecutionHandler

__all__ = [
    # Base
    "ArbExecutor",
    "ExecutionConfig",
    "ExecutionResult",
    "ExecutionState",
    "LegExecution",
    # Algorithms
    "AdaptiveExecutor",
    "SequentialMarketExecutor",
    "SimultaneousLimitExecutor",
    # Leg risk
    "LegRiskAction",
    "LegRiskEvent",
    "LegRiskManager",
    # Metrics
    "ExecutionMetrics",
    "MetricsCollector",
    "PlatformMetrics",
    # Handler
    "ArbExecutionHandler",
]
