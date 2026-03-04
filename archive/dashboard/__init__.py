"""Trading Dashboard - Web UI for algorithm monitoring."""

from .app import create_app
from .state import StateAggregator, state_aggregator

__all__ = ["create_app", "StateAggregator", "state_aggregator"]
