"""Synchronous wrapper for KalshiWebSocket.

Runs the async WebSocket client in a background thread for use with
synchronous code (executors, OMS). Provides blocking wait_for_fill()
for event-driven fill detection instead of polling.
"""

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from .kalshi_auth import KalshiAuth
from .kalshi_websocket import Channel, KalshiWebSocket, WebSocketConfig

logger = logging.getLogger(__name__)


@dataclass
class KalshiFill:
    """Fill event from Kalshi WebSocket.

    Attributes:
        order_id: The order that was filled
        trade_id: Unique trade identifier
        ticker: Market ticker
        side: 'yes' or 'no'
        price: Fill price in cents
        count: Number of contracts filled
        timestamp: When the fill occurred
        is_taker: Whether this fill was a taker order
    """

    order_id: str
    trade_id: str
    ticker: str
    side: str
    price: int
    count: int
    timestamp: datetime = field(default_factory=datetime.utcnow)
    is_taker: bool = False


# Callback type for fill notifications
FillCallback = Callable[[KalshiFill], None]


class KalshiWebSocketSync:
    """Synchronous wrapper for KalshiWebSocket.

    Runs the async WebSocket client in a background thread for use with
    synchronous executors and the OMS.

    Example:
        >>> auth = KalshiAuth.from_env()
        >>> ws = KalshiWebSocketSync(auth)
        >>> ws.start()
        >>>
        >>> # Register global fill handler
        >>> ws.on_fill(handle_fill)
        >>>
        >>> # Subscribe to fills
        >>> ws.subscribe_fills()
        >>>
        >>> # Wait for specific order fill
        >>> fill = ws.wait_for_fill("order_123", timeout=5.0)
        >>> if fill:
        ...     print(f"Filled at {fill.price}c")
        >>>
        >>> ws.stop()
    """

    def __init__(
        self,
        auth: KalshiAuth,
        config: Optional[WebSocketConfig] = None,
    ) -> None:
        """Initialize synchronous WebSocket client.

        Args:
            auth: Kalshi authentication handler
            config: Optional WebSocket configuration
        """
        self._auth = auth
        self._config = config or WebSocketConfig()
        self._ws = KalshiWebSocket(auth=auth, config=self._config)

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

        # Fill tracking - thread-safe via lock
        self._lock = threading.Lock()
        self._pending_fills: Dict[str, threading.Event] = {}
        self._received_fills: Dict[str, List[KalshiFill]] = {}
        self._fill_callbacks: List[FillCallback] = []

        # Register internal fill handler
        self._ws.on_fill(self._handle_fill_message)

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._ws.is_connected

    def start(self, timeout: float = 5.0) -> "KalshiWebSocketSync":
        """Start WebSocket in background thread.

        Args:
            timeout: Maximum seconds to wait for connection

        Returns:
            Self for chaining
        """
        if self._running:
            return self

        self._running = True
        self._thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
            name="KalshiWebSocket",
        )
        self._thread.start()

        # Wait for connection
        wait_iterations = int(timeout * 10)
        for _ in range(wait_iterations):
            if self._ws.is_connected:
                break
            time.sleep(0.1)

        if not self._ws.is_connected:
            logger.warning("WebSocket connection timeout after %.1fs", timeout)

        return self

    def stop(self) -> None:
        """Stop WebSocket client."""
        self._running = False

        if self._loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._ws.disconnect(),
                    self._loop,
                ).result(timeout=5.0)
            except Exception as e:
                logger.warning("Error stopping WebSocket: %s", e)

        if self._thread:
            self._thread.join(timeout=5.0)

        # Clear pending waits
        with self._lock:
            for event in self._pending_fills.values():
                event.set()
            self._pending_fills.clear()
            self._received_fills.clear()

    def subscribe_fills(self, ticker: str = "*") -> None:
        """Subscribe to fill notifications.

        Args:
            ticker: Market ticker or "*" for all fills
        """
        if not self._loop:
            logger.warning("Cannot subscribe: WebSocket not running")
            return

        # For fills, Kalshi uses a special subscription
        # The ticker "*" subscribes to all authenticated user fills
        asyncio.run_coroutine_threadsafe(
            self._ws.subscribe(Channel.FILL.value, ticker),
            self._loop,
        )
        logger.info("Subscribed to Kalshi fill notifications")

    def subscribe_orderbook(self, ticker: str) -> None:
        """Subscribe to orderbook delta updates.

        Args:
            ticker: Market ticker
        """
        if not self._loop:
            logger.warning("Cannot subscribe: WebSocket not running")
            return

        asyncio.run_coroutine_threadsafe(
            self._ws.subscribe(Channel.ORDERBOOK_DELTA.value, ticker),
            self._loop,
        )
        logger.info(f"Subscribed to orderbook deltas for {ticker}")

    def subscribe_trades(self, ticker: str) -> None:
        """Subscribe to trade updates.

        Args:
            ticker: Market ticker
        """
        if not self._loop:
            logger.warning("Cannot subscribe: WebSocket not running")
            return

        asyncio.run_coroutine_threadsafe(
            self._ws.subscribe(Channel.TRADE.value, ticker),
            self._loop,
        )
        logger.info(f"Subscribed to trades for {ticker}")

    def on_fill(self, callback: FillCallback) -> None:
        """Register a global fill callback.

        Args:
            callback: Function(KalshiFill) -> None
        """
        with self._lock:
            self._fill_callbacks.append(callback)

    def register_order(self, order_id: str) -> threading.Event:
        """Register an order for fill notification.

        Args:
            order_id: Order ID to wait for

        Returns:
            Event that will be set when fill arrives
        """
        with self._lock:
            event = threading.Event()
            self._pending_fills[order_id] = event
            self._received_fills[order_id] = []
            return event

    def unregister_order(self, order_id: str) -> None:
        """Unregister an order from fill tracking.

        Args:
            order_id: Order ID to unregister
        """
        with self._lock:
            self._pending_fills.pop(order_id, None)
            self._received_fills.pop(order_id, None)

    def wait_for_fill(
        self,
        order_id: str,
        timeout: float = 5.0,
    ) -> Optional[List[KalshiFill]]:
        """Wait for an order to fill.

        This is the main method for event-driven fill detection.
        Call register_order() first, then wait on the returned event,
        then call this method to retrieve fills.

        Args:
            order_id: Order ID to wait for
            timeout: Maximum seconds to wait

        Returns:
            List of fills if any, None if timeout/not found
        """
        with self._lock:
            event = self._pending_fills.get(order_id)

        if event:
            event.wait(timeout=timeout)

        with self._lock:
            fills = self._received_fills.get(order_id, [])
            return fills if fills else None

    def get_fills(self, order_id: str) -> List[KalshiFill]:
        """Get any fills already received for an order.

        Args:
            order_id: Order ID to check

        Returns:
            List of fills (may be empty)
        """
        with self._lock:
            return list(self._received_fills.get(order_id, []))

    def _handle_fill_message(self, ticker: str, data: dict) -> None:
        """Handle fill message from WebSocket.

        Args:
            ticker: Market ticker
            data: Fill message data
        """
        try:
            # Parse fill from Kalshi message format
            order_id = data.get("order_id", "")
            if not order_id:
                logger.debug("Fill message missing order_id: %s", data)
                return

            fill = KalshiFill(
                order_id=order_id,
                trade_id=data.get("trade_id", ""),
                ticker=ticker or data.get("market_ticker", ""),
                side=data.get("side", ""),
                price=data.get("price", 0),
                count=data.get("count", 0),
                timestamp=datetime.utcnow(),
                is_taker=data.get("is_taker", False),
            )

            logger.debug(
                "Fill received: order=%s ticker=%s side=%s price=%dc size=%d",
                fill.order_id,
                fill.ticker,
                fill.side,
                fill.price,
                fill.count,
            )

            # Update tracking
            with self._lock:
                if order_id in self._received_fills:
                    self._received_fills[order_id].append(fill)

                if order_id in self._pending_fills:
                    self._pending_fills[order_id].set()

                callbacks = list(self._fill_callbacks)

            # Dispatch to global callbacks (outside lock)
            for callback in callbacks:
                try:
                    callback(fill)
                except Exception as e:
                    logger.error("Fill callback error: %s", e)

        except Exception as e:
            logger.error("Error handling fill message: %s", e)

    def _run_in_thread(self) -> None:
        """Run async WebSocket client in background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._run_ws())
        except Exception as e:
            logger.error("WebSocket thread error: %s", e)
        finally:
            self._loop.close()

    async def _run_ws(self) -> None:
        """Run WebSocket connection loop."""
        try:
            await self._ws.connect()

            # Keep running until stopped
            while self._running:
                await asyncio.sleep(0.1)

        except Exception as e:
            logger.error("WebSocket error: %s", e)
        finally:
            await self._ws.disconnect()
