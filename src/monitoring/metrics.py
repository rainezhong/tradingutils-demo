"""Prometheus Metrics Collection.

Provides comprehensive metrics for monitoring trading system performance:
- Trading metrics: opportunities/hour, trades/hour, win rate, fill rate
- System metrics: API latency (p50/p95/p99), WebSocket uptime, error rates
- Business metrics: capital deployed, P&L, positions

Metrics are exposed via HTTP endpoint for Prometheus scraping.

Example:
    from src.monitoring.metrics import MetricsCollector

    # Initialize and start metrics server
    metrics = MetricsCollector(port=9090)
    metrics.start()

    # Record trading activity
    metrics.record_opportunity(category="price_discrepancy", platforms="kalshi_polymarket")
    metrics.record_trade(
        platform="kalshi",
        status="filled",
        strategy="arbitrage",
        profit=25.50,
        latency=0.5
    )

    # Record API performance
    metrics.record_api_latency(
        platform="kalshi",
        endpoint="/markets",
        method="GET",
        latency=0.123
    )
"""

from typing import Optional

from prometheus_client import Counter, Gauge, Histogram, start_http_server, REGISTRY
from prometheus_client.metrics import MetricWrapperBase


# =============================================================================
# Trading Metrics
# =============================================================================

OPPORTUNITIES_DETECTED = Counter(
    "arbitrage_opportunities_detected_total",
    "Total arbitrage opportunities detected",
    ["category", "platform_pair"],
)

TRADES_EXECUTED = Counter(
    "arbitrage_trades_executed_total",
    "Total trades executed",
    ["platform", "status", "strategy"],
)

TRADE_PROFIT = Histogram(
    "arbitrage_trade_profit_dollars",
    "Profit per trade in dollars",
    buckets=[0, 5, 10, 25, 50, 100, 250, 500, 1000],
)

TRADE_LATENCY = Histogram(
    "arbitrage_trade_latency_seconds",
    "End-to-end trade execution latency",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

FILL_RATE = Gauge(
    "arbitrage_fill_rate",
    "Order fill rate (filled / total orders)",
    ["platform", "strategy"],
)

WIN_RATE = Gauge(
    "arbitrage_win_rate",
    "Trade win rate (profitable / total trades)",
    ["strategy"],
)

OPPORTUNITIES_PER_HOUR = Gauge(
    "arbitrage_opportunities_per_hour",
    "Rolling opportunities detected per hour",
    ["category"],
)

TRADES_PER_HOUR = Gauge(
    "arbitrage_trades_per_hour",
    "Rolling trades executed per hour",
    ["platform"],
)


# =============================================================================
# System Metrics
# =============================================================================

API_LATENCY = Histogram(
    "api_request_latency_seconds",
    "API request latency by platform and endpoint",
    ["platform", "endpoint", "method"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)

API_REQUESTS = Counter(
    "api_requests_total",
    "Total API requests",
    ["platform", "endpoint", "method", "status_code"],
)

WEBSOCKET_CONNECTED = Gauge(
    "websocket_connected",
    "WebSocket connection status (1=connected, 0=disconnected)",
    ["platform"],
)

WEBSOCKET_MESSAGES = Counter(
    "websocket_messages_total",
    "Total WebSocket messages received",
    ["platform", "message_type"],
)

WEBSOCKET_RECONNECTS = Counter(
    "websocket_reconnects_total",
    "Total WebSocket reconnection attempts",
    ["platform"],
)

WEBSOCKET_UPTIME = Gauge(
    "websocket_uptime_seconds",
    "WebSocket connection uptime in seconds",
    ["platform"],
)

ERROR_COUNT = Counter(
    "errors_total",
    "Total errors by component and type",
    ["component", "error_type"],
)

RATE_LIMIT_HITS = Counter(
    "rate_limit_hits_total",
    "Total rate limit hits",
    ["platform", "endpoint"],
)


# =============================================================================
# Business Metrics
# =============================================================================

CAPITAL_DEPLOYED = Gauge(
    "capital_deployed_dollars",
    "Capital currently deployed",
    ["platform"],
)

CAPITAL_AVAILABLE = Gauge(
    "capital_available_dollars",
    "Capital available for trading",
    ["platform"],
)

CURRENT_PNL = Gauge(
    "current_pnl_dollars",
    "Current profit/loss in dollars",
    ["type"],  # 'realized', 'unrealized', 'total'
)

DAILY_PNL = Gauge(
    "daily_pnl_dollars",
    "Daily profit/loss in dollars",
)

OPEN_POSITIONS = Gauge(
    "open_positions_count",
    "Number of open positions",
    ["platform"],
)

POSITION_VALUE = Gauge(
    "position_value_dollars",
    "Total value of open positions",
    ["platform"],
)

POSITION_EXPOSURE = Gauge(
    "position_exposure_dollars",
    "Total position exposure",
    ["platform", "ticker"],
)


# =============================================================================
# Risk Metrics
# =============================================================================

RISK_UTILIZATION = Gauge(
    "risk_utilization_pct",
    "Risk limit utilization percentage",
    ["limit_type"],  # 'position', 'total', 'daily_loss'
)

TRADING_HALTED = Gauge(
    "trading_halted",
    "Trading halted flag (1=halted, 0=active)",
)


class MetricsCollector:
    """Centralized metrics collection and management.

    Provides a convenient interface for recording various metrics
    and manages the Prometheus HTTP server lifecycle.

    Example:
        metrics = MetricsCollector(port=9090)
        metrics.start()

        # Record metrics throughout your application
        metrics.record_opportunity("price_discrepancy")
        metrics.record_trade("kalshi", "filled", "arbitrage", 25.50, 0.5)
        metrics.record_api_latency("kalshi", "/orders", "POST", 0.123)

        # Update business metrics
        metrics.update_pnl(realized=100.0, unrealized=50.0)
        metrics.update_positions("kalshi", count=5, value=1500.0)
    """

    def __init__(self, port: int = 9090):
        """Initialize metrics collector.

        Args:
            port: Port number for Prometheus HTTP server
        """
        self.port = port
        self._started = False

    def start(self) -> None:
        """Start Prometheus metrics HTTP server.

        Metrics will be available at http://localhost:{port}/metrics
        """
        if not self._started:
            start_http_server(self.port)
            self._started = True

    # =========================================================================
    # Trading Metrics
    # =========================================================================

    def record_opportunity(
        self,
        category: str,
        platforms: str = "kalshi_polymarket",
    ) -> None:
        """Record a detected arbitrage opportunity.

        Args:
            category: Opportunity category (e.g., 'price_discrepancy', 'timing')
            platforms: Platform pair identifier
        """
        OPPORTUNITIES_DETECTED.labels(
            category=category,
            platform_pair=platforms,
        ).inc()

    def record_trade(
        self,
        platform: str,
        status: str,
        strategy: str,
        profit: float,
        latency: float,
    ) -> None:
        """Record an executed trade.

        Args:
            platform: Trading platform (e.g., 'kalshi', 'polymarket')
            status: Trade status ('filled', 'partial', 'rejected', 'cancelled')
            strategy: Strategy name that generated the trade
            profit: Profit/loss in dollars
            latency: End-to-end execution latency in seconds
        """
        TRADES_EXECUTED.labels(
            platform=platform,
            status=status,
            strategy=strategy,
        ).inc()
        TRADE_PROFIT.observe(profit)
        TRADE_LATENCY.observe(latency)

    def update_fill_rate(
        self,
        platform: str,
        strategy: str,
        rate: float,
    ) -> None:
        """Update fill rate metric.

        Args:
            platform: Trading platform
            strategy: Strategy name
            rate: Fill rate (0.0 to 1.0)
        """
        FILL_RATE.labels(platform=platform, strategy=strategy).set(rate)

    def update_win_rate(self, strategy: str, rate: float) -> None:
        """Update win rate metric.

        Args:
            strategy: Strategy name
            rate: Win rate (0.0 to 1.0)
        """
        WIN_RATE.labels(strategy=strategy).set(rate)

    def update_opportunities_per_hour(self, category: str, count: float) -> None:
        """Update rolling opportunities per hour.

        Args:
            category: Opportunity category
            count: Opportunities per hour
        """
        OPPORTUNITIES_PER_HOUR.labels(category=category).set(count)

    def update_trades_per_hour(self, platform: str, count: float) -> None:
        """Update rolling trades per hour.

        Args:
            platform: Trading platform
            count: Trades per hour
        """
        TRADES_PER_HOUR.labels(platform=platform).set(count)

    # =========================================================================
    # System Metrics
    # =========================================================================

    def record_api_latency(
        self,
        platform: str,
        endpoint: str,
        method: str,
        latency: float,
    ) -> None:
        """Record API request latency.

        Args:
            platform: API platform (e.g., 'kalshi', 'polymarket')
            endpoint: API endpoint path
            method: HTTP method (GET, POST, etc.)
            latency: Request latency in seconds
        """
        API_LATENCY.labels(
            platform=platform,
            endpoint=endpoint,
            method=method,
        ).observe(latency)

    def record_api_request(
        self,
        platform: str,
        endpoint: str,
        method: str,
        status_code: int,
    ) -> None:
        """Record an API request.

        Args:
            platform: API platform
            endpoint: API endpoint path
            method: HTTP method
            status_code: HTTP response status code
        """
        API_REQUESTS.labels(
            platform=platform,
            endpoint=endpoint,
            method=method,
            status_code=str(status_code),
        ).inc()

    def set_websocket_status(self, platform: str, connected: bool) -> None:
        """Set WebSocket connection status.

        Args:
            platform: Platform identifier
            connected: Whether WebSocket is connected
        """
        WEBSOCKET_CONNECTED.labels(platform=platform).set(1 if connected else 0)

    def record_websocket_message(self, platform: str, message_type: str) -> None:
        """Record a WebSocket message received.

        Args:
            platform: Platform identifier
            message_type: Type of message received
        """
        WEBSOCKET_MESSAGES.labels(
            platform=platform,
            message_type=message_type,
        ).inc()

    def record_websocket_reconnect(self, platform: str) -> None:
        """Record a WebSocket reconnection attempt.

        Args:
            platform: Platform identifier
        """
        WEBSOCKET_RECONNECTS.labels(platform=platform).inc()

    def set_websocket_uptime(self, platform: str, uptime_seconds: float) -> None:
        """Set WebSocket uptime.

        Args:
            platform: Platform identifier
            uptime_seconds: Connection uptime in seconds
        """
        WEBSOCKET_UPTIME.labels(platform=platform).set(uptime_seconds)

    def record_error(self, component: str, error_type: str) -> None:
        """Record an error occurrence.

        Args:
            component: Component where error occurred
            error_type: Type/category of error
        """
        ERROR_COUNT.labels(component=component, error_type=error_type).inc()

    def record_rate_limit(self, platform: str, endpoint: str) -> None:
        """Record a rate limit hit.

        Args:
            platform: API platform
            endpoint: API endpoint
        """
        RATE_LIMIT_HITS.labels(platform=platform, endpoint=endpoint).inc()

    # =========================================================================
    # Business Metrics
    # =========================================================================

    def update_capital(
        self,
        platform: str,
        deployed: float,
        available: Optional[float] = None,
    ) -> None:
        """Update capital metrics.

        Args:
            platform: Trading platform
            deployed: Capital currently deployed
            available: Capital available for trading
        """
        CAPITAL_DEPLOYED.labels(platform=platform).set(deployed)
        if available is not None:
            CAPITAL_AVAILABLE.labels(platform=platform).set(available)

    def update_pnl(
        self,
        realized: Optional[float] = None,
        unrealized: Optional[float] = None,
        total: Optional[float] = None,
    ) -> None:
        """Update P&L metrics.

        Args:
            realized: Realized P&L in dollars
            unrealized: Unrealized P&L in dollars
            total: Total P&L in dollars
        """
        if realized is not None:
            CURRENT_PNL.labels(type="realized").set(realized)
        if unrealized is not None:
            CURRENT_PNL.labels(type="unrealized").set(unrealized)
        if total is not None:
            CURRENT_PNL.labels(type="total").set(total)

    def update_daily_pnl(self, pnl: float) -> None:
        """Update daily P&L metric.

        Args:
            pnl: Daily P&L in dollars
        """
        DAILY_PNL.set(pnl)

    def update_positions(
        self,
        platform: str,
        count: int,
        value: Optional[float] = None,
    ) -> None:
        """Update position metrics.

        Args:
            platform: Trading platform
            count: Number of open positions
            value: Total value of positions
        """
        OPEN_POSITIONS.labels(platform=platform).set(count)
        if value is not None:
            POSITION_VALUE.labels(platform=platform).set(value)

    def update_position_exposure(
        self,
        platform: str,
        ticker: str,
        exposure: float,
    ) -> None:
        """Update position exposure for a specific ticker.

        Args:
            platform: Trading platform
            ticker: Ticker symbol
            exposure: Position exposure in dollars
        """
        POSITION_EXPOSURE.labels(platform=platform, ticker=ticker).set(exposure)

    # =========================================================================
    # Risk Metrics
    # =========================================================================

    def update_risk_utilization(
        self,
        position_pct: Optional[float] = None,
        total_pct: Optional[float] = None,
        daily_loss_pct: Optional[float] = None,
    ) -> None:
        """Update risk utilization metrics.

        Args:
            position_pct: Position limit utilization (0-100)
            total_pct: Total position limit utilization (0-100)
            daily_loss_pct: Daily loss limit utilization (0-100)
        """
        if position_pct is not None:
            RISK_UTILIZATION.labels(limit_type="position").set(position_pct)
        if total_pct is not None:
            RISK_UTILIZATION.labels(limit_type="total").set(total_pct)
        if daily_loss_pct is not None:
            RISK_UTILIZATION.labels(limit_type="daily_loss").set(daily_loss_pct)

    def set_trading_halted(self, halted: bool) -> None:
        """Set trading halted flag.

        Args:
            halted: Whether trading is halted
        """
        TRADING_HALTED.set(1 if halted else 0)


def reset_metrics() -> None:
    """Reset all metrics to initial state.

    Useful for testing. Clears all counter values and gauge states.
    Note: This is a best-effort reset for testing purposes.
    """
    # For testing, we clear the child metrics from each collector
    for collector in list(REGISTRY._names_to_collectors.values()):
        if hasattr(collector, "_metrics"):
            try:
                collector._metrics.clear()
            except Exception:
                pass
        # Handle labeled metrics
        if hasattr(collector, "_lock"):
            try:
                with collector._lock:
                    if hasattr(collector, "_metrics"):
                        collector._metrics.clear()
            except Exception:
                pass
