"""Order Management System (OMS) for multi-exchange trading.

This module provides centralized order tracking, execution, and management
across multiple prediction market exchanges (Kalshi, Polymarket).

Core Components:
    OrderManagementSystem: Central hub for order lifecycle management
    SpreadExecutor: Two-leg atomic spread execution with rollback
    CapitalManager: Capital reservation to prevent double-spending
    TimeoutManager: Automatic order timeout handling
    Reconciler: Exchange state reconciliation

Models:
    TrackedOrder: Order with full lifecycle tracking
    FailedOrder: Captured failure details
    ReconciliationReport: Reconciliation results
    SpreadLeg: Individual leg of a spread
    SpreadExecutionResult: Spread execution outcome

Example Usage:
    >>> from src.oms import (
    ...     OrderManagementSystem,
    ...     SpreadExecutor,
    ...     CapitalManager,
    ... )
    >>>
    >>> # Initialize components
    >>> capital_mgr = CapitalManager()
    >>> oms = OrderManagementSystem(capital_manager=capital_mgr)
    >>> spread_executor = SpreadExecutor(oms, capital_mgr)
    >>>
    >>> # Register exchanges
    >>> oms.register_exchange(kalshi_client)
    >>> oms.register_exchange(polymarket_client)
    >>>
    >>> # Start background services
    >>> oms.start()
    >>>
    >>> # Submit orders
    >>> order = oms.submit_order(
    ...     exchange="kalshi",
    ...     ticker="AAPL-YES",
    ...     side="buy",
    ...     price=0.55,
    ...     size=100,
    ... )
    >>>
    >>> # Execute spreads
    >>> result = spread_executor.execute_spread(
    ...     opportunity_id="opp_123",
    ...     leg1_exchange="kalshi",
    ...     leg1_ticker="AAPL-YES",
    ...     leg1_side="buy",
    ...     leg1_price=0.45,
    ...     leg1_size=100,
    ...     leg2_exchange="polymarket",
    ...     leg2_ticker="AAPL-YES",
    ...     leg2_side="sell",
    ...     leg2_price=0.48,
    ...     leg2_size=100,
    ... )
    >>>
    >>> # Cleanup
    >>> oms.stop()
"""

# Models
from .models import (
    FailedOrder,
    FailureReason,
    LegStatus,
    OrderStatus,
    PositionInventory,
    ReconciliationMismatch,
    ReconciliationReport,
    SpreadExecutionResult,
    SpreadExecutionStatus,
    SpreadLeg,
    TrackedOrder,
    generate_idempotency_key,
)

# Capital management
from .capital_manager import (
    CapitalManager,
    CapitalReservation,
    CapitalState,
)

# Resolution tracking
from .resolution_tracker import (
    PendingResolution,
    PendingResolutionTracker,
)

# Timeout management
from .timeout_manager import (
    OrderTimeoutTracker,
    TimeoutConfig,
    TimeoutEntry,
    TimeoutManager,
)

# Reconciliation
from .reconciliation import (
    OrderReconciler,
    PositionReconciler,
    Reconciler,
)

# Core OMS
from .order_manager import (
    OMSConfig,
    OrderManagementSystem,
)

# Spread execution
from .spread_executor import (
    SpreadExecutor,
    SpreadExecutorConfig,
)


__all__ = [
    # Models
    "TrackedOrder",
    "FailedOrder",
    "FailureReason",
    "OrderStatus",
    "LegStatus",
    "SpreadLeg",
    "SpreadExecutionResult",
    "SpreadExecutionStatus",
    "ReconciliationMismatch",
    "ReconciliationReport",
    "PositionInventory",
    "generate_idempotency_key",
    # Capital
    "CapitalManager",
    "CapitalReservation",
    "CapitalState",
    # Resolution tracking
    "PendingResolution",
    "PendingResolutionTracker",
    # Timeout
    "TimeoutManager",
    "TimeoutConfig",
    "TimeoutEntry",
    "OrderTimeoutTracker",
    # Reconciliation
    "Reconciler",
    "OrderReconciler",
    "PositionReconciler",
    # OMS
    "OrderManagementSystem",
    "OMSConfig",
    # Spread
    "SpreadExecutor",
    "SpreadExecutorConfig",
]
