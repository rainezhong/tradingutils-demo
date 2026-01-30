"""Real-time order book state management for Kalshi markets."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
from typing import Callable, Dict, List, Optional, Tuple

from .exceptions import OrderBookError

logger = logging.getLogger(__name__)


@dataclass
class OrderBookLevel:
    """A single price level in the order book.

    Attributes:
        price: Price in cents (1-99)
        size: Number of contracts at this level
    """

    price: int
    size: int

    def __post_init__(self) -> None:
        if self.price < 1 or self.price > 99:
            raise ValueError(f"Price must be 1-99 cents, got {self.price}")
        if self.size < 0:
            raise ValueError(f"Size must be non-negative, got {self.size}")


@dataclass
class OrderBookState:
    """Complete order book state for a market.

    Attributes:
        ticker: Market ticker
        bids: Bid levels sorted descending by price (best first)
        asks: Ask levels sorted ascending by price (best first)
        sequence: Sequence number for ordering deltas
        timestamp: Last update time
    """

    ticker: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    sequence: int = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)

    @property
    def best_bid(self) -> Optional[OrderBookLevel]:
        """Get the best (highest) bid level."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[OrderBookLevel]:
        """Get the best (lowest) ask level."""
        return self.asks[0] if self.asks else None

    @property
    def spread(self) -> Optional[int]:
        """Calculate spread in cents."""
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return None

    @property
    def spread_decimal(self) -> Optional[float]:
        """Calculate spread as decimal (0-1)."""
        s = self.spread
        return s / 100.0 if s is not None else None

    @property
    def mid_price(self) -> Optional[float]:
        """Calculate mid price in cents."""
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2.0
        return None

    @property
    def mid_price_decimal(self) -> Optional[float]:
        """Calculate mid price as decimal (0-1)."""
        m = self.mid_price
        return m / 100.0 if m is not None else None

    @property
    def bid_depth(self) -> int:
        """Total size across all bid levels."""
        return sum(level.size for level in self.bids)

    @property
    def ask_depth(self) -> int:
        """Total size across all ask levels."""
        return sum(level.size for level in self.asks)

    def is_crossed(self) -> bool:
        """Check if book is crossed (best bid >= best ask)."""
        if self.best_bid and self.best_ask:
            return self.best_bid.price >= self.best_ask.price
        return False


class OrderBookManager:
    """Manages real-time order book state for multiple markets.

    Thread-safe manager that applies snapshots and incremental deltas
    to maintain accurate order book state.

    Example:
        >>> manager = OrderBookManager()
        >>> manager.apply_snapshot("TICKER", snapshot_data)
        >>> manager.apply_delta("TICKER", delta_data)
        >>> book = manager.get_orderbook("TICKER")
        >>> print(f"Spread: {book.spread} cents")
    """

    def __init__(
        self,
        on_update: Optional[Callable[[str, OrderBookState], None]] = None,
    ):
        """Initialize the order book manager.

        Args:
            on_update: Callback invoked after each update (ticker, state)
        """
        self._books: Dict[str, OrderBookState] = {}
        self._lock = RLock()
        self._on_update = on_update

    def apply_snapshot(self, ticker: str, snapshot: dict) -> None:
        """Apply a full order book snapshot.

        Args:
            ticker: Market ticker
            snapshot: Snapshot with format:
                {
                    "yes": [[price, size], ...],
                    "no": [[price, size], ...],
                    "seq": 12345
                }
        """
        with self._lock:
            bids = self._parse_levels(snapshot.get("yes", []), is_bid=True)
            asks = self._parse_levels(snapshot.get("no", []), is_bid=False)
            sequence = snapshot.get("seq", 0)

            state = OrderBookState(
                ticker=ticker,
                bids=bids,
                asks=asks,
                sequence=sequence,
                timestamp=datetime.utcnow(),
            )

            self._books[ticker] = state
            logger.debug(
                f"Applied snapshot for {ticker}: "
                f"{len(bids)} bids, {len(asks)} asks, seq={sequence}"
            )

            if self._on_update:
                self._on_update(ticker, state)

    def apply_delta(self, ticker: str, delta: dict) -> bool:
        """Apply an incremental delta to the order book.

        Args:
            ticker: Market ticker
            delta: Delta with format:
                {
                    "side": "yes" or "no",
                    "price": 45,
                    "delta": 10,
                    "seq": 12346
                }

        Returns:
            True if applied successfully, False if out of sequence

        Raises:
            OrderBookError: If no snapshot exists
        """
        with self._lock:
            if ticker not in self._books:
                raise OrderBookError(
                    "Cannot apply delta without snapshot",
                    ticker=ticker,
                )

            state = self._books[ticker]
            new_seq = delta.get("seq", 0)

            # Check sequence ordering
            if new_seq <= state.sequence:
                logger.warning(
                    f"Stale delta for {ticker}: got seq={new_seq}, current={state.sequence}"
                )
                return False

            if new_seq > state.sequence + 1:
                logger.warning(
                    f"Sequence gap for {ticker}: expected {state.sequence + 1}, got {new_seq}"
                )
                return False

            side = delta.get("side", "")
            price = delta.get("price", 0)
            size_delta = delta.get("delta", 0)

            if side == "yes":
                self._apply_level_delta(state.bids, price, size_delta, is_bid=True)
            elif side == "no":
                ask_price = 100 - price
                self._apply_level_delta(state.asks, ask_price, size_delta, is_bid=False)
            else:
                logger.warning(f"Unknown delta side: {side}")
                return False

            state.sequence = new_seq
            state.timestamp = datetime.utcnow()

            logger.debug(
                f"Applied delta for {ticker}: side={side}, "
                f"price={price}, delta={size_delta}, seq={new_seq}"
            )

            if self._on_update:
                self._on_update(ticker, state)

            return True

    def _parse_levels(
        self,
        levels: List[List[int]],
        is_bid: bool,
    ) -> List[OrderBookLevel]:
        """Parse API levels to OrderBookLevel objects."""
        parsed = []
        for level in levels:
            if len(level) >= 2:
                price, size = level[0], level[1]
                if not is_bid:
                    price = 100 - price
                if size > 0 and 1 <= price <= 99:
                    parsed.append(OrderBookLevel(price=price, size=size))

        parsed.sort(key=lambda x: x.price, reverse=is_bid)
        return parsed

    def _apply_level_delta(
        self,
        levels: List[OrderBookLevel],
        price: int,
        size_delta: int,
        is_bid: bool,
    ) -> None:
        """Apply a size delta to a price level."""
        for i, level in enumerate(levels):
            if level.price == price:
                new_size = level.size + size_delta
                if new_size <= 0:
                    levels.pop(i)
                else:
                    level.size = new_size
                return

        if size_delta > 0 and 1 <= price <= 99:
            levels.append(OrderBookLevel(price=price, size=size_delta))
            levels.sort(key=lambda x: x.price, reverse=is_bid)

    def get_orderbook(self, ticker: str) -> Optional[OrderBookState]:
        """Get current order book state."""
        with self._lock:
            return self._books.get(ticker)

    def get_best_bid(self, ticker: str) -> Optional[OrderBookLevel]:
        """Get best bid for a market."""
        with self._lock:
            book = self._books.get(ticker)
            return book.best_bid if book else None

    def get_best_ask(self, ticker: str) -> Optional[OrderBookLevel]:
        """Get best ask for a market."""
        with self._lock:
            book = self._books.get(ticker)
            return book.best_ask if book else None

    def get_spread(self, ticker: str) -> Optional[int]:
        """Get spread in cents."""
        with self._lock:
            book = self._books.get(ticker)
            return book.spread if book else None

    def get_depth(self, ticker: str, levels: int = 5) -> Tuple[int, int]:
        """Get depth within top N levels.

        Returns:
            (bid_depth, ask_depth) or (0, 0)
        """
        with self._lock:
            book = self._books.get(ticker)
            if not book:
                return (0, 0)

            bid_depth = sum(level.size for level in book.bids[:levels])
            ask_depth = sum(level.size for level in book.asks[:levels])
            return (bid_depth, ask_depth)

    def get_vwap(self, ticker: str, side: str, size: int) -> Optional[float]:
        """Calculate VWAP to fill given size.

        Args:
            ticker: Market ticker
            side: "bid" or "ask"
            size: Contracts to fill

        Returns:
            VWAP in cents, or None if insufficient liquidity
        """
        with self._lock:
            book = self._books.get(ticker)
            if not book:
                return None

            levels = book.bids if side == "bid" else book.asks
            remaining = size
            total_value = 0.0

            for level in levels:
                fill_size = min(remaining, level.size)
                total_value += fill_size * level.price
                remaining -= fill_size
                if remaining <= 0:
                    break

            if remaining > 0:
                return None

            return total_value / size

    def clear(self, ticker: Optional[str] = None) -> None:
        """Clear order book state."""
        with self._lock:
            if ticker:
                self._books.pop(ticker, None)
            else:
                self._books.clear()

    def get_all_tickers(self) -> List[str]:
        """Get all tracked tickers."""
        with self._lock:
            return list(self._books.keys())

    def has_orderbook(self, ticker: str) -> bool:
        """Check if order book exists."""
        with self._lock:
            return ticker in self._books
