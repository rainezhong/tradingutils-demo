"""Abstract base class for order management."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from .order_manager_types import (
    OrderRequest,
    OrderStatus,
    Fill,
    Side,
)


class I_OrderManager(ABC):
    """Interface for order management systems.

    Handles order submission, tracking, and lifecycle management.
    """

    @abstractmethod
    async def submit_order(self, request: OrderRequest) -> str:
        """Submit an order to the exchange.

        Args:
            request: Order parameters

        Returns:
            Order ID from exchange
        """
        pass

    @abstractmethod
    async def buy(self, request: OrderRequest) -> str:
        """Submit a buy order.

        Args:
            request: Order parameters (action will be set to BUY)

        Returns:
            Order ID
        """
        pass

    @abstractmethod
    async def sell(self, request: OrderRequest) -> str:
        """Submit a sell order.

        Args:
            request: Order parameters (action will be set to SELL)

        Returns:
            Order ID
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order.

        Args:
            order_id: ID of order to cancel

        Returns:
            True if successfully canceled
        """
        pass

    @abstractmethod
    async def get_order_status(self, order_id: str) -> OrderStatus:
        """Get current status of an order.

        Args:
            order_id: Order ID to check

        Returns:
            Current order status
        """
        pass

    @abstractmethod
    async def get_fills(self, order_id: Optional[str] = None) -> List[Fill]:
        """Get fills for an order or all recent fills.

        Args:
            order_id: Optional order ID filter

        Returns:
            List of fill events
        """
        pass

    # Position tracking (to prevent buying both YES and NO on same market)

    def get_position(self, ticker: str, side: Side) -> int:
        """Get current position for a ticker and side.

        Args:
            ticker: Market ticker
            side: YES or NO

        Returns:
            Number of contracts held (0 if no position)
        """
        return 0

    def has_opposite_position(self, ticker: str, side: Side) -> bool:
        """Check if we have a position on the opposite side.

        Args:
            ticker: Market ticker
            side: Side we want to trade

        Returns:
            True if we hold contracts on the opposite side
        """
        return False

    def get_all_positions(self) -> Dict[Tuple[str, Side], int]:
        """Get all current positions.

        Returns:
            Dictionary mapping (ticker, side) to quantity
        """
        return {}
