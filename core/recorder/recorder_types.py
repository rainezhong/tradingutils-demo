"""Recording types for market data capture and replay.

Two frame types for different market structures:
- MarketFrame: Single-ticker yes/no markets (most Kalshi markets)
- PairMarketFrame: Two-ticker paired markets (e.g., NBA home/away win)

Orderbook depth data can be attached to either frame type.
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple
import json
from pathlib import Path


# =============================================================================
# Orderbook depth snapshot (exchange-agnostic)
# =============================================================================

# Each level is [price_cents, quantity]
OrderbookLevel = Tuple[int, int]


@dataclass
class OrderbookSnapshot:
    """Point-in-time orderbook depth for a market.

    Each side (yes/no) is a list of [price, quantity] levels sorted by price.
    Prices are in cents (0-100), quantities are contract counts.

    Example:
        >>> ob = OrderbookSnapshot(
        ...     yes=[[30, 50], [31, 20], [32, 10]],
        ...     no=[[37, 300], [38, 100]],
        ... )
        >>> ob.best_yes_bid   # 32 (highest yes bid)
        >>> ob.total_yes_depth  # 80 (50+20+10)
    """

    yes: List[List[int]]  # [[price, qty], ...] sorted by price ascending
    no: List[List[int]]  # [[price, qty], ...] sorted by price ascending

    def to_dict(self) -> Dict[str, Any]:
        return {"yes": self.yes, "no": self.no}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrderbookSnapshot":
        return cls(yes=data["yes"], no=data["no"])

    @property
    def best_yes_bid(self) -> Optional[int]:
        """Highest yes bid price (top of book)."""
        return self.yes[-1][0] if self.yes else None

    @property
    def best_no_bid(self) -> Optional[int]:
        """Highest no bid price (top of book)."""
        return self.no[-1][0] if self.no else None

    @property
    def best_yes_ask(self) -> Optional[int]:
        """Best yes ask = 100 - best_no_bid."""
        return (100 - self.best_no_bid) if self.best_no_bid is not None else None

    @property
    def best_no_ask(self) -> Optional[int]:
        """Best no ask = 100 - best_yes_bid."""
        return (100 - self.best_yes_bid) if self.best_yes_bid is not None else None

    @property
    def total_yes_depth(self) -> int:
        """Total contracts on yes side."""
        return sum(qty for _, qty in self.yes)

    @property
    def total_no_depth(self) -> int:
        """Total contracts on no side."""
        return sum(qty for _, qty in self.no)

    @property
    def vwap_yes(self) -> Optional[float]:
        """Volume-weighted average price of yes side."""
        total_qty = self.total_yes_depth
        if total_qty == 0:
            return None
        return sum(price * qty for price, qty in self.yes) / total_qty

    @property
    def vwap_no(self) -> Optional[float]:
        """Volume-weighted average price of no side."""
        total_qty = self.total_no_depth
        if total_qty == 0:
            return None
        return sum(price * qty for price, qty in self.no) / total_qty


# =============================================================================
# Single-ticker market (yes/no)
# =============================================================================


@dataclass
class MarketFrame:
    """Point-in-time snapshot of a single-ticker yes/no market."""

    timestamp: int
    ticker: str

    # Prices in cents (0-100)
    yes_bid: int
    yes_ask: int
    volume: int
    market_status: str  # "open", "closed", etc.

    # Orderbook depth (optional)
    orderbook: Optional[OrderbookSnapshot] = None

    # Optional context (event data, scores, etc.)
    extra: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.orderbook:
            d["orderbook"] = self.orderbook.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketFrame":
        ob_data = data.pop("orderbook", None)
        frame = cls(**data)
        if ob_data:
            frame.orderbook = OrderbookSnapshot.from_dict(ob_data)
        return frame

    @property
    def no_bid(self) -> int:
        """No bid derived from yes ask (complement)."""
        return 100 - self.yes_ask

    @property
    def no_ask(self) -> int:
        """No ask derived from yes bid (complement)."""
        return 100 - self.yes_bid

    @property
    def yes_mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def spread(self) -> int:
        """Bid-ask spread in cents."""
        return self.yes_ask - self.yes_bid


@dataclass
class MarketSeriesMetadata:
    """Metadata for a single-ticker market recording."""

    ticker: str
    date: str  # YYYY-MM-DD
    recorded_at: str  # ISO timestamp
    poll_interval_ms: int = 500
    total_frames: int = 0
    final_status: Optional[str] = None
    label: Optional[str] = None  # e.g. "BTC > 50k", "Will it rain?"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketSeriesMetadata":
        return cls(**data)


@dataclass
class MarketSeries:
    """Recorded series of single-ticker market snapshots.

    Example:
        >>> series = MarketSeries.load("data/recordings/KXBTC-50k.json")
        >>> for frame in series:
        ...     print(f"yes_mid={frame.yes_mid:.1f} spread={frame.spread}")
    """

    metadata: MarketSeriesMetadata
    frames: List[MarketFrame] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "frames": [f.to_dict() for f in self.frames],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MarketSeries":
        metadata = MarketSeriesMetadata.from_dict(data["metadata"])
        frames = [MarketFrame.from_dict(f) for f in data["frames"]]
        return cls(metadata=metadata, frames=frames)

    def save(self, filepath: str) -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "MarketSeries":
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def add_frame(self, frame: MarketFrame) -> None:
        self.frames.append(frame)
        self.metadata.total_frames = len(self.frames)

    def get_frame_at_time(self, timestamp: float) -> Optional[MarketFrame]:
        """Get the frame closest to a given timestamp."""
        if not self.frames:
            return None
        left, right = 0, len(self.frames) - 1
        while left < right:
            mid = (left + right) // 2
            if self.frames[mid].timestamp < timestamp:
                left = mid + 1
            else:
                right = mid
        if left > 0 and abs(self.frames[left - 1].timestamp - timestamp) < abs(
            self.frames[left].timestamp - timestamp
        ):
            return self.frames[left - 1]
        return self.frames[left]

    def get_frames_in_range(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> List[MarketFrame]:
        """Get all frames within a time range (inclusive)."""
        result = []
        for frame in self.frames:
            if start_time and frame.timestamp < start_time:
                continue
            if end_time and frame.timestamp > end_time:
                break
            result.append(frame)
        return result

    @property
    def duration_seconds(self) -> float:
        if len(self.frames) < 2:
            return 0.0
        return self.frames[-1].timestamp - self.frames[0].timestamp

    @property
    def start_time(self) -> Optional[int]:
        return self.frames[0].timestamp if self.frames else None

    @property
    def end_time(self) -> Optional[int]:
        return self.frames[-1].timestamp if self.frames else None

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)

    def __getitem__(self, idx: int) -> MarketFrame:
        return self.frames[idx]


# =============================================================================
# Paired-ticker market (two separate tickers for each side)
# =============================================================================


@dataclass
class PairMarketFrame:
    """Point-in-time snapshot of a paired two-ticker market.

    Used when each side has its own independent ticker and order book,
    e.g., NBA game where "LAL wins" and "BOS wins" are separate markets.
    """

    timestamp: int

    # Two independent tickers
    yes_ticker: str
    no_ticker: str

    # Prices in cents (0-100)
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int
    volume: int
    market_status: str  # "open", "closed", etc.

    # Orderbook depth per side (optional)
    yes_orderbook: Optional[OrderbookSnapshot] = None
    no_orderbook: Optional[OrderbookSnapshot] = None

    # Optional context (sport scores, event data, etc.)
    extra: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.yes_orderbook:
            d["yes_orderbook"] = self.yes_orderbook.to_dict()
        if self.no_orderbook:
            d["no_orderbook"] = self.no_orderbook.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PairMarketFrame":
        yes_ob = data.pop("yes_orderbook", None)
        no_ob = data.pop("no_orderbook", None)
        frame = cls(**data)
        if yes_ob:
            frame.yes_orderbook = OrderbookSnapshot.from_dict(yes_ob)
        if no_ob:
            frame.no_orderbook = OrderbookSnapshot.from_dict(no_ob)
        return frame

    @property
    def yes_mid(self) -> float:
        return (self.yes_bid + self.yes_ask) / 2.0

    @property
    def no_mid(self) -> float:
        return (self.no_bid + self.no_ask) / 2.0

    @property
    def yes_spread(self) -> int:
        return self.yes_ask - self.yes_bid

    @property
    def no_spread(self) -> int:
        return self.no_ask - self.no_bid


@dataclass
class PairMarketSeriesMetadata:
    """Metadata for a paired-ticker market recording."""

    yes_ticker: str
    no_ticker: str
    date: str  # YYYY-MM-DD
    recorded_at: str  # ISO timestamp
    poll_interval_ms: int = 500
    total_frames: int = 0
    final_status: Optional[str] = None
    label: Optional[str] = None  # e.g. "LAL vs BOS"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PairMarketSeriesMetadata":
        return cls(**data)


@dataclass
class PairMarketSeries:
    """Recorded series of paired-ticker market snapshots.

    Example:
        >>> series = PairMarketSeries.load("data/recordings/KXNBA_LAL_vs_BOS.json")
        >>> for frame in series:
        ...     print(f"yes={frame.yes_mid:.1f} no={frame.no_mid:.1f} vol={frame.volume}")
    """

    metadata: PairMarketSeriesMetadata
    frames: List[PairMarketFrame] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metadata": self.metadata.to_dict(),
            "frames": [f.to_dict() for f in self.frames],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PairMarketSeries":
        metadata = PairMarketSeriesMetadata.from_dict(data["metadata"])
        frames = [PairMarketFrame.from_dict(f) for f in data["frames"]]
        return cls(metadata=metadata, frames=frames)

    def save(self, filepath: str) -> None:
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, filepath: str) -> "PairMarketSeries":
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls.from_dict(data)

    def add_frame(self, frame: PairMarketFrame) -> None:
        self.frames.append(frame)
        self.metadata.total_frames = len(self.frames)

    def get_frame_at_time(self, timestamp: float) -> Optional[PairMarketFrame]:
        """Get the frame closest to a given timestamp."""
        if not self.frames:
            return None
        left, right = 0, len(self.frames) - 1
        while left < right:
            mid = (left + right) // 2
            if self.frames[mid].timestamp < timestamp:
                left = mid + 1
            else:
                right = mid
        if left > 0 and abs(self.frames[left - 1].timestamp - timestamp) < abs(
            self.frames[left].timestamp - timestamp
        ):
            return self.frames[left - 1]
        return self.frames[left]

    def get_frames_in_range(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> List[PairMarketFrame]:
        """Get all frames within a time range (inclusive)."""
        result = []
        for frame in self.frames:
            if start_time and frame.timestamp < start_time:
                continue
            if end_time and frame.timestamp > end_time:
                break
            result.append(frame)
        return result

    @property
    def duration_seconds(self) -> float:
        if len(self.frames) < 2:
            return 0.0
        return self.frames[-1].timestamp - self.frames[0].timestamp

    @property
    def start_time(self) -> Optional[int]:
        return self.frames[0].timestamp if self.frames else None

    @property
    def end_time(self) -> Optional[int]:
        return self.frames[-1].timestamp if self.frames else None

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)

    def __getitem__(self, idx: int) -> PairMarketFrame:
        return self.frames[idx]
