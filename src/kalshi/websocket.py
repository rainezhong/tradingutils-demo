"""WebSocket client for real-time Kalshi market data."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional, Set

from .auth import KalshiAuth
from .exceptions import WebSocketError
from .orderbook import OrderBookManager

logger = logging.getLogger(__name__)

try:
    import websockets
    from websockets.exceptions import ConnectionClosed

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets package not installed")


class Channel(str, Enum):
    """Available WebSocket channels."""

    ORDERBOOK_DELTA = "orderbook_delta"
    TICKER = "ticker"
    TRADE = "trade"
    FILL = "fill"
    ORDER = "order"


class ConnectionState(str, Enum):
    """Connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


@dataclass
class WebSocketConfig:
    """WebSocket configuration.

    Attributes:
        url: WebSocket endpoint URL
        reconnect_delay_base: Initial reconnection delay
        reconnect_delay_max: Maximum reconnection delay
        heartbeat_interval: Ping interval in seconds
        message_timeout: Message receive timeout
        max_reconnect_attempts: Max reconnection attempts (0=unlimited)
    """

    url: str = "wss://trading-api.kalshi.com/trade-api/ws/v2"
    reconnect_delay_base: float = 1.0
    reconnect_delay_max: float = 60.0
    heartbeat_interval: float = 30.0
    message_timeout: float = 60.0
    max_reconnect_attempts: int = 0


@dataclass
class Subscription:
    """Active subscription."""

    channel: str
    ticker: str
    subscribed_at: float = field(default_factory=time.time)


# Callback types
MarketUpdateCallback = Callable[[str, dict], None]
ErrorCallback = Callable[[Exception], None]


class KalshiWebSocket:
    """WebSocket client for real-time Kalshi market data.

    Provides streaming order book updates, trades, and account events.
    Automatically handles reconnection and subscription restoration.

    Example:
        >>> auth = KalshiAuth.from_env()
        >>> config = WebSocketConfig()
        >>> ws = KalshiWebSocket(auth, config)
        >>>
        >>> # Register callbacks
        >>> ws.on_orderbook_delta(handle_orderbook)
        >>> ws.on_trade(handle_trade)
        >>>
        >>> # Connect and subscribe
        >>> async with ws:
        ...     await ws.subscribe("orderbook_delta", "TICKER")
        ...     await asyncio.sleep(3600)
    """

    def __init__(
        self,
        auth: Optional[KalshiAuth] = None,
        config: Optional[WebSocketConfig] = None,
        orderbook_manager: Optional[OrderBookManager] = None,
    ):
        """Initialize WebSocket client.

        Args:
            auth: Authentication handler (required for private channels)
            config: WebSocket configuration
            orderbook_manager: Optional order book manager for automatic updates
        """
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError("websockets package required")

        self._auth = auth
        self._config = config or WebSocketConfig()
        self._orderbook_manager = orderbook_manager

        self._ws = None
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_attempts = 0

        self._subscriptions: Dict[str, Subscription] = {}
        self._pending_subscriptions: Set[str] = set()

        # Callbacks
        self._orderbook_snapshot_callbacks: List[MarketUpdateCallback] = []
        self._orderbook_delta_callbacks: List[MarketUpdateCallback] = []
        self._trade_callbacks: List[MarketUpdateCallback] = []
        self._ticker_callbacks: List[MarketUpdateCallback] = []
        self._fill_callbacks: List[MarketUpdateCallback] = []
        self._order_callbacks: List[MarketUpdateCallback] = []
        self._error_callbacks: List[ErrorCallback] = []

        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._state == ConnectionState.CONNECTED and self._ws is not None

    @property
    def state(self) -> ConnectionState:
        """Get connection state."""
        return self._state

    async def connect(self) -> None:
        """Connect to WebSocket server."""
        if self._state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            return

        self._state = ConnectionState.CONNECTING
        self._running = True

        try:
            headers = {}
            if self._auth:
                headers = self._auth.sign_request(
                    "GET",
                    "/trade-api/ws/v2",
                )

            self._ws = await websockets.connect(
                self._config.url,
                additional_headers=headers if headers else None,
                ping_interval=None,
            )
            self._state = ConnectionState.CONNECTED
            self._reconnect_attempts = 0

            logger.info(f"Connected to {self._config.url}")

            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            await self._restore_subscriptions()

        except Exception as e:
            self._state = ConnectionState.DISCONNECTED
            raise WebSocketError(f"Connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from WebSocket server."""
        if self._state == ConnectionState.DISCONNECTED:
            return

        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._state = ConnectionState.DISCONNECTED
        logger.info("Disconnected")

    async def subscribe(self, channel: str, ticker: str) -> None:
        """Subscribe to a channel.

        Args:
            channel: Channel name (orderbook_delta, ticker, trade, fill, order)
            ticker: Market ticker
        """
        sub_key = f"{channel}:{ticker}"

        if sub_key in self._subscriptions:
            return

        if not self.is_connected:
            self._pending_subscriptions.add(sub_key)
            return

        message = {
            "id": int(time.time() * 1000),
            "cmd": "subscribe",
            "params": {
                "channels": [channel],
                "market_tickers": [ticker],
            },
        }

        try:
            await self._ws.send(json.dumps(message))
            self._subscriptions[sub_key] = Subscription(channel=channel, ticker=ticker)
            self._pending_subscriptions.discard(sub_key)
            logger.info(f"Subscribed to {channel} for {ticker}")
        except Exception as e:
            raise WebSocketError(f"Subscribe failed: {e}") from e

    async def unsubscribe(self, channel: str, ticker: str) -> None:
        """Unsubscribe from a channel."""
        sub_key = f"{channel}:{ticker}"

        if sub_key not in self._subscriptions:
            self._pending_subscriptions.discard(sub_key)
            return

        if not self.is_connected:
            del self._subscriptions[sub_key]
            return

        message = {
            "id": int(time.time() * 1000),
            "cmd": "unsubscribe",
            "params": {
                "channels": [channel],
                "market_tickers": [ticker],
            },
        }

        try:
            await self._ws.send(json.dumps(message))
            del self._subscriptions[sub_key]
        except Exception as e:
            logger.warning(f"Unsubscribe failed: {e}")

    async def _restore_subscriptions(self) -> None:
        """Restore subscriptions after reconnect."""
        pending = list(self._pending_subscriptions)
        for sub_key in pending:
            channel, ticker = sub_key.split(":", 1)
            await self.subscribe(channel, ticker)

        for sub in list(self._subscriptions.values()):
            message = {
                "id": int(time.time() * 1000),
                "cmd": "subscribe",
                "params": {
                    "channels": [sub.channel],
                    "market_tickers": [sub.ticker],
                },
            }
            try:
                await self._ws.send(json.dumps(message))
            except Exception as e:
                logger.warning(f"Failed to restore subscription: {e}")

    async def _receive_loop(self) -> None:
        """Receive and process messages."""
        while self._running and self._ws:
            try:
                message = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=self._config.message_timeout,
                )
                await self._handle_message(message)

            except asyncio.TimeoutError:
                continue

            except ConnectionClosed as e:
                logger.warning(f"Connection closed: {e.code}")
                if self._running:
                    await self._handle_disconnect()
                break

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error(f"Receive error: {e}")
                self._notify_error(WebSocketError(str(e)))
                if self._running:
                    await self._handle_disconnect()
                break

    async def _handle_message(self, raw: str) -> None:
        """Parse and dispatch message."""
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = message.get("type", "")
        channel = message.get("channel", "")
        data = message.get("msg", {})
        ticker = message.get("sid", data.get("market_ticker", ""))

        logger.debug(f"Received: type={msg_type}, channel={channel}")

        # Update order book manager if available
        if self._orderbook_manager:
            if msg_type == "orderbook_snapshot":
                self._orderbook_manager.apply_snapshot(ticker, data)
            elif msg_type == "orderbook_delta" or channel == "orderbook_delta":
                try:
                    self._orderbook_manager.apply_delta(ticker, data)
                except Exception as e:
                    logger.warning(f"Failed to apply delta: {e}")

        # Dispatch to callbacks
        if msg_type == "orderbook_snapshot":
            for cb in self._orderbook_snapshot_callbacks:
                try:
                    cb(ticker, data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

        elif msg_type == "orderbook_delta" or channel == "orderbook_delta":
            for cb in self._orderbook_delta_callbacks:
                try:
                    cb(ticker, data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

        elif msg_type == "trade" or channel == "trade":
            for cb in self._trade_callbacks:
                try:
                    cb(ticker, data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

        elif msg_type == "ticker" or channel == "ticker":
            for cb in self._ticker_callbacks:
                try:
                    cb(ticker, data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

        elif msg_type == "fill" or channel == "fill":
            for cb in self._fill_callbacks:
                try:
                    cb(ticker, data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

        elif msg_type == "order" or channel == "order":
            for cb in self._order_callbacks:
                try:
                    cb(ticker, data)
                except Exception as e:
                    logger.error(f"Callback error: {e}")

        elif msg_type == "error":
            error_msg = data.get("message", str(data))
            logger.error(f"Server error: {error_msg}")
            self._notify_error(WebSocketError(error_msg))

    async def _heartbeat_loop(self) -> None:
        """Send periodic pings."""
        while self._running and self._ws:
            try:
                await asyncio.sleep(self._config.heartbeat_interval)
                if self.is_connected:
                    await self._ws.send(json.dumps({
                        "id": int(time.time() * 1000),
                        "cmd": "ping",
                    }))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Heartbeat error: {e}")

    async def _handle_disconnect(self) -> None:
        """Handle disconnection with reconnection."""
        if not self._running:
            return

        self._state = ConnectionState.RECONNECTING
        self._ws = None

        while self._running:
            self._reconnect_attempts += 1

            if (
                self._config.max_reconnect_attempts > 0
                and self._reconnect_attempts > self._config.max_reconnect_attempts
            ):
                logger.error("Max reconnection attempts exceeded")
                self._state = ConnectionState.DISCONNECTED
                self._notify_error(WebSocketError("Max reconnection attempts"))
                return

            delay = min(
                self._config.reconnect_delay_base * (2 ** (self._reconnect_attempts - 1)),
                self._config.reconnect_delay_max,
            )

            logger.info(f"Reconnecting in {delay:.1f}s (attempt {self._reconnect_attempts})")
            await asyncio.sleep(delay)

            try:
                headers = {}
                if self._auth:
                    headers = self._auth.sign_request("GET", "/trade-api/ws/v2")

                self._ws = await websockets.connect(
                    self._config.url,
                    additional_headers=headers if headers else None,
                    ping_interval=None,
                )
                self._state = ConnectionState.CONNECTED
                self._reconnect_attempts = 0

                logger.info("Reconnected successfully")
                self._receive_task = asyncio.create_task(self._receive_loop())
                await self._restore_subscriptions()
                return

            except Exception as e:
                logger.warning(f"Reconnection failed: {e}")

    def _notify_error(self, error: Exception) -> None:
        """Notify error callbacks."""
        for cb in self._error_callbacks:
            try:
                cb(error)
            except Exception:
                pass

    # Callback registration
    def on_orderbook_snapshot(self, callback: MarketUpdateCallback) -> None:
        """Register orderbook snapshot callback."""
        self._orderbook_snapshot_callbacks.append(callback)

    def on_orderbook_delta(self, callback: MarketUpdateCallback) -> None:
        """Register orderbook delta callback."""
        self._orderbook_delta_callbacks.append(callback)

    def on_trade(self, callback: MarketUpdateCallback) -> None:
        """Register trade callback."""
        self._trade_callbacks.append(callback)

    def on_ticker(self, callback: MarketUpdateCallback) -> None:
        """Register ticker callback."""
        self._ticker_callbacks.append(callback)

    def on_fill(self, callback: MarketUpdateCallback) -> None:
        """Register fill callback (authenticated)."""
        self._fill_callbacks.append(callback)

    def on_order(self, callback: MarketUpdateCallback) -> None:
        """Register order callback (authenticated)."""
        self._order_callbacks.append(callback)

    def on_error(self, callback: ErrorCallback) -> None:
        """Register error callback."""
        self._error_callbacks.append(callback)

    async def __aenter__(self) -> "KalshiWebSocket":
        await self.connect()
        return self

    async def __aexit__(self, *args) -> None:
        await self.disconnect()
