"""Order management type definitions."""

from dataclasses import dataclass, field
from typing import Callable, Optional
from enum import Enum
from datetime import datetime


class Side(Enum):
    """Which contract type (YES or NO)."""

    YES = "yes"
    NO = "no"


class Action(Enum):
    """Trading direction (BUY or SELL)."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    """Order lifecycle status."""

    PENDING = "pending"  # Created but not submitted
    SUBMITTED = "submitted"  # Sent to exchange
    RESTING = "resting"  # On the order book
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderType(Enum):
    """Order type."""

    LIMIT = "limit"
    MARKET = "market"


# Callback type aliases (forward declared)
PartialFillCallback = Callable[["Fill"], None]
FillCallback = Callable[["Fill"], None]
StaleOrderCallback = Callable[["TrackedOrder"], None]  # Order aged out
ExpiredOrderCallback = Callable[["TrackedOrder"], None]  # Order expired at market close
RejectedOrderCallback = Callable[["TrackedOrder", str], None]  # Order rejected (+ reason)


@dataclass
class OrderRequest:
    """Request to submit an order."""

    ticker: str
    side: Side
    size: int
    action: Action
    price_cents: Optional[int] = None  # None for market orders
    order_type: OrderType = OrderType.LIMIT
    on_partial_fill: Optional[Callable] = None
    on_fill: Optional[Callable] = None
    idempotency_key: Optional[str] = None
    timeout_seconds: Optional[float] = None
    max_age_seconds: Optional[float] = None  # Order TTL - auto-cancel after this age
    allow_concurrent: bool = False  # Allow multiple pending orders on same ticker+side


@dataclass
class Fill:
    """A single fill event."""

    fill_id: str
    order_id: str
    ticker: str
    outcome: Side
    action: Action
    quantity: int
    price_cents: int
    timestamp: float

    @property
    def price_dollars(self) -> float:
        return self.price_cents / 100.0

    @property
    def notional_cents(self) -> int:
        return self.price_cents * self.quantity


@dataclass
class TrackedOrder:
    """An order being tracked by the OMS."""

    order_id: str
    ticker: str
    side: Side
    action: Action
    size: int
    price_cents: Optional[int]
    status: OrderStatus
    filled_quantity: int = 0
    avg_fill_price_cents: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    exchange: str = "kalshi"
    idempotency_key: Optional[str] = None

    # Order TTL (time-to-live) for stale order prevention
    max_age_seconds: Optional[float] = None  # If set, order canceled after this age
    expiry_time: Optional[datetime] = None  # Auto-calculated: created_at + max_age_seconds

    @property
    def remaining_quantity(self) -> int:
        return self.size - self.filled_quantity

    @property
    def is_complete(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def age_seconds(self) -> float:
        """Get current age of order in seconds."""
        return (datetime.now() - self.created_at).total_seconds()

    @property
    def is_expired(self) -> bool:
        """Check if order has exceeded max_age_seconds."""
        if self.expiry_time is None:
            return False
        return datetime.now() >= self.expiry_time


@dataclass
class OrderResult:
    """Result of an order operation."""

    success: bool
    order_id: Optional[str] = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
