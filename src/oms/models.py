"""OMS data models for multi-exchange order tracking.

Provides dataclasses for:
- TrackedOrder: Order with metadata for lifecycle tracking
- FailedOrder: Captured failure details with reason codes
- ReconciliationReport: Results of local vs exchange state comparison
- SpreadLeg: Individual leg of a spread trade
- SpreadExecutionResult: Outcome of a spread execution attempt
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from src.core.models import Position


class OrderStatus(Enum):
    """Order lifecycle states."""
    PENDING = "pending"           # Created, not yet submitted
    SUBMITTED = "submitted"       # Sent to exchange, awaiting confirmation
    OPEN = "open"                 # Confirmed open on exchange
    PARTIAL = "partial"           # Partially filled
    FILLED = "filled"             # Completely filled
    CANCELED = "canceled"         # Canceled (by user or system)
    REJECTED = "rejected"         # Rejected by exchange
    EXPIRED = "expired"           # Timed out and canceled
    FAILED = "failed"             # Submission failed after retries


class FailureReason(Enum):
    """Categorized failure reasons."""
    INSUFFICIENT_FUNDS = "insufficient_funds"
    MARKET_CLOSED = "market_closed"
    INVALID_PRICE = "invalid_price"
    INVALID_SIZE = "invalid_size"
    RATE_LIMITED = "rate_limited"
    NETWORK_ERROR = "network_error"
    EXCHANGE_ERROR = "exchange_error"
    TIMEOUT = "timeout"
    RISK_LIMIT = "risk_limit"
    UNKNOWN = "unknown"


class LegStatus(Enum):
    """Status of a spread leg."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIAL = "partial"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class SpreadExecutionStatus(Enum):
    """Overall spread execution status."""
    PENDING = "pending"
    LEG1_SUBMITTED = "leg1_submitted"
    LEG1_FILLED = "leg1_filled"
    LEG2_SUBMITTED = "leg2_submitted"
    COMPLETED = "completed"           # Both legs filled
    PARTIAL = "partial"               # One leg filled, other pending
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"       # Leg1 was unwound
    FAILED = "failed"                 # Both legs failed


def generate_idempotency_key() -> str:
    """Generate a unique idempotency key for order submission."""
    return f"OMS-{uuid.uuid4().hex[:16].upper()}"


@dataclass
class TrackedOrder:
    """An order with full lifecycle tracking metadata.

    Attributes:
        idempotency_key: Unique client-generated key to prevent duplicates
        exchange: Exchange name (e.g., 'kalshi', 'polymarket')
        ticker: Market identifier
        side: 'buy' or 'sell'
        price: Order price (0-100 cents or 0.0-1.0)
        size: Number of contracts
        order_id: Exchange-assigned order ID (set after submission)
        status: Current order status
        filled_size: Number of contracts filled
        avg_fill_price: Average fill price (if filled)
        created_at: When the order was created locally
        submitted_at: When the order was submitted to exchange
        last_update: Last status update timestamp
        timeout_at: When the order should be auto-canceled
        fills: List of fill events
        metadata: Additional tracking data
    """
    idempotency_key: str
    exchange: str
    ticker: str
    side: str  # 'buy' or 'sell'
    price: float
    size: int
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_size: int = 0
    avg_fill_price: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.now)
    submitted_at: Optional[datetime] = None
    last_update: datetime = field(default_factory=datetime.now)
    timeout_at: Optional[datetime] = None
    fills: List[Dict] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"Order side must be 'buy' or 'sell', got '{self.side}'")
        if self.price < 0:
            raise ValueError(f"Order price cannot be negative, got {self.price}")
        if self.size <= 0:
            raise ValueError(f"Order size must be positive, got {self.size}")

    @property
    def remaining_size(self) -> int:
        """Return unfilled size."""
        return self.size - self.filled_size

    @property
    def is_active(self) -> bool:
        """Check if order is still active (can be filled or canceled)."""
        return self.status in (
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.OPEN,
            OrderStatus.PARTIAL,
        )

    @property
    def is_terminal(self) -> bool:
        """Check if order has reached a terminal state."""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        )

    @property
    def notional_value(self) -> float:
        """Calculate notional value of the order."""
        return self.price * self.size

    def record_fill(self, fill_size: int, fill_price: float, timestamp: Optional[datetime] = None) -> None:
        """Record a fill event and update tracking."""
        fill = {
            "size": fill_size,
            "price": fill_price,
            "timestamp": timestamp or datetime.now(),
        }
        self.fills.append(fill)

        # Update filled size and average price
        total_filled_value = sum(f["size"] * f["price"] for f in self.fills)
        self.filled_size = sum(f["size"] for f in self.fills)
        if self.filled_size > 0:
            self.avg_fill_price = total_filled_value / self.filled_size

        # Update status
        if self.filled_size >= self.size:
            self.status = OrderStatus.FILLED
        elif self.filled_size > 0:
            self.status = OrderStatus.PARTIAL

        self.last_update = datetime.now()


@dataclass
class FailedOrder:
    """Captured details of a failed order attempt.

    Attributes:
        idempotency_key: The key used for the failed order
        exchange: Exchange where failure occurred
        ticker: Market identifier
        side: Order side
        price: Attempted price
        size: Attempted size
        reason: Categorized failure reason
        error_message: Raw error message from exchange/system
        failed_at: When the failure occurred
        retry_count: Number of retry attempts made
        original_order: Reference to TrackedOrder if it existed
    """
    idempotency_key: str
    exchange: str
    ticker: str
    side: str
    price: float
    size: int
    reason: FailureReason
    error_message: str
    failed_at: datetime = field(default_factory=datetime.now)
    retry_count: int = 0
    original_order: Optional[TrackedOrder] = None


@dataclass
class ReconciliationMismatch:
    """A single mismatch found during reconciliation.

    Attributes:
        mismatch_type: Type of mismatch found
        order_id: Order ID with mismatch
        local_value: Value in local tracking
        exchange_value: Value on exchange
        description: Human-readable description
    """
    mismatch_type: str  # 'missing_local', 'missing_exchange', 'status', 'fill_size'
    order_id: str
    local_value: Optional[str]
    exchange_value: Optional[str]
    description: str


@dataclass
class ReconciliationReport:
    """Results of comparing local state vs exchange state.

    Attributes:
        exchange: Exchange that was reconciled
        started_at: When reconciliation started
        completed_at: When reconciliation completed
        orders_checked: Number of orders compared
        positions_checked: Number of positions compared
        mismatches: List of found mismatches
        corrections_made: List of automatic corrections applied
        success: Whether reconciliation completed without errors
        error: Error message if reconciliation failed
    """
    exchange: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    orders_checked: int = 0
    positions_checked: int = 0
    mismatches: List[ReconciliationMismatch] = field(default_factory=list)
    corrections_made: List[str] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None

    @property
    def has_mismatches(self) -> bool:
        """Check if any mismatches were found."""
        return len(self.mismatches) > 0

    @property
    def duration_seconds(self) -> Optional[float]:
        """Calculate reconciliation duration."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None


@dataclass
class SpreadLeg:
    """A single leg of a spread trade.

    Attributes:
        leg_id: Unique identifier for this leg
        exchange: Exchange for this leg
        ticker: Market identifier
        side: 'buy' or 'sell'
        price: Target price
        size: Target size
        order: Associated TrackedOrder (set after submission)
        status: Current leg status
        actual_fill_price: Actual fill price (may differ from target)
        actual_fill_size: Actual filled size
    """
    leg_id: str
    exchange: str
    ticker: str
    side: str
    price: float
    size: int
    order: Optional[TrackedOrder] = None
    status: LegStatus = LegStatus.PENDING
    actual_fill_price: Optional[float] = None
    actual_fill_size: int = 0

    @property
    def is_filled(self) -> bool:
        return self.status == LegStatus.FILLED

    @property
    def slippage(self) -> Optional[float]:
        """Calculate slippage from target price."""
        if self.actual_fill_price is None:
            return None
        if self.side == "buy":
            # For buys, paying more than target is negative slippage
            return self.price - self.actual_fill_price
        else:
            # For sells, receiving less than target is negative slippage
            return self.actual_fill_price - self.price


@dataclass
class SpreadExecutionResult:
    """Outcome of a spread execution attempt.

    Attributes:
        spread_id: Unique identifier for this spread execution
        opportunity_id: Reference to the SpreadOpportunity that triggered this
        leg1: First leg (typically the entry)
        leg2: Second leg (typically the hedge/exit)
        status: Overall execution status
        started_at: When execution started
        completed_at: When execution completed
        expected_profit: Expected profit based on opportunity
        actual_profit: Actual realized profit (if both legs filled)
        total_fees: Total fees paid across both legs
        rollback_order: Order used to unwind leg1 if leg2 failed
        error: Error message if execution failed
    """
    spread_id: str
    opportunity_id: str
    leg1: SpreadLeg
    leg2: SpreadLeg
    status: SpreadExecutionStatus = SpreadExecutionStatus.PENDING
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    expected_profit: float = 0.0
    actual_profit: Optional[float] = None
    total_fees: float = 0.0
    rollback_order: Optional[TrackedOrder] = None
    error: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        """Check if spread execution is complete (success or failure)."""
        return self.status in (
            SpreadExecutionStatus.COMPLETED,
            SpreadExecutionStatus.ROLLED_BACK,
            SpreadExecutionStatus.FAILED,
        )

    @property
    def is_successful(self) -> bool:
        """Check if spread was successfully executed."""
        return self.status == SpreadExecutionStatus.COMPLETED

    @property
    def duration_seconds(self) -> Optional[float]:
        """Calculate execution duration."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def calculate_actual_profit(self) -> Optional[float]:
        """Calculate actual profit from filled legs."""
        if not (self.leg1.is_filled and self.leg2.is_filled):
            return None

        # Profit = sell proceeds - buy cost - fees
        leg1_value = (
            self.leg1.actual_fill_price * self.leg1.actual_fill_size
            if self.leg1.side == "sell"
            else -self.leg1.actual_fill_price * self.leg1.actual_fill_size
        )
        leg2_value = (
            self.leg2.actual_fill_price * self.leg2.actual_fill_size
            if self.leg2.side == "sell"
            else -self.leg2.actual_fill_price * self.leg2.actual_fill_size
        )

        self.actual_profit = leg1_value + leg2_value - self.total_fees
        return self.actual_profit


@dataclass
class PositionInventory:
    """Unified position view across exchanges.

    Attributes:
        positions: Nested dict of {exchange: {ticker: Position}}
        last_sync: Last time positions were synced with exchanges
        pending_updates: Position changes not yet confirmed
    """
    positions: Dict[str, Dict[str, Position]] = field(default_factory=dict)
    last_sync: Optional[datetime] = None
    pending_updates: List[Dict] = field(default_factory=list)

    def get_position(self, exchange: str, ticker: str) -> Optional[Position]:
        """Get position for a specific exchange and ticker."""
        return self.positions.get(exchange, {}).get(ticker)

    def set_position(self, exchange: str, ticker: str, position: Position) -> None:
        """Set position for a specific exchange and ticker."""
        if exchange not in self.positions:
            self.positions[exchange] = {}
        self.positions[exchange][ticker] = position

    def get_total_exposure(self) -> float:
        """Calculate total exposure across all positions."""
        total = 0.0
        for exchange_positions in self.positions.values():
            for position in exchange_positions.values():
                total += position.exposure
        return total

    def get_exchange_positions(self, exchange: str) -> Dict[str, Position]:
        """Get all positions for an exchange."""
        return self.positions.get(exchange, {})

    def get_all_tickers(self) -> List[str]:
        """Get all unique tickers with positions."""
        tickers = set()
        for exchange_positions in self.positions.values():
            tickers.update(exchange_positions.keys())
        return list(tickers)
