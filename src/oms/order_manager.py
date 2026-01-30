"""Centralized Order Management System for multi-exchange trading.

Provides unified order tracking, submission, and lifecycle management
across multiple exchanges with idempotency, timeout handling, and reconciliation.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from src.core.exchange import ExchangeClient, Order
from src.core.models import Position

from .capital_manager import CapitalManager
from .fill_notifier import FillEvent, FillNotifier
from .models import (
    FailedOrder,
    FailureReason,
    OrderStatus,
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
    """
    default_timeout_seconds: float = 60.0
    reconciliation_interval_seconds: float = 60.0
    max_retries: int = 3
    retry_delay_seconds: float = 0.5
    enable_auto_reconciliation: bool = True
    enable_timeout_manager: bool = True


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
    ) -> None:
        """Initialize OrderManagementSystem.

        Args:
            config: Optional configuration (uses defaults if not provided)
            capital_manager: Optional capital manager for reservations
            fill_notifier: Optional fill notifier for real-time WebSocket fills
        """
        self._config = config or OMSConfig()
        self._capital_manager = capital_manager
        self._fill_notifier = fill_notifier

        # Exchange clients
        self._clients: Dict[str, ExchangeClient] = {}

        # Order tracking
        self._pending_orders: Dict[str, TrackedOrder] = {}    # by idempotency_key
        self._active_orders: Dict[str, TrackedOrder] = {}     # by order_id
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

        # Register for WebSocket fill notifications
        if self._fill_notifier:
            self._fill_notifier.add_listener(self._handle_ws_fill)

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
        logger.info("OrderManagementSystem stopped")

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
    ) -> TrackedOrder:
        """Submit a new order with idempotency.

        Args:
            exchange: Exchange to submit to
            ticker: Market identifier
            side: 'buy' or 'sell'
            price: Order price
            size: Number of contracts
            idempotency_key: Optional client-generated key (auto-generated if not provided)
            timeout_seconds: Optional timeout (uses default if not provided)
            metadata: Optional additional tracking data

        Returns:
            TrackedOrder with order details

        Raises:
            ValueError: If exchange not registered or invalid parameters
            RuntimeError: If order submission fails after retries
        """
        if exchange not in self._clients:
            raise ValueError(f"Exchange not registered: {exchange}")

        if side not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {side}")

        # Generate or validate idempotency key
        key = idempotency_key or generate_idempotency_key()

        # Check for duplicate submission
        with self._lock:
            if key in self._pending_orders:
                logger.warning("Duplicate order submission blocked: key=%s", key)
                return self._pending_orders[key]

        # Create tracked order
        order = TrackedOrder(
            idempotency_key=key,
            exchange=exchange,
            ticker=ticker,
            side=side,
            price=price,
            size=size,
            metadata=metadata or {},
        )

        # Register as pending
        with self._lock:
            self._pending_orders[key] = order

        # Submit to exchange with retries
        try:
            order = self._submit_with_retry(order)
        except Exception as e:
            # Capture failure
            self._capture_failure(order, FailureReason.EXCHANGE_ERROR, str(e))
            raise

        # Register for timeout tracking
        if self._config.enable_timeout_manager and order.order_id:
            timeout = timeout_seconds or self._config.default_timeout_seconds
            self._timeout_tracker.track(order, timeout)

        return order

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
            logger.error("Cancel failed: order=%s exchange=%s error=%s", order_id, exchange, e)
            return False

        if success:
            with self._lock:
                order = self._active_orders.get(order_id)
                if order:
                    order.status = OrderStatus.CANCELED
                    order.last_update = datetime.now()

                    # Unregister from timeout tracking
                    self._timeout_tracker.untrack(order)

                    if self._on_cancel:
                        try:
                            self._on_cancel(order)
                        except Exception as e:
                            logger.error("Error in cancel callback: %s", e)

            logger.info("Order canceled: order=%s exchange=%s", order_id, exchange)

        return success

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
                    return OrderStatus(o.status) if hasattr(OrderStatus, o.status.upper()) else OrderStatus.OPEN
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
                    o for o in self._active_orders.values()
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
                                fill_price = getattr(ex_order, 'avg_fill_price', order.price) or order.price
                                order.record_fill(new_filled, fill_price)
                                orders_with_fills.append(order)

                                # Update position inventory
                                self._update_position_from_fill(order, new_filled, fill_price)

                                # Callback
                                if self._on_fill:
                                    try:
                                        self._on_fill(order, new_filled, fill_price)
                                    except Exception as e:
                                        logger.error("Error in fill callback: %s", e)

                            # Update status
                            if ex_order.status == "filled":
                                order.status = OrderStatus.FILLED
                                self._timeout_tracker.untrack(order)
                            elif ex_order.status == "canceled":
                                order.status = OrderStatus.CANCELED
                                self._timeout_tracker.untrack(order)

                            break
                except Exception as e:
                    logger.error("Error checking fills for order %s: %s", order.order_id, e)

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
                logger.debug("Synced positions for %s: %d positions", ex_name, len(positions))
            except Exception as e:
                logger.error("Failed to sync positions for %s: %s", ex_name, e)

    def reconcile(self, exchange: str, auto_correct: bool = False) -> ReconciliationReport:
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

    def reconcile_all(self, auto_correct: bool = False) -> Dict[str, ReconciliationReport]:
        """Reconcile all exchanges.

        Args:
            auto_correct: If True, update local state to match exchanges

        Returns:
            Dict mapping exchange name to ReconciliationReport
        """
        reports = {}
        for exchange in self._clients.keys():
            try:
                reports[exchange] = self.reconcile(exchange, auto_correct)
            except Exception as e:
                logger.error("Reconciliation failed for %s: %s", exchange, e)
                report = ReconciliationReport(
                    exchange=exchange,
                    started_at=datetime.now(),
                    completed_at=datetime.now(),
                    success=False,
                    error=str(e),
                )
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

            # Record the fill
            fill_price = fill.price / 100.0  # Convert cents to dollars
            prev_filled = order.filled_size
            order.record_fill(fill.size, fill_price)

            logger.debug(
                "WS fill recorded: order_id=%s size=%d price=%.4f total_filled=%d",
                fill.order_id,
                fill.size,
                fill_price,
                order.filled_size,
            )

        # Fire callback outside lock
        if self._on_fill and order.filled_size > prev_filled:
            try:
                self._on_fill(order, fill.size, fill_price)
            except Exception as e:
                logger.error("Fill callback error: %s", e)

    def get_metrics(self) -> Dict:
        """Get OMS metrics.

        Returns:
            Dictionary with current metrics
        """
        with self._lock:
            active_count = sum(1 for o in self._active_orders.values() if o.is_active)
            filled_count = sum(1 for o in self._active_orders.values() if o.status == OrderStatus.FILLED)

            return {
                "pending_orders": len(self._pending_orders),
                "active_orders": active_count,
                "filled_orders": filled_count,
                "failed_orders": len(self._failed_orders),
                "exchanges_registered": len(self._clients),
                "timeout_registered": self._timeout_manager.get_registered_count(),
                "positions": {
                    ex: len(pos)
                    for ex, pos in self._positions.positions.items()
                },
            }

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

                # Submit to exchange
                ex_order = client._place_order(
                    ticker=order.ticker,
                    side=order.side,
                    price=order.price,
                    size=order.size,
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
                    delay = self._config.retry_delay_seconds * (2 ** attempt)
                    logger.warning(
                        "Order submission failed (attempt %d/%d): %s. Retrying in %.1fs",
                        attempt + 1,
                        self._config.max_retries + 1,
                        e,
                        delay,
                    )
                    time.sleep(delay)

        # All retries exhausted
        order.status = OrderStatus.FAILED
        raise RuntimeError(f"Order submission failed after {self._config.max_retries + 1} attempts: {last_error}")

    def _capture_failure(self, order: TrackedOrder, reason: FailureReason, message: str) -> None:
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
            try:
                self._on_reject(failed)
            except Exception as e:
                logger.error("Error in reject callback: %s", e)

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

    def _update_position_from_fill(self, order: TrackedOrder, fill_size: int, fill_price: float) -> None:
        """Update position inventory from a fill.

        Args:
            order: The order that was filled
            fill_size: Size of the fill
            fill_price: Price of the fill
        """
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
            self._positions.set_position(order.exchange, order.ticker, new_pos)
        else:
            # Update existing position
            if order.side == "buy":
                # Buying increases position
                new_size = current.size + fill_size
                if new_size != 0:
                    # Weighted average entry price
                    if current.size >= 0:
                        total_value = current.size * current.entry_price + fill_size * fill_price
                        new_entry = total_value / new_size if new_size != 0 else fill_price
                    else:
                        # Reducing short position
                        new_entry = current.entry_price
                else:
                    new_entry = 0.0
            else:
                # Selling decreases position
                new_size = current.size - fill_size
                if new_size != 0:
                    if current.size <= 0:
                        total_value = abs(current.size) * current.entry_price + fill_size * fill_price
                        new_entry = total_value / abs(new_size) if new_size != 0 else fill_price
                    else:
                        new_entry = current.entry_price
                else:
                    new_entry = 0.0

            updated = Position(
                ticker=order.ticker,
                size=new_size,
                entry_price=new_entry,
                current_price=fill_price,
                opened_at=current.opened_at,
            )
            self._positions.set_position(order.exchange, order.ticker, updated)

    def _reconciliation_loop(self) -> None:
        """Background reconciliation loop."""
        while not self._stop_event.is_set():
            try:
                self.reconcile_all(auto_correct=True)
            except Exception as e:
                logger.error("Reconciliation loop error: %s", e)

            self._stop_event.wait(self._config.reconciliation_interval_seconds)
