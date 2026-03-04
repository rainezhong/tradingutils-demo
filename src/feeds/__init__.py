"""Data feeds for real-time market data.

This module provides reusable WebSocket feed classes for streaming live market data
from cryptocurrency exchanges. All feeds follow a consistent callback-based architecture
with automatic reconnection and thread-safe operations.

Available feeds:
    - KrakenPriceFeed: BTC/USD trades with 60-second rolling average (BRTI proxy)
    - BinanceTradeStream: BTC/USDT individual trades (~8/sec, lowest latency)
    - CoinbaseTradeStream: BTC-USD individual trades (~1.7/sec)
"""

try:
    from .binance_ws import BinanceWebSocket, PriceUpdate
except ImportError:
    pass

try:
    from .coinbase_feed import CoinbasePriceFeed, CoinbasePriceUpdate
except ImportError:
    pass

from .kraken_feed import KrakenPriceFeed, KrakenPriceUpdate
from .binance_trade_stream import BinanceTradeStream
from .binance_trade_stream import Trade as BinanceTrade
from .coinbase_trade_stream import CoinbaseTradeStream
from .coinbase_trade_stream import Trade as CoinbaseTrade

__all__ = [
    "KrakenPriceFeed",
    "KrakenPriceUpdate",
    "BinanceTradeStream",
    "BinanceTrade",
    "CoinbaseTradeStream",
    "CoinbaseTrade",
]
