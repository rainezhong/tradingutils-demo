"""Test position cache optimization in KalshiOrderManager."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from core.order_manager import KalshiOrderManager
from core.order_manager.order_manager_types import (
    OrderRequest,
    Side,
    Action,
    OrderType,
    Fill,
)


class MockExchangeClient:
    """Mock exchange client for testing."""

    async def create_order(self, **kwargs):
        return MagicMock(order_id="test_order_123")


@pytest.mark.asyncio
async def test_position_cache_initialization():
    """Test that position cache is initialized correctly."""
    client = MockExchangeClient()
    om = KalshiOrderManager(client)

    assert hasattr(om, "_position_tickers")
    assert isinstance(om._position_tickers, set)
    assert len(om._position_tickers) == 0


@pytest.mark.asyncio
async def test_position_cache_updates_on_fill():
    """Test that position cache updates when fills occur."""
    client = MockExchangeClient()
    om = KalshiOrderManager(client)

    # Simulate fill
    fill = Fill(
        fill_id="fill_123",
        order_id="order_123",
        ticker="KXNBAGAME-TEST",
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=datetime.now().timestamp(),
    )

    om.update_position_from_fill(fill)

    # Check cache updated
    assert "KXNBAGAME-TEST" in om._position_tickers
    assert om.get_position("KXNBAGAME-TEST", Side.YES) == 10


@pytest.mark.asyncio
async def test_position_cache_removes_on_close():
    """Test that position cache removes ticker when position closes."""
    client = MockExchangeClient()
    om = KalshiOrderManager(client)

    ticker = "KXNBAGAME-TEST"

    # Open position
    fill_buy = Fill(
        fill_id="fill_123",
        order_id="order_123",
        ticker=ticker,
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=datetime.now().timestamp(),
    )
    om.update_position_from_fill(fill_buy)

    assert ticker in om._position_tickers
    assert om.get_position(ticker, Side.YES) == 10

    # Close position
    fill_sell = Fill(
        fill_id="fill_124",
        order_id="order_124",
        ticker=ticker,
        outcome=Side.YES,
        action=Action.SELL,
        quantity=10,
        price_cents=55,
        timestamp=datetime.now().timestamp(),
    )
    om.update_position_from_fill(fill_sell)

    # Cache should be empty
    assert ticker not in om._position_tickers
    assert om.get_position(ticker, Side.YES) == 0


@pytest.mark.asyncio
async def test_position_cache_keeps_ticker_with_opposite_side():
    """Test that cache keeps ticker if opposite side still has position."""
    client = MockExchangeClient()
    om = KalshiOrderManager(client)

    ticker = "KXNBAGAME-TEST"

    # Open YES position
    fill_yes = Fill(
        fill_id="fill_123",
        order_id="order_123",
        ticker=ticker,
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=datetime.now().timestamp(),
    )
    om.update_position_from_fill(fill_yes)

    # Open NO position
    fill_no = Fill(
        fill_id="fill_124",
        order_id="order_124",
        ticker=ticker,
        outcome=Side.NO,
        action=Action.BUY,
        quantity=5,
        price_cents=50,
        timestamp=datetime.now().timestamp(),
    )
    om.update_position_from_fill(fill_no)

    assert ticker in om._position_tickers
    assert om.get_position(ticker, Side.YES) == 10
    assert om.get_position(ticker, Side.NO) == 5

    # Close YES position
    fill_sell_yes = Fill(
        fill_id="fill_125",
        order_id="order_125",
        ticker=ticker,
        outcome=Side.YES,
        action=Action.SELL,
        quantity=10,
        price_cents=55,
        timestamp=datetime.now().timestamp(),
    )
    om.update_position_from_fill(fill_sell_yes)

    # Ticker should still be in cache (NO position exists)
    assert ticker in om._position_tickers
    assert om.get_position(ticker, Side.YES) == 0
    assert om.get_position(ticker, Side.NO) == 5


@pytest.mark.asyncio
async def test_fast_path_validation_with_cache():
    """Test that fast-path validation uses cache correctly."""
    client = MockExchangeClient()
    om = KalshiOrderManager(client)

    ticker = "KXNBAGAME-TEST"

    # Submit order with no positions (fast path - ticker not in cache)
    request1 = OrderRequest(
        ticker=ticker,
        side=Side.YES,
        action=Action.BUY,
        size=10,
        price_cents=50,
    )

    order_id1 = await om.submit_order(request1)
    assert order_id1 == "test_order_123"

    # Simulate fill to create position
    fill = Fill(
        fill_id="fill_123",
        order_id=order_id1,
        ticker=ticker,
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=datetime.now().timestamp(),
    )
    om.update_position_from_fill(fill)

    # Try to buy opposite side (should fail via cached check)
    request2 = OrderRequest(
        ticker=ticker,
        side=Side.NO,
        action=Action.BUY,
        size=5,
        price_cents=50,
    )

    with pytest.raises(ValueError, match="Cannot buy no"):
        await om.submit_order(request2)

    # Verify cache was used (ticker should be in cache)
    assert ticker in om._position_tickers


@pytest.mark.asyncio
async def test_cache_consistency_with_positions():
    """Test that cache always stays consistent with positions dict."""
    client = MockExchangeClient()
    om = KalshiOrderManager(client)

    tickers = [f"TICKER-{i}" for i in range(5)]

    # Build positions across multiple tickers
    for i, ticker in enumerate(tickers):
        fill = Fill(
            fill_id=f"fill_{i}",
            order_id=f"order_{i}",
            ticker=ticker,
            outcome=Side.YES,
            action=Action.BUY,
            quantity=10,
            price_cents=50,
            timestamp=datetime.now().timestamp(),
        )
        om.update_position_from_fill(fill)

    # Verify cache matches position keys
    position_tickers = {ticker for ticker, _ in om._positions.keys()}
    assert om._position_tickers == position_tickers

    # Close half the positions
    for i in range(0, 3):
        fill = Fill(
            fill_id=f"fill_close_{i}",
            order_id=f"order_close_{i}",
            ticker=tickers[i],
            outcome=Side.YES,
            action=Action.SELL,
            quantity=10,
            price_cents=55,
            timestamp=datetime.now().timestamp(),
        )
        om.update_position_from_fill(fill)

    # Verify cache still matches
    position_tickers = {ticker for ticker, _ in om._positions.keys()}
    assert om._position_tickers == position_tickers
    assert len(om._position_tickers) == 2
