"""Tests for market impact modeling in the backtest framework."""

import math
from datetime import datetime

import pytest

from src.core.models import Fill, MarketState
from src.backtesting import MarketImpactConfig, ImmediateFillModel
from src.backtesting.fill_model import apply_market_impact
from strategies.base import Signal


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def make_signal(ticker: str, side: str, size: int, price: float = 0.50) -> Signal:
    """Create a test signal."""
    return Signal(
        ticker=ticker,
        side=side,
        price=price,
        size=size,
        timestamp=datetime(2025, 1, 1),
    )


def make_market(
    ticker: str,
    bid: float = 0.49,
    ask: float = 0.51,
    bid_depth: int = None,
    ask_depth: int = None,
) -> MarketState:
    """Create a test market state."""
    return MarketState(
        ticker=ticker,
        timestamp=datetime(2025, 1, 1),
        bid=bid,
        ask=ask,
        bid_depth=bid_depth,
        ask_depth=ask_depth,
    )


# ---------------------------------------------------------------------------
# Unit tests for apply_market_impact
# ---------------------------------------------------------------------------


class TestApplyMarketImpact:
    """Test the market impact calculation function."""

    def test_disabled_impact_returns_unchanged_price(self):
        """When impact is disabled, price should be unchanged."""
        config = MarketImpactConfig(enable_impact=False)
        result = apply_market_impact(0.50, 10, "BID", 100, config)
        assert result == 0.50

    def test_zero_size_returns_unchanged_price(self):
        """Zero-size orders should not have impact."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        result = apply_market_impact(0.50, 0, "BID", 100, config)
        assert result == 0.50

    def test_buy_order_increases_price(self):
        """BUY orders should move price UP (worse for buyer)."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        result = apply_market_impact(0.50, 10, "BID", 5, config)
        # Impact: 5 * sqrt(10/5) = 5 * sqrt(2) ≈ 7.07 cents
        # Expected: 0.50 + 0.0707 ≈ 0.5707
        assert result > 0.50
        assert result == pytest.approx(0.5707, abs=0.001)

    def test_sell_order_decreases_price(self):
        """SELL orders should move price DOWN (worse for seller)."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        result = apply_market_impact(0.50, 10, "ASK", 5, config)
        # Impact: 5 * sqrt(10/5) = 5 * sqrt(2) ≈ 7.07 cents
        # Expected: 0.50 - 0.0707 ≈ 0.4293
        assert result < 0.50
        assert result == pytest.approx(0.4293, abs=0.001)

    def test_large_order_has_larger_impact(self):
        """Larger orders should have more impact."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)

        small_impact = apply_market_impact(0.50, 10, "BID", 100, config)
        large_impact = apply_market_impact(0.50, 100, "BID", 100, config)

        # Both should increase price, but large order more so
        assert small_impact > 0.50
        assert large_impact > small_impact

        # Large order: 5 * sqrt(100/100) = 5 cents
        # Expected: 0.50 + 0.05 = 0.55
        assert large_impact == pytest.approx(0.55, abs=0.001)

    def test_better_depth_reduces_impact(self):
        """Higher depth should reduce impact."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)

        low_depth_impact = apply_market_impact(0.50, 10, "BID", 5, config)
        high_depth_impact = apply_market_impact(0.50, 10, "BID", 100, config)

        # Both should increase price, but low depth more so
        assert low_depth_impact > high_depth_impact

        # High depth: 5 * sqrt(10/100) = 5 * 0.316... ≈ 1.58 cents
        # Expected: 0.50 + 0.0158 ≈ 0.5158
        assert high_depth_impact == pytest.approx(0.5158, abs=0.001)

    def test_impact_coefficient_scaling(self):
        """Higher coefficient should produce more impact."""
        config_low = MarketImpactConfig(enable_impact=True, impact_coeff=2.0)
        config_high = MarketImpactConfig(enable_impact=True, impact_coeff=10.0)

        low_impact = apply_market_impact(0.50, 10, "BID", 5, config_low)
        high_impact = apply_market_impact(0.50, 10, "BID", 5, config_high)

        # Both should increase price, but high coefficient more so
        assert low_impact < high_impact

        # Low: 2 * sqrt(2) ≈ 2.83 cents → 0.5283
        # High: 10 * sqrt(2) ≈ 14.14 cents → 0.6414
        assert low_impact == pytest.approx(0.5283, abs=0.001)
        assert high_impact == pytest.approx(0.6414, abs=0.001)

    def test_price_clamped_to_zero(self):
        """Price should be clamped to 0.0 minimum."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=100.0)
        # Huge impact on sell should not go negative
        result = apply_market_impact(0.10, 100, "ASK", 1, config)
        assert result == 0.0

    def test_price_clamped_to_one(self):
        """Price should be clamped to 1.0 maximum."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=100.0)
        # Huge impact on buy should not exceed 1.0
        result = apply_market_impact(0.90, 100, "BID", 1, config)
        assert result == 1.0

    def test_min_depth_prevents_division_by_zero(self):
        """Should use min_depth when available depth is zero."""
        config = MarketImpactConfig(
            enable_impact=True,
            impact_coeff=5.0,
            min_depth=1.0,
        )
        # Zero depth should fall back to min_depth
        result = apply_market_impact(0.50, 10, "BID", 0, config)

        # Impact: 5 * sqrt(10/1) = 5 * 3.162... ≈ 15.81 cents
        # Expected: 0.50 + 0.1581 ≈ 0.6581
        assert result == pytest.approx(0.6581, abs=0.001)

    def test_square_root_scaling(self):
        """Impact should scale with sqrt(size/depth)."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)

        # Test various size/depth ratios
        ratios = [0.25, 1.0, 4.0, 16.0]
        expected_impacts = [5.0 * math.sqrt(r) for r in ratios]

        for ratio, expected in zip(ratios, expected_impacts):
            size = int(ratio * 100)
            result = apply_market_impact(0.50, size, "BID", 100, config)
            expected_price = 0.50 + expected / 100.0
            assert result == pytest.approx(expected_price, abs=0.001)


# ---------------------------------------------------------------------------
# Integration tests with ImmediateFillModel
# ---------------------------------------------------------------------------


class TestImmediateFillModelWithImpact:
    """Test ImmediateFillModel integration with market impact."""

    def test_no_impact_when_disabled(self):
        """Fill model should not apply impact when disabled."""
        config = MarketImpactConfig(enable_impact=False)
        model = ImmediateFillModel(impact_config=config)

        signal = make_signal("TEST", "BID", 10)
        market = make_market("TEST", bid=0.49, ask=0.51, ask_depth=5)

        fill = model.simulate_fill(signal, market)

        # Should fill at ask price without impact
        assert fill is not None
        assert fill.price == 0.51

    def test_buy_order_with_impact(self):
        """BUY orders should have upward price impact."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        model = ImmediateFillModel(impact_config=config)

        signal = make_signal("TEST", "BID", 10)
        market = make_market("TEST", bid=0.49, ask=0.51, ask_depth=5)

        fill = model.simulate_fill(signal, market)

        # Should fill at ask + impact
        # Base: 0.51, Impact: 5 * sqrt(10/5) ≈ 7.07 cents
        # Expected: 0.51 + 0.0707 ≈ 0.5807
        assert fill is not None
        assert fill.price > 0.51
        assert fill.price == pytest.approx(0.5807, abs=0.001)

    def test_sell_order_with_impact(self):
        """SELL orders should have downward price impact."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        model = ImmediateFillModel(impact_config=config)

        signal = make_signal("TEST", "ASK", 10)
        market = make_market("TEST", bid=0.49, ask=0.51, bid_depth=5)

        fill = model.simulate_fill(signal, market)

        # Should fill at bid - impact
        # Base: 0.49, Impact: 5 * sqrt(10/5) ≈ 7.07 cents
        # Expected: 0.49 - 0.0707 ≈ 0.4193
        assert fill is not None
        assert fill.price < 0.49
        assert fill.price == pytest.approx(0.4193, abs=0.001)

    def test_impact_with_slippage(self):
        """Impact should be applied on top of slippage."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        model = ImmediateFillModel(
            slippage=0.01,  # 1 cent slippage
            impact_config=config,
        )

        signal = make_signal("TEST", "BID", 10)
        market = make_market("TEST", bid=0.49, ask=0.51, ask_depth=5)

        fill = model.simulate_fill(signal, market)

        # Should fill at ask + slippage + impact
        # Base: 0.51, Slippage: 0.01, Impact: 0.0707
        # Expected: 0.51 + 0.01 + 0.0707 ≈ 0.5907
        assert fill is not None
        assert fill.price == pytest.approx(0.5907, abs=0.001)

    def test_missing_depth_uses_fallback(self):
        """Should use depth estimation when orderbook depth is missing."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        model = ImmediateFillModel(impact_config=config)

        signal = make_signal("TEST", "BID", 10)
        # Market with missing depth (will be estimated from spread)
        market = make_market("TEST", bid=0.49, ask=0.51, ask_depth=None)

        fill = model.simulate_fill(signal, market)

        # Spread: 2 cents, estimated depth ≈ 50 * (100/2) = 2500
        # Impact: 5 * sqrt(10/2500) ≈ 0.316 cents ≈ 0.00316
        # Expected: 0.51 + 0.00316 ≈ 0.51316
        assert fill is not None
        assert fill.price > 0.51
        assert fill.price < 0.52  # Should be small impact

    def test_large_order_on_thin_market(self):
        """Large order on thin market should have substantial impact."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        model = ImmediateFillModel(impact_config=config)

        signal = make_signal("TEST", "BID", 100)
        market = make_market("TEST", bid=0.49, ask=0.51, ask_depth=10)

        fill = model.simulate_fill(signal, market)

        # Impact: 5 * sqrt(100/10) = 5 * sqrt(10) ≈ 15.81 cents
        # Expected: 0.51 + 0.1581 ≈ 0.6681
        assert fill is not None
        assert fill.price == pytest.approx(0.6681, abs=0.001)

    def test_small_order_on_deep_market(self):
        """Small order on deep market should have minimal impact."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        model = ImmediateFillModel(impact_config=config)

        signal = make_signal("TEST", "BID", 10)
        market = make_market("TEST", bid=0.49, ask=0.51, ask_depth=1000)

        fill = model.simulate_fill(signal, market)

        # Impact: 5 * sqrt(10/1000) = 5 * 0.1 = 0.5 cents
        # Expected: 0.51 + 0.005 = 0.515
        assert fill is not None
        assert fill.price == pytest.approx(0.515, abs=0.001)

    def test_fees_calculated_on_adjusted_price(self):
        """Fees should be calculated on the impact-adjusted price."""
        config = MarketImpactConfig(enable_impact=True, impact_coeff=5.0)
        model = ImmediateFillModel(impact_config=config)

        signal = make_signal("TEST", "BID", 10)
        market = make_market("TEST", bid=0.49, ask=0.51, ask_depth=5)

        fill = model.simulate_fill(signal, market)

        # Price after impact: ~0.5807
        # Fee: min(0.0175, 0.07 * 0.5807 * (1 - 0.5807)) * 10
        # Fee: min(0.0175, 0.07 * 0.5807 * 0.4193) * 10
        # Fee: min(0.0175, 0.0170...) * 10 ≈ 0.170
        assert fill is not None
        assert fill.fee > 0
        assert fill.fee < 0.0175 * 10  # Should be less than max fee
