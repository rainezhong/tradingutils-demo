"""Kalshi API client module.

This module provides a production-ready Kalshi API client with:
- Async HTTP client with retry logic and rate limiting
- WebSocket client for real-time market data
- Order book management with snapshot + delta support
- Pydantic models for type safety

Example usage:

    # REST API
    >>> from src.kalshi import KalshiClient
    >>> client = KalshiClient.from_env()
    >>> async with client:
    ...     market = await client.get_market_data("TICKER")
    ...     order_id = await client.place_order_async("TICKER", "buy", 0.45, 10)

    # WebSocket
    >>> from src.kalshi import KalshiWebSocket, KalshiAuth, OrderBookManager
    >>> auth = KalshiAuth.from_env()
    >>> manager = OrderBookManager()
    >>> ws = KalshiWebSocket(auth, orderbook_manager=manager)
    >>> async with ws:
    ...     await ws.subscribe("orderbook_delta", "TICKER")
    ...     await asyncio.sleep(60)
    ...     book = manager.get_orderbook("TICKER")
"""

from .auth import KalshiAuth, generate_auth_headers, generate_signature
from .client import KalshiClient
from .mock_client import MockKalshiClient, MockOrder
from .exceptions import (
    AuthenticationError,
    InsufficientFundsError,
    KalshiAPIError,
    MarketNotFoundError,
    OrderBookError,
    OrderError,
    RateLimitError,
    WebSocketError,
)
from .models import (
    ExchangeStatus,
    KalshiBalance,
    KalshiFill,
    KalshiMarket,
    KalshiOrder,
    KalshiOrderBook,
    KalshiOrderRequest,
    KalshiPosition,
    OrderBookLevel,
    OrderSide,
    OrderStatus,
    OrderType,
)
from .orderbook import OrderBookLevel as OBLevel
from .orderbook import OrderBookManager, OrderBookState
from .websocket import (
    Channel,
    ConnectionState,
    KalshiWebSocket,
    WebSocketConfig,
)

__all__ = [
    # Auth
    "KalshiAuth",
    "generate_auth_headers",
    "generate_signature",
    # Client
    "KalshiClient",
    # Mock Client
    "MockKalshiClient",
    "MockOrder",
    # WebSocket
    "KalshiWebSocket",
    "WebSocketConfig",
    "Channel",
    "ConnectionState",
    # Order Book
    "OrderBookManager",
    "OrderBookState",
    "OBLevel",
    # Models
    "KalshiBalance",
    "KalshiMarket",
    "KalshiOrder",
    "KalshiOrderRequest",
    "KalshiFill",
    "KalshiPosition",
    "KalshiOrderBook",
    "OrderBookLevel",
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "ExchangeStatus",
    # Exceptions
    "KalshiAPIError",
    "AuthenticationError",
    "RateLimitError",
    "OrderError",
    "MarketNotFoundError",
    "InsufficientFundsError",
    "WebSocketError",
    "OrderBookError",
]
