"""Polymarket CLOB API client - DEMO VERSION.

This demo version uses mock implementations.
No real API calls are made.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.interfaces import APIClient
from src.core.models import MarketState

logger = logging.getLogger(__name__)


class MockPolymarketClient(APIClient):
    """Mock Polymarket client for demo mode.

    This client simulates the Polymarket API without making real network requests.
    """

    def __init__(self, initial_balance: float = 1000.0):
        """Initialize mock client.

        Args:
            initial_balance: Starting USDC balance
        """
        self._balance = initial_balance
        self._positions: Dict[str, int] = {}
        self._orders: Dict[str, dict] = {}
        self._market_data: Dict[str, MarketState] = {}
        logger.info("MockPolymarketClient initialized (DEMO MODE)")

    def connect(self) -> "MockPolymarketClient":
        """Connect (no-op in mock)."""
        logger.info("MockPolymarketClient connected (DEMO MODE)")
        return self

    def disconnect(self) -> None:
        """Disconnect (no-op in mock)."""
        logger.info("MockPolymarketClient disconnected")

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place a mock order.

        Args:
            ticker: Asset/token ID
            side: 'BID', 'ASK', 'buy', or 'sell'
            price: Limit price (0-1)
            size: Number of contracts

        Returns:
            Mock order ID
        """
        import uuid
        order_id = str(uuid.uuid4())
        self._orders[order_id] = {
            "ticker": ticker,
            "side": side,
            "price": price,
            "size": size,
            "status": "OPEN",
            "filled_size": 0,
        }
        logger.info(f"DEMO: Mock order placed: {order_id}")
        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a mock order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if found and canceled
        """
        if order_id in self._orders:
            self._orders[order_id]["status"] = "CANCELED"
            return True
        return False

    def get_order_status(self, order_id: str) -> dict:
        """Get mock order status.

        Args:
            order_id: Order ID

        Returns:
            Order status dictionary
        """
        if order_id not in self._orders:
            raise ValueError(f"Order not found: {order_id}")
        return self._orders[order_id]

    def get_market_data(self, ticker: str) -> MarketState:
        """Get mock market data.

        Args:
            ticker: Asset/token ID

        Returns:
            MarketState with simulated data
        """
        if ticker in self._market_data:
            return self._market_data[ticker]

        # Return simulated data
        return MarketState(
            ticker=ticker,
            timestamp=datetime.now(),
            bid=0.48,
            ask=0.52,
            last_price=0.50,
            volume=1000,
        )

    def get_balance(self) -> float:
        """Get mock balance.

        Returns:
            Simulated USDC balance
        """
        return self._balance

    def get_markets(
        self,
        limit: int = 100,
        next_cursor: Optional[str] = None,
    ) -> List[dict]:
        """Get mock markets list.

        Returns:
            Empty list in demo mode
        """
        return []

    def cancel_all_orders(self, market: Optional[str] = None) -> int:
        """Cancel all mock orders.

        Returns:
            Number of orders canceled
        """
        canceled = 0
        for order_id, order in self._orders.items():
            if order["status"] == "OPEN":
                if market is None or order.get("market") == market:
                    order["status"] = "CANCELED"
                    canceled += 1
        return canceled


class PolymarketClient:
    """Polymarket API client wrapper - DEMO VERSION.

    In demo mode, this class always returns MockPolymarketClient instances.
    """

    def __new__(cls, *args, **kwargs):
        """Always return MockPolymarketClient in demo mode."""
        logger.info("DEMO MODE: Using MockPolymarketClient instead of real API")
        return MockPolymarketClient()

    @classmethod
    def from_env(cls) -> MockPolymarketClient:
        """Create client from environment variables.

        DEMO: Always returns MockPolymarketClient.
        """
        return MockPolymarketClient()


__all__ = ["PolymarketClient", "MockPolymarketClient"]
