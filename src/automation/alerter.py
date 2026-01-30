"""Alerting system for monitoring critical conditions."""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from ..core import Config, MarketDatabase, get_config, setup_logger, parse_iso_timestamp

logger = setup_logger(__name__)


@dataclass
class Alert:
    """Represents an alert condition."""
    level: str  # "info", "warning", "error", "critical"
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def __str__(self) -> str:
        return f"[{self.level.upper()}] {self.timestamp}: {self.message}"


class Alerter:
    """Monitors conditions and sends alerts when thresholds are exceeded."""

    def __init__(
        self,
        config: Optional[Config] = None,
        db_size_threshold_mb: float = 500.0,
        consecutive_failures_threshold: int = 3,
        score_threshold: float = 15.0,
    ):
        """
        Initialize the alerter.

        Args:
            config: Configuration instance
            db_size_threshold_mb: Alert when database exceeds this size
            consecutive_failures_threshold: Alert after this many consecutive failures
            score_threshold: Alert when market score exceeds this value
        """
        self.config = config or get_config()
        self.db_size_threshold_mb = db_size_threshold_mb
        self.consecutive_failures_threshold = consecutive_failures_threshold
        self.score_threshold = score_threshold
        self._failure_count = 0
        self._last_snapshot_time: Optional[datetime] = None
        self._alert_handlers: list[Callable[[Alert], None]] = [self._console_handler]

    def _console_handler(self, alert: Alert) -> None:
        """Print alert to console."""
        print(f"\n{'='*60}")
        print(f"  ALERT: {alert.level.upper()}")
        print(f"  {alert.message}")
        print(f"  Time: {alert.timestamp}")
        print(f"{'='*60}\n")

    def add_handler(self, handler: Callable[[Alert], None]) -> None:
        """
        Add a custom alert handler.

        Args:
            handler: Callable that receives Alert objects
        """
        self._alert_handlers.append(handler)

    def _send_alert(self, level: str, message: str) -> Alert:
        """
        Create and dispatch an alert.

        Args:
            level: Alert level (info, warning, error, critical)
            message: Alert message

        Returns:
            Created Alert object
        """
        alert = Alert(level=level, message=message)
        logger.warning(f"Alert triggered: {alert}")

        for handler in self._alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Alert handler failed: {e}")

        return alert

    def record_collection_result(self, success: bool) -> Optional[Alert]:
        """
        Record a data collection attempt result.

        Args:
            success: Whether the collection succeeded

        Returns:
            Alert if threshold exceeded, None otherwise
        """
        if success:
            self._failure_count = 0
            self._last_snapshot_time = datetime.now()
            return None

        self._failure_count += 1
        logger.warning(f"Data collection failed ({self._failure_count} consecutive)")

        if self._failure_count >= self.consecutive_failures_threshold:
            return self._send_alert(
                "error",
                f"Data collection has failed {self._failure_count} times in a row"
            )

        return None

    def check_snapshot_freshness(self) -> Optional[Alert]:
        """
        Check if snapshots are fresh (within last 2 hours during market hours).

        Returns:
            Alert if stale, None otherwise
        """
        now = datetime.now()

        # Only check during market hours (9:30 AM - 4:00 PM)
        if not (9 <= now.hour < 16 or (now.hour == 9 and now.minute >= 30)):
            return None

        try:
            db = MarketDatabase(self.config.db_path)
            snapshots = db.get_snapshots(limit=1)
            db.close()

            if not snapshots:
                return self._send_alert(
                    "warning",
                    "No snapshots found in database"
                )

            last_time = parse_iso_timestamp(snapshots[0].timestamp)
            if last_time.tzinfo is not None:
                last_time = last_time.replace(tzinfo=None)

            age = now - last_time

            if age > timedelta(hours=2):
                hours_ago = age.total_seconds() / 3600
                return self._send_alert(
                    "error",
                    f"No snapshots in last 2 hours during market hours (last: {hours_ago:.1f}h ago)"
                )

            return None

        except Exception as e:
            logger.error(f"Error checking snapshot freshness: {e}")
            return None

    def check_database_size(self) -> Optional[Alert]:
        """
        Check if database size exceeds threshold.

        Returns:
            Alert if exceeded, None otherwise
        """
        db_path = Path(self.config.db_path)

        if not db_path.exists():
            return None

        size_mb = db_path.stat().st_size / (1024 * 1024)

        if size_mb > self.db_size_threshold_mb:
            return self._send_alert(
                "warning",
                f"Database size ({size_mb:.1f} MB) exceeds threshold ({self.db_size_threshold_mb} MB)"
            )

        return None

    def check_new_market(self, ticker: str, score: float) -> Optional[Alert]:
        """
        Check if a new market qualifies based on score.

        Args:
            ticker: Market ticker
            score: Calculated market score

        Returns:
            Alert if qualified, None otherwise
        """
        if score > self.score_threshold:
            return self._send_alert(
                "info",
                f"New market qualifies: {ticker} (score: {score:.1f})"
            )
        return None

    def run_all_checks(self) -> list[Alert]:
        """
        Run all periodic checks.

        Returns:
            List of triggered alerts
        """
        alerts = []

        alert = self.check_snapshot_freshness()
        if alert:
            alerts.append(alert)

        alert = self.check_database_size()
        if alert:
            alerts.append(alert)

        return alerts


def main():
    """CLI entry point for alerter."""
    parser = argparse.ArgumentParser(description="Run alert checks")
    parser.add_argument("--config", type=str, default=None, help="Path to config file")
    parser.add_argument(
        "--db-threshold",
        type=float,
        default=500.0,
        help="Database size threshold in MB",
    )
    args = parser.parse_args()

    config = Config.from_yaml(args.config) if args.config else get_config()
    alerter = Alerter(config, db_size_threshold_mb=args.db_threshold)

    print("Running alert checks...")
    alerts = alerter.run_all_checks()

    if not alerts:
        print("No alerts triggered.")
    else:
        print(f"\n{len(alerts)} alert(s) triggered.")


if __name__ == "__main__":
    main()
