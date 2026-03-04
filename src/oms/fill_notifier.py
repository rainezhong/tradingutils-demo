"""Fill notification system for order management.

Provides event-driven fill detection by wrapping KalshiWebSocketSync.
Bridges between the WebSocket fill stream and executor wait logic.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class FillEvent:
    """A fill event.

    Attributes:
        order_id: Order that was filled
        ticker: Market ticker
        side: 'yes' or 'no'
        price: Fill price in cents
        size: Number of contracts filled
        timestamp: When the fill occurred
        exchange: Exchange name (e.g., 'kalshi')
        trade_id: Unique trade identifier
    """

    order_id: str
    ticker: str
    side: str
    price: int
    size: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    exchange: str = ""
    trade_id: str = ""


class FillNotifier:
    """Fill notification hub for event-driven fill detection.

    Wraps KalshiWebSocketSync to provide a simple interface for
    registering orders and waiting for fills.

    Example:
        >>> notifier = FillNotifier()
        >>> event = notifier.register_order("order_123")
        >>> # ... submit order ...
        >>> event.wait(timeout=5.0)
        >>> fills = notifier.get_fills("order_123")
        >>> notifier.unregister_order("order_123")
    """

    def __init__(self) -> None:
        """Initialize fill notifier."""
        self._lock = threading.Lock()
        self._pending_orders: dict = {}
        self._received_fills: dict = {}

    def register_order(self, order_id: str) -> threading.Event:
        """Register an order for fill notification.

        Args:
            order_id: Order ID to wait for

        Returns:
            Event that will be set when fill arrives
        """
        with self._lock:
            event = threading.Event()
            self._pending_orders[order_id] = event
            self._received_fills[order_id] = []
            return event

    def unregister_order(self, order_id: str) -> None:
        """Unregister an order from fill tracking.

        Args:
            order_id: Order ID to unregister
        """
        with self._lock:
            self._pending_orders.pop(order_id, None)
            self._received_fills.pop(order_id, None)

    def get_fills(self, order_id: str) -> List[FillEvent]:
        """Get any fills already received for an order.

        Args:
            order_id: Order ID to check

        Returns:
            List of fills (may be empty)
        """
        with self._lock:
            return list(self._received_fills.get(order_id, []))

    def notify_fill(self, fill: FillEvent) -> None:
        """Notify that a fill has occurred.

        This is called by the orchestrator's _on_ws_fill callback
        to bridge KalshiFill -> FillEvent.

        Args:
            fill: The fill event
        """
        with self._lock:
            order_id = fill.order_id

            if order_id in self._received_fills:
                self._received_fills[order_id].append(fill)

            if order_id in self._pending_orders:
                self._pending_orders[order_id].set()

        logger.debug(
            "Fill notified: order=%s ticker=%s side=%s price=%dc size=%d",
            fill.order_id,
            fill.ticker,
            fill.side,
            fill.price,
            fill.size,
        )

    def clear(self) -> None:
        """Clear all pending orders and fills."""
        with self._lock:
            # Set all pending events to allow any waiters to exit
            for event in self._pending_orders.values():
                event.set()

            self._pending_orders.clear()
            self._received_fills.clear()
