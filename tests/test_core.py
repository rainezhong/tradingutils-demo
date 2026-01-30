"""Tests for core module functionality."""

import os
import tempfile
import unittest
from datetime import datetime, timezone

from src.core.config import Config, RateLimitConfig, get_config, set_config
from src.core.models import Market, Snapshot, SummaryStats, ValidationError
from src.core.database import MarketDatabase, create_database
from src.core.utils import (
    calculate_spread,
    parse_iso_timestamp,
    utc_now,
    utc_now_iso,
)


class TestConfig(unittest.TestCase):
    """Tests for configuration management."""

    def test_default_config(self):
        """Test default configuration values."""
        config = Config()
        self.assertEqual(config.db_path, "data/markets.db")
        self.assertEqual(config.api_timeout, 30)
        self.assertEqual(config.min_volume, 1000)
        self.assertEqual(config.rate_limits.requests_per_second, 10)
        self.assertEqual(config.rate_limits.requests_per_minute, 100)

    def test_config_from_dict(self):
        """Test configuration from dictionary."""
        data = {
            "db_path": "custom/path.db",
            "min_volume": 500,
            "rate_limits": {
                "requests_per_second": 5,
                "requests_per_minute": 50,
            },
        }
        config = Config._from_dict(data)
        self.assertEqual(config.db_path, "custom/path.db")
        self.assertEqual(config.min_volume, 500)
        self.assertEqual(config.rate_limits.requests_per_second, 5)

    def test_config_from_yaml(self):
        """Test configuration from YAML file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("db_path: test.db\nmin_volume: 2000\n")
            f.flush()
            config = Config.from_yaml(f.name)
            self.assertEqual(config.db_path, "test.db")
            self.assertEqual(config.min_volume, 2000)
            os.unlink(f.name)


class TestModels(unittest.TestCase):
    """Tests for data models."""

    def test_market_creation(self):
        """Test Market model creation."""
        market = Market(ticker="TEST-TICKER", title="Test Market")
        self.assertEqual(market.ticker, "TEST-TICKER")
        self.assertEqual(market.title, "Test Market")

    def test_market_validation_empty_ticker(self):
        """Test Market validation rejects empty ticker."""
        with self.assertRaises(ValidationError):
            Market(ticker="", title="Test")

    def test_market_validation_empty_title(self):
        """Test Market validation rejects empty title."""
        with self.assertRaises(ValidationError):
            Market(ticker="TEST", title="")

    def test_market_validation_negative_volume(self):
        """Test Market validation rejects negative volume."""
        with self.assertRaises(ValidationError):
            Market(ticker="TEST", title="Test", volume_24h=-100)

    def test_market_from_api_response(self):
        """Test Market creation from API response."""
        data = {
            "ticker": "API-TEST",
            "title": "API Test Market",
            "category": "politics",
            "status": "open",
            "volume_24h": 5000,
        }
        market = Market.from_api_response(data)
        self.assertEqual(market.ticker, "API-TEST")
        self.assertEqual(market.category, "politics")

    def test_snapshot_creation(self):
        """Test Snapshot model creation."""
        snapshot = Snapshot(
            ticker="TEST",
            timestamp=utc_now_iso(),
            yes_bid=45,
            yes_ask=55,
        )
        self.assertEqual(snapshot.ticker, "TEST")
        self.assertEqual(snapshot.yes_bid, 45)

    def test_snapshot_validation_price_range(self):
        """Test Snapshot validation for price range."""
        with self.assertRaises(ValidationError):
            Snapshot(ticker="TEST", timestamp=utc_now_iso(), yes_bid=150)

    def test_snapshot_validation_bid_less_than_ask(self):
        """Test Snapshot validation ensures bid <= ask."""
        with self.assertRaises(ValidationError):
            Snapshot(
                ticker="TEST",
                timestamp=utc_now_iso(),
                yes_bid=60,
                yes_ask=40,
            )

    def test_snapshot_from_orderbook(self):
        """Test Snapshot creation from orderbook data."""
        orderbook = {
            "orderbook": {
                "yes": [[45, 100], [44, 200]],
                "no": [[55, 150], [56, 100]],
            }
        }
        snapshot = Snapshot.from_orderbook("TEST", orderbook)
        self.assertEqual(snapshot.yes_bid, 45)
        self.assertEqual(snapshot.yes_ask, 45)  # 100 - 55
        self.assertEqual(snapshot.orderbook_bid_depth, 300)
        self.assertEqual(snapshot.orderbook_ask_depth, 250)

    def test_summary_stats_str(self):
        """Test SummaryStats string representation."""
        stats = SummaryStats(
            total_markets=10,
            total_snapshots=100,
            avg_spread_cents=3.5,
            avg_spread_pct=5.2,
            min_spread=1,
            max_spread=12,
        )
        output = str(stats)
        self.assertIn("Total Markets: 10", output)
        self.assertIn("Avg Spread: 3.5 cents", output)


class TestDatabase(unittest.TestCase):
    """Tests for database operations."""

    def setUp(self):
        """Set up test database."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        # Set config before creating database
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_init_db(self):
        """Test database initialization creates tables."""
        self.assertTrue(os.path.exists(self.db_path))

    def test_upsert_market(self):
        """Test inserting and updating markets."""
        market = Market(
            ticker="TEST-MKT",
            title="Test Market",
            status="open",
            volume_24h=1000,
        )
        self.db.upsert_market(market)

        retrieved = self.db.get_market("TEST-MKT")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.title, "Test Market")

        # Update
        market.volume_24h = 2000
        self.db.upsert_market(market)
        retrieved = self.db.get_market("TEST-MKT")
        self.assertEqual(retrieved.volume_24h, 2000)

    def test_get_active_markets(self):
        """Test retrieving active markets."""
        market1 = Market(ticker="OPEN-1", title="Open Market", status="open")
        market2 = Market(ticker="CLOSED-1", title="Closed Market", status="closed")
        self.db.upsert_market(market1)
        self.db.upsert_market(market2)

        active = self.db.get_active_markets()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].ticker, "OPEN-1")

    def test_add_snapshot(self):
        """Test adding snapshots."""
        market = Market(ticker="SNAP-TEST", title="Snapshot Test", status="open")
        self.db.upsert_market(market)

        snapshot = Snapshot(
            ticker="SNAP-TEST",
            timestamp=utc_now_iso(),
            yes_bid=45,
            yes_ask=55,
            spread_cents=10,
            spread_pct=20.0,
            mid_price=50.0,
        )
        row_id = self.db.add_snapshot(snapshot)
        self.assertIsNotNone(row_id)

        snapshots = self.db.get_snapshots("SNAP-TEST")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].spread_cents, 10)

    def test_get_summary_stats(self):
        """Test summary statistics."""
        market = Market(ticker="STATS-TEST", title="Stats Test", status="open")
        self.db.upsert_market(market)

        for i in range(5):
            snapshot = Snapshot(
                ticker="STATS-TEST",
                timestamp=utc_now_iso(),
                spread_cents=i + 1,
                spread_pct=float(i + 1),
            )
            self.db.add_snapshot(snapshot)

        stats = self.db.get_summary_stats()
        self.assertEqual(stats.total_markets, 1)
        self.assertEqual(stats.total_snapshots, 5)
        self.assertEqual(stats.min_spread, 1)
        self.assertEqual(stats.max_spread, 5)


class TestUtils(unittest.TestCase):
    """Tests for utility functions."""

    def test_utc_now(self):
        """Test UTC datetime generation."""
        now = utc_now()
        self.assertEqual(now.tzinfo, timezone.utc)

    def test_utc_now_iso(self):
        """Test ISO timestamp generation."""
        iso = utc_now_iso()
        self.assertIn("T", iso)
        self.assertIn("+00:00", iso)

    def test_parse_iso_timestamp(self):
        """Test ISO timestamp parsing."""
        dt = parse_iso_timestamp("2024-01-15T12:30:00Z")
        self.assertEqual(dt.year, 2024)
        self.assertEqual(dt.month, 1)
        self.assertEqual(dt.hour, 12)

    def test_parse_iso_timestamp_with_offset(self):
        """Test ISO timestamp parsing with offset."""
        dt = parse_iso_timestamp("2024-01-15T12:30:00+00:00")
        self.assertEqual(dt.year, 2024)

    def test_calculate_spread(self):
        """Test spread calculation."""
        spread_cents, spread_pct, mid_price = calculate_spread(45, 55)
        self.assertEqual(spread_cents, 10)
        self.assertEqual(mid_price, 50.0)
        self.assertEqual(spread_pct, 20.0)

    def test_calculate_spread_zero_mid(self):
        """Test spread calculation with zero mid price."""
        spread_cents, spread_pct, mid_price = calculate_spread(0, 0)
        self.assertEqual(spread_pct, 0.0)


if __name__ == "__main__":
    unittest.main()
