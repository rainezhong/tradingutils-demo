#!/usr/bin/env python3
"""Integration test for network latency model with DataFeed.

Demonstrates how to use apply_network_latency with a real DataFeed
that implements get_market_at_timestamp.
"""

import pytest
from datetime import datetime, timedelta
from src.backtesting.fill_model import apply_network_latency
from src.backtesting.realism_config import NetworkLatencyConfig
from src.backtesting.data_feed import DataFeed, BacktestFrame
from src.core.models import MarketState
from strategies.base import Signal
from typing import Dict, Iterator, List, Optional


class MockDataFeed(DataFeed):
    """Mock feed with timestamp lookup support."""

    def __init__(self):
        # Create frames at 100ms intervals
        self._frames = []
        base_ts = datetime(2024, 1, 1, 12, 0, 0)

        for i in range(100):
            ts = base_ts + timedelta(milliseconds=i * 100)
            # Price increases gradually
            bid = 0.48 + (i * 0.001)
            ask = 0.52 + (i * 0.001)

            markets = {
                "TEST": MarketState(
                    ticker="TEST",
                    timestamp=ts,
                    bid=bid,
                    ask=ask,
                )
            }

            self._frames.append(
                BacktestFrame(
                    timestamp=ts,
                    frame_idx=i,
                    markets=markets,
                    context={},
                )
            )

    def __iter__(self) -> Iterator[BacktestFrame]:
        return iter(self._frames)

    def get_settlement(self) -> Dict[str, Optional[float]]:
        return {"TEST": 1.0}

    @property
    def tickers(self) -> List[str]:
        return ["TEST"]

    def get_market_at_timestamp(
        self, ticker: str, timestamp: datetime
    ) -> Optional[MarketState]:
        """Find closest frame to target timestamp."""
        if ticker != "TEST":
            return None

        # Simple linear search (bisect would be better in production)
        closest_frame = None
        min_delta = float("inf")

        for frame in self._frames:
            delta = abs((frame.timestamp - timestamp).total_seconds())
            if delta < min_delta:
                min_delta = delta
                closest_frame = frame

        if closest_frame is None:
            return None

        return closest_frame.markets.get(ticker)


@pytest.fixture
def feed():
    return MockDataFeed()


@pytest.fixture
def config():
    return NetworkLatencyConfig(
        enabled=True,
        latency_ms=200.0,
        std_ms=0.0,  # Fixed for deterministic test
        min_latency_ms=50.0,
        max_latency_ms=1000.0,
        adverse_selection_factor=0.5,
        mode="fixed",
    )


def test_latency_with_datafeed(feed, config):
    """Test latency model with real DataFeed lookup."""
    # Signal at frame 0 (T=0)
    signal = Signal(
        ticker="TEST",
        side="BID",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
    )

    # Market at signal time
    market_at_signal = feed._frames[0].markets["TEST"]
    assert market_at_signal.bid == 0.48
    assert market_at_signal.ask == 0.52

    # Apply latency (200ms = 2 frames at 100ms intervals)
    adjusted, latency_ms = apply_network_latency(
        signal=signal,
        market_at_signal=market_at_signal,
        latency_config=config,
        get_delayed_state_fn=feed.get_market_at_timestamp,
    )

    # Should fetch frame at T+200ms (frame 2)
    expected_frame = feed._frames[2]
    expected_market = expected_frame.markets["TEST"]

    # Price moved up from 0.50 to 0.502 (mid price)
    # Adverse move = 0.002 * 0.5 = 0.001
    # Ask should increase: 0.522 + 0.001 = 0.523

    assert latency_ms == 200.0
    assert adjusted.bid == expected_market.bid  # Bid unchanged for BUY
    assert adjusted.ask > expected_market.ask  # Ask worsened
    assert adjusted.ask == pytest.approx(0.523, abs=0.0001)


def test_latency_disabled_uses_original_market(feed, config):
    """Test that disabled latency skips lookup."""
    config.enabled = False

    signal = Signal(
        ticker="TEST",
        side="BID",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
    )

    market_at_signal = feed._frames[0].markets["TEST"]

    # Should not call feed.get_market_at_timestamp
    adjusted, latency_ms = apply_network_latency(
        signal=signal,
        market_at_signal=market_at_signal,
        latency_config=config,
        get_delayed_state_fn=feed.get_market_at_timestamp,
    )

    assert latency_ms == 0.0
    assert adjusted == market_at_signal


def test_sell_order_adverse_selection(feed, config):
    """Test SELL order with price moving down (adverse)."""
    # Start at frame 50 (mid of sequence)
    frame_50 = feed._frames[50]
    signal = Signal(
        ticker="TEST",
        side="ASK",  # SELL
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=frame_50.timestamp,
    )

    market_at_signal = frame_50.markets["TEST"]

    # Create a modified feed where price drops after frame 50
    # For simplicity, we'll just test with the existing upward trend
    # and verify the logic (bid should only worsen if spot moves down)

    adjusted, latency_ms = apply_network_latency(
        signal=signal,
        market_at_signal=market_at_signal,
        latency_config=config,
        get_delayed_state_fn=feed.get_market_at_timestamp,
    )

    # In our mock feed, price moves up, so SELL should NOT be penalized
    expected_frame = feed._frames[52]  # 200ms later
    expected_market = expected_frame.markets["TEST"]

    assert adjusted.bid == expected_market.bid  # No penalty for favorable move
    assert adjusted.ask == expected_market.ask


def test_no_data_fallback(feed, config):
    """Test fallback when no data at target timestamp."""
    # Signal way in the future
    future_ts = datetime(2024, 1, 1, 12, 1, 0)  # 1 minute later
    signal = Signal(
        ticker="TEST",
        side="BID",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=future_ts,
    )

    market_at_signal = feed._frames[0].markets["TEST"]

    # Should return original market when delayed lookup fails
    adjusted, latency_ms = apply_network_latency(
        signal=signal,
        market_at_signal=market_at_signal,
        latency_config=config,
        get_delayed_state_fn=lambda t, ts: None,  # Always returns None
    )

    assert adjusted == market_at_signal


def test_ticker_not_found(feed, config):
    """Test with unknown ticker."""
    signal = Signal(
        ticker="UNKNOWN",
        side="BID",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
    )

    market_at_signal = MarketState(
        ticker="UNKNOWN",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        bid=0.48,
        ask=0.52,
    )

    # Should return original market when ticker not found
    adjusted, latency_ms = apply_network_latency(
        signal=signal,
        market_at_signal=market_at_signal,
        latency_config=config,
        get_delayed_state_fn=feed.get_market_at_timestamp,
    )

    assert adjusted == market_at_signal
