"""Tests for QuoteEngine with A-S reservation price mode."""

from strategies.prediction_mm.pricer import BinaryBSPricer, MarketState
from strategies.prediction_mm.quote_engine import QuoteEngine


def _state(spot=100_000, strike=100_000, ttx=600):
    return MarketState(
        ticker="TEST", spot_price=spot, strike_price=strike, time_to_expiry_sec=ttx
    )


PRICER = BinaryBSPricer()


class TestReservationPriceMode:
    """Test QuoteEngine with use_reservation_price=True."""

    def test_reservation_mode_initialization(self):
        """QuoteEngine should initialize with reservation price enabled."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            use_reservation_price=True,
            risk_aversion=0.05,
        )
        assert engine._use_reservation is True
        assert engine._reservation_pricer is not None

    def test_no_position_same_as_standard(self):
        """With no position, A-S mode should produce same quotes as standard."""
        engine_standard = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=2,
            max_spread_cents=15,
            use_reservation_price=False,
        )
        engine_as = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=2,
            max_spread_cents=15,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state(spot=100_000, strike=100_000, ttx=600)
        q_standard = engine_standard.generate(state, sigma=0.5, net_position=0)
        q_as = engine_as.generate(state, sigma=0.5, net_position=0)

        # Should be identical when position = 0
        assert q_standard.bid_cents == q_as.bid_cents
        assert q_standard.ask_cents == q_as.ask_cents
        assert q_as.reservation_result is not None
        assert q_as.reservation_result.inventory_adjustment == 0.0

    def test_long_position_lowers_quotes(self):
        """Long position should lower both bid and ask in A-S mode."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=1,
            max_spread_cents=30,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state(spot=100_000, strike=100_000, ttx=600)
        q_flat = engine.generate(state, sigma=0.5, net_position=0)
        q_long = engine.generate(state, sigma=0.5, net_position=50)

        # Long position → reservation price < fair → quotes shift down
        assert q_long.reservation_result is not None
        assert q_long.reservation_result.reservation_price < q_long.fair_prob
        # At least one quote should be lower
        assert q_long.bid_cents <= q_flat.bid_cents or q_long.ask_cents <= q_flat.ask_cents

    def test_short_position_raises_quotes(self):
        """Short position should raise both bid and ask in A-S mode."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=1,
            max_spread_cents=30,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state(spot=100_000, strike=100_000, ttx=600)
        q_flat = engine.generate(state, sigma=0.5, net_position=0)
        q_short = engine.generate(state, sigma=0.5, net_position=-50)

        # Short position → reservation price > fair → quotes shift up
        assert q_short.reservation_result is not None
        assert q_short.reservation_result.reservation_price > q_short.fair_prob
        # At least one quote should be higher
        assert q_short.bid_cents >= q_flat.bid_cents or q_short.ask_cents >= q_flat.ask_cents

    def test_reservation_result_populated(self):
        """QuoteResult should include reservation_result when enabled."""
        engine = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.03,
        )

        state = _state()
        q = engine.generate(state, sigma=0.5, net_position=30)

        assert q.reservation_result is not None
        assert q.reservation_result.net_position == 30
        assert q.reservation_result.risk_aversion == 0.03
        assert q.reservation_result.used_log_odds is False
        assert q.reservation_result.fair_price == q.fair_prob

    def test_reservation_ignores_simple_skew(self):
        """When using A-S mode, inventory_skew parameter should be ignored."""
        engine = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state()
        # Pass both net_position and inventory_skew
        # A-S mode should use net_position and ignore inventory_skew
        q = engine.generate(
            state,
            sigma=0.5,
            net_position=50,
            inventory_skew=0.05,  # should be ignored
        )

        # inventory_skew in result should be 0 (overridden)
        assert q.inventory_skew == 0.0
        assert q.reservation_result is not None


class TestReservationPriceTimeDecay:
    """Test how A-S quotes change as expiry approaches."""

    def test_adjustment_decreases_near_expiry(self):
        """Inventory adjustment should decrease as time to expiry decreases."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        # Far from expiry
        state_far = _state(spot=100_000, strike=100_000, ttx=3600)
        q_far = engine.generate(state_far, sigma=0.5, net_position=50)

        # Near expiry
        state_near = _state(spot=100_000, strike=100_000, ttx=60)
        q_near = engine.generate(state_near, sigma=0.5, net_position=50)

        # Adjustment magnitude should be larger when far from expiry
        assert abs(q_far.reservation_result.inventory_adjustment) > abs(
            q_near.reservation_result.inventory_adjustment
        )

    def test_at_expiry_no_adjustment(self):
        """At expiry (T=0), reservation price should equal fair price."""
        engine = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state(spot=100_000, strike=100_000, ttx=0)
        q = engine.generate(state, sigma=0.5, net_position=50)

        assert q.reservation_result is not None
        assert q.reservation_result.inventory_adjustment == 0.0
        assert q.reservation_result.reservation_price == q.fair_prob


class TestReservationPriceRiskAversion:
    """Test effect of risk_aversion parameter."""

    def test_higher_gamma_larger_adjustment(self):
        """Higher risk aversion → larger inventory adjustment."""
        engine_low = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.01,
        )
        engine_high = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.1,
        )

        state = _state()
        q_low = engine_low.generate(state, sigma=0.5, net_position=50)
        q_high = engine_high.generate(state, sigma=0.5, net_position=50)

        assert abs(q_high.reservation_result.inventory_adjustment) > abs(
            q_low.reservation_result.inventory_adjustment
        )

    def test_gamma_affects_quote_spacing(self):
        """Higher risk aversion should produce more aggressive unwinding quotes."""
        engine_low = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=1,
            max_spread_cents=30,
            use_reservation_price=True,
            risk_aversion=0.01,
        )
        engine_high = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=1,
            max_spread_cents=30,
            use_reservation_price=True,
            risk_aversion=0.1,
        )

        # For long position, higher gamma → lower quotes (more eager to sell)
        state = _state()
        q_low = engine_low.generate(state, sigma=0.5, net_position=50)
        q_high = engine_high.generate(state, sigma=0.5, net_position=50)

        # high gamma reservation should be lower
        assert q_high.reservation_result.reservation_price < q_low.reservation_result.reservation_price


class TestReservationPriceLogOddsMode:
    """Test log-odds transformation in QuoteEngine."""

    def test_log_odds_mode_enabled(self):
        """QuoteEngine should support log-odds reservation pricing."""
        engine = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.05,
            reservation_use_log_odds=True,
        )

        state = _state()
        q = engine.generate(state, sigma=0.5, net_position=50)

        assert q.reservation_result is not None
        assert q.reservation_result.used_log_odds is True

    def test_log_odds_produces_valid_quotes(self):
        """Log-odds mode should produce valid quotes in [1, 99]."""
        engine = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.05,
            reservation_use_log_odds=True,
        )

        state = _state()
        q = engine.generate(state, sigma=0.5, net_position=100)

        assert 1 <= q.bid_cents <= 99
        assert 1 <= q.ask_cents <= 99
        assert q.bid_cents < q.ask_cents


class TestReservationPriceEdgeCases:
    """Test edge cases and robustness."""

    def test_extreme_position_clamping(self):
        """Very large positions should not crash, quotes clamped to [1, 99]."""
        engine = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=1.0,  # high gamma
        )

        state = _state()
        q = engine.generate(state, sigma=2.0, net_position=1000)

        assert 1 <= q.bid_cents <= 99
        assert 1 <= q.ask_cents <= 99

    def test_extreme_itm_market(self):
        """Deep ITM market should produce valid quotes with A-S."""
        engine = QuoteEngine(
            pricer=PRICER,
            min_spread_cents=1,
            max_spread_cents=30,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state(spot=110_000, strike=100_000)  # deep ITM
        q = engine.generate(state, sigma=0.5, net_position=50)

        assert 1 <= q.bid_cents <= 99
        assert 1 <= q.ask_cents <= 99
        assert q.bid_cents < q.ask_cents

    def test_extreme_otm_market(self):
        """Deep OTM market should produce valid quotes with A-S."""
        engine = QuoteEngine(
            pricer=PRICER,
            min_spread_cents=1,
            max_spread_cents=30,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state(spot=90_000, strike=100_000)  # deep OTM
        q = engine.generate(state, sigma=0.5, net_position=50)

        assert 1 <= q.bid_cents <= 99
        assert 1 <= q.ask_cents <= 99
        assert q.bid_cents < q.ask_cents


class TestReservationPriceVsSimpleSkew:
    """Compare A-S mode vs simple inventory skew."""

    def test_different_behavior_over_time(self):
        """A-S and simple skew should diverge as time to expiry changes."""
        engine_as = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            use_reservation_price=True,
            risk_aversion=0.05,
        )
        engine_simple = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            use_reservation_price=False,
        )

        # Simple skew: constant regardless of time
        simple_skew = -0.02  # for long position

        # Near expiry
        state_near = _state(ttx=60)
        q_as_near = engine_as.generate(state_near, sigma=0.5, net_position=50)
        q_simple_near = engine_simple.generate(
            state_near, sigma=0.5, inventory_skew=simple_skew
        )

        # Far from expiry
        state_far = _state(ttx=3600)
        q_as_far = engine_as.generate(state_far, sigma=0.5, net_position=50)
        q_simple_far = engine_simple.generate(
            state_far, sigma=0.5, inventory_skew=simple_skew
        )

        # A-S: adjustment increases with time
        as_adjustment_near = abs(q_as_near.reservation_result.inventory_adjustment)
        as_adjustment_far = abs(q_as_far.reservation_result.inventory_adjustment)
        assert as_adjustment_far > as_adjustment_near

        # Simple skew: same adjustment regardless of time
        # (inventory_skew is constant)
        assert q_simple_near.inventory_skew == q_simple_far.inventory_skew

    def test_variance_sensitivity(self):
        """A-S is variance-aware, simple skew is not."""
        engine_as = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=True,
            risk_aversion=0.05,
        )

        state = _state()
        q_low_vol = engine_as.generate(state, sigma=0.2, net_position=50)
        q_high_vol = engine_as.generate(state, sigma=1.0, net_position=50)

        # A-S adjustment should be larger with higher vol
        assert abs(q_high_vol.reservation_result.inventory_adjustment) > abs(
            q_low_vol.reservation_result.inventory_adjustment
        )


class TestReservationPriceBackwardCompatibility:
    """Ensure backward compatibility with existing QuoteEngine API."""

    def test_default_mode_unchanged(self):
        """Default QuoteEngine (no reservation price) should work as before."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=2,
            max_spread_cents=15,
        )

        state = _state()
        q = engine.generate(state, sigma=0.5, inventory_skew=0.02)

        assert q.bid_cents is not None
        assert q.ask_cents is not None
        assert q.inventory_skew == 0.02
        assert q.reservation_result is None  # not populated in standard mode

    def test_net_position_optional_in_standard_mode(self):
        """net_position parameter should be optional in standard mode."""
        engine = QuoteEngine(
            pricer=PRICER,
            use_reservation_price=False,
        )

        state = _state()
        # Should not raise error when net_position not provided
        q = engine.generate(state, sigma=0.5, inventory_skew=0.02)

        assert q.bid_cents is not None
        assert q.ask_cents is not None

    def test_existing_tests_still_pass(self):
        """Verify that adding A-S doesn't break existing test patterns."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.03,
            min_spread_cents=2,
            max_spread_cents=15,
        )

        state = _state()
        q = engine.generate(state, sigma=0.5)

        # Classic assertions still hold
        assert q.bid_cents < q.fair_cents or q.bid_cents == q.fair_cents
        assert q.ask_cents > q.fair_cents or q.ask_cents == q.fair_cents
        assert q.spread_cents >= 2
        assert q.spread_cents <= 15
        assert 1 <= q.bid_cents <= 99
        assert 1 <= q.ask_cents <= 99
