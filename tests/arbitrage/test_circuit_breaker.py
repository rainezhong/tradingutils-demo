"""Tests for the circuit breaker."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.arbitrage.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerState,
    CircuitBreakerMetrics,
    TripEvent,
)
from src.arbitrage.config import ArbitrageConfig


class TestCircuitBreaker:
    """Test suite for CircuitBreaker."""

    def test_init_closed(self, config):
        """Test that circuit breaker starts closed."""
        cb = CircuitBreaker(config)
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.is_closed()

    def test_init_with_alert_callback(self, config):
        """Test initialization with alert callback."""
        callback = MagicMock()
        cb = CircuitBreaker(config, alert_callback=callback)
        assert cb._alert_callback is callback

    def test_check_no_trip_on_healthy_metrics(self, circuit_breaker):
        """Test that check doesn't trip on healthy metrics."""
        # Record some successful trades
        for _ in range(5):
            circuit_breaker.record_trade(success=True, latency=0.5, pnl=10.0)

        state = circuit_breaker.check()
        assert state == CircuitBreakerState.CLOSED

    def test_trip_on_daily_loss_limit(self, circuit_breaker):
        """Test tripping on daily loss limit."""
        # Record losses exceeding the limit
        for _ in range(10):
            circuit_breaker.record_trade(success=True, latency=0.5, pnl=-60.0)

        state = circuit_breaker.check()
        assert state == CircuitBreakerState.OPEN
        assert circuit_breaker.current_trip is not None
        assert "loss" in circuit_breaker.current_trip.reason.lower()

    def test_trip_on_error_rate(self, circuit_breaker):
        """Test tripping on high error rate."""
        # Record mostly failures
        for _ in range(10):
            circuit_breaker.record_trade(success=False, latency=0.5)
        for _ in range(2):
            circuit_breaker.record_trade(success=True, latency=0.5)

        # Error rate is 10/12 = 83% > 10% threshold
        state = circuit_breaker.check()
        assert state == CircuitBreakerState.OPEN
        assert "error" in circuit_breaker.current_trip.reason.lower()

    def test_trip_on_low_fill_rate(self, circuit_breaker):
        """Test tripping on low fill rate."""
        # Record mostly unfilled orders
        for _ in range(10):
            circuit_breaker.record_order(filled=False)
        for _ in range(2):
            circuit_breaker.record_order(filled=True)

        # Fill rate is 2/12 = 17% < 70% threshold
        state = circuit_breaker.check()
        assert state == CircuitBreakerState.OPEN
        assert "fill" in circuit_breaker.current_trip.reason.lower()

    def test_trip_on_high_latency(self, circuit_breaker):
        """Test tripping on high latency."""
        # Record high latency operations
        for _ in range(15):
            circuit_breaker.record_latency(3.0)  # 3 seconds > 2 second threshold

        state = circuit_breaker.check()
        assert state == CircuitBreakerState.OPEN
        assert "latency" in circuit_breaker.current_trip.reason.lower()

    def test_manual_trip(self, circuit_breaker):
        """Test manual trip."""
        circuit_breaker.trip("Manual test trip")
        assert circuit_breaker.state == CircuitBreakerState.OPEN
        assert circuit_breaker.current_trip is not None
        assert "manual" in circuit_breaker.current_trip.reason.lower()

    def test_reset_requires_confirmation(self, circuit_breaker):
        """Test that reset requires confirmation."""
        circuit_breaker.trip("Test trip")

        # Without confirmation
        result = circuit_breaker.reset(operator_id="test@example.com", confirm=False)
        assert not result
        assert circuit_breaker.state == CircuitBreakerState.OPEN

    def test_reset_requires_operator_id(self, circuit_breaker):
        """Test that reset requires operator ID."""
        circuit_breaker.trip("Test trip")

        result = circuit_breaker.reset(operator_id="", confirm=True)
        assert not result
        assert circuit_breaker.state == CircuitBreakerState.OPEN

    def test_reset_success(self, circuit_breaker):
        """Test successful reset."""
        circuit_breaker.trip("Test trip")

        result = circuit_breaker.reset(operator_id="admin@example.com", confirm=True)
        assert result
        assert circuit_breaker.state == CircuitBreakerState.CLOSED
        assert circuit_breaker.current_trip is None

    def test_reset_records_audit_info(self, circuit_breaker):
        """Test that reset records audit information."""
        circuit_breaker.trip("Test trip")
        trip_before = circuit_breaker.trip_history[-1]

        circuit_breaker.reset(operator_id="admin@example.com", confirm=True)

        # Trip event should have reset info
        assert trip_before.operator_id == "admin@example.com"
        assert trip_before.reset_at is not None

    def test_reset_when_not_tripped(self, circuit_breaker):
        """Test reset when not tripped returns False."""
        result = circuit_breaker.reset(operator_id="admin@example.com", confirm=True)
        assert not result

    def test_reset_daily_metrics(self, circuit_breaker):
        """Test daily metrics reset."""
        # Record some activity
        circuit_breaker.record_trade(success=True, latency=0.5, pnl=100.0)
        circuit_breaker.record_order(filled=True)

        # Reset daily
        circuit_breaker.reset_daily()

        # Metrics should be zeroed
        assert circuit_breaker.metrics.total_trades == 0
        assert circuit_breaker.metrics.daily_pnl == 0.0

    def test_update_daily_pnl(self, circuit_breaker):
        """Test updating daily P&L from external source."""
        circuit_breaker.update_daily_pnl(-100.0)
        assert circuit_breaker.metrics.daily_pnl == -100.0
        assert circuit_breaker.metrics.daily_loss == 100.0

    def test_get_status(self, circuit_breaker):
        """Test get_status returns complete information."""
        circuit_breaker.record_trade(success=True, latency=0.5, pnl=10.0)

        status = circuit_breaker.get_status()

        assert "state" in status
        assert "is_closed" in status
        assert "metrics" in status
        assert "thresholds" in status
        assert "current_trip" in status
        assert "trip_count" in status

    def test_alert_callback_called_on_trip(self, config):
        """Test that alert callback is called when tripping."""
        callback = MagicMock()
        cb = CircuitBreaker(config, alert_callback=callback)

        cb.trip("Test reason")

        callback.assert_called_once()
        args = callback.call_args[0]
        assert "Test reason" in args[0] or "Test reason" in args[1]

    def test_latency_samples_bounded(self, circuit_breaker):
        """Test that latency samples don't grow unbounded."""
        # Record many samples
        for _ in range(200):
            circuit_breaker.record_latency(0.5)

        assert len(circuit_breaker.metrics.recent_latencies) <= CircuitBreaker.MAX_LATENCY_SAMPLES


class TestCircuitBreakerMetrics:
    """Test the CircuitBreakerMetrics dataclass."""

    def test_error_rate_no_trades(self):
        """Test error rate with no trades."""
        metrics = CircuitBreakerMetrics()
        assert metrics.error_rate == 0.0

    def test_error_rate_with_trades(self):
        """Test error rate calculation."""
        metrics = CircuitBreakerMetrics(
            total_trades=10,
            successful_trades=8,
            failed_trades=2,
        )
        assert metrics.error_rate == 0.2

    def test_fill_rate_no_orders(self):
        """Test fill rate with no orders."""
        metrics = CircuitBreakerMetrics()
        assert metrics.fill_rate == 1.0  # No failures

    def test_fill_rate_with_orders(self):
        """Test fill rate calculation."""
        metrics = CircuitBreakerMetrics(
            total_orders=10,
            filled_orders=7,
        )
        assert metrics.fill_rate == 0.7

    def test_avg_latency_no_samples(self):
        """Test average latency with no samples."""
        metrics = CircuitBreakerMetrics()
        assert metrics.avg_latency == 0.0

    def test_avg_latency_with_samples(self):
        """Test average latency calculation."""
        metrics = CircuitBreakerMetrics(
            recent_latencies=[1.0, 2.0, 3.0],
        )
        assert metrics.avg_latency == 2.0

    def test_p95_latency(self):
        """Test p95 latency calculation."""
        metrics = CircuitBreakerMetrics(
            recent_latencies=[float(i) for i in range(1, 101)],
        )
        # 95th percentile of 1-100 should be around 95
        assert 94 <= metrics.p95_latency <= 96


class TestTripEvent:
    """Test the TripEvent dataclass."""

    def test_is_reset_false(self):
        """Test is_reset when not reset."""
        event = TripEvent(
            timestamp=datetime.now(),
            reason="Test",
            metric_name="test",
            metric_value=0.5,
            threshold=0.1,
        )
        assert not event.is_reset

    def test_is_reset_true(self):
        """Test is_reset when reset."""
        event = TripEvent(
            timestamp=datetime.now(),
            reason="Test",
            metric_name="test",
            metric_value=0.5,
            threshold=0.1,
            operator_id="admin",
            reset_at=datetime.now(),
        )
        assert event.is_reset
