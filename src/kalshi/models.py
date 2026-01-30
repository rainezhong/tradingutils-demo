"""Pydantic models for Kalshi API data structures."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class OrderSide(str, Enum):
    """Order side."""

    YES = "yes"
    NO = "no"


class OrderType(str, Enum):
    """Order type."""

    LIMIT = "limit"
    MARKET = "market"


class OrderStatus(str, Enum):
    """Order status."""

    RESTING = "resting"
    PENDING = "pending"
    EXECUTED = "executed"
    CANCELED = "canceled"


class MarketStatus(str, Enum):
    """Market status."""

    OPEN = "open"
    ACTIVE = "active"
    CLOSED = "closed"
    SETTLED = "settled"


class KalshiBalance(BaseModel):
    """Account balance information.

    Attributes:
        balance: Available balance in cents
        portfolio_value: Total portfolio value in cents
    """

    balance: int = Field(..., description="Available balance in cents")
    portfolio_value: int = Field(default=0, description="Total portfolio value in cents")

    @property
    def balance_dollars(self) -> float:
        """Get balance in dollars."""
        return self.balance / 100.0

    @property
    def portfolio_value_dollars(self) -> float:
        """Get portfolio value in dollars."""
        return self.portfolio_value / 100.0


class KalshiMarket(BaseModel):
    """Market metadata.

    Attributes:
        ticker: Unique market identifier
        title: Human-readable market title
        subtitle: Additional description
        status: Current market status
        yes_bid: Best bid price in cents (0-99)
        yes_ask: Best ask price in cents (1-100)
        last_price: Last traded price in cents
        volume: Total trading volume
        volume_24h: 24-hour volume
        open_interest: Total open interest
        close_time: When the market closes
    """

    ticker: str = Field(..., description="Market ticker")
    title: str = Field(default="", description="Market title")
    subtitle: str = Field(default="", description="Market subtitle")
    status: MarketStatus = Field(default=MarketStatus.OPEN)
    yes_bid: Optional[int] = Field(default=None, ge=0, le=99)
    yes_ask: Optional[int] = Field(default=None, ge=1, le=100)
    last_price: Optional[int] = Field(default=None, ge=0, le=100)
    volume: int = Field(default=0, ge=0)
    volume_24h: int = Field(default=0, ge=0)
    open_interest: int = Field(default=0, ge=0)
    close_time: Optional[datetime] = None

    @property
    def spread(self) -> Optional[int]:
        """Calculate spread in cents."""
        if self.yes_bid is not None and self.yes_ask is not None:
            return self.yes_ask - self.yes_bid
        return None

    @property
    def mid_price(self) -> Optional[float]:
        """Calculate mid price."""
        if self.yes_bid is not None and self.yes_ask is not None:
            return (self.yes_bid + self.yes_ask) / 2.0
        return None

    @property
    def bid_decimal(self) -> Optional[float]:
        """Get bid as decimal probability (0-1)."""
        return self.yes_bid / 100.0 if self.yes_bid is not None else None

    @property
    def ask_decimal(self) -> Optional[float]:
        """Get ask as decimal probability (0-1)."""
        return self.yes_ask / 100.0 if self.yes_ask is not None else None


class KalshiOrderRequest(BaseModel):
    """Request body for placing an order.

    Attributes:
        ticker: Market ticker
        side: Order side (yes or no)
        action: Order action (buy or sell)
        type: Order type (limit or market)
        count: Number of contracts
        yes_price: Limit price in cents for yes side
        no_price: Limit price in cents for no side
        expiration_ts: Optional expiration timestamp
        client_order_id: Optional client-provided order ID
    """

    ticker: str
    side: OrderSide
    action: str = Field(default="buy", pattern="^(buy|sell)$")
    type: OrderType = Field(default=OrderType.LIMIT)
    count: int = Field(..., gt=0)
    yes_price: Optional[int] = Field(default=None, ge=1, le=99)
    no_price: Optional[int] = Field(default=None, ge=1, le=99)
    expiration_ts: Optional[int] = None
    client_order_id: Optional[str] = None

    @field_validator("yes_price", "no_price")
    @classmethod
    def validate_price(cls, v: Optional[int], info) -> Optional[int]:
        """Validate that at least one price is provided for limit orders."""
        return v


class KalshiOrder(BaseModel):
    """Order information from Kalshi API.

    Maps to the Quote model from src/core/models.py.

    Attributes:
        order_id: Unique order identifier
        ticker: Market ticker
        side: Order side (yes or no)
        action: Order action (buy or sell)
        type: Order type
        status: Current order status
        yes_price: Price in cents
        no_price: Price in cents
        count: Total contracts
        remaining_count: Unfilled contracts
        created_time: When order was created
        expiration_time: When order expires
        client_order_id: Client-provided ID
    """

    order_id: str = Field(..., description="Order ID")
    ticker: str = Field(..., description="Market ticker")
    side: OrderSide
    action: str = Field(default="buy")
    type: OrderType = Field(default=OrderType.LIMIT)
    status: OrderStatus = Field(default=OrderStatus.PENDING)
    yes_price: Optional[int] = None
    no_price: Optional[int] = None
    count: int = Field(default=0, ge=0)
    remaining_count: int = Field(default=0, ge=0)
    created_time: Optional[datetime] = None
    expiration_time: Optional[datetime] = None
    client_order_id: Optional[str] = None

    @property
    def price(self) -> Optional[int]:
        """Get the effective price in cents."""
        return self.yes_price or self.no_price

    @property
    def price_decimal(self) -> Optional[float]:
        """Get price as decimal (0-1)."""
        p = self.price
        return p / 100.0 if p is not None else None

    @property
    def filled_count(self) -> int:
        """Get number of filled contracts."""
        return self.count - self.remaining_count

    @property
    def is_open(self) -> bool:
        """Check if order is still active."""
        return self.status in (OrderStatus.RESTING, OrderStatus.PENDING)


class KalshiFill(BaseModel):
    """Fill information from Kalshi API.

    Maps to the Fill model from src/core/models.py.

    Attributes:
        trade_id: Unique trade/fill identifier
        order_id: Associated order ID
        ticker: Market ticker
        side: Fill side
        action: Fill action
        count: Number of contracts filled
        yes_price: Fill price in cents
        no_price: Fill price in cents
        created_time: When fill occurred
        is_taker: Whether this was a taker fill
    """

    trade_id: str = Field(..., description="Trade/fill ID")
    order_id: str = Field(..., description="Order ID")
    ticker: str = Field(..., description="Market ticker")
    side: OrderSide
    action: str = Field(default="buy")
    count: int = Field(..., gt=0)
    yes_price: Optional[int] = None
    no_price: Optional[int] = None
    created_time: Optional[datetime] = None
    is_taker: bool = Field(default=True)

    @property
    def price(self) -> Optional[int]:
        """Get the fill price in cents."""
        return self.yes_price or self.no_price

    @property
    def price_decimal(self) -> Optional[float]:
        """Get price as decimal (0-1)."""
        p = self.price
        return p / 100.0 if p is not None else None


class KalshiPosition(BaseModel):
    """Position information from Kalshi API.

    Attributes:
        ticker: Market ticker
        position: Number of contracts (positive=yes, negative=no)
        market_exposure: Exposure in cents
        realized_pnl: Realized P&L in cents
        total_cost: Total cost basis in cents
    """

    ticker: str = Field(..., description="Market ticker")
    position: int = Field(default=0, description="Position size")
    market_exposure: int = Field(default=0)
    realized_pnl: int = Field(default=0)
    total_cost: int = Field(default=0)

    @property
    def is_long(self) -> bool:
        """Check if position is long (yes contracts)."""
        return self.position > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short (no contracts)."""
        return self.position < 0

    @property
    def size(self) -> int:
        """Get absolute position size."""
        return abs(self.position)


class OrderBookLevel(BaseModel):
    """Single price level in order book.

    Attributes:
        price: Price in cents
        size: Number of contracts at this level
    """

    price: int = Field(..., ge=0, le=100)
    size: int = Field(..., ge=0)


class KalshiOrderBook(BaseModel):
    """Order book data from Kalshi API.

    Attributes:
        ticker: Market ticker
        yes_bids: Bid levels for yes contracts (price, size pairs)
        no_bids: Bid levels for no contracts (price, size pairs)
        sequence: Sequence number for delta ordering
    """

    ticker: str
    yes_bids: List[List[int]] = Field(default_factory=list)
    no_bids: List[List[int]] = Field(default_factory=list)
    sequence: int = Field(default=0)

    @property
    def best_bid(self) -> Optional[int]:
        """Get best bid price in cents."""
        if self.yes_bids:
            return self.yes_bids[0][0]
        return None

    @property
    def best_ask(self) -> Optional[int]:
        """Get best ask price in cents (derived from no bids)."""
        if self.no_bids:
            # Ask = 100 - best no bid
            return 100 - self.no_bids[0][0]
        return None

    @property
    def spread(self) -> Optional[int]:
        """Calculate spread in cents."""
        bid = self.best_bid
        ask = self.best_ask
        if bid is not None and ask is not None:
            return ask - bid
        return None


class ExchangeStatus(BaseModel):
    """Exchange status information.

    Attributes:
        trading_active: Whether trading is currently active
        exchange_active: Whether the exchange is active
    """

    trading_active: bool = Field(default=True)
    exchange_active: bool = Field(default=True)
