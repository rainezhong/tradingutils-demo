"""Strategy interface for trading strategies."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, List

if TYPE_CHECKING:
    from .strategy_types import Signal


class I_Strategy(ABC):
    """Abstract base class for trading strategies.

    Strategies implement market selection, signal generation,
    and position management logic.
    """

    @abstractmethod
    def market_filter(self, market: Any) -> bool:
        """Filter function for market selection.

        Args:
            market: Market data object to evaluate

        Returns:
            True if market passes strategy's filters
        """
        pass

    def get_market_filter(self) -> Callable[[Any], bool]:
        """Get the market filter as a callable for scanner integration."""
        return self.market_filter

    @abstractmethod
    def get_candidate_markets(self) -> List[Any]:
        """Get all markets that pass initial filters."""
        pass

    @abstractmethod
    def get_selected_markets(self) -> List[Any]:
        """Get markets actively being traded."""
        pass

    @abstractmethod
    def select_markets(self, markets: List[Any]) -> None:
        """Select markets to trade from candidates."""
        pass

    @abstractmethod
    def get_signal(self, market: Any) -> "Signal":
        """Get trading signal for a market."""
        pass

    @abstractmethod
    async def load_markets(self) -> None:
        """Load initial market data for all tickers.

        Implementation should populate internal market data structures
        from the exchange client.
        """
        pass

    @abstractmethod
    async def refresh_markets(self) -> None:
        """Refresh market data for selected markets.

        Implementation should update orderbooks/prices for
        all actively traded markets.
        """
        pass

    @abstractmethod
    async def on_tick(self) -> None:
        """Called on each tick/update cycle."""
        pass

    @abstractmethod
    async def run(self) -> None:
        """Main strategy loop."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the strategy loop."""
        pass

    @abstractmethod
    def log(self, message: str) -> None:
        """Log strategy activity."""
        pass

    async def get_bankroll(self) -> float:
        """Get current bankroll from exchange account.

        Default implementation returns 0.0. Strategies that use Kelly sizing
        or dynamic position sizing should override this to query the exchange.

        Returns:
            Current available balance in dollars
        """
        return 0.0
