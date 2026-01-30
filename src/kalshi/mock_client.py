"""Mock Kalshi API client for testing and replay.

This module provides a mock implementation of the KalshiClient that can be:
1. Fed market data from a replay system
2. Used for unit testing strategies
3. Used for backtesting with recorded data

The mock client simulates order placement, cancellation, and fills without
connecting to the real Kalshi API.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.interfaces import APIClient
from ..core.models import Fill, MarketState


@dataclass
class MockOrder:
    """Internal representation of a mock order."""

    order_id: str
    ticker: str
    side: str  # "YES" or "NO"
    action: str  # "buy" or "sell"
    price: float  # 0-1 probability
    size: int
    status: str = "open"  # "open", "filled", "partially_filled", "canceled"
    filled_size: int = 0
    fills: List[Fill] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    @property
    def remaining_size(self) -> int:
        """Get unfilled size."""
        return self.size - self.filled_size


class MockKalshiClient(APIClient):
    """Mock Kalshi client for testing and replay.

    This client simulates the Kalshi API without making real network requests.
    It can be fed market data from a replay system or configured manually.

    Features:
    - Simulates order placement and cancellation
    - Tracks positions and balance
    - Supports immediate fills based on market prices
    - Can be updated with market data from replay

    Example:
        mock = MockKalshiClient(initial_balance=10000)

        # Update market data (from replay)
        mock.update_market("TICKER", market_state)

        # Place order
        order_id = await mock.place_order_async("TICKER", "buy", 0.45, 10)

        # Check fills
        for fill in mock.get_fills():
            print(f"Filled: {fill}")
    """

    def __init__(
        self,
        initial_balance: int = 100000,  # cents
        fill_probability: float = 1.0,
        simulate_latency_ms: int = 0,
        auto_fill: bool = True,
    ):
        """Initialize the mock client.

        Args:
            initial_balance: Starting balance in cents
            fill_probability: Probability of order filling when price crosses (0-1)
            simulate_latency_ms: Simulated network latency in ms
            auto_fill: If True, orders fill immediately when price crosses
        """
        self._balance: int = initial_balance
        self._initial_balance: int = initial_balance
        self._fill_probability: float = fill_probability
        self._latency_ms: int = simulate_latency_ms
        self._auto_fill: bool = auto_fill

        # State
        self._market_states: Dict[str, MarketState] = {}
        self._orders: Dict[str, MockOrder] = {}
        self._positions: Dict[str, int] = {}  # ticker -> position (positive = long YES)
        self._fills: List[Fill] = []

        # Callbacks for order events
        self._on_fill_callbacks: List = []

    # ==================== Market Data Methods ====================

    def update_market(self, ticker: str, state: MarketState) -> None:
        """Update market state for a ticker (called by replay system).

        Args:
            ticker: Market ticker
            state: New market state
        """
        self._market_states[ticker] = state

        # Check for fills on open orders
        if self._auto_fill:
            self._check_fills_for_ticker(ticker)

    def get_market_state(self, ticker: str) -> Optional[MarketState]:
        """Get current market state for a ticker.

        Args:
            ticker: Market ticker

        Returns:
            Current MarketState or None if not found
        """
        return self._market_states.get(ticker)

    # ==================== APIClient Interface ====================

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place an order (sync wrapper).

        Args:
            ticker: Market ticker
            side: Order side ('buy', 'sell', 'yes', 'no')
            price: Limit price as decimal (0-1)
            size: Number of contracts

        Returns:
            Order ID string
        """
        return asyncio.get_event_loop().run_until_complete(
            self.place_order_async(ticker, side, price, size)
        )

    async def place_order_async(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
        order_type: str = "limit",
        client_order_id: Optional[str] = None,
    ) -> str:
        """Place an order asynchronously.

        Args:
            ticker: Market ticker
            side: Order side ('buy', 'sell', 'yes', 'no', 'BID', 'ASK')
            price: Limit price as decimal (0-1)
            size: Number of contracts
            order_type: 'limit' or 'market'
            client_order_id: Optional client-provided order ID

        Returns:
            Order ID string
        """
        # Simulate latency
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        # Normalize side
        side_lower = side.lower()
        if side_lower in ("buy", "bid", "yes"):
            kalshi_side = "YES"
            action = "buy"
        elif side_lower in ("sell", "ask", "no"):
            kalshi_side = "NO"
            action = "sell"
        else:
            raise ValueError(f"Invalid side: {side}")

        # Validate price
        if price < 0.01 or price > 0.99:
            raise ValueError(f"Price must be 0.01-0.99, got {price}")

        # Generate order ID
        order_id = client_order_id or str(uuid.uuid4())

        # Create order
        order = MockOrder(
            order_id=order_id,
            ticker=ticker,
            side=kalshi_side,
            action=action,
            price=price,
            size=size,
        )

        self._orders[order_id] = order

        # Check for immediate fill
        if self._auto_fill:
            self._check_fills_for_order(order_id)

        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order (sync wrapper).

        Args:
            order_id: Order ID to cancel

        Returns:
            True if canceled, False if not found or already filled
        """
        return asyncio.get_event_loop().run_until_complete(
            self.cancel_order_async(order_id)
        )

    async def cancel_order_async(self, order_id: str) -> bool:
        """Cancel an order asynchronously.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if canceled successfully
        """
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        if order_id not in self._orders:
            return False

        order = self._orders[order_id]

        if order.status in ("filled", "canceled"):
            return False

        order.status = "canceled"
        order.updated_at = datetime.now()
        return True

    def get_order_status(self, order_id: str) -> dict:
        """Get order status (sync wrapper).

        Args:
            order_id: Order ID

        Returns:
            Order status dictionary
        """
        return asyncio.get_event_loop().run_until_complete(
            self.get_order_status_async(order_id)
        )

    async def get_order_status_async(self, order_id: str) -> dict:
        """Get order status asynchronously.

        Args:
            order_id: Order ID

        Returns:
            Dictionary with order status
        """
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        if order_id not in self._orders:
            raise ValueError(f"Order not found: {order_id}")

        # Check for fills before returning
        if self._auto_fill:
            self._check_fills_for_order(order_id)

        order = self._orders[order_id]

        # Map status
        status = order.status.upper()
        if order.filled_size >= order.size:
            status = "FILLED"
        elif order.filled_size > 0:
            status = "PARTIALLY_FILLED"
        elif order.status == "canceled":
            status = "CANCELED"
        else:
            status = "OPEN"

        return {
            "order_id": order.order_id,
            "ticker": order.ticker,
            "side": order.side,
            "action": order.action,
            "price": order.price,
            "size": order.size,
            "status": status,
            "filled_size": order.filled_size,
            "remaining_size": order.remaining_size,
            "fills": [
                {
                    "fill_id": f.fill_id,
                    "price": f.price,
                    "size": f.size,
                    "timestamp": f.timestamp.isoformat() if f.timestamp else None,
                }
                for f in order.fills
            ],
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat(),
        }

    def get_market_data(self, ticker: str) -> MarketState:
        """Get market data (sync wrapper).

        Args:
            ticker: Market ticker

        Returns:
            MarketState instance
        """
        return asyncio.get_event_loop().run_until_complete(
            self.get_market_data_async(ticker)
        )

    async def get_market_data_async(self, ticker: str) -> MarketState:
        """Get market data asynchronously.

        Args:
            ticker: Market ticker

        Returns:
            MarketState instance
        """
        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        state = self._market_states.get(ticker)

        if state is None:
            # Return default state if not set
            return MarketState(
                ticker=ticker,
                timestamp=datetime.now(),
                bid=0.45,
                ask=0.55,
                last_price=0.50,
                volume=0,
            )

        return state

    # ==================== Additional Methods ====================

    def get_balance(self) -> int:
        """Get current balance in cents.

        Returns:
            Balance in cents
        """
        return self._balance

    def get_position(self, ticker: str) -> int:
        """Get position for a ticker.

        Args:
            ticker: Market ticker

        Returns:
            Position size (positive = long YES, negative = long NO)
        """
        return self._positions.get(ticker, 0)

    def get_all_positions(self) -> Dict[str, int]:
        """Get all positions.

        Returns:
            Dict of ticker -> position
        """
        return self._positions.copy()

    def get_fills(self, ticker: Optional[str] = None) -> List[Fill]:
        """Get all fills, optionally filtered by ticker.

        Args:
            ticker: Optional ticker to filter by

        Returns:
            List of Fill objects
        """
        if ticker:
            return [f for f in self._fills if f.ticker == ticker]
        return self._fills.copy()

    def get_open_orders(self, ticker: Optional[str] = None) -> List[dict]:
        """Get all open orders.

        Args:
            ticker: Optional ticker to filter by

        Returns:
            List of order status dicts
        """
        result = []
        for order in self._orders.values():
            if order.status not in ("filled", "canceled"):
                if ticker is None or order.ticker == ticker:
                    result.append({
                        "order_id": order.order_id,
                        "ticker": order.ticker,
                        "side": order.side,
                        "action": order.action,
                        "price": order.price,
                        "size": order.size,
                        "status": order.status,
                        "filled_size": order.filled_size,
                        "remaining_size": order.remaining_size,
                    })
        return result

    async def cancel_all_orders(self, ticker: Optional[str] = None) -> int:
        """Cancel all open orders.

        Args:
            ticker: Optional ticker to filter by

        Returns:
            Number of orders canceled
        """
        canceled = 0
        for order_id, order in list(self._orders.items()):
            if order.status not in ("filled", "canceled"):
                if ticker is None or order.ticker == ticker:
                    if await self.cancel_order_async(order_id):
                        canceled += 1
        return canceled

    def on_fill(self, callback) -> None:
        """Register a callback for fills.

        Args:
            callback: Function to call with Fill objects
        """
        self._on_fill_callbacks.append(callback)

    def reset(self) -> None:
        """Reset all state to initial values."""
        self._balance = self._initial_balance
        self._market_states.clear()
        self._orders.clear()
        self._positions.clear()
        self._fills.clear()

    def set_balance(self, balance: int) -> None:
        """Set balance directly (for testing).

        Args:
            balance: New balance in cents
        """
        self._balance = balance

    def set_position(self, ticker: str, position: int) -> None:
        """Set position directly (for testing).

        Args:
            ticker: Market ticker
            position: Position size
        """
        self._positions[ticker] = position

    # ==================== Internal Methods ====================

    def _check_fills_for_ticker(self, ticker: str) -> None:
        """Check all orders for a ticker for potential fills."""
        for order_id, order in list(self._orders.items()):
            if order.ticker == ticker and order.status == "open":
                self._check_fills_for_order(order_id)

    def _check_fills_for_order(self, order_id: str) -> None:
        """Check if an order should fill based on current market prices."""
        import random

        if order_id not in self._orders:
            return

        order = self._orders[order_id]

        if order.status in ("filled", "canceled"):
            return

        market = self._market_states.get(order.ticker)
        if not market:
            return

        should_fill = False
        fill_price = order.price

        # Check if order crosses the market
        if order.side == "YES" and order.action == "buy":
            # Buying YES - fills if bid >= ask
            if order.price >= market.ask:
                should_fill = True
                fill_price = market.ask
        elif order.side == "NO" and order.action == "buy":
            # Buying NO - fills if (1-price) >= (1-bid) => price <= bid
            if order.price <= market.bid:
                should_fill = True
                fill_price = market.bid
        elif order.side == "YES" and order.action == "sell":
            # Selling YES - fills if ask <= bid
            if order.price <= market.bid:
                should_fill = True
                fill_price = market.bid
        elif order.side == "NO" and order.action == "sell":
            # Selling NO
            if order.price >= market.ask:
                should_fill = True
                fill_price = market.ask

        if should_fill and random.random() <= self._fill_probability:
            self._execute_fill(order, fill_price)

    def _execute_fill(self, order: MockOrder, fill_price: float) -> None:
        """Execute a fill for an order.

        Args:
            order: Order to fill
            fill_price: Price at which to fill
        """
        remaining = order.remaining_size
        if remaining <= 0:
            return

        # Create fill
        fill = Fill(
            ticker=order.ticker,
            side="BID" if order.action == "buy" else "ASK",
            price=fill_price,
            size=remaining,
            order_id=order.order_id,
            fill_id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            fee=0.0,  # Mock doesn't charge fees
        )

        # Update order
        order.fills.append(fill)
        order.filled_size += remaining
        order.status = "filled"
        order.updated_at = datetime.now()

        # Update position
        position_change = remaining if order.side == "YES" else -remaining
        if order.action == "sell":
            position_change = -position_change

        current_pos = self._positions.get(order.ticker, 0)
        self._positions[order.ticker] = current_pos + position_change

        # Update balance
        cost_cents = int(fill_price * remaining * 100)
        if order.action == "buy":
            self._balance -= cost_cents
        else:
            self._balance += cost_cents

        # Record fill
        self._fills.append(fill)

        # Fire callbacks
        for callback in self._on_fill_callbacks:
            try:
                callback(fill)
            except Exception as e:
                print(f"[MockClient] Fill callback error: {e}")

    def get_pnl(self) -> float:
        """Calculate current P&L in dollars.

        Returns:
            P&L in dollars
        """
        # Realized P&L from balance change
        realized_pnl = (self._balance - self._initial_balance) / 100.0

        # Unrealized P&L from open positions
        unrealized_pnl = 0.0
        for ticker, position in self._positions.items():
            market = self._market_states.get(ticker)
            if market and position != 0:
                # Mark to mid price
                mid = (market.bid + market.ask) / 2
                unrealized_pnl += position * mid

        return realized_pnl + unrealized_pnl
