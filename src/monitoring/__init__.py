"""Monitoring and Observability Module.

This module provides comprehensive monitoring infrastructure including:
- Structured logging with JSON output
- Prometheus metrics collection
- Multi-channel alerting (Slack, email)
- Health check endpoints

Example:
    from src.monitoring import setup_logging, get_logger, MetricsCollector, AlertManager

    # Setup logging
    setup_logging(log_level="INFO", json_output=True)
    logger = get_logger(__name__)
    logger.info("Application started", version="1.0.0")

    # Record metrics
    metrics = MetricsCollector()
    metrics.start()
    metrics.record_trade(platform="kalshi", status="filled", strategy="arbitrage", profit=25.50, latency=0.5)

    # Send alerts
    alert_manager = AlertManager(config)
    await alert_manager.send_alert("risk_breach", "critical", "Daily loss limit exceeded")
"""

from .logger import (
    setup_logging,
    get_logger,
    log_context,
    bind_context,
    clear_context,
)
from .metrics import (
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
from .alerts import AlertManager, AlertSeverity, AlertConfig
from .health import HealthChecker, HealthStatus, create_health_app

__all__ = [
    # Logger
    "setup_logging",
    "get_logger",
    "log_context",
    "bind_context",
    "clear_context",
    # Metrics
    "MetricsCollector",
    "OPPORTUNITIES_DETECTED",
    "TRADES_EXECUTED",
    "TRADE_PROFIT",
    "TRADE_LATENCY",
    "API_LATENCY",
    "WEBSOCKET_CONNECTED",
    "ERROR_COUNT",
    "CAPITAL_DEPLOYED",
    "CURRENT_PNL",
    "OPEN_POSITIONS",
    # Alerts
    "AlertManager",
    "AlertSeverity",
    "AlertConfig",
    # Health
    "HealthChecker",
    "HealthStatus",
    "create_health_app",
]
