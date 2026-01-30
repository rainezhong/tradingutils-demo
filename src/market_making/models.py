"""Data models for market-making operations.

All prices use the 0-1 probability range (not cents).
These models build on top of the core data collection layer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core.models import Snapshot, ValidationError
from ..core.utils import utc_now, utc_now_iso
from .constants import (
    CENTS_TO_PROB,
    MAX_PRICE,
    MIN_PRICE,
    SIDE_ASK,
    SIDE_BID,
    VALID_SIDES,
)


@dataclass
class MarketState:
    """Current state of a market for trading decisions.

    All prices are in 0-1 probability range.

    Attributes:
        ticker: Market identifier.
        timestamp: When this state was captured.
        best_bid: Best bid price (0-1 range).
        best_ask: Best ask price (0-1 range).
        mid_price: Midpoint between bid and ask.
        bid_size: Total depth at best bid.
        ask_size: Total depth at best ask.

    Example:
        >>> state = MarketState(
        ...     ticker="AAPL-YES",
        ...     timestamp=datetime.now(),
        ...     best_bid=0.45,
        ...     best_ask=0.48,
        ...     mid_price=0.465,
        ...     bid_size=100,
        ...     ask_size=150
        ... )
        >>> state.spread_pct
        0.0645...
    """

    ticker: str
    timestamp: datetime
    best_bid: float
    best_ask: float
    mid_price: float
    bid_size: int
    ask_size: int

    def __post_init__(self) -> None:
        """Validate market state after initialization."""
        self._validate()

    def _validate(self) -> None:
        """Validate all fields."""
        if not self.ticker:
            raise ValidationError("MarketState ticker cannot be empty")

        if not isinstance(self.timestamp, datetime):
            raise ValidationError(
                f"timestamp must be datetime, got {type(self.timestamp).__name__}"
            )

        # Validate price ranges
        for name, value in [
            ("best_bid", self.best_bid),
            ("best_ask", self.best_ask),
            ("mid_price", self.mid_price),
        ]:
            if not MIN_PRICE <= value <= MAX_PRICE:
                raise ValidationError(
                    f"{name} must be between {MIN_PRICE} and {MAX_PRICE}, got {value}"
                )

        # Validate bid < ask
        if self.best_bid >= self.best_ask:
            raise ValidationError(
                f"best_bid ({self.best_bid}) must be < best_ask ({self.best_ask})"
            )

        # Validate sizes
        if self.bid_size < 0:
            raise ValidationError(f"bid_size must be non-negative, got {self.bid_size}")
        if self.ask_size < 0:
            raise ValidationError(f"ask_size must be non-negative, got {self.ask_size}")

    @property
    def spread_pct(self) -> float:
        """Calculate spread as percentage of mid price.

        Returns:
            Spread percentage (e.g., 0.05 for 5% spread).

        Example:
            >>> state = MarketState("X", datetime.now(), 0.45, 0.50, 0.475, 10, 10)
            >>> round(state.spread_pct, 4)
            0.1053
        """
        if self.mid_price == 0:
            return 0.0
        return (self.best_ask - self.best_bid) / self.mid_price

    @property
    def spread_absolute(self) -> float:
        """Calculate absolute spread.

        Returns:
            Absolute spread (e.g., 0.03 for 3 cent spread).
        """
        return self.best_ask - self.best_bid

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot) -> "MarketState":
        """Create MarketState from a core Snapshot.

        Converts from cents (0-100) to probability (0-1) range.

        Args:
            snapshot: Core layer Snapshot instance.

        Returns:
            MarketState with converted prices.

        Raises:
            ValidationError: If snapshot has missing bid/ask data.

        Example:
            >>> from src.core.models import Snapshot
            >>> snap = Snapshot("TICKER", "2024-01-01T00:00:00Z", yes_bid=45, yes_ask=48)
            >>> state = MarketState.from_snapshot(snap)
            >>> state.best_bid
            0.45
        """
        if snapshot.yes_bid is None or snapshot.yes_ask is None:
            raise ValidationError(
                f"Snapshot {snapshot.ticker} missing bid/ask data"
            )

        # Convert cents to probability
        best_bid = snapshot.yes_bid * CENTS_TO_PROB
        best_ask = snapshot.yes_ask * CENTS_TO_PROB
        mid_price = (best_bid + best_ask) / 2

        # Parse timestamp
        from ..core.utils import parse_iso_timestamp
        timestamp = parse_iso_timestamp(snapshot.timestamp)

        return cls(
            ticker=snapshot.ticker,
            timestamp=timestamp,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            bid_size=snapshot.orderbook_bid_depth or 0,
            ask_size=snapshot.orderbook_ask_depth or 0,
        )

    def to_dict(self) -> dict:
        """Convert to dictionary.

        Returns:
            Dictionary representation with ISO timestamp.
        """
        return {
            "ticker": self.ticker,
            "timestamp": self.timestamp.isoformat(),
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "spread_pct": self.spread_pct,
        }


@dataclass
class Quote:
    """A quote to be placed in the market.

    Represents an order that hasn't been submitted yet or is pending.

    Attributes:
        ticker: Market identifier.
        side: 'BID' or 'ASK'.
        price: Quote price (0-1 range).
        size: Number of contracts.
        timestamp: When quote was created.
        order_id: Exchange order ID (None if not yet submitted).

    Example:
        >>> quote = Quote(
        ...     ticker="AAPL-YES",
        ...     side="BID",
        ...     price=0.45,
        ...     size=20
        ... )
    """

    ticker: str
    side: str
    price: float
    size: int
    timestamp: datetime = field(default_factory=utc_now)
    order_id: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate quote after initialization."""
        self._validate()

    def _validate(self) -> None:
        """Validate all fields."""
        if not self.ticker:
            raise ValidationError("Quote ticker cannot be empty")

        if self.side not in VALID_SIDES:
            raise ValidationError(
                f"side must be one of {VALID_SIDES}, got '{self.side}'"
            )

        if not MIN_PRICE <= self.price <= MAX_PRICE:
            raise ValidationError(
                f"price must be between {MIN_PRICE} and {MAX_PRICE}, got {self.price}"
            )

        if self.size <= 0:
            raise ValidationError(f"size must be positive, got {self.size}")

    @property
    def is_bid(self) -> bool:
        """Check if this is a bid quote."""
        return self.side == SIDE_BID

    @property
    def is_ask(self) -> bool:
        """Check if this is an ask quote."""
        return self.side == SIDE_ASK

    @property
    def is_submitted(self) -> bool:
        """Check if quote has been submitted to exchange."""
        return self.order_id is not None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "ticker": self.ticker,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp.isoformat(),
            "order_id": self.order_id,
        }


@dataclass
class Position:
    """Current position in a market.

    Attributes:
        ticker: Market identifier.
        contracts: Number of contracts (positive=long, negative=short).
        avg_entry_price: Volume-weighted average entry price.
        unrealized_pnl: Unrealized profit/loss in dollars.
        realized_pnl: Realized profit/loss in dollars.

    Example:
        >>> pos = Position(
        ...     ticker="AAPL-YES",
        ...     contracts=50,
        ...     avg_entry_price=0.45,
        ...     unrealized_pnl=2.50,
        ...     realized_pnl=0.0
        ... )
        >>> pos.is_long
        True
    """

    ticker: str
    contracts: int  # positive = long, negative = short
    avg_entry_price: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        """Validate position after initialization."""
        self._validate()

    def _validate(self) -> None:
        """Validate all fields."""
        if not self.ticker:
            raise ValidationError("Position ticker cannot be empty")

        if self.avg_entry_price < 0:
            raise ValidationError(
                f"avg_entry_price must be non-negative, got {self.avg_entry_price}"
            )

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.contracts > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.contracts < 0

    @property
    def is_flat(self) -> bool:
        """Check if position is flat (no contracts)."""
        return self.contracts == 0

    @property
    def abs_size(self) -> int:
        """Get absolute position size."""
        return abs(self.contracts)

    @property
    def total_pnl(self) -> float:
        """Get total P&L (realized + unrealized)."""
        return self.realized_pnl + self.unrealized_pnl

    def update_unrealized_pnl(self, current_price: float) -> None:
        """Update unrealized P&L based on current price.

        Args:
            current_price: Current market price (0-1 range).

        Example:
            >>> pos = Position("X", 100, 0.45)
            >>> pos.update_unrealized_pnl(0.50)
            >>> pos.unrealized_pnl
            5.0
        """
        if self.is_flat:
            self.unrealized_pnl = 0.0
            return

        # Each contract is $1 at settlement
        # P&L = contracts * (current_price - entry_price)
        self.unrealized_pnl = self.contracts * (current_price - self.avg_entry_price)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "ticker": self.ticker,
            "contracts": self.contracts,
            "avg_entry_price": self.avg_entry_price,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "total_pnl": self.total_pnl,
        }


@dataclass
class Fill:
    """A completed trade execution.

    Attributes:
        order_id: Exchange order ID.
        ticker: Market identifier.
        side: 'BID' or 'ASK'.
        price: Execution price (0-1 range).
        size: Number of contracts filled.
        timestamp: When fill occurred.

    Example:
        >>> fill = Fill(
        ...     order_id="ORD123",
        ...     ticker="AAPL-YES",
        ...     side="BID",
        ...     price=0.45,
        ...     size=20,
        ...     timestamp=datetime.now()
        ... )
    """

    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    timestamp: datetime

    def __post_init__(self) -> None:
        """Validate fill after initialization."""
        self._validate()

    def _validate(self) -> None:
        """Validate all fields."""
        if not self.order_id:
            raise ValidationError("Fill order_id cannot be empty")

        if not self.ticker:
            raise ValidationError("Fill ticker cannot be empty")

        if self.side not in VALID_SIDES:
            raise ValidationError(
                f"side must be one of {VALID_SIDES}, got '{self.side}'"
            )

        if not MIN_PRICE <= self.price <= MAX_PRICE:
            raise ValidationError(
                f"price must be between {MIN_PRICE} and {MAX_PRICE}, got {self.price}"
            )

        if self.size <= 0:
            raise ValidationError(f"size must be positive, got {self.size}")

    @property
    def is_buy(self) -> bool:
        """Check if this was a buy fill."""
        return self.side == SIDE_BID

    @property
    def is_sell(self) -> bool:
        """Check if this was a sell fill."""
        return self.side == SIDE_ASK

    @property
    def notional_value(self) -> float:
        """Calculate notional value of fill in dollars."""
        return self.price * self.size

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp.isoformat(),
            "notional_value": self.notional_value,
        }
