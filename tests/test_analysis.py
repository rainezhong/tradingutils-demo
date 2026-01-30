"""Tests for analysis module functionality."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.core.config import Config, set_config
from src.core.database import MarketDatabase, create_database
from src.core.models import Market, Snapshot
from src.core.utils import utc_now_iso

from src.analysis.metrics import MarketMetrics
from src.analysis.scorer import MarketScorer
from src.analysis.ranker import MarketRanker, TradabilityFilter
from src.analysis.correlation import CorrelationDetector, CorrelationMatch


class TestMarketScorer(unittest.TestCase):
    """Tests for MarketScorer scoring logic."""

    def setUp(self):
        """Set up scorer instance."""
        self.scorer = MarketScorer()

    def test_max_score(self):
        """Test maximum score constant."""
        self.assertEqual(self.scorer.MAX_SCORE, 20)

    def test_score_spread_above_5_percent(self):
        """Test spread score for >5% gets 5 points."""
        metrics = {"avg_spread_pct": 6.0}
        score = self.scorer._score_spread(metrics["avg_spread_pct"])
        self.assertEqual(score, 5)

    def test_score_spread_4_to_5_percent(self):
        """Test spread score for 4-5% gets 4 points."""
        metrics = {"avg_spread_pct": 4.5}
        score = self.scorer._score_spread(metrics["avg_spread_pct"])
        self.assertEqual(score, 4)

    def test_score_spread_3_to_4_percent(self):
        """Test spread score for 3-4% gets 2 points."""
        metrics = {"avg_spread_pct": 3.5}
        score = self.scorer._score_spread(metrics["avg_spread_pct"])
        self.assertEqual(score, 2)

    def test_score_spread_below_3_percent(self):
        """Test spread score for <3% gets 0 points."""
        metrics = {"avg_spread_pct": 2.5}
        score = self.scorer._score_spread(metrics["avg_spread_pct"])
        self.assertEqual(score, 0)

    def test_score_spread_none(self):
        """Test spread score for None value."""
        score = self.scorer._score_spread(None)
        self.assertEqual(score, 0)

    def test_score_volume_above_5k(self):
        """Test volume score for >5k gets 5 points."""
        score = self.scorer._score_volume(6000)
        self.assertEqual(score, 5)

    def test_score_volume_2k_to_5k(self):
        """Test volume score for 2-5k gets 3 points."""
        score = self.scorer._score_volume(3000)
        self.assertEqual(score, 3)

    def test_score_volume_1k_to_2k(self):
        """Test volume score for 1-2k gets 1 point."""
        score = self.scorer._score_volume(1500)
        self.assertEqual(score, 1)

    def test_score_volume_below_1k(self):
        """Test volume score for <1k gets 0 points."""
        score = self.scorer._score_volume(500)
        self.assertEqual(score, 0)

    def test_score_stability_below_1_5_percent(self):
        """Test stability score for std <1.5% gets 5 points."""
        score = self.scorer._score_stability(1.0)
        self.assertEqual(score, 5)

    def test_score_stability_below_3_percent(self):
        """Test stability score for std <3% gets 3 points."""
        score = self.scorer._score_stability(2.5)
        self.assertEqual(score, 3)

    def test_score_stability_below_5_percent(self):
        """Test stability score for std <5% gets 1 point."""
        score = self.scorer._score_stability(4.0)
        self.assertEqual(score, 1)

    def test_score_stability_above_5_percent(self):
        """Test stability score for std >5% gets 0 points."""
        score = self.scorer._score_stability(6.0)
        self.assertEqual(score, 0)

    def test_score_depth_above_100(self):
        """Test depth score for >100 gets 5 points."""
        score = self.scorer._score_depth(150)
        self.assertEqual(score, 5)

    def test_score_depth_above_50(self):
        """Test depth score for >50 gets 3 points."""
        score = self.scorer._score_depth(75)
        self.assertEqual(score, 3)

    def test_score_depth_above_20(self):
        """Test depth score for >20 gets 1 point."""
        score = self.scorer._score_depth(30)
        self.assertEqual(score, 1)

    def test_score_depth_below_20(self):
        """Test depth score for <20 gets 0 points."""
        score = self.scorer._score_depth(10)
        self.assertEqual(score, 0)

    def test_score_market_full_score(self):
        """Test total score calculation for max score scenario."""
        metrics = {
            "ticker": "TEST",
            "avg_spread_pct": 6.0,       # 5 points
            "avg_volume": 6000,           # 5 points
            "spread_volatility": 1.0,     # 5 points
            "avg_depth": 150,             # 5 points
        }
        score = self.scorer.score_market(metrics)
        self.assertEqual(score, 20)

    def test_score_market_zero_score(self):
        """Test total score calculation for zero score scenario."""
        metrics = {
            "ticker": "TEST",
            "avg_spread_pct": 2.0,        # 0 points
            "avg_volume": 500,             # 0 points
            "spread_volatility": 6.0,      # 0 points
            "avg_depth": 10,               # 0 points
        }
        score = self.scorer.score_market(metrics)
        self.assertEqual(score, 0)

    def test_score_market_partial_score(self):
        """Test total score calculation for mixed scenario."""
        metrics = {
            "ticker": "TEST",
            "avg_spread_pct": 4.5,        # 4 points
            "avg_volume": 3000,            # 3 points
            "spread_volatility": 2.5,      # 3 points
            "avg_depth": 30,               # 1 point
        }
        score = self.scorer.score_market(metrics)
        self.assertEqual(score, 11)

    def test_score_market_detailed(self):
        """Test detailed score breakdown."""
        metrics = {
            "ticker": "TEST-DETAIL",
            "avg_spread_pct": 5.5,
            "avg_volume": 4000,
            "spread_volatility": 1.2,
            "avg_depth": 80,
        }
        result = self.scorer.score_market_detailed(metrics)

        self.assertEqual(result["ticker"], "TEST-DETAIL")
        self.assertEqual(result["spread_score"], 5)
        self.assertEqual(result["volume_score"], 3)
        self.assertEqual(result["stability_score"], 5)
        self.assertEqual(result["depth_score"], 3)
        self.assertEqual(result["total_score"], 16)
        self.assertEqual(result["max_score"], 20)

    def test_score_market_with_none_values(self):
        """Test scoring handles None values gracefully."""
        metrics = {
            "ticker": "TEST",
            "avg_spread_pct": None,
            "avg_volume": None,
            "spread_volatility": None,
            "avg_depth": None,
        }
        score = self.scorer.score_market(metrics)
        self.assertEqual(score, 0)


class TestMarketMetrics(unittest.TestCase):
    """Tests for MarketMetrics calculation."""

    def setUp(self):
        """Set up test database and metrics calculator."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.metrics = MarketMetrics(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_empty_metrics_no_data(self):
        """Test metrics returns empty structure when no data."""
        result = self.metrics.calculate_metrics("NONEXISTENT", days=3)

        self.assertEqual(result["ticker"], "NONEXISTENT")
        self.assertEqual(result["snapshot_count"], 0)
        self.assertIsNone(result["avg_spread_pct"])
        self.assertIsNone(result["avg_volume"])
        self.assertIsNone(result["spread_volatility"])
        self.assertIsNone(result["avg_depth"])
        self.assertEqual(result["price_range"], (None, None))

    def test_calculate_metrics_with_data(self):
        """Test metrics calculation with actual data."""
        market = Market(ticker="METRICS-TEST", title="Metrics Test", status="open")
        self.db.upsert_market(market)

        # Add recent snapshots
        now = datetime.now(timezone.utc)
        for i in range(5):
            ts = (now - timedelta(hours=i)).isoformat()
            snapshot = Snapshot(
                ticker="METRICS-TEST",
                timestamp=ts,
                yes_bid=45,
                yes_ask=55,
                spread_cents=10,
                spread_pct=20.0,
                mid_price=50.0,
                volume_24h=3000 + (i * 100),
                orderbook_bid_depth=100,
                orderbook_ask_depth=100,
            )
            self.db.add_snapshot(snapshot)

        result = self.metrics.calculate_metrics("METRICS-TEST", days=3)

        self.assertEqual(result["ticker"], "METRICS-TEST")
        self.assertEqual(result["snapshot_count"], 5)
        self.assertEqual(result["avg_spread_pct"], 20.0)
        self.assertIsNotNone(result["avg_volume"])
        self.assertEqual(result["avg_depth"], 200.0)

    def test_metrics_filters_old_data(self):
        """Test that metrics only includes data within time period."""
        market = Market(ticker="OLD-DATA", title="Old Data Test", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)

        # Add recent snapshot
        recent = Snapshot(
            ticker="OLD-DATA",
            timestamp=now.isoformat(),
            spread_pct=10.0,
            volume_24h=1000,
        )
        self.db.add_snapshot(recent)

        # Add old snapshot (outside window)
        old_time = (now - timedelta(days=10)).isoformat()
        old = Snapshot(
            ticker="OLD-DATA",
            timestamp=old_time,
            spread_pct=50.0,
            volume_24h=9000,
        )
        self.db.add_snapshot(old)

        result = self.metrics.calculate_metrics("OLD-DATA", days=3)

        # Should only include recent data
        self.assertEqual(result["snapshot_count"], 1)
        self.assertEqual(result["avg_spread_pct"], 10.0)


class TestMarketRanker(unittest.TestCase):
    """Tests for MarketRanker functionality."""

    def setUp(self):
        """Set up test database and ranker."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.ranker = MarketRanker(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        # Clean up any exported CSV files
        for f in os.listdir(self.temp_dir):
            os.unlink(os.path.join(self.temp_dir, f))
        os.rmdir(self.temp_dir)

    def test_get_top_markets_empty_db(self):
        """Test get_top_markets with empty database."""
        result = self.ranker.get_top_markets(n=10)
        self.assertTrue(result.empty)

    def test_get_top_markets_with_data(self):
        """Test get_top_markets returns sorted results."""
        now = datetime.now(timezone.utc)

        # Create markets with different quality metrics
        for i, (spread, volume) in enumerate([
            (6.0, 6000),   # High score
            (4.5, 3000),   # Medium score
            (2.0, 500),    # Low score
        ]):
            market = Market(
                ticker=f"RANK-{i}",
                title=f"Rank Test {i}",
                status="open",
            )
            self.db.upsert_market(market)

            snapshot = Snapshot(
                ticker=f"RANK-{i}",
                timestamp=now.isoformat(),
                spread_pct=spread,
                volume_24h=volume,
                orderbook_bid_depth=150,
                orderbook_ask_depth=150,
            )
            self.db.add_snapshot(snapshot)

        result = self.ranker.get_top_markets(n=10, min_score=0)

        self.assertFalse(result.empty)
        # First result should have highest score
        self.assertEqual(result.iloc[0]["ticker"], "RANK-0")

    def test_get_market_summary(self):
        """Test get_market_summary returns complete info."""
        market = Market(
            ticker="SUMMARY-TEST",
            title="Summary Test Market",
            category="test",
            status="open",
        )
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)
        snapshot = Snapshot(
            ticker="SUMMARY-TEST",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        )
        self.db.add_snapshot(snapshot)

        result = self.ranker.get_market_summary("SUMMARY-TEST")

        self.assertEqual(result["ticker"], "SUMMARY-TEST")
        self.assertEqual(result["title"], "Summary Test Market")
        self.assertIn("metrics", result)
        self.assertIn("scores", result)

    def test_get_market_summary_not_found(self):
        """Test get_market_summary handles missing market."""
        result = self.ranker.get_market_summary("NONEXISTENT")
        self.assertIn("error", result)

    def test_export_to_csv(self):
        """Test export_to_csv creates valid file."""
        market = Market(ticker="EXPORT-TEST", title="Export Test", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)
        snapshot = Snapshot(
            ticker="EXPORT-TEST",
            timestamp=now.isoformat(),
            spread_pct=5.0,
        )
        self.db.add_snapshot(snapshot)

        output_file = os.path.join(self.temp_dir, "rankings.csv")
        result_path = self.ranker.export_to_csv(output_file)

        self.assertTrue(os.path.exists(result_path))


class TestCorrelationDetector(unittest.TestCase):
    """Tests for CorrelationDetector functionality."""

    def setUp(self):
        """Set up test database and detector."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.detector = CorrelationDetector(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_detect_correlations_empty_db(self):
        """Test detect_correlations with empty database."""
        result = self.detector.detect_correlations()
        self.assertEqual(len(result), 0)

    def test_detect_correlations_fed_rates(self):
        """Test detection of fed_rates category."""
        # Add markets with fed-related keywords
        markets = [
            Market(ticker="FED-1", title="Will the Fed raise interest rates?"),
            Market(ticker="FED-2", title="FOMC meeting rate decision"),
            Market(ticker="OTHER", title="Weather in Chicago"),
        ]
        for m in markets:
            self.db.upsert_market(m)

        result = self.detector.detect_correlations()

        self.assertIn("fed_rates", result)
        self.assertEqual(len(result["fed_rates"].markets), 2)
        self.assertIn("FED-1", result["fed_rates"].markets)
        self.assertIn("FED-2", result["fed_rates"].markets)

    def test_detect_correlations_politics(self):
        """Test detection of politics category."""
        markets = [
            Market(ticker="POL-1", title="Will Biden win the election?"),
            Market(ticker="POL-2", title="Republican vs Democrat in Senate"),
        ]
        for m in markets:
            self.db.upsert_market(m)

        result = self.detector.detect_correlations()

        self.assertIn("politics", result)
        self.assertGreaterEqual(len(result["politics"].markets), 2)

    def test_get_correlated_markets(self):
        """Test finding markets correlated to a specific ticker."""
        markets = [
            Market(ticker="WEATHER-1", title="Hurricane will hit Florida"),
            Market(ticker="WEATHER-2", title="Temperature storm warning"),
            Market(ticker="UNRELATED", title="Stock market trends"),
        ]
        for m in markets:
            self.db.upsert_market(m)

        result = self.detector.get_correlated_markets("WEATHER-1")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ticker"], "WEATHER-2")
        self.assertIn("weather", result[0]["shared_categories"])

    def test_get_correlated_markets_not_found(self):
        """Test get_correlated_markets with nonexistent ticker."""
        result = self.detector.get_correlated_markets("NONEXISTENT")
        self.assertEqual(len(result), 0)

    def test_flag_for_review(self):
        """Test flag_for_review with multiple correlated markets."""
        # Add multiple markets in same category
        for i in range(5):
            market = Market(
                ticker=f"CRYPTO-{i}",
                title=f"Bitcoin price prediction {i}",
            )
            self.db.upsert_market(market)

        result = self.detector.flag_for_review(min_markets=3)

        self.assertGreater(len(result), 0)
        crypto_flag = next(
            (r for r in result if r["category"] == "crypto"),
            None
        )
        self.assertIsNotNone(crypto_flag)
        self.assertGreaterEqual(crypto_flag["market_count"], 3)

    def test_add_custom_category(self):
        """Test adding custom keyword category."""
        self.detector.add_custom_category(
            "custom_test",
            ["unique_keyword", "another_term"]
        )

        market = Market(ticker="CUSTOM-1", title="Market with unique_keyword")
        self.db.upsert_market(market)

        categories = self.detector._categorize_market(market)
        self.assertIn("custom_test", categories)

    def test_categorize_market_multiple_categories(self):
        """Test market matching multiple categories."""
        market = Market(
            ticker="MULTI-CAT",
            title="Fed interest rate impact on crypto bitcoin"
        )
        self.db.upsert_market(market)

        categories = self.detector._categorize_market(market)

        self.assertIn("fed_rates", categories)
        self.assertIn("crypto", categories)


class TestTradabilityFilter(unittest.TestCase):
    """Tests for tradability filtering functionality."""

    def setUp(self):
        """Set up test database and ranker."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.ranker = MarketRanker(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_filter_closed_market(self):
        """Test that closed markets are filtered out."""
        now = datetime.now(timezone.utc)

        # Create open market
        open_market = Market(ticker="OPEN-MKT", title="Open Market", status="open")
        self.db.upsert_market(open_market)
        self.db.add_snapshot(Snapshot(
            ticker="OPEN-MKT",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # Create closed market
        closed_market = Market(ticker="CLOSED-MKT", title="Closed Market", status="closed")
        self.db.upsert_market(closed_market)
        self.db.add_snapshot(Snapshot(
            ticker="CLOSED-MKT",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # Without filter - both markets should appear
        results = self.ranker.get_all_rankings(filter_untradeable=False)
        self.assertEqual(len(results), 2)

        # With filter - only open market should appear
        results = self.ranker.get_all_rankings(filter_untradeable=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results.iloc[0]["ticker"], "OPEN-MKT")

    def test_filter_expired_market(self):
        """Test that expired markets are filtered out."""
        now = datetime.now(timezone.utc)
        past_time = (now - timedelta(days=1)).isoformat()
        future_time = (now + timedelta(days=7)).isoformat()

        # Create non-expired market
        active_market = Market(
            ticker="ACTIVE-MKT",
            title="Active Market",
            status="open",
            close_time=future_time,
        )
        self.db.upsert_market(active_market)
        self.db.add_snapshot(Snapshot(
            ticker="ACTIVE-MKT",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # Create expired market
        expired_market = Market(
            ticker="EXPIRED-MKT",
            title="Expired Market",
            status="open",
            close_time=past_time,
        )
        self.db.upsert_market(expired_market)
        self.db.add_snapshot(Snapshot(
            ticker="EXPIRED-MKT",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # With filter - only active market should appear
        results = self.ranker.get_all_rankings(filter_untradeable=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results.iloc[0]["ticker"], "ACTIVE-MKT")

    def test_filter_stale_market(self):
        """Test that markets with no recent snapshots are filtered out."""
        now = datetime.now(timezone.utc)
        old_time = (now - timedelta(days=5)).isoformat()

        # Create market with recent snapshots
        fresh_market = Market(ticker="FRESH-MKT", title="Fresh Market", status="open")
        self.db.upsert_market(fresh_market)
        self.db.add_snapshot(Snapshot(
            ticker="FRESH-MKT",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # Create market with only old snapshots (outside metrics window)
        stale_market = Market(ticker="STALE-MKT", title="Stale Market", status="open")
        self.db.upsert_market(stale_market)
        self.db.add_snapshot(Snapshot(
            ticker="STALE-MKT",
            timestamp=old_time,
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # With filter - only fresh market should appear (stale has 0 snapshots in window)
        results = self.ranker.get_all_rankings(days=3, filter_untradeable=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results.iloc[0]["ticker"], "FRESH-MKT")


class TestStrategyMatrix(unittest.TestCase):
    """Tests for strategy matrix functionality."""

    def setUp(self):
        """Set up test database and ranker."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.ranker = MarketRanker(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_strategy_matrix_empty_db(self):
        """Test strategy matrix with empty database."""
        result = self.ranker.get_strategy_matrix()
        self.assertTrue(result.empty)

    def test_strategy_matrix_columns(self):
        """Test strategy matrix returns expected columns."""
        now = datetime.now(timezone.utc)
        market = Market(ticker="MATRIX-TEST", title="Matrix Test", status="open")
        self.db.upsert_market(market)
        self.db.add_snapshot(Snapshot(
            ticker="MATRIX-TEST",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
            orderbook_bid_depth=100,
            orderbook_ask_depth=100,
        ))

        result = self.ranker.get_strategy_matrix()

        expected_columns = [
            "ticker", "title", "status", "mm_score",
            "market_making", "spread_trading", "momentum",
            "scalping", "arbitrage", "event_trading", "best_strategy"
        ]
        for col in expected_columns:
            self.assertIn(col, result.columns)

    def test_strategy_matrix_with_filter(self):
        """Test strategy matrix respects tradability filter."""
        now = datetime.now(timezone.utc)

        # Open market
        open_market = Market(ticker="OPEN-TEST", title="Open Test", status="open")
        self.db.upsert_market(open_market)
        self.db.add_snapshot(Snapshot(
            ticker="OPEN-TEST",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # Closed market
        closed_market = Market(ticker="CLOSED-TEST", title="Closed Test", status="closed")
        self.db.upsert_market(closed_market)
        self.db.add_snapshot(Snapshot(
            ticker="CLOSED-TEST",
            timestamp=now.isoformat(),
            spread_pct=5.0,
            volume_24h=3000,
        ))

        # Without filter
        result = self.ranker.get_strategy_matrix(filter_untradeable=False)
        self.assertEqual(len(result), 2)

        # With filter
        result = self.ranker.get_strategy_matrix(filter_untradeable=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0]["ticker"], "OPEN-TEST")


class TestPricePosition(unittest.TestCase):
    """Tests for price position calculation."""

    def setUp(self):
        """Set up test database and metrics calculator."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.metrics = MarketMetrics(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_price_position_extreme_low(self):
        """Test price position label for extreme low (<=10)."""
        market = Market(ticker="EXTLOW", title="Extreme Low", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)
        self.db.add_snapshot(Snapshot(
            ticker="EXTLOW",
            timestamp=now.isoformat(),
            mid_price=5.0,
        ))

        result = self.metrics.calculate_metrics("EXTLOW")

        self.assertEqual(result["latest_mid"], 5.0)
        self.assertEqual(result["distance_from_extreme"], 5.0)
        self.assertEqual(result["position_label"], "extreme_low")

    def test_price_position_extreme_high(self):
        """Test price position label for extreme high (>90)."""
        market = Market(ticker="EXTHIGH", title="Extreme High", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)
        self.db.add_snapshot(Snapshot(
            ticker="EXTHIGH",
            timestamp=now.isoformat(),
            mid_price=95.0,
        ))

        result = self.metrics.calculate_metrics("EXTHIGH")

        self.assertEqual(result["latest_mid"], 95.0)
        self.assertEqual(result["distance_from_extreme"], 5.0)
        self.assertEqual(result["position_label"], "extreme_high")

    def test_price_position_mid(self):
        """Test price position label for mid range (30-70)."""
        market = Market(ticker="MID", title="Mid Range", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)
        self.db.add_snapshot(Snapshot(
            ticker="MID",
            timestamp=now.isoformat(),
            mid_price=50.0,
        ))

        result = self.metrics.calculate_metrics("MID")

        self.assertEqual(result["latest_mid"], 50.0)
        self.assertEqual(result["distance_from_extreme"], 50.0)
        self.assertEqual(result["position_label"], "mid")

    def test_price_position_low(self):
        """Test price position label for low range (10-30)."""
        market = Market(ticker="LOW", title="Low Range", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)
        self.db.add_snapshot(Snapshot(
            ticker="LOW",
            timestamp=now.isoformat(),
            mid_price=20.0,
        ))

        result = self.metrics.calculate_metrics("LOW")

        self.assertEqual(result["latest_mid"], 20.0)
        self.assertEqual(result["distance_from_extreme"], 20.0)
        self.assertEqual(result["position_label"], "low")

    def test_price_position_high(self):
        """Test price position label for high range (70-90)."""
        market = Market(ticker="HIGH", title="High Range", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)
        self.db.add_snapshot(Snapshot(
            ticker="HIGH",
            timestamp=now.isoformat(),
            mid_price=80.0,
        ))

        result = self.metrics.calculate_metrics("HIGH")

        self.assertEqual(result["latest_mid"], 80.0)
        self.assertEqual(result["distance_from_extreme"], 20.0)
        self.assertEqual(result["position_label"], "high")

    def test_price_position_no_data(self):
        """Test price position returns None when no data."""
        result = self.metrics.calculate_metrics("NONEXISTENT")

        self.assertIsNone(result["latest_mid"])
        self.assertIsNone(result["distance_from_extreme"])
        self.assertIsNone(result["position_label"])

    def test_price_position_uses_latest(self):
        """Test price position uses the most recent snapshot."""
        market = Market(ticker="LATEST", title="Latest Test", status="open")
        self.db.upsert_market(market)

        now = datetime.now(timezone.utc)

        # Add older snapshot with low price
        self.db.add_snapshot(Snapshot(
            ticker="LATEST",
            timestamp=(now - timedelta(hours=1)).isoformat(),
            mid_price=10.0,
        ))

        # Add newer snapshot with mid price
        self.db.add_snapshot(Snapshot(
            ticker="LATEST",
            timestamp=now.isoformat(),
            mid_price=50.0,
        ))

        result = self.metrics.calculate_metrics("LATEST")

        # Should use the latest price (50), not the older one (10)
        self.assertEqual(result["latest_mid"], 50.0)
        self.assertEqual(result["position_label"], "mid")


class TestCorrelationMatch(unittest.TestCase):
    """Tests for CorrelationMatch dataclass."""

    def test_correlation_match_creation(self):
        """Test CorrelationMatch creation."""
        match = CorrelationMatch(
            category="test_category",
            markets=["MKT-1", "MKT-2"],
            keywords_matched={"keyword1", "keyword2"},
        )

        self.assertEqual(match.category, "test_category")
        self.assertEqual(len(match.markets), 2)
        self.assertEqual(len(match.keywords_matched), 2)

    def test_correlation_match_defaults(self):
        """Test CorrelationMatch default values."""
        match = CorrelationMatch(category="test")

        self.assertEqual(match.markets, [])
        self.assertEqual(match.keywords_matched, set())


if __name__ == "__main__":
    unittest.main()
