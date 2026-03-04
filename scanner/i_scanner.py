"""Abstract scanner interface - exchange and game type agnostic."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, List, Optional

from .scanner_types import ScanFilter, ScanResult

if TYPE_CHECKING:
    from strategies import I_Strategy


class I_Scanner(ABC):
    """Abstract interface for market scanners.

    Implementations provide exchange-specific logic while strategies
    interact through this common interface.

    Example:
        >>> class MyStrategy(I_Strategy):
        ...     def __init__(self, scanner: I_Scanner):
        ...         self._scanner = scanner
        ...
        ...     async def discover_markets(self):
        ...         results = await self._scanner.scan_for_strategy(self)
        ...         return [r.ticker for r in results]
    """

    @abstractmethod
    async def scan(self, filters: Optional[ScanFilter] = None) -> List[ScanResult]:
        """Scan for markets matching filters.

        Args:
            filters: Scan filters. Defaults to all open markets.

        Returns:
            List of ScanResult sorted by volume descending.
        """
        pass

    @abstractmethod
    async def scan_for_strategy(
        self,
        strategy: "I_Strategy",
        series_ticker: Optional[str] = None,
        min_volume: int = 0,
        max_spread_cents: int = 100,
    ) -> List[ScanResult]:
        """Scan for markets using a strategy's filter.

        Passes each market to the strategy's market_filter method.

        Args:
            strategy: Strategy instance with market_filter method
            series_ticker: Optional series/event filter
            min_volume: Minimum volume threshold
            max_spread_cents: Maximum spread threshold

        Returns:
            Markets that pass the strategy's filter
        """
        pass

    @abstractmethod
    async def get_markets(
        self,
        series_ticker: Optional[str] = None,
        status: str = "open",
    ) -> List[Any]:
        """Get raw market data from exchange.

        Args:
            series_ticker: Optional series/event filter
            status: Market status filter

        Returns:
            List of exchange-specific market data objects
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close scanner and cleanup resources."""
        pass

    async def __aenter__(self) -> "I_Scanner":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    # --- Output Helpers (concrete implementations) ---

    @staticmethod
    def format_table(results: List[ScanResult]) -> str:
        """Format results as a table string."""
        lines = [
            f"{'TICKER':<45} {'BID':>5} {'ASK':>5} {'SPRD':>5} {'VOL':>8}",
            "-" * 75,
        ]
        for r in results:
            lines.append(
                f"{r.ticker:<45} {r.bid_cents:>5} {r.ask_cents:>5} {r.spread_cents:>5} {r.volume:>8}"
            )
        return "\n".join(lines)

    @staticmethod
    def get_tickers(results: List[ScanResult], limit: int = 10) -> List[str]:
        """Get list of tickers from results."""
        return [r.ticker for r in results[:limit]]

    @staticmethod
    def format_tickers_csv(results: List[ScanResult], limit: int = 10) -> str:
        """Format tickers as comma-separated string."""
        return ",".join(I_Scanner.get_tickers(results, limit))
