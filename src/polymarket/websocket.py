"""WebSocket client for Polymarket real-time market data.

Provides:
- Order book streaming
- Trade feed subscription
- Automatic reconnection
- Message parsing and validation
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from .exceptions import PolymarketWebSocketError
from .orderbook import OrderBookManager


logger = logging.getLogger(__name__)


# WebSocket endpoints
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"


class MessageType(str, Enum):
    """WebSocket message types."""

    BOOK = "book"
    PRICE_CHANGE = "price_change"
    TRADE = "last_trade_price"
    TICK_SIZE = "tick_size_change"


@dataclass
class TradeMessage:
    """Parsed trade message."""

    asset_id: str
    price: float
    size: float
    side: str
    timestamp: datetime


class PolymarketWebSocket:
    """WebSocket client for Polymarket market data.

    Features:
    - Subscribe to multiple assets
    - Automatic reconnection with exponential backoff
    - Order book state management via OrderBookManager
    - Trade callbacks

    Example:
        >>> ws = PolymarketWebSocket()
        >>> ws.on_trade(lambda trade: print(f"Trade: {trade}"))
        >>> await ws.connect()
        >>> await ws.subscribe(["asset123", "asset456"])
        >>> # ... later
        >>> await ws.close()
    """

    def __init__(
        self,
        orderbook_manager: Optional[OrderBookManager] = None,
        url: str = WS_URL,
        reconnect: bool = True,
        max_reconnect_attempts: int = 10,
        reconnect_delay: float = 1.0,
    ) -> None:
        """Initialize WebSocket client.

        Args:
            orderbook_manager: Manager for order book state
            url: WebSocket URL
            reconnect: Whether to auto-reconnect on disconnect
            max_reconnect_attempts: Maximum reconnection attempts
            reconnect_delay: Initial delay between reconnect attempts
        """
        self._url = url
        self._reconnect = reconnect
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_delay = reconnect_delay

        self._orderbook_manager = orderbook_manager or OrderBookManager()
        self._websocket = None
        self._connected = False
        self._running = False
        self._subscribed_assets: Set[str] = set()

        self._trade_callbacks: List[Callable[[TradeMessage], None]] = []
        self._connect_callbacks: List[Callable[[], None]] = []
        self._disconnect_callbacks: List[Callable[[], None]] = []

        self._reconnect_count = 0
        self._last_message_time: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected

    @property
    def orderbook_manager(self) -> OrderBookManager:
        """Get the order book manager."""
        return self._orderbook_manager

    def on_trade(self, callback: Callable[[TradeMessage], None]) -> None:
        """Register callback for trade messages.

        Args:
            callback: Function called with TradeMessage on each trade
        """
        self._trade_callbacks.append(callback)

    def on_connect(self, callback: Callable[[], None]) -> None:
        """Register callback for connection events."""
        self._connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register callback for disconnection events."""
        self._disconnect_callbacks.append(callback)

    async def connect(self) -> None:
        """Connect to WebSocket server."""
        try:
            import websockets
        except ImportError:
            raise PolymarketWebSocketError(
                "websockets package required. Install with: pip install websockets"
            )

        if self._connected:
            return

        self._running = True

        try:
            self._websocket = await websockets.connect(
                self._url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
            self._connected = True
            self._reconnect_count = 0

            logger.info("WebSocket connected to %s", self._url)

            # Notify callbacks
            for callback in self._connect_callbacks:
                try:
                    callback()
                except Exception as e:
                    logger.error("Connect callback error: %s", e)

            # Resubscribe to assets
            if self._subscribed_assets:
                await self._send_subscribe(list(self._subscribed_assets))

        except Exception as e:
            self._connected = False
            raise PolymarketWebSocketError(f"Failed to connect: {e}")

    async def close(self) -> None:
        """Close WebSocket connection."""
        self._running = False
        self._connected = False

        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

        logger.info("WebSocket closed")

    async def subscribe(self, asset_ids: List[str]) -> None:
        """Subscribe to order book updates for assets.

        Args:
            asset_ids: List of asset/token IDs to subscribe to
        """
        with self._lock:
            self._subscribed_assets.update(asset_ids)

        if self._connected:
            await self._send_subscribe(asset_ids)

    async def unsubscribe(self, asset_ids: List[str]) -> None:
        """Unsubscribe from assets.

        Args:
            asset_ids: List of asset IDs to unsubscribe from
        """
        with self._lock:
            self._subscribed_assets.difference_update(asset_ids)

        if self._connected and self._websocket:
            message = {
                "type": "unsubscribe",
                "assets_ids": asset_ids,
            }
            await self._websocket.send(json.dumps(message))

    async def _send_subscribe(self, asset_ids: List[str]) -> None:
        """Send subscription message."""
        if not self._websocket:
            return

        message = {
            "assets_ids": asset_ids,
            "type": "market",
        }

        try:
            await self._websocket.send(json.dumps(message))
            logger.debug("Subscribed to %d assets", len(asset_ids))
        except Exception as e:
            logger.error("Failed to send subscribe: %s", e)

    async def run(self) -> None:
        """Run the WebSocket message loop.

        This is the main loop that processes incoming messages.
        Call this in a task to keep the connection alive.
        """
        while self._running:
            try:
                if not self._connected:
                    await self.connect()

                await self._receive_loop()

            except Exception as e:
                logger.error("WebSocket error: %s", e)
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

    async def _receive_loop(self) -> None:
        """Process incoming WebSocket messages."""
        if not self._websocket:
            return

        async for message in self._websocket:
            self._last_message_time = time.time()

            try:
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON message: %s", message[:100])
            except Exception as e:
                logger.error("Message handling error: %s", e)

    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """Handle a parsed WebSocket message."""
        # Handle different message formats
        if isinstance(data, list):
            # Batch of messages
            for item in data:
                await self._handle_single_message(item)
        else:
            await self._handle_single_message(data)

    async def _handle_single_message(self, msg: Dict[str, Any]) -> None:
        """Handle a single message."""
        event_type = msg.get("event_type", "")
        asset_id = msg.get("asset_id", "")

        if event_type == MessageType.BOOK:
            self._handle_book_message(msg)

        elif event_type == MessageType.PRICE_CHANGE:
            self._handle_price_change(msg)

        elif event_type == MessageType.TRADE:
            self._handle_trade(msg)

        elif event_type == MessageType.TICK_SIZE:
            # Tick size change - just log
            logger.debug("Tick size change: %s", asset_id)

        elif "bids" in msg or "asks" in msg:
            # Direct book snapshot
            self._handle_book_snapshot(msg)

        else:
            logger.debug("Unknown message type: %s", event_type or msg.get("type"))

    def _handle_book_message(self, msg: Dict[str, Any]) -> None:
        """Handle order book update message."""
        asset_id = msg.get("asset_id", "")
        market = msg.get("market", "")

        if "bids" in msg and "asks" in msg:
            # Full snapshot
            self._orderbook_manager.apply_snapshot(
                asset_id=asset_id,
                market=market,
                bids=msg.get("bids", []),
                asks=msg.get("asks", []),
                sequence=msg.get("timestamp", 0),
            )
        else:
            # Delta update
            changes = msg.get("changes", [])
            self._orderbook_manager.apply_deltas(
                asset_id=asset_id,
                changes=changes,
                sequence=msg.get("timestamp", 0),
            )

    def _handle_book_snapshot(self, msg: Dict[str, Any]) -> None:
        """Handle direct book snapshot format."""
        asset_id = msg.get("asset_id", msg.get("token_id", ""))
        market = msg.get("market", msg.get("condition_id", ""))

        bids = msg.get("bids", [])
        asks = msg.get("asks", [])

        if asset_id:
            self._orderbook_manager.apply_snapshot(
                asset_id=asset_id,
                market=market,
                bids=bids,
                asks=asks,
            )

    def _handle_price_change(self, msg: Dict[str, Any]) -> None:
        """Handle price change message."""
        asset_id = msg.get("asset_id", "")
        changes = msg.get("changes", [])

        for change in changes:
            side = change.get("side", "").lower()
            price = float(change.get("price", 0))
            size = float(change.get("size", 0))

            self._orderbook_manager.apply_delta(
                asset_id=asset_id,
                side=side,
                price=price,
                size=size,
            )

    def _handle_trade(self, msg: Dict[str, Any]) -> None:
        """Handle trade message."""
        try:
            trade = TradeMessage(
                asset_id=msg.get("asset_id", ""),
                price=float(msg.get("price", 0)),
                size=float(msg.get("size", 0)),
                side=msg.get("side", ""),
                timestamp=datetime.now(),
            )

            for callback in self._trade_callbacks:
                try:
                    callback(trade)
                except Exception as e:
                    logger.error("Trade callback error: %s", e)

        except Exception as e:
            logger.error("Trade parsing error: %s", e)

    async def _handle_reconnect(self) -> None:
        """Handle reconnection with exponential backoff."""
        self._reconnect_count += 1

        if self._reconnect_count > self._max_reconnect_attempts:
            logger.error("Max reconnection attempts exceeded")
            self._running = False
            return

        delay = self._reconnect_delay * (2 ** (self._reconnect_count - 1))
        delay = min(delay, 60.0)  # Cap at 60 seconds

        logger.info(
            "Reconnecting in %.1fs (attempt %d/%d)",
            delay,
            self._reconnect_count,
            self._max_reconnect_attempts,
        )

        await asyncio.sleep(delay)

        try:
            await self.connect()
        except Exception as e:
            logger.error("Reconnection failed: %s", e)


class PolymarketWebSocketSync:
    """Synchronous wrapper for PolymarketWebSocket.

    Runs the async WebSocket client in a background thread.

    Example:
        >>> ws = PolymarketWebSocketSync()
        >>> ws.start()
        >>> ws.subscribe(["asset123"])
        >>> book = ws.get_orderbook("asset123")
        >>> ws.stop()
    """

    def __init__(
        self,
        orderbook_manager: Optional[OrderBookManager] = None,
        **kwargs,
    ) -> None:
        """Initialize synchronous WebSocket client."""
        self._orderbook_manager = orderbook_manager or OrderBookManager()
        self._ws = PolymarketWebSocket(
            orderbook_manager=self._orderbook_manager,
            **kwargs,
        )
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False

    def start(self) -> "PolymarketWebSocketSync":
        """Start WebSocket in background thread."""
        if self._running:
            return self

        self._running = True
        self._thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
            name="PolymarketWebSocket",
        )
        self._thread.start()

        # Wait for connection
        for _ in range(50):  # 5 seconds timeout
            if self._ws.is_connected:
                break
            time.sleep(0.1)

        return self

    def stop(self) -> None:
        """Stop WebSocket client."""
        self._running = False

        if self._loop:
            asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)

        if self._thread:
            self._thread.join(timeout=5.0)

    def subscribe(self, asset_ids: List[str]) -> None:
        """Subscribe to assets."""
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._ws.subscribe(asset_ids),
                self._loop,
            )

    def get_orderbook(self, asset_id: str):
        """Get current order book."""
        return self._orderbook_manager.get_orderbook(asset_id)

    def get_best_bid_ask(self, asset_id: str):
        """Get best bid/ask."""
        return self._orderbook_manager.get_best_bid_ask(asset_id)

    @property
    def is_connected(self) -> bool:
        """Check if connected."""
        return self._ws.is_connected

    @property
    def orderbook_manager(self) -> OrderBookManager:
        """Get order book manager."""
        return self._orderbook_manager

    def on_trade(self, callback: Callable[[TradeMessage], None]) -> None:
        """Register trade callback."""
        self._ws.on_trade(callback)

    def _run_in_thread(self) -> None:
        """Run async client in thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._ws.run())
        except Exception as e:
            logger.error("WebSocket thread error: %s", e)
        finally:
            self._loop.close()
