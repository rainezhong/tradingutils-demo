"""Tests for repository layer."""

from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio

from src.database.models import (
    MarketModel,
    MarketStatus,
    OpportunityModel,
    OpportunityStatus,
    OrderModel,
    OrderStatus,
    Platform,
    PositionModel,
)
from src.database.repository import (
    BalanceRepository,
    FillRepository,
    MarketRepository,
    OpportunityRepository,
    OrderRepository,
    PositionRepository,
    SystemEventRepository,
    TradeRepository,
)


class TestMarketRepository:
    """Tests for MarketRepository."""

    @pytest_asyncio.fixture
    async def repo(self, session):
        """Create repository instance."""
        return MarketRepository(session)

    @pytest.mark.asyncio
    async def test_create_market(self, repo, sample_market, session):
        """Test creating a market."""
        created = await repo.create(sample_market)

        assert created.id is not None
        assert created.platform == Platform.KALSHI
        assert created.ticker == "BTC-100K-YES"
        assert created.title == "Will BTC reach $100k?"

    @pytest.mark.asyncio
    async def test_get_by_id(self, repo, sample_market, session):
        """Test getting market by ID."""
        created = await repo.create(sample_market)
        await session.commit()

        fetched = await repo.get_by_id(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.ticker == created.ticker

    @pytest.mark.asyncio
    async def test_get_by_ticker(self, repo, sample_market, session):
        """Test getting market by platform and ticker."""
        await repo.create(sample_market)
        await session.commit()

        fetched = await repo.get_by_ticker(Platform.KALSHI, "BTC-100K-YES")

        assert fetched is not None
        assert fetched.ticker == "BTC-100K-YES"
        assert fetched.platform == Platform.KALSHI

    @pytest.mark.asyncio
    async def test_get_by_ticker_not_found(self, repo, session):
        """Test getting non-existent market returns None."""
        fetched = await repo.get_by_ticker(Platform.KALSHI, "NONEXISTENT")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_upsert_create(self, repo, sample_market, session):
        """Test upsert creates new market."""
        upserted = await repo.upsert(sample_market)
        await session.commit()

        assert upserted.id is not None
        assert upserted.ticker == "BTC-100K-YES"

    @pytest.mark.asyncio
    async def test_upsert_update(self, repo, sample_market, session):
        """Test upsert updates existing market."""
        # Create initial market
        created = await repo.create(sample_market)
        await session.commit()

        # Update via upsert
        updated_market = MarketModel(
            platform=Platform.KALSHI,
            external_id="kalshi-btc-100k",
            ticker="BTC-100K-YES-UPDATED",
            title="Updated title",
            status=MarketStatus.ACTIVE,
        )
        upserted = await repo.upsert(updated_market)
        await session.commit()

        assert upserted.id == created.id
        assert upserted.ticker == "BTC-100K-YES-UPDATED"
        assert upserted.title == "Updated title"

    @pytest.mark.asyncio
    async def test_get_active_markets(self, repo, session):
        """Test getting active markets."""
        # Create active and closed markets
        active = MarketModel(
            platform=Platform.KALSHI,
            external_id="active-1",
            ticker="ACTIVE-1",
            title="Active Market",
            status=MarketStatus.ACTIVE,
        )
        closed = MarketModel(
            platform=Platform.KALSHI,
            external_id="closed-1",
            ticker="CLOSED-1",
            title="Closed Market",
            status=MarketStatus.CLOSED,
        )

        await repo.create(active)
        await repo.create(closed)
        await session.commit()

        markets = await repo.get_active_markets()

        assert len(markets) == 1
        assert markets[0].ticker == "ACTIVE-1"

    @pytest.mark.asyncio
    async def test_delete_market(self, repo, sample_market, session):
        """Test deleting a market."""
        created = await repo.create(sample_market)
        await session.commit()

        deleted = await repo.delete(created.id)
        assert deleted is True

        fetched = await repo.get_by_id(created.id)
        assert fetched is None


class TestOpportunityRepository:
    """Tests for OpportunityRepository."""

    @pytest_asyncio.fixture
    async def repo(self, session):
        """Create repository instance."""
        return OpportunityRepository(session)

    @pytest.mark.asyncio
    async def test_create_opportunity(self, repo, sample_opportunity, session):
        """Test creating an opportunity."""
        created = await repo.create(sample_opportunity)

        assert created.id is not None
        assert created.spread == Decimal("0.07")
        assert created.roi == Decimal("0.11")
        assert created.status == OpportunityStatus.OPEN

    @pytest.mark.asyncio
    async def test_get_open_opportunities(self, repo, session):
        """Test getting open opportunities."""
        # Create open and closed opportunities
        open_opp = OpportunityModel(
            kalshi_price=Decimal("0.45"),
            polymarket_price=Decimal("0.52"),
            spread=Decimal("0.07"),
            net_spread=Decimal("0.05"),
            roi=Decimal("0.11"),
            status=OpportunityStatus.OPEN,
        )
        closed_opp = OpportunityModel(
            kalshi_price=Decimal("0.50"),
            polymarket_price=Decimal("0.55"),
            spread=Decimal("0.05"),
            net_spread=Decimal("0.03"),
            roi=Decimal("0.06"),
            status=OpportunityStatus.COMPLETED,
        )

        await repo.create(open_opp)
        await repo.create(closed_opp)
        await session.commit()

        opportunities = await repo.get_open_opportunities()

        assert len(opportunities) == 1
        assert opportunities[0].status == OpportunityStatus.OPEN

    @pytest.mark.asyncio
    async def test_get_open_opportunities_min_roi(self, repo, session):
        """Test getting open opportunities with minimum ROI."""
        low_roi = OpportunityModel(
            kalshi_price=Decimal("0.50"),
            polymarket_price=Decimal("0.52"),
            spread=Decimal("0.02"),
            net_spread=Decimal("0.01"),
            roi=Decimal("0.02"),
            status=OpportunityStatus.OPEN,
        )
        high_roi = OpportunityModel(
            kalshi_price=Decimal("0.45"),
            polymarket_price=Decimal("0.60"),
            spread=Decimal("0.15"),
            net_spread=Decimal("0.12"),
            roi=Decimal("0.25"),
            status=OpportunityStatus.OPEN,
        )

        await repo.create(low_roi)
        await repo.create(high_roi)
        await session.commit()

        opportunities = await repo.get_open_opportunities(min_roi=0.10)

        assert len(opportunities) == 1
        assert opportunities[0].roi == Decimal("0.25")

    @pytest.mark.asyncio
    async def test_update_status(self, repo, sample_opportunity, session):
        """Test updating opportunity status."""
        created = await repo.create(sample_opportunity)
        await session.commit()

        updated = await repo.update_status(created.id, OpportunityStatus.EXECUTING)
        await session.commit()

        assert updated is not None
        assert updated.status == OpportunityStatus.EXECUTING


class TestOrderRepository:
    """Tests for OrderRepository."""

    @pytest_asyncio.fixture
    async def repo(self, session):
        """Create repository instance."""
        return OrderRepository(session)

    @pytest.mark.asyncio
    async def test_create_order(self, repo, sample_order, session):
        """Test creating an order."""
        created = await repo.create(sample_order)

        assert created.id is not None
        assert created.ticker == "BTC-100K-YES"
        assert created.side == "BID"
        assert created.size == 100
        assert created.filled_size == 0
        assert created.status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_open_orders(self, repo, session):
        """Test getting open orders."""
        pending = OrderModel(
            platform=Platform.KALSHI,
            ticker="ORDER-1",
            side="BID",
            price=Decimal("0.45"),
            size=100,
            status=OrderStatus.PENDING,
        )
        filled = OrderModel(
            platform=Platform.KALSHI,
            ticker="ORDER-2",
            side="ASK",
            price=Decimal("0.55"),
            size=50,
            filled_size=50,
            status=OrderStatus.FILLED,
        )

        await repo.create(pending)
        await repo.create(filled)
        await session.commit()

        orders = await repo.get_open_orders()

        assert len(orders) == 1
        assert orders[0].status == OrderStatus.PENDING

    @pytest.mark.asyncio
    async def test_update_fill(self, repo, sample_order, session):
        """Test updating order fill."""
        created = await repo.create(sample_order)
        await session.commit()

        updated = await repo.update_fill(created.id, filled_size=50)
        await session.commit()

        assert updated.filled_size == 50
        assert updated.status == OrderStatus.PARTIALLY_FILLED

        # Fill completely
        updated = await repo.update_fill(created.id, filled_size=100)
        await session.commit()

        assert updated.filled_size == 100
        assert updated.status == OrderStatus.FILLED

    @pytest.mark.asyncio
    async def test_cancel_order(self, repo, sample_order, session):
        """Test canceling an order."""
        created = await repo.create(sample_order)
        await session.commit()

        canceled = await repo.cancel_order(created.id)
        await session.commit()

        assert canceled.status == OrderStatus.CANCELED


class TestPositionRepository:
    """Tests for PositionRepository."""

    @pytest_asyncio.fixture
    async def repo(self, session):
        """Create repository instance."""
        return PositionRepository(session)

    @pytest.mark.asyncio
    async def test_create_position(self, repo, sample_position, session):
        """Test creating a position."""
        created = await repo.create(sample_position)

        assert created.id is not None
        assert created.ticker == "BTC-100K-YES"
        assert created.size == 50
        assert created.entry_price == Decimal("0.45")

    @pytest.mark.asyncio
    async def test_get_by_ticker(self, repo, sample_position, session):
        """Test getting position by ticker."""
        await repo.create(sample_position)
        await session.commit()

        fetched = await repo.get_by_ticker(Platform.KALSHI, "BTC-100K-YES")

        assert fetched is not None
        assert fetched.size == 50

    @pytest.mark.asyncio
    async def test_get_open_positions(self, repo, session):
        """Test getting open positions."""
        open_pos = PositionModel(
            platform=Platform.KALSHI,
            ticker="OPEN-POS",
            size=50,
            entry_price=Decimal("0.50"),
            current_price=Decimal("0.55"),
        )
        flat_pos = PositionModel(
            platform=Platform.KALSHI,
            ticker="FLAT-POS",
            size=0,
            entry_price=Decimal("0.50"),
            current_price=Decimal("0.55"),
        )

        await repo.create(open_pos)
        await repo.create(flat_pos)
        await session.commit()

        positions = await repo.get_open_positions()

        assert len(positions) == 1
        assert positions[0].ticker == "OPEN-POS"

    @pytest.mark.asyncio
    async def test_upsert_position(self, repo, sample_position, session):
        """Test upserting a position."""
        # Create initial position
        created = await repo.upsert(sample_position)
        await session.commit()

        # Update via upsert
        updated_pos = PositionModel(
            platform=Platform.KALSHI,
            ticker="BTC-100K-YES",
            size=100,  # Updated size
            entry_price=Decimal("0.45"),
            current_price=Decimal("0.50"),
        )
        upserted = await repo.upsert(updated_pos)
        await session.commit()

        assert upserted.id == created.id
        assert upserted.size == 100

    @pytest.mark.asyncio
    async def test_close_position(self, repo, sample_position, session):
        """Test closing a position."""
        created = await repo.create(sample_position)
        await session.commit()

        closed = await repo.close_position(
            Platform.KALSHI,
            "BTC-100K-YES",
            realized_pnl=Decimal("2.50"),
        )
        await session.commit()

        assert closed.size == 0
        assert closed.unrealized_pnl == Decimal("0")
        assert closed.realized_pnl == Decimal("4.00")  # 1.50 + 2.50


class TestFillRepository:
    """Tests for FillRepository."""

    @pytest_asyncio.fixture
    async def repo(self, session):
        """Create repository instance."""
        return FillRepository(session)

    @pytest.mark.asyncio
    async def test_create_fill(self, repo, sample_fill, session):
        """Test creating a fill."""
        created = await repo.create(sample_fill)

        assert created.id is not None
        assert created.ticker == "BTC-100K-YES"
        assert created.price == Decimal("0.45")
        assert created.size == 50
        assert created.fee == Decimal("0.05")


class TestBalanceRepository:
    """Tests for BalanceRepository."""

    @pytest_asyncio.fixture
    async def repo(self, session):
        """Create repository instance."""
        return BalanceRepository(session)

    @pytest.mark.asyncio
    async def test_record_balance(self, repo, session):
        """Test recording a balance."""
        balance = await repo.record_balance(
            platform=Platform.KALSHI,
            available=Decimal("1000.00"),
            reserved=Decimal("250.00"),
        )
        await session.commit()

        assert balance.id is not None
        assert balance.total == Decimal("1250.00")

    @pytest.mark.asyncio
    async def test_get_latest(self, repo, session):
        """Test getting latest balance."""
        await repo.record_balance(Platform.KALSHI, Decimal("1000"), Decimal("0"))
        await session.commit()
        await repo.record_balance(Platform.KALSHI, Decimal("1100"), Decimal("100"))
        await session.commit()

        latest = await repo.get_latest(Platform.KALSHI)

        assert latest.available == Decimal("1100")
        assert latest.reserved == Decimal("100")


class TestSystemEventRepository:
    """Tests for SystemEventRepository."""

    @pytest_asyncio.fixture
    async def repo(self, session):
        """Create repository instance."""
        return SystemEventRepository(session)

    @pytest.mark.asyncio
    async def test_log_event(self, repo, session):
        """Test logging a system event."""
        event = await repo.log_event(
            event_type="ORDER_PLACED",
            message="Order placed successfully",
            severity="INFO",
            metadata={"order_id": "123"},
        )
        await session.commit()

        assert event.id is not None
        assert event.event_type == "ORDER_PLACED"
        assert event.severity == "INFO"

    @pytest.mark.asyncio
    async def test_get_events_by_type(self, repo, session):
        """Test getting events by type."""
        await repo.log_event("ORDER_PLACED", "Order 1")
        await repo.log_event("ORDER_FILLED", "Fill 1")
        await repo.log_event("ORDER_PLACED", "Order 2")
        await session.commit()

        events = await repo.get_events(event_type="ORDER_PLACED")

        assert len(events) == 2
