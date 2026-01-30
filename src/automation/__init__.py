"""Automation module for scheduled data collection and monitoring.

This module provides:
- MarketMakerScheduler: Scheduled job execution for data collection
- SystemMonitor: Real-time system status display
- HealthCheck: System health verification
- Alerter: Condition-based alerting
- NBAGameScheduler: Auto-detect and record NBA games
"""

from .scheduler import MarketMakerScheduler
from .monitor import SystemMonitor
from .healthcheck import HealthCheck, HealthStatus
from .alerter import Alerter, Alert
from .nba_scheduler import NBAGameScheduler

__all__ = [
    "MarketMakerScheduler",
    "SystemMonitor",
    "HealthCheck",
    "HealthStatus",
    "Alerter",
    "Alert",
    "NBAGameScheduler",
]
