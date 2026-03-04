"""Data models for fill time estimation."""

import time
from dataclasses import dataclass
from typing import List, Optional

from src.core.orderbook_manager import OrderBookState


@dataclass
class SnapshotRecord:
    """Compact order book snapshot for storage.

    Uses short keys for JSONL storage efficiency:
      t=ticker, ts=timestamp, seq=sequence,
      b=bids[[price,size],...], a=asks[[price,size],...],
      bb=best_bid, ba=best_ask, sp=spread,
      bd=bid_depth, ad=ask_depth
    """

    ticker: str
    timestamp: float
    sequence: int
    bids: List[List[int]]  # [[price, size], ...]
    asks: List[List[int]]  # [[price, size], ...]
    best_bid: Optional[int]
    best_ask: Optional[int]
    spread: Optional[int]
    bid_depth: int
    ask_depth: int

    @classmethod
    def from_orderbook_state(cls, book: OrderBookState) -> "SnapshotRecord":
        return cls(
            ticker=book.ticker,
            timestamp=time.time(),
            sequence=book.sequence,
            bids=[[lvl.price, lvl.size] for lvl in book.bids],
            asks=[[lvl.price, lvl.size] for lvl in book.asks],
            best_bid=book.best_bid.price if book.best_bid else None,
            best_ask=book.best_ask.price if book.best_ask else None,
            spread=book.spread,
            bid_depth=book.bid_depth,
            ask_depth=book.ask_depth,
        )

    def to_dict(self) -> dict:
        return {
            "t": self.ticker,
            "ts": self.timestamp,
            "seq": self.sequence,
            "b": self.bids,
            "a": self.asks,
            "bb": self.best_bid,
            "ba": self.best_ask,
            "sp": self.spread,
            "bd": self.bid_depth,
            "ad": self.ask_depth,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SnapshotRecord":
        return cls(
            ticker=d["t"],
            timestamp=d["ts"],
            sequence=d["seq"],
            bids=d["b"],
            asks=d["a"],
            best_bid=d.get("bb"),
            best_ask=d.get("ba"),
            spread=d.get("sp"),
            bid_depth=d.get("bd", 0),
            ask_depth=d.get("ad", 0),
        )


@dataclass
class VelocityObservation:
    """A single velocity measurement from consecutive snapshots."""

    ticker: str
    timestamp: float
    side: str  # "bid" or "ask"
    price: int
    dt_seconds: float
    depth_change: int  # negative = depth decreased
    estimated_fills: float
    estimated_cancels: float
    velocity: float  # estimated_fills / dt_seconds
    spread_bucket: int
    bbo_transition: bool  # True if best bid/ask changed


@dataclass
class FillTimeEstimate:
    """Fill time distribution estimate for a single leg."""

    side: str  # "bid" or "ask"
    price: int
    queue_position: float
    velocity: float  # contracts/sec at this level
    observation_count: int

    # Time estimates (seconds)
    expected_seconds: float
    median_seconds: float
    std_seconds: float

    # Fill probabilities at time horizons
    p_fill_30s: float
    p_fill_60s: float
    p_fill_120s: float
    p_fill_300s: float
    p_ever_fills: float

    # Model metadata
    model_type: str  # "exponential" or "gamma"
    confidence: str  # "high", "medium", "low" based on observation_count


@dataclass
class RoundTripEstimate:
    """Combined fill time estimate for entry + exit."""

    entry: FillTimeEstimate
    exit: FillTimeEstimate

    # Combined probabilities
    p_round_trip_completes: float  # P(entry fills) * P(exit fills)
    p_round_trip_60s: float
    p_round_trip_120s: float

    # Expected value
    gross_edge_per_contract: float
    expected_profit_per_contract: float  # gross_edge * p_round_trip_completes
    expected_profit_total: float
    size: int
