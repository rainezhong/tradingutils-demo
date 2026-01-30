"""Pytest fixtures for database tests."""

import asyncio
import os
from decimal import Decimal
from typing import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.database.models import (
    Base,
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
    TradeModel,
)


# Check if PostgreSQL is available for repository tests
POSTGRES_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/tradingutils_test",
)

# For model tests that don't need a real database
SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "requires_postgres: mark test as requiring PostgreSQL"
    )


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


async def check_postgres_available() -> bool:
    """Check if PostgreSQL is available."""
    try:
        engine = create_async_engine(POSTGRES_URL, poolclass=NullPool)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create test database engine with PostgreSQL."""
    # Check if PostgreSQL is available
    postgres_available = await check_postgres_available()

    if not postgres_available:
        pytest.skip("PostgreSQL not available - skipping repository tests")

    engine = create_async_engine(
        POSTGRES_URL,
        poolclass=NullPool,
        echo=False,
    )

    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Drop all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture
async def session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def sample_market() -> MarketModel:
    """Create a sample market for testing."""
    return MarketModel(
        platform=Platform.KALSHI,
        external_id="kalshi-btc-100k",
        ticker="BTC-100K-YES",
        title="Will BTC reach $100k?",
        category="Crypto",
        status=MarketStatus.ACTIVE,
        metadata_={"exchange": "kalshi"},
    )


@pytest.fixture
def sample_polymarket() -> MarketModel:
    """Create a sample Polymarket market for testing."""
    return MarketModel(
        platform=Platform.POLYMARKET,
        external_id="poly-btc-100k",
        ticker="btc-100k-yes",
        title="BTC to reach 100k",
        category="Crypto",
        status=MarketStatus.ACTIVE,
    )


@pytest.fixture
def sample_opportunity(sample_market, sample_polymarket) -> OpportunityModel:
    """Create a sample opportunity for testing."""
    return OpportunityModel(
        kalshi_price=Decimal("0.45"),
        polymarket_price=Decimal("0.52"),
        spread=Decimal("0.07"),
        net_spread=Decimal("0.05"),
        roi=Decimal("0.11"),
        confidence=Decimal("0.85"),
        status=OpportunityStatus.OPEN,
    )


@pytest.fixture
def sample_order() -> OrderModel:
    """Create a sample order for testing."""
    return OrderModel(
        platform=Platform.KALSHI,
        ticker="BTC-100K-YES",
        side="BID",
        price=Decimal("0.45"),
        size=100,
        filled_size=0,
        status=OrderStatus.PENDING,
    )


@pytest.fixture
def sample_position() -> PositionModel:
    """Create a sample position for testing."""
    return PositionModel(
        platform=Platform.KALSHI,
        ticker="BTC-100K-YES",
        size=50,
        entry_price=Decimal("0.45"),
        current_price=Decimal("0.48"),
        unrealized_pnl=Decimal("1.50"),
        realized_pnl=Decimal("0"),
    )


@pytest.fixture
def sample_fill() -> FillModel:
    """Create a sample fill for testing."""
    return FillModel(
        platform=Platform.KALSHI,
        external_order_id="ext-order-123",
        ticker="BTC-100K-YES",
        side="BID",
        price=Decimal("0.45"),
        size=50,
        fee=Decimal("0.05"),
    )


@pytest.fixture
def sample_balance() -> BalanceModel:
    """Create a sample balance for testing."""
    return BalanceModel(
        platform=Platform.KALSHI,
        available=Decimal("1000.00"),
        reserved=Decimal("250.00"),
        total=Decimal("1250.00"),
    )
