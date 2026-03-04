"""Kalshi Market - Implementation of I_Market for Kalshi markets."""

from datetime import datetime
from typing import Any, Dict, Optional

from .i_market import I_Market
from .market_types import OrderBook
from ..exchange_client.kalshi import KalshiMarketData


class KalshiMarket(I_Market):
    """Kalshi market implementing the I_Market interface.

    Represents a single Kalshi binary outcome market with
    orderbook data and timing information.

    Example:
        >>> market = KalshiMarket.from_api_data(api_response)
        >>> orderbook = market.get_current_orderbook("yes")
        >>> print(f"Best bid: {orderbook.best_bid_yes}")
    """

    def __init__(
        self,
        market_data: KalshiMarketData,
        exchange_client: Any = None,
    ):
        """Initialize market.

        Args:
            market_data: Market data from API
            exchange_client: Optional exchange client for updates
        """
        self._data = market_data
        self._client = exchange_client
        self._orderbook: Optional[OrderBook] = None
        self._last_update = datetime.now()

    @classmethod
    def from_api_data(cls, data: Dict[str, Any], client: Any = None) -> "KalshiMarket":
        """Create market from API response.

        Args:
            data: Raw API response dict
            client: Optional exchange client

        Returns:
            KalshiMarket instance
        """
        market_data = KalshiMarketData.from_api(data)
        return cls(market_data, client)

    # --- I_Market Properties ---

    @property
    def ticker(self) -> str:
        return self._data.ticker

    @property
    def slug(self) -> str:
        return self._data.event_ticker

    @property
    def name(self) -> str:
        return self._data.title

    # --- I_Market Methods ---

    def get_ticker(self) -> str:
        return self._data.ticker

    def get_slug(self) -> str:
        return self._data.event_ticker

    def get_name(self) -> str:
        return self._data.title

    def get_current_orderbook(self, outcome: str = "yes") -> OrderBook:
        """Get current orderbook for the specified outcome.

        Args:
            outcome: "yes" or "no"

        Returns:
            OrderBook with current data
        """
        if outcome.lower() == "yes":
            return OrderBook(
                best_bid_yes=self._data.yes_bid,
                best_ask_yes=self._data.yes_ask,
                current_volume=self._data.volume,
                last_traded_at_yes=0,  # Not available in basic data
                depth_yes={},  # Requires separate orderbook call
                spread=self._data.yes_ask - self._data.yes_bid,
                timestamp_ns=int(self._last_update.timestamp() * 1e9),
            )
        else:
            # Invert for NO side
            return OrderBook(
                best_bid_yes=self._data.no_bid,
                best_ask_yes=self._data.no_ask,
                current_volume=self._data.volume,
                last_traded_at_yes=0,
                depth_yes={},
                spread=self._data.no_ask - self._data.no_bid,
                timestamp_ns=int(self._last_update.timestamp() * 1e9),
            )

    async def update_orderbook(self) -> None:
        """Refresh orderbook data from exchange."""
        if self._client is None:
            raise RuntimeError("No exchange client configured for updates")

        # Fetch updated market data
        updated: KalshiMarketData = await self._client.request_market(self._data.ticker)
        self._data = updated
        self._last_update = datetime.now()

    def time_to_resolution(self) -> Optional[float]:
        """Seconds until market resolves."""
        if self._data.close_time is None:
            return None

        now = datetime.now(self._data.close_time.tzinfo)
        delta = self._data.close_time - now
        return max(0, delta.total_seconds())

    def get_volatility(self) -> float:
        """Estimated volatility based on mid-price distance from 50%."""
        mid = (self._data.yes_bid + self._data.yes_ask) / 2
        # Simple vol estimate: higher when closer to 50%
        return min(mid, 100 - mid) / 50.0

    # --- Additional Properties ---

    @property
    def mid_price(self) -> float:
        """Midpoint price for YES contracts (in dollars)."""
        return (self._data.yes_bid + self._data.yes_ask) / 200.0

    @property
    def spread(self) -> int:
        """Bid-ask spread in cents."""
        return self._data.yes_ask - self._data.yes_bid

    @property
    def status(self) -> str:
        """Market status string."""
        return self._data.status

    @property
    def is_open(self) -> bool:
        """Whether market is open for trading."""
        return self._data.status == "open"

    @property
    def volume(self) -> int:
        """Trading volume."""
        return self._data.volume

    def __repr__(self) -> str:
        return f"KalshiMarket({self.ticker}, bid={self._data.yes_bid}, ask={self._data.yes_ask})"
