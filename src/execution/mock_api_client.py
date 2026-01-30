"""Mock API client for testing execution components.

Provides a complete mock implementation of the APIClient interface
for testing QuoteManager and other execution components without
connecting to a real exchange.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core.utils import utc_now
from ..market_making.interfaces import APIClient, OrderError
from ..market_making.models import Fill, MarketState


@dataclass
class MockOrder:
    """Internal representation of an order in the mock system."""

    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    filled_size: int = 0
    status: str = "open"  # open, filled, partial, cancelled
    created_at: datetime = field(default_factory=utc_now)


class MockAPIClient(APIClient):
    """Mock implementation of APIClient for testing.

    Simulates exchange behavior including:
    - Order placement and cancellation
    - Order status tracking
    - Manual fill triggering for testing
    - Configurable failure modes

    Attributes:
        fail_next_place: If True, next place_order will raise OrderError.
        fail_next_cancel: If True, next cancel_order will return False.
        fail_next_status: If True, next get_order_status will raise OrderError.
        rate_limit_until: If set, operations will fail until this time.

    Example:
        >>> client = MockAPIClient()
        >>> order_id = client.place_order("TICKER", "BID", 0.45, 20)
        >>> client.simulate_fill(order_id, 10)
        >>> status = client.get_order_status(order_id)
        >>> status['filled_size']
        10
    """

    def __init__(self) -> None:
        """Initialize mock client with empty state."""
        self._orders: dict[str, MockOrder] = {}
        self._positions: dict[str, int] = {}
        self._fills: list[Fill] = []
        self._market_data: dict[str, MarketState] = {}

        # Failure simulation flags
        self.fail_next_place: bool = False
        self.fail_next_cancel: bool = False
        self.fail_next_status: bool = False
        self.rate_limit_until: Optional[datetime] = None

    def _check_rate_limit(self) -> None:
        """Check if rate limited and raise if so."""
        if self.rate_limit_until and utc_now() < self.rate_limit_until:
            raise OrderError("Rate limited")

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place a mock order.

        Args:
            ticker: Market identifier.
            side: 'BID' or 'ASK'.
            price: Order price (0-1 range).
            size: Number of contracts.

        Returns:
            Generated order ID.

        Raises:
            OrderError: If fail_next_place is True or rate limited.
        """
        self._check_rate_limit()

        if self.fail_next_place:
            self.fail_next_place = False
            raise OrderError("Simulated order placement failure")

        order_id = f"mock_{uuid.uuid4().hex[:12]}"
        order = MockOrder(
            order_id=order_id,
            ticker=ticker,
            side=side,
            price=price,
            size=size,
        )
        self._orders[order_id] = order
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a mock order.

        Args:
            order_id: Order ID to cancel.

        Returns:
            True if cancelled, False if failed or order not found.
        """
        try:
            self._check_rate_limit()
        except OrderError:
            return False

        if self.fail_next_cancel:
            self.fail_next_cancel = False
            return False

        if order_id not in self._orders:
            return False

        order = self._orders[order_id]
        if order.status in ("filled", "cancelled"):
            return False

        order.status = "cancelled"
        return True

    def get_order_status(self, order_id: str) -> dict:
        """Get status of a mock order.

        Args:
            order_id: Order ID to check.

        Returns:
            Status dict with: status, filled_size, remaining_size, avg_fill_price.

        Raises:
            OrderError: If fail_next_status is True or order not found.
        """
        self._check_rate_limit()

        if self.fail_next_status:
            self.fail_next_status = False
            raise OrderError("Simulated status check failure")

        if order_id not in self._orders:
            raise OrderError(f"Order not found: {order_id}")

        order = self._orders[order_id]
        remaining = order.size - order.filled_size

        # Calculate average fill price from fills
        avg_fill_price = None
        if order.filled_size > 0:
            order_fills = [f for f in self._fills if f.order_id == order_id]
            if order_fills:
                total_value = sum(f.price * f.size for f in order_fills)
                total_size = sum(f.size for f in order_fills)
                avg_fill_price = total_value / total_size

        return {
            "status": order.status,
            "filled_size": order.filled_size,
            "remaining_size": remaining,
            "avg_fill_price": avg_fill_price,
        }

    def get_market_data(self, ticker: str) -> MarketState:
        """Get mock market data.

        Args:
            ticker: Market identifier.

        Returns:
            MarketState for the ticker.

        Raises:
            OrderError: If no market data set for ticker.
        """
        if ticker not in self._market_data:
            raise OrderError(f"No market data for: {ticker}")
        return self._market_data[ticker]

    def get_positions(self) -> dict[str, int]:
        """Get all mock positions.

        Returns:
            Dictionary mapping ticker to contract count.
        """
        return dict(self._positions)

    def get_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
    ) -> list[Fill]:
        """Get mock fills.

        Args:
            ticker: Filter by ticker (optional).
            limit: Maximum fills to return.

        Returns:
            List of Fill objects, most recent first.
        """
        fills = self._fills
        if ticker:
            fills = [f for f in fills if f.ticker == ticker]
        return sorted(fills, key=lambda f: f.timestamp, reverse=True)[:limit]

    # Testing helpers

    def simulate_fill(
        self,
        order_id: str,
        fill_size: Optional[int] = None,
        fill_price: Optional[float] = None,
    ) -> Fill:
        """Simulate a fill on an order.

        Args:
            order_id: Order to fill.
            fill_size: Size to fill (defaults to remaining size).
            fill_price: Price of fill (defaults to order price).

        Returns:
            The generated Fill object.

        Raises:
            OrderError: If order not found or already fully filled.
        """
        if order_id not in self._orders:
            raise OrderError(f"Order not found: {order_id}")

        order = self._orders[order_id]
        if order.status in ("filled", "cancelled"):
            raise OrderError(f"Order already {order.status}")

        remaining = order.size - order.filled_size
        if remaining <= 0:
            raise OrderError("Order already fully filled")

        actual_fill_size = min(fill_size or remaining, remaining)
        actual_fill_price = fill_price or order.price

        fill = Fill(
            order_id=order_id,
            ticker=order.ticker,
            side=order.side,
            price=actual_fill_price,
            size=actual_fill_size,
            timestamp=utc_now(),
        )
        self._fills.append(fill)

        order.filled_size += actual_fill_size
        if order.filled_size >= order.size:
            order.status = "filled"
        else:
            order.status = "partial"

        # Update position
        position_delta = actual_fill_size if order.side == "BID" else -actual_fill_size
        self._positions[order.ticker] = (
            self._positions.get(order.ticker, 0) + position_delta
        )

        return fill

    def set_market_data(self, state: MarketState) -> None:
        """Set mock market data for a ticker.

        Args:
            state: MarketState to store.
        """
        self._market_data[state.ticker] = state

    def set_rate_limit(self, seconds: float) -> None:
        """Simulate rate limiting for a duration.

        Args:
            seconds: Duration to rate limit.
        """
        from datetime import timedelta

        self.rate_limit_until = utc_now() + timedelta(seconds=seconds)

    def clear_rate_limit(self) -> None:
        """Clear any active rate limit."""
        self.rate_limit_until = None

    def reset(self) -> None:
        """Reset all mock state."""
        self._orders.clear()
        self._positions.clear()
        self._fills.clear()
        self._market_data.clear()
        self.fail_next_place = False
        self.fail_next_cancel = False
        self.fail_next_status = False
        self.rate_limit_until = None

    def get_order(self, order_id: str) -> Optional[MockOrder]:
        """Get raw MockOrder object for testing inspection.

        Args:
            order_id: Order ID to retrieve.

        Returns:
            MockOrder or None if not found.
        """
        return self._orders.get(order_id)
