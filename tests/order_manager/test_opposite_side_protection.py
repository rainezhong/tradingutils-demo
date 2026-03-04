"""Test opposite side position protection in KalshiOrderManager.

Ensures we can't buy both YES and NO on the same market, which would
overleverage capital on perfectly negatively correlated outcomes.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from core.order_manager.kalshi_order_manager import KalshiOrderManager
from core.order_manager.order_manager_types import (
    OrderRequest,
    Action,
    Side,
    OrderType,
    Fill,
)


@pytest.fixture
def mock_client():
    """Mock Kalshi exchange client."""
    client = MagicMock()
    client.create_order = AsyncMock(
        return_value=MagicMock(order_id="test_order_123")
    )
    return client


@pytest.fixture
def order_manager(mock_client):
    """Create order manager with mock client."""
    return KalshiOrderManager(mock_client)


@pytest.mark.asyncio
async def test_can_buy_yes_initially(order_manager):
    """Should allow buying YES when no position exists."""
    request = OrderRequest(
        ticker="TEST-ABC",
        side=Side.YES,
        action=Action.BUY,
        size=10,
        price_cents=50,
        order_type=OrderType.LIMIT,
    )

    # Should succeed
    order_id = await order_manager.submit_order(request)
    assert order_id == "test_order_123"


@pytest.mark.asyncio
async def test_blocks_opposite_side_purchase(order_manager):
    """Should prevent buying NO when holding YES position."""
    # First, simulate a YES fill
    yes_fill = Fill(
        fill_id="fill_1",
        order_id="order_1",
        ticker="TEST-ABC",
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=1234567890,
    )
    order_manager.update_position_from_fill(yes_fill)

    # Verify YES position exists
    assert order_manager.get_position("TEST-ABC", Side.YES) == 10

    # Now try to buy NO on same ticker - should raise ValueError
    no_request = OrderRequest(
        ticker="TEST-ABC",
        side=Side.NO,
        action=Action.BUY,
        size=10,
        price_cents=50,
        order_type=OrderType.LIMIT,
    )

    with pytest.raises(ValueError) as exc_info:
        await order_manager.submit_order(no_request)

    assert "already holding" in str(exc_info.value).lower()
    assert "overleverage" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_allows_same_side_accumulation(order_manager):
    """Should allow buying more of the same side."""
    # First YES purchase
    yes_fill_1 = Fill(
        fill_id="fill_1",
        order_id="order_1",
        ticker="TEST-ABC",
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=1234567890,
    )
    order_manager.update_position_from_fill(yes_fill_1)

    # Second YES purchase should be allowed
    yes_request = OrderRequest(
        ticker="TEST-ABC",
        side=Side.YES,
        action=Action.BUY,
        size=5,
        price_cents=45,
        order_type=OrderType.LIMIT,
    )

    order_id = await order_manager.submit_order(yes_request)
    assert order_id == "test_order_123"


@pytest.mark.asyncio
async def test_allows_opposite_after_close(order_manager):
    """Should allow buying opposite side after position is closed."""
    # Buy YES
    yes_fill = Fill(
        fill_id="fill_1",
        order_id="order_1",
        ticker="TEST-ABC",
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=1234567890,
    )
    order_manager.update_position_from_fill(yes_fill)

    # Sell YES (close position)
    yes_sell_fill = Fill(
        fill_id="fill_2",
        order_id="order_2",
        ticker="TEST-ABC",
        outcome=Side.YES,
        action=Action.SELL,
        quantity=10,
        price_cents=60,
        timestamp=1234567900,
    )
    order_manager.update_position_from_fill(yes_sell_fill)

    # Verify position is closed
    assert order_manager.get_position("TEST-ABC", Side.YES) == 0

    # Now buying NO should be allowed
    no_request = OrderRequest(
        ticker="TEST-ABC",
        side=Side.NO,
        action=Action.BUY,
        size=5,
        price_cents=40,
        order_type=OrderType.LIMIT,
    )

    order_id = await order_manager.submit_order(no_request)
    assert order_id == "test_order_123"


@pytest.mark.asyncio
async def test_blocks_yes_when_holding_no(order_manager):
    """Should prevent buying YES when holding NO position."""
    # First, buy NO
    no_fill = Fill(
        fill_id="fill_1",
        order_id="order_1",
        ticker="TEST-ABC",
        outcome=Side.NO,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=1234567890,
    )
    order_manager.update_position_from_fill(no_fill)

    # Verify NO position exists
    assert order_manager.get_position("TEST-ABC", Side.NO) == 10

    # Try to buy YES - should fail
    yes_request = OrderRequest(
        ticker="TEST-ABC",
        side=Side.YES,
        action=Action.BUY,
        size=10,
        price_cents=50,
        order_type=OrderType.LIMIT,
    )

    with pytest.raises(ValueError) as exc_info:
        await order_manager.submit_order(yes_request)

    assert "already holding" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_allows_selling_opposite_side(order_manager):
    """Should allow SELLING the opposite side (to close someone else's position or short)."""
    # Hold YES position
    yes_fill = Fill(
        fill_id="fill_1",
        order_id="order_1",
        ticker="TEST-ABC",
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=1234567890,
    )
    order_manager.update_position_from_fill(yes_fill)

    # Selling NO should be allowed (it's a sell action, not buy)
    no_sell_request = OrderRequest(
        ticker="TEST-ABC",
        side=Side.NO,
        action=Action.SELL,  # SELL, not BUY
        size=5,
        price_cents=40,
        order_type=OrderType.LIMIT,
    )

    # Should succeed because action is SELL
    order_id = await order_manager.submit_order(no_sell_request)
    assert order_id == "test_order_123"


@pytest.mark.asyncio
async def test_different_tickers_independent(order_manager):
    """Positions on different tickers should be independent."""
    # Buy YES on ticker A
    yes_fill_a = Fill(
        fill_id="fill_1",
        order_id="order_1",
        ticker="TEST-AAA",
        outcome=Side.YES,
        action=Action.BUY,
        quantity=10,
        price_cents=50,
        timestamp=1234567890,
    )
    order_manager.update_position_from_fill(yes_fill_a)

    # Buying NO on ticker B should be allowed (different ticker)
    no_request_b = OrderRequest(
        ticker="TEST-BBB",  # Different ticker
        side=Side.NO,
        action=Action.BUY,
        size=10,
        price_cents=50,
        order_type=OrderType.LIMIT,
    )

    order_id = await order_manager.submit_order(no_request_b)
    assert order_id == "test_order_123"
