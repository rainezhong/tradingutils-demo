"""Kalshi-specific market scanner implementation."""

import asyncio
from typing import TYPE_CHECKING, List, Optional

from .i_scanner import I_Scanner
from .scanner_types import ScanFilter, ScanResult
from core import KalshiExchangeClient
from core.exchange_client import KalshiMarketData

if TYPE_CHECKING:
    from strategies import I_Strategy


class KalshiScanner(I_Scanner):
    """Kalshi exchange scanner implementation.

    Scans Kalshi markets for those matching filter criteria.

    Example:
        >>> async with KalshiScanner() as scanner:
        ...     results = await scanner.scan(ScanFilter(
        ...         series_ticker="KXNBAGAME",
        ...         min_volume=100,
        ...         max_spread_cents=10,
        ...     ))
        ...     for r in results:
        ...         print(f"{r.ticker}: bid={r.bid_cents}, spread={r.spread_cents}")
    """

    def __init__(
        self,
        client: Optional[KalshiExchangeClient] = None,
        demo: bool = False,
    ):
        """Initialize scanner.

        Args:
            client: Optional existing client. If None, creates one.
            demo: Use demo exchange endpoints.
        """
        self._client = client
        self._demo = demo
        self._owns_client = client is None

    async def _get_client(self) -> KalshiExchangeClient:
        """Get or create the exchange client."""
        if self._client is None:
            self._client = KalshiExchangeClient.from_env(demo=self._demo)
        if not self._client.is_connected:
            await self._client.connect()
        return self._client

    async def get_markets(
        self,
        series_ticker: Optional[str] = None,
        status: str = "open",
    ) -> List[KalshiMarketData]:
        """Get raw market data from Kalshi.

        Args:
            series_ticker: Filter by series (e.g., "KXNBAGAME")
            status: Market status filter

        Returns:
            List of KalshiMarketData objects
        """
        client = await self._get_client()
        return await client.get_markets(
            series_ticker=series_ticker,
            status=status,
        )

    async def scan(self, filters: Optional[ScanFilter] = None) -> List[ScanResult]:
        """Scan for markets matching filters.

        Args:
            filters: Scan filters. Defaults to all open markets.

        Returns:
            List of ScanResult sorted by volume descending.
        """
        filters = filters or ScanFilter()

        # Fetch markets from exchange
        markets = await self.get_markets(
            series_ticker=filters.series_ticker,
            status=filters.status,
        )

        # Apply filters
        results = []
        for m in markets:
            spread = m.yes_ask - m.yes_bid

            # Volume filter
            if m.volume < filters.min_volume:
                continue

            # Spread filter
            if spread > filters.max_spread_cents:
                continue

            # Price range filter
            if (
                m.yes_bid < filters.min_price_cents
                or m.yes_bid > filters.max_price_cents
            ):
                continue

            # Custom filter (sync or async)
            if filters.custom_filter:
                result = filters.custom_filter(m)
                if asyncio.iscoroutine(result):
                    result = await result
                if not result:
                    continue

            # Strategy filter (sync or async)
            if filters.strategy_filter:
                result = filters.strategy_filter(m)
                if asyncio.iscoroutine(result):
                    result = await result
                if not result:
                    continue

            results.append(ScanResult(market=m, spread_cents=spread))

        # Sort by volume descending
        results.sort(key=lambda x: x.volume, reverse=True)

        return results

    async def scan_for_strategy(
        self,
        strategy: "I_Strategy",
        series_ticker: Optional[str] = None,
        min_volume: int = 0,
        max_spread_cents: int = 100,
    ) -> List[ScanResult]:
        """Scan for markets using a strategy's filter.

        Args:
            strategy: Strategy instance with market_filter method
            series_ticker: Optional series filter
            min_volume: Minimum volume threshold
            max_spread_cents: Maximum spread threshold

        Returns:
            Markets that pass the strategy's filter
        """
        filters = ScanFilter.from_strategy(
            strategy,
            series_ticker=series_ticker,
            min_volume=min_volume,
            max_spread_cents=max_spread_cents,
        )
        return await self.scan(filters)

    async def scan_strong_side(
        self,
        series_ticker: Optional[str] = None,
        threshold: float = 0.60,
        min_volume: int = 100,
    ) -> List[ScanResult]:
        """Scan for markets with a strong directional bias.

        Args:
            series_ticker: Optional series filter
            threshold: Price threshold for "strong" (0.60 = 60%)
            min_volume: Minimum volume

        Returns:
            Markets where yes_bid >= threshold*100 or no_bid >= threshold*100
        """
        threshold_cents = int(threshold * 100)

        def is_strong_side(m: KalshiMarketData) -> bool:
            # YES is strong
            if m.yes_bid >= threshold_cents:
                return True
            # NO is strong (100 - yes_ask is the no_bid)
            no_bid = 100 - m.yes_ask
            if no_bid >= threshold_cents:
                return True
            return False

        filters = ScanFilter(
            series_ticker=series_ticker,
            min_volume=min_volume,
            custom_filter=is_strong_side,
        )

        return await self.scan(filters)

    async def close(self) -> None:
        """Close the scanner and cleanup resources."""
        if self._owns_client and self._client is not None:
            await self._client.exit()
            self._client = None

    async def __aenter__(self) -> "KalshiScanner":
        await self._get_client()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()
