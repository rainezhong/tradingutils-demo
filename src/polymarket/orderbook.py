"""Order book manager for Polymarket.

Maintains real-time order book state with support for:
- Snapshot initialization
- Incremental updates (deltas)
- Price level aggregation
- Thread-safe operations
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from .models import OrderBookLevel, PolymarketOrderBook


logger = logging.getLogger(__name__)


@dataclass
class OrderBookState:
    """Internal order book state for a single asset."""

    asset_id: str
    market: str
    bids: Dict[float, float] = field(default_factory=dict)  # price -> size
    asks: Dict[float, float] = field(default_factory=dict)  # price -> size
    last_update: Optional[datetime] = None
    sequence: int = 0

    def to_orderbook(self) -> PolymarketOrderBook:
        """Convert to PolymarketOrderBook model."""
        bid_levels = [
            OrderBookLevel(price=p, size=s)
            for p, s in sorted(self.bids.items(), reverse=True)
            if s > 0
        ]
        ask_levels = [
            OrderBookLevel(price=p, size=s)
            for p, s in sorted(self.asks.items())
            if s > 0
        ]

        return PolymarketOrderBook(
            asset_id=self.asset_id,
            market=self.market,
            bids=bid_levels,
            asks=ask_levels,
            timestamp=self.last_update,
        )


class OrderBookManager:
    """Manages order book state for multiple Polymarket assets.

    Features:
    - Thread-safe updates
    - Snapshot and delta handling
    - Callbacks on book changes
    - Depth calculation at price levels

    Example:
        >>> manager = OrderBookManager()
        >>> manager.on_update(lambda asset_id, book: print(f"Update: {asset_id}"))
        >>> manager.apply_snapshot("asset123", "market456", bids, asks)
        >>> book = manager.get_orderbook("asset123")
    """

    def __init__(self) -> None:
        """Initialize order book manager."""
        self._books: Dict[str, OrderBookState] = {}
        self._lock = threading.RLock()
        self._callbacks: List[Callable[[str, PolymarketOrderBook], None]] = []

    def on_update(self, callback: Callable[[str, PolymarketOrderBook], None]) -> None:
        """Register callback for order book updates.

        Args:
            callback: Function called with (asset_id, orderbook) on updates
        """
        self._callbacks.append(callback)

    def apply_snapshot(
        self,
        asset_id: str,
        market: str,
        bids: List[Dict],
        asks: List[Dict],
        sequence: int = 0,
    ) -> None:
        """Apply a full order book snapshot.

        Args:
            asset_id: Asset/token ID
            market: Market/condition ID
            bids: List of bid levels [{"price": x, "size": y}, ...]
            asks: List of ask levels
            sequence: Sequence number for ordering
        """
        with self._lock:
            state = OrderBookState(
                asset_id=asset_id,
                market=market,
                bids={float(b["price"]): float(b["size"]) for b in bids},
                asks={float(a["price"]): float(a["size"]) for a in asks},
                last_update=datetime.now(),
                sequence=sequence,
            )
            self._books[asset_id] = state

        logger.debug(
            "Snapshot applied: %s (%d bids, %d asks)",
            asset_id[:12] + "...",
            len(bids),
            len(asks),
        )

        self._notify_callbacks(asset_id)

    def apply_delta(
        self,
        asset_id: str,
        side: str,
        price: float,
        size: float,
        sequence: int = 0,
    ) -> None:
        """Apply an incremental order book update.

        Args:
            asset_id: Asset/token ID
            side: "bid" or "ask"
            price: Price level
            size: New size (0 to remove level)
            sequence: Sequence number
        """
        with self._lock:
            if asset_id not in self._books:
                logger.warning("Delta for unknown asset: %s", asset_id)
                return

            state = self._books[asset_id]

            # Check sequence (allow out-of-order by small margin)
            if sequence > 0 and sequence < state.sequence:
                logger.debug("Stale delta ignored: seq=%d < %d", sequence, state.sequence)
                return

            # Apply update
            book_side = state.bids if side.lower() == "bid" else state.asks

            if size > 0:
                book_side[price] = size
            elif price in book_side:
                del book_side[price]

            state.last_update = datetime.now()
            state.sequence = max(state.sequence, sequence)

        self._notify_callbacks(asset_id)

    def apply_deltas(
        self,
        asset_id: str,
        changes: List[Dict],
        sequence: int = 0,
    ) -> None:
        """Apply multiple deltas atomically.

        Args:
            asset_id: Asset/token ID
            changes: List of {"side": x, "price": y, "size": z}
            sequence: Sequence number
        """
        with self._lock:
            if asset_id not in self._books:
                logger.warning("Deltas for unknown asset: %s", asset_id)
                return

            state = self._books[asset_id]

            if sequence > 0 and sequence < state.sequence:
                return

            for change in changes:
                side = change.get("side", "").lower()
                price = float(change.get("price", 0))
                size = float(change.get("size", 0))

                book_side = state.bids if side == "bid" else state.asks

                if size > 0:
                    book_side[price] = size
                elif price in book_side:
                    del book_side[price]

            state.last_update = datetime.now()
            state.sequence = max(state.sequence, sequence)

        self._notify_callbacks(asset_id)

    def get_orderbook(self, asset_id: str) -> Optional[PolymarketOrderBook]:
        """Get current order book for an asset.

        Args:
            asset_id: Asset/token ID

        Returns:
            PolymarketOrderBook or None if not found
        """
        with self._lock:
            state = self._books.get(asset_id)
            if state is None:
                return None
            return state.to_orderbook()

    def get_best_bid_ask(self, asset_id: str) -> Tuple[Optional[float], Optional[float]]:
        """Get best bid and ask prices.

        Args:
            asset_id: Asset/token ID

        Returns:
            Tuple of (best_bid, best_ask), either can be None
        """
        with self._lock:
            state = self._books.get(asset_id)
            if state is None:
                return None, None

            best_bid = max(state.bids.keys()) if state.bids else None
            best_ask = min(state.asks.keys()) if state.asks else None

            return best_bid, best_ask

    def get_mid_price(self, asset_id: str) -> Optional[float]:
        """Get mid price.

        Args:
            asset_id: Asset/token ID

        Returns:
            Mid price or None
        """
        bid, ask = self.get_best_bid_ask(asset_id)
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return None

    def get_depth_at_price(
        self,
        asset_id: str,
        side: str,
        price_limit: float,
    ) -> float:
        """Get total depth up to a price level.

        Args:
            asset_id: Asset/token ID
            side: "bid" or "ask"
            price_limit: Price limit

        Returns:
            Total size available up to price_limit
        """
        with self._lock:
            state = self._books.get(asset_id)
            if state is None:
                return 0.0

            if side.lower() == "bid":
                # For bids, count prices >= limit
                return sum(
                    size for price, size in state.bids.items()
                    if price >= price_limit
                )
            else:
                # For asks, count prices <= limit
                return sum(
                    size for price, size in state.asks.items()
                    if price <= price_limit
                )

    def estimate_fill_price(
        self,
        asset_id: str,
        side: str,
        size: float,
    ) -> Optional[float]:
        """Estimate average fill price for a given size.

        Args:
            asset_id: Asset/token ID
            side: "buy" or "sell" (what the taker wants to do)
            size: Size to fill

        Returns:
            Volume-weighted average price or None if insufficient liquidity
        """
        with self._lock:
            state = self._books.get(asset_id)
            if state is None:
                return None

            # Buying takes from asks, selling takes from bids
            if side.lower() in ("buy", "bid"):
                levels = sorted(state.asks.items())  # Ascending
            else:
                levels = sorted(state.bids.items(), reverse=True)  # Descending

            remaining = size
            total_cost = 0.0

            for price, level_size in levels:
                fill_size = min(remaining, level_size)
                total_cost += price * fill_size
                remaining -= fill_size

                if remaining <= 0:
                    break

            if remaining > 0:
                return None  # Insufficient liquidity

            return total_cost / size

    def get_spread(self, asset_id: str) -> Optional[float]:
        """Get bid-ask spread.

        Args:
            asset_id: Asset/token ID

        Returns:
            Spread or None
        """
        bid, ask = self.get_best_bid_ask(asset_id)
        if bid is not None and ask is not None:
            return ask - bid
        return None

    def get_assets(self) -> List[str]:
        """Get list of tracked asset IDs."""
        with self._lock:
            return list(self._books.keys())

    def clear(self, asset_id: Optional[str] = None) -> None:
        """Clear order book state.

        Args:
            asset_id: Specific asset to clear, or None for all
        """
        with self._lock:
            if asset_id:
                self._books.pop(asset_id, None)
            else:
                self._books.clear()

    def _notify_callbacks(self, asset_id: str) -> None:
        """Notify registered callbacks of an update."""
        book = self.get_orderbook(asset_id)
        if book is None:
            return

        for callback in self._callbacks:
            try:
                callback(asset_id, book)
            except Exception as e:
                logger.error("Callback error: %s", e)
