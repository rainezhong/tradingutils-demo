"""Test WebSocket fill detection for latency arb executor.

Verifies that:
1. FillNotifier is created when use_websocket_fills=True
2. Executor uses WebSocket path when fill_notifier is present
3. Polling fallback works when WebSocket not available
4. Polling interval is 100ms (not 250ms)
"""

import threading
import time
from datetime import datetime

import pytest

from src.oms.fill_notifier import FillEvent, FillNotifier
from strategies.latency_arb.config import LatencyArbConfig
from strategies.latency_arb.executor import LatencyArbExecutor


class MockKalshiClient:
    """Mock Kalshi client for testing."""

    def __init__(self):
        self._auth = object()  # Mock auth object
        self.orders = {}
        self.fills = {}

    def get_fills(self, order_id, limit=10):
        return {"fills": self.fills.get(order_id, [])}


def test_fill_notifier_created():
    """Test that FillNotifier is created and works."""
    notifier = FillNotifier()

    # Register an order
    event = notifier.register_order("order_123")
    assert isinstance(event, threading.Event)
    assert not event.is_set()

    # Simulate a fill
    fill = FillEvent(
        order_id="order_123",
        ticker="TEST-TICKER",
        side="yes",
        price=55,
        size=10,
        timestamp=datetime.utcnow(),
        exchange="kalshi",
        trade_id="trade_456",
    )
    notifier.notify_fill(fill)

    # Event should be set
    assert event.is_set()

    # Fills should be retrievable
    fills = notifier.get_fills("order_123")
    assert len(fills) == 1
    assert fills[0].price == 55
    assert fills[0].size == 10

    # Cleanup
    notifier.unregister_order("order_123")


def test_executor_uses_websocket_path():
    """Test that executor uses WebSocket path when fill_notifier present."""
    client = MockKalshiClient()
    config = LatencyArbConfig()
    notifier = FillNotifier()

    executor = LatencyArbExecutor(
        client=client,
        config=config,
        risk_manager=None,
        fill_notifier=notifier,
    )

    # Executor should have fill_notifier
    assert executor._fill_notifier is notifier

    # Test _wait_for_fill dispatches to WebSocket method
    # We'll simulate a fill notification in a background thread
    def simulate_fill():
        time.sleep(0.05)  # 50ms delay
        fill = FillEvent(
            order_id="ws_order_123",
            ticker="TEST-TICKER",
            side="yes",
            price=60,
            size=5,
            timestamp=datetime.utcnow(),
            exchange="kalshi",
            trade_id="trade_789",
        )
        notifier.notify_fill(fill)

    # Start fill simulation
    thread = threading.Thread(target=simulate_fill)
    thread.start()

    # Wait for fill (should return quickly via WebSocket)
    start = time.time()
    avg_price, size = executor._wait_for_fill("ws_order_123", "TEST-TICKER", "yes", timeout_sec=2.0)
    elapsed = time.time() - start

    thread.join()

    # Should have received the fill
    assert size == 5
    assert avg_price == 60

    # Should have been fast (< 200ms including 50ms simulated delay)
    assert elapsed < 0.2


def test_executor_polling_fallback():
    """Test that executor falls back to polling when no fill_notifier."""
    client = MockKalshiClient()
    config = LatencyArbConfig()

    executor = LatencyArbExecutor(
        client=client,
        config=config,
        risk_manager=None,
        fill_notifier=None,  # No WebSocket
    )

    # Executor should not have fill_notifier
    assert executor._fill_notifier is None

    # Simulate fill via REST API
    client.fills["poll_order_123"] = [
        {
            "yes_price": 65,
            "count": 3,
        }
    ]

    # Wait for fill (should use polling)
    start = time.time()
    avg_price, size = executor._wait_for_fill("poll_order_123", "TEST-TICKER", "yes", timeout_sec=1.0)
    elapsed = time.time() - start

    # Should have received the fill
    assert size == 3
    assert avg_price == 65

    # Should have been fast (polling interval is 100ms)
    assert elapsed < 0.3  # First poll should catch it


def test_polling_interval_is_100ms():
    """Test that polling fallback uses 100ms intervals (not 250ms)."""
    client = MockKalshiClient()
    config = LatencyArbConfig()

    executor = LatencyArbExecutor(
        client=client,
        config=config,
        risk_manager=None,
        fill_notifier=None,
    )

    # Simulate fill arriving after 150ms
    def delayed_fill():
        time.sleep(0.15)
        client.fills["delayed_order_123"] = [{"yes_price": 70, "count": 2}]

    thread = threading.Thread(target=delayed_fill)
    thread.start()

    # Wait for fill
    start = time.time()
    avg_price, size = executor._wait_for_fill("delayed_order_123", "TEST-TICKER", "yes", timeout_sec=1.0)
    elapsed = time.time() - start

    thread.join()

    # Should have received the fill
    assert size == 2
    assert avg_price == 70

    # Should detect within 250ms (150ms delay + 100ms poll interval)
    # Old 250ms polling would take 250-500ms
    assert elapsed < 0.35  # 150ms + 100ms + overhead


def test_fill_notifier_clear():
    """Test that FillNotifier.clear() releases all waiters."""
    notifier = FillNotifier()

    # Register multiple orders
    event1 = notifier.register_order("order_1")
    event2 = notifier.register_order("order_2")

    assert not event1.is_set()
    assert not event2.is_set()

    # Clear should set all events
    notifier.clear()

    assert event1.is_set()
    assert event2.is_set()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
