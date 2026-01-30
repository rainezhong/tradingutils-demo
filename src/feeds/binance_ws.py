"""Binance WebSocket client for real-time crypto price feeds.

Provides low-latency streaming of trade prices for BTC, ETH, SOL and other
crypto assets from Binance's WebSocket API.
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


WS_URL = "wss://stream.binance.com:9443/ws"


@dataclass
class PriceUpdate:
    """A real-time price update from Binance.

    Attributes:
        symbol: Trading pair symbol (e.g., "BTCUSDT")
        price: Current trade price
        quantity: Trade quantity
        timestamp: Exchange timestamp (milliseconds since epoch)
        local_timestamp: Local receive timestamp
    """

    symbol: str
    price: float
    quantity: float
    timestamp: int  # Exchange timestamp in ms
    local_timestamp: float  # Local time.time()

    @property
    def age_ms(self) -> float:
        """Age of this update in milliseconds."""
        return (time.time() - self.local_timestamp) * 1000


class BinanceWebSocket:
    """WebSocket client for Binance real-time trade data.

    Streams trade events for specified symbols with minimal latency.
    Maintains the latest price for each subscribed symbol.

    Example:
        >>> ws = BinanceWebSocket(symbols=["BTCUSDT", "ETHUSDT"])
        >>> ws.on_price_update(lambda u: print(f"{u.symbol}: ${u.price:.2f}"))
        >>> ws.start()
        >>> # ... later
        >>> print(ws.get_price("BTCUSDT"))
        >>> ws.stop()
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        url: str = WS_URL,
        reconnect: bool = True,
        max_reconnect_attempts: int = 10,
    ) -> None:
        """Initialize Binance WebSocket client.

        Args:
            symbols: List of symbols to subscribe to (e.g., ["BTCUSDT", "ETHUSDT"])
            url: WebSocket base URL
            reconnect: Whether to auto-reconnect on disconnect
            max_reconnect_attempts: Maximum reconnection attempts
        """
        self._url = url
        self._reconnect = reconnect
        self._max_reconnect_attempts = max_reconnect_attempts

        # Subscriptions
        self._symbols: Set[str] = set(s.lower() for s in (symbols or []))

        # State
        self._websocket = None
        self._connected = False
        self._running = False
        self._reconnect_count = 0

        # Price tracking
        self._latest_prices: Dict[str, PriceUpdate] = {}
        self._lock = threading.Lock()

        # Callbacks
        self._price_callbacks: List[Callable[[PriceUpdate], None]] = []
        self._connect_callbacks: List[Callable[[], None]] = []
        self._disconnect_callbacks: List[Callable[[], None]] = []

        # Threading
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected

    @property
    def symbols(self) -> Set[str]:
        """Get subscribed symbols."""
        return self._symbols.copy()

    def on_price_update(self, callback: Callable[[PriceUpdate], None]) -> None:
        """Register callback for price updates.

        Args:
            callback: Function called with PriceUpdate on each trade
        """
        self._price_callbacks.append(callback)

    def on_connect(self, callback: Callable[[], None]) -> None:
        """Register callback for connection events."""
        self._connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register callback for disconnection events."""
        self._disconnect_callbacks.append(callback)

    def add_symbol(self, symbol: str) -> None:
        """Add a symbol to subscribe to.

        Args:
            symbol: Trading pair symbol (e.g., "BTCUSDT")
        """
        symbol_lower = symbol.lower()
        with self._lock:
            self._symbols.add(symbol_lower)

        # If connected, subscribe to the new symbol
        if self._connected and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._subscribe([symbol_lower]),
                self._loop,
            )

    def remove_symbol(self, symbol: str) -> None:
        """Remove a symbol from subscriptions.

        Args:
            symbol: Trading pair symbol to unsubscribe
        """
        symbol_lower = symbol.lower()
        with self._lock:
            self._symbols.discard(symbol_lower)

        # If connected, unsubscribe
        if self._connected and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._unsubscribe([symbol_lower]),
                self._loop,
            )

    def get_price(self, symbol: str) -> Optional[PriceUpdate]:
        """Get the latest price update for a symbol.

        Args:
            symbol: Trading pair symbol

        Returns:
            Latest PriceUpdate or None if no data available
        """
        with self._lock:
            return self._latest_prices.get(symbol.lower())

    def get_all_prices(self) -> Dict[str, PriceUpdate]:
        """Get all latest prices.

        Returns:
            Dictionary mapping symbol to latest PriceUpdate
        """
        with self._lock:
            return dict(self._latest_prices)

    def start(self) -> "BinanceWebSocket":
        """Start WebSocket client in background thread.

        Returns:
            Self for chaining
        """
        if self._running:
            return self

        self._running = True
        self._thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
            name="BinanceWebSocket",
        )
        self._thread.start()

        # Wait for connection (up to 5 seconds)
        for _ in range(50):
            if self._connected:
                break
            time.sleep(0.1)

        return self

    def stop(self) -> None:
        """Stop WebSocket client."""
        self._running = False

        if self._loop:
            asyncio.run_coroutine_threadsafe(self._close(), self._loop)

        if self._thread:
            self._thread.join(timeout=5.0)

        logger.info("BinanceWebSocket stopped")

    def _run_in_thread(self) -> None:
        """Run async client in thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._run())
        except Exception as e:
            logger.error("BinanceWebSocket thread error: %s", e)
        finally:
            self._loop.close()

    async def _run(self) -> None:
        """Main WebSocket run loop."""
        while self._running:
            try:
                await self._connect()
                await self._receive_loop()
            except Exception as e:
                logger.error("BinanceWebSocket error: %s", e)
                self._connected = False

                # Notify disconnect callbacks
                for callback in self._disconnect_callbacks:
                    try:
                        callback()
                    except Exception as ce:
                        logger.error("Disconnect callback error: %s", ce)

                if self._running and self._reconnect:
                    await self._handle_reconnect()
                else:
                    break

    async def _connect(self) -> None:
        """Connect to Binance WebSocket."""
        try:
            import websockets
        except ImportError:
            raise RuntimeError(
                "websockets package required. Install with: pip install websockets"
            )

        if self._connected:
            return

        # Build stream URL with all symbols
        streams = [f"{s}@trade" for s in self._symbols]
        if not streams:
            logger.warning("No symbols to subscribe to")
            return

        # Use combined stream endpoint for multiple symbols
        if len(streams) == 1:
            url = f"{self._url}/{streams[0]}"
        else:
            stream_param = "/".join(streams)
            url = f"wss://stream.binance.com:9443/stream?streams={stream_param}"

        self._websocket = await websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        )
        self._connected = True
        self._reconnect_count = 0

        logger.info(
            "BinanceWebSocket connected (symbols: %s)",
            ", ".join(self._symbols),
        )

        # Notify connect callbacks
        for callback in self._connect_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Connect callback error: %s", e)

    async def _close(self) -> None:
        """Close WebSocket connection."""
        self._connected = False

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

    async def _receive_loop(self) -> None:
        """Process incoming messages."""
        if not self._websocket:
            return

        async for message in self._websocket:
            try:
                data = json.loads(message)
                self._handle_message(data)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON: %s", message[:100])
            except Exception as e:
                logger.error("Message handling error: %s", e)

    def _handle_message(self, data: Dict) -> None:
        """Handle a WebSocket message.

        Args:
            data: Parsed JSON message
        """
        local_ts = time.time()

        # Handle combined stream format
        if "stream" in data:
            data = data.get("data", {})

        # Parse trade event
        event_type = data.get("e")
        if event_type != "trade":
            return

        symbol = data.get("s", "").lower()
        if not symbol:
            return

        try:
            update = PriceUpdate(
                symbol=symbol,
                price=float(data.get("p", 0)),
                quantity=float(data.get("q", 0)),
                timestamp=int(data.get("T", 0)),
                local_timestamp=local_ts,
            )
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse price update: %s", e)
            return

        # Update latest price
        with self._lock:
            self._latest_prices[symbol] = update

        # Notify callbacks
        for callback in self._price_callbacks:
            try:
                callback(update)
            except Exception as e:
                logger.error("Price callback error: %s", e)

    async def _subscribe(self, symbols: List[str]) -> None:
        """Subscribe to additional symbols."""
        if not self._websocket or not symbols:
            return

        streams = [f"{s}@trade" for s in symbols]
        message = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": int(time.time() * 1000),
        }

        try:
            await self._websocket.send(json.dumps(message))
            logger.debug("Subscribed to: %s", symbols)
        except Exception as e:
            logger.error("Subscribe failed: %s", e)

    async def _unsubscribe(self, symbols: List[str]) -> None:
        """Unsubscribe from symbols."""
        if not self._websocket or not symbols:
            return

        streams = [f"{s}@trade" for s in symbols]
        message = {
            "method": "UNSUBSCRIBE",
            "params": streams,
            "id": int(time.time() * 1000),
        }

        try:
            await self._websocket.send(json.dumps(message))
            logger.debug("Unsubscribed from: %s", symbols)
        except Exception as e:
            logger.error("Unsubscribe failed: %s", e)

    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self._reconnect_count += 1

        if self._reconnect_count > self._max_reconnect_attempts:
            logger.error("Max reconnection attempts exceeded")
            self._running = False
            return

        delay = min(1.0 * (2 ** (self._reconnect_count - 1)), 60.0)

        logger.info(
            "Reconnecting in %.1fs (attempt %d/%d)",
            delay,
            self._reconnect_count,
            self._max_reconnect_attempts,
        )

        await asyncio.sleep(delay)

        try:
            await self._connect()
        except Exception as e:
            logger.error("Reconnection failed: %s", e)
