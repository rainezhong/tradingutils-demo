"""Tests for InventoryManager — position tracking, skew, quote gating."""

from strategies.prediction_mm.inventory_manager import InventoryManager
from strategies.prediction_mm.pricer import BinaryGreeks


class TestZeroPosition:
    def test_no_skew(self):
        """Flat position → zero skew."""
        mgr = InventoryManager(max_position_per_market=50)
        state = mgr.get_inventory_state("T")
        assert state.skew_vol_points == 0.0
        assert state.position_pct == 0.0

    def test_both_sides_quoted(self):
        """Flat → both bid and ask allowed."""
        mgr = InventoryManager(max_position_per_market=50)
        state = mgr.get_inventory_state("T")
        assert state.should_quote_bid is True
        assert state.should_quote_ask is True

    def test_equal_sizes(self):
        """Flat → bid_size == ask_size."""
        mgr = InventoryManager(max_position_per_market=50, base_quote_size=10)
        state = mgr.get_inventory_state("T")
        assert state.bid_size == state.ask_size == 10


class TestLongPosition:
    def test_negative_skew(self):
        """Long position → negative skew (lower prices to encourage selling)."""
        mgr = InventoryManager(max_position_per_market=50, max_skew_vol_points=0.05)
        mgr.on_fill("T", side_is_buy=True, size=25, price_cents=50)
        state = mgr.get_inventory_state("T")
        assert state.skew_vol_points < 0

    def test_ask_size_larger(self):
        """Long → ask_size >= bid_size (favor unwinding)."""
        mgr = InventoryManager(max_position_per_market=50, base_quote_size=10)
        mgr.on_fill("T", side_is_buy=True, size=25, price_cents=50)
        state = mgr.get_inventory_state("T")
        assert state.ask_size >= state.bid_size


class TestMaxPosition:
    def test_stops_quoting_bid_at_max_long(self):
        """At max long position, should stop quoting bid."""
        mgr = InventoryManager(max_position_per_market=50)
        mgr.on_fill("T", side_is_buy=True, size=50, price_cents=50)
        state = mgr.get_inventory_state("T")
        assert state.should_quote_bid is False
        assert state.should_quote_ask is True

    def test_stops_quoting_ask_at_max_short(self):
        """At max short position, should stop quoting ask."""
        mgr = InventoryManager(max_position_per_market=50)
        mgr.on_fill("T", side_is_buy=False, size=50, price_cents=50)
        state = mgr.get_inventory_state("T")
        assert state.should_quote_bid is True
        assert state.should_quote_ask is False


class TestFillTracking:
    def test_buy_increases_position(self):
        mgr = InventoryManager()
        mgr.on_fill("T", side_is_buy=True, size=10, price_cents=45)
        pos = mgr.get_position("T")
        assert pos.net_position == 10
        assert pos.avg_entry_cents == 45.0

    def test_sell_decreases_position(self):
        mgr = InventoryManager()
        mgr.on_fill("T", side_is_buy=True, size=10, price_cents=45)
        mgr.on_fill("T", side_is_buy=False, size=5, price_cents=50)
        pos = mgr.get_position("T")
        assert pos.net_position == 5
        assert pos.realized_pnl == 5 * (50 - 45)  # 25 cents profit

    def test_flip_position(self):
        mgr = InventoryManager()
        mgr.on_fill("T", side_is_buy=True, size=5, price_cents=40)
        mgr.on_fill("T", side_is_buy=False, size=10, price_cents=50)
        pos = mgr.get_position("T")
        assert pos.net_position == -5
        assert pos.realized_pnl == 5 * (50 - 40)

    def test_close_position(self):
        mgr = InventoryManager()
        mgr.on_fill("T", side_is_buy=True, size=10, price_cents=45)
        mgr.on_fill("T", side_is_buy=False, size=10, price_cents=55)
        pos = mgr.get_position("T")
        assert pos.net_position == 0
        assert pos.realized_pnl == 10 * (55 - 45)


class TestGreeksAggregation:
    def test_portfolio_delta(self):
        mgr = InventoryManager()
        mgr.on_fill("T1", side_is_buy=True, size=10, price_cents=50)
        mgr.on_fill("T2", side_is_buy=True, size=5, price_cents=50)
        mgr.update_greeks("T1", BinaryGreeks(delta=0.01, gamma=0, vega=0, theta=0))
        mgr.update_greeks("T2", BinaryGreeks(delta=0.02, gamma=0, vega=0, theta=0))
        assert abs(mgr.portfolio_delta() - (10 * 0.01 + 5 * 0.02)) < 1e-10

    def test_portfolio_delta_limits_quoting(self):
        """When portfolio delta exceeds max, stop quoting the accumulating side."""
        mgr = InventoryManager(max_portfolio_delta=1.0)
        mgr.on_fill("T", side_is_buy=True, size=100, price_cents=50)
        mgr.update_greeks("T", BinaryGreeks(delta=0.02, gamma=0, vega=0, theta=0))
        # portfolio delta = 100 * 0.02 = 2.0 > max 1.0
        state = mgr.get_inventory_state("T")
        assert state.should_quote_bid is False  # can't add more long
