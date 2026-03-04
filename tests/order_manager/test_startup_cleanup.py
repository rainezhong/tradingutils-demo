"""Tests for OMS startup cleanup (Task #9)."""

import pytest
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime

from core.order_manager.kalshi_order_manager import KalshiOrderManager
from core.order_manager.order_manager_types import (
    Action,
    Fill,
    OrderStatus,
    Side,
)


@pytest.fixture
def mock_client():
    """Mock Kalshi exchange client."""
    client = Mock()
    client.cancel_order = AsyncMock()
    client.get_order = AsyncMock(return_value={"status": "canceled", "filled_count": 0})
    client.get_orders = AsyncMock(return_value=[])
    client.get_fills = AsyncMock(return_value=[])
    # Disable WebSocket for tests
    client._auth = None
    return client


@pytest.fixture
def order_manager(mock_client):
    """Create OMS with mock client."""
    return KalshiOrderManager(mock_client, enable_websocket=False)


@pytest.mark.asyncio
async def test_initialize_cancels_resting_orders(order_manager, mock_client):
    """Test that initialize() cancels all resting orders from previous runs."""
    # Mock 3 resting orders from previous run
    mock_client.get_orders.return_value = [
        {"order_id": "order1", "ticker": "KXBTC-1"},
        {"order_id": "order2", "ticker": "KXBTC-2"},
        {"order_id": "order3", "ticker": "KXBTC-3"},
    ]

    # Initialize OMS
    await order_manager.initialize()

    # Verify cancel_order called for each resting order
    assert mock_client.cancel_order.call_count == 3
    mock_client.cancel_order.assert_any_call("order1")
    mock_client.cancel_order.assert_any_call("order2")
    mock_client.cancel_order.assert_any_call("order3")


@pytest.mark.asyncio
async def test_initialize_recovers_positions_from_fills(order_manager, mock_client):
    """Test that initialize() recovers positions from recent fills."""
    # Mock 2 fills: 1 BUY YES (open position), 1 BUY then SELL (closed)
    mock_client.get_fills.return_value = [
        {
            "trade_id": "fill1",
            "order_id": "order1",
            "ticker": "KXBTC-1",
            "side": "yes",
            "action": "buy",
            "count": 5,
            "yes_price": 50,
            "created_time": datetime.now().timestamp(),
        },
        {
            "trade_id": "fill2",
            "order_id": "order2",
            "ticker": "KXBTC-2",
            "side": "no",
            "action": "buy",
            "count": 3,
            "no_price": 60,
            "created_time": datetime.now().timestamp(),
        },
        {
            "trade_id": "fill3",
            "order_id": "order3",
            "ticker": "KXBTC-2",
            "side": "no",
            "action": "sell",
            "count": 3,
            "no_price": 65,
            "created_time": datetime.now().timestamp(),
        },
    ]

    # Initialize OMS
    await order_manager.initialize()

    # Verify positions recovered
    positions = order_manager.get_all_positions()

    # KXBTC-1 YES: 5 contracts (only BUY)
    assert positions.get(("KXBTC-1", Side.YES)) == 5

    # KXBTC-2 NO: 0 contracts (BUY 3, SELL 3)
    assert ("KXBTC-2", Side.NO) not in positions


@pytest.mark.asyncio
async def test_initialize_only_runs_once(order_manager, mock_client):
    """Test that initialize() can only be run once."""
    # First initialization
    await order_manager.initialize()
    assert order_manager._initialized is True

    # Reset mock call counts
    mock_client.cancel_order.reset_mock()
    mock_client.get_fills.reset_mock()

    # Second initialization should skip
    await order_manager.initialize()

    # Verify no additional calls
    mock_client.cancel_order.assert_not_called()
    mock_client.get_fills.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_handles_cancel_failure_gracefully(order_manager, mock_client):
    """Test that initialize() continues even if cancel_all_orders fails."""
    # Mock cancel failure
    mock_client.get_orders.side_effect = Exception("Cancel failed")

    # Initialize should not raise
    await order_manager.initialize()

    # Verify still initialized (get_fills was called)
    assert order_manager._initialized is True
    mock_client.get_fills.assert_called_once()


@pytest.mark.asyncio
async def test_initialize_handles_position_recovery_failure_gracefully(
    order_manager, mock_client
):
    """Test that initialize() continues even if get_fills fails."""
    # Mock get_fills failure
    mock_client.get_fills.side_effect = Exception("API error")

    # Initialize should not raise
    await order_manager.initialize()

    # Verify still initialized
    assert order_manager._initialized is True


@pytest.mark.asyncio
async def test_shutdown_stops_sweeper(order_manager, mock_client):
    """Test that shutdown() stops the order age sweeper."""
    # Initialize to start sweeper
    await order_manager.initialize()
    assert order_manager._sweeper_task is not None
    assert order_manager._sweeper_running is True

    # Shutdown
    await order_manager.shutdown()

    # Verify sweeper stopped
    assert order_manager._sweeper_running is False
    assert order_manager._sweeper_task.cancelled()
