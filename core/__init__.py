"""Core trading infrastructure.

This package provides the foundational interfaces and implementations for:
- Exchange connectivity (I_ExchangeClient)
- Market data (I_Market)
- Order management (I_OrderManager)

Usage:
    from core.exchange_client import KalshiExchangeClient
    from core.order_manager import KalshiOrderManager, OrderRequest, Side, Action
    from core.market import KalshiMarket
"""

from .exchange_client import (
    I_ExchangeClient,
    KalshiExchangeClient,
    KalshiAuth,
    ExchangeStatus,
    KalshiBalance,
    KalshiPosition,
    KalshiMarketData,
)

from .order_manager import (
    I_OrderManager,
    KalshiOrderManager,
    OrderRequest,
    Side,
    Action,
    OrderStatus,
    OrderType,
    Fill,
    TrackedOrder,
)

from .market import (
    I_Market,
    KalshiMarket,
    OrderBook,
)

from .recorder import (
    # Orderbook
    OrderbookSnapshot,
    # Single-ticker
    MarketFrame,
    MarketSeriesMetadata,
    MarketSeries,
    # Paired-ticker
    PairMarketFrame,
    PairMarketSeriesMetadata,
    PairMarketSeries,
    # Kalshi recorder
    KalshiMarketRecorder,
)

from .nba_utils import (
    GameProgress,
    GamePeriod,
    get_todays_games,
    get_game_progress,
    find_game,
    should_include_1h_markets,
)

from .regime_detector import (
    RegimeDetector,
    RegimeState,
)

__all__ = [
    # Exchange
    "I_ExchangeClient",
    "KalshiExchangeClient",
    "KalshiAuth",
    "ExchangeStatus",
    "KalshiBalance",
    "KalshiPosition",
    "KalshiMarketData",
    # Order Manager
    "I_OrderManager",
    "KalshiOrderManager",
    "OrderRequest",
    "Side",
    "Action",
    "OrderStatus",
    "OrderType",
    "Fill",
    "TrackedOrder",
    # Market
    "I_Market",
    "KalshiMarket",
    "OrderBook",
    # Recorder
    "OrderbookSnapshot",
    "MarketFrame",
    "MarketSeriesMetadata",
    "MarketSeries",
    "PairMarketFrame",
    "PairMarketSeriesMetadata",
    "PairMarketSeries",
    "KalshiMarketRecorder",
    # NBA utils
    "GameProgress",
    "GamePeriod",
    "get_todays_games",
    "get_game_progress",
    "find_game",
    "should_include_1h_markets",
    # Regime detection
    "RegimeDetector",
    "RegimeState",
]
