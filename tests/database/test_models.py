"""Tests for SQLAlchemy ORM models."""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.database.models import (
    BalanceModel,
    FillModel,
    MarketModel,
    MarketStatus,
    OpportunityModel,
    OpportunityStatus,
    OrderModel,
    OrderStatus,
    Platform,
    PositionModel,
    SystemEventModel,
    TradeModel,
)


class TestPlatformEnum:
    """Tests for Platform enum."""

    def test_platform_values(self):
        """Test platform enum values."""
        assert Platform.KALSHI.value == "KALSHI"
        assert Platform.POLYMARKET.value == "POLYMARKET"

    def test_platform_is_string(self):
        """Test platform enum is string-based."""
        assert isinstance(Platform.KALSHI, str)


class TestMarketModel:
    """Tests for MarketModel."""

    def test_create_market(self):
        """Test creating a market model."""
        market = MarketModel(
            platform=Platform.KALSHI,
            external_id="kalshi-123",
            ticker="BTC-100K-YES",
            title="Will BTC reach $100k?",
            category="Crypto",
            status=MarketStatus.ACTIVE,
        )

        assert market.platform == Platform.KALSHI
        assert market.ticker == "BTC-100K-YES"
        assert market.status == MarketStatus.ACTIVE

    def test_market_repr(self):
        """Test market string representation."""
        market = MarketModel(
            platform=Platform.KALSHI,
            external_id="test",
            ticker="TEST-YES",
            title="Test Market",
        )

        assert "KALSHI" in repr(market)
        assert "TEST-YES" in repr(market)

    def test_market_with_metadata(self):
        """Test market with metadata."""
        market = MarketModel(
            platform=Platform.POLYMARKET,
            external_id="poly-123",
            title="Test",
            metadata_={"custom_field": "value", "nested": {"key": 1}},
        )

        assert market.metadata_["custom_field"] == "value"
        assert market.metadata_["nested"]["key"] == 1


class TestOpportunityModel:
    """Tests for OpportunityModel."""

    def test_create_opportunity(self):
        """Test creating an opportunity model."""
        opp = OpportunityModel(
            kalshi_price=Decimal("0.45"),
            polymarket_price=Decimal("0.52"),
            spread=Decimal("0.07"),
            net_spread=Decimal("0.05"),
            roi=Decimal("0.11"),
            confidence=Decimal("0.85"),
            status=OpportunityStatus.OPEN,
        )

        assert opp.spread == Decimal("0.07")
        assert opp.roi == Decimal("0.11")
        assert opp.status == OpportunityStatus.OPEN

    def test_opportunity_statuses(self):
        """Test opportunity status values."""
        assert OpportunityStatus.OPEN.value == "OPEN"
        assert OpportunityStatus.EXECUTING.value == "EXECUTING"
        assert OpportunityStatus.COMPLETED.value == "COMPLETED"
        assert OpportunityStatus.FAILED.value == "FAILED"
        assert OpportunityStatus.EXPIRED.value == "EXPIRED"


class TestOrderModel:
    """Tests for OrderModel."""

    def test_create_order(self):
        """Test creating an order model."""
        order = OrderModel(
            platform=Platform.KALSHI,
            ticker="BTC-100K-YES",
            side="BID",
            price=Decimal("0.45"),
            size=100,
            filled_size=0,
            status=OrderStatus.PENDING,
        )

        assert order.side == "BID"
        assert order.size == 100
        assert order.filled_size == 0

    def test_order_remaining_size(self):
        """Test order remaining size calculation."""
        order = OrderModel(
            platform=Platform.KALSHI,
            ticker="TEST",
            side="BID",
            price=Decimal("0.50"),
            size=100,
            filled_size=30,
            status=OrderStatus.PARTIALLY_FILLED,
        )

        assert order.remaining_size == 70

    def test_order_is_filled(self):
        """Test order is_filled property."""
        partial = OrderModel(
            platform=Platform.KALSHI,
            ticker="TEST",
            side="BID",
            price=Decimal("0.50"),
            size=100,
            filled_size=50,
        )
        filled = OrderModel(
            platform=Platform.KALSHI,
            ticker="TEST",
            side="BID",
            price=Decimal("0.50"),
            size=100,
            filled_size=100,
        )

        assert partial.is_filled is False
        assert filled.is_filled is True

    def test_order_repr(self):
        """Test order string representation."""
        order = OrderModel(
            platform=Platform.KALSHI,
            ticker="BTC-YES",
            side="BID",
            price=Decimal("0.45"),
            size=100,
        )

        repr_str = repr(order)
        assert "KALSHI" in repr_str
        assert "BTC-YES" in repr_str
        assert "BID" in repr_str


class TestPositionModel:
    """Tests for PositionModel."""

    def test_create_position(self):
        """Test creating a position model."""
        position = PositionModel(
            platform=Platform.KALSHI,
            ticker="BTC-100K-YES",
            size=50,
            entry_price=Decimal("0.45"),
            current_price=Decimal("0.48"),
            unrealized_pnl=Decimal("1.50"),
        )

        assert position.size == 50
        assert position.entry_price == Decimal("0.45")

    def test_position_is_long(self):
        """Test position is_long property."""
        long_pos = PositionModel(
            platform=Platform.KALSHI,
            ticker="TEST",
            size=50,
        )
        short_pos = PositionModel(
            platform=Platform.KALSHI,
            ticker="TEST",
            size=-50,
        )
        flat_pos = PositionModel(
            platform=Platform.KALSHI,
            ticker="TEST",
            size=0,
        )

        assert long_pos.is_long is True
        assert long_pos.is_short is False
        assert long_pos.is_flat is False

        assert short_pos.is_long is False
        assert short_pos.is_short is True
        assert short_pos.is_flat is False

        assert flat_pos.is_long is False
        assert flat_pos.is_short is False
        assert flat_pos.is_flat is True

    def test_position_total_pnl(self):
        """Test position total_pnl calculation."""
        position = PositionModel(
            platform=Platform.KALSHI,
            ticker="TEST",
            size=100,
            unrealized_pnl=Decimal("2.50"),
            realized_pnl=Decimal("1.00"),
        )

        assert position.total_pnl == Decimal("3.50")


class TestFillModel:
    """Tests for FillModel."""

    def test_create_fill(self):
        """Test creating a fill model."""
        fill = FillModel(
            platform=Platform.KALSHI,
            external_order_id="order-123",
            ticker="BTC-YES",
            side="BID",
            price=Decimal("0.45"),
            size=50,
            fee=Decimal("0.05"),
        )

        assert fill.price == Decimal("0.45")
        assert fill.size == 50
        assert fill.fee == Decimal("0.05")

    def test_fill_notional_value(self):
        """Test fill notional value calculation."""
        fill = FillModel(
            platform=Platform.KALSHI,
            external_order_id="order-123",
            ticker="TEST",
            side="BID",
            price=Decimal("0.50"),
            size=100,
            fee=Decimal("0.10"),
        )

        assert fill.notional_value == Decimal("50.00")

    def test_fill_net_value(self):
        """Test fill net value calculation."""
        fill = FillModel(
            platform=Platform.KALSHI,
            external_order_id="order-123",
            ticker="TEST",
            side="BID",
            price=Decimal("0.50"),
            size=100,
            fee=Decimal("0.10"),
        )

        assert fill.net_value == Decimal("49.90")


class TestBalanceModel:
    """Tests for BalanceModel."""

    def test_create_balance(self):
        """Test creating a balance model."""
        balance = BalanceModel(
            platform=Platform.KALSHI,
            available=Decimal("1000.00"),
            reserved=Decimal("250.00"),
            total=Decimal("1250.00"),
        )

        assert balance.available == Decimal("1000.00")
        assert balance.total == Decimal("1250.00")

    def test_balance_repr(self):
        """Test balance string representation."""
        balance = BalanceModel(
            platform=Platform.KALSHI,
            total=Decimal("1250.00"),
        )

        assert "KALSHI" in repr(balance)
        assert "1250" in repr(balance)


class TestSystemEventModel:
    """Tests for SystemEventModel."""

    def test_create_event(self):
        """Test creating a system event model."""
        event = SystemEventModel(
            event_type="ORDER_PLACED",
            severity="INFO",
            message="Order placed successfully",
            metadata_={"order_id": "123"},
        )

        assert event.event_type == "ORDER_PLACED"
        assert event.severity == "INFO"
        assert event.metadata_["order_id"] == "123"

    def test_event_repr(self):
        """Test event string representation."""
        event = SystemEventModel(
            event_type="ERROR",
            severity="ERROR",
            message="Something went wrong",
        )

        repr_str = repr(event)
        assert "ERROR" in repr_str


class TestTradeModel:
    """Tests for TradeModel."""

    def test_create_trade(self):
        """Test creating a trade model."""
        trade = TradeModel(
            gross_profit=Decimal("10.00"),
            fees=Decimal("0.50"),
            net_profit=Decimal("9.50"),
            opened_at=datetime.utcnow(),
        )

        assert trade.gross_profit == Decimal("10.00")
        assert trade.net_profit == Decimal("9.50")

    def test_trade_repr(self):
        """Test trade string representation."""
        trade = TradeModel(
            net_profit=Decimal("9.50"),
        )

        assert "9.50" in repr(trade)
