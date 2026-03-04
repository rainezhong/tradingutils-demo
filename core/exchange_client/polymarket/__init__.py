"""Polymarket exchange client module.

Provides Polymarket-specific implementations of exchange connectivity:
- PolymarketAuth: Wallet-based authentication
- PolymarketExchangeClient: CLOB + Gamma API client
"""

from .polymarket_auth import PolymarketAuth
from .polymarket_client import PolymarketExchangeClient
from .polymarket_types import (
    PolymarketBalance,
    PolymarketPosition,
    PolymarketMarketData,
    PolymarketOrderResponse,
)
from .polymarket_exceptions import (
    PolymarketError,
    PolymarketAuthError,
    PolymarketNotFoundError,
    PolymarketRateLimitError,
    PolymarketBadRequestError,
    PolymarketConnectionError,
    PolymarketTimeoutError,
    PolymarketMaxRetriesError,
)

__all__ = [
    # Auth
    "PolymarketAuth",
    # REST Client
    "PolymarketExchangeClient",
    # Types
    "PolymarketBalance",
    "PolymarketPosition",
    "PolymarketMarketData",
    "PolymarketOrderResponse",
    # Exceptions
    "PolymarketError",
    "PolymarketAuthError",
    "PolymarketNotFoundError",
    "PolymarketRateLimitError",
    "PolymarketBadRequestError",
    "PolymarketConnectionError",
    "PolymarketTimeoutError",
    "PolymarketMaxRetriesError",
]
