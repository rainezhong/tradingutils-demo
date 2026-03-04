"""Tests for Avellaneda-Stoikov reservation pricer."""

import math
import pytest

from strategies.prediction_mm.reservation_pricer import ReservationPricer
from strategies.prediction_mm.pricer import SECONDS_PER_YEAR


class TestReservationPricerBasics:
    """Test basic initialization and edge cases."""

    def test_initialization(self):
        """Pricer should initialize with valid parameters."""
        pricer = ReservationPricer(risk_aversion=0.05)
        assert pricer.risk_aversion == 0.05
        assert pricer.use_log_odds is False

    def test_invalid_risk_aversion(self):
        """Risk aversion must be positive."""
        with pytest.raises(ValueError):
            ReservationPricer(risk_aversion=0.0)
        with pytest.raises(ValueError):
            ReservationPricer(risk_aversion=-0.01)

    def test_no_position_no_adjustment(self):
        """Zero position should yield no adjustment."""
        pricer = ReservationPricer(risk_aversion=0.05)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=0,
            variance=0.25,
            time_to_expiry_sec=600,
        )
        assert result.reservation_price == 0.5
        assert result.inventory_adjustment == 0.0
        assert result.net_position == 0

    def test_zero_time_no_adjustment(self):
        """At expiry (T=0), no adjustment regardless of position."""
        pricer = ReservationPricer(risk_aversion=0.05)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=50,
            variance=0.25,
            time_to_expiry_sec=0,
        )
        assert result.reservation_price == 0.5
        assert result.inventory_adjustment == 0.0

    def test_negative_time_no_adjustment(self):
        """Negative time (expired) should not crash."""
        pricer = ReservationPricer(risk_aversion=0.05)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=50,
            variance=0.25,
            time_to_expiry_sec=-100,
        )
        assert result.reservation_price == 0.5
        assert result.inventory_adjustment == 0.0


class TestReservationPriceDirectMode:
    """Test direct (non-log-odds) A-S formula: r = s - q × γ × σ² × T"""

    def test_long_position_lowers_reservation(self):
        """Long position (q > 0) should lower reservation price."""
        pricer = ReservationPricer(risk_aversion=0.01, use_log_odds=False)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=50,  # long
            variance=0.25,
            time_to_expiry_sec=600,  # 10 minutes
        )
        # r = 0.5 - 50 × 0.01 × 0.25 × (600 / SECONDS_PER_YEAR)
        T_years = 600 / SECONDS_PER_YEAR
        expected_adjustment = -50 * 0.01 * 0.25 * T_years
        assert result.reservation_price < 0.5
        assert result.inventory_adjustment < 0
        assert abs(result.inventory_adjustment - expected_adjustment) < 1e-10

    def test_short_position_raises_reservation(self):
        """Short position (q < 0) should raise reservation price."""
        pricer = ReservationPricer(risk_aversion=0.01, use_log_odds=False)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=-50,  # short
            variance=0.25,
            time_to_expiry_sec=600,
        )
        # r = 0.5 - (-50) × 0.01 × 0.25 × T
        T_years = 600 / SECONDS_PER_YEAR
        expected_adjustment = -(-50) * 0.01 * 0.25 * T_years
        assert result.reservation_price > 0.5
        assert result.inventory_adjustment > 0
        assert abs(result.inventory_adjustment - expected_adjustment) < 1e-10

    def test_higher_risk_aversion_larger_adjustment(self):
        """Higher γ should produce larger inventory adjustment."""
        result_low = ReservationPricer(risk_aversion=0.01).calculate(
            fair_price=0.5, net_position=50, variance=0.25, time_to_expiry_sec=600
        )
        result_high = ReservationPricer(risk_aversion=0.1).calculate(
            fair_price=0.5, net_position=50, variance=0.25, time_to_expiry_sec=600
        )
        assert abs(result_high.inventory_adjustment) > abs(result_low.inventory_adjustment)
        assert result_high.reservation_price < result_low.reservation_price

    def test_higher_variance_larger_adjustment(self):
        """Higher variance should produce larger inventory adjustment."""
        pricer = ReservationPricer(risk_aversion=0.01)
        result_low = pricer.calculate(
            fair_price=0.5, net_position=50, variance=0.1, time_to_expiry_sec=600
        )
        result_high = pricer.calculate(
            fair_price=0.5, net_position=50, variance=0.5, time_to_expiry_sec=600
        )
        assert abs(result_high.inventory_adjustment) > abs(result_low.inventory_adjustment)

    def test_longer_time_larger_adjustment(self):
        """Longer time to expiry should produce larger inventory adjustment."""
        pricer = ReservationPricer(risk_aversion=0.01)
        result_short = pricer.calculate(
            fair_price=0.5, net_position=50, variance=0.25, time_to_expiry_sec=60
        )
        result_long = pricer.calculate(
            fair_price=0.5, net_position=50, variance=0.25, time_to_expiry_sec=600
        )
        assert abs(result_long.inventory_adjustment) > abs(result_short.inventory_adjustment)

    def test_clamping_to_valid_range(self):
        """Extreme adjustments should be clamped to [0.01, 0.99]."""
        pricer = ReservationPricer(risk_aversion=1.0, use_log_odds=False)
        # Very large long position + high risk aversion → reservation could go negative
        result = pricer.calculate(
            fair_price=0.5,
            net_position=1000,  # huge long position
            variance=2.0,
            time_to_expiry_sec=3600,
        )
        assert 0.01 <= result.reservation_price <= 0.99

    def test_manual_calculation_example(self):
        """Verify exact calculation against manual computation."""
        pricer = ReservationPricer(risk_aversion=0.02, use_log_odds=False)
        fair = 0.5
        q = 30
        var = 0.16  # σ² where σ = 0.4
        ttx_sec = 300  # 5 minutes
        T_years = 300 / SECONDS_PER_YEAR

        # r = 0.5 - 30 × 0.02 × 0.16 × T_years
        expected_adjustment = -30 * 0.02 * 0.16 * T_years
        expected_r = 0.5 + expected_adjustment

        result = pricer.calculate(fair, q, var, ttx_sec)
        assert abs(result.reservation_price - expected_r) < 1e-10
        assert abs(result.inventory_adjustment - expected_adjustment) < 1e-10


class TestReservationPriceLogOddsMode:
    """Test log-odds transformation mode."""

    def test_log_odds_basic_long_position(self):
        """Log-odds mode should produce valid reservation price for long position."""
        pricer = ReservationPricer(risk_aversion=0.01, use_log_odds=True)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=50,
            variance=0.25,
            time_to_expiry_sec=600,
        )
        assert result.reservation_price < 0.5  # long position → lower reservation
        assert 0.01 <= result.reservation_price <= 0.99
        assert result.used_log_odds is True

    def test_log_odds_basic_short_position(self):
        """Log-odds mode should produce valid reservation price for short position."""
        pricer = ReservationPricer(risk_aversion=0.01, use_log_odds=True)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=-50,
            variance=0.25,
            time_to_expiry_sec=600,
        )
        assert result.reservation_price > 0.5  # short position → higher reservation
        assert 0.01 <= result.reservation_price <= 0.99

    def test_log_odds_at_extreme_prices(self):
        """Log-odds should handle extreme fair prices (0.05, 0.95)."""
        pricer = ReservationPricer(risk_aversion=0.05, use_log_odds=True)

        # Very low price
        result_low = pricer.calculate(
            fair_price=0.05, net_position=50, variance=0.25, time_to_expiry_sec=600
        )
        assert 0.01 <= result_low.reservation_price <= 0.99

        # Very high price
        result_high = pricer.calculate(
            fair_price=0.95, net_position=50, variance=0.25, time_to_expiry_sec=600
        )
        assert 0.01 <= result_high.reservation_price <= 0.99

    def test_log_odds_extreme_adjustment_no_crash(self):
        """Extreme log-odds adjustments should not crash (overflow protection)."""
        pricer = ReservationPricer(risk_aversion=10.0, use_log_odds=True)
        result = pricer.calculate(
            fair_price=0.5,
            net_position=1000,  # huge position
            variance=5.0,
            time_to_expiry_sec=3600,
        )
        # Should be clamped to valid range
        assert 0.01 <= result.reservation_price <= 0.99

    def test_log_odds_at_50_percent_symmetric(self):
        """At 50% fair value, log-odds L=0, so formula simplifies to direct mode."""
        pricer_log = ReservationPricer(risk_aversion=0.02, use_log_odds=True)
        pricer_direct = ReservationPricer(risk_aversion=0.02, use_log_odds=False)

        # At fair=0.5, log-odds = ln(0.5/0.5) = 0, so adjustment is same
        fair = 0.5
        q = 30
        var = 0.25
        ttx = 600

        result_log = pricer_log.calculate(fair, q, var, ttx)
        result_direct = pricer_direct.calculate(fair, q, var, ttx)

        # Should be very close (may differ slightly due to exp/log roundoff)
        assert abs(result_log.reservation_price - result_direct.reservation_price) < 0.01


class TestReservationPriceDiagnostics:
    """Test diagnostic fields in ReservationPriceResult."""

    def test_result_captures_all_inputs(self):
        """Result should capture all input parameters."""
        pricer = ReservationPricer(risk_aversion=0.03, use_log_odds=False)
        result = pricer.calculate(
            fair_price=0.6,
            net_position=25,
            variance=0.36,
            time_to_expiry_sec=1200,
        )
        assert result.fair_price == 0.6
        assert result.net_position == 25
        assert result.variance == 0.36
        assert result.time_to_expiry_years == 1200 / SECONDS_PER_YEAR
        assert result.risk_aversion == 0.03
        assert result.used_log_odds is False

    def test_inventory_adjustment_sign(self):
        """Inventory adjustment sign should match position direction."""
        pricer = ReservationPricer(risk_aversion=0.01)

        # Long → negative adjustment
        result_long = pricer.calculate(0.5, 50, 0.25, 600)
        assert result_long.inventory_adjustment < 0

        # Short → positive adjustment
        result_short = pricer.calculate(0.5, -50, 0.25, 600)
        assert result_short.inventory_adjustment > 0

        # Zero → zero adjustment
        result_flat = pricer.calculate(0.5, 0, 0.25, 600)
        assert result_flat.inventory_adjustment == 0.0


class TestReservationPriceComparison:
    """Compare A-S reservation price to simple inventory skew."""

    def test_as_is_more_time_sensitive(self):
        """A-S adjustment increases with time, simple skew does not."""
        pricer = ReservationPricer(risk_aversion=0.01)

        # Near expiry
        result_near = pricer.calculate(0.5, 50, 0.25, 60)
        # Far from expiry
        result_far = pricer.calculate(0.5, 50, 0.25, 3600)

        # A-S adjustment magnitude increases with time
        assert abs(result_far.inventory_adjustment) > abs(result_near.inventory_adjustment)

        # Simple inventory skew would be the same regardless of time
        # This is the key difference: A-S is time-aware

    def test_as_is_variance_aware(self):
        """A-S adjustment scales with variance, demonstrating risk sensitivity."""
        pricer = ReservationPricer(risk_aversion=0.01)

        result_low_vol = pricer.calculate(0.5, 50, 0.04, 600)  # σ=0.2
        result_high_vol = pricer.calculate(0.5, 50, 1.00, 600)  # σ=1.0

        # Higher vol → larger adjustment (more risky to hold inventory)
        assert abs(result_high_vol.inventory_adjustment) > abs(result_low_vol.inventory_adjustment)
