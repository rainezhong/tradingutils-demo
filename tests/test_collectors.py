"""Tests for data collection module."""

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, patch, MagicMock

from src.core import (
    Config,
    Market,
    MarketDatabase,
    Snapshot,
    set_config,
    utc_now_iso,
)
from src.collectors import Scanner, Logger, OrderbookFetcher, OrderbookDepth


class TestScanner(unittest.TestCase):
    """Tests for Scanner class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = Config(db_path=self.db_path)
        set_config(self.config)

        self.mock_client = Mock()
        self.db = MarketDatabase(self.db_path)
        self.db.init_db()

        self.scanner = Scanner(
            config=self.config,
            client=self.mock_client,
            db=self.db,
        )

    def tearDown(self):
        """Clean up."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_filter_by_volume(self):
        """Test that scanner filters by minimum volume."""
        # Future close time (30 days from now)
        future_close = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        markets = [
            Market(ticker="HIGH-VOL", title="High Volume", volume_24h=5000, status="open", close_time=future_close),
            Market(ticker="LOW-VOL", title="Low Volume", volume_24h=500, status="open", close_time=future_close),
            Market(ticker="MED-VOL", title="Medium Volume", volume_24h=1500, status="open", close_time=future_close),
        ]
        self.mock_client.get_all_markets.return_value = markets

        result = self.scanner.scan(min_volume=1000, min_days_until_close=7)

        self.assertEqual(len(result), 2)
        tickers = [m.ticker for m in result]
        self.assertIn("HIGH-VOL", tickers)
        self.assertIn("MED-VOL", tickers)
        self.assertNotIn("LOW-VOL", tickers)

    def test_filter_by_status(self):
        """Test that scanner only includes open markets."""
        future_close = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        markets = [
            Market(ticker="OPEN-MKT", title="Open", volume_24h=5000, status="open", close_time=future_close),
            Market(ticker="CLOSED-MKT", title="Closed", volume_24h=5000, status="closed", close_time=future_close),
        ]
        self.mock_client.get_all_markets.return_value = markets

        result = self.scanner.scan(min_volume=1000, min_days_until_close=7)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ticker, "OPEN-MKT")

    def test_filter_by_days_until_close(self):
        """Test that scanner filters by days until close."""
        soon_close = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        later_close = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        markets = [
            Market(ticker="SOON", title="Closes Soon", volume_24h=5000, status="open", close_time=soon_close),
            Market(ticker="LATER", title="Closes Later", volume_24h=5000, status="open", close_time=later_close),
        ]
        self.mock_client.get_all_markets.return_value = markets

        result = self.scanner.scan(min_volume=1000, min_days_until_close=7)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ticker, "LATER")

    def test_scan_and_save(self):
        """Test that scan_and_save stores markets in database."""
        future_close = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()

        markets = [
            Market(ticker="SAVE-TEST", title="Save Test", volume_24h=5000, status="open", close_time=future_close),
        ]
        self.mock_client.get_all_markets.return_value = markets

        count = self.scanner.scan_and_save(min_volume=1000, min_days_until_close=7)

        self.assertEqual(count, 1)
        saved = self.db.get_market("SAVE-TEST")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.title, "Save Test")

    def test_retry_on_failure(self):
        """Test that scanner retries on failure."""
        self.mock_client.get_all_markets.side_effect = [
            Exception("Network error"),
            Exception("Network error"),
            [],  # Success on third attempt
        ]

        result = self.scanner.scan(min_volume=1000)

        self.assertEqual(len(result), 0)
        self.assertEqual(self.mock_client.get_all_markets.call_count, 3)


class TestLogger(unittest.TestCase):
    """Tests for Logger class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = Config(db_path=self.db_path)
        set_config(self.config)

        self.mock_client = Mock()
        self.db = MarketDatabase(self.db_path)
        self.db.init_db()

        self.logger = Logger(
            config=self.config,
            client=self.mock_client,
            db=self.db,
        )

    def tearDown(self):
        """Clean up."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_log_snapshots_creates_records(self):
        """Test that log_snapshots creates snapshot records."""
        # Add market to database
        market = Market(ticker="LOG-TEST", title="Log Test", status="open", volume_24h=1000)
        self.db.upsert_market(market)

        # Mock orderbook response
        self.mock_client.get_orderbook.return_value = {
            "orderbook": {
                "yes": [[45, 100]],
                "no": [[55, 100]],
            }
        }

        count = self.logger.log_snapshots(show_progress=False)

        self.assertEqual(count, 1)
        snapshots = self.db.get_snapshots("LOG-TEST")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].yes_bid, 45)
        self.assertEqual(snapshots[0].yes_ask, 45)  # 100 - 55

    def test_log_specific_tickers(self):
        """Test logging specific tickers."""
        market1 = Market(ticker="TICK-1", title="Ticker 1", status="open")
        market2 = Market(ticker="TICK-2", title="Ticker 2", status="open")
        self.db.upsert_market(market1)
        self.db.upsert_market(market2)

        self.mock_client.get_orderbook.return_value = {
            "orderbook": {"yes": [[50, 100]], "no": [[50, 100]]}
        }

        count = self.logger.log_snapshots(tickers=["TICK-1"], show_progress=False)

        self.assertEqual(count, 1)
        # Only TICK-1 should be called
        self.mock_client.get_orderbook.assert_called_once_with("TICK-1")

    def test_batch_insert_performance(self):
        """Test that batch insert works correctly."""
        # Add multiple markets
        for i in range(150):  # More than BATCH_SIZE
            market = Market(ticker=f"BATCH-{i}", title=f"Batch {i}", status="open")
            self.db.upsert_market(market)

        self.mock_client.get_orderbook.return_value = {
            "orderbook": {"yes": [[50, 100]], "no": [[50, 100]]}
        }

        count = self.logger.log_snapshots(show_progress=False)

        self.assertEqual(count, 150)

    def test_continues_on_single_failure(self):
        """Test that logger continues when one market fails."""
        market1 = Market(ticker="GOOD", title="Good", status="open")
        market2 = Market(ticker="BAD", title="Bad", status="open")
        self.db.upsert_market(market1)
        self.db.upsert_market(market2)

        def mock_orderbook(ticker):
            if ticker == "BAD":
                raise Exception("API error")
            return {"orderbook": {"yes": [[50, 100]], "no": [[50, 100]]}}

        self.mock_client.get_orderbook.side_effect = mock_orderbook

        count = self.logger.log_snapshots(show_progress=False)

        # Should have logged the good one
        self.assertEqual(count, 1)


class TestOrderbookFetcher(unittest.TestCase):
    """Tests for OrderbookFetcher class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.config = Config(db_path=self.db_path)
        set_config(self.config)

        self.mock_client = Mock()
        self.db = MarketDatabase(self.db_path)
        self.db.init_db()

        self.fetcher = OrderbookFetcher(
            config=self.config,
            client=self.mock_client,
            db=self.db,
        )

    def tearDown(self):
        """Clean up."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_parse_orderbook_depth(self):
        """Test orderbook parsing calculates depth correctly."""
        orderbook = {
            "orderbook": {
                "yes": [[45, 100], [44, 200], [43, 150]],
                "no": [[55, 100], [56, 50]],
            }
        }

        depth = self.fetcher._parse_orderbook("TEST", orderbook)

        self.assertEqual(depth.ticker, "TEST")
        self.assertEqual(depth.yes_bid, 45)
        self.assertEqual(depth.yes_ask, 45)  # 100 - 55
        self.assertEqual(depth.bid_depth_total, 450)  # 100 + 200 + 150
        self.assertEqual(depth.ask_depth_total, 150)  # 100 + 50
        self.assertEqual(depth.bid_levels, 3)
        self.assertEqual(depth.ask_levels, 2)
        self.assertEqual(depth.bid_depth_at_best, 100)
        self.assertEqual(depth.ask_depth_at_best, 100)

    def test_spread_calculation(self):
        """Test spread is calculated correctly."""
        orderbook = {
            "orderbook": {
                "yes": [[40, 100]],
                "no": [[50, 100]],  # yes_ask = 100 - 50 = 50
            }
        }

        depth = self.fetcher._parse_orderbook("TEST", orderbook)

        self.assertEqual(depth.spread_cents, 10)  # 50 - 40
        self.assertEqual(depth.mid_price, 45.0)  # (40 + 50) / 2
        self.assertAlmostEqual(depth.spread_pct, 22.22, places=1)  # 10 / 45 * 100

    def test_fetch_and_store(self):
        """Test fetching and storing orderbook data."""
        market = Market(ticker="STORE-TEST", title="Store Test", status="open", volume_24h=1000)
        self.db.upsert_market(market)

        self.mock_client.get_orderbook.return_value = {
            "orderbook": {
                "yes": [[45, 100]],
                "no": [[55, 100]],
            }
        }

        count = self.fetcher.fetch_and_store(show_progress=False)

        self.assertEqual(count, 1)
        snapshots = self.db.get_snapshots("STORE-TEST")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].orderbook_bid_depth, 100)
        self.assertEqual(snapshots[0].orderbook_ask_depth, 100)

    def test_handles_empty_orderbook(self):
        """Test handling of empty orderbook."""
        orderbook = {"orderbook": {"yes": [], "no": []}}

        depth = self.fetcher._parse_orderbook("EMPTY", orderbook)

        self.assertIsNone(depth.yes_bid)
        self.assertIsNone(depth.yes_ask)
        self.assertEqual(depth.bid_depth_total, 0)
        self.assertEqual(depth.ask_depth_total, 0)

    def test_retry_on_failure(self):
        """Test retry logic on API failure."""
        self.mock_client.get_orderbook.side_effect = [
            Exception("Timeout"),
            Exception("Timeout"),
            {"orderbook": {"yes": [[50, 100]], "no": [[50, 100]]}},
        ]

        depth = self.fetcher.fetch_depth("RETRY-TEST")

        self.assertIsNotNone(depth)
        self.assertEqual(self.mock_client.get_orderbook.call_count, 3)

    def test_gives_up_after_max_retries(self):
        """Test that fetcher gives up after max retries."""
        self.mock_client.get_orderbook.side_effect = Exception("Persistent error")

        depth = self.fetcher.fetch_depth("FAIL-TEST", max_retries=3)

        self.assertIsNone(depth)
        self.assertEqual(self.mock_client.get_orderbook.call_count, 3)


class TestOrderbookDepth(unittest.TestCase):
    """Tests for OrderbookDepth dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        depth = OrderbookDepth(ticker="TEST")

        self.assertEqual(depth.ticker, "TEST")
        self.assertIsNone(depth.yes_bid)
        self.assertEqual(depth.bid_depth_total, 0)
        self.assertIsNotNone(depth.bid_volume_by_price)
        self.assertEqual(len(depth.bid_volume_by_price), 0)

    def test_volume_by_price_tracking(self):
        """Test volume by price tracking."""
        depth = OrderbookDepth(
            ticker="TEST",
            bid_volume_by_price={45: 100, 44: 200},
            ask_volume_by_price={55: 150, 56: 100},
        )

        self.assertEqual(depth.bid_volume_by_price[45], 100)
        self.assertEqual(depth.ask_volume_by_price[56], 100)


if __name__ == "__main__":
    unittest.main()
