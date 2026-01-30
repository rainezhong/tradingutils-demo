"""Core module for trading utilities.

This module provides the foundational components that other modules import and use:
- Configuration management
- Data models with validation
- Database operations
- API client with rate limiting
- WebSocket client for real-time data
- Order book state management
- Exchange-agnostic trading interface
- Shared utilities
"""

from .api_client import KalshiClient, RateLimiter
from .rate_limiter import Priority, SharedRateLimiter, get_shared_rate_limiter
from .trading_state import TradingState, get_trading_state
from .config import CapitalConfig, Config, RateLimitConfig, RiskConfig, get_config, set_config
from .database import MarketDatabase, create_database
from .exceptions import (
    AuthenticationError,
    InsufficientFundsError,
    KalshiError,
    MarketNotFoundError,
    OrderBookError,
    OrderError,
    RateLimitError,
    WebSocketError,
)
from .exchange import ExchangeClient, Order, OrderBook, TradableMarket
from .interfaces import AbstractBot, APIClient, DataProvider, OrderManager, SpreadQuote
from .models import (
    Fill,
    Market,
    MarketState,
    Position,
    Quote,
    Snapshot,
    SummaryStats,
    ValidationError,
)
from .orderbook_manager import OrderBookLevel, OrderBookManager, OrderBookState
from .utils import (
    calculate_spread,
    ensure_directory,
    get_db_connection,
    parse_iso_timestamp,
    setup_logger,
    utc_now,
    utc_now_iso,
)

# Conditionally import WebSocket client (requires websockets package)
try:
    from .websocket_client import (
        Channel,
        ConnectionState,
        KalshiWebSocketClient,
        WebSocketConfig,
    )
    _WEBSOCKET_AVAILABLE = True
except ImportError:
    _WEBSOCKET_AVAILABLE = False

__all__ = [
    # Config
    "Config",
    "RateLimitConfig",
    "RiskConfig",
    "CapitalConfig",
    "get_config",
    "set_config",
    # Exceptions
    "KalshiError",
    "AuthenticationError",
    "RateLimitError",
    "WebSocketError",
    "OrderBookError",
    "MarketNotFoundError",
    "InsufficientFundsError",
    "OrderError",
    # Interfaces
    "AbstractBot",
    "APIClient",
    "DataProvider",
    "OrderManager",
    "SpreadQuote",
    # Exchange Interface
    "ExchangeClient",
    "TradableMarket",
    "Order",
    "OrderBook",
    # Models
    "Fill",
    "Market",
    "MarketState",
    "Position",
    "Quote",
    "Snapshot",
    "SummaryStats",
    "ValidationError",
    # Order Book Manager
    "OrderBookLevel",
    "OrderBookState",
    "OrderBookManager",
    # Database
    "MarketDatabase",
    "create_database",
    # API Client
    "KalshiClient",
    "RateLimiter",
    # Shared Rate Limiter
    "Priority",
    "SharedRateLimiter",
    "get_shared_rate_limiter",
    # Trading State
    "TradingState",
    "get_trading_state",
    # WebSocket Client (conditionally available)
    "KalshiWebSocketClient",
    "WebSocketConfig",
    "Channel",
    "ConnectionState",
    # Utils
    "setup_logger",
    "utc_now",
    "utc_now_iso",
    "parse_iso_timestamp",
    "ensure_directory",
    "get_db_connection",
    "calculate_spread",
]
