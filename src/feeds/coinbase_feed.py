"""Coinbase price feed for real-time crypto prices.

Uses Coinbase's public REST API which is accessible from the US.
Falls back to polling since WebSocket requires authentication.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


COINBASE_API_URL = "https://api.coinbase.com/v2"


@dataclass
class CoinbasePriceUpdate:
    """A price update from Coinbase.

    Attributes:
        symbol: Trading pair symbol (e.g., "BTC-USD")
        price: Current price in USD
        timestamp: Unix timestamp of the update
    """

    symbol: str
    price: float
    timestamp: float

    @property
    def age_ms(self) -> float:
        """Age of this update in milliseconds."""
        return (time.time() - self.timestamp) * 1000


# Map Binance-style symbols to Coinbase format
SYMBOL_MAP = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "SOLUSDT": "SOL-USD",
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "SOL": "SOL-USD",
}


class CoinbasePriceFeed:
    """Coinbase price feed using REST API polling.

    Polls Coinbase's public API for current prices at a configurable interval.
    No authentication required.

    Example:
        >>> feed = CoinbasePriceFeed(symbols=["BTCUSDT", "ETHUSDT"])
        >>> feed.on_price_update(lambda u: print(f"{u.symbol}: ${u.price:.2f}"))
        >>> feed.start()
        >>> # ... later
        >>> print(feed.get_price("BTCUSDT"))
        >>> feed.stop()
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        poll_interval_sec: float = 1.0,
        timeout: float = 5.0,
    ) -> None:
        """Initialize Coinbase price feed.

        Args:
            symbols: List of symbols to track (Binance or Coinbase format)
            poll_interval_sec: How often to poll for prices
            timeout: HTTP request timeout
        """
        self._poll_interval = poll_interval_sec
        self._timeout = timeout

        # Convert symbols to Coinbase format
        self._symbols: Dict[str, str] = {}  # binance_symbol -> coinbase_symbol
        for s in (symbols or []):
            s_upper = s.upper()
            coinbase_sym = SYMBOL_MAP.get(s_upper, s_upper)
            self._symbols[s_upper] = coinbase_sym

        # State
        self._latest_prices: Dict[str, CoinbasePriceUpdate] = {}
        self._lock = threading.Lock()
        self._running = False

        # HTTP client
        self._client: Optional[httpx.Client] = None

        # Callbacks
        self._price_callbacks: List[Callable[[CoinbasePriceUpdate], None]] = []
        self._connect_callbacks: List[Callable[[], None]] = []
        self._disconnect_callbacks: List[Callable[[], None]] = []

        # Threading
        self._thread: Optional[threading.Thread] = None

    @property
    def is_connected(self) -> bool:
        """Check if feed is running."""
        return self._running and self._client is not None

    @property
    def symbols(self) -> List[str]:
        """Get tracked symbols (Binance format)."""
        return list(self._symbols.keys())

    def on_price_update(self, callback: Callable[[CoinbasePriceUpdate], None]) -> None:
        """Register callback for price updates."""
        self._price_callbacks.append(callback)

    def on_connect(self, callback: Callable[[], None]) -> None:
        """Register callback for connection events."""
        self._connect_callbacks.append(callback)

    def on_disconnect(self, callback: Callable[[], None]) -> None:
        """Register callback for disconnection events."""
        self._disconnect_callbacks.append(callback)

    def get_price(self, symbol: str) -> Optional[CoinbasePriceUpdate]:
        """Get the latest price for a symbol.

        Args:
            symbol: Trading pair symbol (Binance or Coinbase format)

        Returns:
            Latest CoinbasePriceUpdate or None
        """
        symbol_upper = symbol.upper()
        with self._lock:
            return self._latest_prices.get(symbol_upper)

    def get_all_prices(self) -> Dict[str, CoinbasePriceUpdate]:
        """Get all latest prices."""
        with self._lock:
            return dict(self._latest_prices)

    def start(self) -> "CoinbasePriceFeed":
        """Start the price feed.

        Returns:
            Self for chaining
        """
        if self._running:
            return self

        self._running = True
        self._client = httpx.Client(timeout=self._timeout)

        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="CoinbasePriceFeed",
        )
        self._thread.start()

        # Wait for first price fetch
        time.sleep(0.5)

        # Notify connect callbacks
        for callback in self._connect_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Connect callback error: %s", e)

        logger.info(
            "CoinbasePriceFeed started (symbols: %s)",
            ", ".join(self._symbols.keys()),
        )

        return self

    def stop(self) -> None:
        """Stop the price feed."""
        self._running = False

        if self._thread:
            self._thread.join(timeout=2.0)

        if self._client:
            self._client.close()
            self._client = None

        # Notify disconnect callbacks
        for callback in self._disconnect_callbacks:
            try:
                callback()
            except Exception as e:
                logger.error("Disconnect callback error: %s", e)

        logger.info("CoinbasePriceFeed stopped")

    def _poll_loop(self) -> None:
        """Background polling loop."""
        while self._running:
            try:
                self._fetch_prices()
            except Exception as e:
                logger.error("Price fetch error: %s", e)

            time.sleep(self._poll_interval)

    def _fetch_prices(self) -> None:
        """Fetch current prices from Coinbase."""
        if not self._client:
            return

        now = time.time()

        for binance_sym, coinbase_sym in self._symbols.items():
            try:
                # Coinbase API endpoint for spot price
                response = self._client.get(
                    f"{COINBASE_API_URL}/prices/{coinbase_sym}/spot"
                )
                response.raise_for_status()

                data = response.json()
                price_str = data.get("data", {}).get("amount")

                if price_str:
                    price = float(price_str)

                    update = CoinbasePriceUpdate(
                        symbol=binance_sym.lower(),  # Match Binance format (lowercase)
                        price=price,
                        timestamp=now,
                    )

                    with self._lock:
                        self._latest_prices[binance_sym] = update

                    # Notify callbacks
                    for callback in self._price_callbacks:
                        try:
                            callback(update)
                        except Exception as e:
                            logger.error("Price callback error: %s", e)

            except httpx.HTTPError as e:
                logger.debug("Failed to fetch %s: %s", coinbase_sym, e)
            except Exception as e:
                logger.debug("Error processing %s: %s", coinbase_sym, e)
