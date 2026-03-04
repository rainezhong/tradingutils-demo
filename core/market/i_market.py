"""Abstract base class for market representations."""

from abc import ABC, abstractmethod
from typing import Optional

from .market_types import OrderBook


class I_Market(ABC):
    """Interface for market data and state.

    Represents a single tradeable market with orderbook data,
    timing information, and computed metrics.
    """

    @property
    @abstractmethod
    def ticker(self) -> str:
        """Market ticker identifier."""
        pass

    @property
    @abstractmethod
    def slug(self) -> str:
        """Market slug/event identifier."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable market name."""
        pass

    @abstractmethod
    def get_ticker(self) -> str:
        """Get market ticker."""
        pass

    @abstractmethod
    def get_slug(self) -> str:
        """Get market slug."""
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Get market name."""
        pass

    @abstractmethod
    def get_current_orderbook(self, outcome: str = "yes") -> OrderBook:
        """Get current orderbook for the specified outcome.

        Args:
            outcome: "yes" or "no" side

        Returns:
            OrderBook with current bids/asks
        """
        pass

    @abstractmethod
    async def update_orderbook(self) -> None:
        """Refresh orderbook data from exchange."""
        pass

    @abstractmethod
    def time_to_resolution(self) -> Optional[float]:
        """Seconds until market resolves, or None if unknown."""
        pass

    @abstractmethod
    def get_volatility(self) -> float:
        """Estimated volatility of the market."""
        pass
