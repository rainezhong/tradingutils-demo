"""Tests for MarketMaker strategy class."""

import pytest
from datetime import datetime, timezone

from src.market_making.config import MarketMakerConfig
from src.market_making.constants import SIDE_ASK, SIDE_BID, MIN_PRICE, MAX_PRICE
from src.market_making.models import Fill, MarketState
from src.market_maker import MarketMaker


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def config() -> MarketMakerConfig:
    """Standard test configuration."""
    return MarketMakerConfig(
        target_spread=0.04,
        edge_per_side=0.005,
        quote_size=20,
        max_position=50,
        inventory_skew_factor=0.01,
        min_spread_to_quote=0.02,
    )


@pytest.fixture
def market_maker(config: MarketMakerConfig) -> MarketMaker:
    """Standard market maker instance."""
    return MarketMaker("TEST-YES", config)


@pytest.fixture
def valid_market() -> MarketState:
    """Valid market state with wide spread."""
    return MarketState(
        ticker="TEST-YES",
        timestamp=datetime.now(timezone.utc),
        best_bid=0.45,
        best_ask=0.55,
        mid_price=0.50,
        bid_size=100,
        ask_size=100,
    )


@pytest.fixture
def tight_spread_market() -> MarketState:
    """Market with spread too tight to quote (< 2% min_spread_to_quote)."""
    # Spread = 0.004/0.50 = 0.8% which is < 2% min, so should NOT quote
    return MarketState(
        ticker="TEST-YES",
        timestamp=datetime.now(timezone.utc),
        best_bid=0.498,
        best_ask=0.502,
        mid_price=0.50,
        bid_size=100,
        ask_size=100,
    )


# =============================================================================
# Initialization Tests
# =============================================================================


class TestMarketMakerInit:
    """Tests for MarketMaker initialization."""

    def test_init_with_config(self, config: MarketMakerConfig):
        """Test initialization with explicit config."""
        mm = MarketMaker("TEST", config)
        assert mm.ticker == "TEST"
        assert mm.config == config
        assert mm.position.contracts == 0
        assert mm.position.avg_entry_price == 0.0

    def test_init_with_default_config(self):
        """Test initialization with default config."""
        mm = MarketMaker("TEST")
        assert mm.ticker == "TEST"
        assert mm.config is not None
        assert mm.config.target_spread == 0.04

    def test_init_empty_ticker_raises(self, config: MarketMakerConfig):
        """Test that empty ticker raises error."""
        with pytest.raises(ValueError, match="ticker cannot be empty"):
            MarketMaker("", config)


# =============================================================================
# should_quote Tests
# =============================================================================


class TestShouldQuote:
    """Tests for should_quote method."""

    def test_should_quote_valid_market(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test should_quote returns True for valid wide market."""
        assert market_maker.should_quote(valid_market) is True

    def test_should_not_quote_tight_spread(
        self, market_maker: MarketMaker, tight_spread_market: MarketState
    ):
        """Test should_quote returns False for tight spread."""
        # Spread is 0.02/0.50 = 4%, but market spread_pct calculation differs
        # Let's verify the actual spread_pct
        assert market_maker.should_quote(tight_spread_market) is False

    def test_should_not_quote_wrong_ticker(
        self, market_maker: MarketMaker
    ):
        """Test should_quote returns False for wrong ticker."""
        wrong_market = MarketState(
            ticker="WRONG",
            timestamp=datetime.now(timezone.utc),
            best_bid=0.45,
            best_ask=0.55,
            mid_price=0.50,
            bid_size=100,
            ask_size=100,
        )
        assert market_maker.should_quote(wrong_market) is False

    def test_should_quote_at_max_long(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test can still quote ask when at max long position."""
        market_maker.position.contracts = 50  # At max
        # Should still be True because can quote ask side
        assert market_maker.should_quote(valid_market) is True

    def test_should_quote_at_max_short(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test can still quote bid when at max short position."""
        market_maker.position.contracts = -50  # At max short
        # Should still be True because can quote bid side
        assert market_maker.should_quote(valid_market) is True


# =============================================================================
# calculate_fair_value Tests
# =============================================================================


class TestCalculateFairValue:
    """Tests for calculate_fair_value method."""

    def test_fair_value_equals_mid(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test fair value is mid price."""
        fv = market_maker.calculate_fair_value(valid_market)
        assert fv == valid_market.mid_price


# =============================================================================
# calculate_inventory_skew Tests
# =============================================================================


class TestCalculateInventorySkew:
    """Tests for calculate_inventory_skew method."""

    def test_skew_flat_position(self, market_maker: MarketMaker):
        """Test skew is zero for flat position."""
        assert market_maker.calculate_inventory_skew() == 0.0

    def test_skew_long_position(self, market_maker: MarketMaker):
        """Test skew is positive for long position."""
        market_maker.position.contracts = 25  # 50% of max
        skew = market_maker.calculate_inventory_skew()
        # 25/50 * 0.01 = 0.005
        assert skew == 0.005

    def test_skew_short_position(self, market_maker: MarketMaker):
        """Test skew is negative for short position."""
        market_maker.position.contracts = -25  # 50% of max short
        skew = market_maker.calculate_inventory_skew()
        # -25/50 * 0.01 = -0.005
        assert skew == -0.005

    def test_skew_at_max_position(self, market_maker: MarketMaker):
        """Test skew at maximum position."""
        market_maker.position.contracts = 50  # 100% of max
        skew = market_maker.calculate_inventory_skew()
        # 50/50 * 0.01 = 0.01
        assert skew == 0.01


# =============================================================================
# calculate_quote_prices Tests
# =============================================================================


class TestCalculateQuotePrices:
    """Tests for calculate_quote_prices method."""

    def test_quote_prices_flat_position(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test quote prices with no position."""
        bid, ask = market_maker.calculate_quote_prices(valid_market)

        # Fair value = 0.50
        # Half spread = 0.02
        # Edge = 0.005
        # Bid = 0.50 - 0.02 - 0.005 = 0.475
        # Ask = 0.50 + 0.02 + 0.005 = 0.525
        assert abs(bid - 0.475) < 0.001
        assert abs(ask - 0.525) < 0.001
        assert bid < ask

    def test_quote_prices_long_position(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test quote prices skew down when long."""
        market_maker.position.contracts = 25

        bid, ask = market_maker.calculate_quote_prices(valid_market)

        # Skew = 0.005 (positive, subtracted from both)
        # Bid = 0.475 - 0.005 = 0.470
        # Ask = 0.525 - 0.005 = 0.520
        assert abs(bid - 0.470) < 0.001
        assert abs(ask - 0.520) < 0.001

    def test_quote_prices_short_position(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test quote prices skew up when short."""
        market_maker.position.contracts = -25

        bid, ask = market_maker.calculate_quote_prices(valid_market)

        # Skew = -0.005 (negative, subtracted = adding)
        # Bid = 0.475 + 0.005 = 0.480
        # Ask = 0.525 + 0.005 = 0.530
        assert abs(bid - 0.480) < 0.001
        assert abs(ask - 0.530) < 0.001

    def test_quote_prices_clamped_to_range(
        self, config: MarketMakerConfig
    ):
        """Test quote prices are clamped to valid range."""
        mm = MarketMaker("TEST", config)

        # Create market near edge
        edge_market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            best_bid=0.02,
            best_ask=0.05,
            mid_price=0.035,
            bid_size=100,
            ask_size=100,
        )

        bid, ask = mm.calculate_quote_prices(edge_market)

        assert bid >= MIN_PRICE
        assert ask <= MAX_PRICE
        assert bid < ask


# =============================================================================
# calculate_quote_sizes Tests
# =============================================================================


class TestCalculateQuoteSizes:
    """Tests for calculate_quote_sizes method."""

    def test_sizes_flat_position(self, market_maker: MarketMaker):
        """Test equal sizes with no position."""
        bid_size, ask_size = market_maker.calculate_quote_sizes()
        assert bid_size == ask_size
        assert bid_size == 20  # Base size

    def test_sizes_long_position(self, market_maker: MarketMaker):
        """Test larger ask size when long."""
        market_maker.position.contracts = 25

        bid_size, ask_size = market_maker.calculate_quote_sizes()

        assert ask_size > bid_size  # Prefer selling

    def test_sizes_short_position(self, market_maker: MarketMaker):
        """Test larger bid size when short."""
        market_maker.position.contracts = -25

        bid_size, ask_size = market_maker.calculate_quote_sizes()

        assert bid_size > ask_size  # Prefer buying

    def test_sizes_decrease_with_utilization(self, market_maker: MarketMaker):
        """Test sizes decrease as position grows."""
        # Flat
        flat_bid, flat_ask = market_maker.calculate_quote_sizes()

        # 50% utilized
        market_maker.position.contracts = 25
        half_bid, half_ask = market_maker.calculate_quote_sizes()

        # Full
        market_maker.position.contracts = 50
        full_bid, full_ask = market_maker.calculate_quote_sizes()

        # Sizes should decrease
        assert flat_bid >= half_ask >= full_ask

    def test_sizes_minimum_one(self, market_maker: MarketMaker):
        """Test sizes are at least 1."""
        market_maker.position.contracts = 50  # Max utilization

        bid_size, ask_size = market_maker.calculate_quote_sizes()

        assert bid_size >= 1
        assert ask_size >= 1


# =============================================================================
# generate_quotes Tests
# =============================================================================


class TestGenerateQuotes:
    """Tests for generate_quotes method."""

    def test_generates_both_sides(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test generates both bid and ask quotes."""
        quotes = market_maker.generate_quotes(valid_market)

        assert len(quotes) == 2
        sides = {q.side for q in quotes}
        assert sides == {SIDE_BID, SIDE_ASK}

    def test_quote_properties(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test quote objects have correct properties."""
        quotes = market_maker.generate_quotes(valid_market)

        for quote in quotes:
            assert quote.ticker == "TEST-YES"
            assert quote.side in {SIDE_BID, SIDE_ASK}
            assert MIN_PRICE <= quote.price <= MAX_PRICE
            assert quote.size > 0
            assert quote.timestamp is not None

    def test_no_quotes_tight_spread(
        self, market_maker: MarketMaker, tight_spread_market: MarketState
    ):
        """Test no quotes generated for tight spread."""
        quotes = market_maker.generate_quotes(tight_spread_market)
        assert len(quotes) == 0

    def test_only_ask_at_max_long(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test only ask quote when at max long."""
        market_maker.position.contracts = 50

        quotes = market_maker.generate_quotes(valid_market)

        assert len(quotes) == 1
        assert quotes[0].side == SIDE_ASK

    def test_only_bid_at_max_short(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test only bid quote when at max short."""
        market_maker.position.contracts = -50

        quotes = market_maker.generate_quotes(valid_market)

        assert len(quotes) == 1
        assert quotes[0].side == SIDE_BID

    def test_updates_state_tracking(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test quote generation updates internal state."""
        assert market_maker._state.quotes_generated == 0

        market_maker.generate_quotes(valid_market)

        assert market_maker._state.quotes_generated == 2
        assert market_maker._state.last_quote_time is not None


# =============================================================================
# update_position Tests
# =============================================================================


class TestUpdatePosition:
    """Tests for update_position method."""

    def test_buy_from_flat(self, market_maker: MarketMaker):
        """Test buying from flat position."""
        fill = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_BID,
            price=0.45,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )

        market_maker.update_position(fill)

        assert market_maker.position.contracts == 20
        assert market_maker.position.avg_entry_price == 0.45

    def test_sell_from_flat(self, market_maker: MarketMaker):
        """Test selling from flat position (go short)."""
        fill = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_ASK,
            price=0.55,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )

        market_maker.update_position(fill)

        assert market_maker.position.contracts == -20
        assert market_maker.position.avg_entry_price == 0.55

    def test_add_to_long(self, market_maker: MarketMaker):
        """Test adding to long position."""
        # Initial position
        market_maker.position.contracts = 20
        market_maker.position.avg_entry_price = 0.40

        fill = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_BID,
            price=0.50,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )

        market_maker.update_position(fill)

        assert market_maker.position.contracts == 40
        # New avg: (20*0.40 + 20*0.50) / 40 = 0.45
        assert abs(market_maker.position.avg_entry_price - 0.45) < 0.001

    def test_close_long_position(self, market_maker: MarketMaker):
        """Test closing long position realizes P&L."""
        market_maker.position.contracts = 20
        market_maker.position.avg_entry_price = 0.40

        fill = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_ASK,
            price=0.50,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )

        market_maker.update_position(fill)

        assert market_maker.position.contracts == 0
        # Realized: 20 * (0.50 - 0.40) = 2.0
        assert abs(market_maker.position.realized_pnl - 2.0) < 0.001

    def test_close_short_position(self, market_maker: MarketMaker):
        """Test closing short position realizes P&L."""
        market_maker.position.contracts = -20
        market_maker.position.avg_entry_price = 0.60

        fill = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_BID,
            price=0.50,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )

        market_maker.update_position(fill)

        assert market_maker.position.contracts == 0
        # Realized: 20 * (0.60 - 0.50) = 2.0
        assert abs(market_maker.position.realized_pnl - 2.0) < 0.001

    def test_partial_close(self, market_maker: MarketMaker):
        """Test partial close of position."""
        market_maker.position.contracts = 40
        market_maker.position.avg_entry_price = 0.40

        fill = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_ASK,
            price=0.50,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )

        market_maker.update_position(fill)

        assert market_maker.position.contracts == 20
        # Realized: 20 * (0.50 - 0.40) = 2.0
        assert abs(market_maker.position.realized_pnl - 2.0) < 0.001
        # Avg entry unchanged for remaining
        assert market_maker.position.avg_entry_price == 0.40

    def test_flip_position(self, market_maker: MarketMaker):
        """Test flipping from long to short."""
        market_maker.position.contracts = 20
        market_maker.position.avg_entry_price = 0.40

        fill = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_ASK,
            price=0.50,
            size=40,  # Sell 40 (20 to close + 20 short)
            timestamp=datetime.now(timezone.utc),
        )

        market_maker.update_position(fill)

        assert market_maker.position.contracts == -20
        # Realized on closing 20: 20 * (0.50 - 0.40) = 2.0
        assert abs(market_maker.position.realized_pnl - 2.0) < 0.001
        # New short position at fill price
        assert market_maker.position.avg_entry_price == 0.50

    def test_wrong_ticker_raises(self, market_maker: MarketMaker):
        """Test wrong ticker raises error."""
        fill = Fill(
            order_id="ORD1",
            ticker="WRONG",
            side=SIDE_BID,
            price=0.50,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )

        with pytest.raises(ValueError, match="doesn't match"):
            market_maker.update_position(fill)


# =============================================================================
# calculate_unrealized_pnl Tests
# =============================================================================


class TestCalculateUnrealizedPnl:
    """Tests for calculate_unrealized_pnl method."""

    def test_unrealized_pnl_flat(self, market_maker: MarketMaker):
        """Test unrealized P&L is zero when flat."""
        pnl = market_maker.calculate_unrealized_pnl(0.50)
        assert pnl == 0.0

    def test_unrealized_pnl_long_profit(self, market_maker: MarketMaker):
        """Test unrealized profit on long position."""
        market_maker.position.contracts = 20
        market_maker.position.avg_entry_price = 0.40

        pnl = market_maker.calculate_unrealized_pnl(0.50)

        # 20 * (0.50 - 0.40) = 2.0
        assert pnl == 2.0
        assert market_maker.position.unrealized_pnl == 2.0

    def test_unrealized_pnl_long_loss(self, market_maker: MarketMaker):
        """Test unrealized loss on long position."""
        market_maker.position.contracts = 20
        market_maker.position.avg_entry_price = 0.50

        pnl = market_maker.calculate_unrealized_pnl(0.40)

        # 20 * (0.40 - 0.50) = -2.0
        assert pnl == -2.0

    def test_unrealized_pnl_short_profit(self, market_maker: MarketMaker):
        """Test unrealized profit on short position."""
        market_maker.position.contracts = -20
        market_maker.position.avg_entry_price = 0.50

        pnl = market_maker.calculate_unrealized_pnl(0.40)

        # -20 * (0.40 - 0.50) = 2.0
        assert pnl == 2.0


# =============================================================================
# get_status Tests
# =============================================================================


class TestGetStatus:
    """Tests for get_status method."""

    def test_status_has_required_fields(self, market_maker: MarketMaker):
        """Test status has all required fields."""
        status = market_maker.get_status()

        assert "ticker" in status
        assert "position" in status
        assert "utilization" in status
        assert "at_limit" in status
        assert "stats" in status
        assert "config" in status

    def test_status_with_market(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test status includes market data when provided."""
        status = market_maker.get_status(valid_market)

        assert "market" in status
        assert status["market"]["mid_price"] == 0.50

    def test_status_utilization(self, market_maker: MarketMaker):
        """Test utilization calculation."""
        market_maker.position.contracts = 25

        status = market_maker.get_status()

        assert status["utilization"] == 0.5
        assert status["at_limit"] is False

    def test_status_at_limit(self, market_maker: MarketMaker):
        """Test at_limit flag when at max position."""
        market_maker.position.contracts = 50

        status = market_maker.get_status()

        assert status["at_limit"] is True


# =============================================================================
# reset Tests
# =============================================================================


class TestReset:
    """Tests for reset method."""

    def test_reset_clears_position(self, market_maker: MarketMaker):
        """Test reset clears position."""
        market_maker.position.contracts = 50
        market_maker.position.avg_entry_price = 0.45
        market_maker.position.realized_pnl = 10.0

        market_maker.reset()

        assert market_maker.position.contracts == 0
        assert market_maker.position.avg_entry_price == 0.0
        assert market_maker.position.realized_pnl == 0.0

    def test_reset_clears_stats(
        self, market_maker: MarketMaker, valid_market: MarketState
    ):
        """Test reset clears stats."""
        market_maker.generate_quotes(valid_market)
        assert market_maker._state.quotes_generated > 0

        market_maker.reset()

        assert market_maker._state.quotes_generated == 0


# =============================================================================
# Integration Tests
# =============================================================================


class TestIntegration:
    """Integration tests for full workflows."""

    def test_full_trading_cycle(
        self, config: MarketMakerConfig, valid_market: MarketState
    ):
        """Test complete trading cycle."""
        mm = MarketMaker("TEST-YES", config)

        # Generate initial quotes
        quotes = mm.generate_quotes(valid_market)
        assert len(quotes) == 2

        bid_quote = next(q for q in quotes if q.side == SIDE_BID)
        ask_quote = next(q for q in quotes if q.side == SIDE_ASK)

        # Simulate bid fill
        fill1 = Fill(
            order_id="ORD1",
            ticker="TEST-YES",
            side=SIDE_BID,
            price=bid_quote.price,
            size=bid_quote.size,
            timestamp=datetime.now(timezone.utc),
        )
        mm.update_position(fill1)

        assert mm.position.contracts == bid_quote.size

        # Generate new quotes (should skew down due to long position)
        quotes2 = mm.generate_quotes(valid_market)
        bid2 = next(q for q in quotes2 if q.side == SIDE_BID)

        # Bid should be lower due to inventory skew
        assert bid2.price < bid_quote.price

        # Simulate ask fill to close
        fill2 = Fill(
            order_id="ORD2",
            ticker="TEST-YES",
            side=SIDE_ASK,
            price=ask_quote.price,
            size=bid_quote.size,
            timestamp=datetime.now(timezone.utc),
        )
        mm.update_position(fill2)

        assert mm.position.contracts == 0
        assert mm.position.realized_pnl > 0  # Should have profit

    def test_position_limit_respected(self, config: MarketMakerConfig):
        """Test position limits are respected through fills."""
        mm = MarketMaker("TEST", config)

        market = MarketState(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            best_bid=0.45,
            best_ask=0.55,
            mid_price=0.50,
            bid_size=100,
            ask_size=100,
        )

        # Fill up to max position
        for i in range(5):
            fill = Fill(
                order_id=f"ORD{i}",
                ticker="TEST",
                side=SIDE_BID,
                price=0.45,
                size=10,
                timestamp=datetime.now(timezone.utc),
            )
            mm.update_position(fill)

        assert mm.position.contracts == 50  # At max

        # Should only generate ask quote now
        quotes = mm.generate_quotes(market)
        assert len(quotes) == 1
        assert quotes[0].side == SIDE_ASK

    def test_pnl_tracking(self, config: MarketMakerConfig):
        """Test P&L is tracked correctly through trades."""
        mm = MarketMaker("TEST", config)

        # Buy at 0.40
        buy = Fill(
            order_id="ORD1",
            ticker="TEST",
            side=SIDE_BID,
            price=0.40,
            size=100,
            timestamp=datetime.now(timezone.utc),
        )
        mm.update_position(buy)

        # Check unrealized at higher price
        mm.calculate_unrealized_pnl(0.50)
        assert mm.position.unrealized_pnl == 10.0  # 100 * 0.10

        # Sell at 0.50
        sell = Fill(
            order_id="ORD2",
            ticker="TEST",
            side=SIDE_ASK,
            price=0.50,
            size=100,
            timestamp=datetime.now(timezone.utc),
        )
        mm.update_position(sell)

        # Realized P&L should be 10.0
        assert abs(mm.position.realized_pnl - 10.0) < 0.001
        assert mm.position.contracts == 0
