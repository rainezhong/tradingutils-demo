"""Coinbase WebSocket trade feed for real-time BTC-USD trades.

Streams individual trade events (matches) from Coinbase's public WebSocket API.
Provides raw trade data with price, timestamp, and quantity.

Extracted from strategies/crypto_scalp/orchestrator.py for reusability.
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
RECONNECT_DELAY_SEC = 2.0


@dataclass
class Trade:
    """A trade event from Coinbase.

    Attributes:
        symbol: Trading pair symbol (e.g., "BTC-USD")
        price: Trade execution price
        timestamp: Unix timestamp (local time when received)
        quantity: Trade quantity in BTC (called 'size' in Coinbase API)
    """

    symbol: str
    price: float
    timestamp: float
    quantity: float


class CoinbaseTradeStream:
    """Coinbase WebSocket trade stream for BTC-USD.

    Streams live trades (matches) from Coinbase with ~1.7 trades/second average frequency.
    Auto-reconnects on disconnect. Thread-safe callback architecture.

    Example:
        >>> stream = CoinbaseTradeStream()
        >>> stream.on_trade(lambda t: print(f"BTC: ${t.price:.2f} qty={t.quantity:.4f}"))
        >>> stream.start()
        >>> # ... later
        >>> stream.stop()
    """

    def __init__(self) -> None:
        """Initialize Coinbase trade stream."""
        # State
        self._running = False
        self._connected = False

        # Callbacks
        self._trade_callbacks: List[Callable[[Trade], None]] = []
        self._connect_callbacks: List[Callable[[], None]] = []
        self._disconnect_callbacks: List[Callable[[], None]] = []
        self._lock = threading.Lock()

        # Threading
        self._thread: Optional[threading.Thread] = None

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._running and self._connected

    def on_trade(self, callback: Callable[[Trade], None]) -> None:
        """Register callback for trade events.

        Args:
            callback: Function receiving Trade objects
        """
        with self._lock:
            self._trade_callbacks.append(callback)

    def on_connect(self, callback: Callable[[], None]) -> None:
        """Register callback for connection events."""
        with self._lock:
            self._connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register callback for disconnection events."""
        with self._lock:
            self._disconnect_callbacks.append(callback)

    def start(self) -> "CoinbaseTradeStream":
        """Start the WebSocket trade stream.

        Returns:
            Self for chaining
        """
        if self._running:
            return self

        self._running = True

        self._thread = threading.Thread(
            target=self._run_ws_thread,
            daemon=True,
            name="CoinbaseTradeStream",
        )
        self._thread.start()

        # Wait for connection (up to 5 seconds)
        for _ in range(50):
            if self._connected:
                break
            time.sleep(0.1)

        logger.info("CoinbaseTradeStream started")
        return self

    def stop(self) -> None:
        """Stop the WebSocket trade stream."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=3.0)

        self._connected = False

        with self._lock:
            callbacks = list(self._disconnect_callbacks)

        for callback in callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Disconnect callback error: %s", e)

        logger.info("CoinbaseTradeStream stopped")

    def _run_ws_thread(self) -> None:
        """Background thread running the async WebSocket loop."""
        asyncio.run(self._ws_loop())

    async def _ws_loop(self) -> None:
        """Main WebSocket loop with auto-reconnect."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required: pip install websockets")
            return

        while self._running:
            try:
                async with websockets.connect(
                    COINBASE_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    # Subscribe to BTC-USD matches (public, no auth required)
                    subscribe_msg = {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channels": ["matches"],
                    }
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info("Coinbase WS connected: subscribed to BTC-USD matches")
                    self._connected = True

                    with self._lock:
                        connect_callbacks = list(self._connect_callbacks)

                    for cb in connect_callbacks:
                        try:
                            cb()
                        except Exception as e:
                            logger.error("Connect callback error: %s", e)

                    async for raw in ws:
                        if not self._running:
                            break

                        try:
                            msg = json.loads(raw)

                            # Match message format: {"type":"match", "price":"99123.45", "size":"0.001"}
                            # Also handle "last_match" type
                            msg_type = msg.get("type")
                            if msg_type in ("match", "last_match"):
                                price = float(msg["price"])
                                quantity = float(msg.get("size", 0.0))
                                timestamp = time.time()

                                trade = Trade(
                                    symbol="BTC-USD",
                                    price=price,
                                    timestamp=timestamp,
                                    quantity=quantity,
                                )

                                with self._lock:
                                    callbacks = list(self._trade_callbacks)

                                for callback in callbacks:
                                    try:
                                        callback(trade)
                                    except Exception as e:
                                        logger.error("Trade callback error: %s", e)

                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue

            except Exception as e:
                logger.error("Coinbase WS error: %s", e)
                self._connected = False

                with self._lock:
                    disconnect_callbacks = list(self._disconnect_callbacks)

                for cb in disconnect_callbacks:
                    try:
                        cb()
                    except Exception as exc:
                        logger.error("Disconnect callback error: %s", exc)

                if self._running:
                    logger.info("Reconnecting in %.0fs...", RECONNECT_DELAY_SEC)
                    await asyncio.sleep(RECONNECT_DELAY_SEC)
