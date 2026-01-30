"""Simulated API client for testing trading algorithms.

Provides a complete mock of the trading API that works with MarketSimulator
to enable realistic backtesting and strategy development.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from src.core.interfaces import APIClient
from src.core.models import Fill, MarketState, Quote

from .market_simulator import MarketSimulator


@dataclass
class OrderRecord:
    """Internal record of an order for tracking."""

    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    status: str = "OPEN"
    filled_size: int = 0
    fills: list[Fill] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def remaining_size(self) -> int:
        """Get unfilled size."""
        return self.size - self.filled_size

    def to_dict(self) -> dict:
        """Convert to dictionary for status response."""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "status": self.status,
            "filled_size": self.filled_size,
            "remaining_size": self.remaining_size,
            "fills": [
                {
                    "fill_id": f.fill_id,
                    "price": f.price,
                    "size": f.size,
                    "timestamp": f.timestamp.isoformat() if f.timestamp else None,
                }
                for f in self.fills
            ],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


class SimulatedAPIClient(APIClient):
    """Simulates trading API behavior for testing.

    Works with a MarketSimulator to provide realistic order matching
    and fill simulation.

    Attributes:
        simulator: MarketSimulator providing market data
        orders: Dictionary of order_id to OrderRecord
        fill_probability: Probability of fill check succeeding (for partial fills)
        latency_ms: Simulated latency in milliseconds (not actively used)
    """

    def __init__(
        self,
        simulator: MarketSimulator,
        fill_probability: float = 1.0,
        latency_ms: int = 0,
    ) -> None:
        """Initialize the simulated API client.

        Args:
            simulator: MarketSimulator to use for market data
            fill_probability: Probability of fills occurring when price crosses (0-1)
            latency_ms: Simulated network latency (for future use)
        """
        if not isinstance(simulator, MarketSimulator):
            raise TypeError(f"simulator must be MarketSimulator, got {type(simulator).__name__}")
        if not 0.0 <= fill_probability <= 1.0:
            raise ValueError(f"fill_probability must be between 0 and 1, got {fill_probability}")

        self.simulator = simulator
        self.fill_probability = fill_probability
        self.latency_ms = latency_ms
        self.orders: dict[str, OrderRecord] = {}
        self._fill_history: list[Fill] = []

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place an order in the simulated market.

        Args:
            ticker: Market identifier
            side: Order side ('buy', 'sell', 'BID', or 'ASK')
            price: Limit price (0-1 as probability)
            size: Number of contracts

        Returns:
            Generated order ID

        Raises:
            ValueError: If parameters are invalid
        """
        # Validate inputs
        if not ticker:
            raise ValueError("ticker cannot be empty")

        # Normalize side
        normalized_side = self._normalize_side(side)

        if price < 0:
            raise ValueError(f"price cannot be negative, got {price}")
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")

        # Generate order ID
        order_id = str(uuid.uuid4())

        # Create order record
        order = OrderRecord(
            order_id=order_id,
            ticker=ticker,
            side=normalized_side,
            price=price,
            size=size,
            status="OPEN",
        )

        self.orders[order_id] = order

        # Check for immediate fill
        self._check_fills(order_id)

        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order.

        Args:
            order_id: ID of order to cancel

        Returns:
            True if canceled, False if not found or already filled
        """
        if order_id not in self.orders:
            return False

        order = self.orders[order_id]

        if order.status in ("FILLED", "CANCELED"):
            return False

        order.status = "CANCELED"
        order.updated_at = datetime.now()
        return True

    def get_order_status(self, order_id: str) -> dict:
        """Get current status of an order.

        Args:
            order_id: ID of order to check

        Returns:
            Dictionary with order status details

        Raises:
            ValueError: If order_id not found
        """
        if order_id not in self.orders:
            raise ValueError(f"Order not found: {order_id}")

        # Check for fills before returning status
        self._check_fills(order_id)

        return self.orders[order_id].to_dict()

    def get_market_data(self, ticker: str) -> MarketState:
        """Get current market data for a ticker.

        Args:
            ticker: Market identifier

        Returns:
            Current MarketState from simulator

        Raises:
            ValueError: If ticker doesn't match simulator
        """
        return self.simulator.get_market_state(ticker)

    def step(self) -> MarketState:
        """Advance simulation by one step and check all orders.

        This is the main simulation loop method. Call this to:
        1. Generate new market state
        2. Check all open orders for fills

        Returns:
            New MarketState after step
        """
        # Generate new market state
        market = self.simulator.generate_market_state()

        # Check all open orders for fills
        for order_id in list(self.orders.keys()):
            self._check_fills(order_id)

        return market

    def run_steps(self, n: int) -> list[MarketState]:
        """Run n simulation steps.

        Args:
            n: Number of steps to run

        Returns:
            List of MarketState objects generated
        """
        states = []
        for _ in range(n):
            states.append(self.step())
        return states

    def get_open_orders(self, ticker: Optional[str] = None) -> list[dict]:
        """Get all open orders, optionally filtered by ticker.

        Args:
            ticker: Optional ticker to filter by

        Returns:
            List of order status dictionaries
        """
        result = []
        for order in self.orders.values():
            if order.status not in ("FILLED", "CANCELED"):
                if ticker is None or order.ticker == ticker:
                    result.append(order.to_dict())
        return result

    def get_all_fills(self, ticker: Optional[str] = None) -> list[Fill]:
        """Get all fills, optionally filtered by ticker.

        Args:
            ticker: Optional ticker to filter by

        Returns:
            List of Fill objects
        """
        if ticker is None:
            return list(self._fill_history)
        return [f for f in self._fill_history if f.ticker == ticker]

    def reset(self) -> None:
        """Reset the simulated API state."""
        self.orders.clear()
        self._fill_history.clear()
        self.simulator.reset()

    def _normalize_side(self, side: str) -> str:
        """Normalize order side to BID/ASK."""
        side_upper = side.upper()
        if side_upper in ("BUY", "BID"):
            return "BID"
        elif side_upper in ("SELL", "ASK"):
            return "ASK"
        else:
            raise ValueError(f"Invalid side: {side}, must be 'buy', 'sell', 'BID', or 'ASK'")

    def _check_fills(self, order_id: str) -> None:
        """Check if an order should fill against current market."""
        if order_id not in self.orders:
            return

        order = self.orders[order_id]

        if order.status in ("FILLED", "CANCELED"):
            return

        # Get current market state
        try:
            market = self.simulator.get_market_state(order.ticker)
        except ValueError:
            return  # Ticker mismatch

        # Create a Quote for fill checking
        quote = Quote(
            ticker=order.ticker,
            side=order.side,
            price=order.price,
            size=order.size,
            order_id=order_id,
            status="OPEN",
            filled_size=order.filled_size,
        )

        # Check for fill
        fill = self.simulator.simulate_fill(quote, market)

        if fill is not None:
            # Apply fill probability
            import random
            if random.random() > self.fill_probability:
                return

            # Record fill
            order.fills.append(fill)
            order.filled_size += fill.size
            order.updated_at = datetime.now()
            self._fill_history.append(fill)

            # Update status
            if order.filled_size >= order.size:
                order.status = "FILLED"
            else:
                order.status = "PARTIALLY_FILLED"
