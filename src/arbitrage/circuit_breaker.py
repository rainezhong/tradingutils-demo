"""Circuit Breaker - System-level safety controls.

Monitors trading system health and halts operations when thresholds are breached:
- Daily loss limits
- Error rates
- API latency
- Fill rates

Requires manual reset with audit logging after trip.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Dict, List, Optional

from .config import ArbitrageConfig


logger = logging.getLogger(__name__)


class CircuitBreakerState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Tripped, blocking all operations
    HALF_OPEN = "half_open"  # Testing recovery (not used in this impl)


@dataclass
class TripEvent:
    """Record of a circuit breaker trip.

    Attributes:
        timestamp: When the trip occurred
        reason: Why the breaker tripped
        metric_name: Which metric triggered it
        metric_value: The value that triggered the trip
        threshold: The threshold that was exceeded
        operator_id: Who reset it (None if not yet reset)
        reset_at: When it was reset (None if not yet reset)
    """

    timestamp: datetime
    reason: str
    metric_name: str
    metric_value: float
    threshold: float
    operator_id: Optional[str] = None
    reset_at: Optional[datetime] = None

    @property
    def is_reset(self) -> bool:
        """Whether this trip has been reset."""
        return self.reset_at is not None


@dataclass
class CircuitBreakerMetrics:
    """Metrics tracked by the circuit breaker.

    Updated by the orchestrator during operation.
    """

    # Counters (reset daily)
    total_trades: int = 0
    successful_trades: int = 0
    failed_trades: int = 0
    total_orders: int = 0
    filled_orders: int = 0

    # Financial
    daily_pnl: float = 0.0
    daily_loss: float = 0.0

    # Latency (rolling window)
    recent_latencies: List[float] = field(default_factory=list)

    # Timestamps
    last_updated: datetime = field(default_factory=datetime.now)
    day_start: datetime = field(default_factory=datetime.now)

    @property
    def error_rate(self) -> float:
        """Current error rate (failed / total trades)."""
        if self.total_trades == 0:
            return 0.0
        return self.failed_trades / self.total_trades

    @property
    def fill_rate(self) -> float:
        """Current fill rate (filled / total orders)."""
        if self.total_orders == 0:
            return 1.0  # No orders = 100% fill rate (no failures)
        return self.filled_orders / self.total_orders

    @property
    def avg_latency(self) -> float:
        """Average latency of recent operations."""
        if not self.recent_latencies:
            return 0.0
        return sum(self.recent_latencies) / len(self.recent_latencies)

    @property
    def p95_latency(self) -> float:
        """95th percentile latency."""
        if not self.recent_latencies:
            return 0.0
        sorted_latencies = sorted(self.recent_latencies)
        idx = int(len(sorted_latencies) * 0.95)
        return sorted_latencies[min(idx, len(sorted_latencies) - 1)]


class CircuitBreaker:
    """System-level circuit breaker for trading operations.

    Monitors key health metrics and automatically trips when thresholds
    are exceeded. Requires manual reset with operator identification
    for audit purposes.

    Example:
        breaker = CircuitBreaker(config)

        # Check before each operation
        if not breaker.is_closed():
            logger.warning("Circuit breaker is open, skipping operation")
            return

        # Record trade outcomes
        breaker.record_trade(success=True, latency=0.5)

        # Check after recording
        state = breaker.check()
        if state == CircuitBreakerState.OPEN:
            logger.critical("Circuit breaker tripped!")

        # Manual reset (requires operator ID)
        breaker.reset(operator_id="admin@example.com")
    """

    # Maximum latency samples to keep
    MAX_LATENCY_SAMPLES = 100

    def __init__(
        self,
        config: Optional[ArbitrageConfig] = None,
        alert_callback: Optional[Callable[[str, str], None]] = None,
    ):
        """Initialize circuit breaker.

        Args:
            config: Optional configuration (uses defaults if not provided)
            alert_callback: Optional callback for alerts (reason, details)
        """
        self._config = config or ArbitrageConfig()
        self._alert_callback = alert_callback

        self._state = CircuitBreakerState.CLOSED
        self._metrics = CircuitBreakerMetrics()
        self._trip_history: List[TripEvent] = []
        self._current_trip: Optional[TripEvent] = None

    @property
    def state(self) -> CircuitBreakerState:
        """Current circuit breaker state."""
        return self._state

    @property
    def metrics(self) -> CircuitBreakerMetrics:
        """Current metrics snapshot."""
        return self._metrics

    @property
    def current_trip(self) -> Optional[TripEvent]:
        """Current trip event if breaker is open."""
        return self._current_trip

    @property
    def trip_history(self) -> List[TripEvent]:
        """History of all trip events."""
        return self._trip_history.copy()

    def is_closed(self) -> bool:
        """Check if the circuit breaker allows operations.

        Returns:
            True if operations are allowed (closed state)
        """
        return self._state == CircuitBreakerState.CLOSED

    def check(self) -> CircuitBreakerState:
        """Check all metrics and trip if thresholds exceeded.

        This should be called periodically by the orchestrator.

        Returns:
            Current circuit breaker state
        """
        if self._state == CircuitBreakerState.OPEN:
            # Already tripped, nothing to check
            return self._state

        # Check daily loss
        if self._metrics.daily_loss >= self._config.max_daily_loss:
            self._trip(
                reason="Daily loss limit exceeded",
                metric_name="daily_loss",
                metric_value=self._metrics.daily_loss,
                threshold=self._config.max_daily_loss,
            )
            return self._state

        # Check error rate (only if we have enough trades)
        if self._metrics.total_trades >= 10:
            if self._metrics.error_rate >= self._config.max_error_rate:
                self._trip(
                    reason="Error rate threshold exceeded",
                    metric_name="error_rate",
                    metric_value=self._metrics.error_rate,
                    threshold=self._config.max_error_rate,
                )
                return self._state

        # Check fill rate (only if we have enough orders)
        if self._metrics.total_orders >= 10:
            if self._metrics.fill_rate < self._config.min_fill_rate:
                self._trip(
                    reason="Fill rate below threshold",
                    metric_name="fill_rate",
                    metric_value=self._metrics.fill_rate,
                    threshold=self._config.min_fill_rate,
                )
                return self._state

        # Check latency (only if we have enough samples)
        if len(self._metrics.recent_latencies) >= 10:
            if self._metrics.p95_latency >= self._config.max_api_latency_seconds:
                self._trip(
                    reason="API latency threshold exceeded",
                    metric_name="p95_latency",
                    metric_value=self._metrics.p95_latency,
                    threshold=self._config.max_api_latency_seconds,
                )
                return self._state

        return self._state

    def record_trade(
        self,
        success: bool,
        latency: float,
        pnl: float = 0.0,
    ) -> None:
        """Record a trade outcome.

        Args:
            success: Whether the trade succeeded
            latency: End-to-end latency in seconds
            pnl: Profit/loss from the trade
        """
        self._metrics.total_trades += 1
        if success:
            self._metrics.successful_trades += 1
        else:
            self._metrics.failed_trades += 1

        self._metrics.daily_pnl += pnl
        if pnl < 0:
            self._metrics.daily_loss += abs(pnl)

        self._record_latency(latency)
        self._metrics.last_updated = datetime.now()

    def record_order(self, filled: bool, latency: float = 0.0) -> None:
        """Record an order outcome.

        Args:
            filled: Whether the order was filled
            latency: Order execution latency
        """
        self._metrics.total_orders += 1
        if filled:
            self._metrics.filled_orders += 1

        if latency > 0:
            self._record_latency(latency)

        self._metrics.last_updated = datetime.now()

    def record_latency(self, latency: float) -> None:
        """Record an operation latency.

        Args:
            latency: Latency in seconds
        """
        self._record_latency(latency)

    def update_daily_pnl(self, total_pnl: float) -> None:
        """Update the daily P&L from an external source.

        Args:
            total_pnl: Current daily P&L
        """
        self._metrics.daily_pnl = total_pnl
        if total_pnl < 0:
            self._metrics.daily_loss = abs(total_pnl)
        else:
            self._metrics.daily_loss = 0.0

        self._metrics.last_updated = datetime.now()

    def trip(self, reason: str) -> None:
        """Manually trip the circuit breaker.

        Args:
            reason: Reason for manual trip
        """
        self._trip(
            reason=f"Manual trip: {reason}",
            metric_name="manual",
            metric_value=0.0,
            threshold=0.0,
        )

    def reset(self, operator_id: str, confirm: bool = True) -> bool:
        """Reset the circuit breaker after a trip.

        Requires operator identification for audit purposes.

        Args:
            operator_id: ID of the operator performing the reset
            confirm: Must be True to confirm the reset

        Returns:
            True if reset was successful, False otherwise
        """
        if not confirm:
            logger.warning("Circuit breaker reset attempted without confirmation")
            return False

        if self._state != CircuitBreakerState.OPEN:
            logger.info("Circuit breaker reset called but not in OPEN state")
            return False

        if not operator_id:
            logger.error("Circuit breaker reset requires operator_id")
            return False

        # Update trip record
        if self._current_trip:
            self._current_trip.operator_id = operator_id
            self._current_trip.reset_at = datetime.now()

        # Reset state
        self._state = CircuitBreakerState.CLOSED
        self._current_trip = None

        logger.info(
            "Circuit breaker reset by operator: %s",
            operator_id,
        )

        return True

    def reset_daily(self) -> None:
        """Reset daily metrics for a new trading day.

        Does NOT reset the circuit breaker state - use reset() for that.
        """
        self._metrics = CircuitBreakerMetrics()
        logger.info("Circuit breaker daily metrics reset")

    def get_status(self) -> Dict:
        """Get current status summary.

        Returns:
            Dictionary with state, metrics, and trip info
        """
        return {
            "state": self._state.value,
            "is_closed": self.is_closed(),
            "metrics": {
                "total_trades": self._metrics.total_trades,
                "successful_trades": self._metrics.successful_trades,
                "failed_trades": self._metrics.failed_trades,
                "error_rate": self._metrics.error_rate,
                "total_orders": self._metrics.total_orders,
                "filled_orders": self._metrics.filled_orders,
                "fill_rate": self._metrics.fill_rate,
                "daily_pnl": self._metrics.daily_pnl,
                "daily_loss": self._metrics.daily_loss,
                "avg_latency": self._metrics.avg_latency,
                "p95_latency": self._metrics.p95_latency,
                "last_updated": self._metrics.last_updated.isoformat(),
            },
            "thresholds": {
                "max_daily_loss": self._config.max_daily_loss,
                "max_error_rate": self._config.max_error_rate,
                "min_fill_rate": self._config.min_fill_rate,
                "max_api_latency": self._config.max_api_latency_seconds,
            },
            "current_trip": (
                {
                    "reason": self._current_trip.reason,
                    "metric": self._current_trip.metric_name,
                    "value": self._current_trip.metric_value,
                    "threshold": self._current_trip.threshold,
                    "timestamp": self._current_trip.timestamp.isoformat(),
                }
                if self._current_trip
                else None
            ),
            "trip_count": len(self._trip_history),
        }

    def _trip(
        self,
        reason: str,
        metric_name: str,
        metric_value: float,
        threshold: float,
    ) -> None:
        """Internal method to trip the circuit breaker.

        Args:
            reason: Human-readable reason for the trip
            metric_name: Name of the metric that caused the trip
            metric_value: Value of the metric
            threshold: Threshold that was exceeded
        """
        self._state = CircuitBreakerState.OPEN

        trip_event = TripEvent(
            timestamp=datetime.now(),
            reason=reason,
            metric_name=metric_name,
            metric_value=metric_value,
            threshold=threshold,
        )

        self._current_trip = trip_event
        self._trip_history.append(trip_event)

        logger.critical(
            "CIRCUIT BREAKER TRIPPED: %s (metric=%s, value=%.4f, threshold=%.4f)",
            reason,
            metric_name,
            metric_value,
            threshold,
        )

        # Alert callback
        if self._alert_callback:
            try:
                details = f"Metric: {metric_name}, Value: {metric_value:.4f}, Threshold: {threshold:.4f}"
                self._alert_callback(reason, details)
            except Exception as e:
                logger.error("Alert callback failed: %s", e)

    def _record_latency(self, latency: float) -> None:
        """Record a latency sample with bounded list size."""
        self._metrics.recent_latencies.append(latency)

        # Trim to max samples
        if len(self._metrics.recent_latencies) > self.MAX_LATENCY_SAMPLES:
            self._metrics.recent_latencies = self._metrics.recent_latencies[
                -self.MAX_LATENCY_SAMPLES :
            ]
