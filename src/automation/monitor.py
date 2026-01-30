"""System monitoring for market data collection status."""

import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..core import Config, MarketDatabase, get_config, setup_logger

logger = setup_logger(__name__)


class SystemMonitor:
    """Monitors system status and displays key metrics."""

    def __init__(self, config: Optional[Config] = None):
        """
        Initialize the monitor.

        Args:
            config: Configuration instance
        """
        self.config = config or get_config()
        self.db = MarketDatabase(self.config.db_path)

    def get_markets_tracked(self) -> int:
        """Get count of markets being tracked."""
        return len(self.db.get_active_markets())

    def get_snapshots_today(self) -> int:
        """Get count of snapshots captured today."""
        today = datetime.now().date().isoformat()
        conn = self.db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) as count FROM snapshots WHERE timestamp >= ?",
            (today,)
        )
        return cursor.fetchone()["count"]

    def get_success_rate(self, hours: int = 24) -> float:
        """
        Calculate snapshot success rate over recent period.

        Args:
            hours: Number of hours to look back

        Returns:
            Success rate as percentage (0-100)
        """
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
        conn = self.db._get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) as count FROM snapshots WHERE timestamp >= ?",
            (cutoff,)
        )
        total_snapshots = cursor.fetchone()["count"]

        active_markets = len(self.db.get_active_markets())
        if active_markets == 0:
            return 100.0

        # Calculate expected snapshots (every 30 min during market hours = ~14 per day)
        expected_per_market = 14
        expected_total = active_markets * expected_per_market

        if expected_total == 0:
            return 100.0

        return min(100.0, (total_snapshots / expected_total) * 100)

    def get_last_run(self) -> Optional[str]:
        """Get timestamp of most recent snapshot."""
        snapshots = self.db.get_snapshots(limit=1)
        if snapshots:
            return snapshots[0].timestamp
        return None

    def get_next_run(self) -> Optional[str]:
        """Estimate next scheduled run time."""
        now = datetime.now()

        # Find next 30-minute boundary during market hours
        if now.hour < 9 or (now.hour == 9 and now.minute < 30):
            next_run = now.replace(hour=9, minute=30, second=0, microsecond=0)
        elif now.hour >= 16:
            # Next day 9:30 AM
            next_run = (now + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        else:
            # Next 30-minute slot
            if now.minute < 30:
                next_run = now.replace(minute=30, second=0, microsecond=0)
            else:
                next_run = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

        return next_run.isoformat()

    def get_database_size(self) -> float:
        """Get database file size in MB."""
        db_path = Path(self.config.db_path)
        if db_path.exists():
            return db_path.stat().st_size / (1024 * 1024)
        return 0.0

    def get_recent_errors(self, limit: int = 10) -> list[str]:
        """
        Get recent errors from log file.

        Args:
            limit: Maximum number of errors to return

        Returns:
            List of error messages
        """
        errors = []
        log_path = Path("errors.log")

        if log_path.exists():
            with open(log_path, "r") as f:
                lines = f.readlines()
                errors = [line.strip() for line in lines[-limit:] if line.strip()]

        return errors

    def display(self) -> dict:
        """
        Gather and display all monitoring metrics.

        Returns:
            Dictionary of all metrics
        """
        metrics = {
            "markets_tracked": self.get_markets_tracked(),
            "snapshots_today": self.get_snapshots_today(),
            "success_rate": self.get_success_rate(),
            "last_run": self.get_last_run(),
            "next_run": self.get_next_run(),
            "database_size_mb": self.get_database_size(),
            "recent_errors": self.get_recent_errors(),
        }

        print("=" * 50)
        print("       SYSTEM MONITOR")
        print("=" * 50)
        print(f"Markets tracked:    {metrics['markets_tracked']}")
        print(f"Snapshots today:    {metrics['snapshots_today']}")
        print(f"Success rate:       {metrics['success_rate']:.1f}%")
        print(f"Last run:           {metrics['last_run'] or 'Never'}")
        print(f"Next run:           {metrics['next_run']}")
        print(f"Database size:      {metrics['database_size_mb']:.2f} MB")
        print("-" * 50)
        print("Recent Errors:")
        if metrics['recent_errors']:
            for error in metrics['recent_errors']:
                print(f"  {error[:80]}...")
        else:
            print("  No recent errors")
        print("=" * 50)

        return metrics

    def close(self) -> None:
        """Close database connection."""
        self.db.close()


def main():
    """CLI entry point for the monitor."""
    parser = argparse.ArgumentParser(description="System monitor")
    parser.add_argument("--config", type=str, default=None, help="Path to config file")
    args = parser.parse_args()

    config = Config.from_yaml(args.config) if args.config else get_config()
    monitor = SystemMonitor(config)

    try:
        monitor.display()
    finally:
        monitor.close()


if __name__ == "__main__":
    main()
