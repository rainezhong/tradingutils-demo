"""Tests for cancel order validation (Task #11)."""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, Mock
from datetime import datetime

from core.order_manager.kalshi_order_manager import KalshiOrderManager
from core.order_manager.order_manager_types import (
    Action,
    OrderRequest,
    OrderStatus,
    OrderType,
    Side,
)


@pytest.fixture
def mock_client():
    """Mock Kalshi exchange client."""
    client = Mock()
    client.create_order = AsyncMock(
        return_value=Mock(order_id="test_order_1")
    )
    client.cancel_order = AsyncMock()
    client.get_order = AsyncMock()
    client.get_orders = AsyncMock(return_value=[])
    client.get_fills = AsyncMock(return_value=[])
    # Disable WebSocket for tests
    client._auth = None
    return client


@pytest_asyncio.fixture
async def order_manager(mock_client):
    """Create and initialize OMS with mock client."""
    om = KalshiOrderManager(mock_client, enable_websocket=False)
    await om.initialize()
    yield om
    await om.shutdown()


@pytest.mark.asyncio
async def test_cancel_verifies_actual_status(order_manager, mock_client):
    """Test that cancel_order verifies order is actually canceled."""
    # Submit order
    request = OrderRequest(
        ticker="KXBTC-1",
        side=Side.YES,
        action=Action.BUY,
        size=5,
        price_cents=50,
    )
    order_id = await order_manager.submit_order(request)

    # Mock get_order to return CANCELED status
    mock_client.get_order.return_value = {
        "status": "canceled",
        "filled_count": 0,
    }

    # Cancel should return True (verified)
    result = await order_manager.cancel_order(order_id)
    assert result is True

    # Verify status updated
    tracked = order_manager.get_tracked_orders()[order_id]
    assert tracked.status == OrderStatus.CANCELED


@pytest.mark.asyncio
async def test_cancel_detects_fill_during_cancel(order_manager, mock_client):
    """Test that cancel_order detects if order filled during cancellation."""
    # Submit order
    request = OrderRequest(
        ticker="KXBTC-1",
        side=Side.YES,
        action=Action.BUY,
        size=5,
        price_cents=50,
    )
    order_id = await order_manager.submit_order(request)

    # Mock: order filled before cancel completed
    mock_client.get_order.return_value = {
        "status": "filled",
        "filled_count": 5,
    }

    # Mock fill data
    mock_client.get_fills.return_value = [
        {
            "trade_id": "fill1",
            "order_id": order_id,
            "ticker": "KXBTC-1",
            "side": "yes",
            "action": "buy",
            "count": 5,
            "yes_price": 50,
            "created_time": datetime.now().timestamp(),
        }
    ]

    # Cancel should return False (not canceled, it filled)
    result = await order_manager.cancel_order(order_id)
    assert result is False

    # Verify status updated to FILLED
    tracked = order_manager.get_tracked_orders()[order_id]
    assert tracked.status == OrderStatus.FILLED
    assert tracked.filled_quantity == 5


@pytest.mark.asyncio
async def test_cancel_detects_partial_fill(order_manager, mock_client):
    """Test that cancel_order detects partial fills before canceling."""
    # Submit order for 10 contracts
    request = OrderRequest(
        ticker="KXBTC-1",
        side=Side.YES,
        action=Action.BUY,
        size=10,
        price_cents=50,
    )
    order_id = await order_manager.submit_order(request)

    # Mock: partially filled (5/10) then canceled
    mock_client.get_order.return_value = {
        "status": "canceled",
        "filled_count": 5,
    }

    # Mock partial fill data
    mock_client.get_fills.return_value = [
        {
            "trade_id": "fill1",
            "order_id": order_id,
            "ticker": "KXBTC-1",
            "side": "yes",
            "action": "buy",
            "count": 5,
            "yes_price": 50,
            "created_time": datetime.now().timestamp(),
        }
    ]

    # Cancel should return True (canceled after partial fill)
    result = await order_manager.cancel_order(order_id)
    assert result is True

    # Verify partial fill tracked
    tracked = order_manager.get_tracked_orders()[order_id]
    assert tracked.status == OrderStatus.CANCELED
    assert tracked.filled_quantity == 5

    # Verify position updated from partial fill
    positions = order_manager.get_all_positions()
    assert positions.get(("KXBTC-1", Side.YES)) == 5


@pytest.mark.asyncio
async def test_cancel_triggers_fill_callback_on_fill(order_manager, mock_client):
    """Test that cancel_order triggers fill callback if order filled."""
    fill_callback_called = False
    fill_order = None

    def on_fill(order, fill):
        nonlocal fill_callback_called, fill_order
        fill_callback_called = True
        fill_order = order

    order_manager.set_on_fill_callback(on_fill)

    # Submit order
    request = OrderRequest(
        ticker="KXBTC-1",
        side=Side.YES,
        action=Action.BUY,
        size=5,
        price_cents=50,
    )
    order_id = await order_manager.submit_order(request)

    # Mock: order filled during cancel
    mock_client.get_order.return_value = {
        "status": "filled",
        "filled_count": 5,
    }

    mock_client.get_fills.return_value = [
        {
            "trade_id": "fill1",
            "order_id": order_id,
            "ticker": "KXBTC-1",
            "side": "yes",
            "action": "buy",
            "count": 5,
            "yes_price": 50,
            "created_time": datetime.now().timestamp(),
        }
    ]

    # Cancel (which detects fill)
    await order_manager.cancel_order(order_id)

    # Verify fill callback triggered
    assert fill_callback_called
    assert fill_order.order_id == order_id


@pytest.mark.asyncio
async def test_cancel_retries_until_verified(order_manager, mock_client):
    """Test that cancel_order retries if status not CANCELED."""
    # Submit order
    request = OrderRequest(
        ticker="KXBTC-1",
        side=Side.YES,
        action=Action.BUY,
        size=5,
        price_cents=50,
    )
    order_id = await order_manager.submit_order(request)

    # Mock: first 2 checks still RESTING, 3rd check CANCELED
    get_order_responses = [
        {"status": "resting", "filled_count": 0},
        {"status": "resting", "filled_count": 0},
        {"status": "canceled", "filled_count": 0},
    ]
    mock_client.get_order.side_effect = get_order_responses

    # Cancel should retry and eventually succeed
    result = await order_manager.cancel_order(order_id, max_retries=5, retry_delay=0.1)
    assert result is True

    # Verify get_order called 3 times (verification after each cancel attempt)
    assert mock_client.get_order.call_count == 3


@pytest.mark.asyncio
async def test_cancel_callback_triggered_on_success(order_manager, mock_client):
    """Test that on_cancel callback is triggered when order successfully canceled."""
    cancel_callback_called = False
    canceled_order = None

    def on_cancel(order):
        nonlocal cancel_callback_called, canceled_order
        cancel_callback_called = True
        canceled_order = order

    order_manager.set_on_cancel_callback(on_cancel)

    # Submit order
    request = OrderRequest(
        ticker="KXBTC-1",
        side=Side.YES,
        action=Action.BUY,
        size=5,
        price_cents=50,
    )
    order_id = await order_manager.submit_order(request)

    # Mock successful cancel
    mock_client.get_order.return_value = {
        "status": "canceled",
        "filled_count": 0,
    }

    # Cancel
    await order_manager.cancel_order(order_id)

    # Verify callback triggered
    assert cancel_callback_called
    assert canceled_order.order_id == order_id
