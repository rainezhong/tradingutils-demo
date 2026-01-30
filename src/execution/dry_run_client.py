"""Dry run API client for testing algorithms without executing real trades.

Wraps a real API client to read live market data but logs orders instead
of placing them. Useful for testing strategies against real market conditions
without risking capital.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core.utils import utc_now
from ..market_making.interfaces import APIClient, OrderError
from ..market_making.models import Fill, MarketState


logger = logging.getLogger(__name__)


@dataclass
class DryRunOrder:
    """Record of an order that would have been placed."""

    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    timestamp: datetime
    would_fill: bool = False
    simulated_fill_price: Optional[float] = None
    simulated_fill_size: int = 0
    status: str = "simulated"

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/export."""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp.isoformat(),
            "would_fill": self.would_fill,
            "simulated_fill_price": self.simulated_fill_price,
            "simulated_fill_size": self.simulated_fill_size,
            "status": self.status,
        }


@dataclass
class DryRunStats:
    """Statistics for a dry run session."""

    started_at: datetime = field(default_factory=utc_now)
    orders_would_place: int = 0
    orders_would_fill: int = 0
    total_volume: int = 0
    total_notional: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for reporting."""
        return {
            "started_at": self.started_at.isoformat(),
            "duration_seconds": (utc_now() - self.started_at).total_seconds(),
            "orders_would_place": self.orders_would_place,
            "orders_would_fill": self.orders_would_fill,
            "total_volume": self.total_volume,
            "total_notional": self.total_notional,
        }


class DryRunAPIClient(APIClient):
    """API client wrapper that logs orders but doesn't execute them.

    Wraps a real APIClient to:
    - Pass through all read operations (market data, positions)
    - Intercept and log order placements without execution
    - Simulate fill behavior based on current market conditions
    - Track statistics for analysis

    Example:
        >>> real_client = KalshiAPIClient(...)
        >>> dry_run = DryRunAPIClient(real_client)
        >>>
        >>> # This reads real market data
        >>> market = dry_run.get_market_data("TICKER")
        >>>
        >>> # This logs the order but doesn't place it
        >>> order_id = dry_run.place_order("TICKER", "BID", 0.45, 20)
        >>> print(f"Would have placed order: {order_id}")
        >>>
        >>> # Get session statistics
        >>> print(dry_run.get_stats())

    Attributes:
        real_client: The underlying real API client for market data.
        simulate_fills: If True, simulate whether orders would fill.
        log_orders: If True, log each order to the logger.
    """

    def __init__(
        self,
        real_client: Optional[APIClient] = None,
        simulate_fills: bool = True,
        log_orders: bool = True,
    ) -> None:
        """Initialize dry run client.

        Args:
            real_client: Real API client for market data. If None, market data
                operations will raise errors.
            simulate_fills: Whether to simulate fill behavior based on market.
            log_orders: Whether to log each order placement.
        """
        self._real_client = real_client
        self._simulate_fills = simulate_fills
        self._log_orders = log_orders

        self._orders: dict[str, DryRunOrder] = {}
        self._positions: dict[str, int] = {}
        self._fills: list[Fill] = []
        self._stats = DryRunStats()
        self._last_market_data: dict[str, MarketState] = {}

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Log an order that would have been placed.

        Args:
            ticker: Market identifier.
            side: 'BID' or 'ASK'.
            price: Order price (0-1 range).
            size: Number of contracts.

        Returns:
            Generated simulated order ID.
        """
        order_id = f"dryrun_{uuid.uuid4().hex[:12]}"

        order = DryRunOrder(
            order_id=order_id,
            ticker=ticker,
            side=side,
            price=price,
            size=size,
            timestamp=utc_now(),
        )

        # Check if order would fill based on last known market data
        if self._simulate_fills and ticker in self._last_market_data:
            market = self._last_market_data[ticker]
            would_fill, fill_price = self._check_would_fill(order, market)
            order.would_fill = would_fill
            if would_fill:
                order.simulated_fill_price = fill_price
                order.simulated_fill_size = size
                order.status = "would_fill"
                self._stats.orders_would_fill += 1

                # Simulate position update
                position_delta = size if side == "BID" else -size
                self._positions[ticker] = self._positions.get(ticker, 0) + position_delta

                # Create simulated fill
                fill = Fill(
                    order_id=order_id,
                    ticker=ticker,
                    side=side,
                    price=fill_price,
                    size=size,
                    timestamp=utc_now(),
                )
                self._fills.append(fill)

        self._orders[order_id] = order
        self._stats.orders_would_place += 1
        self._stats.total_volume += size
        self._stats.total_notional += price * size

        if self._log_orders:
            fill_status = " (WOULD FILL)" if order.would_fill else ""
            logger.info(
                "[DRY RUN] Would place order: %s %s %d @ %.4f%s",
                side,
                ticker,
                size,
                price,
                fill_status,
            )

        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Simulate canceling an order.

        Args:
            order_id: Order ID to cancel.

        Returns:
            True (always succeeds in dry run).
        """
        if order_id in self._orders:
            order = self._orders[order_id]
            order.status = "cancelled"

            if self._log_orders:
                logger.info("[DRY RUN] Would cancel order: %s", order_id)

            return True
        return False

    def get_order_status(self, order_id: str) -> dict:
        """Get status of a simulated order.

        Args:
            order_id: Order ID to check.

        Returns:
            Status dict with simulated order state.

        Raises:
            OrderError: If order not found.
        """
        if order_id not in self._orders:
            raise OrderError(f"Order not found: {order_id}")

        order = self._orders[order_id]

        return {
            "status": "filled" if order.would_fill else order.status,
            "filled_size": order.simulated_fill_size,
            "remaining_size": order.size - order.simulated_fill_size,
            "avg_fill_price": order.simulated_fill_price,
        }

    def get_market_data(self, ticker: str) -> MarketState:
        """Get real market data from underlying client.

        Args:
            ticker: Market identifier.

        Returns:
            MarketState from real exchange.

        Raises:
            OrderError: If no real client or market data unavailable.
        """
        if self._real_client is None:
            raise OrderError("No real client configured for market data")

        market = self._real_client.get_market_data(ticker)
        self._last_market_data[ticker] = market
        return market

    def get_positions(self) -> dict[str, int]:
        """Get simulated positions from dry run session.

        Returns:
            Dictionary mapping ticker to simulated contract count.
        """
        return dict(self._positions)

    def get_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
    ) -> list[Fill]:
        """Get simulated fills.

        Args:
            ticker: Filter by ticker (optional).
            limit: Maximum fills to return.

        Returns:
            List of simulated Fill objects.
        """
        fills = self._fills
        if ticker:
            fills = [f for f in fills if f.ticker == ticker]
        return sorted(fills, key=lambda f: f.timestamp, reverse=True)[:limit]

    def _check_would_fill(
        self,
        order: DryRunOrder,
        market: MarketState,
    ) -> tuple[bool, float]:
        """Check if an order would fill given current market conditions.

        Args:
            order: The order to check.
            market: Current market state.

        Returns:
            Tuple of (would_fill, fill_price).
        """
        if order.side == "BID":
            # Bid fills if price >= best ask
            if market.best_ask is not None and order.price >= market.best_ask:
                return True, market.best_ask
        else:
            # Ask fills if price <= best bid
            if market.best_bid is not None and order.price <= market.best_bid:
                return True, market.best_bid

        return False, order.price

    def get_stats(self) -> DryRunStats:
        """Get dry run session statistics.

        Returns:
            DryRunStats with session metrics.
        """
        return self._stats

    def get_orders(self) -> list[DryRunOrder]:
        """Get all orders from this dry run session.

        Returns:
            List of DryRunOrder objects.
        """
        return list(self._orders.values())

    def print_summary(self) -> None:
        """Print a summary of the dry run session."""
        stats = self._stats.to_dict()

        print("\n" + "=" * 60)
        print("DRY RUN SUMMARY")
        print("=" * 60)
        print(f"Duration: {stats['duration_seconds']:.1f} seconds")
        print(f"Orders would place: {stats['orders_would_place']}")
        print(f"Orders would fill: {stats['orders_would_fill']}")
        print(f"Total volume: {stats['total_volume']} contracts")
        print(f"Total notional: ${stats['total_notional']:.2f}")
        print()

        if self._positions:
            print("Simulated Positions:")
            for ticker, size in self._positions.items():
                print(f"  {ticker}: {size} contracts")

        print("=" * 60)

    def reset(self) -> None:
        """Reset all dry run state for a new session."""
        self._orders.clear()
        self._positions.clear()
        self._fills.clear()
        self._stats = DryRunStats()
        self._last_market_data.clear()


class DryRunExchangeClient:
    """ExchangeClient wrapper that logs orders but doesn't execute them.

    Wraps a real ExchangeClient for use with the OMS and arbitrage execution
    systems. All read operations pass through to the real client, while
    order placement is simulated.

    Example:
        >>> real_client = KalshiExchangeClient(...)
        >>> dry_run = DryRunExchangeClient(real_client)
        >>>
        >>> # Register with OMS
        >>> oms = OrderManagementSystem()
        >>> oms.register_exchange(dry_run)
        >>>
        >>> # Orders will be logged but not placed
        >>> order = oms.submit_order("kalshi_dry", "TICKER", "buy", 0.45, 10)
    """

    def __init__(
        self,
        real_client,
        simulate_fills: bool = True,
        log_orders: bool = True,
    ) -> None:
        """Initialize dry run exchange client.

        Args:
            real_client: Real ExchangeClient for market data.
            simulate_fills: Whether to simulate fill behavior.
            log_orders: Whether to log each order.
        """
        self._real_client = real_client
        self._simulate_fills = simulate_fills
        self._log_orders = log_orders

        self._orders: dict[str, "DryRunExchangeOrder"] = {}
        self._positions: dict[str, int] = {}
        self._stats = DryRunStats()

    @property
    def name(self) -> str:
        """Exchange name with _dry suffix."""
        return f"{self._real_client.name}_dry"

    # Pass through read operations
    def get_market(self, ticker: str):
        """Pass through to real client."""
        return self._real_client.get_market(ticker)

    def get_markets(self, status: Optional[str] = None, limit: int = 100):
        """Pass through to real client."""
        return self._real_client.get_markets(status, limit)

    def get_balance(self) -> float:
        """Pass through to real client."""
        return self._real_client.get_balance()

    def get_all_positions(self):
        """Return simulated positions."""
        from ..core.models import Position
        result = {}
        for ticker, size in self._positions.items():
            if size != 0:
                result[ticker] = Position(
                    ticker=ticker,
                    size=size,
                    entry_price=0.0,
                    current_price=0.0,
                )
        return result

    def get_all_orders(self, status: Optional[str] = None):
        """Return simulated orders."""
        from ..core.exchange import Order
        orders = []
        for order in self._orders.values():
            if status is None or order.status == status:
                orders.append(Order(
                    order_id=order.order_id,
                    ticker=order.ticker,
                    side=order.side,
                    price=order.price,
                    size=order.size,
                    filled_size=order.filled_size,
                    status=order.status,
                    exchange=self.name,
                ))
        return orders

    def _place_order(self, ticker: str, side: str, price: float, size: int):
        """Simulate placing an order."""
        from ..core.exchange import Order

        order_id = f"dryrun_{uuid.uuid4().hex[:12]}"

        # Get current market data to check if order would fill
        would_fill = False
        fill_price = price
        filled_size = 0

        if self._simulate_fills:
            try:
                orderbook = self._real_client._get_orderbook(ticker)
                if side == "buy":
                    if orderbook.best_ask is not None and price >= orderbook.best_ask:
                        would_fill = True
                        fill_price = orderbook.best_ask
                        filled_size = size
                else:
                    if orderbook.best_bid is not None and price <= orderbook.best_bid:
                        would_fill = True
                        fill_price = orderbook.best_bid
                        filled_size = size
            except Exception:
                pass

        status = "filled" if would_fill else "open"

        dry_order = DryRunExchangeOrder(
            order_id=order_id,
            ticker=ticker,
            side=side,
            price=price,
            size=size,
            filled_size=filled_size,
            status=status,
            timestamp=utc_now(),
        )
        self._orders[order_id] = dry_order

        # Update simulated positions
        if would_fill:
            position_delta = size if side == "buy" else -size
            self._positions[ticker] = self._positions.get(ticker, 0) + position_delta
            self._stats.orders_would_fill += 1

        self._stats.orders_would_place += 1
        self._stats.total_volume += size

        if self._log_orders:
            fill_status = " (WOULD FILL)" if would_fill else ""
            logger.info(
                "[DRY RUN] Would place order: %s %s %d @ %.2f%s",
                side,
                ticker,
                size,
                price,
                fill_status,
            )

        return Order(
            order_id=order_id,
            ticker=ticker,
            side=side,
            price=price,
            size=size,
            filled_size=filled_size,
            status=status,
            exchange=self.name,
        )

    def _cancel_order(self, order_id: str) -> bool:
        """Simulate canceling an order."""
        if order_id in self._orders:
            self._orders[order_id].status = "canceled"
            if self._log_orders:
                logger.info("[DRY RUN] Would cancel order: %s", order_id)
            return True
        return False

    def _get_orderbook(self, ticker: str):
        """Pass through to real client."""
        return self._real_client._get_orderbook(ticker)

    def _get_position(self, ticker: str):
        """Return simulated position."""
        from ..core.models import Position
        size = self._positions.get(ticker, 0)
        if size == 0:
            return None
        return Position(
            ticker=ticker,
            size=size,
            entry_price=0.0,
            current_price=0.0,
        )

    def _get_orders(self, ticker: str, status: Optional[str] = None):
        """Return simulated orders for ticker."""
        from ..core.exchange import Order
        orders = []
        for order in self._orders.values():
            if order.ticker == ticker:
                if status is None or order.status == status:
                    orders.append(Order(
                        order_id=order.order_id,
                        ticker=order.ticker,
                        side=order.side,
                        price=order.price,
                        size=order.size,
                        filled_size=order.filled_size,
                        status=order.status,
                        exchange=self.name,
                    ))
        return orders

    def _get_fills(self, ticker: str, limit: int = 100):
        """Return simulated fills."""
        from ..core.models import Fill
        fills = []
        for order in self._orders.values():
            if order.ticker == ticker and order.filled_size > 0:
                fills.append(Fill(
                    fill_id=f"fill_{order.order_id}",
                    order_id=order.order_id,
                    ticker=ticker,
                    side=order.side,
                    price=order.price,
                    size=order.filled_size,
                    timestamp=order.timestamp,
                ))
        return fills[:limit]

    def _get_market_data(self, ticker: str):
        """Pass through to real client."""
        return self._real_client._get_market_data(ticker)

    def get_stats(self) -> DryRunStats:
        """Get dry run session statistics."""
        return self._stats

    def print_summary(self) -> None:
        """Print a summary of the dry run session."""
        stats = self._stats.to_dict()

        print("\n" + "=" * 60)
        print(f"DRY RUN SUMMARY - {self.name}")
        print("=" * 60)
        print(f"Duration: {stats['duration_seconds']:.1f} seconds")
        print(f"Orders would place: {stats['orders_would_place']}")
        print(f"Orders would fill: {stats['orders_would_fill']}")
        print(f"Total volume: {stats['total_volume']} contracts")
        print()

        if self._positions:
            print("Simulated Positions:")
            for ticker, size in self._positions.items():
                if size != 0:
                    print(f"  {ticker}: {size} contracts")

        print("=" * 60)


@dataclass
class DryRunExchangeOrder:
    """Internal order tracking for DryRunExchangeClient."""

    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    filled_size: int = 0
    status: str = "open"
    timestamp: datetime = field(default_factory=utc_now)


if __name__ == "__main__":
    # Demo of the DryRunAPIClient without a real client
    print("Testing DryRunAPIClient...")
    
    client = DryRunAPIClient(real_client=None, simulate_fills=False)
    
    # Place some test orders
    order1 = client.place_order("TEST-TICKER", "BID", 0.45, 10)
    order2 = client.place_order("TEST-TICKER", "ASK", 0.55, 5)
    order3 = client.place_order("ANOTHER-TICKER", "BID", 0.30, 20)
    
    # Cancel one order
    client.cancel_order(order1)
    
    # Print summary
    client.print_summary()
    
    # Show all orders
    print("\nAll Orders:")
    for order in client.get_orders():
        print(f"  {order.to_dict()}")
