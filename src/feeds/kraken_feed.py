"""Kraken WebSocket price feed for real-time BTC/USD trades.

Streams trades from Kraken's v2 WebSocket API and maintains a rolling
60-second average that closely tracks the BRTI settlement price used
by Kalshi KXBTC15M markets.

Adapted from scripts/btc_latency_probe.py KrakenTradeStream.
"""

import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"
RECONNECT_DELAY_SEC = 2.0
WINDOW_SEC = 60.0


@dataclass
class KrakenPriceUpdate:
    """A price update from Kraken WebSocket trade stream.

    Attributes:
        symbol: Trading pair symbol (Binance-compatible, e.g. "btcusdt")
        price: Latest trade price
        timestamp: Unix timestamp of the update
        avg_60s: Rolling 60-second average price (BRTI proxy)
        trade_count_60s: Number of trades in the 60-second window
        age_ms: Age of this update in milliseconds
    """

    symbol: str
    price: float
    timestamp: float
    avg_60s: float
    trade_count_60s: int
    age_ms: float


# Only BTC/USD is supported (maps to Kraken's BTC/USD pair)
SYMBOL_MAP = {
    "BTCUSDT": "BTC/USD",
    "BTC-USD": "BTC/USD",
    "BTC": "BTC/USD",
}


class KrakenPriceFeed:
    """Kraken WebSocket price feed streaming BTC/USD trades.

    Maintains a rolling 60-second window of trade prices and computes
    an average that tracks BRTI within ~$10 median deviation.

    Drop-in replacement for CoinbasePriceFeed with the same interface.

    Example:
        >>> feed = KrakenPriceFeed(symbols=["BTCUSDT"])
        >>> feed.on_price_update(lambda u: print(f"{u.symbol}: ${u.price:.2f} avg60=${u.avg_60s:.2f}"))
        >>> feed.start()
        >>> # ... later
        >>> print(feed.get_avg_60s())
        >>> feed.stop()
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        poll_interval_sec: Optional[float] = None,
    ) -> None:
        """Initialize Kraken price feed.

        Args:
            symbols: List of symbols to track. Only BTC symbols are supported;
                     non-BTC symbols are silently ignored.
            poll_interval_sec: Ignored (kept for interface compatibility with CoinbasePriceFeed).
        """
        # Check if any requested symbol maps to BTC/USD
        self._has_btc = False
        self._btc_symbol = "BTCUSDT"  # default output symbol
        for s in symbols or []:
            s_upper = s.upper()
            if s_upper in SYMBOL_MAP:
                self._has_btc = True
                self._btc_symbol = s_upper
                break

        # Rolling 60-second trade window: (local_ts, price)
        self._trades: deque = deque()
        self._lock = threading.Lock()
        self._latest_price: Optional[float] = None
        self._latest_avg_60s: float = 0.0

        # State
        self._running = False
        self._connected = False

        # Callbacks
        self._price_callbacks: List[Callable[[KrakenPriceUpdate], None]] = []
        self._connect_callbacks: List[Callable[[], None]] = []
        self._disconnect_callbacks: List[Callable[[], None]] = []

        # Threading
        self._thread: Optional[threading.Thread] = None

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._running and self._connected

    @property
    def symbols(self) -> List[str]:
        """Get tracked symbols (Binance format)."""
        if self._has_btc:
            return [self._btc_symbol]
        return []

    def on_price_update(self, callback: Callable[[KrakenPriceUpdate], None]) -> None:
        """Register callback for price updates."""
        self._price_callbacks.append(callback)

    def on_connect(self, callback: Callable[[], None]) -> None:
        """Register callback for connection events."""
        self._connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register callback for disconnection events."""
        self._disconnect_callbacks.append(callback)

    def get_price(self, symbol: str = None) -> Optional[KrakenPriceUpdate]:
        """Get the latest price update.

        Args:
            symbol: Ignored (only BTC/USD is tracked).

        Returns:
            Latest KrakenPriceUpdate or None
        """
        with self._lock:
            if self._latest_price is None:
                return None
            now = time.time()
            avg, count = self._compute_avg(now)
            return KrakenPriceUpdate(
                symbol=self._btc_symbol.lower(),
                price=self._latest_price,
                timestamp=now,
                avg_60s=avg,
                trade_count_60s=count,
                age_ms=0.0,
            )

    def get_avg_60s(self) -> float:
        """Get the current 60-second rolling average price."""
        with self._lock:
            return self._latest_avg_60s

    def get_all_prices(self) -> Dict[str, KrakenPriceUpdate]:
        """Get all latest prices."""
        update = self.get_price()
        if update:
            return {self._btc_symbol: update}
        return {}

    def start(self) -> "KrakenPriceFeed":
        """Start the WebSocket feed.

        Returns:
            Self for chaining
        """
        if self._running:
            return self

        if not self._has_btc:
            logger.warning("KrakenPriceFeed: no BTC symbol requested, not starting")
            return self

        self._running = True

        self._thread = threading.Thread(
            target=self._run_ws_thread,
            daemon=True,
            name="KrakenPriceFeed",
        )
        self._thread.start()

        # Wait for connection (up to 5 seconds)
        for _ in range(50):
            if self._connected:
                break
            time.sleep(0.1)

        logger.info("KrakenPriceFeed started (symbol: %s)", self._btc_symbol)
        return self

    def stop(self) -> None:
        """Stop the WebSocket feed."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=3.0)

        self._connected = False

        for callback in self._disconnect_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Disconnect callback error: %s", e)

        logger.info("KrakenPriceFeed stopped")

    def _run_ws_thread(self) -> None:
        """Background thread running the async WebSocket loop."""
        asyncio.run(self._ws_loop())

    async def _ws_loop(self) -> None:
        """Main WebSocket loop with auto-reconnect."""
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required: pip install websockets")
            return

        while self._running:
            try:
                async with websockets.connect(
                    KRAKEN_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    # Subscribe to BTC/USD trades
                    sub_msg = {
                        "method": "subscribe",
                        "params": {
                            "channel": "trade",
                            "symbol": ["BTC/USD"],
                        },
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("Kraken WS: subscribing to BTC/USD trades...")

                    async for raw in ws:
                        if not self._running:
                            break

                        now = time.time()
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # Handle subscription confirmations
                        if msg.get("method") in ("subscribe", "pong"):
                            if not self._connected:
                                self._connected = True
                                logger.info("Kraken WS: connected and subscribed")
                                for cb in self._connect_callbacks:
                                    try:
                                        cb()
                                    except Exception as e:
                                        logger.error("Connect callback error: %s", e)
                            continue

                        # Handle heartbeat
                        if msg.get("channel") == "heartbeat":
                            continue

                        # Handle trade data
                        if msg.get("channel") == "trade":
                            for trade in msg.get("data", []):
                                price = float(trade["price"])

                                with self._lock:
                                    self._latest_price = price
                                    self._trades.append((now, price))
                                    self._trim_window(now)
                                    avg, count = self._compute_avg(now)
                                    self._latest_avg_60s = avg

                                update = KrakenPriceUpdate(
                                    symbol=self._btc_symbol.lower(),
                                    price=price,
                                    timestamp=now,
                                    avg_60s=avg,
                                    trade_count_60s=count,
                                    age_ms=0.0,
                                )

                                for callback in self._price_callbacks:
                                    try:
                                        callback(update)
                                    except Exception as e:
                                        logger.error("Price callback error: %s", e)

            except Exception as e:
                logger.error("Kraken WS error: %s", e)
                self._connected = False
                for cb in self._disconnect_callbacks:
                    try:
                        cb()
                    except Exception as exc:
                        logger.error("Disconnect callback error: %s", exc)

                if self._running:
                    logger.info("Reconnecting in %.0fs...", RECONNECT_DELAY_SEC)
                    await asyncio.sleep(RECONNECT_DELAY_SEC)

    def _trim_window(self, now: float) -> None:
        """Remove trades older than 60 seconds. Must hold self._lock."""
        cutoff = now - WINDOW_SEC
        while self._trades and self._trades[0][0] < cutoff:
            self._trades.popleft()

    def _compute_avg(self, now: float) -> Tuple[float, int]:
        """Compute average price over 60-second window. Must hold self._lock.

        Returns:
            (average_price, trade_count)
        """
        self._trim_window(now)
        if not self._trades:
            return (0.0, 0)
        prices = [t[1] for t in self._trades]
        return (sum(prices) / len(prices), len(prices))
