"""Scheduler for automated market data collection and analysis."""

import argparse
import signal
import time
from datetime import datetime
from typing import Callable, Optional

import schedule

try:
    from src.core import Config, get_config, MarketDatabase, setup_logger
except ImportError:
    Config = None
    get_config = None
    MarketDatabase = None
    from logging import getLogger as setup_logger  # type: ignore[assignment]

try:
    from core.trading_state import get_trading_state
except ImportError:
    get_trading_state = None  # type: ignore[assignment]

try:
    from src.collectors import Scanner, Logger
except ImportError:
    Scanner = None  # type: ignore[assignment]
    Logger = None  # type: ignore[assignment]

logger = setup_logger(__name__)


class MarketMakerScheduler:
    """Schedules and runs automated market data collection jobs."""

    def __init__(
        self,
        config: Optional["Config"] = None,
        scanner: Optional["Scanner"] = None,
        data_logger: Optional["Logger"] = None,
    ):
        """
        Initialize the scheduler.

        Args:
            config: Configuration instance
            scanner: Scanner instance for market discovery
            data_logger: Logger instance for snapshot collection
        """
        self.config = config or (get_config() if get_config else None)
        self.scanner = scanner or (Scanner(self.config) if Scanner else None)
        self.data_logger = data_logger or (Logger(self.config) if Logger else None)
        self._running = False
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _handle_shutdown(self, signum: int, frame) -> None:
        """Handle shutdown signals gracefully."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        self._running = False

    def _is_market_hours(self) -> bool:
        """
        Check if current time is within market hours (9:30 AM - 4:00 PM).

        Returns:
            True if within market hours
        """
        now = datetime.now()
        market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)
        return market_open <= now <= market_close

    def scan_markets(self) -> int:
        """
        Scan for new markets and save to database.

        Returns:
            Number of markets discovered
        """
        logger.info("Starting market scan...")
        try:
            count = self.scanner.scan_and_save()
            logger.info(f"Market scan complete: {count} markets saved")
            return count
        except Exception as e:
            logger.error(f"Market scan failed: {e}")
            raise

    def log_data(self) -> int:
        """
        Log market snapshots for all active markets using bulk mode.

        Returns:
            Number of snapshots logged
        """
        logger.info("Starting data logging (bulk mode)...")
        try:
            count = self.data_logger.log_snapshots_bulk(show_progress=False)
            logger.info(f"Data logging complete: {count} snapshots")
            return count
        except Exception as e:
            logger.error(f"Data logging failed: {e}")
            raise

    def analyze_markets(self) -> dict:
        """
        Run market analysis and return summary.

        Returns:
            Analysis results dictionary
        """
        logger.info("Starting market analysis...")
        try:
            db = MarketDatabase(self.config.db_path)
            stats = db.get_summary_stats()

            results = {
                "total_markets": stats.total_markets,
                "total_snapshots": stats.total_snapshots,
                "avg_spread_cents": stats.avg_spread_cents,
                "avg_spread_pct": stats.avg_spread_pct,
            }

            logger.info(
                f"Analysis complete: {stats.total_markets} markets, {stats.total_snapshots} snapshots"
            )
            db.close()
            return results
        except Exception as e:
            logger.error(f"Market analysis failed: {e}")
            raise

    def setup_jobs(self) -> None:
        """Configure all scheduled jobs."""
        # Daily market scan at 6:00 AM
        schedule.every().day.at("06:00").do(
            self._run_job, self.scan_markets, "scan_markets"
        )

        # Data logging every hour, 24/7 (Kalshi markets run all day)
        schedule.every().hour.do(self._run_job, self.log_data, "log_data")

        # Daily analysis at 8:00 AM and 8:00 PM
        schedule.every().day.at("08:00").do(
            self._run_job, self.analyze_markets, "analyze_markets"
        )
        schedule.every().day.at("20:00").do(
            self._run_job, self.analyze_markets, "analyze_markets"
        )

        logger.info("Scheduled jobs configured:")
        logger.info("  - Market scan: daily at 6:00 AM")
        logger.info("  - Data logging: every hour")
        logger.info("  - Analysis: daily at 8:00 AM and 8:00 PM")
        self._log_next_runs()

    def _run_job(self, job_func: Callable, job_name: str) -> None:
        """
        Run a job with error handling and logging.

        Pauses if trading is active to avoid competing for rate limits.

        Args:
            job_func: Function to execute
            job_name: Name for logging
        """
        # Check if trading is active - pause if so
        if get_trading_state is not None:
            trading_state = get_trading_state()
            if trading_state.should_pause():
                logger.info(f"Pausing job {job_name}: trading is active")
                if not trading_state.wait_while_paused(timeout=60.0):
                    logger.info(
                        f"Skipping job {job_name}: trading still active after timeout"
                    )
                    return

        logger.info(f"Running job: {job_name}")
        try:
            job_func()
            logger.info(f"Job {job_name} completed successfully")
        except Exception as e:
            logger.error(f"Job {job_name} failed: {e}")

    def _run_job_if_market_hours(self, job_func: Callable, job_name: str) -> None:
        """
        Run a job only if within market hours.

        Args:
            job_func: Function to execute
            job_name: Name for logging
        """
        if self._is_market_hours():
            self._run_job(job_func, job_name)
        else:
            logger.debug(f"Skipping {job_name}: outside market hours")

    def _log_next_runs(self) -> None:
        """Log the next scheduled run times."""
        jobs = schedule.get_jobs()
        if jobs:
            next_job = min(jobs, key=lambda j: j.next_run)
            logger.info(f"Next scheduled job at: {next_job.next_run}")

    def get_next_run(self) -> Optional[datetime]:
        """
        Get the next scheduled job time.

        Returns:
            Next run datetime or None if no jobs scheduled
        """
        jobs = schedule.get_jobs()
        if jobs:
            return min(j.next_run for j in jobs)
        return None

    def run_forever(self, check_interval: int = 60) -> None:
        """
        Run the scheduler loop indefinitely.

        Args:
            check_interval: Seconds between schedule checks
        """
        self.setup_jobs()
        self._running = True
        logger.info("Scheduler started, running forever...")

        while self._running:
            try:
                schedule.run_pending()
                time.sleep(check_interval)
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                time.sleep(check_interval)

        # Cleanup
        self._cleanup()
        logger.info("Scheduler stopped")

    def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        schedule.clear()
        if self.scanner:
            self.scanner.close()
        if self.data_logger:
            self.data_logger.close()
        logger.info("Resources cleaned up")

    def run_once(self, job_name: str) -> None:
        """
        Run a specific job immediately.

        Args:
            job_name: Name of job to run (scan_markets, log_data, analyze_markets)
        """
        jobs = {
            "scan_markets": self.scan_markets,
            "log_data": self.log_data,
            "analyze_markets": self.analyze_markets,
        }

        if job_name not in jobs:
            raise ValueError(
                f"Unknown job: {job_name}. Valid jobs: {list(jobs.keys())}"
            )

        self._run_job(jobs[job_name], job_name)


def main():
    """CLI entry point for the scheduler."""
    parser = argparse.ArgumentParser(description="Market data scheduler")
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file",
    )
    parser.add_argument(
        "--run-once",
        type=str,
        choices=["scan_markets", "log_data", "analyze_markets"],
        default=None,
        help="Run a specific job once and exit",
    )
    args = parser.parse_args()

    # Load config if specified
    if args.config:
        config = Config.from_yaml(args.config)
    else:
        config = get_config() if get_config else None

    scheduler = MarketMakerScheduler(config)

    if args.run_once:
        print(f"Running job: {args.run_once}")
        scheduler.run_once(args.run_once)
        scheduler._cleanup()
    else:
        print("Starting scheduler (Ctrl+C to stop)...")
        scheduler.run_forever()


if __name__ == "__main__":
    main()
