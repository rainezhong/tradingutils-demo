"""Scanner types - exchange-agnostic dataclasses for market scanning."""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Union


# Filter type: sync or async callable that takes market and returns bool
MarketFilterFn = Callable[[Any], Union[bool, Awaitable[bool]]]


@dataclass
class ScanFilter:
    """Exchange-agnostic filters for market scanning.

    Attributes:
        series_ticker: Filter by series/event ticker
        status: Market status filter (e.g., "open", "closed")
        min_volume: Minimum trading volume
        max_spread_cents: Maximum bid-ask spread in cents
        min_price_cents: Minimum price (yes_bid or equivalent)
        max_price_cents: Maximum price
        custom_filter: Custom filter function
        strategy_filter: Filter from I_Strategy.market_filter()
    """

    series_ticker: Optional[str] = None
    status: str = "open"
    min_volume: int = 0
    max_spread_cents: int = 100
    min_price_cents: int = 0
    max_price_cents: int = 100
    custom_filter: Optional[MarketFilterFn] = None
    strategy_filter: Optional[MarketFilterFn] = None

    @classmethod
    def from_strategy(cls, strategy: Any, **kwargs) -> "ScanFilter":
        """Create filter using a strategy's market_filter method.

        Args:
            strategy: Strategy with get_market_filter() method
            **kwargs: Additional filter parameters
        """
        return cls(strategy_filter=strategy.get_market_filter(), **kwargs)


@dataclass
class ScanResult:
    """Exchange-agnostic result from a market scan.

    Wraps any market data type with computed spread.
    """

    market: Any  # Exchange-specific market data
    spread_cents: int

    @property
    def ticker(self) -> str:
        """Market ticker symbol."""
        return getattr(self.market, "ticker", str(self.market))

    @property
    def volume(self) -> int:
        """Trading volume."""
        return getattr(self.market, "volume", 0)

    @property
    def bid_cents(self) -> int:
        """Best bid price in cents."""
        # Try common attribute names
        for attr in ("yes_bid", "bid", "bid_cents", "best_bid"):
            if hasattr(self.market, attr):
                return getattr(self.market, attr)
        return 0

    @property
    def ask_cents(self) -> int:
        """Best ask price in cents."""
        for attr in ("yes_ask", "ask", "ask_cents", "best_ask"):
            if hasattr(self.market, attr):
                return getattr(self.market, attr)
        return 100

    @property
    def mid_cents(self) -> float:
        """Midpoint price in cents."""
        return (self.bid_cents + self.ask_cents) / 2

    # Backwards compat
    @property
    def spread(self) -> int:
        """Alias for spread_cents."""
        return self.spread_cents

    @property
    def yes_bid(self) -> int:
        """Alias for bid_cents."""
        return self.bid_cents

    @property
    def yes_ask(self) -> int:
        """Alias for ask_cents."""
        return self.ask_cents
