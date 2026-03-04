"""Binance WebSocket trade feed for real-time BTC/USDT trades.

Streams individual trade events from Binance's public WebSocket API.
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

BINANCE_WS_URL = "wss://data-stream.binance.vision/ws/btcusdt@trade"
RECONNECT_DELAY_SEC = 2.0


@dataclass
class Trade:
    """A trade event from Binance.

    Attributes:
        symbol: Trading pair symbol (e.g., "BTCUSDT")
        price: Trade execution price
        timestamp: Unix timestamp (local time when received)
        quantity: Trade quantity in BTC
    """

    symbol: str
    price: float
    timestamp: float
    quantity: float


class BinanceTradeStream:
    """Binance WebSocket trade stream for BTC/USDT.

    Streams live trades from Binance with ~8 trades/second average frequency.
    Auto-reconnects on disconnect. Thread-safe callback architecture.

    Example:
        >>> stream = BinanceTradeStream()
        >>> stream.on_trade(lambda t: print(f"BTC: ${t.price:.2f} qty={t.quantity:.4f}"))
        >>> stream.start()
        >>> # ... later
        >>> stream.stop()
    """

    def __init__(self) -> None:
        """Initialize Binance trade stream."""
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

    def start(self) -> "BinanceTradeStream":
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
            name="BinanceTradeStream",
        )
        self._thread.start()

        # Wait for connection (up to 5 seconds)
        for _ in range(50):
            if self._connected:
                break
            time.sleep(0.1)

        logger.info("BinanceTradeStream started")
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

        logger.info("BinanceTradeStream stopped")

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
                    BINANCE_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    logger.info("Binance WS connected: %s", BINANCE_WS_URL)
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

                            # Trade message format: {"e":"trade", "p":"99123.45", "q":"0.001", "T":1234567890}
                            if "p" in msg and "T" in msg:
                                price = float(msg["p"])
                                quantity = float(msg.get("q", 0.0))
                                timestamp = time.time()

                                trade = Trade(
                                    symbol="BTCUSDT",
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
                logger.error("Binance WS error: %s", e)
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
