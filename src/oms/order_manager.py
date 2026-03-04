"""Centralized Order Management System for multi-exchange trading.

Provides unified order tracking, submission, and lifecycle management
across multiple exchanges with idempotency, timeout handling, and reconciliation.
"""

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from src.core.exchange import ExchangeClient
from src.core.models import Position
from src.core.orderbook_manager import OrderBookManager
from src.risk.risk_manager import RiskManager

from .capital_manager import CapitalManager
from .fill_notifier import FillEvent, FillNotifier
from .models import (
    Action,
    ConstraintViolation,
    FailedOrder,
    FailureReason,
    OrderConstraints,
    OrderStatus,
    Outcome,
    PositionInventory,
    ReconciliationReport,
    TrackedOrder,
    generate_idempotency_key,
)
from .reconciliation import Reconciler
from .timeout_manager import OrderTimeoutTracker, TimeoutConfig, TimeoutManager


logger = logging.getLogger(__name__)


@dataclass
class OMSConfig:
    """Configuration for the Order Management System.

    Attributes:
        default_timeout_seconds: Default order timeout
        reconciliation_interval_seconds: How often to reconcile with exchanges
        max_retries: Maximum retry attempts for order operations
        retry_delay_seconds: Base delay between retries
        enable_auto_reconciliation: Whether to auto-reconcile periodically
        enable_timeout_manager: Whether to use automatic timeouts
        order_retention_seconds: How long to keep completed orders in memory
        enable_order_cleanup: Whether to automatically clean up old orders
    """

    default_timeout_seconds: float = 60.0
    reconciliation_interval_seconds: float = 60.0
    max_retries: int = 3
    retry_delay_seconds: float = 0.5
    enable_auto_reconciliation: bool = True
    enable_timeout_manager: bool = True
    order_retention_seconds: float = 3600.0  # 1 hour default
    enable_order_cleanup: bool = True
    callback_timeout_seconds: float = 5.0  # Max time for callback execution
    callback_pool_size: int = 2  # Thread pool size for callbacks


class OrderManagementSystem:
    """Centralized order tracking across multiple exchanges.

    Manages order lifecycle from creation through fill/cancel, with:
    - Idempotency keys to prevent duplicate orders
    - Automatic timeout handling
    - Periodic reconciliation with exchanges
    - Position inventory tracking
    - Failed order capture

    Example:
        >>> oms = OrderManagementSystem()
        >>> oms.register_exchange(kalshi_client)
        >>> oms.register_exchange(polymarket_client)
        >>> oms.start()
        >>>
        >>> # Submit order with idempotency
        >>> order = oms.submit_order(
        ...     exchange="kalshi",
        ...     ticker="AAPL-2024",
        ...     side="buy",
        ...     price=0.55,
        ...     size=100,
        ...     timeout_seconds=30
        ... )
        >>>
        >>> # Check status
        >>> status = oms.get_order_status(order.order_id, "kalshi")
        >>>
        >>> # Cancel if needed
        >>> oms.cancel_order(order.order_id, "kalshi")
        >>>
        >>> oms.stop()
    """

    def __init__(
        self,
        config: Optional[OMSConfig] = None,
        capital_manager: Optional[CapitalManager] = None,
        fill_notifier: Optional[FillNotifier] = None,
        orderbook_manager: Optional[OrderBookManager] = None,
        risk_manager: Optional[RiskManager] = None,
    ) -> None:
        """Initialize OrderManagementSystem.

        Args:
            config: Optional configuration (uses defaults if not provided)
            capital_manager: Optional capital manager for reservations
            fill_notifier: Optional fill notifier for real-time WebSocket fills
            orderbook_manager: Optional orderbook manager for constraint validation
            risk_manager: Optional risk manager for trade validation
        """
        self._config = config or OMSConfig()
        self._capital_manager = capital_manager
        self._fill_notifier = fill_notifier
        self._orderbook_manager = orderbook_manager
        self._risk_manager = risk_manager

        # Exchange clients
        self._clients: Dict[str, ExchangeClient] = {}

        # Order tracking
        self._pending_orders: Dict[str, TrackedOrder] = {}  # by idempotency_key
        self._active_orders: Dict[str, TrackedOrder] = {}  # by order_id
        self._failed_orders: List[FailedOrder] = []

        # Position inventory
        self._positions = PositionInventory()

        # Reconcilers
        self._reconcilers: Dict[str, Reconciler] = {}

        # Timeout management
        self._timeout_config = TimeoutConfig(
            default_timeout_seconds=self._config.default_timeout_seconds,
        )
        self._timeout_manager = TimeoutManager(self._timeout_config)
        self._timeout_tracker = OrderTimeoutTracker(
            self._timeout_manager,
            default_timeout_seconds=self._config.default_timeout_seconds,
            on_timeout=self._handle_order_timeout,
        )

        # Threading
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._reconciliation_thread: Optional[threading.Thread] = None

        # Callbacks
        self._on_fill: Optional[Callable[[TrackedOrder, int, float], None]] = None
        self._on_cancel: Optional[Callable[[TrackedOrder], None]] = None
        self._on_reject: Optional[Callable[[FailedOrder], None]] = None

        # Callback executor for non-blocking callback invocation
        self._callback_executor = ThreadPoolExecutor(
            max_workers=self._config.callback_pool_size,
            thread_name_prefix="OMS-Callback",
        )

        # Register for WebSocket fill notifications
        if self._fill_notifier:
            self._fill_notifier.add_listener(self._handle_ws_fill)

        # Register for orderbook updates to monitor constraints in real-time
        if self._orderbook_manager:
            self._orderbook_manager.add_update_listener(self._handle_orderbook_update)

        # Track orders with active constraint monitoring by ticker
        self._constrained_orders: Dict[str, List[str]] = {}  # ticker -> [order_ids]

    def register_exchange(self, client: ExchangeClient) -> None:
        """Register an exchange client for order management.

        Args:
            client: Exchange client to register
        """
        with self._lock:
            self._clients[client.name] = client
            self._reconcilers[client.name] = Reconciler(
                client,
                on_order_mismatch=self._handle_order_mismatch,
                on_position_mismatch=self._handle_position_mismatch,
            )

            # Sync initial balance if capital manager exists
            if self._capital_manager:
                self._capital_manager.sync_from_exchange(client)

            logger.info("Registered exchange: %s", client.name)

    def start(self) -> "OrderManagementSystem":
        """Start the OMS background services.

        Returns:
            Self for chaining
        """
        if self._config.enable_timeout_manager:
            self._timeout_manager.start()

        if self._config.enable_auto_reconciliation:
            self._stop_event.clear()
            self._reconciliation_thread = threading.Thread(
                target=self._reconciliation_loop,
                daemon=True,
                name="OMS-Reconciliation",
            )
            self._reconciliation_thread.start()

        logger.info("OrderManagementSystem started")
        return self

    def stop(self) -> None:
        """Stop the OMS background services."""
        self._stop_event.set()

        if self._reconciliation_thread:
            self._reconciliation_thread.join(timeout=2.0)

        self._timeout_manager.stop()

        # Shutdown callback executor
        self._callback_executor.shutdown(wait=False)

        logger.info("OrderManagementSystem stopped")

    def _record_portfolio_fill(
        self,
        order: TrackedOrder,
        fill_size: int,
        fill_price: int,
    ) -> None:
        """Record fill to portfolio trade database.

        Only records if portfolio optimization is enabled.

        Args:
            order: The order that was filled
            fill_size: Size of this fill
            fill_price: Price of this fill (in cents)
        """
        try:
            import os
            if os.getenv("ENABLE_PORTFOLIO_OPT") != "true":
                return

            from core.portfolio import PerformanceTracker

            # Record the fill
            # Note: PnL will be calculated when position is closed/settled
            tracker = PerformanceTracker()
            tracker.record_trade(
                strategy_name=getattr(order, "strategy_name", "unknown"),
                ticker=order.ticker,
                timestamp=datetime.now(),
                side="buy" if order.side == "buy" else "sell",
                price=fill_price / 100.0,  # Convert cents to dollars
                size=fill_size,
                pnl=None,  # PnL calculated on position close
                settled_at=None,
            )

            logger.debug(
                f"Recorded fill to portfolio: {order.ticker} "
                f"{fill_size}@{fill_price/100.0:.2f}"
            )

        except Exception as e:
            # Don't fail order processing if portfolio tracking fails
            logger.warning(f"Failed to record portfolio fill: {e}")

    def _invoke_callback(
        self,
        callback: Callable,
        *args,
        callback_name: str = "callback",
    ) -> None:
        """Invoke a callback asynchronously with timeout protection.

        Submits the callback to the thread pool for non-blocking execution.
        Logs errors if the callback fails or times out.

        Args:
            callback: The callback function to invoke
            *args: Arguments to pass to the callback
            callback_name: Name for logging purposes
        """

        def _run_callback():
            try:
                callback(*args)
            except Exception as e:
                logger.error("Error in %s: %s", callback_name, e)

        try:
            future = self._callback_executor.submit(_run_callback)
            # We don't wait for the result - fire and forget
            # But log if it fails
            future.add_done_callback(
                lambda f: (
                    logger.error("%s raised: %s", callback_name, f.exception())
                    if f.exception()
                    else None
                )
            )
        except Exception as e:
            logger.error("Failed to submit %s: %s", callback_name, e)

    def submit_order(
        self,
        exchange: str,
        ticker: str,
        side: str,
        price: float,
        size: int,
        idempotency_key: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        metadata: Optional[Dict] = None,
        constraints: Optional[OrderConstraints] = None,
        reserve_capital: bool = False,
        outcome: Optional[Outcome] = None,
        action: Optional[Action] = None,
        strategy_id: Optional[str] = None,
    ) -> TrackedOrder:
        """Submit a new order with idempotency and optional constraints.

        Args:
            exchange: Exchange to submit to
            ticker: Market identifier
            side: 'buy' or 'sell' (can be derived from action if not provided)
            price: Order price
            size: Number of contracts
            idempotency_key: Optional client-generated key (auto-generated if not provided)
            timeout_seconds: Optional timeout (uses default if not provided)
            metadata: Optional additional tracking data
            constraints: Optional price constraints for the order
            reserve_capital: If True and capital_manager is configured, reserve
                            capital before submission and release on completion
            outcome: YES or NO contract (for prediction markets)
            action: BUY or SELL action (if provided and side is empty, side is derived)
            strategy_id: Strategy that placed this order (for attribution/queries)

        Returns:
            TrackedOrder with order details

        Raises:
            ValueError: If exchange not registered, invalid parameters, or constraint violated
            RuntimeError: If order submission fails after retries
        """
        if exchange not in self._clients:
            raise ValueError(f"Exchange not registered: {exchange}")

        # Derive side from action if action provided and side is empty
        if action is not None and not side:
            side = action.to_side()

        if side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {side}")

        # Validate action matches side if both provided
        if action is not None and action.to_side() != side:
            raise ValueError(f"Action {action.value} does not match side '{side}'")

        # Generate or validate idempotency key
        key = idempotency_key or generate_idempotency_key()

        # Check for duplicate submission
        with self._lock:
            if key in self._pending_orders:
                logger.warning("Duplicate order submission blocked: key=%s", key)
                return self._pending_orders[key]

        # Validate constraints against current orderbook
        if constraints:
            violation = self._validate_constraints(
                ticker, side, int(price), constraints
            )
            if violation:
                raise ValueError(
                    f"Order constraint violated: {violation.value} "
                    f"(side={side}, price={price}, ticker={ticker})"
                )

        # Validate against risk limits
        if self._risk_manager:
            current_position = self._positions.get_position(exchange, ticker)
            allowed, reason = self._risk_manager.can_trade(
                ticker=ticker,
                side=side,
                size=size,
                current_position=current_position,
            )
            if not allowed:
                logger.warning(
                    "Order rejected by risk manager: %s (ticker=%s, side=%s, size=%d)",
                    reason,
                    ticker,
                    side,
                    size,
                )
                raise ValueError(f"Risk limit violation: {reason}")

        # Reserve capital if requested
        reservation_id: Optional[str] = None
        if reserve_capital and self._capital_manager:
            # Calculate capital needed (only for buys)
            capital_needed = price * size if side == "buy" else 0.0
            if capital_needed > 0:
                reservation_id = f"ORDER-{key}"
                timeout = timeout_seconds or self._config.default_timeout_seconds
                if not self._capital_manager.reserve(
                    reservation_id=reservation_id,
                    exchange=exchange,
                    amount=capital_needed,
                    purpose=f"Order: {side} {size}x{ticker}@{price}",
                    ttl_seconds=timeout * 2,  # Extra buffer for processing
                ):
                    raise ValueError(
                        f"Insufficient capital: need ${capital_needed:.2f} for "
                        f"{side} {size}x{ticker}@{price}"
                    )

        # Create tracked order
        order_metadata = metadata or {}
        if reservation_id:
            order_metadata["capital_reservation_id"] = reservation_id

        order = TrackedOrder(
            idempotency_key=key,
            exchange=exchange,
            ticker=ticker,
            side=side,
            price=price,
            size=size,
            outcome=outcome,
            action=action,
            strategy_id=strategy_id,
            constraints=constraints,
            metadata=order_metadata,
        )

        # Register as pending
        with self._lock:
            self._pending_orders[key] = order

        # Submit to exchange with retries
        try:
            order = self._submit_with_retry(order)
        except Exception as e:
            # Release capital reservation on failure
            if reservation_id and self._capital_manager:
                self._capital_manager.release(reservation_id)
            # Capture failure
            self._capture_failure(order, FailureReason.EXCHANGE_ERROR, str(e))
            raise

        # Register for timeout tracking
        if self._config.enable_timeout_manager and order.order_id:
            timeout = timeout_seconds or self._config.default_timeout_seconds
            self._timeout_tracker.track(order, timeout)

        # Register for constraint monitoring if enabled
        if constraints and constraints.monitor_while_open and order.order_id:
            self._register_constraint_monitor(order)

        return order

    # =========================================================================
    # 4-Way Prediction Market API
    # =========================================================================

    def buy_yes(
        self,
        exchange: str,
        ticker: str,
        price: float,
        size: int,
        strategy_id: Optional[str] = None,
        **kwargs,
    ) -> TrackedOrder:
        """Buy YES contracts - bet that the event will happen.

        Args:
            exchange: Exchange to submit to
            ticker: Market identifier
            price: Price per YES contract (0-100 cents or 0.0-1.0)
            size: Number of contracts
            strategy_id: Strategy placing this order
            **kwargs: Additional arguments passed to submit_order

        Returns:
            TrackedOrder with order details
        """
        return self.submit_order(
            exchange=exchange,
            ticker=ticker,
            side="buy",
            price=price,
            size=size,
            outcome=Outcome.YES,
            action=Action.BUY,
            strategy_id=strategy_id,
            **kwargs,
        )

    def sell_yes(
        self,
        exchange: str,
        ticker: str,
        price: float,
        size: int,
        strategy_id: Optional[str] = None,
        **kwargs,
    ) -> TrackedOrder:
        """Sell YES contracts - close a YES position or short.

        Args:
            exchange: Exchange to submit to
            ticker: Market identifier
            price: Price per YES contract
            size: Number of contracts
            strategy_id: Strategy placing this order
            **kwargs: Additional arguments passed to submit_order

        Returns:
            TrackedOrder with order details
        """
        return self.submit_order(
            exchange=exchange,
            ticker=ticker,
            side="sell",
            price=price,
            size=size,
            outcome=Outcome.YES,
            action=Action.SELL,
            strategy_id=strategy_id,
            **kwargs,
        )

    def buy_no(
        self,
        exchange: str,
        ticker: str,
        price: float,
        size: int,
        strategy_id: Optional[str] = None,
        **kwargs,
    ) -> TrackedOrder:
        """Buy NO contracts - bet that the event will NOT happen.

        Args:
            exchange: Exchange to submit to
            ticker: Market identifier
            price: Price per NO contract (0-100 cents or 0.0-1.0)
            size: Number of contracts
            strategy_id: Strategy placing this order
            **kwargs: Additional arguments passed to submit_order

        Returns:
            TrackedOrder with order details
        """
        return self.submit_order(
            exchange=exchange,
            ticker=ticker,
            side="buy",
            price=price,
            size=size,
            outcome=Outcome.NO,
            action=Action.BUY,
            strategy_id=strategy_id,
            **kwargs,
        )

    def sell_no(
        self,
        exchange: str,
        ticker: str,
        price: float,
        size: int,
        strategy_id: Optional[str] = None,
        **kwargs,
    ) -> TrackedOrder:
        """Sell NO contracts - close a NO position or short.

        Args:
            exchange: Exchange to submit to
            ticker: Market identifier
            price: Price per NO contract
            size: Number of contracts
            strategy_id: Strategy placing this order
            **kwargs: Additional arguments passed to submit_order

        Returns:
            TrackedOrder with order details
        """
        return self.submit_order(
            exchange=exchange,
            ticker=ticker,
            side="sell",
            price=price,
            size=size,
            outcome=Outcome.NO,
            action=Action.SELL,
            strategy_id=strategy_id,
            **kwargs,
        )

    def _validate_constraints(
        self,
        ticker: str,
        side: str,
        price: int,
        constraints: OrderConstraints,
    ) -> Optional[ConstraintViolation]:
        """Validate order constraints against current orderbook.

        Args:
            ticker: Market ticker
            side: 'buy' or 'sell'
            price: Order price
            constraints: Constraints to validate

        Returns:
            ConstraintViolation if violated, None if OK
        """
        best_bid = None
        best_ask = None

        # Get current book state if orderbook manager available
        if self._orderbook_manager:
            bid_level = self._orderbook_manager.get_best_bid(ticker)
            ask_level = self._orderbook_manager.get_best_ask(ticker)
            best_bid = bid_level.price if bid_level else None
            best_ask = ask_level.price if ask_level else None

        if side == "buy":
            return constraints.validate_buy(price, best_ask)
        else:
            return constraints.validate_sell(price, best_bid)

    def _register_constraint_monitor(self, order: TrackedOrder) -> None:
        """Register an order for real-time constraint monitoring.

        When the orderbook updates, we'll check if the market has moved
        past the order's constraints and cancel if needed.
        """
        if not order.constraints or not self._orderbook_manager or not order.order_id:
            return

        with self._lock:
            if order.ticker not in self._constrained_orders:
                self._constrained_orders[order.ticker] = []
            if order.order_id not in self._constrained_orders[order.ticker]:
                self._constrained_orders[order.ticker].append(order.order_id)

        logger.debug(
            "Registered constraint monitor for order %s (ticker=%s)",
            order.order_id,
            order.ticker,
        )

    def _unregister_constraint_monitor(self, order: TrackedOrder) -> None:
        """Unregister an order from constraint monitoring."""
        if not order.order_id:
            return

        with self._lock:
            if order.ticker in self._constrained_orders:
                if order.order_id in self._constrained_orders[order.ticker]:
                    self._constrained_orders[order.ticker].remove(order.order_id)
                # Clean up empty lists
                if not self._constrained_orders[order.ticker]:
                    del self._constrained_orders[order.ticker]

    def _release_order_capital(self, order: TrackedOrder) -> None:
        """Release capital reservation for an order if one exists.

        Args:
            order: The order whose capital reservation should be released
        """
        if not self._capital_manager:
            return

        reservation_id = order.metadata.get("capital_reservation_id")
        if reservation_id:
            released = self._capital_manager.release(reservation_id)
            if released:
                logger.debug(
                    "Released capital reservation %s for order %s: $%.2f",
                    reservation_id,
                    order.order_id,
                    released,
                )

    def _handle_orderbook_update(self, ticker: str, state) -> None:
        """Handle orderbook update - check constraints for affected orders.

        Called by OrderBookManager when the book updates. Checks all orders
        with constraints on this ticker and cancels any that violate.

        Args:
            ticker: Market ticker that was updated
            state: OrderBookState with current book
        """
        # Get order IDs for this ticker (copy to avoid lock during cancel)
        with self._lock:
            order_ids = self._constrained_orders.get(ticker, []).copy()

        if not order_ids:
            return

        best_bid = state.best_bid.price if state.best_bid else None
        best_ask = state.best_ask.price if state.best_ask else None

        for order_id in order_ids:
            order = self.get_order(order_id)
            if not order or not order.is_active or not order.constraints:
                # Order completed or no longer has constraints
                self._unregister_constraint_monitor(order) if order else None
                continue

            # Check if market still valid for this order's constraints
            violation = order.constraints.check_market_still_valid(
                order.side,
                int(order.price),
                best_bid,
                best_ask,
            )

            if violation:
                order.constraint_violation = violation
                logger.warning(
                    "Real-time constraint violation for order %s: %s "
                    "(side=%s, price=%s, bid=%s, ask=%s)",
                    order.order_id,
                    violation.value,
                    order.side,
                    order.price,
                    best_bid,
                    best_ask,
                )

                if order.constraints.cancel_on_violation:
                    success = self.cancel_order(order.order_id, order.exchange)
                    if success:
                        logger.info(
                            "Canceled order %s due to real-time constraint violation: %s",
                            order.order_id,
                            violation.value,
                        )

    def check_active_order_constraints(self) -> List[TrackedOrder]:
        """Check all active orders against their constraints.

        Call this periodically (e.g., from reconciliation loop) or
        when orderbook updates to find constraint violations.

        Returns:
            List of orders that violate their constraints
        """
        violations = []

        if not self._orderbook_manager:
            return violations

        with self._lock:
            for order in self._active_orders.values():
                if not order.is_active or not order.constraints:
                    continue

                # Get current book
                bid_level = self._orderbook_manager.get_best_bid(order.ticker)
                ask_level = self._orderbook_manager.get_best_ask(order.ticker)
                best_bid = bid_level.price if bid_level else None
                best_ask = ask_level.price if ask_level else None

                violation = order.constraints.check_market_still_valid(
                    order.side,
                    int(order.price),
                    best_bid,
                    best_ask,
                )

                if violation:
                    order.constraint_violation = violation
                    violations.append(order)
                    logger.warning(
                        "Constraint violation for order %s: %s "
                        "(side=%s, price=%s, bid=%s, ask=%s)",
                        order.order_id,
                        violation.value,
                        order.side,
                        order.price,
                        best_bid,
                        best_ask,
                    )

        return violations

    def cancel_constraint_violations(self) -> List[TrackedOrder]:
        """Check constraints and cancel any violating orders.

        Returns:
            List of orders that were canceled
        """
        violations = self.check_active_order_constraints()
        canceled = []

        for order in violations:
            if order.constraints and order.constraints.cancel_on_violation:
                success = self.cancel_order(order.order_id, order.exchange)
                if success:
                    canceled.append(order)
                    logger.info(
                        "Canceled order %s due to constraint violation: %s",
                        order.order_id,
                        order.constraint_violation.value
                        if order.constraint_violation
                        else "unknown",
                    )

        return canceled

    def cancel_order(self, order_id: str, exchange: str) -> bool:
        """Cancel an order.

        Args:
            order_id: Exchange-assigned order ID
            exchange: Exchange name

        Returns:
            True if cancellation successful
        """
        if exchange not in self._clients:
            raise ValueError(f"Exchange not registered: {exchange}")

        client = self._clients[exchange]

        try:
            success = client._cancel_order(order_id)
        except Exception as e:
            logger.error(
                "Cancel failed: order=%s exchange=%s error=%s", order_id, exchange, e
            )
            return False

        if success:
            with self._lock:
                order = self._active_orders.get(order_id)
                if order:
                    order.status = OrderStatus.CANCELED
                    order.last_update = datetime.now()

                    # Unregister from timeout tracking
                    self._timeout_tracker.untrack(order)

                    # Unregister from constraint monitoring
                    self._unregister_constraint_monitor(order)

                    # Release capital reservation
                    self._release_order_capital(order)

                    if self._on_cancel:
                        self._invoke_callback(
                            self._on_cancel,
                            order,
                            callback_name="cancel_callback",
                        )

            logger.info("Order canceled: order=%s exchange=%s", order_id, exchange)

        return success

    def modify_order(
        self,
        order_id: str,
        exchange: str,
        new_price: Optional[float] = None,
        new_size: Optional[int] = None,
    ) -> Optional[TrackedOrder]:
        """Modify an existing order's price and/or size.

        Uses cancel-replace pattern: cancels the existing order and submits
        a new one with the modified parameters. The new order gets a new
        order_id but keeps the same idempotency key with a version suffix.

        Args:
            order_id: Exchange-assigned order ID of order to modify
            exchange: Exchange name
            new_price: New price (uses original if not specified)
            new_size: New size (uses remaining size if not specified)

        Returns:
            New TrackedOrder if modification successful, None if failed

        Note:
            This is not atomic - there's a window between cancel and new order
            where the position is unprotected. For exchanges that support
            native order amendments, consider adding a direct amendment method.
        """
        if exchange not in self._clients:
            raise ValueError(f"Exchange not registered: {exchange}")

        if new_price is None and new_size is None:
            raise ValueError("Must specify at least one of new_price or new_size")

        # Get the existing order
        order = self.get_order(order_id)
        if not order:
            logger.warning("Cannot modify order: not found order_id=%s", order_id)
            return None

        if not order.is_active:
            logger.warning(
                "Cannot modify order: not active order_id=%s status=%s",
                order_id,
                order.status.value,
            )
            return None

        # Determine new parameters
        price = new_price if new_price is not None else order.price
        size = new_size if new_size is not None else order.remaining_size

        if size <= 0:
            logger.warning("Cannot modify order: invalid size=%d", size)
            return None

        # Cancel the existing order
        if not self.cancel_order(order_id, exchange):
            logger.warning(
                "Failed to cancel order for modification: order_id=%s", order_id
            )
            return None

        # Generate a new idempotency key based on the original
        # This allows tracking the modification chain
        base_key = order.idempotency_key.split(":v")[0]
        version = 1
        if ":v" in order.idempotency_key:
            try:
                version = int(order.idempotency_key.split(":v")[1]) + 1
            except ValueError:
                pass
        new_key = f"{base_key}:v{version}"

        # Submit the new order
        try:
            new_order = self.submit_order(
                exchange=exchange,
                ticker=order.ticker,
                side=order.side,
                price=price,
                size=size,
                idempotency_key=new_key,
                outcome=order.outcome,
                action=order.action,
                strategy_id=order.strategy_id,
                constraints=order.constraints,
                metadata={
                    **order.metadata,
                    "modified_from": order_id,
                    "modification_version": version,
                },
            )

            logger.info(
                "Order modified: original=%s new=%s price=%.2f->%.2f size=%d->%d",
                order_id,
                new_order.order_id,
                order.price,
                price,
                order.size,
                size,
            )

            return new_order

        except Exception as e:
            logger.error(
                "Failed to submit modified order: original=%s error=%s",
                order_id,
                e,
            )
            return None

    def get_order_status(self, order_id: str, exchange: str) -> Optional[OrderStatus]:
        """Get current status of an order.

        Args:
            order_id: Exchange-assigned order ID
            exchange: Exchange name

        Returns:
            OrderStatus or None if order not found
        """
        # Check local tracking first
        with self._lock:
            order = self._active_orders.get(order_id)
            if order:
                return order.status

        # Query exchange
        if exchange not in self._clients:
            return None

        client = self._clients[exchange]
        try:
            orders = client._get_orders(ticker="", status=None)
            for o in orders:
                if o.order_id == order_id:
                    return (
                        OrderStatus(o.status)
                        if hasattr(OrderStatus, o.status.upper())
                        else OrderStatus.OPEN
                    )
        except Exception as e:
            logger.error("Failed to get order status: %s", e)

        return None

    def get_order(self, order_id: str) -> Optional[TrackedOrder]:
        """Get a tracked order by ID.

        Args:
            order_id: Exchange-assigned order ID

        Returns:
            TrackedOrder or None if not found
        """
        with self._lock:
            return self._active_orders.get(order_id)

    def get_order_by_key(self, idempotency_key: str) -> Optional[TrackedOrder]:
        """Get a tracked order by idempotency key.

        Args:
            idempotency_key: Client-generated idempotency key

        Returns:
            TrackedOrder or None if not found
        """
        with self._lock:
            # Check pending first
            if idempotency_key in self._pending_orders:
                return self._pending_orders[idempotency_key]

            # Check active orders
            for order in self._active_orders.values():
                if order.idempotency_key == idempotency_key:
                    return order

        return None

    def get_active_orders(self, exchange: Optional[str] = None) -> List[TrackedOrder]:
        """Get all active orders.

        Args:
            exchange: Optional filter by exchange

        Returns:
            List of active TrackedOrder objects
        """
        with self._lock:
            orders = []
            for order in self._active_orders.values():
                if order.is_active:
                    if exchange is None or order.exchange == exchange:
                        orders.append(order)
            return orders

    def get_failed_orders(self, limit: int = 100) -> List[FailedOrder]:
        """Get recent failed orders.

        Args:
            limit: Maximum number to return

        Returns:
            List of FailedOrder objects (newest first)
        """
        with self._lock:
            return list(reversed(self._failed_orders[-limit:]))

    # =========================================================================
    # Strategy-Level Queries
    # =========================================================================

    def get_strategy_orders(
        self,
        strategy_id: str,
        exchange: Optional[str] = None,
        include_terminal: bool = True,
    ) -> List[TrackedOrder]:
        """Get all orders placed by a specific strategy.

        Args:
            strategy_id: Strategy identifier to filter by
            exchange: Optional filter by exchange
            include_terminal: If True, include completed/canceled orders

        Returns:
            List of TrackedOrder objects for the strategy
        """
        with self._lock:
            orders = []
            for order in self._active_orders.values():
                if order.strategy_id != strategy_id:
                    continue
                if exchange is not None and order.exchange != exchange:
                    continue
                if not include_terminal and order.is_terminal:
                    continue
                orders.append(order)
            return orders

    def get_strategy_active_orders(
        self,
        strategy_id: str,
        exchange: Optional[str] = None,
    ) -> List[TrackedOrder]:
        """Get active (non-terminal) orders for a strategy.

        Args:
            strategy_id: Strategy identifier to filter by
            exchange: Optional filter by exchange

        Returns:
            List of active TrackedOrder objects for the strategy
        """
        with self._lock:
            orders = []
            for order in self._active_orders.values():
                if order.strategy_id != strategy_id:
                    continue
                if not order.is_active:
                    continue
                if exchange is not None and order.exchange != exchange:
                    continue
                orders.append(order)
            return orders

    def get_strategy_exposure(
        self,
        strategy_id: str,
        exchange: Optional[str] = None,
    ) -> Dict[str, Dict[str, float]]:
        """Get exposure by ticker for a strategy based on filled orders.

        Calculates net position and exposure from filled orders attributed
        to the strategy. Note: This is order-based, not the same as the
        position inventory which tracks actual exchange positions.

        Args:
            strategy_id: Strategy identifier
            exchange: Optional filter by exchange

        Returns:
            Dict of {ticker: {"net_position": int, "exposure": float}}
        """
        exposure: Dict[str, Dict[str, float]] = {}

        with self._lock:
            for order in self._active_orders.values():
                if order.strategy_id != strategy_id:
                    continue
                if exchange is not None and order.exchange != exchange:
                    continue
                if order.filled_size == 0:
                    continue

                ticker = order.ticker
                if ticker not in exposure:
                    exposure[ticker] = {"net_position": 0, "exposure": 0.0}

                # Calculate signed position change
                size_change = (
                    order.filled_size if order.side == "buy" else -order.filled_size
                )
                exposure[ticker]["net_position"] += size_change

                # Exposure is absolute value times price
                fill_price = order.avg_fill_price or order.price
                exposure[ticker]["exposure"] += abs(order.filled_size) * fill_price

        return exposure

    def get_strategy_failed_orders(
        self,
        strategy_id: str,
        limit: int = 50,
    ) -> List[FailedOrder]:
        """Get failed orders for a specific strategy.

        Args:
            strategy_id: Strategy identifier to filter by
            limit: Maximum number to return

        Returns:
            List of FailedOrder objects (newest first)
        """
        with self._lock:
            failed = [f for f in self._failed_orders if f.strategy_id == strategy_id]
            return list(reversed(failed[-limit:]))

    def cancel_strategy_orders(
        self,
        strategy_id: str,
        exchange: Optional[str] = None,
    ) -> List[str]:
        """Cancel all active orders for a strategy.

        Useful for strategy shutdown or emergency stop.

        Args:
            strategy_id: Strategy identifier
            exchange: Optional filter by exchange

        Returns:
            List of order IDs that were successfully canceled
        """
        active_orders = self.get_strategy_active_orders(strategy_id, exchange)
        canceled = []

        for order in active_orders:
            if order.order_id:
                if self.cancel_order(order.order_id, order.exchange):
                    canceled.append(order.order_id)

        if canceled:
            logger.info(
                "Canceled %d orders for strategy %s",
                len(canceled),
                strategy_id,
            )

        return canceled

    def check_fills(self, exchange: Optional[str] = None) -> List[TrackedOrder]:
        """Check for fills on active orders.

        Args:
            exchange: Optional filter by exchange

        Returns:
            List of orders with new fills
        """
        orders_with_fills = []

        exchanges = [exchange] if exchange else list(self._clients.keys())

        for ex_name in exchanges:
            if ex_name not in self._clients:
                continue

            client = self._clients[ex_name]

            with self._lock:
                exchange_orders = [
                    o
                    for o in self._active_orders.values()
                    if o.exchange == ex_name and o.is_active
                ]

            for order in exchange_orders:
                try:
                    # Get current status from exchange
                    ex_orders = client._get_orders(order.ticker)
                    for ex_order in ex_orders:
                        if ex_order.order_id == order.order_id:
                            new_filled = ex_order.filled_size - order.filled_size
                            if new_filled > 0:
                                # Record the fill
                                fill_price = (
                                    getattr(ex_order, "avg_fill_price", order.price)
                                    or order.price
                                )
                                order.record_fill(new_filled, fill_price)
                                orders_with_fills.append(order)

                                # Update position inventory
                                self._update_position_from_fill(
                                    order, new_filled, fill_price
                                )

                                # Callback (async to avoid blocking)
                                if self._on_fill:
                                    self._invoke_callback(
                                        self._on_fill,
                                        order,
                                        new_filled,
                                        fill_price,
                                        callback_name="fill_callback",
                                    )

                                # Portfolio optimizer integration: record fill
                                self._record_portfolio_fill(order, new_filled, fill_price)

                            # Update status
                            if ex_order.status == "filled":
                                order.status = OrderStatus.FILLED
                                self._timeout_tracker.untrack(order)
                                self._unregister_constraint_monitor(order)
                                self._release_order_capital(order)
                            elif ex_order.status == "canceled":
                                order.status = OrderStatus.CANCELED
                                self._timeout_tracker.untrack(order)
                                self._unregister_constraint_monitor(order)
                                self._release_order_capital(order)

                            break
                except Exception as e:
                    logger.error(
                        "Error checking fills for order %s: %s", order.order_id, e
                    )

        return orders_with_fills

    def get_positions(self) -> PositionInventory:
        """Get unified position inventory.

        Returns:
            PositionInventory with positions across all exchanges
        """
        return self._positions

    def get_position(self, exchange: str, ticker: str) -> Optional[Position]:
        """Get position for a specific exchange and ticker.

        Args:
            exchange: Exchange name
            ticker: Market identifier

        Returns:
            Position or None
        """
        return self._positions.get_position(exchange, ticker)

    def sync_positions(self, exchange: Optional[str] = None) -> None:
        """Sync positions from exchanges.

        Args:
            exchange: Optional specific exchange to sync
        """
        exchanges = [exchange] if exchange else list(self._clients.keys())

        for ex_name in exchanges:
            if ex_name not in self._clients:
                continue

            client = self._clients[ex_name]
            try:
                positions = client.get_all_positions()
                for ticker, position in positions.items():
                    self._positions.set_position(ex_name, ticker, position)

                self._positions.last_sync = datetime.now()
                logger.debug(
                    "Synced positions for %s: %d positions", ex_name, len(positions)
                )
            except Exception as e:
                logger.error("Failed to sync positions for %s: %s", ex_name, e)

    def reconcile(
        self, exchange: str, auto_correct: bool = False
    ) -> ReconciliationReport:
        """Reconcile local state with exchange.

        Args:
            exchange: Exchange to reconcile
            auto_correct: If True, update local state to match exchange

        Returns:
            ReconciliationReport with results
        """
        if exchange not in self._reconcilers:
            raise ValueError(f"No reconciler for exchange: {exchange}")

        reconciler = self._reconcilers[exchange]

        with self._lock:
            # Get orders for this exchange
            exchange_orders = {
                order.order_id: order
                for order in self._active_orders.values()
                if order.exchange == exchange and order.order_id
            }

            # Get positions for this exchange
            exchange_positions = self._positions.get_exchange_positions(exchange)

        report = reconciler.reconcile(exchange_orders, exchange_positions, auto_correct)

        if report.has_mismatches:
            logger.warning(
                "Reconciliation found %d mismatches for %s",
                len(report.mismatches),
                exchange,
            )

        return report

    def reconcile_all(
        self, auto_correct: bool = False
    ) -> Dict[str, ReconciliationReport]:
        """Reconcile all exchanges in parallel.

        Args:
            auto_correct: If True, update local state to match exchanges

        Returns:
            Dict mapping exchange name to ReconciliationReport
        """
        exchanges = list(self._clients.keys())
        if not exchanges:
            return {}

        reports = {}

        def reconcile_one(exchange: str) -> tuple[str, ReconciliationReport]:
            try:
                return exchange, self.reconcile(exchange, auto_correct)
            except Exception as e:
                logger.error("Reconciliation failed for %s: %s", exchange, e)
                return exchange, ReconciliationReport(
                    exchange=exchange,
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                    success=False,
                    error=str(e),
                )

        # Use thread pool for parallel reconciliation
        # Limit workers to number of exchanges or 4, whichever is smaller
        max_workers = min(len(exchanges), 4)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(reconcile_one, ex): ex for ex in exchanges}
            for future in as_completed(futures):
                exchange, report = future.result()
                reports[exchange] = report

        return reports

    def set_on_fill(self, callback: Callable[[TrackedOrder, int, float], None]) -> None:
        """Set callback for fill events.

        Args:
            callback: Function(order, fill_size, fill_price) -> None
        """
        self._on_fill = callback

    def set_on_cancel(self, callback: Callable[[TrackedOrder], None]) -> None:
        """Set callback for cancel events.

        Args:
            callback: Function(order) -> None
        """
        self._on_cancel = callback

    def set_on_reject(self, callback: Callable[[FailedOrder], None]) -> None:
        """Set callback for rejection events.

        Args:
            callback: Function(failed_order) -> None
        """
        self._on_reject = callback

    def _handle_ws_fill(self, fill: FillEvent) -> None:
        """Handle fill notification from WebSocket.

        Updates order state in real-time when fills arrive via WebSocket,
        without waiting for polling.

        Args:
            fill: The fill event from WebSocket
        """
        with self._lock:
            order = self._active_orders.get(fill.order_id)
            if not order:
                # Order not tracked - may be from another source
                logger.debug(
                    "WS fill for unknown order: order_id=%s ticker=%s",
                    fill.order_id,
                    fill.ticker,
                )
                return

            # Record the fill (keep price in cents, consistent with rest of system)
            fill_price = fill.price
            prev_filled = order.filled_size
            order.record_fill(fill.size, fill_price)

            # Update position inventory (same as check_fills polling path)
            self._update_position_from_fill(order, fill.size, fill_price)

            # Handle terminal status
            if order.filled_size >= order.size:
                self._timeout_tracker.untrack(order)
                self._unregister_constraint_monitor(order)
                self._release_order_capital(order)

            logger.debug(
                "WS fill recorded: order_id=%s size=%d price=%d total_filled=%d",
                fill.order_id,
                fill.size,
                fill_price,
                order.filled_size,
            )

        # Fire callback outside lock (async to avoid blocking)
        if self._on_fill and order.filled_size > prev_filled:
            self._invoke_callback(
                self._on_fill,
                order,
                fill.size,
                fill_price,
                callback_name="ws_fill_callback",
            )

        # Portfolio optimizer integration: record fill to trade database
        if order.filled_size > prev_filled:
            self._record_portfolio_fill(order, fill.size, fill_price)

    def get_metrics(self) -> Dict:
        """Get detailed OMS metrics.

        Returns:
            Dictionary with comprehensive metrics including:
            - Order counts by status and exchange
            - Fill statistics (rates, average fill times)
            - Constraint violation counts
            - Position summaries
            - Capital state (if capital manager configured)
        """
        with self._lock:
            # Order counts by status
            status_counts = {status.value: 0 for status in OrderStatus}
            exchange_counts: Dict[str, Dict[str, int]] = {}
            total_filled_value = 0.0
            total_fill_count = 0
            constraint_violations = 0
            fill_times: List[float] = []

            for order in self._active_orders.values():
                status_counts[order.status.value] += 1

                # Count by exchange
                if order.exchange not in exchange_counts:
                    exchange_counts[order.exchange] = {
                        "total": 0,
                        "active": 0,
                        "filled": 0,
                        "canceled": 0,
                    }
                exchange_counts[order.exchange]["total"] += 1
                if order.is_active:
                    exchange_counts[order.exchange]["active"] += 1
                if order.status == OrderStatus.FILLED:
                    exchange_counts[order.exchange]["filled"] += 1
                if order.status == OrderStatus.CANCELED:
                    exchange_counts[order.exchange]["canceled"] += 1

                # Fill statistics
                if order.filled_size > 0 and order.avg_fill_price:
                    total_filled_value += order.filled_size * order.avg_fill_price
                    total_fill_count += order.filled_size

                # Track fill times for filled orders
                if order.status == OrderStatus.FILLED and order.submitted_at:
                    fill_time = (order.last_update - order.submitted_at).total_seconds()
                    fill_times.append(fill_time)

                # Constraint violations
                if order.constraint_violation:
                    constraint_violations += 1

            # Calculate averages
            avg_fill_time = sum(fill_times) / len(fill_times) if fill_times else None
            avg_fill_price = (
                total_filled_value / total_fill_count if total_fill_count > 0 else None
            )

            # Failure reason breakdown
            failure_reasons: Dict[str, int] = {}
            for failed in self._failed_orders:
                reason = failed.reason.value
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

            # Position summary
            position_summary = {}
            total_exposure = 0.0
            for exchange, positions in self._positions.positions.items():
                ex_exposure = sum(
                    abs(p.size) * p.current_price / 100.0 for p in positions.values()
                )
                position_summary[exchange] = {
                    "count": len(positions),
                    "exposure": ex_exposure,
                }
                total_exposure += ex_exposure

            metrics = {
                # Basic counts
                "pending_orders": len(self._pending_orders),
                "active_orders": status_counts.get("open", 0)
                + status_counts.get("partial", 0),
                "total_tracked_orders": len(self._active_orders),
                # Status breakdown
                "orders_by_status": status_counts,
                # Exchange breakdown
                "orders_by_exchange": exchange_counts,
                # Fill statistics
                "fill_stats": {
                    "total_filled_contracts": total_fill_count,
                    "total_filled_value": total_filled_value,
                    "avg_fill_price": avg_fill_price,
                    "avg_fill_time_seconds": avg_fill_time,
                    "fill_count": len(fill_times),
                },
                # Failures
                "failed_orders": len(self._failed_orders),
                "failure_reasons": failure_reasons,
                # Constraints
                "constraint_violations": constraint_violations,
                "constrained_tickers": len(self._constrained_orders),
                # Infrastructure
                "exchanges_registered": len(self._clients),
                "timeout_registered": self._timeout_manager.get_registered_count(),
                # Positions
                "positions": position_summary,
                "total_exposure": total_exposure,
            }

            # Add capital metrics if capital manager available
            if self._capital_manager:
                metrics["capital"] = self._capital_manager.get_summary()

            return metrics

    def cleanup_old_orders(self) -> int:
        """Remove old completed orders from memory.

        Cleans up orders that have been in a terminal state (FILLED, CANCELED,
        REJECTED, EXPIRED, FAILED) for longer than order_retention_seconds.

        Returns:
            Number of orders cleaned up
        """
        retention = timedelta(seconds=self._config.order_retention_seconds)
        cutoff = datetime.now() - retention
        cleaned = 0

        with self._lock:
            # Find orders to remove
            pending_to_remove = []
            active_to_remove = []

            for key, order in self._pending_orders.items():
                if order.is_terminal and order.last_update < cutoff:
                    pending_to_remove.append(key)

            for order_id, order in self._active_orders.items():
                if order.is_terminal and order.last_update < cutoff:
                    active_to_remove.append(order_id)

            # Track idempotency keys of orders removed from active, to dedup
            # against pending (which is keyed by idempotency_key, not order_id)
            removed_idem_keys = set()

            # Remove from active orders first
            for order_id in active_to_remove:
                order = self._active_orders.pop(order_id)
                removed_idem_keys.add(order.idempotency_key)
                cleaned += 1

            # Remove from pending orders, skipping those already counted via active
            for key in pending_to_remove:
                del self._pending_orders[key]
                if key not in removed_idem_keys:
                    cleaned += 1

            # Trim failed orders list (keep most recent 1000)
            if len(self._failed_orders) > 1000:
                removed = len(self._failed_orders) - 1000
                self._failed_orders = self._failed_orders[-1000:]
                cleaned += removed

        if cleaned > 0:
            logger.debug("Cleaned up %d old orders", cleaned)

        return cleaned

    def _submit_with_retry(self, order: TrackedOrder) -> TrackedOrder:
        """Submit order with retry logic.

        Args:
            order: Order to submit

        Returns:
            Updated order with order_id

        Raises:
            RuntimeError: If all retries exhausted
        """
        client = self._clients[order.exchange]
        last_error = None

        for attempt in range(self._config.max_retries + 1):
            try:
                order.status = OrderStatus.SUBMITTED
                order.submitted_at = datetime.now()

                # Submit to exchange with outcome for prediction markets
                outcome_str = order.outcome.value if order.outcome else None
                ex_order = client._place_order(
                    ticker=order.ticker,
                    side=order.side,
                    price=order.price,
                    size=order.size,
                    outcome=outcome_str,
                )

                # Update tracking
                order.order_id = ex_order.order_id
                order.status = OrderStatus.OPEN
                order.last_update = datetime.now()

                with self._lock:
                    self._active_orders[order.order_id] = order
                    # Keep in pending for idempotency lookups
                    self._pending_orders[order.idempotency_key] = order

                logger.info(
                    "Order submitted: key=%s order_id=%s exchange=%s ticker=%s %s@%.2f x%d",
                    order.idempotency_key,
                    order.order_id,
                    order.exchange,
                    order.ticker,
                    order.side,
                    order.price,
                    order.size,
                )
                return order

            except Exception as e:
                last_error = e
                if attempt < self._config.max_retries:
                    # Check for rate limiting
                    is_rate_limited, retry_after = self._parse_rate_limit(e)

                    if retry_after:
                        # Use retry-after from response
                        delay = retry_after
                    else:
                        # Exponential backoff with jitter
                        base_delay = self._config.retry_delay_seconds * (2**attempt)
                        # Add 10-50% jitter to prevent thundering herd
                        jitter = base_delay * (0.1 + 0.4 * random.random())
                        delay = base_delay + jitter

                        # Longer delay for rate limits without retry-after
                        if is_rate_limited:
                            delay = max(
                                delay, 5.0
                            )  # At least 5 seconds for rate limits

                    logger.warning(
                        "Order submission failed (attempt %d/%d): %s. "
                        "Retrying in %.1fs%s",
                        attempt + 1,
                        self._config.max_retries + 1,
                        e,
                        delay,
                        " (rate limited)" if is_rate_limited else "",
                    )
                    time.sleep(delay)

        # All retries exhausted
        order.status = OrderStatus.FAILED
        raise RuntimeError(
            f"Order submission failed after {self._config.max_retries + 1} attempts: {last_error}"
        )

    def _parse_rate_limit(self, error: Exception) -> tuple[bool, Optional[float]]:
        """Parse rate limit information from an exception.

        Args:
            error: The exception to check

        Returns:
            Tuple of (is_rate_limited, retry_after_seconds)
        """
        error_str = str(error).lower()

        # Check for common rate limit indicators
        is_rate_limited = any(
            indicator in error_str
            for indicator in [
                "rate limit",
                "rate_limit",
                "ratelimit",
                "too many requests",
                "429",
                "throttl",
            ]
        )

        # Try to extract retry-after value
        retry_after = None

        # Check for retry_after attribute (some HTTP libraries)
        if hasattr(error, "retry_after"):
            try:
                retry_after = float(error.retry_after)
            except (TypeError, ValueError):
                pass

        # Check for response with headers (requests library)
        if hasattr(error, "response") and error.response is not None:
            response = error.response
            if hasattr(response, "headers"):
                retry_header = response.headers.get("Retry-After")
                if retry_header:
                    try:
                        retry_after = float(retry_header)
                    except ValueError:
                        pass

            # Check status code
            if hasattr(response, "status_code") and response.status_code == 429:
                is_rate_limited = True

        return is_rate_limited, retry_after

    def _capture_failure(
        self, order: TrackedOrder, reason: FailureReason, message: str
    ) -> None:
        """Capture a failed order.

        Args:
            order: The order that failed
            reason: Failure reason category
            message: Error message
        """
        failed = FailedOrder(
            idempotency_key=order.idempotency_key,
            exchange=order.exchange,
            ticker=order.ticker,
            side=order.side,
            price=order.price,
            size=order.size,
            reason=reason,
            error_message=message,
            outcome=order.outcome,
            action=order.action,
            strategy_id=order.strategy_id,
            retry_count=self._config.max_retries,
            original_order=order,
        )

        with self._lock:
            self._failed_orders.append(failed)
            # Remove from pending
            self._pending_orders.pop(order.idempotency_key, None)

        logger.error(
            "Order failed: key=%s reason=%s message=%s",
            order.idempotency_key,
            reason.value,
            message,
        )

        if self._on_reject:
            self._invoke_callback(
                self._on_reject,
                failed,
                callback_name="reject_callback",
            )

    def _handle_order_timeout(self, order: TrackedOrder) -> None:
        """Handle order timeout.

        Args:
            order: The order that timed out
        """
        logger.warning("Order timed out: order_id=%s", order.order_id)

        # Try to cancel
        if order.order_id:
            self.cancel_order(order.order_id, order.exchange)

    def _handle_order_mismatch(self, mismatch) -> None:
        """Handle order mismatch from reconciliation."""
        logger.warning("Order mismatch detected: %s", mismatch.description)

    def _handle_position_mismatch(self, ticker: str, local_pos, exchange_pos) -> None:
        """Handle position mismatch from reconciliation."""
        logger.warning(
            "Position mismatch: ticker=%s local=%s exchange=%s",
            ticker,
            local_pos.size if local_pos else None,
            exchange_pos.size if exchange_pos else None,
        )

    def _update_position_from_fill(
        self, order: TrackedOrder, fill_size: int, fill_price: float
    ) -> None:
        """Update position inventory from a fill.

        Handles all cases correctly:
        - New position creation
        - Increasing existing position (weighted average entry)
        - Reducing position (keep original entry)
        - Position flip (long->short or short->long): new entry at fill price

        For prediction markets with outcome specified, positions are tracked
        separately by outcome (YES/NO).

        Args:
            order: The order that was filled
            fill_size: Size of the fill
            fill_price: Price of the fill
        """
        # Use outcome-aware position tracking if order has outcome set
        if order.outcome is not None:
            current = self._positions.get_position_by_outcome(
                order.exchange, order.ticker, order.outcome
            )
        else:
            current = self._positions.get_position(order.exchange, order.ticker)

        if current is None:
            # Create new position
            size = fill_size if order.side == "buy" else -fill_size
            new_pos = Position(
                ticker=order.ticker,
                size=size,
                entry_price=fill_price,
                current_price=fill_price,
                opened_at=datetime.now(),
            )
            if order.outcome is not None:
                self._positions.set_position_by_outcome(
                    order.exchange, order.ticker, new_pos, order.outcome
                )
            else:
                self._positions.set_position(order.exchange, order.ticker, new_pos)
        else:
            # Calculate signed size change: positive for buys, negative for sells
            delta = fill_size if order.side == "buy" else -fill_size
            new_size = current.size + delta

            if new_size == 0:
                # Position closed completely
                new_entry = 0.0
                opened_at = current.opened_at
            elif current.size == 0:
                # Was flat, now have a position
                new_entry = fill_price
                opened_at = datetime.now()
            elif (current.size > 0 and new_size > 0) or (
                current.size < 0 and new_size < 0
            ):
                # Same direction - either increasing or reducing but not flipping
                if abs(new_size) > abs(current.size):
                    # Increasing position size - weighted average entry price
                    total_value = (
                        abs(current.size) * current.entry_price + fill_size * fill_price
                    )
                    new_entry = total_value / abs(new_size)
                else:
                    # Reducing position size - keep original entry price
                    new_entry = current.entry_price
                opened_at = current.opened_at
            else:
                # Position flip: went from long to short or short to long
                # The portion that closed the old position is realized P&L (not tracked here)
                # The new position starts fresh at the fill price
                new_entry = fill_price
                opened_at = datetime.now()

            updated = Position(
                ticker=order.ticker,
                size=new_size,
                entry_price=new_entry,
                current_price=fill_price,
                opened_at=opened_at,
            )
            if order.outcome is not None:
                self._positions.set_position_by_outcome(
                    order.exchange, order.ticker, updated, order.outcome
                )
            else:
                self._positions.set_position(order.exchange, order.ticker, updated)

        # Publish to dashboard
        self._publish_position_to_dashboard(order.exchange, order.ticker)

    def _publish_position_to_dashboard(self, exchange: str, ticker: str) -> None:
        """Publish position update to dashboard (no-op, dashboard removed)."""
        pass

    def _publish_metrics_to_dashboard(self) -> None:
        """Publish OMS metrics to dashboard (no-op, dashboard removed)."""
        pass

    def _reconciliation_loop(self) -> None:
        """Background reconciliation and cleanup loop."""
        while not self._stop_event.is_set():
            try:
                self.reconcile_all(auto_correct=True)
            except Exception as e:
                logger.error("Reconciliation loop error: %s", e)

            # Cleanup old orders if enabled
            if self._config.enable_order_cleanup:
                try:
                    self.cleanup_old_orders()
                except Exception as e:
                    logger.error("Order cleanup error: %s", e)

            # Publish metrics to dashboard
            try:
                self._publish_metrics_to_dashboard()
            except Exception as e:
                logger.debug("Dashboard metrics publish error: %s", e)

            self._stop_event.wait(self._config.reconciliation_interval_seconds)
