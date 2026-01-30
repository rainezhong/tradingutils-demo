"""Kalshi WebSocket client for real-time market data streaming."""

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from .config import Config, get_config
from .exceptions import AuthenticationError, WebSocketError
from .utils import setup_logger

logger = setup_logger(__name__)

# Optional websockets import
try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException

    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.debug("websockets not installed - WebSocket client disabled")

# Optional cryptography import for authentication
try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False


class Channel(str, Enum):
    """Available WebSocket channels."""

    ORDERBOOK_DELTA = "orderbook_delta"
    TICKER = "ticker"
    TRADE = "trade"
    FILL = "fill"  # Authenticated only
    ORDER = "order"  # Authenticated only


class ConnectionState(str, Enum):
    """WebSocket connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    CLOSING = "closing"


@dataclass
class WebSocketConfig:
    """WebSocket client configuration.

    Attributes:
        url: WebSocket endpoint URL
        reconnect_delay_base: Initial reconnection delay in seconds
        reconnect_delay_max: Maximum reconnection delay in seconds
        heartbeat_interval: Interval between ping messages in seconds
        message_timeout: Timeout for receiving messages in seconds
        max_reconnect_attempts: Max reconnection attempts (0 = unlimited)
    """

    url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    reconnect_delay_base: float = 1.0
    reconnect_delay_max: float = 60.0
    heartbeat_interval: float = 30.0
    message_timeout: float = 60.0
    max_reconnect_attempts: int = 0  # 0 = unlimited


@dataclass
class Subscription:
    """Represents an active channel subscription."""

    channel: str
    ticker: str
    subscribed_at: float = field(default_factory=time.time)


# Type aliases for callbacks
OrderBookSnapshotCallback = Callable[[str, dict], None]
OrderBookDeltaCallback = Callable[[str, dict], None]
TradeCallback = Callable[[str, dict], None]
TickerCallback = Callable[[str, dict], None]
FillCallback = Callable[[dict], None]
OrderCallback = Callable[[dict], None]
ErrorCallback = Callable[[Exception], None]


class KalshiWebSocketClient:
    """WebSocket client for real-time Kalshi market data.

    Provides streaming access to order book updates, trades, and account
    events. Handles authentication, automatic reconnection, and heartbeat.

    Usage:
        async with KalshiWebSocketClient(config) as client:
            client.on_orderbook_delta(handle_delta)
            await client.subscribe("orderbook_delta", "TICKER")
            await asyncio.sleep(3600)  # Run for an hour
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        ws_config: Optional[WebSocketConfig] = None,
    ):
        """
        Initialize the WebSocket client.

        Args:
            config: Application config (for API keys)
            ws_config: WebSocket-specific configuration

        Raises:
            ImportError: If websockets package is not installed
        """
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError(
                "websockets package required for WebSocket client. "
                "Install with: pip install websockets"
            )

        self.config = config or get_config()
        self.ws_config = ws_config or WebSocketConfig()

        # Connection state
        self._ws = None
        self._state = ConnectionState.DISCONNECTED
        self._reconnect_attempts = 0

        # Authentication
        self._private_key = None
        self._load_private_key()

        # Subscriptions
        self._subscriptions: Dict[str, Subscription] = {}
        self._pending_subscriptions: Set[str] = set()

        # Callbacks
        self._orderbook_snapshot_callbacks: List[OrderBookSnapshotCallback] = []
        self._orderbook_delta_callbacks: List[OrderBookDeltaCallback] = []
        self._trade_callbacks: List[TradeCallback] = []
        self._ticker_callbacks: List[TickerCallback] = []
        self._fill_callbacks: List[FillCallback] = []
        self._order_callbacks: List[OrderCallback] = []
        self._error_callbacks: List[ErrorCallback] = []

        # Background tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False

    def _load_private_key(self) -> None:
        """Load private key for authentication."""
        if not self.config.api_private_key_path or not self.config.api_key_id:
            return

        if not CRYPTO_AVAILABLE:
            logger.warning(
                "cryptography package not installed - "
                "authenticated WebSocket channels unavailable"
            )
            return

        key_path = Path(self.config.api_private_key_path)
        if not key_path.exists():
            logger.warning(f"Private key file not found: {key_path}")
            return

        try:
            with open(key_path, "rb") as f:
                self._private_key = serialization.load_pem_private_key(
                    f.read(),
                    password=None,
                    backend=default_backend(),
                )
            logger.info("Loaded API private key for WebSocket authentication")
        except Exception as e:
            logger.error(f"Failed to load private key: {e}")

    def _generate_auth_headers(self) -> Dict[str, str]:
        """Generate authentication headers for WebSocket connection."""
        if not self._private_key or not self.config.api_key_id:
            return {}

        timestamp_ms = int(time.time() * 1000)
        timestamp_str = str(timestamp_ms)

        # For WebSocket, sign: timestamp + GET + /trade-api/ws/v2
        message = f"{timestamp_str}GET/trade-api/ws/v2"

        signature = self._private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        }

    @property
    def is_authenticated(self) -> bool:
        """Check if client can authenticate."""
        return self._private_key is not None and bool(self.config.api_key_id)

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._state == ConnectionState.CONNECTED and self._ws is not None

    @property
    def state(self) -> ConnectionState:
        """Get current connection state."""
        return self._state

    async def connect(self) -> None:
        """
        Connect to the WebSocket server.

        Raises:
            WebSocketError: If connection fails
            AuthenticationError: If authentication fails
        """
        if self._state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            logger.warning("Already connected or connecting")
            return

        self._state = ConnectionState.CONNECTING
        self._running = True

        try:
            headers = self._generate_auth_headers()
            self._ws = await websockets.connect(
                self.ws_config.url,
                additional_headers=headers if headers else None,
                ping_interval=None,  # We handle our own heartbeat
            )
            self._state = ConnectionState.CONNECTED
            self._reconnect_attempts = 0

            logger.info(f"Connected to {self.ws_config.url}")

            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Resubscribe to any previous subscriptions
            await self._restore_subscriptions()

        except Exception as e:
            self._state = ConnectionState.DISCONNECTED
            error_msg = f"Failed to connect: {e}"
            logger.error(error_msg)
            raise WebSocketError(error_msg) from e

    async def disconnect(self) -> None:
        """Gracefully disconnect from the WebSocket server."""
        if self._state == ConnectionState.DISCONNECTED:
            return

        self._state = ConnectionState.CLOSING
        self._running = False

        # Cancel background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
            self._ws = None

        self._state = ConnectionState.DISCONNECTED
        logger.info("Disconnected from WebSocket")

    async def subscribe(self, channel: str, ticker: str) -> None:
        """
        Subscribe to a market channel.

        Args:
            channel: Channel name (orderbook_delta, ticker, trade, fill, order)
            ticker: Market ticker

        Raises:
            WebSocketError: If not connected
        """
        sub_key = f"{channel}:{ticker}"

        if sub_key in self._subscriptions:
            logger.debug(f"Already subscribed to {sub_key}")
            return

        if not self.is_connected:
            # Queue for subscription when connected
            self._pending_subscriptions.add(sub_key)
            logger.debug(f"Queued subscription for {sub_key}")
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
            raise WebSocketError(f"Failed to subscribe: {e}") from e

    async def unsubscribe(self, channel: str, ticker: str) -> None:
        """
        Unsubscribe from a market channel.

        Args:
            channel: Channel name
            ticker: Market ticker
        """
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
            logger.info(f"Unsubscribed from {channel} for {ticker}")
        except Exception as e:
            logger.warning(f"Failed to unsubscribe: {e}")

    async def _restore_subscriptions(self) -> None:
        """Restore subscriptions after reconnection."""
        # Process pending subscriptions first
        pending = list(self._pending_subscriptions)
        for sub_key in pending:
            channel, ticker = sub_key.split(":", 1)
            await self.subscribe(channel, ticker)

        # Re-subscribe to existing subscriptions
        for sub_key, sub in list(self._subscriptions.items()):
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
                logger.debug(f"Restored subscription to {sub_key}")
            except Exception as e:
                logger.warning(f"Failed to restore subscription {sub_key}: {e}")

    async def _receive_loop(self) -> None:
        """Background task to receive and process messages."""
        while self._running and self._ws:
            try:
                message = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=self.ws_config.message_timeout,
                )
                await self._handle_message(message)

            except asyncio.TimeoutError:
                logger.warning("Message receive timeout")
                continue

            except ConnectionClosed as e:
                logger.warning(f"Connection closed: code={e.code}, reason={e.reason}")
                if self._running:
                    await self._handle_disconnect()
                break

            except asyncio.CancelledError:
                break

            except Exception as e:
                logger.error(f"Error receiving message: {e}")
                self._notify_error(WebSocketError(str(e)))
                if self._running:
                    await self._handle_disconnect()
                break

    async def _handle_message(self, raw_message: str) -> None:
        """Parse and dispatch a received message."""
        try:
            message = json.loads(raw_message)
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON message: {e}")
            return

        msg_type = message.get("type", "")
        channel = message.get("channel", "")
        data = message.get("msg", {})
        ticker = message.get("sid", data.get("market_ticker", ""))

        logger.debug(f"Received: type={msg_type}, channel={channel}, ticker={ticker}")

        # Handle different message types
        if msg_type == "orderbook_snapshot":
            for callback in self._orderbook_snapshot_callbacks:
                try:
                    callback(ticker, data)
                except Exception as e:
                    logger.error(f"Error in orderbook_snapshot callback: {e}")

        elif msg_type == "orderbook_delta" or channel == "orderbook_delta":
            for callback in self._orderbook_delta_callbacks:
                try:
                    callback(ticker, data)
                except Exception as e:
                    logger.error(f"Error in orderbook_delta callback: {e}")

        elif msg_type == "trade" or channel == "trade":
            for callback in self._trade_callbacks:
                try:
                    callback(ticker, data)
                except Exception as e:
                    logger.error(f"Error in trade callback: {e}")

        elif msg_type == "ticker" or channel == "ticker":
            for callback in self._ticker_callbacks:
                try:
                    callback(ticker, data)
                except Exception as e:
                    logger.error(f"Error in ticker callback: {e}")

        elif msg_type == "fill" or channel == "fill":
            for callback in self._fill_callbacks:
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in fill callback: {e}")

        elif msg_type == "order" or channel == "order":
            for callback in self._order_callbacks:
                try:
                    callback(data)
                except Exception as e:
                    logger.error(f"Error in order callback: {e}")

        elif msg_type == "subscribed":
            logger.debug(f"Subscription confirmed: {data}")

        elif msg_type == "error":
            error_msg = data.get("message", str(data))
            logger.error(f"Server error: {error_msg}")
            self._notify_error(WebSocketError(error_msg))

        elif msg_type == "pong":
            logger.debug("Received pong")

    async def _heartbeat_loop(self) -> None:
        """Background task to send periodic heartbeats."""
        while self._running and self._ws:
            try:
                await asyncio.sleep(self.ws_config.heartbeat_interval)

                if self.is_connected:
                    ping_msg = json.dumps({
                        "id": int(time.time() * 1000),
                        "cmd": "ping",
                    })
                    await self._ws.send(ping_msg)
                    logger.debug("Sent ping")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Heartbeat error: {e}")

    async def _handle_disconnect(self) -> None:
        """Handle unexpected disconnection with reconnection logic."""
        if not self._running:
            return

        self._state = ConnectionState.RECONNECTING
        self._ws = None

        while self._running:
            self._reconnect_attempts += 1

            # Check max attempts
            if (
                self.ws_config.max_reconnect_attempts > 0
                and self._reconnect_attempts > self.ws_config.max_reconnect_attempts
            ):
                logger.error("Max reconnection attempts exceeded")
                self._state = ConnectionState.DISCONNECTED
                self._notify_error(
                    WebSocketError("Max reconnection attempts exceeded")
                )
                return

            # Calculate backoff delay
            delay = min(
                self.ws_config.reconnect_delay_base * (2 ** (self._reconnect_attempts - 1)),
                self.ws_config.reconnect_delay_max,
            )

            logger.info(
                f"Reconnecting in {delay:.1f}s "
                f"(attempt {self._reconnect_attempts})"
            )
            await asyncio.sleep(delay)

            try:
                headers = self._generate_auth_headers()
                self._ws = await websockets.connect(
                    self.ws_config.url,
                    additional_headers=headers if headers else None,
                    ping_interval=None,
                )
                self._state = ConnectionState.CONNECTED
                self._reconnect_attempts = 0

                logger.info("Reconnected successfully")

                # Restart receive loop
                self._receive_task = asyncio.create_task(self._receive_loop())

                # Restore subscriptions
                await self._restore_subscriptions()
                return

            except Exception as e:
                logger.warning(f"Reconnection failed: {e}")

    def _notify_error(self, error: Exception) -> None:
        """Notify registered error callbacks."""
        for callback in self._error_callbacks:
            try:
                callback(error)
            except Exception as e:
                logger.error(f"Error in error callback: {e}")

    # Callback registration methods
    def on_orderbook_snapshot(self, callback: OrderBookSnapshotCallback) -> None:
        """Register callback for order book snapshots."""
        self._orderbook_snapshot_callbacks.append(callback)

    def on_orderbook_delta(self, callback: OrderBookDeltaCallback) -> None:
        """Register callback for order book deltas."""
        self._orderbook_delta_callbacks.append(callback)

    def on_trade(self, callback: TradeCallback) -> None:
        """Register callback for trade events."""
        self._trade_callbacks.append(callback)

    def on_ticker(self, callback: TickerCallback) -> None:
        """Register callback for ticker updates."""
        self._ticker_callbacks.append(callback)

    def on_fill(self, callback: FillCallback) -> None:
        """Register callback for fill events (authenticated only)."""
        self._fill_callbacks.append(callback)

    def on_order(self, callback: OrderCallback) -> None:
        """Register callback for order updates (authenticated only)."""
        self._order_callbacks.append(callback)

    def on_error(self, callback: ErrorCallback) -> None:
        """Register callback for error events."""
        self._error_callbacks.append(callback)

    # Context manager support
    async def __aenter__(self) -> "KalshiWebSocketClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()
