"""Portfolio drawdown tracking for risk management.

This module provides tools for monitoring portfolio equity drawdowns across
multiple time horizons (rolling, weekly, monthly) and signaling when limits
are breached.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Deque, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DrawdownConfig:
    """Configuration for drawdown tracking limits.

    Attributes:
        max_rolling_drawdown_pct: Maximum allowed drawdown from peak (0-1)
        max_weekly_drawdown_pct: Maximum drawdown allowed within a week (0-1)
        max_monthly_drawdown_pct: Maximum drawdown allowed within a month (0-1)
        drawdown_recovery_threshold: Fraction of limit at which trading can resume (0-1)
    """

    max_rolling_drawdown_pct: float = 0.15  # 15% from peak
    max_weekly_drawdown_pct: float = 0.10  # 10% weekly
    max_monthly_drawdown_pct: float = 0.20  # 20% monthly
    drawdown_recovery_threshold: float = 0.50  # Resume at 50% of limit

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate that drawdown limits are sensible."""
        errors = []

        if not 0 < self.max_rolling_drawdown_pct <= 1:
            errors.append(
                f"max_rolling_drawdown_pct must be in (0, 1], got {self.max_rolling_drawdown_pct}"
            )

        if not 0 < self.max_weekly_drawdown_pct <= 1:
            errors.append(
                f"max_weekly_drawdown_pct must be in (0, 1], got {self.max_weekly_drawdown_pct}"
            )

        if not 0 < self.max_monthly_drawdown_pct <= 1:
            errors.append(
                f"max_monthly_drawdown_pct must be in (0, 1], got {self.max_monthly_drawdown_pct}"
            )

        if not 0 < self.drawdown_recovery_threshold < 1:
            errors.append(
                f"drawdown_recovery_threshold must be in (0, 1), got {self.drawdown_recovery_threshold}"
            )

        if errors:
            raise ValueError("Invalid DrawdownConfig: " + "; ".join(errors))


@dataclass
class EquityPoint:
    """A point-in-time equity observation."""

    timestamp: datetime
    equity: float


@dataclass
class DrawdownState:
    """Current state of drawdown metrics.

    Attributes:
        current_equity: Current portfolio equity
        peak_equity: All-time peak equity
        rolling_drawdown_pct: Drawdown from all-time peak (0-1)
        weekly_drawdown_pct: Drawdown from weekly peak (0-1)
        monthly_drawdown_pct: Drawdown from monthly peak (0-1)
        is_breached: Whether any drawdown limit is breached
        breach_reason: Reason for breach, if any
        recovery_pct: How much drawdown has recovered as fraction of limit
    """

    current_equity: float
    peak_equity: float
    rolling_drawdown_pct: float
    weekly_drawdown_pct: float
    monthly_drawdown_pct: float
    is_breached: bool = False
    breach_reason: Optional[str] = None
    recovery_pct: float = 1.0


class DrawdownTracker:
    """Tracks portfolio drawdowns and signals when limits are breached.

    Maintains a rolling history of equity values to calculate drawdowns
    over different time periods (rolling from peak, weekly, monthly).

    Attributes:
        config: DrawdownConfig with limit settings
        peak_equity: All-time peak equity value
        current_equity: Most recent equity value
    """

    # Default history length: 30 days of data
    DEFAULT_HISTORY_DAYS = 30

    def __init__(
        self,
        config: Optional[DrawdownConfig] = None,
        initial_equity: float = 0.0,
    ) -> None:
        """Initialize drawdown tracker.

        Args:
            config: DrawdownConfig instance (uses defaults if None)
            initial_equity: Starting equity value
        """
        self.config = config or DrawdownConfig()
        self.peak_equity = initial_equity
        self.current_equity = initial_equity
        self._was_breached = False

        # Maintain equity history for period calculations
        # Each entry is (timestamp, equity)
        self._history: Deque[EquityPoint] = deque()

        if initial_equity > 0:
            self._history.append(EquityPoint(datetime.now(), initial_equity))

        logger.info(
            "DrawdownTracker initialized: initial_equity=$%.2f, "
            "rolling_limit=%.1f%%, weekly_limit=%.1f%%, monthly_limit=%.1f%%",
            initial_equity,
            self.config.max_rolling_drawdown_pct * 100,
            self.config.max_weekly_drawdown_pct * 100,
            self.config.max_monthly_drawdown_pct * 100,
        )

    def update(
        self,
        current_equity: float,
        timestamp: Optional[datetime] = None,
    ) -> DrawdownState:
        """Update equity and calculate current drawdown state.

        Args:
            current_equity: Current portfolio equity value
            timestamp: Observation timestamp (defaults to now)

        Returns:
            DrawdownState with current metrics and breach status
        """
        timestamp = timestamp or datetime.now()
        self.current_equity = current_equity

        # Update peak if new high
        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
            logger.debug("New peak equity: $%.2f", self.peak_equity)

        # Add to history
        self._history.append(EquityPoint(timestamp, current_equity))

        # Prune old history (keep 30 days)
        cutoff = timestamp - timedelta(days=self.DEFAULT_HISTORY_DAYS)
        while self._history and self._history[0].timestamp < cutoff:
            self._history.popleft()

        # Calculate drawdowns
        rolling_dd = self._calculate_rolling_drawdown()
        weekly_dd = self._calculate_period_drawdown(days=7, timestamp=timestamp)
        monthly_dd = self._calculate_period_drawdown(days=30, timestamp=timestamp)

        # Check for breaches
        is_breached, breach_reason = self._check_breach(
            rolling_dd, weekly_dd, monthly_dd
        )

        # Calculate recovery percentage (how close to resuming trading)
        recovery_pct = self._calculate_recovery_pct(rolling_dd, weekly_dd, monthly_dd)

        # Log state changes
        if is_breached and not self._was_breached:
            logger.critical(
                "DRAWDOWN LIMIT BREACHED: %s (rolling=%.1f%%, weekly=%.1f%%, monthly=%.1f%%)",
                breach_reason,
                rolling_dd * 100,
                weekly_dd * 100,
                monthly_dd * 100,
            )
        elif not is_breached and self._was_breached:
            logger.info(
                "Drawdown recovered: rolling=%.1f%%, weekly=%.1f%%, monthly=%.1f%%",
                rolling_dd * 100,
                weekly_dd * 100,
                monthly_dd * 100,
            )

        self._was_breached = is_breached

        return DrawdownState(
            current_equity=current_equity,
            peak_equity=self.peak_equity,
            rolling_drawdown_pct=rolling_dd,
            weekly_drawdown_pct=weekly_dd,
            monthly_drawdown_pct=monthly_dd,
            is_breached=is_breached,
            breach_reason=breach_reason,
            recovery_pct=recovery_pct,
        )

    def get_metrics(self) -> dict:
        """Get current drawdown metrics as a dictionary.

        Returns:
            Dictionary with all drawdown metrics
        """
        state = self.update(self.current_equity)
        return {
            "current_equity": state.current_equity,
            "peak_equity": state.peak_equity,
            "rolling_drawdown_pct": state.rolling_drawdown_pct,
            "weekly_drawdown_pct": state.weekly_drawdown_pct,
            "monthly_drawdown_pct": state.monthly_drawdown_pct,
            "is_breached": state.is_breached,
            "breach_reason": state.breach_reason,
            "recovery_pct": state.recovery_pct,
            "history_points": len(self._history),
        }

    def reset(self, new_equity: float) -> None:
        """Reset tracker with new starting equity.

        Args:
            new_equity: New starting equity value
        """
        self.peak_equity = new_equity
        self.current_equity = new_equity
        self._history.clear()
        self._was_breached = False

        if new_equity > 0:
            self._history.append(EquityPoint(datetime.now(), new_equity))

        logger.info("DrawdownTracker reset: new_equity=$%.2f", new_equity)

    def _calculate_rolling_drawdown(self) -> float:
        """Calculate drawdown from all-time peak.

        Returns:
            Drawdown as a fraction (0-1)
        """
        if self.peak_equity <= 0:
            return 0.0

        return max(0.0, (self.peak_equity - self.current_equity) / self.peak_equity)

    def _calculate_period_drawdown(
        self,
        days: int,
        timestamp: datetime,
    ) -> float:
        """Calculate drawdown from peak within a time period.

        Args:
            days: Number of days to look back
            timestamp: Current timestamp

        Returns:
            Drawdown from period peak as a fraction (0-1)
        """
        cutoff = timestamp - timedelta(days=days)

        # Find peak within period
        period_peak = self.current_equity
        for point in self._history:
            if point.timestamp >= cutoff:
                if point.equity > period_peak:
                    period_peak = point.equity

        if period_peak <= 0:
            return 0.0

        return max(0.0, (period_peak - self.current_equity) / period_peak)

    def _check_breach(
        self,
        rolling_dd: float,
        weekly_dd: float,
        monthly_dd: float,
    ) -> Tuple[bool, Optional[str]]:
        """Check if any drawdown limit is breached.

        Args:
            rolling_dd: Rolling drawdown from peak
            weekly_dd: Weekly drawdown
            monthly_dd: Monthly drawdown

        Returns:
            Tuple of (is_breached, reason)
        """
        if rolling_dd >= self.config.max_rolling_drawdown_pct:
            return (
                True,
                f"Rolling drawdown {rolling_dd * 100:.1f}% >= {self.config.max_rolling_drawdown_pct * 100:.1f}% limit",
            )

        if weekly_dd >= self.config.max_weekly_drawdown_pct:
            return (
                True,
                f"Weekly drawdown {weekly_dd * 100:.1f}% >= {self.config.max_weekly_drawdown_pct * 100:.1f}% limit",
            )

        if monthly_dd >= self.config.max_monthly_drawdown_pct:
            return (
                True,
                f"Monthly drawdown {monthly_dd * 100:.1f}% >= {self.config.max_monthly_drawdown_pct * 100:.1f}% limit",
            )

        # If previously breached, check if recovered enough
        if self._was_breached:
            # Must recover to threshold fraction of each limit to resume
            threshold = self.config.drawdown_recovery_threshold
            rolling_ok = rolling_dd <= self.config.max_rolling_drawdown_pct * threshold
            weekly_ok = weekly_dd <= self.config.max_weekly_drawdown_pct * threshold
            monthly_ok = monthly_dd <= self.config.max_monthly_drawdown_pct * threshold

            if not (rolling_ok and weekly_ok and monthly_ok):
                return (True, "Drawdown not recovered to threshold")

        return (False, None)

    def _calculate_recovery_pct(
        self,
        rolling_dd: float,
        weekly_dd: float,
        monthly_dd: float,
    ) -> float:
        """Calculate how much of the limit has been recovered.

        Returns:
            1.0 if fully recovered, 0.0 if at limit, values in between for partial recovery
        """
        # Calculate utilization for each limit
        rolling_util = (
            rolling_dd / self.config.max_rolling_drawdown_pct
            if self.config.max_rolling_drawdown_pct > 0
            else 0
        )
        weekly_util = (
            weekly_dd / self.config.max_weekly_drawdown_pct
            if self.config.max_weekly_drawdown_pct > 0
            else 0
        )
        monthly_util = (
            monthly_dd / self.config.max_monthly_drawdown_pct
            if self.config.max_monthly_drawdown_pct > 0
            else 0
        )

        # Return 1 - max utilization (so 1.0 means fully recovered, 0.0 means at limit)
        max_util = max(rolling_util, weekly_util, monthly_util)
        return max(0.0, 1.0 - max_util)
