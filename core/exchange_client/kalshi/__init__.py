"""Kalshi exchange client module.

Provides Kalshi-specific implementations of exchange connectivity:
- KalshiAuth: API authentication
- KalshiExchangeClient: REST API client
- KalshiWebSocket: Async WebSocket client
- KalshiWebSocketSync: Sync WebSocket wrapper
"""

from .kalshi_auth import KalshiAuth
from .kalshi_client import KalshiExchangeClient
from .kalshi_types import (
    KalshiBalance,
    KalshiPosition,
    KalshiMarketData,
    KalshiOrderResponse,
)
from .kalshi_exceptions import (
    KalshiError,
    KalshiAuthError,
    KalshiNotFoundError,
    KalshiRateLimitError,
    KalshiBadRequestError,
    KalshiTimeoutError,
    KalshiMaxRetriesError,
    WebSocketError,
)
from .kalshi_websocket import (
    KalshiWebSocket,
    WebSocketConfig,
    Channel,
    ConnectionState,
    DEMO_WS_URL,
)
from .kalshi_websocket_sync import KalshiWebSocketSync, KalshiFill

__all__ = [
    # Auth
    "KalshiAuth",
    # REST Client
    "KalshiExchangeClient",
    # Types
    "KalshiBalance",
    "KalshiPosition",
    "KalshiMarketData",
    "KalshiOrderResponse",
    # Exceptions
    "KalshiError",
    "KalshiAuthError",
    "KalshiNotFoundError",
    "KalshiRateLimitError",
    "KalshiBadRequestError",
    "KalshiTimeoutError",
    "KalshiMaxRetriesError",
    "WebSocketError",
    # WebSocket
    "KalshiWebSocket",
    "KalshiWebSocketSync",
    "WebSocketConfig",
    "Channel",
    "ConnectionState",
    "KalshiFill",
    "DEMO_WS_URL",
]
