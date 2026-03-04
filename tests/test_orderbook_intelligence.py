"""Tests for OrderbookIntelligence.

1. Unit tests: feature extraction matches existing DepthMetrics/OrderbookAnalyzer
2. Temporal tests: EMA convergence, velocity, staleness
3. Integration tests: adverse selection, timing, confidence, edge capture wiring
"""

from unittest.mock import patch

import pytest

from src.core.orderbook_manager import OrderBookLevel, OrderBookState
from src.core.utils import utc_now
from signal_extraction.models.orderbook_intelligence import (
    OrderbookIntelConfig,
    OrderbookIntelligence,
)
from strategies.depth_strategy_base import DepthMetrics


# =============================================================================
# Helpers
# =============================================================================


def make_book(
    ticker: str = "TEST-T",
    bid_price: int = 45,
    bid_size: int = 10,
    ask_price: int = 55,
    ask_size: int = 10,
    extra_bids: list = None,
    extra_asks: list = None,
    volume_24h: int = 500,
) -> OrderBookState:
    """Create an OrderBookState for testing."""
    bids = [OrderBookLevel(price=bid_price, size=bid_size)]
    if extra_bids:
        for p, s in extra_bids:
            bids.append(OrderBookLevel(price=p, size=s))
    bids.sort(key=lambda x: x.price, reverse=True)

    asks = [OrderBookLevel(price=ask_price, size=ask_size)]
    if extra_asks:
        for p, s in extra_asks:
            asks.append(OrderBookLevel(price=p, size=s))
    asks.sort(key=lambda x: x.price)

    return OrderBookState(
        ticker=ticker,
        bids=bids,
        asks=asks,
        sequence=1,
        timestamp=utc_now(),
        volume_24h=volume_24h,
    )


def make_intel(config: OrderbookIntelConfig = None) -> OrderbookIntelligence:
    return OrderbookIntelligence(config=config)


# =============================================================================
# Phase 1: Feature Extraction
# =============================================================================


class TestFeatureExtraction:
    """Test that _extract_features matches existing implementations."""

    def test_bbo_basics(self):
        """BBO, spread, mid price."""
        book = make_book(bid_price=40, ask_price=50)
        intel = make_intel()
        signals = intel.update("T", book)
        f = signals.features

        assert f.best_bid == 40
        assert f.best_ask == 50
        assert f.spread_cents == 10
        assert f.mid_price == 45.0

    def test_matches_depth_metrics(self):
        """Features should agree with DepthMetrics.from_orderbook."""
        book = make_book(
            bid_price=55,
            bid_size=20,
            ask_price=60,
            ask_size=15,
            extra_bids=[(52, 30), (49, 10)],
            extra_asks=[(63, 25), (67, 5)],
        )
        intel = make_intel()
        signals = intel.update("T", book)
        f = signals.features

        dm = DepthMetrics.from_orderbook(book)

        assert f.best_bid == dm.best_bid
        assert f.best_ask == dm.best_ask
        assert f.spread_cents == dm.spread_cents
        assert f.bid_depth_at_best == dm.bid_depth_at_best
        assert f.ask_depth_at_best == dm.ask_depth_at_best
        assert f.total_bid_depth == dm.total_bid_depth
        assert f.total_ask_depth == dm.total_ask_depth

    def test_empty_book(self):
        """Empty book returns safe defaults."""
        book = OrderBookState(ticker="EMPTY", bids=[], asks=[])
        intel = make_intel()
        signals = intel.update("EMPTY", book)
        f = signals.features

        assert f.best_bid == 0
        assert f.best_ask == 0
        assert f.spread_cents == 0
        assert f.mid_price == 0.0
        assert f.microprice == 0.0
        assert f.book_is_thin is True

    def test_one_sided_book(self):
        """Book with only bids or only asks."""
        bid_only = OrderBookState(
            ticker="BID",
            bids=[OrderBookLevel(price=50, size=10)],
            asks=[],
        )
        intel = make_intel()
        signals = intel.update("BID", bid_only)
        assert signals.features.best_bid == 0  # requires both sides
        assert signals.features.best_ask == 0

    def test_top_of_book_imbalance(self):
        """BBO-only imbalance: (bid_size - ask_size) / total."""
        book = make_book(bid_size=30, ask_size=10)
        intel = make_intel()
        signals = intel.update("T", book)
        # (30 - 10) / (30 + 10) = 0.5
        assert signals.features.top_of_book_imbalance == pytest.approx(0.5, abs=1e-6)

    def test_top_of_book_imbalance_negative(self):
        """Sell pressure."""
        book = make_book(bid_size=5, ask_size=15)
        intel = make_intel()
        signals = intel.update("T", book)
        # (5 - 15) / 20 = -0.5
        assert signals.features.top_of_book_imbalance == pytest.approx(-0.5, abs=1e-6)

    def test_depth_imbalance_distance_weighted(self):
        """Distance-weighted imbalance weights closer levels more."""
        # Bid at 50 (close to mid=52.5) with lots of size, ask at 55 with less
        # Extra bid far away at 40 — should contribute less
        book = make_book(
            bid_price=50,
            bid_size=10,
            ask_price=55,
            ask_size=10,
            extra_bids=[(40, 100)],  # far away, lots of size
            extra_asks=[(65, 100)],  # far away, lots of size
        )
        intel = make_intel()
        signals = intel.update("T", book)

        # With distance weighting, the far-away levels contribute less
        # than their raw size suggests — imbalance should be near 0
        # (both sides have symmetric far-away depth)
        assert abs(signals.features.depth_imbalance) < 0.3

    def test_microprice(self):
        """Microprice = size-weighted mid from BBO quantities."""
        book = make_book(bid_price=40, bid_size=20, ask_price=60, ask_size=10)
        intel = make_intel()
        signals = intel.update("T", book)

        # microprice = (40*10 + 60*20) / (20+10) = (400 + 1200) / 30 = 53.33
        expected = (40 * 10 + 60 * 20) / 30
        assert signals.features.microprice == pytest.approx(expected, abs=0.01)

    def test_microprice_equal_size(self):
        """Equal BBO sizes -> microprice == mid."""
        book = make_book(bid_price=40, bid_size=10, ask_price=60, ask_size=10)
        intel = make_intel()
        signals = intel.update("T", book)
        assert signals.features.microprice == pytest.approx(50.0, abs=0.01)

    def test_liquidity_score(self):
        """Liquidity = total_depth / (spread * 10)."""
        book = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        intel = make_intel()
        signals = intel.update("T", book)

        expected = (10 + 10) / (10 * 10.0)
        assert signals.features.liquidity_score == pytest.approx(expected, abs=0.001)

    def test_thin_book_detection(self):
        """Thin book when few levels or low depth."""
        # 1 bid + 1 ask level, small sizes
        book = make_book(bid_size=2, ask_size=2)
        intel = make_intel()
        signals = intel.update("T", book)
        # total_depth=4 < thin_book_min_depth=5 (default)
        assert signals.features.book_is_thin is True

    def test_thick_book(self):
        """Sufficient depth and levels -> not thin."""
        book = make_book(
            bid_size=10,
            ask_size=10,
            extra_bids=[(43, 10)],
            extra_asks=[(57, 10)],
        )
        intel = make_intel()
        signals = intel.update("T", book)
        # 4 levels, 40 total depth
        assert signals.features.book_is_thin is False

    def test_vwap_buy(self):
        """VWAP to buy 5 contracts through the ask side."""
        book = make_book(
            bid_price=45,
            bid_size=10,
            ask_price=50,
            ask_size=3,
            extra_asks=[(52, 10)],
        )
        intel = make_intel()
        signals = intel.update("T", book)

        # Buy 5: fill 3 @ 50, then 2 @ 52 = (150 + 104) / 5 = 50.8
        expected = (3 * 50 + 2 * 52) / 5
        assert signals.features.vwap_buy[5] == pytest.approx(expected, abs=0.01)

    def test_vwap_insufficient_liquidity(self):
        """VWAP returns None when not enough depth."""
        book = make_book(bid_size=2, ask_size=2)
        intel = make_intel()
        signals = intel.update("T", book)

        # Only 2 contracts available, VWAP for 5 should be None
        assert signals.features.vwap_buy[5] is None

    def test_vwap_sell(self):
        """VWAP to sell through the bid side."""
        book = make_book(
            bid_price=50,
            bid_size=3,
            ask_price=55,
            ask_size=10,
            extra_bids=[(48, 10)],
        )
        intel = make_intel()
        signals = intel.update("T", book)

        # Sell 5: fill 3 @ 50, then 2 @ 48 = (150 + 96) / 5 = 49.2
        expected = (3 * 50 + 2 * 48) / 5
        assert signals.features.vwap_sell[5] == pytest.approx(expected, abs=0.01)


# =============================================================================
# Phase 2: Temporal Signals
# =============================================================================


class TestTemporalSignals:
    """Test EMA, velocity, trend, staleness."""

    def _feed_sequence(self, intel, ticker, books, timestamps):
        """Feed a sequence of books at specified timestamps."""
        signals = None
        for book, ts in zip(books, timestamps):
            with patch("time.time", return_value=ts):
                signals = intel.update(ticker, book)
        return signals

    def test_ema_converges(self):
        """Imbalance EMA converges toward recent values."""
        intel = make_intel()

        # Feed 20 snapshots with increasing buy pressure
        for i in range(20):
            bid_size = 10 + i * 2
            book = make_book(bid_size=bid_size, ask_size=10)
            with patch("time.time", return_value=100.0 + i):
                signals = intel.update("T", book)

        # After 20 snapshots of increasing imbalance, EMA should be positive
        assert signals.imbalance_ema > 0.3

    def test_ema_alpha_sensitivity(self):
        """Higher alpha = faster tracking."""
        fast = make_intel(OrderbookIntelConfig(imbalance_ema_alpha=0.8))
        slow = make_intel(OrderbookIntelConfig(imbalance_ema_alpha=0.1))

        # Feed 5 neutral snapshots then a sudden imbalance shift
        for i in range(5):
            book = make_book(bid_size=10, ask_size=10)
            ts = 100.0 + i
            with patch("time.time", return_value=ts):
                fast.update("T", book)
                slow.update("T", book)

        # Now sudden buy pressure
        book = make_book(bid_size=50, ask_size=5)
        with patch("time.time", return_value=106.0):
            fast_signals = fast.update("T", book)
            slow_signals = slow.update("T", book)

        # Fast should track the jump more closely
        assert abs(fast_signals.imbalance_ema) > abs(slow_signals.imbalance_ema)

    def test_microprice_velocity(self):
        """Velocity measures rate of microprice change."""
        intel = make_intel()

        # First snapshot: microprice near 50
        book1 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Second snapshot 5s later: microprice near 60
        book2 = make_book(bid_price=55, bid_size=10, ask_price=65, ask_size=10)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        # Microprice went from 50 to 60 in 5s -> ~2 c/s
        assert signals.microprice_velocity > 0
        assert signals.microprice_velocity == pytest.approx(2.0, abs=0.5)

    def test_microprice_velocity_negative(self):
        """Falling microprice -> negative velocity."""
        intel = make_intel()

        book1 = make_book(bid_price=55, bid_size=10, ask_price=65, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        book2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        assert signals.microprice_velocity < 0

    def test_spread_widening_detection(self):
        """Detect when spread is widening vs its EMA."""
        intel = make_intel()

        # Feed tight spreads to establish baseline EMA
        for i in range(10):
            book = make_book(bid_price=47, ask_price=53)  # 6c spread
            with patch("time.time", return_value=100.0 + i):
                intel.update("T", book)

        # Now a wider spread
        book = make_book(bid_price=44, ask_price=56)  # 12c spread
        with patch("time.time", return_value=111.0):
            signals = intel.update("T", book)

        assert signals.spread_is_widening is True

    def test_imbalance_trend_positive(self):
        """Increasing imbalance -> positive trend."""
        intel = make_intel()

        for i in range(10):
            bid_size = 10 + i * 3
            book = make_book(bid_size=bid_size, ask_size=10)
            with patch("time.time", return_value=100.0 + i * 5):
                signals = intel.update("T", book)

        assert signals.imbalance_trend > 0

    def test_staleness(self):
        """Signals become stale after threshold."""
        config = OrderbookIntelConfig(stale_threshold_seconds=5.0)
        intel = make_intel(config)

        book = make_book()
        with patch("time.time", return_value=100.0):
            intel.update("T", book)

        # Second update 10s later
        with patch("time.time", return_value=110.0):
            signals = intel.update("T", book)

        assert signals.staleness_seconds == pytest.approx(10.0, abs=0.1)
        assert signals.is_stale is True

    def test_not_stale_when_fresh(self):
        """Signals are fresh when updated recently."""
        intel = make_intel()
        book = make_book()

        with patch("time.time", return_value=100.0):
            intel.update("T", book)

        with patch("time.time", return_value=101.0):
            signals = intel.update("T", book)

        assert signals.is_stale is False

    def test_snapshot_count_increments(self):
        """Snapshot count increases with each update."""
        intel = make_intel()
        book = make_book()

        for i in range(5):
            with patch("time.time", return_value=100.0 + i):
                signals = intel.update("T", book)

        assert signals.snapshot_count == 5


# =============================================================================
# Phase 3: Derived Signals
# =============================================================================


class TestDerivedSignals:
    """Test toxicity, adverse selection, timing, confidence, directional pressure."""

    def test_toxicity_increases_with_imbalance(self):
        """Higher absolute imbalance -> higher toxicity."""
        intel_balanced = make_intel()
        intel_skewed = make_intel()

        book_balanced = make_book(bid_size=10, ask_size=10)
        book_skewed = make_book(bid_size=50, ask_size=2)

        with patch("time.time", return_value=100.0):
            sig_bal = intel_balanced.update("T", book_balanced)
            sig_skew = intel_skewed.update("T", book_skewed)

        assert sig_skew.toxicity_score > sig_bal.toxicity_score

    def test_toxicity_range(self):
        """Toxicity is always [0, 1]."""
        intel = make_intel()

        # Extreme case
        book = make_book(bid_size=99, ask_size=1)
        with patch("time.time", return_value=100.0):
            signals = intel.update("T", book)

        assert 0.0 <= signals.toxicity_score <= 1.0

    def test_toxicity_spread_widening_component(self):
        """Spread widening contributes to toxicity."""
        intel = make_intel()

        # Establish baseline with tight spread
        for i in range(10):
            book = make_book(bid_price=47, ask_price=53)
            with patch("time.time", return_value=100.0 + i):
                intel.update("T", book)

        # Sudden widening
        book_wide = make_book(bid_price=40, ask_price=60)
        with patch("time.time", return_value=111.0):
            signals_wide = intel.update("T", book_wide)

        # Compare to fresh intel with only tight spread
        intel_tight = make_intel()
        book_tight = make_book(bid_price=47, ask_price=53)
        with patch("time.time", return_value=100.0):
            signals_tight = intel_tight.update("T2", book_tight)

        assert signals_wide.toxicity_score > signals_tight.toxicity_score

    def test_adverse_selection_low_toxicity(self):
        """Low toxicity -> near minimum adjustment (1.5c)."""
        config = OrderbookIntelConfig(
            toxicity_min_adjustment_cents=1.5,
            toxicity_max_adjustment_cents=5.0,
        )
        intel = make_intel(config)

        # Balanced book -> low toxicity
        book = make_book(bid_size=10, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("T", book)

        adj = intel.get_adverse_selection_adjustment("T", "buy_yes")
        assert adj < 2.5  # closer to 1.5 than 5.0

    def test_adverse_selection_high_toxicity(self):
        """High toxicity -> near maximum adjustment."""
        intel = make_intel()

        # Create high toxicity: extreme imbalance + rapid microprice change
        book1 = make_book(bid_price=45, bid_size=50, ask_price=55, ask_size=2)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        book2 = make_book(bid_price=55, bid_size=50, ask_price=65, ask_size=2)
        with patch("time.time", return_value=101.0):
            intel.update("T", book2)

        adj = intel.get_adverse_selection_adjustment("T", "buy_yes")
        assert adj > 2.5  # closer to max

    def test_adverse_selection_no_signals_fallback(self):
        """No signals -> mid-range fallback."""
        config = OrderbookIntelConfig(
            toxicity_min_adjustment_cents=1.5,
            toxicity_max_adjustment_cents=5.0,
        )
        intel = make_intel(config)
        adj = intel.get_adverse_selection_adjustment("UNKNOWN", "buy_yes")
        assert adj == pytest.approx(3.25, abs=0.01)  # (1.5 + 5.0) / 2

    def test_timing_score_aligned(self):
        """Buying into buy pressure should give high timing score."""
        intel = make_intel()

        # Strong buy imbalance
        for i in range(10):
            book = make_book(bid_size=50, ask_size=5)
            with patch("time.time", return_value=100.0 + i):
                intel.update("T", book)

        score_buy = intel.get_entry_timing_score("T", "buy_yes")
        score_sell = intel.get_entry_timing_score("T", "buy_no")

        # Buying into buy pressure should score higher than selling into it
        assert score_buy > score_sell

    def test_timing_score_range(self):
        """Timing score always [0, 1]."""
        intel = make_intel()
        book = make_book(bid_size=99, ask_size=1)
        with patch("time.time", return_value=100.0):
            intel.update("T", book)

        for direction in ["buy_yes", "buy_no"]:
            score = intel.get_entry_timing_score("T", direction)
            assert 0.0 <= score <= 1.0

    def test_timing_score_no_signals(self):
        """No signals -> neutral 0.5."""
        intel = make_intel()
        score = intel.get_entry_timing_score("UNKNOWN", "buy_yes")
        assert score == 0.5

    def test_timing_spread_tightness(self):
        """Tighter spread should give better timing score."""
        intel_tight = make_intel()
        intel_wide = make_intel()

        # Same balanced book, different spreads
        book_tight = make_book(bid_price=48, ask_price=52, bid_size=10, ask_size=10)
        book_wide = make_book(bid_price=35, ask_price=65, bid_size=10, ask_size=10)

        with patch("time.time", return_value=100.0):
            intel_tight.update("T", book_tight)
            intel_wide.update("W", book_wide)

        score_tight = intel_tight.get_entry_timing_score("T", "buy_yes")
        score_wide = intel_wide.get_entry_timing_score("W", "buy_yes")

        assert score_tight > score_wide

    def test_confidence_ramps_with_snapshots(self):
        """Confidence increases as more snapshots are collected."""
        intel = make_intel()
        book = make_book(
            bid_size=10,
            ask_size=10,
            extra_bids=[(43, 10)],
            extra_asks=[(57, 10)],
        )

        confidences = []
        for i in range(25):
            with patch("time.time", return_value=100.0 + i):
                signals = intel.update("T", book)
                confidences.append(signals.signal_confidence)

        # Confidence should increase over time
        assert confidences[-1] > confidences[0]
        # After 20+ snapshots (min_snapshots_full_confidence default), should be near 1.0
        assert confidences[-1] > 0.8

    def test_confidence_degrades_thin_book(self):
        """Thin book degrades confidence."""
        intel = make_intel()

        # Thin book (2 contracts each side)
        thin_book = make_book(bid_size=2, ask_size=2)
        for i in range(25):
            with patch("time.time", return_value=100.0 + i):
                signals = intel.update("THIN", thin_book)

        # Thick book
        thick_book = make_book(
            bid_size=20,
            ask_size=20,
            extra_bids=[(43, 20)],
            extra_asks=[(57, 20)],
        )
        for i in range(25):
            with patch("time.time", return_value=100.0 + i):
                signals_thick = intel.update("THICK", thick_book)

        assert signals.signal_confidence < signals_thick.signal_confidence

    def test_directional_pressure_range(self):
        """Directional pressure always [-1, +1]."""
        intel = make_intel()

        # Extreme buy pressure
        book = make_book(bid_size=99, ask_size=1)
        with patch("time.time", return_value=100.0):
            signals = intel.update("T", book)

        assert -1.0 <= signals.directional_pressure <= 1.0

    def test_directional_pressure_buy_positive(self):
        """Buy pressure -> positive directional pressure."""
        intel = make_intel()
        for i in range(10):
            book = make_book(bid_size=50, ask_size=5)
            with patch("time.time", return_value=100.0 + i):
                signals = intel.update("T", book)

        assert signals.directional_pressure > 0

    def test_directional_pressure_sell_negative(self):
        """Sell pressure -> negative directional pressure."""
        intel = make_intel()
        for i in range(10):
            book = make_book(bid_size=5, ask_size=50)
            with patch("time.time", return_value=100.0 + i):
                signals = intel.update("T", book)

        assert signals.directional_pressure < 0

    def test_implied_probability(self):
        """Implied probability = microprice / 100."""
        intel = make_intel()
        book = make_book(bid_price=60, bid_size=10, ask_price=70, ask_size=10)
        with patch("time.time", return_value=100.0):
            signals = intel.update("T", book)

        assert signals.implied_probability == pytest.approx(0.65, abs=0.01)


# =============================================================================
# Phase 4: Multi-ticker Isolation
# =============================================================================


class TestMultiTicker:
    """Test that per-ticker state is independent."""

    def test_independent_tickers(self):
        """Updating one ticker doesn't affect another."""
        intel = make_intel()

        book_a = make_book(bid_price=40, bid_size=50, ask_price=60, ask_size=5)
        book_b = make_book(bid_price=40, bid_size=5, ask_price=60, ask_size=50)

        with patch("time.time", return_value=100.0):
            sig_a = intel.update("A", book_a)
            sig_b = intel.update("B", book_b)

        # A should have positive imbalance, B negative
        assert sig_a.imbalance_ema > 0
        assert sig_b.imbalance_ema < 0

    def test_get_signals_per_ticker(self):
        """get_signals returns correct per-ticker data."""
        intel = make_intel()

        with patch("time.time", return_value=100.0):
            intel.update("X", make_book(bid_price=30, ask_price=40))
            intel.update("Y", make_book(bid_price=60, ask_price=70))

        sig_x = intel.get_signals("X")
        sig_y = intel.get_signals("Y")

        assert sig_x.features.mid_price == 35.0
        assert sig_y.features.mid_price == 65.0

    def test_get_signals_unknown_ticker(self):
        """Unknown ticker returns None."""
        intel = make_intel()
        assert intel.get_signals("NOPE") is None


# =============================================================================
# Phase 5: Logging
# =============================================================================


class TestLogging:
    """Test get_snapshot_for_logging output."""

    def test_snapshot_keys(self):
        """Snapshot has expected keys."""
        intel = make_intel()
        book = make_book()
        with patch("time.time", return_value=100.0):
            intel.update("T", book)

        snap = intel.get_snapshot_for_logging("T")

        expected_keys = {
            "ticker",
            "has_signals",
            "mid_price",
            "spread",
            "microprice",
            "top_imbalance",
            "depth_imbalance",
            "imbalance_ema",
            "imbalance_trend",
            "spread_ema",
            "spread_widening",
            "microprice_vel",
            "directional_pressure",
            "implied_prob",
            "toxicity",
            "timing_confidence",
            "staleness_s",
            "is_stale",
            "snapshots",
            "book_thin",
            "liquidity",
            # Fill activity
            "last_bid_fill_price",
            "last_ask_fill_price",
            "secs_since_bid_fill",
            "secs_since_ask_fill",
            "bid_fill_rate",
            "ask_fill_rate",
            "bid_fill_volume",
            "ask_fill_volume",
            "spread_active",
        }
        assert set(snap.keys()) == expected_keys
        assert snap["has_signals"] is True

    def test_snapshot_unknown_ticker(self):
        """Unknown ticker snapshot indicates no signals."""
        intel = make_intel()
        snap = intel.get_snapshot_for_logging("NOPE")
        assert snap["has_signals"] is False

    def test_snapshot_values_are_serializable(self):
        """All values should be JSON-serializable types."""
        import json

        intel = make_intel()
        book = make_book()
        with patch("time.time", return_value=100.0):
            intel.update("T", book)

        snap = intel.get_snapshot_for_logging("T")
        # Should not raise
        serialized = json.dumps(snap)
        assert isinstance(serialized, str)


# =============================================================================
# Phase 6: Edge Capture Integration
# =============================================================================


class TestEdgeCaptureIntegration:
    """Test integration with EdgeCaptureStrategy."""

    def test_strategy_creates_intel_internally(self):
        """EdgeCaptureStrategy creates OrderbookIntelligence if not provided."""
        from strategies.edge_capture_strategy import (
            EdgeCaptureConfig,
            EdgeCaptureStrategy,
        )
        from unittest.mock import MagicMock

        provider = MagicMock()
        config = EdgeCaptureConfig()
        strategy = EdgeCaptureStrategy(
            config=config,
            provider=provider,
            dry_run=True,
        )
        assert hasattr(strategy, "_orderbook_intel")
        assert isinstance(strategy._orderbook_intel, OrderbookIntelligence)

    def test_strategy_accepts_injected_intel(self):
        """EdgeCaptureStrategy uses injected OrderbookIntelligence."""
        from strategies.edge_capture_strategy import (
            EdgeCaptureConfig,
            EdgeCaptureStrategy,
        )
        from unittest.mock import MagicMock

        provider = MagicMock()
        config = EdgeCaptureConfig()
        intel = OrderbookIntelligence()

        strategy = EdgeCaptureStrategy(
            config=config,
            provider=provider,
            dry_run=True,
            orderbook_intel=intel,
        )
        assert strategy._orderbook_intel is intel

    def test_on_book_updated_feeds_intel(self):
        """_on_book_updated feeds OrderbookIntelligence."""
        from strategies.edge_capture_strategy import (
            EdgeCaptureConfig,
            EdgeCaptureStrategy,
        )
        from unittest.mock import MagicMock

        provider = MagicMock()
        config = EdgeCaptureConfig()
        intel = OrderbookIntelligence()

        strategy = EdgeCaptureStrategy(
            config=config,
            provider=provider,
            dry_run=True,
            orderbook_intel=intel,
        )

        book = make_book(ticker="KXTEST")
        strategy._on_book_updated("KXTEST", book)

        signals = intel.get_signals("KXTEST")
        assert signals is not None
        assert signals.features.best_bid == 45
        assert signals.features.best_ask == 55

    def test_config_has_min_timing_score(self):
        """EdgeCaptureConfig has min_timing_score with sensible default."""
        from strategies.edge_capture_strategy import EdgeCaptureConfig

        config = EdgeCaptureConfig()
        assert hasattr(config, "min_timing_score")
        assert config.min_timing_score == 0.25

    def test_analyze_opportunity_uses_adaptive_adverse_selection(self):
        """analyze_opportunity uses intel for adverse selection, not flat."""
        from strategies.edge_capture_strategy import (
            EdgeCaptureConfig,
            EdgeCaptureStrategy,
            ProbabilityEstimate,
        )
        from unittest.mock import MagicMock

        config = EdgeCaptureConfig(
            min_edge_cents=1,
            min_confidence=0.0,
            min_timing_score=0.0,
            allowed_ticker_prefixes=None,
            min_volume_24h=0,
        )

        # Mock provider returns high fair value -> buy_yes edge
        estimate = ProbabilityEstimate(
            fair_value=0.80,
            confidence=0.9,
            source="test",
        )
        provider = MagicMock()
        provider.estimate.return_value = estimate

        intel = OrderbookIntelligence()

        strategy = EdgeCaptureStrategy(
            config=config,
            provider=provider,
            dry_run=True,
            orderbook_intel=intel,
        )

        # Feed the intel first so it has signals
        book = make_book(
            ticker="TEST-T",
            bid_price=55,
            bid_size=10,
            ask_price=60,
            ask_size=10,
            extra_bids=[(53, 15)],
            extra_asks=[(62, 15)],
            volume_24h=500,
        )
        intel.update("TEST-T", book)

        opp = strategy.analyze_opportunity("TEST-T", book)
        if opp is not None:
            # Verify reasons contain adaptive adverse selection info
            reasons_str = " ".join(opp.reasons)
            assert "adverse_adj=" in reasons_str
            assert "timing=" in reasons_str
            # Should have intel snapshot attached
            assert hasattr(opp, "_intel_snapshot")


# =============================================================================
# Phase 7: Fill Activity / Spread Liquidity Detection
# =============================================================================


class TestFillActivity:
    """Test fill detection from orderbook depth changes.

    Detects when depth disappears between snapshots (= someone traded),
    distinguishing real liquid spreads from phantom resting orders.
    """

    def test_no_fills_on_first_snapshot(self):
        """First snapshot has no fill history to compare against."""
        intel = make_intel()
        book = make_book(bid_size=10, ask_size=10)

        with patch("time.time", return_value=100.0):
            signals = intel.update("T", book)

        fa = signals.fill_activity
        assert fa.last_bid_fill_price is None
        assert fa.last_ask_fill_price is None
        assert fa.bid_fill_rate == 0.0
        assert fa.ask_fill_rate == 0.0
        assert fa.spread_is_active is False

    def test_bid_fill_detected(self):
        """Depth decrease on bid side detected as a fill (seller hit the bid)."""
        intel = make_intel()

        # Snapshot 1: 20 contracts at bid=45
        book1 = make_book(bid_price=45, bid_size=20, ask_price=55, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Snapshot 2: only 12 contracts at bid=45 (8 got filled)
        book2 = make_book(bid_price=45, bid_size=12, ask_price=55, ask_size=10)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        fa = signals.fill_activity
        assert fa.last_bid_fill_price == 45
        assert fa.last_bid_fill_time == 105.0
        assert fa.seconds_since_bid_fill == pytest.approx(0.0, abs=0.1)
        assert fa.bid_fill_volume == 8

    def test_ask_fill_detected(self):
        """Depth decrease on ask side detected as a fill (buyer lifted the ask)."""
        intel = make_intel()

        # Snapshot 1: 15 contracts at ask=55
        book1 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=15)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Snapshot 2: only 5 contracts at ask=55 (10 got filled)
        book2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=5)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        fa = signals.fill_activity
        assert fa.last_ask_fill_price == 55
        assert fa.ask_fill_volume == 10

    def test_level_disappears_completely(self):
        """Level vanishing entirely counts as a fill."""
        intel = make_intel()

        # Snapshot 1: bid at 45 with size 5
        book1 = make_book(bid_price=45, bid_size=5, ask_price=55, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Snapshot 2: bid moved down to 43 (45 disappeared entirely)
        book2 = make_book(bid_price=43, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        fa = signals.fill_activity
        assert fa.last_bid_fill_price == 45
        assert fa.bid_fill_volume == 5

    def test_no_fill_when_depth_increases(self):
        """Depth increasing is not a fill (new resting orders added)."""
        intel = make_intel()

        book1 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Depth increases — someone added orders, no fill
        book2 = make_book(bid_price=45, bid_size=25, ask_price=55, ask_size=10)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        fa = signals.fill_activity
        assert fa.last_bid_fill_price is None

    def test_spread_is_active_both_sides(self):
        """Spread is active when both sides have recent fills."""
        config = OrderbookIntelConfig(spread_active_max_gap_seconds=30.0)
        intel = make_intel(config)

        # Snapshot 1
        book1 = make_book(bid_price=45, bid_size=20, ask_price=55, ask_size=20)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Snapshot 2: bid fill (depth decreased)
        book2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=20)
        with patch("time.time", return_value=105.0):
            intel.update("T", book2)

        # Snapshot 3: ask fill too (depth decreased on ask side)
        book3 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=12)
        with patch("time.time", return_value=110.0):
            signals = intel.update("T", book3)

        fa = signals.fill_activity
        assert fa.last_bid_fill_price == 45
        assert fa.last_ask_fill_price == 55
        assert fa.spread_is_active is True

    def test_spread_not_active_one_side_only(self):
        """Spread is NOT active when only one side has fills."""
        config = OrderbookIntelConfig(spread_active_max_gap_seconds=30.0)
        intel = make_intel(config)

        book1 = make_book(bid_price=45, bid_size=20, ask_price=55, ask_size=20)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Only bid side fill, ask stays the same
        book2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=20)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        fa = signals.fill_activity
        assert fa.last_bid_fill_price == 45
        assert fa.last_ask_fill_price is None
        assert fa.spread_is_active is False

    def test_spread_becomes_inactive_after_gap(self):
        """Spread stops being active when fills are too old."""
        config = OrderbookIntelConfig(spread_active_max_gap_seconds=10.0)
        intel = make_intel(config)

        # Build up fills on both sides
        book1 = make_book(bid_price=45, bid_size=20, ask_price=55, ask_size=20)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        book2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)
        assert signals.fill_activity.spread_is_active is True

        # 20 seconds later, no more fills -> stale
        book3 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=125.0):
            signals = intel.update("T", book3)

        fa = signals.fill_activity
        assert fa.seconds_since_bid_fill == pytest.approx(20.0, abs=0.1)
        assert fa.spread_is_active is False

    def test_fill_rate_calculation(self):
        """Fill rate is fills per minute over the lookback window."""
        config = OrderbookIntelConfig(fill_activity_window_seconds=60.0)
        intel = make_intel(config)

        # Generate 6 bid fills over 30 seconds
        size = 20
        for i in range(7):
            book = make_book(bid_price=45, bid_size=size, ask_price=55, ask_size=10)
            with patch("time.time", return_value=100.0 + i * 5):
                intel.update("T", book)
            size -= 2  # each snapshot loses 2 contracts

        signals = intel.get_signals("T")
        fa = signals.fill_activity

        # 6 fills detected in 60s window -> 6 fills/min
        assert fa.bid_fill_rate == pytest.approx(6.0, abs=0.1)
        assert fa.bid_fill_volume == 12  # 6 fills * 2 contracts each

    def test_far_away_level_not_counted_as_fill(self):
        """Depth disappearing far from best bid is not a fill (likely cancel)."""
        intel = make_intel()

        # Snapshot 1: bid at 45, plus resting order far away at 30
        book1 = make_book(
            bid_price=45,
            bid_size=10,
            ask_price=55,
            ask_size=10,
            extra_bids=[(30, 50)],
        )
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Snapshot 2: far-away bid at 30 disappeared (cancelled, not filled)
        book2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        fa = signals.fill_activity
        # Far away level (30, more than 2c from best bid 45) should not register
        assert fa.last_bid_fill_price is None

    def test_multiple_fills_same_snapshot(self):
        """Multiple levels losing depth in same snapshot registers multiple fills."""
        intel = make_intel()

        # Snapshot 1: best bid 45 and next level 44, both with depth
        book1 = make_book(
            bid_price=45,
            bid_size=10,
            ask_price=55,
            ask_size=10,
            extra_bids=[(44, 15)],
        )
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        # Snapshot 2: both levels lost depth
        book2 = make_book(
            bid_price=45,
            bid_size=5,
            ask_price=55,
            ask_size=10,
            extra_bids=[(44, 8)],
        )
        with patch("time.time", return_value=105.0):
            signals = intel.update("T", book2)

        fa = signals.fill_activity
        # Total volume: 5 from level 45 + 7 from level 44
        assert fa.bid_fill_volume == 12

    def test_fill_activity_independent_per_ticker(self):
        """Fill detection is isolated per ticker."""
        intel = make_intel()

        # Ticker A: set up and create a fill
        book_a1 = make_book(bid_price=45, bid_size=20, ask_price=55, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("A", book_a1)
        book_a2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=10)
        with patch("time.time", return_value=105.0):
            intel.update("A", book_a2)

        # Ticker B: no fills
        book_b = make_book(bid_price=50, bid_size=10, ask_price=60, ask_size=10)
        with patch("time.time", return_value=100.0):
            intel.update("B", book_b)
        with patch("time.time", return_value=105.0):
            signals_b = intel.update("B", book_b)

        sig_a = intel.get_signals("A")
        assert sig_a.fill_activity.last_bid_fill_price == 45
        assert signals_b.fill_activity.last_bid_fill_price is None

    def test_fill_activity_in_logging_snapshot(self):
        """Fill activity fields appear in logging snapshot."""
        intel = make_intel()

        book1 = make_book(bid_price=45, bid_size=20, ask_price=55, ask_size=20)
        with patch("time.time", return_value=100.0):
            intel.update("T", book1)

        book2 = make_book(bid_price=45, bid_size=10, ask_price=55, ask_size=12)
        with patch("time.time", return_value=105.0):
            intel.update("T", book2)

        snap = intel.get_snapshot_for_logging("T")

        assert snap["last_bid_fill_price"] == 45
        assert snap["last_ask_fill_price"] == 55
        assert snap["bid_fill_volume"] == 10
        assert snap["ask_fill_volume"] == 8
        assert snap["spread_active"] is True

    def test_fill_activity_serializable(self):
        """Fill activity fields are JSON-serializable (including None values)."""
        import json

        intel = make_intel()
        book = make_book()
        with patch("time.time", return_value=100.0):
            intel.update("T", book)

        snap = intel.get_snapshot_for_logging("T")
        serialized = json.dumps(snap)
        assert isinstance(serialized, str)

        # None values should serialize as null
        parsed = json.loads(serialized)
        assert parsed["last_bid_fill_price"] is None
