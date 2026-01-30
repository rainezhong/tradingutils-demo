"""Polymarket CLOB API client module.

This module provides a production-ready client for the Polymarket CLOB API
that implements the APIClient interface from src/core/interfaces.py.

Example:
    >>> from src.polymarket import PolymarketClient
    >>>
    >>> # Initialize with private key from environment
    >>> client = PolymarketClient()
    >>> client.connect()
    >>>
    >>> # Get market data
    >>> market = client.get_market_data("token123")
    >>> print(f"Bid: {market.bid}, Ask: {market.ask}")
    >>>
    >>> # Place order
    >>> order_id = client.place_order("token123", "BID", 0.55, 100)
    >>> status = client.get_order_status(order_id)
    >>>
    >>> client.disconnect()

Environment Variables:
    POLYMARKET_PRIVATE_KEY: Wallet private key for signing
    POLYGON_RPC_URL: Polygon RPC endpoint (optional)
"""

from .blockchain import PolygonClient
from .client import PolymarketClient
from .exceptions import (
    PolymarketAPIError,
    PolymarketAuthError,
    PolymarketBlockchainError,
    PolymarketError,
    PolymarketInsufficientFundsError,
    PolymarketOrderError,
    PolymarketRateLimitError,
    PolymarketWebSocketError,
)
from .models import (
    OrderBookLevel,
    OrderSide,
    OrderStatus,
    OrderType,
    PolymarketMarket,
    PolymarketOrder,
    PolymarketOrderBook,
    PolymarketTrade,
)
from .orderbook import OrderBookManager
from .wallet import PolymarketCredentials, PolymarketWallet
from .websocket import (
    PolymarketWebSocket,
    PolymarketWebSocketSync,
    TradeMessage,
)

__all__ = [
    # Main client
    "PolymarketClient",
    # Wallet and auth
    "PolymarketWallet",
    "PolymarketCredentials",
    # Blockchain
    "PolygonClient",
    # WebSocket
    "PolymarketWebSocket",
    "PolymarketWebSocketSync",
    "TradeMessage",
    # Order book
    "OrderBookManager",
    # Models
    "PolymarketMarket",
    "PolymarketOrder",
    "PolymarketOrderBook",
    "PolymarketTrade",
    "OrderBookLevel",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    # Exceptions
    "PolymarketError",
    "PolymarketAPIError",
    "PolymarketAuthError",
    "PolymarketRateLimitError",
    "PolymarketOrderError",
    "PolymarketWebSocketError",
    "PolymarketBlockchainError",
    "PolymarketInsufficientFundsError",
]
