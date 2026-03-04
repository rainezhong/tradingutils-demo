"""Real-time order book state management with snapshot and delta support.

Ported from src/core/orderbook_manager.py — converted from threading.RLock to asyncio.Lock,
kept all business logic intact. Pure computation methods remain sync.
"""

import asyncio
import bisect
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import Callable, ClassVar, Dict, List, Optional, Tuple

import logging

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeltaResult(Enum):
    """Result of applying a delta to an order book."""

    APPLIED = "applied"
    STALE = "stale"
    GAP = "gap"
    INVALID = "invalid"


class Side(Enum):
    """Order book side for VWAP and depth queries."""

    BID = "bid"
    ASK = "ask"


DEFAULT_MIN_PRICE = 0
DEFAULT_MAX_PRICE = 99


@dataclass
class OrderBookLevel:
    """A single price level in the order book."""

    MIN_PRICE: ClassVar[int] = DEFAULT_MIN_PRICE
    MAX_PRICE: ClassVar[int] = DEFAULT_MAX_PRICE

    price: int
    size: int

    def __post_init__(self) -> None:
        if self.price < self.MIN_PRICE or self.price > self.MAX_PRICE:
            raise ValueError(
                f"Price must be {self.MIN_PRICE}-{self.MAX_PRICE} cents, got {self.price}"
            )
        if self.size < 0:
            raise ValueError(f"Size must be non-negative, got {self.size}")


@dataclass
class OrderBookState:
    """Complete order book state for a market."""

    ticker: str
    bids: List[OrderBookLevel] = field(default_factory=list)
    asks: List[OrderBookLevel] = field(default_factory=list)
    sequence: int = 0
    timestamp: datetime = field(default_factory=_utc_now)
    volume_24h: int = 0

    @property
    def best_bid(self) -> Optional[OrderBookLevel]:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> Optional[OrderBookLevel]:
        return self.asks[0] if self.asks else None

    @property
    def spread(self) -> Optional[int]:
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return None

    @property
    def spread_pct(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            mid = (self.best_bid.price + self.best_ask.price) / 2
            if mid > 0:
                return (self.best_ask.price - self.best_bid.price) / mid * 100
        return None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None

    @property
    def bid_depth(self) -> int:
        return sum(level.size for level in self.bids)

    @property
    def ask_depth(self) -> int:
        return sum(level.size for level in self.asks)

    def is_crossed(self) -> bool:
        if self.best_bid and self.best_ask:
            return self.best_bid.price >= self.best_ask.price
        return False

    def __repr__(self) -> str:
        bid_str = (
            f"{self.best_bid.price}@{self.best_bid.size}" if self.best_bid else "None"
        )
        ask_str = (
            f"{self.best_ask.price}@{self.best_ask.size}" if self.best_ask else "None"
        )
        return (
            f"OrderBookState(ticker={self.ticker!r}, "
            f"bid={bid_str}, ask={ask_str}, "
            f"spread={self.spread}, levels={len(self.bids)}/{len(self.asks)}, "
            f"seq={self.sequence})"
        )


class OrderBookManager:
    """Manages real-time order book state for multiple markets.

    Async-safe manager that applies snapshots and incremental deltas
    to maintain accurate order book state. Uses asyncio.Lock instead
    of threading.RLock for async compatibility.
    """

    def __init__(
        self,
        on_update: Optional[Callable[[str, OrderBookState], None]] = None,
        on_gap: Optional[Callable[[str, int, int], None]] = None,
    ):
        self._books: Dict[str, OrderBookState] = {}
        self._lock = asyncio.Lock()
        self._on_update = on_update
        self._on_gap = on_gap
        self._update_listeners: List[Callable[[str, OrderBookState], None]] = []

    async def add_update_listener(
        self, callback: Callable[[str, OrderBookState], None]
    ) -> None:
        async with self._lock:
            if callback not in self._update_listeners:
                self._update_listeners.append(callback)

    async def remove_update_listener(
        self, callback: Callable[[str, OrderBookState], None]
    ) -> None:
        async with self._lock:
            if callback in self._update_listeners:
                self._update_listeners.remove(callback)

    def _notify_listeners(self, ticker: str, state: OrderBookState) -> None:
        if self._on_update:
            try:
                self._on_update(ticker, state)
            except Exception as e:
                logger.error(f"Error in on_update callback for {ticker}: {e}")

        for listener in self._update_listeners:
            try:
                listener(ticker, state)
            except Exception as e:
                logger.error(f"Error in update listener for {ticker}: {e}")

    async def apply_snapshot(self, ticker: str, snapshot: dict) -> None:
        callback_state = None
        async with self._lock:
            bids = self._parse_levels(snapshot.get("yes", []), is_bid=True)
            asks = self._parse_levels(snapshot.get("no", []), is_bid=False)
            sequence = snapshot.get("seq", 0)

            state = OrderBookState(
                ticker=ticker,
                bids=bids,
                asks=asks,
                sequence=sequence,
                timestamp=_utc_now(),
            )

            self._books[ticker] = state
            logger.debug(
                f"Applied snapshot for {ticker}: "
                f"{len(bids)} bids, {len(asks)} asks, seq={sequence}"
            )

            if self._on_update or self._update_listeners:
                callback_state = self._copy_state(state)

        if callback_state is not None:
            self._notify_listeners(ticker, callback_state)

    async def apply_delta(self, ticker: str, delta: dict) -> DeltaResult:
        callback_state = None
        gap_detected = False
        expected_seq = 0
        actual_seq = 0

        async with self._lock:
            if ticker not in self._books:
                raise RuntimeError(f"Cannot apply delta without snapshot for {ticker}")

            state = self._books[ticker]
            new_seq = delta.get("seq", 0)

            if new_seq <= state.sequence:
                return DeltaResult.STALE

            if new_seq > state.sequence + 1:
                logger.warning(
                    f"Sequence gap for {ticker}: "
                    f"expected {state.sequence + 1}, got {new_seq}"
                )
                gap_detected = True
                expected_seq = state.sequence + 1
                actual_seq = new_seq

            side = delta.get("side", "")
            price = delta.get("price", 0)
            size_delta = delta.get("delta", 0)

            if not (0 <= price <= 100):
                logger.warning(f"Invalid delta price for {ticker}: {price}")
                return DeltaResult.INVALID

            if side == "yes":
                self._apply_level_delta(state.bids, price, size_delta, is_bid=True)
            elif side == "no":
                ask_price = 100 - price
                self._apply_level_delta(state.asks, ask_price, size_delta, is_bid=False)
            else:
                logger.warning(f"Unknown delta side for {ticker}: {side}")
                return DeltaResult.INVALID

            state.sequence = new_seq
            state.timestamp = _utc_now()

            if self._on_update or self._update_listeners:
                callback_state = self._copy_state(state)

        if callback_state is not None:
            self._notify_listeners(ticker, callback_state)

        # Notify gap callback outside the lock
        if gap_detected and self._on_gap:
            try:
                self._on_gap(ticker, expected_seq, actual_seq)
            except Exception as e:
                logger.error(f"Error in gap callback for {ticker}: {e}")

        return DeltaResult.GAP if gap_detected else DeltaResult.APPLIED

    def _parse_levels(
        self, levels: List[List[int]], is_bid: bool
    ) -> List[OrderBookLevel]:
        parsed = []
        min_price = OrderBookLevel.MIN_PRICE
        max_price = OrderBookLevel.MAX_PRICE

        for i, level in enumerate(levels):
            if len(level) < 2:
                continue
            price, size = level[0], level[1]
            if not is_bid:
                price = 100 - price
            if not (min_price <= price <= max_price):
                continue
            if size <= 0:
                continue
            parsed.append(OrderBookLevel(price=price, size=size))

        parsed.sort(key=lambda x: x.price, reverse=is_bid)
        return parsed

    def _copy_state(self, state: OrderBookState) -> OrderBookState:
        return OrderBookState(
            ticker=state.ticker,
            bids=[OrderBookLevel(price=lvl.price, size=lvl.size) for lvl in state.bids],
            asks=[OrderBookLevel(price=lvl.price, size=lvl.size) for lvl in state.asks],
            sequence=state.sequence,
            timestamp=state.timestamp,
            volume_24h=state.volume_24h,
        )

    @staticmethod
    def _apply_level_delta(
        levels: List[OrderBookLevel],
        price: int,
        size_delta: int,
        is_bid: bool,
    ) -> None:
        if is_bid:
            keys = [-lvl.price for lvl in levels]
            target = -price
        else:
            keys = [lvl.price for lvl in levels]
            target = price

        idx = bisect.bisect_left(keys, target)

        if idx < len(levels) and levels[idx].price == price:
            new_size = levels[idx].size + size_delta
            if new_size <= 0:
                levels.pop(idx)
            else:
                levels[idx].size = new_size
            return

        if size_delta > 0:
            new_level = OrderBookLevel(price=price, size=size_delta)
            levels.insert(idx, new_level)

    async def get_orderbook(self, ticker: str) -> Optional[OrderBookState]:
        async with self._lock:
            state = self._books.get(ticker)
            return self._copy_state(state) if state else None

    async def get_best_bid(self, ticker: str) -> Optional[OrderBookLevel]:
        async with self._lock:
            book = self._books.get(ticker)
            if book and book.best_bid:
                return OrderBookLevel(
                    price=book.best_bid.price, size=book.best_bid.size
                )
            return None

    async def get_best_ask(self, ticker: str) -> Optional[OrderBookLevel]:
        async with self._lock:
            book = self._books.get(ticker)
            if book and book.best_ask:
                return OrderBookLevel(
                    price=book.best_ask.price, size=book.best_ask.size
                )
            return None

    async def get_spread(self, ticker: str) -> Optional[int]:
        async with self._lock:
            book = self._books.get(ticker)
            return book.spread if book else None

    async def get_depth(
        self, ticker: str, levels: int = 5
    ) -> Optional[Tuple[int, int]]:
        async with self._lock:
            book = self._books.get(ticker)
            if not book:
                return None
            bid_depth = sum(level.size for level in book.bids[:levels])
            ask_depth = sum(level.size for level in book.asks[:levels])
            return (bid_depth, ask_depth)

    async def get_vwap(self, ticker: str, side: Side, size: int) -> Optional[float]:
        if not isinstance(side, Side):
            raise TypeError(
                f"side must be Side.BID or Side.ASK, got {type(side).__name__}: {side!r}"
            )
        async with self._lock:
            book = self._books.get(ticker)
            if not book:
                return None
            levels = book.bids if side == Side.BID else book.asks
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

    async def clear(self, ticker: Optional[str] = None) -> None:
        async with self._lock:
            if ticker:
                self._books.pop(ticker, None)
            else:
                self._books.clear()

    async def get_all_tickers(self) -> List[str]:
        async with self._lock:
            return list(self._books.keys())

    async def has_orderbook(self, ticker: str) -> bool:
        async with self._lock:
            return ticker in self._books
