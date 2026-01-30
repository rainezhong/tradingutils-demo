"""Health check for system status verification."""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import schedule

from ..core import Config, MarketDatabase, get_config, setup_logger, parse_iso_timestamp

logger = setup_logger(__name__)


@dataclass
class HealthStatus:
    """Health check result."""
    healthy: bool = True
    issues: list[str] = field(default_factory=list)

    def add_issue(self, issue: str) -> None:
        """Add an issue and mark as unhealthy."""
        self.issues.append(issue)
        self.healthy = False

    def __str__(self) -> str:
        if self.healthy:
            return "HEALTHY: All checks passed"
        return f"UNHEALTHY: {len(self.issues)} issue(s) found\n" + "\n".join(f"  - {i}" for i in self.issues)


class HealthCheck:
    """Performs health checks on the data collection system."""

    def __init__(self, config: Optional[Config] = None):
        """
        Initialize health checker.

        Args:
            config: Configuration instance
        """
        self.config = config or get_config()

    def check_database_exists(self) -> tuple[bool, Optional[str]]:
        """
        Verify database file exists and is accessible.

        Returns:
            Tuple of (success, error_message)
        """
        db_path = Path(self.config.db_path)

        if not db_path.exists():
            return False, f"Database not found at {db_path}"

        try:
            db = MarketDatabase(str(db_path))
            db._get_connection()
            db.close()
            return True, None
        except Exception as e:
            return False, f"Database not accessible: {e}"

    def check_recent_snapshot(self, max_age_hours: float = 1.0) -> tuple[bool, Optional[str]]:
        """
        Verify snapshots are being captured recently.

        Args:
            max_age_hours: Maximum age of last snapshot in hours

        Returns:
            Tuple of (success, error_message)
        """
        try:
            db = MarketDatabase(self.config.db_path)
            snapshots = db.get_snapshots(limit=1)
            db.close()

            if not snapshots:
                return False, "No snapshots found in database"

            last_snapshot = snapshots[0]
            last_time = parse_iso_timestamp(last_snapshot.timestamp)

            # Make last_time timezone-naive for comparison
            if last_time.tzinfo is not None:
                last_time = last_time.replace(tzinfo=None)

            age = datetime.now() - last_time
            max_age = timedelta(hours=max_age_hours)

            if age > max_age:
                hours_ago = age.total_seconds() / 3600
                return False, f"Last snapshot is {hours_ago:.1f} hours old (max: {max_age_hours}h)"

            return True, None

        except Exception as e:
            return False, f"Error checking snapshots: {e}"

    def check_scheduled_jobs(self) -> tuple[bool, Optional[str]]:
        """
        Verify scheduled jobs are configured.

        Returns:
            Tuple of (success, error_message)
        """
        jobs = schedule.get_jobs()

        if not jobs:
            return False, "No scheduled jobs found (scheduler may not be running)"

        return True, None

    def check_error_rate(self, max_errors: int = 10, hours: int = 24) -> tuple[bool, Optional[str]]:
        """
        Check error log for excessive errors.

        Args:
            max_errors: Maximum acceptable errors in time period
            hours: Time period to check in hours

        Returns:
            Tuple of (success, error_message)
        """
        log_path = Path("errors.log")

        if not log_path.exists():
            return True, None  # No errors logged

        cutoff = datetime.now() - timedelta(hours=hours)
        recent_errors = 0

        try:
            with open(log_path, "r") as f:
                for line in f:
                    # Parse timestamp from log line (format: YYYY-MM-DD HH:MM:SS,mmm)
                    try:
                        timestamp_str = line[:23]
                        timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
                        if timestamp > cutoff:
                            recent_errors += 1
                    except (ValueError, IndexError):
                        continue

            if recent_errors > max_errors:
                return False, f"{recent_errors} errors in last {hours}h (max: {max_errors})"

            return True, None

        except Exception as e:
            return False, f"Error reading error log: {e}"

    def run_all_checks(self) -> HealthStatus:
        """
        Run all health checks.

        Returns:
            HealthStatus with results
        """
        status = HealthStatus()

        # Database check
        ok, error = self.check_database_exists()
        if not ok:
            status.add_issue(error)

        # Recent snapshot check
        ok, error = self.check_recent_snapshot()
        if not ok:
            status.add_issue(error)

        # Scheduled jobs check (only warn, don't fail)
        ok, error = self.check_scheduled_jobs()
        if not ok:
            logger.warning(error)

        # Error rate check
        ok, error = self.check_error_rate()
        if not ok:
            status.add_issue(error)

        return status


def main():
    """CLI entry point for health check."""
    parser = argparse.ArgumentParser(description="Health check")
    parser.add_argument("--config", type=str, default=None, help="Path to config file")
    parser.add_argument(
        "--alert-if-unhealthy",
        action="store_true",
        help="Exit with code 1 if unhealthy",
    )
    args = parser.parse_args()

    config = Config.from_yaml(args.config) if args.config else get_config()
    checker = HealthCheck(config)

    status = checker.run_all_checks()
    print(status)

    if args.alert_if_unhealthy and not status.healthy:
        sys.exit(1)


if __name__ == "__main__":
    main()
