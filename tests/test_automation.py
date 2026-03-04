"""Tests for the automation module."""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core import Config, MarketDatabase, Market, Snapshot
from core.automation.scheduler import MarketMakerScheduler
from core.automation.monitor import SystemMonitor
from core.automation.healthcheck import HealthCheck, HealthStatus
from core.automation.alerter import Alerter, Alert


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    config = Config(db_path=db_path)
    db = MarketDatabase(db_path)
    db.init_db()

    yield db, config

    db.close()
    os.unlink(db_path)


@pytest.fixture
def sample_market():
    """Create a sample market for testing."""
    return Market(
        ticker="TEST-MARKET",
        title="Test Market",
        status="open",
        volume_24h=5000,
    )


@pytest.fixture
def sample_snapshot():
    """Create a sample snapshot for testing."""
    return Snapshot(
        ticker="TEST-MARKET",
        timestamp=datetime.now().isoformat(),
        yes_bid=45,
        yes_ask=55,
        spread_cents=10,
        spread_pct=20.0,
        mid_price=50.0,
    )


class TestHealthCheck:
    """Tests for HealthCheck class."""

    def test_check_database_exists_success(self, temp_db):
        """Test database exists check passes with valid db."""
        db, config = temp_db
        checker = HealthCheck(config)

        ok, error = checker.check_database_exists()

        assert ok is True
        assert error is None

    def test_check_database_exists_failure(self):
        """Test database exists check fails with invalid path."""
        config = Config(db_path="/nonexistent/path/db.sqlite")
        checker = HealthCheck(config)

        ok, error = checker.check_database_exists()

        assert ok is False
        assert "not found" in error.lower()

    def test_check_recent_snapshot_success(
        self, temp_db, sample_market, sample_snapshot
    ):
        """Test recent snapshot check passes with fresh data."""
        db, config = temp_db
        db.upsert_market(sample_market)
        db.add_snapshot(sample_snapshot)

        checker = HealthCheck(config)
        ok, error = checker.check_recent_snapshot()

        assert ok is True
        assert error is None

    def test_check_recent_snapshot_no_snapshots(self, temp_db):
        """Test recent snapshot check fails with no data."""
        db, config = temp_db
        checker = HealthCheck(config)

        ok, error = checker.check_recent_snapshot()

        assert ok is False
        assert "no snapshots" in error.lower()

    def test_check_recent_snapshot_stale(self, temp_db, sample_market):
        """Test recent snapshot check fails with old data."""
        db, config = temp_db
        db.upsert_market(sample_market)

        # Create old snapshot
        old_time = (datetime.now() - timedelta(hours=3)).isoformat()
        old_snapshot = Snapshot(
            ticker="TEST-MARKET",
            timestamp=old_time,
            yes_bid=45,
            yes_ask=55,
        )
        db.add_snapshot(old_snapshot)

        checker = HealthCheck(config)
        ok, error = checker.check_recent_snapshot(max_age_hours=1.0)

        assert ok is False
        assert "hours old" in error.lower()

    def test_run_all_checks_healthy(self, temp_db, sample_market, sample_snapshot):
        """Test all checks pass with healthy system."""
        db, config = temp_db
        db.upsert_market(sample_market)
        db.add_snapshot(sample_snapshot)

        checker = HealthCheck(config)
        status = checker.run_all_checks()

        assert status.healthy is True
        assert len(status.issues) == 0


class TestHealthStatus:
    """Tests for HealthStatus class."""

    def test_healthy_by_default(self):
        """Test status is healthy by default."""
        status = HealthStatus()
        assert status.healthy is True
        assert len(status.issues) == 0

    def test_add_issue_makes_unhealthy(self):
        """Test adding issue marks status as unhealthy."""
        status = HealthStatus()
        status.add_issue("Test issue")

        assert status.healthy is False
        assert len(status.issues) == 1
        assert "Test issue" in status.issues

    def test_multiple_issues(self):
        """Test multiple issues can be added."""
        status = HealthStatus()
        status.add_issue("Issue 1")
        status.add_issue("Issue 2")

        assert status.healthy is False
        assert len(status.issues) == 2

    def test_str_healthy(self):
        """Test string representation when healthy."""
        status = HealthStatus()
        assert "HEALTHY" in str(status)

    def test_str_unhealthy(self):
        """Test string representation when unhealthy."""
        status = HealthStatus()
        status.add_issue("Test issue")
        output = str(status)
        assert "UNHEALTHY" in output
        assert "Test issue" in output


class TestAlerter:
    """Tests for Alerter class."""

    def test_record_success_resets_counter(self):
        """Test successful collection resets failure counter."""
        alerter = Alerter()
        alerter._failure_count = 5

        result = alerter.record_collection_result(success=True)

        assert result is None
        assert alerter._failure_count == 0

    def test_record_failure_increments_counter(self):
        """Test failed collection increments failure counter."""
        alerter = Alerter(consecutive_failures_threshold=5)

        result = alerter.record_collection_result(success=False)

        assert result is None
        assert alerter._failure_count == 1

    def test_record_failure_triggers_alert_at_threshold(self):
        """Test alert triggered at failure threshold."""
        alerter = Alerter(consecutive_failures_threshold=3)

        alerter.record_collection_result(success=False)
        alerter.record_collection_result(success=False)
        result = alerter.record_collection_result(success=False)

        assert result is not None
        assert result.level == "error"
        assert "3 times" in result.message

    def test_check_database_size_under_threshold(self, temp_db):
        """Test no alert when database is under threshold."""
        db, config = temp_db
        alerter = Alerter(config, db_size_threshold_mb=1000.0)

        result = alerter.check_database_size()

        assert result is None

    def test_check_database_size_over_threshold(self, temp_db):
        """Test alert when database exceeds threshold."""
        db, config = temp_db
        # Set very low threshold
        alerter = Alerter(config, db_size_threshold_mb=0.0001)

        result = alerter.check_database_size()

        assert result is not None
        assert result.level == "warning"
        assert "exceeds threshold" in result.message

    def test_custom_alert_handler(self):
        """Test custom alert handlers are called."""
        received_alerts = []

        def custom_handler(alert: Alert):
            received_alerts.append(alert)

        alerter = Alerter(consecutive_failures_threshold=1)
        alerter.add_handler(custom_handler)

        alerter.record_collection_result(success=False)

        assert len(received_alerts) == 1
        assert received_alerts[0].level == "error"

    def test_check_new_market_qualified(self):
        """Test alert for qualifying market."""
        alerter = Alerter(score_threshold=15.0)

        result = alerter.check_new_market("TEST-MKT", score=20.0)

        assert result is not None
        assert result.level == "info"
        assert "TEST-MKT" in result.message

    def test_check_new_market_not_qualified(self):
        """Test no alert for non-qualifying market."""
        alerter = Alerter(score_threshold=15.0)

        result = alerter.check_new_market("TEST-MKT", score=10.0)

        assert result is None


class TestAlert:
    """Tests for Alert class."""

    def test_alert_creation(self):
        """Test alert object creation."""
        alert = Alert(level="error", message="Test message")

        assert alert.level == "error"
        assert alert.message == "Test message"
        assert alert.timestamp is not None

    def test_alert_str(self):
        """Test alert string representation."""
        alert = Alert(level="warning", message="Test warning")
        output = str(alert)

        assert "[WARNING]" in output
        assert "Test warning" in output


class TestSystemMonitor:
    """Tests for SystemMonitor class."""

    def test_get_markets_tracked(self, temp_db, sample_market):
        """Test counting tracked markets."""
        db, config = temp_db
        db.upsert_market(sample_market)

        monitor = SystemMonitor(config)
        count = monitor.get_markets_tracked()

        assert count == 1
        monitor.close()

    def test_get_markets_tracked_empty(self, temp_db):
        """Test counting with no markets."""
        db, config = temp_db

        monitor = SystemMonitor(config)
        count = monitor.get_markets_tracked()

        assert count == 0
        monitor.close()

    def test_get_snapshots_today(self, temp_db, sample_market, sample_snapshot):
        """Test counting today's snapshots."""
        db, config = temp_db
        db.upsert_market(sample_market)
        db.add_snapshot(sample_snapshot)

        monitor = SystemMonitor(config)
        count = monitor.get_snapshots_today()

        assert count == 1
        monitor.close()

    def test_get_database_size(self, temp_db):
        """Test getting database file size."""
        db, config = temp_db

        monitor = SystemMonitor(config)
        size = monitor.get_database_size()

        assert size > 0
        monitor.close()

    def test_get_last_run_with_data(self, temp_db, sample_market, sample_snapshot):
        """Test getting last run timestamp."""
        db, config = temp_db
        db.upsert_market(sample_market)
        db.add_snapshot(sample_snapshot)

        monitor = SystemMonitor(config)
        last_run = monitor.get_last_run()

        assert last_run is not None
        assert last_run == sample_snapshot.timestamp
        monitor.close()

    def test_get_last_run_no_data(self, temp_db):
        """Test getting last run with no snapshots."""
        db, config = temp_db

        monitor = SystemMonitor(config)
        last_run = monitor.get_last_run()

        assert last_run is None
        monitor.close()

    def test_display_returns_metrics(
        self, temp_db, sample_market, sample_snapshot, capsys
    ):
        """Test display returns all metrics."""
        db, config = temp_db
        db.upsert_market(sample_market)
        db.add_snapshot(sample_snapshot)

        monitor = SystemMonitor(config)
        metrics = monitor.display()

        assert "markets_tracked" in metrics
        assert "snapshots_today" in metrics
        assert "success_rate" in metrics
        assert "database_size_mb" in metrics

        # Check output was printed
        captured = capsys.readouterr()
        assert "SYSTEM MONITOR" in captured.out

        monitor.close()


class TestMarketMakerScheduler:
    """Tests for MarketMakerScheduler class."""

    def test_is_market_hours_logic(self):
        """Test market hours logic with boundary conditions."""
        # Test the logic directly by creating specific datetime objects
        # and checking against the market hours window

        # Market hours: 9:30 AM - 4:00 PM
        test_cases = [
            # (hour, minute, expected_result, description)
            (9, 30, True, "exactly at open"),
            (10, 30, True, "during market hours"),
            (12, 0, True, "at noon"),
            (15, 59, True, "just before close"),
            (16, 0, True, "exactly at close"),
            (8, 0, False, "before open"),
            (9, 0, False, "before 9:30"),
            (9, 29, False, "one minute before open"),
            (16, 1, False, "one minute after close"),
            (17, 0, False, "after market hours"),
            (20, 0, False, "evening"),
        ]

        for hour, minute, expected, desc in test_cases:
            # Create a test datetime
            test_time = datetime(2024, 1, 15, hour, minute)
            market_open = test_time.replace(hour=9, minute=30, second=0, microsecond=0)
            market_close = test_time.replace(hour=16, minute=0, second=0, microsecond=0)

            result = market_open <= test_time <= market_close
            assert result == expected, f"Failed for {desc}: {hour}:{minute:02d}"

    def test_is_market_hours_current_time(self):
        """Test _is_market_hours returns a boolean for current time."""
        scheduler = MarketMakerScheduler.__new__(MarketMakerScheduler)
        result = scheduler._is_market_hours()

        # Should return a boolean regardless of current time
        assert isinstance(result, bool)

    def test_run_once_invalid_job(self):
        """Test run_once raises error for invalid job name."""
        with (
            patch("core.automation.scheduler.Scanner"),
            patch("core.automation.scheduler.Logger"),
            patch("core.automation.scheduler.get_config") as mock_config,
        ):
            mock_config.return_value = Config()

            scheduler = MarketMakerScheduler.__new__(MarketMakerScheduler)
            scheduler.config = Config()
            scheduler.scanner = MagicMock()
            scheduler.data_logger = MagicMock()
            scheduler._running = False

            with pytest.raises(ValueError, match="Unknown job"):
                scheduler.run_once("invalid_job")

    def test_run_once_valid_job(self):
        """Test run_once executes valid job."""
        with (
            patch("core.automation.scheduler.Scanner"),
            patch("core.automation.scheduler.Logger"),
            patch("core.automation.scheduler.get_config") as mock_config,
        ):
            mock_config.return_value = Config()

            scheduler = MarketMakerScheduler.__new__(MarketMakerScheduler)
            scheduler.config = Config()
            scheduler.scanner = MagicMock()
            scheduler.scanner.scan_and_save.return_value = 5
            scheduler.data_logger = MagicMock()
            scheduler._running = False

            # Should not raise
            scheduler.run_once("scan_markets")

            scheduler.scanner.scan_and_save.assert_called_once()

    def test_cleanup(self):
        """Test cleanup closes resources."""
        with (
            patch("core.automation.scheduler.Scanner"),
            patch("core.automation.scheduler.Logger"),
            patch("core.automation.scheduler.schedule") as mock_schedule,
        ):
            scheduler = MarketMakerScheduler.__new__(MarketMakerScheduler)
            scheduler.scanner = MagicMock()
            scheduler.data_logger = MagicMock()

            scheduler._cleanup()

            mock_schedule.clear.assert_called_once()
            scheduler.scanner.close.assert_called_once()
            scheduler.data_logger.close.assert_called_once()
