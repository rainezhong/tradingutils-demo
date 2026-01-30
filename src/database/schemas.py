"""Pydantic schemas for API serialization and validation.

These schemas bridge the gap between SQLAlchemy models and API responses,
and provide validation for incoming requests.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.database.models import (
    MarketStatus,
    OpportunityStatus,
    OrderStatus,
    Platform,
)


class TimestampSchema(BaseModel):
    """Base schema with timestamp fields."""

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Market Schemas
# ============================================================================


class MarketBase(BaseModel):
    """Base market schema for common fields."""

    platform: Platform
    external_id: str
    ticker: Optional[str] = None
    title: str
    category: Optional[str] = None
    close_time: Optional[datetime] = None
    status: Optional[MarketStatus] = MarketStatus.ACTIVE
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class MarketCreate(MarketBase):
    """Schema for creating a market."""

    pass


class MarketUpdate(BaseModel):
    """Schema for updating a market."""

    ticker: Optional[str] = None
    title: Optional[str] = None
    category: Optional[str] = None
    close_time: Optional[datetime] = None
    status: Optional[MarketStatus] = None
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class MarketResponse(MarketBase, TimestampSchema):
    """Schema for market response."""

    id: UUID


class MarketWithOpportunities(MarketResponse):
    """Market with associated opportunities."""

    opportunities_as_kalshi: List["OpportunityResponse"] = []
    opportunities_as_polymarket: List["OpportunityResponse"] = []


# ============================================================================
# Opportunity Schemas
# ============================================================================


class OpportunityBase(BaseModel):
    """Base opportunity schema."""

    kalshi_market_id: Optional[UUID] = None
    polymarket_market_id: Optional[UUID] = None
    kalshi_price: Decimal
    polymarket_price: Decimal
    spread: Decimal
    net_spread: Decimal
    roi: Decimal
    confidence: Decimal = Field(default=Decimal("1.0"), ge=0, le=1)
    expires_at: Optional[datetime] = None
    status: OpportunityStatus = OpportunityStatus.OPEN
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class OpportunityCreate(OpportunityBase):
    """Schema for creating an opportunity."""

    pass


class OpportunityUpdate(BaseModel):
    """Schema for updating an opportunity."""

    status: Optional[OpportunityStatus] = None
    expires_at: Optional[datetime] = None
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class OpportunityResponse(OpportunityBase, TimestampSchema):
    """Schema for opportunity response."""

    id: UUID
    detected_at: datetime


class OpportunityWithDetails(OpportunityResponse):
    """Opportunity with related market and order details."""

    kalshi_market: Optional[MarketResponse] = None
    polymarket_market: Optional[MarketResponse] = None
    orders: List["OrderResponse"] = []


# ============================================================================
# Order Schemas
# ============================================================================


class OrderBase(BaseModel):
    """Base order schema."""

    opportunity_id: Optional[UUID] = None
    market_id: Optional[UUID] = None
    platform: Platform
    external_order_id: Optional[str] = None
    ticker: str
    side: str = Field(..., pattern="^(BID|ASK|buy|sell)$")
    price: Decimal = Field(..., ge=0)
    size: int = Field(..., gt=0)
    status: OrderStatus = OrderStatus.PENDING
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class OrderCreate(OrderBase):
    """Schema for creating an order."""

    pass


class OrderUpdate(BaseModel):
    """Schema for updating an order."""

    external_order_id: Optional[str] = None
    filled_size: Optional[int] = Field(default=None, ge=0)
    status: Optional[OrderStatus] = None
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class OrderResponse(OrderBase, TimestampSchema):
    """Schema for order response."""

    id: UUID
    filled_size: int = 0

    @property
    def remaining_size(self) -> int:
        """Return unfilled size."""
        return self.size - self.filled_size

    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.filled_size >= self.size


class OrderWithFills(OrderResponse):
    """Order with fill details."""

    fills: List["FillResponse"] = []


# ============================================================================
# Trade Schemas
# ============================================================================


class TradeBase(BaseModel):
    """Base trade schema."""

    opportunity_id: Optional[UUID] = None
    kalshi_order_id: Optional[UUID] = None
    polymarket_order_id: Optional[UUID] = None
    gross_profit: Optional[Decimal] = None
    fees: Optional[Decimal] = None
    net_profit: Optional[Decimal] = None
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class TradeCreate(TradeBase):
    """Schema for creating a trade."""

    pass


class TradeUpdate(BaseModel):
    """Schema for updating a trade."""

    gross_profit: Optional[Decimal] = None
    fees: Optional[Decimal] = None
    net_profit: Optional[Decimal] = None
    closed_at: Optional[datetime] = None
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class TradeResponse(TradeBase, TimestampSchema):
    """Schema for trade response."""

    id: UUID


class TradeWithDetails(TradeResponse):
    """Trade with order details."""

    kalshi_order: Optional[OrderResponse] = None
    polymarket_order: Optional[OrderResponse] = None
    opportunity: Optional[OpportunityResponse] = None


# ============================================================================
# Position Schemas
# ============================================================================


class PositionBase(BaseModel):
    """Base position schema."""

    market_id: Optional[UUID] = None
    platform: Platform
    ticker: str
    size: int = 0
    entry_price: Decimal = Decimal("0")
    current_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    opened_at: Optional[datetime] = None


class PositionCreate(PositionBase):
    """Schema for creating a position."""

    pass


class PositionUpdate(BaseModel):
    """Schema for updating a position."""

    size: Optional[int] = None
    entry_price: Optional[Decimal] = None
    current_price: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None
    realized_pnl: Optional[Decimal] = None


class PositionResponse(PositionBase, TimestampSchema):
    """Schema for position response."""

    id: UUID

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
        """Check if position is flat."""
        return self.size == 0

    @property
    def total_pnl(self) -> Decimal:
        """Total P&L including unrealized."""
        return self.realized_pnl + self.unrealized_pnl


# ============================================================================
# Fill Schemas
# ============================================================================


class FillBase(BaseModel):
    """Base fill schema."""

    order_id: Optional[UUID] = None
    platform: Platform
    external_fill_id: Optional[str] = None
    external_order_id: str
    ticker: str
    side: str = Field(..., pattern="^(BID|ASK|buy|sell)$")
    price: Decimal = Field(..., ge=0)
    size: int = Field(..., gt=0)
    fee: Decimal = Decimal("0")


class FillCreate(FillBase):
    """Schema for creating a fill."""

    filled_at: Optional[datetime] = None


class FillResponse(FillBase, TimestampSchema):
    """Schema for fill response."""

    id: UUID
    filled_at: datetime

    @property
    def notional_value(self) -> Decimal:
        """Calculate notional value of the fill."""
        return self.price * self.size

    @property
    def net_value(self) -> Decimal:
        """Calculate net value after fees."""
        return self.notional_value - self.fee


# ============================================================================
# Balance Schemas
# ============================================================================


class BalanceBase(BaseModel):
    """Base balance schema."""

    platform: Platform
    available: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")
    total: Decimal = Decimal("0")


class BalanceCreate(BalanceBase):
    """Schema for creating a balance record."""

    pass


class BalanceResponse(BalanceBase, TimestampSchema):
    """Schema for balance response."""

    id: UUID
    recorded_at: datetime


# ============================================================================
# System Event Schemas
# ============================================================================


class SystemEventBase(BaseModel):
    """Base system event schema."""

    event_type: str
    severity: str = Field(
        default="INFO",
        pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$",
    )
    message: str
    metadata_: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")


class SystemEventCreate(SystemEventBase):
    """Schema for creating a system event."""

    pass


class SystemEventResponse(SystemEventBase):
    """Schema for system event response."""

    id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Aggregation Schemas
# ============================================================================


class TradingSummary(BaseModel):
    """Summary of trading activity."""

    total_trades: int = 0
    total_profit: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    net_profit: Decimal = Decimal("0")
    open_opportunities: int = 0
    open_positions: int = 0
    total_exposure: Decimal = Decimal("0")


class PlatformBalance(BaseModel):
    """Balance summary for a platform."""

    platform: Platform
    available: Decimal
    reserved: Decimal
    total: Decimal
    last_updated: datetime


class PortfolioSummary(BaseModel):
    """Summary of portfolio state."""

    balances: List[PlatformBalance] = []
    total_available: Decimal = Decimal("0")
    total_reserved: Decimal = Decimal("0")
    total_value: Decimal = Decimal("0")
    positions: List[PositionResponse] = []
    unrealized_pnl: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


# Fix forward references
MarketWithOpportunities.model_rebuild()
OpportunityWithDetails.model_rebuild()
OrderWithFills.model_rebuild()
TradeWithDetails.model_rebuild()
