"""Tests for BinaryBSPricer — fair value, implied vol, Greeks."""

import math

from strategies.prediction_mm.pricer import (
    BinaryBSPricer,
    MarketState,
    _normal_cdf,
    _normal_pdf,
)

PRICER = BinaryBSPricer()


def _state(spot=100_000, strike=100_000, ttx=600, r=0.0):
    return MarketState(
        ticker="TEST",
        spot_price=spot,
        strike_price=strike,
        time_to_expiry_sec=ttx,
        risk_free_rate=r,
    )


# ------------------------------------------------------------------ #
# Normal distribution helpers
# ------------------------------------------------------------------ #


class TestNormalDistribution:
    def test_cdf_at_zero(self):
        assert abs(_normal_cdf(0) - 0.5) < 1e-6

    def test_cdf_large_positive(self):
        assert _normal_cdf(5) > 0.999

    def test_cdf_large_negative(self):
        assert _normal_cdf(-5) < 0.001

    def test_pdf_at_zero(self):
        expected = 1.0 / math.sqrt(2 * math.pi)
        assert abs(_normal_pdf(0) - expected) < 1e-6

    def test_pdf_symmetric(self):
        assert abs(_normal_pdf(1.5) - _normal_pdf(-1.5)) < 1e-10


# ------------------------------------------------------------------ #
# Fair value
# ------------------------------------------------------------------ #


class TestFairValue:
    def test_atm_near_50pct(self):
        """ATM binary should be close to 50%."""
        fv = PRICER.fair_value(_state(), vol=0.5)
        assert 0.40 < fv < 0.60

    def test_deep_itm(self):
        """Spot >> strike → prob close to 1."""
        fv = PRICER.fair_value(_state(spot=110_000, strike=100_000), vol=0.3)
        assert fv > 0.80

    def test_deep_otm(self):
        """Spot << strike → prob close to 0."""
        fv = PRICER.fair_value(_state(spot=90_000, strike=100_000), vol=0.3)
        assert fv < 0.20

    def test_at_expiry_itm(self):
        """At expiry, ITM → 1."""
        fv = PRICER.fair_value(_state(spot=100_001, strike=100_000, ttx=0), vol=0.5)
        assert fv == 1.0

    def test_at_expiry_otm(self):
        """At expiry, OTM → 0."""
        fv = PRICER.fair_value(_state(spot=99_999, strike=100_000, ttx=0), vol=0.5)
        assert fv == 0.0

    def test_higher_vol_brings_closer_to_50(self):
        """Higher vol → less certainty → closer to 50% for slightly ITM."""
        # Use spot close enough to strike that prices don't saturate at clamp
        fv_low = PRICER.fair_value(_state(spot=100_300), vol=0.3)
        fv_high = PRICER.fair_value(_state(spot=100_300), vol=0.8)
        assert abs(fv_high - 0.5) < abs(fv_low - 0.5)

    def test_clamped_output(self):
        """Result should be in [0.001, 0.999]."""
        fv = PRICER.fair_value(_state(spot=200_000, strike=100_000, ttx=600), vol=0.1)
        assert 0.001 <= fv <= 0.999

    def test_zero_vol_itm(self):
        """Zero vol, ITM → 1."""
        fv = PRICER.fair_value(_state(spot=101_000, strike=100_000), vol=0.0)
        assert fv == 1.0


# ------------------------------------------------------------------ #
# Implied vol roundtrip
# ------------------------------------------------------------------ #


class TestImpliedVol:
    def test_roundtrip_atm(self):
        """iv(fv(sigma)) ≈ sigma for ATM."""
        state = _state()
        vol_in = 0.45
        price = PRICER.fair_value(state, vol_in)
        vol_out = PRICER.implied_vol(price, state)
        assert vol_out is not None
        assert abs(vol_out - vol_in) < 0.01

    def test_roundtrip_itm(self):
        """iv roundtrip for slightly ITM (price not saturated at clamp)."""
        state = _state(spot=100_500, strike=100_000)
        vol_in = 0.80
        price = PRICER.fair_value(state, vol_in)
        vol_out = PRICER.implied_vol(price, state)
        assert vol_out is not None
        assert abs(vol_out - vol_in) < 0.01

    def test_roundtrip_otm(self):
        """iv roundtrip for slightly OTM."""
        state = _state(spot=99_500, strike=100_000)
        vol_in = 0.80
        price = PRICER.fair_value(state, vol_in)
        vol_out = PRICER.implied_vol(price, state)
        assert vol_out is not None
        assert abs(vol_out - vol_in) < 0.01

    def test_invalid_state_returns_none(self):
        state = _state(spot=0, ttx=0)
        assert PRICER.implied_vol(0.5, state) is None


# ------------------------------------------------------------------ #
# Greeks signs
# ------------------------------------------------------------------ #


class TestGreeks:
    def test_delta_positive_for_call(self):
        """Binary call delta > 0 (higher spot → higher prob)."""
        g = PRICER.greeks(_state(), vol=0.5)
        assert g.delta > 0

    def test_vega_sign_atm(self):
        """ATM binary call vega should be negative (higher vol pushes
        probability toward 50% from above 50% when S==K due to d2 formula)."""
        # For exactly ATM (S=K), d1 > 0, so vega = -n(d2)*d1/sigma < 0
        g = PRICER.greeks(_state(), vol=0.5)
        # Vega direction depends on d1 sign; for S=K, d1 = 0.5*sigma*sqrt(T) > 0
        assert g.vega < 0

    def test_theta_nonzero(self):
        """Theta should be nonzero for a non-expired option."""
        g = PRICER.greeks(_state(ttx=600), vol=0.5)
        assert g.theta != 0

    def test_greeks_zero_at_expiry(self):
        """At expiry, all Greeks → 0."""
        g = PRICER.greeks(_state(ttx=0), vol=0.5)
        assert g.delta == 0
        assert g.gamma == 0
        assert g.vega == 0
        assert g.theta == 0

    def test_gamma_atm_larger_than_otm(self):
        """Gamma is highest near ATM."""
        g_atm = PRICER.greeks(_state(spot=100_000), vol=0.5)
        g_otm = PRICER.greeks(_state(spot=90_000), vol=0.5)
        assert abs(g_atm.gamma) > abs(g_otm.gamma)
