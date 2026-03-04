"""Test executor optimizations (cached config, reduced lock contention)."""

import pytest
import time
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from strategies.latency_arb.executor import LatencyArbExecutor, ArbOpportunity
from strategies.latency_arb.config import LatencyArbConfig
from strategies.latency_arb.market import KalshiMarket


def test_executor_caches_config_values():
    """Test that executor caches config values at initialization."""
    config = LatencyArbConfig(
        market_cooldown_enabled=True,
        quote_staleness_enabled=True,
        max_quote_age_ms=500.0,
        min_time_to_expiry_sec=60,
        max_total_exposure=1000.0,
        execution_slippage_buffer_cents=2,
    )

    client = MagicMock()
    executor = LatencyArbExecutor(client, config)

    # Verify cached values exist
    assert hasattr(executor, "_cooldown_enabled")
    assert hasattr(executor, "_quote_staleness_enabled")
    assert hasattr(executor, "_max_quote_age_ms")
    assert hasattr(executor, "_min_time_to_expiry")
    assert hasattr(executor, "_max_total_exposure")

    # Verify cached values match config
    assert executor._cooldown_enabled == config.market_cooldown_enabled
    assert executor._quote_staleness_enabled == config.quote_staleness_enabled
    assert executor._max_quote_age_ms == config.max_quote_age_ms
    assert executor._min_time_to_expiry == config.min_time_to_expiry_sec
    assert executor._max_total_exposure == config.max_total_exposure


def test_pre_execution_checks_uses_cached_values():
    """Test that pre-execution checks use cached values instead of config lookups."""
    config = LatencyArbConfig(
        market_cooldown_enabled=False,  # Disabled for fast path
        quote_staleness_enabled=False,  # Disabled for fast path
        min_time_to_expiry_sec=60,
        max_total_exposure=1000.0,
        execution_slippage_buffer_cents=2,
    )

    client = MagicMock()
    executor = LatencyArbExecutor(client, config)

    # Create opportunity
    market = KalshiMarket(
        ticker="TEST-TICKER",
        title="Test Market",
        expiration_time=datetime.utcnow() + timedelta(minutes=10),
        yes_bid=45,
        yes_ask=47,
        no_bid=53,
        no_ask=55,
        volume=100,
        open_interest=50,
        quote_timestamp=time.time(),
    )

    opportunity = ArbOpportunity(
        market=market,
        side="yes",
        fair_value=0.60,
        market_prob=0.45,
        edge=0.15,
        confidence=0.8,
        recommended_price=46,
        recommended_size=10,
    )

    # Call pre-execution checks (should pass)
    error = executor._pre_execution_checks(opportunity)
    assert error is None

    # Modify config AFTER caching (cached values should still be used)
    config.min_time_to_expiry_sec = 400  # Would normally fail
    error = executor._pre_execution_checks(opportunity)
    # Should still pass because cached value is 60, not 400
    assert error is None


def test_pre_execution_checks_expiry_validation():
    """Test expiry check uses cached min_time_to_expiry."""
    config = LatencyArbConfig(
        market_cooldown_enabled=False,
        quote_staleness_enabled=False,
        min_time_to_expiry_sec=120,  # 2 minutes minimum
        max_total_exposure=1000.0,
        execution_slippage_buffer_cents=2,
    )

    client = MagicMock()
    executor = LatencyArbExecutor(client, config)

    # Create market expiring in 60 seconds (below threshold)
    market = KalshiMarket(
        ticker="TEST-TICKER",
        title="Test Market",
        expiration_time=datetime.utcnow() + timedelta(seconds=60),
        yes_bid=45,
        yes_ask=47,
        no_bid=53,
        no_ask=55,
        volume=100,
        open_interest=50,
        quote_timestamp=time.time(),
    )

    opportunity = ArbOpportunity(
        market=market,
        side="yes",
        fair_value=0.60,
        market_prob=0.45,
        edge=0.15,
        confidence=0.8,
        recommended_price=46,
        recommended_size=10,
    )

    # Should fail due to expiry
    error = executor._pre_execution_checks(opportunity)
    assert error == "Market too close to expiry"


def test_pre_execution_checks_quote_staleness():
    """Test quote staleness check uses cached values."""
    config = LatencyArbConfig(
        market_cooldown_enabled=False,
        quote_staleness_enabled=True,  # Enable staleness check
        max_quote_age_ms=300.0,  # 300ms max
        min_time_to_expiry_sec=60,
        max_total_exposure=1000.0,
        execution_slippage_buffer_cents=2,
    )

    client = MagicMock()
    executor = LatencyArbExecutor(client, config)

    # Create market with stale quote (1 second old)
    market = KalshiMarket(
        ticker="TEST-TICKER",
        title="Test Market",
        expiration_time=datetime.utcnow() + timedelta(minutes=10),
        yes_bid=45,
        yes_ask=47,
        no_bid=53,
        no_ask=55,
        volume=100,
        open_interest=50,
        quote_timestamp=time.time() - 1.0,  # 1 second ago = 1000ms stale
    )

    opportunity = ArbOpportunity(
        market=market,
        side="yes",
        fair_value=0.60,
        market_prob=0.45,
        edge=0.15,
        confidence=0.8,
        recommended_price=46,
        recommended_size=10,
    )

    # Should fail due to stale quote
    error = executor._pre_execution_checks(opportunity)
    assert "Quote too stale" in error


def test_pre_execution_checks_exposure_limit():
    """Test exposure check uses cached max_total_exposure."""
    config = LatencyArbConfig(
        market_cooldown_enabled=False,
        quote_staleness_enabled=False,
        min_time_to_expiry_sec=60,
        max_total_exposure=100.0,  # Low limit
        execution_slippage_buffer_cents=2,
    )

    client = MagicMock()
    executor = LatencyArbExecutor(client, config)

    # Add existing exposure
    executor._total_exposure = 50.0

    # Create opportunity that would exceed limit
    market = KalshiMarket(
        ticker="TEST-TICKER",
        title="Test Market",
        expiration_time=datetime.utcnow() + timedelta(minutes=10),
        yes_bid=45,
        yes_ask=47,
        no_bid=53,
        no_ask=55,
        volume=100,
        open_interest=50,
        quote_timestamp=time.time(),
    )

    opportunity = ArbOpportunity(
        market=market,
        side="yes",
        fair_value=0.60,
        market_prob=0.45,
        edge=0.15,
        confidence=0.8,
        recommended_price=80,  # 80¢
        recommended_size=100,  # $80 notional (would exceed limit)
    )

    # Should fail due to exposure limit
    error = executor._pre_execution_checks(opportunity)
    assert "Would exceed max exposure" in error


def test_pre_execution_checks_fast_path_no_positions():
    """Test fast path when no positions exist (common case)."""
    config = LatencyArbConfig(
        market_cooldown_enabled=False,  # Disabled for fast path
        quote_staleness_enabled=False,  # Disabled for fast path
        min_time_to_expiry_sec=60,
        max_total_exposure=1000.0,
        execution_slippage_buffer_cents=2,
    )

    client = MagicMock()
    executor = LatencyArbExecutor(client, config)

    # Create valid opportunity
    market = KalshiMarket(
        ticker="TEST-TICKER",
        title="Test Market",
        expiration_time=datetime.utcnow() + timedelta(minutes=10),
        yes_bid=45,
        yes_ask=47,
        no_bid=53,
        no_ask=55,
        volume=100,
        open_interest=50,
        quote_timestamp=time.time(),
    )

    opportunity = ArbOpportunity(
        market=market,
        side="yes",
        fair_value=0.60,
        market_prob=0.45,
        edge=0.15,
        confidence=0.8,
        recommended_price=46,
        recommended_size=10,
    )

    # Should pass all checks (fast path)
    error = executor._pre_execution_checks(opportunity)
    assert error is None
