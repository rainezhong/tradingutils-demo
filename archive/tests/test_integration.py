"""End-to-end integration tests for the data pipeline."""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock
from pathlib import Path

from src.core import (
    Config,
    Market,
    MarketDatabase,
    Snapshot,
    set_config,
    utc_now_iso,
)
from src.collectors import Scanner, Logger
from pipeline import DataPipeline, PipelineResult


class TestPipelineIntegration(unittest.TestCase):
    """Integration tests for the full data pipeline."""

    def setUp(self):
        """Set up test fixtures with mocked API."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = Config(db_path=self.db_path, min_volume=100)
        set_config(self.config)

        # Create database
        self.db = MarketDatabase(self.db_path)
        self.db.init_db()

        # Create mock API client
        self.mock_client = Mock()

    def tearDown(self):
        """Clean up test fixtures."""
        self.db.close()
        # Clean up temp files
        for f in Path(self.temp_dir).glob("*"):
            f.unlink()
        os.rmdir(self.temp_dir)

    def _create_mock_markets(self, count: int = 5) -> list[Market]:
        """Create mock market data."""
        future_close = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        return [
            Market(
                ticker=f"TEST-{i}",
                title=f"Test Market {i}",
                status="open",
                volume_24h=1000 + i * 100,
                close_time=future_close,
            )
            for i in range(count)
        ]

    def _create_mock_orderbook(self, spread: int = 5) -> dict:
        """Create mock orderbook response."""
        bid = 45
        ask_no = 100 - (bid + spread)  # no price that gives yes_ask = bid + spread
        return {
            "orderbook": {
                "yes": [[bid, 100], [bid - 1, 200]],
                "no": [[ask_no, 150], [ask_no + 1, 100]],
            }
        }

    def test_full_pipeline_data_flow(self):
        """Test data flows correctly through all pipeline stages."""
        # Setup mock markets
        mock_markets = self._create_mock_markets(3)
        self.mock_client.get_all_markets.return_value = mock_markets
        self.mock_client.get_orderbook.return_value = self._create_mock_orderbook()

        # Create pipeline with mocked scanner and logger
        scanner = Scanner(config=self.config, client=self.mock_client, db=self.db)
        data_logger = Logger(config=self.config, client=self.mock_client, db=self.db)

        pipeline = DataPipeline(
            config=self.config,
            scanner=scanner,
            data_logger=data_logger,
        )

        # Run pipeline
        results = pipeline.run_full_pipeline(skip_on_error=False)

        # Verify scan stage
        self.assertTrue(results["scan"]["success"])
        self.assertEqual(results["scan"]["count"], 3)

        # Verify log stage
        self.assertTrue(results["log"]["success"])
        self.assertEqual(results["log"]["count"], 3)

        # Verify data in database
        markets = self.db.get_all_markets()
        self.assertEqual(len(markets), 3)

        snapshots = self.db.get_snapshots()
        self.assertEqual(len(snapshots), 3)

        # Verify snapshot data
        for snapshot in snapshots:
            self.assertEqual(snapshot.yes_bid, 45)
            self.assertEqual(snapshot.yes_ask, 50)  # 100 - 50
            self.assertEqual(snapshot.spread_cents, 5)

        pipeline.close()

    def test_pipeline_continues_on_error(self):
        """Test pipeline continues when skip_on_error is True."""
        # First call fails, subsequent calls succeed
        self.mock_client.get_all_markets.side_effect = Exception("API Error")
        self.mock_client.get_orderbook.return_value = self._create_mock_orderbook()

        scanner = Scanner(config=self.config, client=self.mock_client, db=self.db)
        data_logger = Logger(config=self.config, client=self.mock_client, db=self.db)

        pipeline = DataPipeline(
            config=self.config,
            scanner=scanner,
            data_logger=data_logger,
        )

        results = pipeline.run_full_pipeline(skip_on_error=True)

        # Scan should fail
        self.assertFalse(results["scan"]["success"])
        self.assertIn("API Error", results["scan"]["error"])

        # Log should still run (but log 0 since no markets)
        self.assertTrue(results["log"]["success"])

        pipeline.close()

    def test_pipeline_stops_on_error(self):
        """Test pipeline stops when skip_on_error is False."""
        self.mock_client.get_all_markets.side_effect = Exception("API Error")

        scanner = Scanner(config=self.config, client=self.mock_client, db=self.db)
        data_logger = Logger(config=self.config, client=self.mock_client, db=self.db)

        pipeline = DataPipeline(
            config=self.config,
            scanner=scanner,
            data_logger=data_logger,
        )

        results = pipeline.run_full_pipeline(skip_on_error=False)

        # Scan failed
        self.assertFalse(results["scan"]["success"])

        # Log stage should not have run
        self.assertNotIn("log", results)

        pipeline.close()

    def test_single_stage_execution(self):
        """Test running a single pipeline stage."""
        mock_markets = self._create_mock_markets(2)
        self.mock_client.get_all_markets.return_value = mock_markets

        scanner = Scanner(config=self.config, client=self.mock_client, db=self.db)

        pipeline = DataPipeline(
            config=self.config,
            scanner=scanner,
        )

        result = pipeline.run_stage("scan")

        self.assertTrue(result.success)
        self.assertEqual(result.count, 2)
        self.assertEqual(result.stage, "scan")

        pipeline.close()


class TestScannerLoggerIntegration(unittest.TestCase):
    """Test Scanner -> Logger data flow."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = Config(db_path=self.db_path, min_volume=100)
        set_config(self.config)

        self.db = MarketDatabase(self.db_path)
        self.db.init_db()

        self.mock_client = Mock()

    def tearDown(self):
        """Clean up."""
        self.db.close()
        for f in Path(self.temp_dir).glob("*"):
            f.unlink()
        os.rmdir(self.temp_dir)

    def test_scanner_to_logger_flow(self):
        """Test that Scanner output is properly consumed by Logger."""
        # Setup mock data
        future_close = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        mock_markets = [
            Market(
                ticker="FLOW-TEST-1",
                title="Flow Test 1",
                status="open",
                volume_24h=5000,
                close_time=future_close,
            ),
            Market(
                ticker="FLOW-TEST-2",
                title="Flow Test 2",
                status="open",
                volume_24h=3000,
                close_time=future_close,
            ),
        ]

        self.mock_client.get_all_markets.return_value = mock_markets
        self.mock_client.get_orderbook.return_value = {
            "orderbook": {
                "yes": [[40, 100]],
                "no": [[50, 100]],
            }
        }

        # Step 1: Scan markets
        scanner = Scanner(config=self.config, client=self.mock_client, db=self.db)
        scan_count = scanner.scan_and_save(min_volume=1000)
        scanner.close()

        self.assertEqual(scan_count, 2)

        # Verify markets are in database
        db_markets = self.db.get_active_markets()
        self.assertEqual(len(db_markets), 2)

        # Step 2: Log snapshots
        data_logger = Logger(config=self.config, client=self.mock_client, db=self.db)
        log_count = data_logger.log_snapshots(show_progress=False)
        data_logger.close()

        self.assertEqual(log_count, 2)

        # Verify snapshots are in database
        snapshots = self.db.get_snapshots()
        self.assertEqual(len(snapshots), 2)

        # Verify snapshot content
        tickers = {s.ticker for s in snapshots}
        self.assertEqual(tickers, {"FLOW-TEST-1", "FLOW-TEST-2"})


class TestDatabaseIntegrity(unittest.TestCase):
    """Test database integrity across operations."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = Config(db_path=self.db_path)
        set_config(self.config)

    def tearDown(self):
        """Clean up."""
        for f in Path(self.temp_dir).glob("*"):
            f.unlink()
        os.rmdir(self.temp_dir)

    def test_market_snapshot_foreign_key(self):
        """Test that snapshots reference valid markets."""
        db = MarketDatabase(self.db_path)
        db.init_db()

        # Add market
        market = Market(ticker="FK-TEST", title="FK Test", status="open")
        db.upsert_market(market)

        # Add snapshot
        snapshot = Snapshot(
            ticker="FK-TEST",
            timestamp=utc_now_iso(),
            yes_bid=45,
            yes_ask=55,
        )
        db.add_snapshot(snapshot)

        # Verify snapshot links to market
        snapshots = db.get_snapshots("FK-TEST")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].ticker, "FK-TEST")

        # Verify market exists
        retrieved_market = db.get_market("FK-TEST")
        self.assertIsNotNone(retrieved_market)

        db.close()

    def test_multiple_snapshots_per_market(self):
        """Test multiple snapshots for same market."""
        db = MarketDatabase(self.db_path)
        db.init_db()

        market = Market(ticker="MULTI-SNAP", title="Multi Snapshot", status="open")
        db.upsert_market(market)

        # Add multiple snapshots
        for i in range(5):
            snapshot = Snapshot(
                ticker="MULTI-SNAP",
                timestamp=utc_now_iso(),
                yes_bid=40 + i,
                yes_ask=50 + i,
            )
            db.add_snapshot(snapshot)

        snapshots = db.get_snapshots("MULTI-SNAP")
        self.assertEqual(len(snapshots), 5)

        db.close()


class TestPipelineResult(unittest.TestCase):
    """Test PipelineResult dataclass."""

    def test_success_result(self):
        """Test successful result creation."""
        result = PipelineResult(
            stage="test",
            success=True,
            count=10,
        )

        self.assertEqual(result.stage, "test")
        self.assertTrue(result.success)
        self.assertEqual(result.count, 10)
        self.assertIsNone(result.error)

    def test_failure_result(self):
        """Test failure result creation."""
        result = PipelineResult(
            stage="test",
            success=False,
            error="Something went wrong",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Something went wrong")

    def test_to_dict(self):
        """Test dictionary conversion."""
        result = PipelineResult(
            stage="test",
            success=True,
            count=5,
        )

        d = result.to_dict()
        self.assertEqual(d["stage"], "test")
        self.assertTrue(d["success"])
        self.assertEqual(d["count"], 5)


class TestImports(unittest.TestCase):
    """Test that all imports work correctly without circular dependencies."""

    def test_core_imports(self):
        """Test core module imports."""
        from src.core import (
            Config,
            Market,
        )

        self.assertIsNotNone(Config)
        self.assertIsNotNone(Market)

    def test_collectors_imports(self):
        """Test collectors module imports."""
        from src.collectors import Scanner, Logger

        self.assertIsNotNone(Scanner)
        self.assertIsNotNone(Logger)

    def test_analysis_imports(self):
        """Test analysis module imports."""
        from src.analysis import MarketMetrics, MarketScorer

        self.assertIsNotNone(MarketMetrics)
        self.assertIsNotNone(MarketScorer)

    def test_automation_imports(self):
        """Test automation module imports."""
        from src.automation import MarketMakerScheduler, HealthCheck

        self.assertIsNotNone(MarketMakerScheduler)
        self.assertIsNotNone(HealthCheck)

    def test_pipeline_import(self):
        """Test pipeline import."""
        from pipeline import DataPipeline

        self.assertIsNotNone(DataPipeline)

    def test_main_import(self):
        """Test main module import."""
        import main

        self.assertIsNotNone(main.cmd_scan)
        self.assertIsNotNone(main.cmd_log)


if __name__ == "__main__":
    unittest.main()
