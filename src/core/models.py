"""Data models for market data with validation."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .utils import parse_iso_timestamp, utc_now_iso


class ValidationError(Exception):
    """Raised when model validation fails."""
    pass


@dataclass
class Market:
    """Represents a prediction market."""

    ticker: str
    title: str
    category: Optional[str] = None
    close_time: Optional[str] = None
    status: Optional[str] = None
    volume_24h: Optional[int] = None
    open_interest: Optional[int] = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        """Validate market data after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate market data."""
        if not self.ticker:
            raise ValidationError("Market ticker cannot be empty")
        if not self.title:
            raise ValidationError("Market title cannot be empty")
        if self.volume_24h is not None and self.volume_24h < 0:
            raise ValidationError(f"volume_24h must be non-negative, got {self.volume_24h}")
        if self.open_interest is not None and self.open_interest < 0:
            raise ValidationError(f"open_interest must be non-negative, got {self.open_interest}")

    @classmethod
    def from_api_response(cls, data: dict) -> "Market":
        """Create Market from Kalshi API response."""
        return cls(
            ticker=data.get("ticker", ""),
            title=data.get("title", ""),
            category=data.get("category"),
            close_time=data.get("close_time"),
            status=data.get("status"),
            volume_24h=data.get("volume_24h"),
            open_interest=data.get("open_interest"),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "ticker": self.ticker,
            "title": self.title,
            "category": self.category,
            "close_time": self.close_time,
            "status": self.status,
            "volume_24h": self.volume_24h,
            "open_interest": self.open_interest,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class Snapshot:
    """Represents a point-in-time observation of a market."""

    ticker: str
    timestamp: str
    yes_bid: Optional[int] = None
    yes_ask: Optional[int] = None
    spread_cents: Optional[int] = None
    spread_pct: Optional[float] = None
    mid_price: Optional[float] = None
    volume_24h: Optional[int] = None
    open_interest: Optional[int] = None
    orderbook_bid_depth: Optional[int] = None
    orderbook_ask_depth: Optional[int] = None
    id: Optional[int] = None

    def __post_init__(self) -> None:
        """Validate snapshot data after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate snapshot data."""
        if not self.ticker:
            raise ValidationError("Snapshot ticker cannot be empty")
        if not self.timestamp:
            raise ValidationError("Snapshot timestamp cannot be empty")

        # Validate price ranges (0-100 cents)
        if self.yes_bid is not None:
            if not 0 <= self.yes_bid <= 100:
                raise ValidationError(f"yes_bid must be 0-100, got {self.yes_bid}")

        if self.yes_ask is not None:
            if not 0 <= self.yes_ask <= 100:
                raise ValidationError(f"yes_ask must be 0-100, got {self.yes_ask}")

        # Validate bid < ask
        if self.yes_bid is not None and self.yes_ask is not None:
            if self.yes_bid > self.yes_ask:
                raise ValidationError(
                    f"yes_bid ({self.yes_bid}) must be <= yes_ask ({self.yes_ask})"
                )

        # Validate spread percentage (0-100%)
        if self.spread_pct is not None:
            if not 0 <= self.spread_pct <= 200:  # Can exceed 100% for very wide spreads
                raise ValidationError(f"spread_pct must be 0-200, got {self.spread_pct}")

        # Validate non-negative values
        if self.volume_24h is not None and self.volume_24h < 0:
            raise ValidationError(f"volume_24h must be non-negative, got {self.volume_24h}")
        if self.open_interest is not None and self.open_interest < 0:
            raise ValidationError(f"open_interest must be non-negative, got {self.open_interest}")

    @classmethod
    def from_orderbook(
        cls,
        ticker: str,
        orderbook: dict,
        timestamp: Optional[str] = None,
        volume_24h: Optional[int] = None,
        open_interest: Optional[int] = None,
    ) -> "Snapshot":
        """
        Create Snapshot from orderbook data.

        Args:
            ticker: Market ticker
            orderbook: Orderbook response from API
            timestamp: ISO8601 timestamp (defaults to now)
            volume_24h: 24-hour volume
            open_interest: Open interest

        Returns:
            Snapshot instance with calculated spreads
        """
        ob_data = orderbook.get("orderbook", {})
        yes_bids = ob_data.get("yes", [])
        no_bids = ob_data.get("no", [])

        # Extract best bid/ask
        yes_bid = yes_bids[0][0] if yes_bids else None
        yes_ask = 100 - no_bids[0][0] if no_bids else None

        # Calculate depths
        orderbook_bid_depth = sum(level[1] for level in yes_bids) if yes_bids else None
        orderbook_ask_depth = sum(level[1] for level in no_bids) if no_bids else None

        # Calculate spread metrics
        spread_cents = None
        spread_pct = None
        mid_price = None

        if yes_bid is not None and yes_ask is not None:
            spread_cents = yes_ask - yes_bid
            mid_price = (yes_bid + yes_ask) / 2
            spread_pct = (spread_cents / mid_price) * 100 if mid_price > 0 else 0.0

        return cls(
            ticker=ticker,
            timestamp=timestamp or utc_now_iso(),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread_cents=spread_cents,
            spread_pct=spread_pct,
            mid_price=mid_price,
            volume_24h=volume_24h,
            open_interest=open_interest,
            orderbook_bid_depth=orderbook_bid_depth,
            orderbook_ask_depth=orderbook_ask_depth,
        )

    @classmethod
    def from_market_data(cls, data: dict, timestamp: Optional[str] = None) -> "Snapshot":
        """
        Create Snapshot from /markets API response (more efficient than orderbook).

        Args:
            data: Market data from /markets endpoint
            timestamp: ISO8601 timestamp (defaults to now)

        Returns:
            Snapshot instance with calculated spreads
        """
        yes_bid = data.get("yes_bid")
        yes_ask = data.get("yes_ask")

        # Calculate spread metrics
        spread_cents = None
        spread_pct = None
        mid_price = None

        if yes_bid is not None and yes_ask is not None:
            spread_cents = yes_ask - yes_bid
            mid_price = (yes_bid + yes_ask) / 2
            spread_pct = (spread_cents / mid_price) * 100 if mid_price > 0 else 0.0

        return cls(
            ticker=data.get("ticker", ""),
            timestamp=timestamp or utc_now_iso(),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread_cents=spread_cents,
            spread_pct=spread_pct,
            mid_price=mid_price,
            volume_24h=data.get("volume_24h"),
            open_interest=data.get("open_interest"),
            orderbook_bid_depth=None,  # Not available from /markets
            orderbook_ask_depth=None,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "ticker": self.ticker,
            "timestamp": self.timestamp,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "spread_cents": self.spread_cents,
            "spread_pct": self.spread_pct,
            "mid_price": self.mid_price,
            "volume_24h": self.volume_24h,
            "open_interest": self.open_interest,
            "orderbook_bid_depth": self.orderbook_bid_depth,
            "orderbook_ask_depth": self.orderbook_ask_depth,
        }


@dataclass
class SummaryStats:
    """Aggregate statistics from the database."""

    total_markets: int = 0
    total_snapshots: int = 0
    avg_spread_cents: Optional[float] = None
    avg_spread_pct: Optional[float] = None
    min_spread: Optional[int] = None
    max_spread: Optional[int] = None

    def __str__(self) -> str:
        """Format stats for display."""
        lines = [
            f"Total Markets: {self.total_markets}",
            f"Total Snapshots: {self.total_snapshots}",
        ]
        if self.avg_spread_cents is not None:
            lines.append(f"Avg Spread: {self.avg_spread_cents:.1f} cents ({self.avg_spread_pct:.1f}%)")
            lines.append(f"Min Spread: {self.min_spread} cent{'s' if self.min_spread != 1 else ''}")
            lines.append(f"Max Spread: {self.max_spread} cents")
        else:
            lines.append("No spread data available")
        return "\n".join(lines)


@dataclass
class Position:
    """Represents a trading position in a market.

    Attributes:
        ticker: The market/instrument identifier
        size: Position size (positive = long, negative = short, 0 = flat)
        entry_price: Average entry price for the position (in cents, 0-100)
        current_price: Current market price (in cents, 0-100)
        unrealized_pnl: Unrealized profit/loss based on current price (in dollars)
        realized_pnl: Realized profit/loss from closed portions (in dollars)
        opened_at: Timestamp when position was opened
    """

    ticker: str
    size: int = 0
    entry_price: float = 0.0
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        """Validate position data after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate position data."""
        if not self.ticker:
            raise ValidationError("Position ticker cannot be empty")
        if self.entry_price < 0:
            raise ValidationError(f"entry_price cannot be negative, got {self.entry_price}")
        if self.current_price < 0:
            raise ValidationError(f"current_price cannot be negative, got {self.current_price}")

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.size > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.size < 0

    @property
    def is_flat(self) -> bool:
        """Check if position is flat (no exposure)."""
        return self.size == 0

    @property
    def exposure(self) -> float:
        """Calculate absolute exposure value (size * price in dollars)."""
        return abs(self.size) * self.current_price / 100.0

    @property
    def total_pnl(self) -> float:
        """Total P&L including unrealized."""
        return self.realized_pnl + self.unrealized_pnl

    def update_price(self, new_price: float) -> None:
        """Update current price and recalculate unrealized P&L."""
        if new_price < 0:
            raise ValidationError(f"price cannot be negative, got {new_price}")
        self.current_price = new_price
        if self.size != 0:
            # P&L in dollars: (price diff in cents) * size / 100
            self.unrealized_pnl = (new_price - self.entry_price) * self.size / 100.0


@dataclass
class MarketState:
    """Represents the current state of a market at a point in time.

    Attributes:
        ticker: Market identifier
        timestamp: When this state was observed
        bid: Best bid price (0-100 cents, or 0.0-1.0 as probability)
        ask: Best ask price (0-100 cents, or 0.0-1.0 as probability)
        mid: Mid price ((bid + ask) / 2)
        spread: Spread (ask - bid)
        last_price: Last traded price
        volume: Trading volume
    """

    ticker: str
    timestamp: datetime
    bid: float
    ask: float
    mid: float = field(init=False)
    spread: float = field(init=False)
    last_price: Optional[float] = None
    volume: int = 0

    def __post_init__(self) -> None:
        """Calculate derived fields and validate."""
        self.mid = (self.bid + self.ask) / 2
        self.spread = self.ask - self.bid
        self.validate()

    def validate(self) -> None:
        """Validate market state data."""
        if not self.ticker:
            raise ValidationError("MarketState ticker cannot be empty")
        if self.bid < 0:
            raise ValidationError(f"bid cannot be negative, got {self.bid}")
        if self.ask < 0:
            raise ValidationError(f"ask cannot be negative, got {self.ask}")
        if self.bid > self.ask:
            raise ValidationError(f"bid ({self.bid}) cannot be greater than ask ({self.ask})")
        if self.spread < 0:
            raise ValidationError(f"spread cannot be negative, got {self.spread}")


@dataclass
class Quote:
    """Represents a quote (order) placed in the market.

    Attributes:
        ticker: Market identifier
        side: 'BID' or 'ASK'
        price: Quote price (0-100 cents or 0.0-1.0 as probability)
        size: Number of contracts
        order_id: Unique identifier for this quote
        timestamp: When quote was placed
        status: Current status (PENDING, OPEN, FILLED, PARTIALLY_FILLED, CANCELED)
        filled_size: How many contracts have been filled
    """

    ticker: str
    side: str  # 'BID' or 'ASK'
    price: float
    size: int
    order_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    status: str = "PENDING"
    filled_size: int = 0

    def __post_init__(self) -> None:
        """Validate quote data."""
        self.validate()

    def validate(self) -> None:
        """Validate quote data."""
        if not self.ticker:
            raise ValidationError("Quote ticker cannot be empty")
        if self.side not in ("BID", "ASK"):
            raise ValidationError(f"Quote side must be 'BID' or 'ASK', got '{self.side}'")
        if self.price < 0:
            raise ValidationError(f"Quote price cannot be negative, got {self.price}")
        if self.size <= 0:
            raise ValidationError(f"Quote size must be positive, got {self.size}")
        if self.status not in ("PENDING", "OPEN", "FILLED", "PARTIALLY_FILLED", "CANCELED"):
            raise ValidationError(f"Invalid quote status: {self.status}")
        if self.filled_size < 0:
            raise ValidationError(f"filled_size cannot be negative, got {self.filled_size}")
        if self.filled_size > self.size:
            raise ValidationError(f"filled_size ({self.filled_size}) cannot exceed size ({self.size})")

    @property
    def remaining_size(self) -> int:
        """Return unfilled size."""
        return self.size - self.filled_size

    @property
    def is_filled(self) -> bool:
        """Check if quote is completely filled."""
        return self.filled_size >= self.size

    @property
    def is_active(self) -> bool:
        """Check if quote is still active (can be filled)."""
        return self.status in ("PENDING", "OPEN", "PARTIALLY_FILLED")


@dataclass
class Fill:
    """Represents a trade execution (fill).

    Attributes:
        ticker: Market identifier
        side: 'BID' or 'ASK' (from the perspective of the filled order)
        price: Execution price
        size: Number of contracts filled
        order_id: ID of the order that was filled
        fill_id: Unique identifier for this fill
        timestamp: When fill occurred
        fee: Transaction fee (if applicable)
    """

    ticker: str
    side: str  # 'BID' or 'ASK'
    price: float
    size: int
    order_id: str
    fill_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    fee: float = 0.0

    def __post_init__(self) -> None:
        """Validate fill data."""
        self.validate()

    def validate(self) -> None:
        """Validate fill data."""
        if not self.ticker:
            raise ValidationError("Fill ticker cannot be empty")
        if self.side not in ("BID", "ASK"):
            raise ValidationError(f"Fill side must be 'BID' or 'ASK', got '{self.side}'")
        if self.price < 0:
            raise ValidationError(f"Fill price cannot be negative, got {self.price}")
        if self.size <= 0:
            raise ValidationError(f"Fill size must be positive, got {self.size}")
        if not self.order_id:
            raise ValidationError("Fill order_id cannot be empty")
        if self.fee < 0:
            raise ValidationError(f"Fill fee cannot be negative, got {self.fee}")

    @property
    def notional_value(self) -> float:
        """Calculate notional value of the fill."""
        return self.price * self.size

    @property
    def net_value(self) -> float:
        """Calculate net value after fees."""
        return self.notional_value - self.fee
