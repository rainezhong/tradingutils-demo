"""Tests for the fee calculator."""

import pytest
from decimal import Decimal

from arb.spread_detector import Platform
from src.arbitrage.fee_calculator import FeeCalculator, SpreadAnalysis
from src.arbitrage.config import ArbitrageConfig


class TestFeeCalculator:
    """Test suite for FeeCalculator."""

    def test_init_with_defaults(self):
        """Test initialization with default config."""
        calc = FeeCalculator()
        assert calc._config is not None

    def test_init_with_config(self, config):
        """Test initialization with custom config."""
        calc = FeeCalculator(config)
        assert calc._config == config

    def test_calculate_net_spread_profitable(self, fee_calculator):
        """Test net spread calculation for a profitable spread."""
        analysis = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
        )

        assert isinstance(analysis, SpreadAnalysis)
        assert analysis.gross_spread == pytest.approx(0.05, abs=0.001)
        assert analysis.is_profitable
        assert analysis.net_spread > 0
        assert analysis.total_fees > 0
        assert analysis.estimated_profit > 0

    def test_calculate_net_spread_unprofitable(self, fee_calculator):
        """Test net spread calculation for an unprofitable spread."""
        analysis = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.50,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,  # Same price
            size=100,
        )

        assert analysis.gross_spread == 0.0
        assert not analysis.is_profitable

    def test_calculate_net_spread_fees_exceed_spread(self, fee_calculator):
        """Test when fees exceed the gross spread."""
        analysis = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.50,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.505,  # Very small spread
            size=100,
        )

        # Gross spread is positive but net may be negative due to fees
        assert analysis.gross_spread == pytest.approx(0.005, abs=0.001)
        # Net spread should account for fees

    def test_calculate_dutch_book_spread_profitable(self, fee_calculator):
        """Test dutch book calculation when profitable."""
        analysis = fee_calculator.calculate_dutch_book_spread(
            platform_a=Platform.KALSHI,
            price_a=0.45,  # YES price
            platform_b=Platform.POLYMARKET,
            price_b=0.48,  # NO price
            size=100,
        )

        # 0.45 + 0.48 = 0.93, so gross spread is 0.07
        assert analysis.gross_spread == pytest.approx(0.07, abs=0.001)
        assert analysis.is_profitable

    def test_calculate_dutch_book_spread_unprofitable(self, fee_calculator):
        """Test dutch book calculation when unprofitable."""
        analysis = fee_calculator.calculate_dutch_book_spread(
            platform_a=Platform.KALSHI,
            price_a=0.55,
            platform_b=Platform.POLYMARKET,
            price_b=0.50,
            size=100,
        )

        # 0.55 + 0.50 = 1.05 > 1.0, so not profitable
        assert analysis.gross_spread == pytest.approx(-0.05, abs=0.001)
        assert not analysis.is_profitable

    def test_roi_calculation(self, fee_calculator):
        """Test ROI calculation."""
        analysis = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.40,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
        )

        # ROI = net_profit / capital_required
        assert analysis.roi > 0
        assert analysis.capital_required > 0
        # ROI should be roughly net_spread / buy_price
        expected_roi_range = (0.01, 0.30)  # 1% to 30%
        assert expected_roi_range[0] < analysis.roi < expected_roi_range[1]

    def test_maker_vs_taker_fees(self, fee_calculator):
        """Test that maker orders have lower fees than taker."""
        taker_analysis = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.50,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.55,
            size=100,
            buy_maker=False,
            sell_maker=False,
        )

        maker_analysis = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.50,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.55,
            size=100,
            buy_maker=True,
            sell_maker=True,
        )

        # Maker fees should be lower, so net spread higher
        assert maker_analysis.total_fees <= taker_analysis.total_fees
        assert maker_analysis.net_spread >= taker_analysis.net_spread

    def test_breakeven_spread(self, fee_calculator):
        """Test breakeven spread calculation."""
        buy_price = 0.45
        breakeven = fee_calculator.get_breakeven_spread(
            buy_platform=Platform.KALSHI,
            buy_price=buy_price,
            sell_platform=Platform.POLYMARKET,
            size=100,
        )

        # Breakeven should be higher than buy price
        assert breakeven > buy_price

        # Verify by checking that selling at breakeven gives ~0 profit
        analysis = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=buy_price,
            sell_platform=Platform.POLYMARKET,
            sell_price=breakeven,
            size=100,
        )
        # Should be close to zero (allowing for rounding)
        assert abs(analysis.net_spread) < 0.01

    def test_slippage_impact_no_slippage(self, fee_calculator):
        """Test slippage estimate when sufficient depth."""
        slippage = fee_calculator.estimate_slippage_impact(
            platform=Platform.KALSHI,
            price=0.50,
            size=100,
            depth_at_price=200,  # More than needed
        )
        assert slippage == 0.0

    def test_slippage_impact_with_slippage(self, fee_calculator):
        """Test slippage estimate when insufficient depth."""
        slippage = fee_calculator.estimate_slippage_impact(
            platform=Platform.KALSHI,
            price=0.50,
            size=100,
            depth_at_price=50,  # Only 50% of needed
        )
        assert slippage > 0


class TestSpreadAnalysis:
    """Test the SpreadAnalysis dataclass."""

    def test_is_profitable_true(self):
        """Test is_profitable when net_spread positive."""
        analysis = SpreadAnalysis(
            gross_spread=0.05,
            net_spread=0.02,
            buy_fee=0.01,
            sell_fee=0.02,
            total_fees=0.03,
            roi=0.04,
            capital_required=100.0,
            estimated_profit=2.0,
        )
        assert analysis.is_profitable

    def test_is_profitable_false(self):
        """Test is_profitable when net_spread negative."""
        analysis = SpreadAnalysis(
            gross_spread=0.01,
            net_spread=-0.02,
            buy_fee=0.02,
            sell_fee=0.01,
            total_fees=0.03,
            roi=-0.02,
            capital_required=100.0,
            estimated_profit=-2.0,
        )
        assert not analysis.is_profitable


class TestMinProfitableSize:
    """Test the calculate_min_profitable_size method."""

    def test_min_size_with_no_spread(self, fee_calculator):
        """Test that zero or negative spread returns infinity."""
        min_size = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.KALSHI,
            buy_price=0.50,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,  # Same price = no spread
        )
        assert min_size == float('inf')

    def test_min_size_with_negative_spread(self, fee_calculator):
        """Test that negative spread returns infinity."""
        min_size = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.KALSHI,
            buy_price=0.55,  # Buy high
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,  # Sell low
        )
        assert min_size == float('inf')

    def test_min_size_kalshi_only_no_gas(self, fee_calculator):
        """Test min size with Kalshi-only trade (no gas costs)."""
        min_size = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.KALSHI,
            sell_price=0.50,
        )
        # No gas costs, so any size should work
        assert min_size == 1

    def test_min_size_with_polymarket_gas(self, fee_calculator):
        """Test min size calculation includes Polymarket gas."""
        min_size = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.POLYMARKET,
            buy_price=0.45,
            sell_platform=Platform.KALSHI,
            sell_price=0.50,
        )
        # Should need enough contracts to cover gas
        assert min_size >= 1
        assert isinstance(min_size, int)

    def test_min_size_with_both_polymarket_more_gas(self, fee_calculator):
        """Test min size with Polymarket on both sides (double gas)."""
        one_poly = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.POLYMARKET,
            buy_price=0.45,
            sell_platform=Platform.KALSHI,
            sell_price=0.50,
        )
        two_poly = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.POLYMARKET,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
        )
        # Two Polymarket sides should require more contracts
        assert two_poly >= one_poly

    def test_min_size_maker_vs_taker(self, fee_calculator):
        """Test that maker orders require fewer contracts."""
        taker_min = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            buy_maker=False,
            sell_maker=False,
        )
        maker_min = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            buy_maker=True,
            sell_maker=True,
        )
        # Maker should require same or fewer contracts
        assert maker_min <= taker_min

    def test_min_size_small_spread_needs_more_contracts(self, fee_calculator):
        """Test that smaller spreads require more contracts."""
        small_spread = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.POLYMARKET,
            buy_price=0.49,
            sell_platform=Platform.KALSHI,
            sell_price=0.50,  # 1 cent spread
        )
        large_spread = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.POLYMARKET,
            buy_price=0.40,
            sell_platform=Platform.KALSHI,
            sell_price=0.50,  # 10 cent spread
        )
        # Smaller spread should need more contracts to cover fixed costs
        assert small_spread >= large_spread

    def test_min_size_returns_integer(self, fee_calculator):
        """Test that min size is always an integer (or inf)."""
        min_size = fee_calculator.calculate_min_profitable_size(
            buy_platform=Platform.POLYMARKET,
            buy_price=0.45,
            sell_platform=Platform.KALSHI,
            sell_price=0.48,
        )
        if min_size != float('inf'):
            assert isinstance(min_size, int)
            assert min_size >= 1


class TestGetFeeRate:
    """Test the _get_fee_rate helper method."""

    def test_kalshi_taker_rate(self, fee_calculator):
        """Test Kalshi taker fee rate."""
        rate = fee_calculator._get_fee_rate(Platform.KALSHI, is_maker=False)
        assert rate == 0.07

    def test_kalshi_maker_rate(self, fee_calculator):
        """Test Kalshi maker fee rate."""
        rate = fee_calculator._get_fee_rate(Platform.KALSHI, is_maker=True)
        assert rate == 0.0175

    def test_polymarket_taker_rate(self, fee_calculator):
        """Test Polymarket taker fee rate."""
        rate = fee_calculator._get_fee_rate(Platform.POLYMARKET, is_maker=False)
        assert rate == 0.02

    def test_polymarket_maker_rate(self, fee_calculator):
        """Test Polymarket maker fee rate (should be 0)."""
        rate = fee_calculator._get_fee_rate(Platform.POLYMARKET, is_maker=True)
        assert rate == 0.0


class TestFeeSafetyMargin:
    """Test fee safety margin functionality."""

    def test_safety_margin_increases_fees(self, fee_calculator):
        """Test that safety margin inflates calculated fees."""
        # Calculate without safety margin
        normal = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
            apply_safety_margin=False,
        )

        # Calculate with safety margin
        safe = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
            apply_safety_margin=True,
        )

        # Safety margin should increase fees
        assert safe.total_fees > normal.total_fees
        # Safety margin should decrease net spread
        assert safe.net_spread < normal.net_spread
        # Safety margin should decrease estimated profit
        assert safe.estimated_profit < normal.estimated_profit

    def test_safety_margin_percentage(self, config):
        """Test that safety margin applies correct percentage."""
        calc = FeeCalculator(config)

        normal = calc.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.50,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.55,
            size=100,
            apply_safety_margin=False,
        )

        safe = calc.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.50,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.55,
            size=100,
            apply_safety_margin=True,
        )

        # Fees should be inflated by ~15% (the safety margin)
        expected_multiplier = 1.0 + config.fee_safety_margin
        assert safe.total_fees == pytest.approx(
            normal.total_fees * expected_multiplier, rel=0.01
        )


class TestConservativeFeeCalculation:
    """Test conservative fee calculation for filtering."""

    def test_conservative_uses_taker_fees(self, fee_calculator):
        """Test that conservative calculation uses taker fees."""
        # Maker analysis
        maker = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
            buy_maker=True,
            sell_maker=True,
        )

        # Conservative analysis (always uses taker)
        conservative = fee_calculator.calculate_net_spread_conservative(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
        )

        # Conservative should have higher fees (taker > maker)
        # and also includes safety margin
        assert conservative.total_fees > maker.total_fees
        assert conservative.net_spread < maker.net_spread

    def test_conservative_applies_safety_margin(self, fee_calculator):
        """Test that conservative calculation applies safety margin."""
        # Taker analysis without safety margin
        taker = fee_calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
            buy_maker=False,
            sell_maker=False,
            apply_safety_margin=False,
        )

        # Conservative analysis (taker + safety margin)
        conservative = fee_calculator.calculate_net_spread_conservative(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
        )

        # Conservative should have higher fees due to safety margin
        assert conservative.total_fees > taker.total_fees

    def test_conservative_filters_marginal_trades(self, config):
        """Test that conservative calculation properly filters marginal trades."""
        calc = FeeCalculator(config)

        # A trade that looks profitable with maker fees
        maker = calc.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.48,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,  # Only 2 cent spread
            size=100,
            buy_maker=True,
            sell_maker=True,
        )

        # Same trade with conservative calculation
        conservative = calc.calculate_net_spread_conservative(
            buy_platform=Platform.KALSHI,
            buy_price=0.48,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.50,
            size=100,
        )

        # Marginal trade might look profitable with maker fees
        # but should be filtered with conservative calculation
        # The conservative net spread should be lower
        assert conservative.net_spread < maker.net_spread
