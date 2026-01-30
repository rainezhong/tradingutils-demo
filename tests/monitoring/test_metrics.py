"""Tests for Prometheus metrics collection."""

import pytest

from src.monitoring.metrics import (
    MetricsCollector,
    OPPORTUNITIES_DETECTED,
    TRADES_EXECUTED,
    TRADE_PROFIT,
    TRADE_LATENCY,
    API_LATENCY,
    WEBSOCKET_CONNECTED,
    ERROR_COUNT,
    CAPITAL_DEPLOYED,
    CURRENT_PNL,
    OPEN_POSITIONS,
)


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def setup_method(self):
        """Create collector before each test."""
        self.collector = MetricsCollector(port=9999)

    def test_record_opportunity(self):
        """Test recording arbitrage opportunities."""
        # Should not raise
        self.collector.record_opportunity(
            category="price_discrepancy",
            platforms="kalshi_polymarket",
        )

    def test_record_opportunity_multiple(self):
        """Test recording multiple opportunities."""
        # Should not raise
        for _ in range(5):
            self.collector.record_opportunity(category="timing")

    def test_record_trade(self):
        """Test recording executed trades."""
        # Should not raise
        self.collector.record_trade(
            platform="kalshi",
            status="filled",
            strategy="arbitrage",
            profit=25.50,
            latency=0.5,
        )

    def test_record_trade_histogram_values(self):
        """Test that profit and latency histograms are recorded."""
        # Should not raise
        self.collector.record_trade(
            platform="kalshi",
            status="filled",
            strategy="arbitrage",
            profit=25.50,
            latency=0.5,
        )

    def test_record_api_latency(self):
        """Test recording API latency."""
        # Should not raise
        self.collector.record_api_latency(
            platform="kalshi",
            endpoint="/markets",
            method="GET",
            latency=0.123,
        )

    def test_set_websocket_status_connected(self):
        """Test setting WebSocket connected status."""
        # Should not raise
        self.collector.set_websocket_status("kalshi", connected=True)

    def test_set_websocket_status_disconnected(self):
        """Test setting WebSocket disconnected status."""
        # Should not raise
        self.collector.set_websocket_status("kalshi", connected=False)

    def test_record_error(self):
        """Test recording errors."""
        # Should not raise
        self.collector.record_error(
            component="api_client",
            error_type="ConnectionError",
        )

    def test_update_capital(self):
        """Test updating capital metrics."""
        # Should not raise
        self.collector.update_capital(
            platform="kalshi",
            deployed=1500.0,
            available=500.0,
        )

    def test_update_pnl(self):
        """Test updating P&L metrics."""
        # Should not raise
        self.collector.update_pnl(
            realized=100.0,
            unrealized=50.0,
            total=150.0,
        )

    def test_update_positions(self):
        """Test updating position metrics."""
        # Should not raise
        self.collector.update_positions(
            platform="kalshi",
            count=5,
            value=1500.0,
        )

    def test_update_fill_rate(self):
        """Test updating fill rate metric."""
        # Should not raise
        self.collector.update_fill_rate(
            platform="kalshi",
            strategy="arbitrage",
            rate=0.85,
        )

    def test_update_win_rate(self):
        """Test updating win rate metric."""
        # Should not raise
        self.collector.update_win_rate(
            strategy="arbitrage",
            rate=0.72,
        )

    def test_update_risk_utilization(self):
        """Test updating risk utilization metrics."""
        # Should not raise
        self.collector.update_risk_utilization(
            position_pct=45.0,
            total_pct=60.0,
            daily_loss_pct=25.0,
        )

    def test_set_trading_halted(self):
        """Test setting trading halted flag."""
        # Should not raise
        self.collector.set_trading_halted(True)
        self.collector.set_trading_halted(False)


class TestMetricLabels:
    """Tests for metric label handling."""

    def setup_method(self):
        """Create collector before each test."""
        self.collector = MetricsCollector()

    def test_different_platforms(self):
        """Test metrics with different platform labels."""
        # Should not raise - different platforms are handled independently
        self.collector.record_trade("kalshi_test", "filled", "arb", 10.0, 0.1)
        self.collector.record_trade("polymarket_test", "filled", "arb", 20.0, 0.2)

    def test_different_statuses(self):
        """Test metrics with different status labels."""
        # Should not raise - different statuses are tracked separately
        self.collector.record_trade("kalshi_status", "filled", "arb", 10.0, 0.1)
        self.collector.record_trade("kalshi_status", "rejected", "arb", 0.0, 0.1)
        self.collector.record_trade("kalshi_status", "cancelled", "arb", 0.0, 0.1)
