"""Tests for strategy labeling functionality."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from src.core.config import Config, set_config
from src.core.database import MarketDatabase, create_database
from src.core.models import Market, Snapshot

from src.analysis.strategy import (
    StrategyLabeler,
    StrategyLabel,
    TradingStrategy,
)


class TestTradingStrategy(unittest.TestCase):
    """Tests for TradingStrategy enum."""

    def test_all_strategy_values(self):
        """Test all strategy enum values exist."""
        self.assertEqual(TradingStrategy.MARKET_MAKING.value, "market_making")
        self.assertEqual(TradingStrategy.SPREAD_TRADING.value, "spread_trading")
        self.assertEqual(TradingStrategy.MOMENTUM.value, "momentum")
        self.assertEqual(TradingStrategy.SCALPING.value, "scalping")
        self.assertEqual(TradingStrategy.ARBITRAGE.value, "arbitrage")
        self.assertEqual(TradingStrategy.EVENT_TRADING.value, "event_trading")

    def test_strategy_count(self):
        """Test there are exactly 6 strategies."""
        self.assertEqual(len(TradingStrategy), 6)


class TestStrategyLabel(unittest.TestCase):
    """Tests for StrategyLabel dataclass."""

    def test_strategy_label_creation(self):
        """Test StrategyLabel creation."""
        label = StrategyLabel(
            strategy=TradingStrategy.MARKET_MAKING,
            suitability_score=7.5,
            reasons=["Wide spread", "Stable spreads"],
        )

        self.assertEqual(label.strategy, TradingStrategy.MARKET_MAKING)
        self.assertEqual(label.suitability_score, 7.5)
        self.assertEqual(len(label.reasons), 2)

    def test_strategy_label_defaults(self):
        """Test StrategyLabel default values."""
        label = StrategyLabel(
            strategy=TradingStrategy.SCALPING,
            suitability_score=5.0,
        )

        self.assertEqual(label.reasons, [])


class TestStrategyLabeler(unittest.TestCase):
    """Tests for StrategyLabeler functionality."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_empty_metrics_returns_empty_labels(self):
        """Test that empty metrics return no labels."""
        metrics = {
            "ticker": "TEST",
            "avg_spread_pct": None,
            "spread_volatility": None,
            "avg_volume": None,
            "avg_depth": None,
            "price_volatility": None,
            "volume_trend": None,
            "price_range": (None, None),
        }

        labels = self.labeler.label_market("TEST", metrics)
        self.assertEqual(len(labels), 0)


class TestMarketMakingStrategy(unittest.TestCase):
    """Tests for market making strategy evaluation."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_optimal_market_making_conditions(self):
        """Test market making with optimal metrics (max score)."""
        metrics = {
            "ticker": "MM-OPTIMAL",
            "avg_spread_pct": 6.0,        # Wide spread: +3
            "spread_volatility": 1.0,     # Stable: +3
            "avg_volume": 3000,           # Good: +2
            "avg_depth": 60,              # Good: +2
        }

        label = self.labeler._evaluate_market_making(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.strategy, TradingStrategy.MARKET_MAKING)
        self.assertEqual(label.suitability_score, 10)  # Max score

    def test_moderate_market_making_conditions(self):
        """Test market making with moderate metrics."""
        metrics = {
            "ticker": "MM-MODERATE",
            "avg_spread_pct": 4.0,        # Moderate spread: +2
            "spread_volatility": 2.0,     # Moderately stable: +2
            "avg_volume": 1500,           # Moderate: +1
            "avg_depth": 30,              # Moderate: +1
        }

        label = self.labeler._evaluate_market_making(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.suitability_score, 6)

    def test_poor_market_making_conditions(self):
        """Test market making with poor metrics."""
        metrics = {
            "ticker": "MM-POOR",
            "avg_spread_pct": 1.0,        # Narrow spread: +0
            "spread_volatility": 5.0,     # Unstable: +0
            "avg_volume": 500,            # Low: +0
            "avg_depth": 10,              # Low: +0
        }

        label = self.labeler._evaluate_market_making(metrics)

        self.assertIsNone(label)


class TestSpreadTradingStrategy(unittest.TestCase):
    """Tests for spread trading strategy evaluation."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_optimal_spread_trading_conditions(self):
        """Test spread trading with optimal metrics (max score)."""
        metrics = {
            "ticker": "ST-OPTIMAL",
            "spread_volatility": 5.0,     # High volatility: +4
            "avg_volume": 3000,           # Good: +3
            "avg_spread_pct": 3.0,        # Tradeable: +3
        }

        label = self.labeler._evaluate_spread_trading(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.strategy, TradingStrategy.SPREAD_TRADING)
        self.assertEqual(label.suitability_score, 10)

    def test_moderate_spread_trading_conditions(self):
        """Test spread trading with moderate volatility."""
        metrics = {
            "ticker": "ST-MODERATE",
            "spread_volatility": 3.0,     # Moderate: +2.5
            "avg_volume": 1500,           # Moderate: +2
            "avg_spread_pct": 1.5,        # Narrow: +2
        }

        label = self.labeler._evaluate_spread_trading(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.suitability_score, 6.5)


class TestMomentumStrategy(unittest.TestCase):
    """Tests for momentum strategy evaluation."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_optimal_momentum_conditions(self):
        """Test momentum with optimal metrics."""
        metrics = {
            "ticker": "MOM-OPTIMAL",
            "price_volatility": 10.0,     # High: +4
            "volume_trend": 100,          # Rising: +3
            "avg_depth": 30,              # Thin: +2
        }

        label = self.labeler._evaluate_momentum(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.strategy, TradingStrategy.MOMENTUM)
        self.assertEqual(label.suitability_score, 9)

    def test_moderate_momentum_conditions(self):
        """Test momentum with moderate price volatility."""
        metrics = {
            "ticker": "MOM-MODERATE",
            "price_volatility": 6.0,      # Moderate: +2.5
            "volume_trend": 30,           # Slight uptick: +1.5
            "avg_depth": 75,              # Moderate: +1
        }

        label = self.labeler._evaluate_momentum(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.suitability_score, 5)


class TestScalpingStrategy(unittest.TestCase):
    """Tests for scalping strategy evaluation."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_optimal_scalping_conditions(self):
        """Test scalping with optimal metrics (max score)."""
        metrics = {
            "ticker": "SCALP-OPTIMAL",
            "avg_spread_pct": 1.0,        # Tight: +3
            "avg_volume": 6000,           # High: +3
            "price_volatility": 2.0,      # Stable: +2
            "avg_depth": 150,             # Deep: +2
        }

        label = self.labeler._evaluate_scalping(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.strategy, TradingStrategy.SCALPING)
        self.assertEqual(label.suitability_score, 10)

    def test_moderate_scalping_conditions(self):
        """Test scalping with moderate conditions."""
        metrics = {
            "ticker": "SCALP-MODERATE",
            "avg_spread_pct": 2.5,        # Acceptable: +2
            "avg_volume": 4000,           # Good: +2
            "price_volatility": 5.0,      # Moderately stable: +1
            "avg_depth": 75,              # Moderate: +1
        }

        label = self.labeler._evaluate_scalping(metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.suitability_score, 6)


class TestArbitrageStrategy(unittest.TestCase):
    """Tests for arbitrage strategy evaluation."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_no_correlated_markets(self):
        """Test arbitrage returns None when no correlated markets."""
        market = Market(ticker="SOLO", title="Standalone market")
        self.db.upsert_market(market)

        metrics = {
            "ticker": "SOLO",
            "avg_volume": 3000,
            "avg_spread_pct": 2.0,
        }

        label = self.labeler._evaluate_arbitrage("SOLO", metrics)
        self.assertIsNone(label)

    def test_with_correlated_markets(self):
        """Test arbitrage with correlated markets."""
        # Add correlated markets (both contain 'bitcoin')
        markets = [
            Market(ticker="BTC-1", title="Will Bitcoin hit 100k?"),
            Market(ticker="BTC-2", title="Bitcoin price prediction"),
        ]
        for m in markets:
            self.db.upsert_market(m)

        metrics = {
            "ticker": "BTC-1",
            "avg_volume": 3000,
            "avg_spread_pct": 2.0,
        }

        label = self.labeler._evaluate_arbitrage("BTC-1", metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.strategy, TradingStrategy.ARBITRAGE)
        self.assertGreater(label.suitability_score, 0)


class TestEventTradingStrategy(unittest.TestCase):
    """Tests for event trading strategy evaluation."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_market_closing_soon(self):
        """Test event trading for market closing in 2 days."""
        close_time = datetime.now(timezone.utc) + timedelta(days=2)
        market = Market(
            ticker="EVENT-SOON",
            title="Event happening soon",
            close_time=close_time.isoformat(),
        )
        self.db.upsert_market(market)

        metrics = {
            "ticker": "EVENT-SOON",
            "volume_trend": 150,          # Strong surge: +3
            "price_range": (5.0, 10.0),   # Near extreme: +2
            "avg_volume": 2000,           # Active: +1
        }

        label = self.labeler._evaluate_event_trading("EVENT-SOON", metrics)

        self.assertIsNotNone(label)
        self.assertEqual(label.strategy, TradingStrategy.EVENT_TRADING)
        # 4 (close soon) + 3 (volume surge) + 2 (extreme price) + 1 (active)
        self.assertEqual(label.suitability_score, 10)

    def test_market_closing_in_week(self):
        """Test event trading for market closing in 5 days."""
        close_time = datetime.now(timezone.utc) + timedelta(days=5)
        market = Market(
            ticker="EVENT-WEEK",
            title="Event in a week",
            close_time=close_time.isoformat(),
        )
        self.db.upsert_market(market)

        metrics = {
            "ticker": "EVENT-WEEK",
            "volume_trend": 75,           # Rising: +2
            "price_range": (75.0, 85.0),  # Trending to extreme: +1
            "avg_volume": 1500,           # Active: +1
        }

        label = self.labeler._evaluate_event_trading("EVENT-WEEK", metrics)

        self.assertIsNotNone(label)
        # 2 (closes in 5 days) + 2 (rising volume) + 1 (trending) + 1 (active)
        self.assertEqual(label.suitability_score, 6)

    def test_market_no_close_time(self):
        """Test event trading when market has no close_time."""
        market = Market(ticker="NO-CLOSE", title="No close time set")
        self.db.upsert_market(market)

        metrics = {
            "ticker": "NO-CLOSE",
            "volume_trend": 150,
            "price_range": (5.0, 10.0),
            "avg_volume": 2000,
        }

        label = self.labeler._evaluate_event_trading("NO-CLOSE", metrics)

        # Should still return label based on other factors
        self.assertIsNotNone(label)
        # 0 (no close time) + 3 (volume surge) + 2 (extreme) + 1 (active)
        self.assertEqual(label.suitability_score, 6)


class TestMultiStrategyLabeling(unittest.TestCase):
    """Tests for multi-strategy labeling."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_market_suitable_for_multiple_strategies(self):
        """Test that a market can be labeled with multiple strategies."""
        metrics = {
            "ticker": "MULTI",
            "avg_spread_pct": 5.0,        # Good for market making
            "spread_volatility": 4.0,     # Good for spread trading
            "avg_volume": 6000,           # Good for scalping
            "avg_depth": 150,
            "price_volatility": 8.0,      # Good for momentum
            "volume_trend": 100,
        }

        labels = self.labeler.label_market("MULTI", metrics)

        # Should have multiple strategies
        self.assertGreater(len(labels), 1)

        # Labels should be sorted by suitability score descending
        for i in range(len(labels) - 1):
            self.assertGreaterEqual(
                labels[i].suitability_score,
                labels[i + 1].suitability_score
            )

    def test_get_best_strategy(self):
        """Test get_best_strategy returns highest scoring strategy."""
        metrics = {
            "ticker": "BEST",
            "avg_spread_pct": 6.0,        # Optimal for market making
            "spread_volatility": 1.0,     # Optimal for market making
            "avg_volume": 3000,
            "avg_depth": 60,
        }

        best = self.labeler.get_best_strategy("BEST", metrics)

        self.assertIsNotNone(best)
        self.assertEqual(best.strategy, TradingStrategy.MARKET_MAKING)
        self.assertEqual(best.suitability_score, 10)

    def test_evaluate_all_strategies(self):
        """Test evaluate_all_strategies returns dict of all strategies."""
        metrics = {
            "ticker": "ALL",
            "avg_spread_pct": 4.0,
            "spread_volatility": 3.0,
            "avg_volume": 3000,
            "avg_depth": 75,
            "price_volatility": 5.0,
            "volume_trend": 25,
        }

        result = self.labeler.evaluate_all_strategies("ALL", metrics)

        self.assertIsInstance(result, dict)
        # All returned strategies should be valid
        for strategy_name in result.keys():
            self.assertIn(
                strategy_name,
                [s.value for s in TradingStrategy]
            )

    def test_label_market_as_tuples(self):
        """Test label_market_as_tuples returns correct format."""
        metrics = {
            "ticker": "TUPLES",
            "avg_spread_pct": 5.0,
            "spread_volatility": 1.0,
            "avg_volume": 3000,
            "avg_depth": 60,
        }

        tuples = self.labeler.label_market_as_tuples("TUPLES", metrics)

        self.assertIsInstance(tuples, list)
        for item in tuples:
            self.assertEqual(len(item), 3)
            self.assertIsInstance(item[0], str)    # strategy name
            self.assertIsInstance(item[1], float)  # score
            self.assertIsInstance(item[2], str)    # reasons


class TestEdgeCases(unittest.TestCase):
    """Tests for edge cases and None values."""

    def setUp(self):
        """Set up test database and labeler."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        set_config(Config(db_path=self.db_path))
        self.db = create_database(self.db_path)
        self.labeler = StrategyLabeler(db=self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        os.rmdir(self.temp_dir)

    def test_all_none_values(self):
        """Test handling of all None metric values."""
        metrics = {
            "ticker": "ALL-NONE",
            "avg_spread_pct": None,
            "spread_volatility": None,
            "avg_volume": None,
            "avg_depth": None,
            "price_volatility": None,
            "volume_trend": None,
            "price_range": (None, None),
        }

        labels = self.labeler.label_market("ALL-NONE", metrics)
        self.assertEqual(len(labels), 0)

    def test_partial_none_values(self):
        """Test handling of partial None metric values."""
        metrics = {
            "ticker": "PARTIAL",
            "avg_spread_pct": 5.0,  # Only this is set
            "spread_volatility": None,
            "avg_volume": None,
            "avg_depth": None,
            "price_volatility": None,
            "volume_trend": None,
            "price_range": (None, None),
        }

        labels = self.labeler.label_market("PARTIAL", metrics)

        # Should still get some labels based on available data
        # Market making gets +3 for wide spread
        market_making = next(
            (l for l in labels if l.strategy == TradingStrategy.MARKET_MAKING),
            None
        )
        if market_making:
            self.assertEqual(market_making.suitability_score, 3)

    def test_zero_values(self):
        """Test handling of zero metric values."""
        metrics = {
            "ticker": "ZEROS",
            "avg_spread_pct": 0.0,
            "spread_volatility": 0.0,
            "avg_volume": 0.0,
            "avg_depth": 0.0,
            "price_volatility": 0.0,
            "volume_trend": 0.0,
            "price_range": (0.0, 0.0),
        }

        # Should not crash
        labels = self.labeler.label_market("ZEROS", metrics)
        self.assertIsInstance(labels, list)

    def test_negative_values(self):
        """Test handling of negative metric values."""
        metrics = {
            "ticker": "NEGATIVE",
            "avg_spread_pct": 5.0,
            "spread_volatility": 1.0,
            "avg_volume": 3000,
            "avg_depth": 50,
            "price_volatility": 5.0,
            "volume_trend": -50,  # Declining volume
            "price_range": (40.0, 60.0),
        }

        # Should handle negative volume_trend
        labels = self.labeler.label_market("NEGATIVE", metrics)
        self.assertIsInstance(labels, list)

        # Momentum strategy should not score for declining volume
        momentum = next(
            (l for l in labels if l.strategy == TradingStrategy.MOMENTUM),
            None
        )
        if momentum:
            # Should not have volume trend score
            self.assertNotIn("Rising volume", str(momentum.reasons))

    def test_market_not_in_database(self):
        """Test handling when market is not in database."""
        metrics = {
            "ticker": "NOT-IN-DB",
            "avg_spread_pct": 5.0,
            "spread_volatility": 1.0,
            "avg_volume": 3000,
            "avg_depth": 50,
        }

        # Should not crash, just return labels without arbitrage/event
        labels = self.labeler.label_market("NOT-IN-DB", metrics)
        self.assertIsInstance(labels, list)


if __name__ == "__main__":
    unittest.main()
