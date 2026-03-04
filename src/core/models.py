"""Data models used by the backtest framework.

Provides MarketState, Fill, and supporting types. Restored from git history
(these were in src/core/models.py before the consolidation).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class MarketState:
    """Point-in-time market snapshot."""

    ticker: str
    timestamp: datetime
    bid: float
    ask: float
    mid: float = field(init=False)
    spread: float = field(init=False)
    last_price: Optional[float] = None
    volume: int = 0
    bid_depth: Optional[int] = None
    ask_depth: Optional[int] = None

    def __post_init__(self) -> None:
        self.mid = (self.bid + self.ask) / 2
        self.spread = self.ask - self.bid


@dataclass
class Fill:
    """A trade execution (fill)."""

    ticker: str
    side: str  # 'BID' or 'ASK'
    price: float
    size: int
    order_id: str
    fill_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    fee: float = 0.0


@dataclass
class Position:
    """A market position."""

    ticker: str
    side: str
    size: int
    avg_price: float
    timestamp: Optional[datetime] = None


@dataclass
class Market:
    """A prediction market."""

    ticker: str
    title: str
    category: Optional[str] = None
    close_time: Optional[str] = None
    status: Optional[str] = None
    volume_24h: Optional[int] = None
    open_interest: Optional[int] = None


@dataclass
class Quote:
    """A quote (order) in the market."""

    ticker: str
    side: str
    price: float
    size: int
    order_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    status: str = "PENDING"
    filled_size: int = 0


@dataclass
class Snapshot:
    """Point-in-time market observation."""

    ticker: str
    timestamp: str
    yes_bid: Optional[int] = None
    yes_ask: Optional[int] = None
    spread_cents: Optional[int] = None


class SummaryStats:
    """Placeholder for summary statistics."""

    pass


class ValidationError(Exception):
    """Model validation error."""

    pass
