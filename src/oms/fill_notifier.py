"""Fill notification hub for event-driven fill detection.

Central hub that bridges WebSocket fill events to synchronous executors.
Replaces polling-based fill detection with <50ms event-driven updates.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FillEvent:
    """A fill event from any exchange.

    Normalized representation of a fill that can come from
    Kalshi, Polymarket, or other exchanges.

    Attributes:
        order_id: The order that was filled
        ticker: Market ticker
        side: Order side (buy/sell or yes/no depending on exchange)
        price: Fill price in cents
        size: Number of contracts filled
        timestamp: When the fill occurred
        exchange: Exchange name (kalshi, polymarket, etc.)
        trade_id: Exchange-specific trade identifier
    """

    order_id: str
    ticker: str
    side: str
    price: int  # cents
    size: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    exchange: str = ""
    trade_id: str = ""


# Callback type for fill listeners
FillListener = Callable[[FillEvent], None]


@dataclass
class _PendingOrder:
    """Internal tracking for a pending order."""

    order_id: str
    event: threading.Event
    fills: List[FillEvent] = field(default_factory=list)
    registered_at: datetime = field(default_factory=datetime.utcnow)


class FillNotifier:
    """Central hub for fill notifications.

    Bridges WebSocket fill events to synchronous executors. Provides:
    - Order registration for fill waiting
    - Event-driven notification via threading.Event
    - Global listener support for OMS integration
    - Accumulated fills per order for partial fill handling

    Example:
        >>> notifier = FillNotifier()
        >>>
        >>> # Register listener for OMS updates
        >>> notifier.add_listener(oms_handler)
        >>>
        >>> # Wait for specific order fill
        >>> event = notifier.register_order("order_123")
        >>> try:
        ...     if event.wait(timeout=5.0):
        ...         fills = notifier.get_fills("order_123")
        ...         total = sum(f.size for f in fills)
        ... finally:
        ...     notifier.unregister_order("order_123")
    """

    def __init__(self) -> None:
        """Initialize FillNotifier."""
        self._lock = threading.Lock()
        self._pending: Dict[str, _PendingOrder] = {}
        self._listeners: List[FillListener] = []

    def register_order(self, order_id: str) -> threading.Event:
        """Register an order for fill notification.

        Must be called before placing the order to ensure no fills
        are missed.

        Args:
            order_id: Order ID to wait for

        Returns:
            Event that will be set when fill arrives
        """
        with self._lock:
            if order_id in self._pending:
                return self._pending[order_id].event

            pending = _PendingOrder(
                order_id=order_id,
                event=threading.Event(),
            )
            self._pending[order_id] = pending
            return pending.event

    def unregister_order(self, order_id: str) -> None:
        """Unregister an order from fill tracking.

        Should be called in finally block after order completion
        or timeout.

        Args:
            order_id: Order ID to unregister
        """
        with self._lock:
            self._pending.pop(order_id, None)

    def wait_for_fill(
        self,
        order_id: str,
        timeout: float = 5.0,
    ) -> List[FillEvent]:
        """Wait for fills on an order.

        Convenience method that combines event.wait() and get_fills().

        Args:
            order_id: Order ID to wait for
            timeout: Maximum seconds to wait

        Returns:
            List of fills (may be empty on timeout)
        """
        with self._lock:
            pending = self._pending.get(order_id)
            if not pending:
                return []
            event = pending.event

        event.wait(timeout=timeout)

        with self._lock:
            pending = self._pending.get(order_id)
            if pending:
                return list(pending.fills)
            return []

    def get_fills(self, order_id: str) -> List[FillEvent]:
        """Get accumulated fills for an order.

        Args:
            order_id: Order ID to check

        Returns:
            List of fills (may be empty)
        """
        with self._lock:
            pending = self._pending.get(order_id)
            if pending:
                return list(pending.fills)
            return []

    def get_total_filled(self, order_id: str) -> int:
        """Get total filled size for an order.

        Args:
            order_id: Order ID to check

        Returns:
            Total filled size across all fills
        """
        with self._lock:
            pending = self._pending.get(order_id)
            if pending:
                return sum(f.size for f in pending.fills)
            return 0

    def notify_fill(self, fill: FillEvent) -> None:
        """Notify of a fill event.

        Called by WebSocket adapters when a fill is received.
        Updates pending orders and dispatches to listeners.

        Args:
            fill: The fill event
        """
        logger.debug(
            "Fill notification: order=%s ticker=%s price=%dc size=%d",
            fill.order_id,
            fill.ticker,
            fill.price,
            fill.size,
        )

        with self._lock:
            # Update pending order
            pending = self._pending.get(fill.order_id)
            if pending:
                pending.fills.append(fill)
                pending.event.set()

            listeners = list(self._listeners)

        # Dispatch to listeners (outside lock)
        for listener in listeners:
            try:
                listener(fill)
            except Exception as e:
                logger.error("Fill listener error: %s", e)

    def add_listener(self, callback: FillListener) -> None:
        """Add a global fill listener.

        Listeners receive all fills, useful for OMS state updates.

        Args:
            callback: Function(FillEvent) -> None
        """
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: FillListener) -> None:
        """Remove a fill listener.

        Args:
            callback: The callback to remove
        """
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def clear(self) -> None:
        """Clear all pending orders and listeners.

        Primarily for testing or shutdown.
        """
        with self._lock:
            # Set all events to unblock waiters
            for pending in self._pending.values():
                pending.event.set()
            self._pending.clear()
            self._listeners.clear()

    def get_pending_count(self) -> int:
        """Get number of orders awaiting fills.

        Returns:
            Number of registered pending orders
        """
        with self._lock:
            return len(self._pending)
