"""Exchange client module.

Provides abstract interface and concrete implementations for exchange connectivity.
"""

from .i_exchange_client import I_ExchangeClient
from .exchange_client_types import (
    ExchangeStatus,
    ExchangeName,
    ExchangeConfig,
    ExchangeError,
    RateLimitInfo,
)

# Kalshi-specific imports from submodule
from .kalshi import (
    # Auth
    KalshiAuth,
    # Client
    KalshiExchangeClient,
    # Types
    KalshiBalance,
    KalshiPosition,
    KalshiMarketData,
    # Exceptions
    KalshiError,
    KalshiAuthError,
    KalshiNotFoundError,
    KalshiRateLimitError,
    KalshiBadRequestError,
    KalshiTimeoutError,
    KalshiMaxRetriesError,
    WebSocketError,
    # WebSocket
    KalshiWebSocket,
    KalshiWebSocketSync,
    WebSocketConfig,
    Channel,
    ConnectionState,
    KalshiFill,
    DEMO_WS_URL,
)

# Polymarket-specific imports from submodule (optional — requires polymarket deps)
try:
    from .polymarket import (
        # Auth
        PolymarketAuth,
        # Client
        PolymarketExchangeClient,
        # Types
        PolymarketBalance,
        PolymarketPosition,
        PolymarketMarketData,
        PolymarketOrderResponse,
        # Exceptions
        PolymarketError,
        PolymarketAuthError,
        PolymarketNotFoundError,
        PolymarketRateLimitError,
        PolymarketBadRequestError,
        PolymarketConnectionError,
        PolymarketTimeoutError,
        PolymarketMaxRetriesError,
    )
except ImportError:
    pass  # polymarket dependencies not installed

__all__ = [
    # Interface
    "I_ExchangeClient",
    # Shared Types
    "ExchangeStatus",
    "ExchangeName",
    "ExchangeConfig",
    "ExchangeError",
    "RateLimitInfo",
    # Kalshi
    "KalshiAuth",
    "KalshiExchangeClient",
    "KalshiBalance",
    "KalshiPosition",
    "KalshiMarketData",
    "KalshiError",
    "KalshiAuthError",
    "KalshiNotFoundError",
    "KalshiRateLimitError",
    "KalshiBadRequestError",
    "KalshiTimeoutError",
    "KalshiMaxRetriesError",
    "WebSocketError",
    "KalshiWebSocket",
    "KalshiWebSocketSync",
    "WebSocketConfig",
    "Channel",
    "ConnectionState",
    "KalshiFill",
    "DEMO_WS_URL",
    # Polymarket
    "PolymarketAuth",
    "PolymarketExchangeClient",
    "PolymarketBalance",
    "PolymarketPosition",
    "PolymarketMarketData",
    "PolymarketOrderResponse",
    "PolymarketError",
    "PolymarketAuthError",
    "PolymarketNotFoundError",
    "PolymarketRateLimitError",
    "PolymarketBadRequestError",
    "PolymarketConnectionError",
    "PolymarketTimeoutError",
    "PolymarketMaxRetriesError",
]
