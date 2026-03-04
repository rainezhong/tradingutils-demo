"""Truth source abstraction for latency probes.

A TruthSource provides real-time probability readings from an external
data source (e.g., Kraken spot price → Black-Scholes probability) that
can be compared against Kalshi market prices to measure reaction lag.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TruthReading:
    """A single probability reading from the truth source."""

    timestamp: float  # Unix ts
    probability: float  # 0-1: P(YES wins)
    raw_value: Optional[float] = None  # Source value (spot price, score diff, temp)
    confidence: float = 1.0  # 0-1, how confident the source is
    metadata: Optional[dict] = field(default_factory=lambda: None)


class TruthSource(ABC):
    """Abstract base for truth sources that produce probability readings."""

    @abstractmethod
    def start(self) -> None:
        """Start the truth source feed(s)."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the truth source feed(s)."""
        ...

    @abstractmethod
    def get_reading(
        self,
        ticker: str,
        strike: Optional[float],
        seconds_to_close: float,
    ) -> Optional[TruthReading]:
        """Get a probability reading for the given market.

        Args:
            ticker: Kalshi market ticker (e.g., "KXBTC15M-25FEB22T1200")
            strike: Market strike/floor value (e.g., 97250.0 for BTC)
            seconds_to_close: Seconds until market expiration

        Returns:
            TruthReading or None if data not yet available
        """
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the truth source feed is connected and producing data."""
        ...
