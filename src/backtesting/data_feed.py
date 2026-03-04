"""Data feed abstraction for the unified backtest framework.

Provides BacktestFrame (a point-in-time market snapshot) and DataFeed
(an iterable source of frames).  Every strategy adapter consumes frames
from a DataFeed and returns Signals.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from src.core.models import MarketState


@dataclass
class BacktestFrame:
    """Single point-in-time snapshot fed to a BacktestAdapter.

    Attributes:
        timestamp: When this snapshot was observed.
        frame_idx: Monotonically increasing index within the feed.
        markets: Ticker -> MarketState mapping for every active market.
        context: Strategy-specific payload (scores, spot price, etc.).
    """

    timestamp: datetime
    frame_idx: int
    markets: Dict[str, MarketState]
    context: Dict[str, Any] = field(default_factory=dict)


class DataFeed(ABC):
    """Abstract iterable that yields BacktestFrame objects.

    Subclasses convert a concrete data source (JSON recording, SQLite DB,
    API candles, ...) into a uniform stream of BacktestFrames.
    """

    @abstractmethod
    def __iter__(self) -> Iterator[BacktestFrame]:
        """Yield frames in chronological order."""
        ...

    @abstractmethod
    def get_settlement(self) -> Dict[str, Optional[float]]:
        """Return settlement prices for each ticker.

        Returns:
            ticker -> settlement value.  1.0 = YES won, 0.0 = NO won,
            None = unknown / not settled.
        """
        ...

    @property
    @abstractmethod
    def tickers(self) -> List[str]:
        """All tickers that may appear in frames."""
        ...

    def slice(self, start_ts: float, end_ts: float) -> "DataFeed":
        """Return a new DataFeed filtered to frames in [start_ts, end_ts).

        Default implementation uses SlicedDataFeed. Concrete feeds can
        override for efficiency.
        """
        from .validation.walk_forward import SlicedDataFeed
        return SlicedDataFeed(self, start_ts, end_ts)

    @property
    def metadata(self) -> Dict[str, Any]:
        """Optional metadata (game_id, teams, dates, …)."""
        return {}

    def get_market_at_timestamp(
        self, ticker: str, timestamp: "datetime"
    ) -> Optional[MarketState]:
        """Get market state at a specific timestamp.

        Used by network latency model to fetch delayed market state.

        Args:
            ticker: Market ticker to look up.
            timestamp: Target timestamp.

        Returns:
            MarketState at or near the target timestamp, or None if no data.

        Default implementation returns None. Subclasses should override
        to support latency simulation.
        """
        return None
