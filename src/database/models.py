"""SQLAlchemy ORM models for the trading database.

Maps existing dataclasses to database tables with relationships,
database-specific fields, and proper indexing.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Platform(str, enum.Enum):
    """Trading platform enum."""

    KALSHI = "KALSHI"
    POLYMARKET = "POLYMARKET"


class OrderStatus(str, enum.Enum):
    """Order status enum."""

    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OpportunityStatus(str, enum.Enum):
    """Arbitrage opportunity status enum."""

    OPEN = "OPEN"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class MarketStatus(str, enum.Enum):
    """Market status enum."""

    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"
    HALTED = "HALTED"


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    type_annotation_map = {
        Dict[str, Any]: JSONB,
    }


class TimestampMixin:
    """Mixin for created_at and updated_at timestamps."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MarketModel(Base, TimestampMixin):
    """Unified market data from both platforms.

    Maps to the existing Market dataclass with additional database fields.
    """

    __tablename__ = "markets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=True),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    close_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[Optional[MarketStatus]] = mapped_column(
        Enum(MarketStatus, native_enum=True),
        default=MarketStatus.ACTIVE,
        nullable=True,
    )
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Relationships
    opportunities_as_kalshi: Mapped[List["OpportunityModel"]] = relationship(
        "OpportunityModel",
        foreign_keys="OpportunityModel.kalshi_market_id",
        back_populates="kalshi_market",
    )
    opportunities_as_polymarket: Mapped[List["OpportunityModel"]] = relationship(
        "OpportunityModel",
        foreign_keys="OpportunityModel.polymarket_market_id",
        back_populates="polymarket_market",
    )
    positions: Mapped[List["PositionModel"]] = relationship(
        "PositionModel", back_populates="market"
    )
    orders: Mapped[List["OrderModel"]] = relationship(
        "OrderModel", back_populates="market"
    )

    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_market_platform_external"),
        Index("ix_markets_platform", "platform"),
        Index("ix_markets_ticker", "ticker"),
        Index("ix_markets_status", "status"),
        Index("ix_markets_close_time", "close_time"),
    )

    def __repr__(self) -> str:
        return f"<Market {self.platform.value}:{self.ticker or self.external_id}>"


class OpportunityModel(Base, TimestampMixin):
    """Detected arbitrage opportunities between platforms."""

    __tablename__ = "opportunities"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    kalshi_market_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id", ondelete="SET NULL"),
        nullable=True,
    )
    polymarket_market_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id", ondelete="SET NULL"),
        nullable=True,
    )
    kalshi_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    polymarket_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    spread: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    net_spread: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    roi: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=Decimal("1.0")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[OpportunityStatus] = mapped_column(
        Enum(OpportunityStatus, native_enum=True),
        default=OpportunityStatus.OPEN,
        nullable=False,
    )
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Relationships
    kalshi_market: Mapped[Optional["MarketModel"]] = relationship(
        "MarketModel",
        foreign_keys=[kalshi_market_id],
        back_populates="opportunities_as_kalshi",
    )
    polymarket_market: Mapped[Optional["MarketModel"]] = relationship(
        "MarketModel",
        foreign_keys=[polymarket_market_id],
        back_populates="opportunities_as_polymarket",
    )
    orders: Mapped[List["OrderModel"]] = relationship(
        "OrderModel", back_populates="opportunity"
    )
    trades: Mapped[List["TradeModel"]] = relationship(
        "TradeModel", back_populates="opportunity"
    )

    __table_args__ = (
        Index("ix_opportunities_status", "status"),
        Index("ix_opportunities_detected_at", "detected_at"),
        Index("ix_opportunities_roi", "roi"),
        Index("ix_opportunities_open_roi", "status", "roi", postgresql_where=(status == OpportunityStatus.OPEN)),
    )

    def __repr__(self) -> str:
        return f"<Opportunity {self.id} spread={self.spread} roi={self.roi}>"


class OrderModel(Base, TimestampMixin):
    """Orders placed on either platform."""

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    opportunity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True,
    )
    market_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id", ondelete="SET NULL"),
        nullable=True,
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=True),
        nullable=False,
    )
    external_order_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    ticker: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    filled_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False, insert_default=0)
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=True),
        default=OrderStatus.PENDING,
        nullable=False,
    )
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Relationships
    opportunity: Mapped[Optional["OpportunityModel"]] = relationship(
        "OpportunityModel", back_populates="orders"
    )
    market: Mapped[Optional["MarketModel"]] = relationship(
        "MarketModel", back_populates="orders"
    )
    fills: Mapped[List["FillModel"]] = relationship(
        "FillModel", back_populates="order"
    )
    trade_as_kalshi: Mapped[Optional["TradeModel"]] = relationship(
        "TradeModel",
        foreign_keys="TradeModel.kalshi_order_id",
        back_populates="kalshi_order",
    )
    trade_as_polymarket: Mapped[Optional["TradeModel"]] = relationship(
        "TradeModel",
        foreign_keys="TradeModel.polymarket_order_id",
        back_populates="polymarket_order",
    )

    __table_args__ = (
        CheckConstraint("side IN ('BID', 'ASK', 'buy', 'sell')", name="ck_orders_side"),
        CheckConstraint("size > 0", name="ck_orders_size_positive"),
        CheckConstraint("filled_size >= 0", name="ck_orders_filled_nonnegative"),
        CheckConstraint("filled_size <= size", name="ck_orders_filled_le_size"),
        Index("ix_orders_platform", "platform"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_ticker", "ticker"),
        Index("ix_orders_external", "platform", "external_order_id"),
    )

    @property
    def remaining_size(self) -> int:
        """Return unfilled size."""
        return self.size - self.filled_size

    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.filled_size >= self.size

    def __repr__(self) -> str:
        return f"<Order {self.platform.value}:{self.ticker} {self.side} {self.size}@{self.price}>"


class TradeModel(Base, TimestampMixin):
    """Completed arbitrage trade pairs."""

    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    opportunity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True,
    )
    kalshi_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    polymarket_order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    gross_profit: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    fees: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    net_profit: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    opened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Relationships
    opportunity: Mapped[Optional["OpportunityModel"]] = relationship(
        "OpportunityModel", back_populates="trades"
    )
    kalshi_order: Mapped[Optional["OrderModel"]] = relationship(
        "OrderModel",
        foreign_keys=[kalshi_order_id],
        back_populates="trade_as_kalshi",
    )
    polymarket_order: Mapped[Optional["OrderModel"]] = relationship(
        "OrderModel",
        foreign_keys=[polymarket_order_id],
        back_populates="trade_as_polymarket",
    )

    __table_args__ = (
        Index("ix_trades_opened_at", "opened_at"),
        Index("ix_trades_closed_at", "closed_at"),
        Index("ix_trades_net_profit", "net_profit"),
    )

    def __repr__(self) -> str:
        return f"<Trade {self.id} profit={self.net_profit}>"


class PositionModel(Base, TimestampMixin):
    """Current open positions.

    Maps to the existing Position dataclass with database persistence.
    """

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    market_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("markets.id", ondelete="SET NULL"),
        nullable=True,
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=True),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), default=Decimal("0"), nullable=False
    )
    current_price: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), default=Decimal("0"), nullable=False
    )
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    opened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    market: Mapped[Optional["MarketModel"]] = relationship(
        "MarketModel", back_populates="positions"
    )

    __table_args__ = (
        UniqueConstraint("platform", "ticker", name="uq_position_platform_ticker"),
        Index("ix_positions_platform", "platform"),
        Index("ix_positions_ticker", "ticker"),
    )

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

    def __repr__(self) -> str:
        return f"<Position {self.platform.value}:{self.ticker} size={self.size}>"


class FillModel(Base, TimestampMixin):
    """Execution records.

    Maps to the existing Fill dataclass with database persistence.
    """

    __tablename__ = "fills"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    order_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=True),
        nullable=False,
    )
    external_fill_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    external_order_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str] = mapped_column(String(100), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), default=Decimal("0"), nullable=False
    )
    filled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    order: Mapped[Optional["OrderModel"]] = relationship(
        "OrderModel", back_populates="fills"
    )

    __table_args__ = (
        CheckConstraint("side IN ('BID', 'ASK', 'buy', 'sell')", name="ck_fills_side"),
        CheckConstraint("size > 0", name="ck_fills_size_positive"),
        Index("ix_fills_platform", "platform"),
        Index("ix_fills_ticker", "ticker"),
        Index("ix_fills_filled_at", "filled_at"),
        Index("ix_fills_external", "platform", "external_fill_id"),
    )

    @property
    def notional_value(self) -> Decimal:
        """Calculate notional value of the fill."""
        return self.price * self.size

    @property
    def net_value(self) -> Decimal:
        """Calculate net value after fees."""
        return self.notional_value - self.fee

    def __repr__(self) -> str:
        return f"<Fill {self.platform.value}:{self.ticker} {self.side} {self.size}@{self.price}>"


class BalanceModel(Base, TimestampMixin):
    """Capital per platform over time."""

    __tablename__ = "balances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform, native_enum=True),
        nullable=False,
    )
    available: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), default=Decimal("0"), nullable=False
    )
    reserved: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), default=Decimal("0"), nullable=False
    )
    total: Mapped[Decimal] = mapped_column(
        Numeric(14, 4), default=Decimal("0"), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_balances_platform", "platform"),
        Index("ix_balances_recorded_at", "recorded_at"),
        Index("ix_balances_platform_time", "platform", "recorded_at"),
    )

    def __repr__(self) -> str:
        return f"<Balance {self.platform.value} total={self.total}>"


class SpreadExecutionStatus(str, enum.Enum):
    """Spread execution status enum."""

    PENDING = "PENDING"
    LEG1_SUBMITTED = "LEG1_SUBMITTED"
    LEG1_FILLED = "LEG1_FILLED"
    LEG2_SUBMITTED = "LEG2_SUBMITTED"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    ROLLBACK_PENDING = "ROLLBACK_PENDING"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"
    RECOVERY_NEEDED = "RECOVERY_NEEDED"


class SpreadExecutionModel(Base, TimestampMixin):
    """Persistent spread execution tracking for crash recovery.

    This model captures the full state of a two-leg spread execution,
    allowing recovery if the system crashes mid-execution.
    """

    __tablename__ = "spread_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    spread_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    opportunity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[SpreadExecutionStatus] = mapped_column(
        Enum(SpreadExecutionStatus, native_enum=True),
        default=SpreadExecutionStatus.PENDING,
        nullable=False,
    )

    # Leg 1 details
    leg1_exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    leg1_ticker: Mapped[str] = mapped_column(String(100), nullable=False)
    leg1_side: Mapped[str] = mapped_column(String(10), nullable=False)
    leg1_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    leg1_size: Mapped[int] = mapped_column(Integer, nullable=False)
    leg1_order_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    leg1_filled_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    leg1_fill_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)

    # Leg 2 details
    leg2_exchange: Mapped[str] = mapped_column(String(50), nullable=False)
    leg2_ticker: Mapped[str] = mapped_column(String(100), nullable=False)
    leg2_side: Mapped[str] = mapped_column(String(10), nullable=False)
    leg2_price: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    leg2_size: Mapped[int] = mapped_column(Integer, nullable=False)
    leg2_order_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    leg2_filled_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    leg2_fill_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)

    # Rollback tracking
    rollback_order_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rollback_filled_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Profit tracking
    expected_profit: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )
    actual_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    total_fees: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0"), nullable=False
    )

    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_recovery_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Additional metadata
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_spread_executions_status", "status"),
        Index("ix_spread_executions_spread_id", "spread_id"),
        Index("ix_spread_executions_started_at", "started_at"),
        Index(
            "ix_spread_executions_incomplete",
            "status",
            postgresql_where=(
                status.notin_([
                    SpreadExecutionStatus.COMPLETED,
                    SpreadExecutionStatus.FAILED,
                    SpreadExecutionStatus.ROLLED_BACK,
                ])
            ),
        ),
    )

    @property
    def is_complete(self) -> bool:
        """Check if spread execution is complete."""
        return self.status in (
            SpreadExecutionStatus.COMPLETED,
            SpreadExecutionStatus.FAILED,
            SpreadExecutionStatus.ROLLED_BACK,
        )

    @property
    def needs_recovery(self) -> bool:
        """Check if spread needs recovery action."""
        return not self.is_complete

    @property
    def has_leg1_exposure(self) -> bool:
        """Check if leg 1 has unfilled exposure."""
        return self.leg1_filled_size > 0 and self.leg2_filled_size == 0

    def __repr__(self) -> str:
        return f"<SpreadExecution {self.spread_id} status={self.status.value}>"


class SystemEventModel(Base):
    """Audit log for system events."""

    __tablename__ = "system_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(
        String(20), default="INFO", nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name="ck_events_severity",
        ),
        Index("ix_events_type", "event_type"),
        Index("ix_events_severity", "severity"),
        Index("ix_events_created_at", "created_at"),
        Index("ix_events_type_time", "event_type", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<SystemEvent {self.event_type} {self.severity}>"
