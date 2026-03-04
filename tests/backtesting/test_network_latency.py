#!/usr/bin/env python3
"""Unit tests for network latency model in backtest framework.

Tests the apply_network_latency function with different scenarios:
- Latency sampling modes (sampled, fixed, optimistic, pessimistic)
- Adverse selection on BUY orders (spot up = ask worsens)
- Adverse selection on SELL orders (spot down = bid worsens)
- Edge cases (no timestamp, no delayed data, disabled latency)
"""

import pytest
from datetime import datetime, timedelta
from src.backtesting.fill_model import apply_network_latency
from src.backtesting.realism_config import NetworkLatencyConfig
from src.core.models import MarketState
from strategies.base import Signal


@pytest.fixture
def base_config():
    """Base latency configuration for testing."""
    return NetworkLatencyConfig(
        enabled=True,
        latency_ms=200.0,
        std_ms=50.0,
        min_latency_ms=50.0,
        max_latency_ms=1000.0,
        adverse_selection_factor=0.5,
        mode="fixed",
    )


@pytest.fixture
def signal_buy():
    """Sample BUY signal."""
    return Signal(
        ticker="TEST",
        side="BID",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
    )


@pytest.fixture
def signal_sell():
    """Sample SELL signal."""
    return Signal(
        ticker="TEST",
        side="ASK",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
    )


@pytest.fixture
def market_at_signal():
    """Market state at signal time."""
    return MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        bid=0.48,
        ask=0.52,
    )


def test_latency_disabled(base_config, signal_buy, market_at_signal):
    """Test that disabled latency returns original market."""
    base_config.enabled = False

    def get_delayed_state(ticker, ts):
        raise AssertionError("Should not be called when disabled")

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    assert adjusted == market_at_signal
    assert latency_ms == 0.0


def test_fixed_latency_mode(base_config, signal_buy, market_at_signal):
    """Test fixed latency mode always returns mean_ms."""
    base_config.mode = "fixed"
    base_config.latency_ms = 200.0

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.50,
        ask=0.54,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    assert latency_ms == 200.0


def test_optimistic_latency_mode(base_config, signal_buy, market_at_signal):
    """Test optimistic latency mode uses mean - std."""
    base_config.mode = "optimistic"
    base_config.latency_ms = 200.0
    base_config.std_ms = 50.0
    base_config.min_latency_ms = 50.0

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 150000),
        bid=0.50,
        ask=0.54,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    assert latency_ms == 150.0  # 200 - 50


def test_pessimistic_latency_mode(base_config, signal_buy, market_at_signal):
    """Test pessimistic latency mode uses mean + std."""
    base_config.mode = "pessimistic"
    base_config.latency_ms = 200.0
    base_config.std_ms = 50.0
    base_config.max_latency_ms = 1000.0

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 250000),
        bid=0.50,
        ask=0.54,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    assert latency_ms == 250.0  # 200 + 50


def test_sampled_latency_respects_bounds(base_config):
    """Test sampled latency respects min/max bounds."""
    base_config.mode = "sampled"
    base_config.latency_ms = 200.0
    base_config.std_ms = 500.0  # Large std to test bounds
    base_config.min_latency_ms = 50.0
    base_config.max_latency_ms = 1000.0

    signal = Signal(
        ticker="TEST",
        side="BID",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
    )

    market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        bid=0.48,
        ask=0.52,
    )

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.50,
        ask=0.54,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    # Sample 100 times and verify all are within bounds
    for _ in range(100):
        adjusted, latency_ms = apply_network_latency(
            signal, market, base_config, get_delayed_state
        )
        assert base_config.min_latency_ms <= latency_ms <= base_config.max_latency_ms


def test_adverse_selection_buy_spot_up(base_config, signal_buy, market_at_signal):
    """Test adverse selection on BUY when spot moves up.

    BUY order: if spot moves up during latency, ask should worsen.
    """
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 0.5

    # Spot moved from 0.50 to 0.60 (mid price)
    # mid at signal: (0.48 + 0.52) / 2 = 0.50
    # mid at delayed: (0.58 + 0.62) / 2 = 0.60
    # spot move = +0.10
    # adverse move = 0.10 * 0.5 = 0.05
    # ask should increase by 0.05: 0.62 + 0.05 = 0.67

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.58,
        ask=0.62,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    # Check that ask worsened
    assert adjusted.ask > delayed_market.ask
    assert adjusted.ask == pytest.approx(0.67, abs=0.001)
    assert adjusted.bid == delayed_market.bid  # Bid unchanged


def test_adverse_selection_buy_spot_down(base_config, signal_buy, market_at_signal):
    """Test adverse selection on BUY when spot moves down.

    BUY order: if spot moves down during latency, no penalty.
    """
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 0.5

    # Spot moved from 0.50 to 0.40 (mid price)
    # mid at signal: 0.50
    # mid at delayed: 0.40
    # spot move = -0.10
    # adverse move = -0.10 * 0.5 = -0.05 (negative, no penalty on BUY)

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.38,
        ask=0.42,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    # Ask should not worsen (adverse_move is negative)
    assert adjusted.ask == delayed_market.ask
    assert adjusted.bid == delayed_market.bid


def test_adverse_selection_sell_spot_down(base_config, signal_sell, market_at_signal):
    """Test adverse selection on SELL when spot moves down.

    SELL order: if spot moves down during latency, bid should worsen.
    """
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 0.5

    # Spot moved from 0.50 to 0.40 (mid price)
    # mid at signal: 0.50
    # mid at delayed: 0.40
    # spot move = -0.10
    # adverse move = -0.10 * 0.5 = -0.05
    # bid should decrease by 0.05: 0.38 - 0.05 = 0.33

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.38,
        ask=0.42,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_sell, market_at_signal, base_config, get_delayed_state
    )

    # Check that bid worsened
    assert adjusted.bid < delayed_market.bid
    assert adjusted.bid == pytest.approx(0.33, abs=0.001)
    assert adjusted.ask == delayed_market.ask  # Ask unchanged


def test_adverse_selection_sell_spot_up(base_config, signal_sell, market_at_signal):
    """Test adverse selection on SELL when spot moves up.

    SELL order: if spot moves up during latency, no penalty.
    """
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 0.5

    # Spot moved from 0.50 to 0.60 (mid price)
    # spot move = +0.10
    # adverse move = +0.10 * 0.5 = +0.05 (positive, no penalty on SELL)

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.58,
        ask=0.62,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_sell, market_at_signal, base_config, get_delayed_state
    )

    # Bid should not worsen (adverse_move is positive)
    assert adjusted.bid == delayed_market.bid
    assert adjusted.ask == delayed_market.ask


def test_no_adverse_selection_factor_zero(base_config, signal_buy, market_at_signal):
    """Test that adverse_selection_factor=0 means no adjustment."""
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 0.0

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.58,
        ask=0.62,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    # No adjustment with factor=0
    assert adjusted.ask == delayed_market.ask
    assert adjusted.bid == delayed_market.bid


def test_full_adverse_selection_factor_one(base_config, signal_buy, market_at_signal):
    """Test that adverse_selection_factor=1 means full spot move."""
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 1.0

    # Spot moved from 0.50 to 0.60
    # adverse move = 0.10 * 1.0 = 0.10
    # ask should increase by 0.10: 0.62 + 0.10 = 0.72

    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.58,
        ask=0.62,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    # Full adverse move
    assert adjusted.ask == pytest.approx(0.72, abs=0.001)


def test_no_timestamp_returns_original(base_config, market_at_signal):
    """Test that signal with no timestamp returns original market."""
    signal_no_ts = Signal(
        ticker="TEST",
        side="BID",
        price=0.50,
        size=10,
        confidence=0.8,
        reason="test",
        timestamp=None,
    )

    def get_delayed_state(ticker, ts):
        raise AssertionError("Should not be called when no timestamp")

    adjusted, latency_ms = apply_network_latency(
        signal_no_ts, market_at_signal, base_config, get_delayed_state
    )

    assert adjusted == market_at_signal
    assert latency_ms == 200.0  # Still sampled, but not applied


def test_no_delayed_data_returns_original(base_config, signal_buy, market_at_signal):
    """Test that missing delayed data returns original market."""
    def get_delayed_state(ticker, ts):
        return None  # No data at delayed time

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    assert adjusted == market_at_signal
    assert latency_ms == 200.0


def test_price_capped_at_one(base_config, signal_buy, market_at_signal):
    """Test that adjusted ask is capped at 1.0."""
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 1.0

    # Spot moved massively up
    # This would push ask above 1.0, but should be capped
    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.95,
        ask=0.99,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_buy, market_at_signal, base_config, get_delayed_state
    )

    # Ask should be capped at 1.0
    assert adjusted.ask <= 1.0


def test_price_floored_at_zero(base_config, signal_sell, market_at_signal):
    """Test that adjusted bid is floored at 0.0."""
    base_config.mode = "fixed"
    base_config.adverse_selection_factor = 1.0

    # Spot moved massively down
    # This would push bid below 0.0, but should be floored
    delayed_market = MarketState(
        ticker="TEST",
        timestamp=datetime(2024, 1, 1, 12, 0, 0, 200000),
        bid=0.01,
        ask=0.05,
    )

    def get_delayed_state(ticker, ts):
        return delayed_market

    adjusted, latency_ms = apply_network_latency(
        signal_sell, market_at_signal, base_config, get_delayed_state
    )

    # Bid should be floored at 0.0
    assert adjusted.bid >= 0.0
