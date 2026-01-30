"""Automatic order timeout handling.

Monitors orders and triggers cancellation when they exceed configured timeouts.
Supports per-order timeout configuration and callback notifications.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Dict, Optional

from .models import TrackedOrder, OrderStatus


logger = logging.getLogger(__name__)


@dataclass
class TimeoutConfig:
    """Configuration for timeout behavior.

    Attributes:
        default_timeout_seconds: Default timeout for orders without explicit timeout
        check_interval_seconds: How often to check for timed out orders
        max_timeout_seconds: Maximum allowed timeout (safety cap)
    """
    default_timeout_seconds: float = 60.0
    check_interval_seconds: float = 1.0
    max_timeout_seconds: float = 3600.0  # 1 hour cap


@dataclass
class TimeoutEntry:
    """An order registered for timeout monitoring.

    Attributes:
        order_id: Order ID being monitored
        idempotency_key: Idempotency key of the order
        exchange: Exchange where order is placed
        timeout_at: When the order should be canceled
        callback: Function to call when timeout triggers
        extended_count: Number of times timeout was extended
    """
    order_id: str
    idempotency_key: str
    exchange: str
    timeout_at: datetime
    callback: Callable[[str, str], None]  # (order_id, exchange) -> None
    extended_count: int = 0


class TimeoutManager:
    """Manages automatic order timeouts.

    Runs a background thread that periodically checks for orders
    that have exceeded their timeout and triggers cancellation callbacks.

    Example:
        >>> manager = TimeoutManager()
        >>> manager.start()
        >>> manager.register_order(
        ...     order_id="ord_123",
        ...     idempotency_key="OMS-ABC123",
        ...     exchange="kalshi",
        ...     timeout_seconds=30,
        ...     on_timeout=lambda oid, ex: cancel_order(oid, ex)
        ... )
        >>> # Order will be auto-canceled after 30 seconds
        >>> manager.stop()
    """

    def __init__(self, config: Optional[TimeoutConfig] = None) -> None:
        """Initialize TimeoutManager.

        Args:
            config: Optional timeout configuration (uses defaults if not provided)
        """
        self._config = config or TimeoutConfig()
        self._entries: Dict[str, TimeoutEntry] = {}  # keyed by order_id
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "TimeoutManager":
        """Start the timeout monitoring thread.

        Returns:
            Self for chaining
        """
        if self._thread is not None and self._thread.is_alive():
            return self

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TimeoutManager")
        self._thread.start()
        logger.info("TimeoutManager started (check interval: %.1fs)", self._config.check_interval_seconds)
        return self

    def stop(self) -> None:
        """Stop the timeout monitoring thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("TimeoutManager stopped")

    def register_order(
        self,
        order_id: str,
        idempotency_key: str,
        exchange: str,
        timeout_seconds: Optional[float] = None,
        on_timeout: Optional[Callable[[str, str], None]] = None,
    ) -> datetime:
        """Register an order for timeout monitoring.

        Args:
            order_id: Exchange-assigned order ID
            idempotency_key: Client-generated idempotency key
            exchange: Exchange name
            timeout_seconds: Seconds until timeout (uses default if not provided)
            on_timeout: Callback when timeout triggers: (order_id, exchange) -> None

        Returns:
            The datetime when the order will timeout

        Raises:
            ValueError: If timeout exceeds maximum allowed
        """
        timeout = timeout_seconds if timeout_seconds is not None else self._config.default_timeout_seconds

        if timeout > self._config.max_timeout_seconds:
            raise ValueError(
                f"Timeout {timeout}s exceeds maximum allowed {self._config.max_timeout_seconds}s"
            )

        timeout_at = datetime.now() + timedelta(seconds=timeout)

        entry = TimeoutEntry(
            order_id=order_id,
            idempotency_key=idempotency_key,
            exchange=exchange,
            timeout_at=timeout_at,
            callback=on_timeout or self._default_timeout_callback,
        )

        with self._lock:
            self._entries[order_id] = entry

        logger.debug(
            "Registered timeout: order=%s exchange=%s timeout_at=%s",
            order_id,
            exchange,
            timeout_at.isoformat(),
        )
        return timeout_at

    def unregister_order(self, order_id: str) -> bool:
        """Remove an order from timeout monitoring.

        Call this when an order is filled, canceled, or no longer needs monitoring.

        Args:
            order_id: Order ID to unregister

        Returns:
            True if order was found and removed, False if not found
        """
        with self._lock:
            if order_id in self._entries:
                del self._entries[order_id]
                logger.debug("Unregistered timeout: order=%s", order_id)
                return True
        return False

    def extend_timeout(self, order_id: str, additional_seconds: float) -> Optional[datetime]:
        """Extend the timeout for an order.

        Args:
            order_id: Order ID to extend
            additional_seconds: Seconds to add to current timeout

        Returns:
            New timeout datetime, or None if order not found
        """
        with self._lock:
            entry = self._entries.get(order_id)
            if not entry:
                return None

            new_timeout = entry.timeout_at + timedelta(seconds=additional_seconds)

            # Check against max timeout from original registration
            max_allowed = datetime.now() + timedelta(seconds=self._config.max_timeout_seconds)
            if new_timeout > max_allowed:
                new_timeout = max_allowed
                logger.warning(
                    "Extended timeout capped at max: order=%s new_timeout=%s",
                    order_id,
                    new_timeout.isoformat(),
                )

            entry.timeout_at = new_timeout
            entry.extended_count += 1

            logger.debug(
                "Extended timeout: order=%s new_timeout=%s extensions=%d",
                order_id,
                new_timeout.isoformat(),
                entry.extended_count,
            )
            return new_timeout

    def get_timeout(self, order_id: str) -> Optional[datetime]:
        """Get the timeout datetime for an order.

        Args:
            order_id: Order ID to check

        Returns:
            Timeout datetime, or None if order not registered
        """
        with self._lock:
            entry = self._entries.get(order_id)
            return entry.timeout_at if entry else None

    def get_remaining_seconds(self, order_id: str) -> Optional[float]:
        """Get remaining seconds until timeout.

        Args:
            order_id: Order ID to check

        Returns:
            Seconds remaining (negative if already timed out), or None if not registered
        """
        timeout_at = self.get_timeout(order_id)
        if timeout_at is None:
            return None
        return (timeout_at - datetime.now()).total_seconds()

    def get_registered_count(self) -> int:
        """Get number of orders currently registered for timeout."""
        with self._lock:
            return len(self._entries)

    def check_timeouts(self) -> int:
        """Manually check for and process timed out orders.

        Returns:
            Number of orders that timed out
        """
        return self._process_timeouts()

    def _run_loop(self) -> None:
        """Main monitoring loop."""
        while not self._stop_event.is_set():
            try:
                self._process_timeouts()
            except Exception as e:
                logger.error("Error in timeout processing: %s", e)

            self._stop_event.wait(self._config.check_interval_seconds)

    def _process_timeouts(self) -> int:
        """Process all timed out orders.

        Returns:
            Number of orders that timed out
        """
        now = datetime.now()
        timed_out: list[TimeoutEntry] = []

        with self._lock:
            for order_id, entry in list(self._entries.items()):
                if entry.timeout_at <= now:
                    timed_out.append(entry)
                    del self._entries[order_id]

        # Execute callbacks outside the lock
        for entry in timed_out:
            logger.info(
                "Order timed out: order=%s exchange=%s",
                entry.order_id,
                entry.exchange,
            )
            try:
                entry.callback(entry.order_id, entry.exchange)
            except Exception as e:
                logger.error(
                    "Error in timeout callback for order %s: %s",
                    entry.order_id,
                    e,
                )

        return len(timed_out)

    def _default_timeout_callback(self, order_id: str, exchange: str) -> None:
        """Default callback when no custom callback is provided."""
        logger.warning(
            "Order timed out with no callback: order=%s exchange=%s",
            order_id,
            exchange,
        )


class OrderTimeoutTracker:
    """Convenience wrapper that tracks timeouts for TrackedOrder objects.

    Integrates with the OMS to automatically register/unregister orders.

    Example:
        >>> tracker = OrderTimeoutTracker(timeout_manager, on_timeout=cancel_order)
        >>> tracker.track(order, timeout_seconds=30)
        >>> # ... later when order is filled or canceled
        >>> tracker.untrack(order)
    """

    def __init__(
        self,
        timeout_manager: TimeoutManager,
        default_timeout_seconds: float = 60.0,
        on_timeout: Optional[Callable[[TrackedOrder], None]] = None,
    ) -> None:
        """Initialize OrderTimeoutTracker.

        Args:
            timeout_manager: The underlying TimeoutManager
            default_timeout_seconds: Default timeout for orders
            on_timeout: Callback when order times out: (TrackedOrder) -> None
        """
        self._manager = timeout_manager
        self._default_timeout = default_timeout_seconds
        self._on_timeout = on_timeout
        self._orders: Dict[str, TrackedOrder] = {}
        self._lock = threading.Lock()

    def track(self, order: TrackedOrder, timeout_seconds: Optional[float] = None) -> datetime:
        """Start tracking an order for timeout.

        Args:
            order: The order to track
            timeout_seconds: Optional custom timeout

        Returns:
            The datetime when the order will timeout
        """
        if not order.order_id:
            raise ValueError("Cannot track order without order_id")

        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout

        with self._lock:
            self._orders[order.order_id] = order

        timeout_at = self._manager.register_order(
            order_id=order.order_id,
            idempotency_key=order.idempotency_key,
            exchange=order.exchange,
            timeout_seconds=timeout,
            on_timeout=self._handle_timeout,
        )

        # Update the order's timeout_at field
        order.timeout_at = timeout_at
        return timeout_at

    def untrack(self, order: TrackedOrder) -> bool:
        """Stop tracking an order.

        Args:
            order: The order to stop tracking

        Returns:
            True if order was being tracked
        """
        if not order.order_id:
            return False

        with self._lock:
            self._orders.pop(order.order_id, None)

        return self._manager.unregister_order(order.order_id)

    def extend(self, order: TrackedOrder, additional_seconds: float) -> Optional[datetime]:
        """Extend timeout for an order.

        Args:
            order: The order to extend
            additional_seconds: Seconds to add

        Returns:
            New timeout datetime, or None if not tracked
        """
        if not order.order_id:
            return None

        new_timeout = self._manager.extend_timeout(order.order_id, additional_seconds)
        if new_timeout:
            order.timeout_at = new_timeout
        return new_timeout

    def _handle_timeout(self, order_id: str, exchange: str) -> None:
        """Internal handler when an order times out."""
        with self._lock:
            order = self._orders.pop(order_id, None)

        if order and self._on_timeout:
            # Update order status
            order.status = OrderStatus.EXPIRED
            order.last_update = datetime.now()
            self._on_timeout(order)
