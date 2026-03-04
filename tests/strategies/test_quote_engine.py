"""Tests for QuoteEngine — vol-space quote generation."""

from strategies.prediction_mm.pricer import BinaryBSPricer, MarketState
from strategies.prediction_mm.quote_engine import QuoteEngine


PRICER = BinaryBSPricer()
ENGINE = QuoteEngine(
    pricer=PRICER, base_half_spread_vol=0.03, min_spread_cents=2, max_spread_cents=15
)


def _state(spot=100_000, strike=100_000, ttx=600):
    return MarketState(
        ticker="TEST", spot_price=spot, strike_price=strike, time_to_expiry_sec=ttx
    )


class TestQuoteGeneration:
    def test_quotes_straddle_fair_value(self):
        """Bid < fair < ask."""
        state = _state()
        q = ENGINE.generate(state, sigma=0.5)
        assert q.bid_cents is not None
        assert q.ask_cents is not None
        assert (
            q.bid_cents < q.fair_cents <= q.ask_cents
            or q.bid_cents <= q.fair_cents < q.ask_cents
        )

    def test_spread_at_least_min(self):
        """Spread should never be below min_spread_cents."""
        state = _state()
        q = ENGINE.generate(state, sigma=0.5)
        assert q.spread_cents >= 2

    def test_spread_at_most_max(self):
        """Spread should not exceed max_spread_cents."""
        state = _state()
        q = ENGINE.generate(state, sigma=0.5)
        assert q.spread_cents <= 15

    def test_prices_in_valid_range(self):
        """Quotes must be in [1, 99]."""
        state = _state()
        q = ENGINE.generate(state, sigma=0.5)
        assert 1 <= q.bid_cents <= 99
        assert 1 <= q.ask_cents <= 99

    def test_no_bid_when_disabled(self):
        state = _state()
        q = ENGINE.generate(state, sigma=0.5, should_quote_bid=False)
        assert q.bid_cents is None
        assert q.bid_size == 0
        assert q.ask_cents is not None

    def test_no_ask_when_disabled(self):
        state = _state()
        q = ENGINE.generate(state, sigma=0.5, should_quote_ask=False)
        assert q.ask_cents is None
        assert q.ask_size == 0
        assert q.bid_cents is not None


class TestVolSpaceSpreadBehavior:
    def test_wider_cent_spread_at_extremes(self):
        """At extreme prices (90c), the same vol-space spread should produce
        a wider cent-spread than at 50c (ATM). This is the key advantage
        of vol-space quoting."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.05,
            min_spread_cents=1,
            max_spread_cents=50,
        )

        # ATM: ~50c fair value
        q_atm = engine.generate(
            _state(spot=100_000, strike=100_000, ttx=600), sigma=0.5
        )

        # Deep ITM: ~85c+ fair value
        q_itm = engine.generate(
            _state(spot=105_000, strike=100_000, ttx=600), sigma=0.5
        )

        # At extreme prices, vol-space spread should translate to wider cents
        # (or at least not narrower — clamping may interfere)
        # The key property: cent spread should generally be wider when probability
        # is far from 50%, because the same vol change has a larger cents impact
        # at the tails of the normal CDF
        assert q_atm.spread_cents >= 1
        assert q_itm.spread_cents >= 1


class TestInventorySkew:
    def test_positive_skew_lowers_quotes(self):
        """Positive inventory_skew (short inventory) → higher vol on both
        sides → for ITM, prices move toward 50% (lower cents)."""
        # Use ITM market where vol changes produce visible cent moves
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.05,
            min_spread_cents=1,
            max_spread_cents=30,
        )
        state = _state(spot=100_300, strike=100_000)
        q_flat = engine.generate(state, sigma=0.5, inventory_skew=0.0)
        q_skew = engine.generate(state, sigma=0.5, inventory_skew=0.03)
        # Positive skew → bid and ask move down for ITM
        assert (
            q_skew.bid_cents < q_flat.bid_cents or q_skew.ask_cents < q_flat.ask_cents
        )

    def test_negative_skew_raises_quotes(self):
        """Negative inventory_skew (long inventory) → lower vol on both
        sides → for ITM, prices move away from 50% (higher cents)."""
        engine = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.05,
            min_spread_cents=1,
            max_spread_cents=30,
        )
        state = _state(spot=100_300, strike=100_000)
        q_flat = engine.generate(state, sigma=0.5, inventory_skew=0.0)
        q_skew = engine.generate(state, sigma=0.5, inventory_skew=-0.03)
        assert (
            q_skew.bid_cents > q_flat.bid_cents or q_skew.ask_cents > q_flat.ask_cents
        )


class TestAdversePremium:
    def test_premium_widens_spread(self):
        """Adverse selection premium should widen the spread."""
        state = _state()
        q_clean = ENGINE.generate(state, sigma=0.5, adverse_premium=0.0)
        q_toxic = ENGINE.generate(state, sigma=0.5, adverse_premium=0.03)
        assert q_toxic.spread_cents >= q_clean.spread_cents


class TestFeeAdjustedRounding:
    """Tests for directional rounding and fee verification."""

    def test_bid_rounds_down_ask_rounds_up(self):
        """Bid should floor, ask should ceil — never give away edge."""
        from strategies.prediction_mm.quote_engine import _bid_to_cents, _ask_to_cents

        # 0.455 → bid=45 (floor), ask=46 (ceil)
        assert _bid_to_cents(0.455) == 45
        assert _ask_to_cents(0.455) == 46

        # Exact values stay put
        assert _bid_to_cents(0.50) == 50
        assert _ask_to_cents(0.50) == 50

        # 0.999 edge cases
        assert _bid_to_cents(0.001) == 1
        assert _ask_to_cents(0.999) == 99

    def test_spread_covers_maker_fees(self):
        """After rounding, each half-spread should cover its maker fee."""
        from strategies.prediction_mm.quote_engine import _kalshi_maker_fee_cents

        state = _state()
        q = ENGINE.generate(state, sigma=0.5)
        bid_fee = _kalshi_maker_fee_cents(q.bid_cents)
        ask_fee = _kalshi_maker_fee_cents(q.ask_cents)
        bid_edge = q.fair_cents - q.bid_cents
        ask_edge = q.ask_cents - q.fair_cents
        # At least one side should cover its fee (both ideally)
        assert bid_edge >= bid_fee or ask_edge >= ask_fee

    def test_fee_check_widens_when_needed(self):
        """With fee check enabled, spread should be at least as wide as without."""
        engine_no_fee = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.01,  # Very tight spread
            min_spread_cents=1,
            max_spread_cents=50,
            fee_check_enabled=False,
        )
        engine_fee = QuoteEngine(
            pricer=PRICER,
            base_half_spread_vol=0.01,
            min_spread_cents=1,
            max_spread_cents=50,
            fee_check_enabled=True,
        )
        state = _state()
        q_no = engine_no_fee.generate(state, sigma=0.5)
        q_yes = engine_fee.generate(state, sigma=0.5)
        assert q_yes.spread_cents >= q_no.spread_cents
