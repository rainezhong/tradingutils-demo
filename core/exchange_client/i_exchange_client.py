"""Abstract base class for exchange clients."""

from abc import ABC, abstractmethod
from typing import Any, List, Optional


class I_ExchangeClient(ABC):
    """Interface for exchange API clients.

    All exchange clients must implement these methods to provide
    a unified interface for the trading system.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Exchange name identifier (e.g., 'kalshi', 'polymarket')."""
        pass

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether client is currently connected."""
        pass

    @abstractmethod
    async def connect(self) -> None:
        """Initialize connection to the exchange.

        Should establish HTTP/WebSocket connections and verify authentication.
        """
        pass

    @abstractmethod
    async def exit(self) -> None:
        """Close all connections and cleanup resources."""
        pass

    @abstractmethod
    async def request_market(self, ticker: str) -> Any:
        """Request data for a specific market.

        Args:
            ticker: Market ticker/identifier

        Returns:
            Market data object
        """
        pass

    @abstractmethod
    async def get_markets(
        self,
        series_ticker: Optional[str] = None,
        status: str = "open",
        limit: int = 1000,
    ) -> List[Any]:
        """Get markets matching filters.

        Args:
            series_ticker: Optional series filter
            status: Market status filter
            limit: Maximum results

        Returns:
            List of market objects
        """
        pass

    @abstractmethod
    async def get_balance(self) -> Any:
        """Get account balance information.

        Returns:
            Balance object with available funds
        """
        pass

    @abstractmethod
    async def get_positions(self, ticker: Optional[str] = None) -> List[Any]:
        """Get current positions.

        Args:
            ticker: Optional ticker filter

        Returns:
            List of position objects
        """
        pass
