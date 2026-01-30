"""Abstract interfaces for market-making components.

These interfaces define the contracts that concrete implementations must follow.
This allows for easy testing with mocks and swapping implementations.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from .models import Fill, MarketState


class APIClient(ABC):
    """Abstract interface for exchange API operations.

    Implementations should handle authentication, rate limiting,
    and error handling internally.

    Example implementation:
        >>> class MyExchangeClient(APIClient):
        ...     def place_order(self, ticker, side, price, size):
        ...         # Implementation here
        ...         return "order_123"
    """

    @abstractmethod
    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place an order on the exchange.

        Args:
            ticker: Market identifier.
            side: 'BID' or 'ASK'.
            price: Order price (0-1 range).
            size: Number of contracts.

        Returns:
            Order ID assigned by exchange.

        Raises:
            OrderError: If order placement fails.

        Example:
            >>> client = MyExchangeClient()
            >>> order_id = client.place_order("AAPL-YES", "BID", 0.45, 20)
            >>> order_id
            'order_123'
        """
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an existing order.

        Args:
            order_id: Exchange order ID.

        Returns:
            True if cancellation successful, False otherwise.

        Example:
            >>> client.cancel_order("order_123")
            True
        """
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """Get current status of an order.

        Args:
            order_id: Exchange order ID.

        Returns:
            Dictionary with order status fields:
            - status: 'open', 'filled', 'cancelled', 'partial'
            - filled_size: Number of contracts filled
            - remaining_size: Number of contracts remaining
            - avg_fill_price: Average fill price (if any fills)

        Example:
            >>> status = client.get_order_status("order_123")
            >>> status['status']
            'filled'
        """
        pass

    @abstractmethod
    def get_market_data(self, ticker: str) -> MarketState:
        """Get current market state.

        Args:
            ticker: Market identifier.

        Returns:
            Current MarketState for the ticker.

        Raises:
            MarketNotFoundError: If ticker doesn't exist.

        Example:
            >>> state = client.get_market_data("AAPL-YES")
            >>> state.best_bid
            0.45
        """
        pass

    @abstractmethod
    def get_positions(self) -> dict[str, int]:
        """Get all current positions.

        Returns:
            Dictionary mapping ticker to contract count.
            Positive = long, negative = short.

        Example:
            >>> positions = client.get_positions()
            >>> positions
            {'AAPL-YES': 50, 'GOOG-NO': -20}
        """
        pass

    @abstractmethod
    def get_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
    ) -> list[Fill]:
        """Get recent fills.

        Args:
            ticker: Filter by ticker (optional).
            limit: Maximum number of fills to return.

        Returns:
            List of Fill objects, most recent first.

        Example:
            >>> fills = client.get_fills("AAPL-YES", limit=10)
            >>> len(fills)
            10
        """
        pass


class DataProvider(ABC):
    """Abstract interface for market data.

    Provides both snapshot and streaming market data.

    Example implementation:
        >>> class MyDataProvider(DataProvider):
        ...     def get_current_market(self, ticker):
        ...         # Fetch and return current state
        ...         pass
    """

    @abstractmethod
    def get_current_market(self, ticker: str) -> MarketState:
        """Get current market state.

        Args:
            ticker: Market identifier.

        Returns:
            Current MarketState for the ticker.

        Raises:
            MarketNotFoundError: If ticker doesn't exist.
            DataUnavailableError: If data cannot be retrieved.

        Example:
            >>> provider = MyDataProvider()
            >>> state = provider.get_current_market("AAPL-YES")
            >>> state.mid_price
            0.465
        """
        pass

    @abstractmethod
    def get_multiple_markets(self, tickers: list[str]) -> dict[str, MarketState]:
        """Get current state for multiple markets.

        Args:
            tickers: List of market identifiers.

        Returns:
            Dictionary mapping ticker to MarketState.
            Missing markets are omitted from result.

        Example:
            >>> states = provider.get_multiple_markets(["AAPL-YES", "GOOG-YES"])
            >>> len(states)
            2
        """
        pass

    @abstractmethod
    def subscribe_to_updates(
        self,
        ticker: str,
        callback: Callable[[MarketState], None],
    ) -> str:
        """Subscribe to real-time market updates.

        Args:
            ticker: Market identifier.
            callback: Function called with new MarketState on each update.

        Returns:
            Subscription ID for later unsubscription.

        Example:
            >>> def on_update(state):
            ...     print(f"New price: {state.mid_price}")
            >>> sub_id = provider.subscribe_to_updates("AAPL-YES", on_update)
        """
        pass

    @abstractmethod
    def unsubscribe(self, subscription_id: str) -> bool:
        """Unsubscribe from market updates.

        Args:
            subscription_id: ID returned from subscribe_to_updates.

        Returns:
            True if unsubscription successful.

        Example:
            >>> provider.unsubscribe(sub_id)
            True
        """
        pass

    @abstractmethod
    def get_available_markets(self) -> list[str]:
        """Get list of available market tickers.

        Returns:
            List of ticker strings for all tradeable markets.

        Example:
            >>> markets = provider.get_available_markets()
            >>> "AAPL-YES" in markets
            True
        """
        pass


class OrderError(Exception):
    """Raised when an order operation fails."""

    pass


class MarketNotFoundError(Exception):
    """Raised when a market ticker is not found."""

    pass


class DataUnavailableError(Exception):
    """Raised when market data cannot be retrieved."""

    pass
