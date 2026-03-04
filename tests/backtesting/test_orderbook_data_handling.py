"""Tests for orderbook data handling in backtest framework.

Tests depth estimation, confidence scoring, and handling of partial/missing data.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock

from src.core.models import Fill, MarketState
from src.backtesting.depth_estimation import (
    DEFAULT_LOW_DEPTH,
    DEFAULT_WIDE_SPREAD_CENTS,
    DEFAULT_BASE_DEPTH,
    estimate_depth_from_spread,
    get_orderbook_depth_with_fallback,
)
from src.backtesting.metrics import BacktestMetadata
from src.backtesting.engine import BacktestEngine


# ---------------------------------------------------------------------------
# Depth Estimation Tests
# ---------------------------------------------------------------------------


class TestDepthEstimation:
    """Test depth estimation from spread when real depth is missing."""

    def test_estimate_depth_tight_spread(self):
        """Tight spread should estimate high depth."""
        # 1-cent spread (very tight)
        depth = estimate_depth_from_spread(1)
        assert depth >= 1000  # Should be very high
        assert depth == DEFAULT_BASE_DEPTH * 100  # 50 * 100 = 5000

    def test_estimate_depth_wide_spread(self):
        """Wide spread should estimate low depth."""
        # 50-cent spread (very wide)
        depth = estimate_depth_from_spread(50)
        assert depth == DEFAULT_BASE_DEPTH * 2  # 50 * (100/50) = 100

    def test_estimate_depth_moderate_spread(self):
        """Moderate spread should estimate moderate depth."""
        # 10-cent spread
        depth = estimate_depth_from_spread(10)
        assert depth == DEFAULT_BASE_DEPTH * 10  # 50 * (100/10) = 500

    def test_estimate_depth_missing_spread(self):
        """Missing spread should use conservative fallback."""
        depth = estimate_depth_from_spread(None)
        assert depth == DEFAULT_LOW_DEPTH  # Conservative minimum

    def test_estimate_depth_zero_spread(self):
        """Zero spread should use conservative fallback."""
        depth = estimate_depth_from_spread(0)
        assert depth == DEFAULT_LOW_DEPTH

    def test_estimate_depth_negative_spread(self):
        """Negative spread (invalid) should use conservative fallback."""
        depth = estimate_depth_from_spread(-5)
        assert depth == DEFAULT_LOW_DEPTH

    def test_estimate_depth_custom_base(self):
        """Custom base depth should be used in calculation."""
        # 10-cent spread with custom base of 100
        depth = estimate_depth_from_spread(10, base_depth=100)
        assert depth == 1000  # 100 * (100/10)

    def test_estimate_depth_minimum_threshold(self):
        """Estimated depth should never be below minimum."""
        # Very wide spread that would estimate below minimum
        depth = estimate_depth_from_spread(1000)
        assert depth >= DEFAULT_LOW_DEPTH


class TestDepthFallback:
    """Test getting orderbook depth with fallback estimation."""

    def test_get_depth_with_real_data_bid_side(self):
        """Real depth data should be returned when available (BID side)."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
            bid_depth=100,
            ask_depth=50,
        )
        # Buying (BID) needs ask depth
        depth, is_estimated = get_orderbook_depth_with_fallback(market, "BID")
        assert depth == 50
        assert is_estimated is False

    def test_get_depth_with_real_data_ask_side(self):
        """Real depth data should be returned when available (ASK side)."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
            bid_depth=100,
            ask_depth=50,
        )
        # Selling (ASK) needs bid depth
        depth, is_estimated = get_orderbook_depth_with_fallback(market, "ASK")
        assert depth == 100
        assert is_estimated is False

    def test_get_depth_with_missing_data_bid_side(self):
        """Missing depth should be estimated from spread (BID side)."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
            bid_depth=None,
            ask_depth=None,
        )
        # Buying (BID) needs ask depth, spread is 1 cent
        depth, is_estimated = get_orderbook_depth_with_fallback(market, "BID")
        assert depth > 0
        assert is_estimated is True
        # Tight 1-cent spread should estimate high depth
        assert depth >= 1000

    def test_get_depth_with_missing_data_ask_side(self):
        """Missing depth should be estimated from spread (ASK side)."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.40,
            ask=0.45,  # 5-cent spread
            bid_depth=None,
            ask_depth=None,
        )
        # Selling (ASK) needs bid depth
        depth, is_estimated = get_orderbook_depth_with_fallback(market, "ASK")
        assert depth > 0
        assert is_estimated is True
        # 4-5 cent spread should estimate moderate depth (floating point varies)
        # Allow some floating point imprecision
        assert depth >= 800  # Should be around 50 * (100/4 or 100/5)
        assert depth <= 1500

    def test_get_depth_with_zero_depth(self):
        """Zero depth should trigger estimation."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
            bid_depth=0,
            ask_depth=0,
        )
        depth, is_estimated = get_orderbook_depth_with_fallback(market, "BID")
        assert depth > 0
        assert is_estimated is True

    def test_get_depth_with_partial_data(self):
        """When one side has depth but not the other, estimation needed."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
            bid_depth=100,
            ask_depth=None,  # Missing ask depth
        )
        # Buying (BID) needs ask depth - should estimate
        depth, is_estimated = get_orderbook_depth_with_fallback(market, "BID")
        assert depth > 0
        assert is_estimated is True


# ---------------------------------------------------------------------------
# Data Metadata Tests
# ---------------------------------------------------------------------------


class TestDataMetadata:
    """Test BacktestMetadata calculation and confidence scoring."""

    def test_metadata_high_confidence(self):
        """High confidence when >80% signals have full data."""
        # Build metadata with 90% complete data
        metadata = BacktestEngine._build_data_metadata(
            total_signals=100,
            signals_with_depth=90,
            signals_with_spread=95,
            signals_with_estimated_depth=10,
            signals_with_default_spread=5,
        )
        assert metadata.data_confidence == "HIGH"
        assert metadata.signals_with_full_data_pct == 90.0  # min(90, 95)
        assert metadata.signals_with_depth_data_pct == 90.0
        assert metadata.signals_with_spread_data_pct == 95.0
        assert metadata.signals_with_estimated_depth == 10
        assert metadata.signals_with_default_spread == 5
        assert metadata.total_signals == 100

    def test_metadata_medium_confidence(self):
        """Medium confidence when 50-80% signals have full data."""
        # Build metadata with 70% complete data
        metadata = BacktestEngine._build_data_metadata(
            total_signals=100,
            signals_with_depth=70,
            signals_with_spread=75,
            signals_with_estimated_depth=30,
            signals_with_default_spread=25,
        )
        assert metadata.data_confidence == "MEDIUM"
        assert metadata.signals_with_full_data_pct == 70.0

    def test_metadata_low_confidence(self):
        """Low confidence when <50% signals have full data."""
        # Build metadata with 30% complete data
        metadata = BacktestEngine._build_data_metadata(
            total_signals=100,
            signals_with_depth=30,
            signals_with_spread=40,
            signals_with_estimated_depth=70,
            signals_with_default_spread=60,
        )
        assert metadata.data_confidence == "LOW"
        assert metadata.signals_with_full_data_pct == 30.0

    def test_metadata_no_signals(self):
        """Handle zero signals gracefully."""
        metadata = BacktestEngine._build_data_metadata(
            total_signals=0,
            signals_with_depth=0,
            signals_with_spread=0,
            signals_with_estimated_depth=0,
            signals_with_default_spread=0,
        )
        assert metadata.data_confidence == "UNKNOWN"
        assert metadata.signals_with_full_data_pct == 0.0
        assert metadata.total_signals == 0

    def test_metadata_perfect_data(self):
        """100% complete data should be HIGH confidence."""
        metadata = BacktestEngine._build_data_metadata(
            total_signals=50,
            signals_with_depth=50,
            signals_with_spread=50,
            signals_with_estimated_depth=0,
            signals_with_default_spread=0,
        )
        assert metadata.data_confidence == "HIGH"
        assert metadata.signals_with_full_data_pct == 100.0
        assert metadata.signals_with_estimated_depth == 0
        assert metadata.signals_with_default_spread == 0

    def test_metadata_to_dict(self):
        """Metadata should serialize to dictionary."""
        metadata = BacktestMetadata(
            data_confidence="HIGH",
            signals_with_full_data_pct=85.0,
            signals_with_depth_data_pct=90.0,
            signals_with_spread_data_pct=85.0,
            signals_with_estimated_depth=5,
            signals_with_default_spread=10,
            total_signals=100,
        )
        d = metadata.to_dict()
        assert d["data_confidence"] == "HIGH"
        assert d["signals_with_full_data_pct"] == 85.0
        assert d["total_signals"] == 100


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestBacktestDataTracking:
    """Test data completeness tracking during backtest runs."""

    def test_market_state_supports_depth_fields(self):
        """MarketState should accept optional depth fields."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
            bid_depth=100,
            ask_depth=50,
        )
        assert market.bid_depth == 100
        assert market.ask_depth == 50

    def test_market_state_optional_depth(self):
        """MarketState should allow None depth values."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
            bid_depth=None,
            ask_depth=None,
        )
        assert market.bid_depth is None
        assert market.ask_depth is None

    def test_market_state_defaults_to_none(self):
        """MarketState should default depth to None when not provided."""
        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(),
            bid=0.45,
            ask=0.46,
        )
        assert market.bid_depth is None
        assert market.ask_depth is None


# ---------------------------------------------------------------------------
# Conservative Defaults Tests
# ---------------------------------------------------------------------------


class TestConservativeDefaults:
    """Test that conservative defaults are used appropriately."""

    def test_default_low_depth_is_conservative(self):
        """DEFAULT_LOW_DEPTH should be a conservative minimum."""
        assert DEFAULT_LOW_DEPTH == 5
        # This is conservative - assumes limited liquidity
        # when we have no information

    def test_default_wide_spread_is_conservative(self):
        """DEFAULT_WIDE_SPREAD_CENTS should be conservative."""
        assert DEFAULT_WIDE_SPREAD_CENTS == 10
        # 10 cents is quite wide, conservative assumption

    def test_estimation_never_below_minimum(self):
        """Depth estimation should always be at least DEFAULT_LOW_DEPTH."""
        # Test with various spread values
        for spread in [None, 0, -5, 1000, 10000]:
            depth = estimate_depth_from_spread(spread)
            assert depth >= DEFAULT_LOW_DEPTH


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
