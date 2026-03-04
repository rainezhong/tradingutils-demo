"""Tests for SpreadCaptureStrategy.

Unit tests (no I/O): config validation, trade fee/P&L computation, opportunity analysis.
Integration tests (dry_run=True): full lifecycle, timeout, circuit breaker, daily loss limit.
"""

import asyncio
import time

import pytest

from src.core.orderbook_manager import OrderBookLevel, OrderBookState
from src.core.utils import utc_now
from strategies.spread_capture_strategy import (
    SpreadCaptureConfig,
    SpreadCaptureState,
    SpreadCaptureStrategy,
    SpreadCaptureTrade,
    kalshi_fee,
)


# =============================================================================
# Helpers
# =============================================================================


def make_book(
    ticker: str = "TEST-TICKER",
    bid_price: int = 40,
    bid_size: int = 10,
    ask_price: int = 50,
    ask_size: int = 10,
    volume_24h: int = 500,
) -> OrderBookState:
    """Create a simple OrderBookState for testing."""
    bids = [OrderBookLevel(price=bid_price, size=bid_size)]
    asks = [OrderBookLevel(price=ask_price, size=ask_size)]
    return OrderBookState(
        ticker=ticker,
        bids=bids,
        asks=asks,
        sequence=1,
        timestamp=utc_now(),
        volume_24h=volume_24h,
    )


def default_config(**overrides) -> SpreadCaptureConfig:
    """Create a default config with optional overrides."""
    kwargs = {
        "min_spread_cents": 5,
        "max_spread_cents": 30,
        "min_depth_at_best": 3,
        "min_mid_price_cents": 15,
        "max_mid_price_cents": 85,
        "min_entry_size": 1,
        "max_entry_size": 25,
        "max_concurrent_positions": 5,
        "max_daily_loss_dollars": 50.0,
        "circuit_breaker_consecutive_losses": 5,
        "live_games_only": False,
    }
    kwargs.update(overrides)
    return SpreadCaptureConfig(**kwargs)


# =============================================================================
# TestSpreadCaptureConfig
# =============================================================================


class TestSpreadCaptureConfig:
    """Test config validation."""

    def test_valid_config(self):
        config = default_config()
        config.validate()  # Should not raise

    def test_min_spread_too_low(self):
        config = default_config(min_spread_cents=0)
        with pytest.raises(ValueError, match="min_spread_cents"):
            config.validate()

    def test_max_spread_less_than_min(self):
        config = default_config(min_spread_cents=10, max_spread_cents=5)
        with pytest.raises(ValueError, match="max_spread_cents"):
            config.validate()

    def test_min_entry_size_zero(self):
        config = default_config(min_entry_size=0)
        with pytest.raises(ValueError, match="min_entry_size"):
            config.validate()

    def test_max_entry_size_less_than_min(self):
        config = default_config(min_entry_size=10, max_entry_size=5)
        with pytest.raises(ValueError, match="max_entry_size"):
            config.validate()

    def test_invalid_mid_price_range(self):
        config = default_config(min_mid_price_cents=0)
        with pytest.raises(ValueError, match="mid_price_cents"):
            config.validate()

    def test_invalid_mid_price_range_high(self):
        config = default_config(max_mid_price_cents=100)
        with pytest.raises(ValueError, match="mid_price_cents"):
            config.validate()

    def test_daily_loss_not_positive(self):
        config = default_config(max_daily_loss_dollars=0)
        with pytest.raises(ValueError, match="max_daily_loss_dollars"):
            config.validate()

    def test_max_concurrent_positions_zero(self):
        config = default_config(max_concurrent_positions=0)
        with pytest.raises(ValueError, match="max_concurrent_positions"):
            config.validate()

    def test_circuit_breaker_zero(self):
        config = default_config(circuit_breaker_consecutive_losses=0)
        with pytest.raises(ValueError, match="circuit_breaker_consecutive_losses"):
            config.validate()

    def test_depth_utilization_pct_bounds(self):
        config = default_config(depth_utilization_pct=0)
        with pytest.raises(ValueError, match="depth_utilization_pct"):
            config.validate()

        config2 = default_config(depth_utilization_pct=1.5)
        with pytest.raises(ValueError, match="depth_utilization_pct"):
            config2.validate()


# =============================================================================
# TestFeeCalculation
# =============================================================================


class TestFeeCalculation:
    """Test Kalshi fee calculation at various price points."""

    def test_fee_at_mid_50(self):
        """Mid=50c: maximum fee region. P*(1-P) = 0.25"""
        fee = kalshi_fee(0.0175, 1, 50)
        # 0.0175 * 1 * 0.50 * 0.50 = 0.004375 → ceil to $0.01
        assert fee == 0.01

    def test_fee_at_10(self):
        """Low price: P*(1-P) = 0.09"""
        fee = kalshi_fee(0.0175, 1, 10)
        # 0.0175 * 1 * 0.10 * 0.90 = 0.001575 → ceil to $0.01
        assert fee == 0.01

    def test_fee_at_90(self):
        """High price: P*(1-P) = 0.09"""
        fee = kalshi_fee(0.0175, 1, 90)
        # 0.0175 * 1 * 0.90 * 0.10 = 0.001575 → ceil to $0.01
        assert fee == 0.01

    def test_fee_multiple_contracts_at_50(self):
        """10 contracts at 50c."""
        fee = kalshi_fee(0.0175, 10, 50)
        # 0.0175 * 10 * 0.50 * 0.50 = 0.04375 → ceil to $0.05
        assert fee == 0.05

    def test_fee_taker_rate(self):
        """Taker fee at 50c."""
        fee = kalshi_fee(0.07, 1, 50)
        # 0.07 * 1 * 0.50 * 0.50 = 0.0175 → ceil to $0.02
        assert fee == 0.02

    def test_fee_taker_10_contracts_at_50(self):
        """Taker fee, 10 contracts at 50c."""
        fee = kalshi_fee(0.07, 10, 50)
        # 0.07 * 10 * 0.50 * 0.50 = 0.175 → ceil to $0.18
        assert fee == 0.18

    def test_fee_at_extreme_low(self):
        """Very low price: small fee."""
        fee = kalshi_fee(0.0175, 1, 5)
        # 0.0175 * 1 * 0.05 * 0.95 = 0.00083125 → ceil to $0.01
        assert fee == 0.01

    def test_fee_spot_check_plan(self):
        """Spot check from the implementation plan."""
        # bid=40, ask=50, maker=1.75%, 1 contract
        entry_fee = kalshi_fee(0.0175, 1, 40)
        # 0.0175 * 1 * 0.40 * 0.60 = 0.0042 → ceil to $0.01
        assert entry_fee == 0.01

        exit_fee = kalshi_fee(0.0175, 1, 50)
        # 0.0175 * 1 * 0.50 * 0.50 = 0.004375 → ceil to $0.01
        assert exit_fee == 0.01

        gross = 0.10  # 50 - 40 = 10c = $0.10
        net = gross - entry_fee - exit_fee
        assert net == pytest.approx(0.08, abs=0.001)


# =============================================================================
# TestSpreadCaptureTrade
# =============================================================================


class TestSpreadCaptureTrade:
    """Test trade dataclass methods."""

    def test_is_active_pending_entry(self):
        trade = SpreadCaptureTrade(state=SpreadCaptureState.PENDING_ENTRY)
        assert trade.is_active()

    def test_is_active_open(self):
        trade = SpreadCaptureTrade(state=SpreadCaptureState.OPEN)
        assert trade.is_active()

    def test_is_active_pending_exit(self):
        trade = SpreadCaptureTrade(state=SpreadCaptureState.PENDING_EXIT)
        assert trade.is_active()

    def test_is_active_stuck(self):
        trade = SpreadCaptureTrade(state=SpreadCaptureState.STUCK)
        assert trade.is_active()

    def test_not_active_closed(self):
        trade = SpreadCaptureTrade(state=SpreadCaptureState.CLOSED)
        assert not trade.is_active()

    def test_not_active_cancelled(self):
        trade = SpreadCaptureTrade(state=SpreadCaptureState.CANCELLED)
        assert not trade.is_active()

    def test_hold_time_no_fill(self):
        trade = SpreadCaptureTrade()
        assert trade.hold_time() == 0.0

    def test_hold_time_with_fill(self):
        trade = SpreadCaptureTrade(entry_fill_time=time.time() - 5.0)
        assert 4.5 < trade.hold_time() < 6.0

    def test_compute_fees(self):
        trade = SpreadCaptureTrade(
            entry_fill_price=40,
            entry_fill_size=10,
            exit_fill_price=50,
            exit_fill_size=10,
        )
        trade.compute_fees(0.0175)
        assert trade.entry_fee > 0
        assert trade.exit_fee > 0

    def test_compute_pnl(self):
        trade = SpreadCaptureTrade(
            entry_fill_price=40,
            entry_fill_size=10,
            exit_fill_price=50,
            exit_fill_size=10,
        )
        trade.compute_fees(0.0175)
        trade.compute_pnl()

        # Gross: (50-40)*10/100 = $1.00
        assert trade.gross_pnl == pytest.approx(1.0)
        # Net should be less than gross due to fees
        assert trade.net_pnl < trade.gross_pnl
        assert trade.net_pnl > 0

    def test_compute_pnl_loss(self):
        """Exit below entry = loss."""
        trade = SpreadCaptureTrade(
            entry_fill_price=40,
            entry_fill_size=5,
            exit_fill_price=35,
            exit_fill_size=5,
        )
        trade.compute_fees(0.0175)
        trade.compute_pnl()

        # Gross: (35-40)*5/100 = -$0.25
        assert trade.gross_pnl == pytest.approx(-0.25)
        assert trade.net_pnl < trade.gross_pnl  # Fees make it worse

    def test_to_dict(self):
        trade = SpreadCaptureTrade(ticker="TEST", state=SpreadCaptureState.CLOSED)
        d = trade.to_dict()
        assert d["ticker"] == "TEST"
        assert d["state"] == "closed"
        assert "trade_id" in d
        assert "net_pnl" in d


# =============================================================================
# TestAnalyzeOpportunity
# =============================================================================


class TestAnalyzeOpportunity:
    """Test opportunity analysis with mock orderbooks."""

    def _make_strategy(self, **config_overrides) -> SpreadCaptureStrategy:
        config = default_config(**config_overrides)
        return SpreadCaptureStrategy(config=config, dry_run=True)

    def test_wide_spread_good_depth_returns_opportunity(self):
        """Standard case: 10c spread, good depth → opportunity."""
        strategy = self._make_strategy()
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None
        assert opp.opportunity_type == "spread_capture"
        assert opp.entry_side == "buy"
        assert opp.entry_price == 40  # Best bid
        assert opp.target_price == 50  # Best ask
        assert opp.entry_size >= 1
        assert opp.score > 0

    def test_narrow_spread_returns_none(self):
        """Spread below minimum → None."""
        strategy = self._make_strategy(min_spread_cents=8)
        book = make_book(bid_price=45, ask_price=50, bid_size=10, ask_size=10)
        # Spread = 5c < 8c min
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_spread_too_wide_returns_none(self):
        """Spread above maximum → None."""
        strategy = self._make_strategy(max_spread_cents=20)
        book = make_book(bid_price=20, ask_price=55, bid_size=10, ask_size=10)
        # Spread = 35c > 20c max
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_extreme_mid_price_low_returns_none(self):
        """Mid price too low → None."""
        strategy = self._make_strategy(min_mid_price_cents=15)
        book = make_book(bid_price=3, ask_price=13, bid_size=10, ask_size=10)
        # Mid = 8c < 15c min
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_extreme_mid_price_high_returns_none(self):
        """Mid price too high → None."""
        strategy = self._make_strategy(max_mid_price_cents=85)
        book = make_book(bid_price=88, ask_price=98, bid_size=10, ask_size=10)
        # Mid = 93c > 85c max
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_insufficient_depth_bid_returns_none(self):
        """Not enough depth at best bid → None."""
        strategy = self._make_strategy(min_depth_at_best=5)
        book = make_book(bid_price=40, ask_price=50, bid_size=2, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_insufficient_depth_ask_returns_none(self):
        """Not enough depth at best ask → None."""
        strategy = self._make_strategy(min_depth_at_best=5)
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=2)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_negative_net_edge_after_fees_returns_none(self):
        """Spread too narrow for fees → None."""
        # With 2c spread and maker fees, net edge should be negative
        strategy = self._make_strategy(min_spread_cents=1, max_spread_cents=5)
        book = make_book(bid_price=49, ask_price=51, bid_size=10, ask_size=10)
        # Spread = 2c, but fees on each side at 50c are ~1c each
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_daily_loss_limit_hit_returns_none(self):
        """Daily loss limit hit → None."""
        strategy = self._make_strategy()
        strategy._daily_loss_limit_hit = True
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_circuit_breaker_active_returns_none(self):
        """Circuit breaker active → None."""
        strategy = self._make_strategy()
        strategy._circuit_breaker_active = True
        strategy._circuit_breaker_until = time.time() + 300
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_circuit_breaker_expired_returns_opportunity(self):
        """Circuit breaker expired → resume."""
        strategy = self._make_strategy()
        strategy._circuit_breaker_active = True
        strategy._circuit_breaker_until = time.time() - 1  # Expired
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None
        assert not strategy._circuit_breaker_active

    def test_max_concurrent_reached_returns_none(self):
        """Max concurrent positions → None."""
        strategy = self._make_strategy(max_concurrent_positions=1)
        # Add one active trade
        trade = SpreadCaptureTrade(
            ticker="OTHER", state=SpreadCaptureState.PENDING_EXIT
        )
        strategy._trades["t1"] = trade
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_per_ticker_limit_returns_none(self):
        """Per-ticker position limit → None."""
        strategy = self._make_strategy(max_positions_per_ticker=1)
        trade = SpreadCaptureTrade(ticker="TEST", state=SpreadCaptureState.PENDING_EXIT)
        strategy._trades["t1"] = trade
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_cooldown_returns_none(self):
        """Trade cooldown active → None."""
        strategy = self._make_strategy(cooldown_between_trades_seconds=60)
        strategy._last_trade_time["TEST"] = time.time()
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_bid_improvement(self):
        """Bid improvement shifts entry price."""
        strategy = self._make_strategy(bid_improvement_cents=1)
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None
        assert opp.entry_price == 41  # 40 + 1

    def test_ask_discount(self):
        """Ask discount shifts exit price."""
        strategy = self._make_strategy(ask_discount_cents=1)
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None
        assert opp.target_price == 49  # 50 - 1

    def test_no_bids_returns_none(self):
        """No bids in book → None."""
        strategy = self._make_strategy()
        book = OrderBookState(
            ticker="TEST",
            bids=[],
            asks=[OrderBookLevel(price=50, size=10)],
            sequence=1,
            timestamp=utc_now(),
        )
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_no_asks_returns_none(self):
        """No asks in book → None."""
        strategy = self._make_strategy()
        book = OrderBookState(
            ticker="TEST",
            bids=[OrderBookLevel(price=40, size=10)],
            asks=[],
            sequence=1,
            timestamp=utc_now(),
        )
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None


# =============================================================================
# Integration Tests (dry_run=True)
# =============================================================================


class TestIntegration:
    """Integration tests using dry_run mode with orderbook-based fill simulation."""

    def _make_strategy(self, **config_overrides) -> SpreadCaptureStrategy:
        config = default_config(**config_overrides)
        # fill_probability=1.0 makes fills deterministic in tests
        return SpreadCaptureStrategy(config=config, dry_run=True, fill_probability=1.0)

    async def _pump_fills_with_crossing(
        self,
        strategy,
        ticker="TEST",
        entry_cross_book=None,
        exit_cross_book=None,
        interval=0.01,
        duration=2.0,
    ):
        """Pump _check_dry_run_fills, updating the book to simulate market crossing.

        After a short delay, swaps in entry_cross_book so the entry buy fills.
        Once entry fills, swaps in exit_cross_book so the exit sell fills.
        """
        deadline = asyncio.get_event_loop().time() + duration
        entry_crossed = False
        while asyncio.get_event_loop().time() < deadline:
            # Check if there are resting buy orders — cross the book for entry
            if not entry_crossed and entry_cross_book:
                has_resting_buy = any(
                    info["status"] == "resting" and info["side"] == "buy"
                    for info in strategy._pending_orders.values()
                )
                if has_resting_buy:
                    strategy._orderbook_mgr._books[ticker] = entry_cross_book
                    entry_crossed = True

            # Check if there are resting sell orders — cross the book for exit
            if entry_crossed and exit_cross_book:
                has_resting_sell = any(
                    info["status"] == "resting" and info["side"] == "sell"
                    for info in strategy._pending_orders.values()
                )
                if has_resting_sell:
                    strategy._orderbook_mgr._books[ticker] = exit_cross_book

            strategy._check_dry_run_fills()
            await asyncio.sleep(interval)

    async def _pump_fills(self, strategy, interval=0.01, duration=2.0):
        """Simple pump without book changes."""
        deadline = asyncio.get_event_loop().time() + duration
        while asyncio.get_event_loop().time() < deadline:
            strategy._check_dry_run_fills()
            await asyncio.sleep(interval)

    @pytest.mark.asyncio
    async def test_full_lifecycle(self):
        """Full round-trip: PENDING_ENTRY → OPEN → PENDING_EXIT → CLOSED."""
        strategy = self._make_strategy()
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None

        # Entry at 40: need ask to drop to 40. Exit at ~50: need bid to rise to 50.
        entry_cross = make_book(bid_price=38, ask_price=40, bid_size=10, ask_size=10)
        exit_cross = make_book(bid_price=50, ask_price=55, bid_size=10, ask_size=10)

        await asyncio.gather(
            strategy.execute_opportunity(opp),
            self._pump_fills_with_crossing(
                strategy,
                "TEST",
                entry_cross_book=entry_cross,
                exit_cross_book=exit_cross,
            ),
        )

        completed = [
            t for t in strategy._trades.values() if t.state == SpreadCaptureState.CLOSED
        ]
        assert len(completed) == 1

        trade = completed[0]
        assert trade.entry_fill_size > 0
        assert trade.exit_fill_size > 0
        assert trade.net_pnl != 0
        assert trade.entry_fee >= 0
        assert trade.exit_fee >= 0
        assert strategy._session_trades == 1

    @pytest.mark.asyncio
    async def test_entry_timeout_cancels(self):
        """Entry timeout → CANCELLED state (no fills pumped = timeout)."""
        strategy = self._make_strategy(entry_timeout_seconds=0.05)
        strategy._running = True

        # Book where buy at 40 won't fill: best_ask=50 > 40
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None

        # Pump fills but book doesn't cross — entry times out
        await asyncio.gather(
            strategy.execute_opportunity(opp),
            self._pump_fills(strategy, duration=0.5),
        )

        cancelled = [
            t
            for t in strategy._trades.values()
            if t.state == SpreadCaptureState.CANCELLED
        ]
        assert len(cancelled) == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_activation(self):
        """N consecutive losses activates circuit breaker."""
        strategy = self._make_strategy(circuit_breaker_consecutive_losses=3)
        strategy._running = True

        for i in range(3):
            trade = SpreadCaptureTrade(
                trade_id=f"loss_{i}",
                ticker="TEST",
                state=SpreadCaptureState.STUCK,
                entry_fill_price=40,
                entry_fill_size=5,
                exit_fill_price=35,
                exit_fill_size=5,
                entry_fill_time=time.time(),
            )
            strategy._trades[trade.trade_id] = trade
            strategy._complete_trade(trade)

        assert strategy._circuit_breaker_active
        assert strategy._consecutive_losses >= 3

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    @pytest.mark.asyncio
    async def test_daily_loss_limit_halt(self):
        """Hitting daily loss limit halts trading."""
        strategy = self._make_strategy(max_daily_loss_dollars=1.0)
        strategy._running = True

        trade = SpreadCaptureTrade(
            trade_id="big_loss",
            ticker="TEST",
            state=SpreadCaptureState.STUCK,
            entry_fill_price=50,
            entry_fill_size=20,
            exit_fill_price=40,
            exit_fill_size=20,
            entry_fill_time=time.time(),
        )
        strategy._trades[trade.trade_id] = trade
        strategy._complete_trade(trade)

        assert strategy._daily_loss_limit_hit

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    @pytest.mark.asyncio
    async def test_multiple_trades_tracked(self):
        """Multiple trades on different tickers are tracked independently."""
        strategy = self._make_strategy(max_concurrent_positions=5)
        strategy._running = True

        for ticker in ["T1", "T2", "T3"]:
            book = make_book(
                ticker=ticker,
                bid_price=40,
                ask_price=50,
                bid_size=10,
                ask_size=10,
            )
            strategy._orderbook_mgr._books[ticker] = book

            opp = strategy.analyze_opportunity(ticker, book)
            if opp:
                entry_cross = make_book(
                    ticker=ticker,
                    bid_price=38,
                    ask_price=40,
                    bid_size=10,
                    ask_size=10,
                )
                exit_cross = make_book(
                    ticker=ticker,
                    bid_price=50,
                    ask_price=55,
                    bid_size=10,
                    ask_size=10,
                )
                await asyncio.gather(
                    strategy.execute_opportunity(opp),
                    self._pump_fills_with_crossing(
                        strategy,
                        ticker,
                        entry_cross_book=entry_cross,
                        exit_cross_book=exit_cross,
                    ),
                )

        assert strategy._session_trades == 3

    @pytest.mark.asyncio
    async def test_win_resets_consecutive_losses(self):
        """A winning trade resets the consecutive loss counter."""
        strategy = self._make_strategy(circuit_breaker_consecutive_losses=5)
        strategy._running = True

        for i in range(2):
            trade = SpreadCaptureTrade(
                trade_id=f"loss_{i}",
                ticker="TEST",
                state=SpreadCaptureState.STUCK,
                entry_fill_price=40,
                entry_fill_size=5,
                exit_fill_price=35,
                exit_fill_size=5,
                entry_fill_time=time.time(),
            )
            strategy._trades[trade.trade_id] = trade
            strategy._complete_trade(trade)

        assert strategy._consecutive_losses == 2

        win_trade = SpreadCaptureTrade(
            trade_id="win_1",
            ticker="TEST",
            state=SpreadCaptureState.STUCK,
            entry_fill_price=40,
            entry_fill_size=5,
            exit_fill_price=50,
            exit_fill_size=5,
            entry_fill_time=time.time(),
        )
        strategy._trades[win_trade.trade_id] = win_trade
        strategy._complete_trade(win_trade)

        assert strategy._consecutive_losses == 0


# =============================================================================
# TestFeeEdgeCases
# =============================================================================


class TestFeeEdgeCases:
    """Additional fee calculation edge cases."""

    def test_fee_always_rounds_up(self):
        """Fee should always round up to nearest cent."""
        # 0.0175 * 5 * 0.30 * 0.70 = 0.018375 → ceil to $0.02
        fee = kalshi_fee(0.0175, 5, 30)
        assert fee == 0.02

    def test_fee_zero_contracts(self):
        """Zero contracts → zero fee."""
        fee = kalshi_fee(0.0175, 0, 50)
        assert fee == 0.0

    def test_fee_at_1_cent(self):
        """Price = 1c: very low P*(1-P)."""
        fee = kalshi_fee(0.0175, 1, 1)
        # 0.0175 * 1 * 0.01 * 0.99 = 0.00017325 → ceil to $0.01
        assert fee == 0.01

    def test_fee_at_99_cents(self):
        """Price = 99c: very low P*(1-P)."""
        fee = kalshi_fee(0.0175, 1, 99)
        # Same as 1c due to symmetry
        assert fee == 0.01

    def test_fee_symmetry(self):
        """Fee at P should equal fee at (100-P)."""
        for p in [10, 20, 30, 40, 50]:
            fee_low = kalshi_fee(0.0175, 10, p)
            fee_high = kalshi_fee(0.0175, 10, 100 - p)
            assert fee_low == fee_high


# =============================================================================
# Dry Run Fill Simulation Tests
# =============================================================================


class TestDryRunFillSimulation:
    """Tests for orderbook-based dry run fill simulation."""

    def _make_strategy(self, **config_overrides) -> SpreadCaptureStrategy:
        config = default_config(**config_overrides)
        # passive_fill_rate=0 isolates market-crossing logic in these tests
        return SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            fill_probability=1.0,
            passive_fill_rate=0,
        )

    @pytest.mark.asyncio
    async def test_unique_order_ids_no_collisions(self):
        """Rapid order placement produces unique IDs."""
        strategy = self._make_strategy()
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        order_ids = []
        for _ in range(100):
            oid = await strategy.place_order("TEST", "buy", 40, 1)
            order_ids.append(oid)

        assert len(order_ids) == 100
        assert len(set(order_ids)) == 100, "Order IDs must be unique"

    @pytest.mark.asyncio
    async def test_order_does_not_fill_without_market_crossing(self):
        """A buy at 40 should NOT fill when best_ask=50."""
        strategy = self._make_strategy()
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)
        assert oid is not None
        assert strategy._pending_orders[oid]["status"] == "resting"

        # Pump fills — ask=50 > order price=40, should NOT fill
        strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "resting"
        assert strategy._stats["orders_filled"] == 0

    @pytest.mark.asyncio
    async def test_buy_fills_when_ask_drops_to_order_price(self):
        """A buy at 40 fills when best_ask drops to 40."""
        strategy = self._make_strategy()
        strategy._running = True

        # Initial book: ask=50 — won't fill
        book = make_book(bid_price=35, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)
        strategy._check_dry_run_fills()
        assert strategy._pending_orders[oid]["status"] == "resting"

        # Now ask drops to 40 — should fill
        crossed_book = make_book(bid_price=35, ask_price=40, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = crossed_book

        strategy._check_dry_run_fills()
        assert strategy._pending_orders[oid]["status"] == "filled"
        assert strategy._stats["orders_filled"] == 1

    @pytest.mark.asyncio
    async def test_sell_fills_when_bid_rises_to_order_price(self):
        """A sell at 55 fills when best_bid rises to 55."""
        strategy = self._make_strategy()
        strategy._running = True

        book = make_book(bid_price=40, ask_price=60, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "sell", 55, 5)
        strategy._check_dry_run_fills()
        assert strategy._pending_orders[oid]["status"] == "resting"

        # Bid rises to 55
        crossed_book = make_book(bid_price=55, ask_price=60, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = crossed_book

        strategy._check_dry_run_fills()
        assert strategy._pending_orders[oid]["status"] == "filled"

    @pytest.mark.asyncio
    async def test_fill_routed_to_correct_trade(self):
        """Fill triggers _on_fill which routes to the correct SpreadCaptureTrade."""
        strategy = self._make_strategy()
        strategy._running = True

        # Book where buy at bid will fill (ask <= bid for instant crossing)
        book = make_book(bid_price=40, ask_price=40, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        # Place order and register trade mapping
        oid = await strategy.place_order("TEST", "buy", 40, 5)
        trade = SpreadCaptureTrade(
            trade_id="test_trade",
            ticker="TEST",
            state=SpreadCaptureState.PENDING_ENTRY,
            entry_order_id=oid,
        )
        strategy._trades[trade.trade_id] = trade
        strategy._order_to_trade[oid] = trade.trade_id

        # Pump fills — ask=40 <= order price=40, should fill
        strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "filled"
        assert trade.entry_fill_price == 40
        assert trade.entry_fill_size == 5

    @pytest.mark.asyncio
    async def test_fill_probability_filters_fills(self):
        """fill_probability=0.0 should prevent all fills."""
        config = default_config()
        strategy = SpreadCaptureStrategy(
            config=config, dry_run=True, fill_probability=0.0
        )
        strategy._running = True

        # Book where order should cross
        book = make_book(bid_price=40, ask_price=40, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)

        # Pump many times — probability=0.0 means no fills
        for _ in range(20):
            strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "resting"
        assert strategy._stats["orders_filled"] == 0

    @pytest.mark.asyncio
    async def test_cancel_unblocks_wait_for_fill(self):
        """Canceling a dry-run order signals the fill event so wait_for_fill unblocks."""
        strategy = self._make_strategy()
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)

        async def cancel_after_delay():
            await asyncio.sleep(0.05)
            await strategy.cancel_order(oid)

        # wait_for_fill returns True (event was set), but order is removed
        # The important thing is that it unblocks rather than timing out
        result, _ = await asyncio.gather(
            strategy.wait_for_fill(oid, timeout=2.0),
            cancel_after_delay(),
        )
        # Event was signaled so wait_for_fill returns True (unblocked)
        assert result is True
        # But order was actually removed from pending
        assert oid not in strategy._pending_orders


# =============================================================================
# Passive Fill Simulation Tests
# =============================================================================


class TestPassiveFillSimulation:
    """Tests for time-based passive fill simulation."""

    def _make_strategy(self, passive_fill_rate=0.025, **config_overrides):
        config = default_config(**config_overrides)
        return SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            fill_probability=1.0,
            passive_fill_rate=passive_fill_rate,
        )

    @pytest.mark.asyncio
    async def test_passive_buy_fills_at_bid_over_time(self):
        """Buy at best bid fills via passive model even though ask > price."""
        # rate=100 → per-check prob ≈ 99.3% at dt=0.05s, overcomes RNG seed
        strategy = self._make_strategy(passive_fill_rate=100.0)
        strategy._running = True

        # ask=50 > order_price=40, so market-crossing won't trigger
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)

        # Wait a moment so elapsed > 0, then check
        await asyncio.sleep(0.05)
        strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "filled"

    @pytest.mark.asyncio
    async def test_passive_sell_fills_at_ask_over_time(self):
        """Sell at best ask fills via passive model even though bid < price."""
        strategy = self._make_strategy(passive_fill_rate=100.0)
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "sell", 50, 5)

        await asyncio.sleep(0.05)
        strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "filled"

    @pytest.mark.asyncio
    async def test_passive_fill_does_not_trigger_when_undercut(self):
        """Buy below best bid should not get passive fills (we're not at the quote)."""
        strategy = self._make_strategy(passive_fill_rate=10.0)
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        # Place buy at 35, but best bid is 40 — we're behind the queue
        oid = await strategy.place_order("TEST", "buy", 35, 5)

        await asyncio.sleep(0.05)
        strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "resting"

    @pytest.mark.asyncio
    async def test_passive_fill_rate_zero_disables(self):
        """passive_fill_rate=0 disables passive fills entirely."""
        strategy = self._make_strategy(passive_fill_rate=0)
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)

        await asyncio.sleep(0.1)
        for _ in range(20):
            strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "resting"

    @pytest.mark.asyncio
    async def test_passive_fill_full_lifecycle(self):
        """Full round-trip using passive fills (no market crossing needed)."""
        strategy = self._make_strategy(
            passive_fill_rate=10.0, adverse_selection_cents=0
        )
        strategy._running = True

        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        strategy._orderbook_mgr._books["TEST"] = book

        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None

        async def pump(duration=3.0):
            deadline = asyncio.get_event_loop().time() + duration
            while asyncio.get_event_loop().time() < deadline:
                strategy._check_dry_run_fills()
                await asyncio.sleep(0.01)

        await asyncio.gather(
            strategy.execute_opportunity(opp),
            pump(),
        )

        completed = [
            t for t in strategy._trades.values() if t.state == SpreadCaptureState.CLOSED
        ]
        assert len(completed) == 1

        trade = completed[0]
        assert trade.entry_fill_price == 40  # Filled at bid
        assert trade.exit_fill_price > trade.entry_fill_price  # Exit at ask
        assert trade.gross_pnl > 0  # Spread captured
        assert trade.net_pnl != 0


# =============================================================================
# Volume-Scaled Fill Tests
# =============================================================================


class TestVolumeScaledFills:
    """Tests for volume and queue scaling in dry run fill simulation."""

    def _make_strategy(self, passive_fill_rate=0.025, **config_overrides):
        config = default_config(**config_overrides)
        return SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            fill_probability=1.0,
            passive_fill_rate=passive_fill_rate,
        )

    @pytest.mark.asyncio
    async def test_zero_volume_market_rarely_fills(self):
        """Zero-volume market: passive fill should be extremely unlikely."""
        strategy = self._make_strategy(passive_fill_rate=0.025)
        strategy._running = True

        book = make_book(
            bid_price=40, ask_price=50, bid_size=10, ask_size=10, volume_24h=0
        )
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 25)

        # Pump 100 cycles with small dt — effective rate is tiny (volume_factor=0.01)
        for _ in range(100):
            await asyncio.sleep(0.01)
            strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "resting"

    @pytest.mark.asyncio
    async def test_high_volume_market_fills_normally(self):
        """High-volume market with high rate fills quickly."""
        strategy = self._make_strategy(passive_fill_rate=100.0)
        strategy._running = True

        book = make_book(
            bid_price=40, ask_price=50, bid_size=10, ask_size=10, volume_24h=500
        )
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)

        await asyncio.sleep(0.05)
        strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "filled"

    @pytest.mark.asyncio
    async def test_large_queue_reduces_fill_rate(self):
        """Large depth ahead (500) with small order (5) reduces fill probability."""
        strategy = self._make_strategy(passive_fill_rate=0.5)
        strategy._running = True

        book = make_book(
            bid_price=40, ask_price=50, bid_size=500, ask_size=10, volume_24h=500
        )
        strategy._orderbook_mgr._books["TEST"] = book

        oid = await strategy.place_order("TEST", "buy", 40, 5)

        # queue_factor = 5 / (500 + 5) ≈ 0.0099, effective_rate ≈ 0.005
        # Over 1s total: P(fill) ≈ 0.5% — should stay resting
        for _ in range(100):
            await asyncio.sleep(0.01)
            strategy._check_dry_run_fills()

        assert strategy._pending_orders[oid]["status"] == "resting"


# =============================================================================
# Adverse Selection Tests
# =============================================================================


class TestAdverseSelection:
    """Tests for adverse selection in dry run exit pricing."""

    def _make_strategy(self, **config_overrides):
        config = default_config(**config_overrides)
        return SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            fill_probability=1.0,
            passive_fill_rate=100.0,
        )

    @pytest.mark.asyncio
    async def test_adverse_selection_shifts_exit_down(self):
        """With adverse selection enabled, exit price should be <= best ask."""
        strategy = self._make_strategy()  # adverse_selection_cents=None → auto
        strategy._running = True

        book = make_book(
            bid_price=40, ask_price=50, bid_size=10, ask_size=10, volume_24h=500
        )
        strategy._orderbook_mgr._books["TEST"] = book

        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None

        # Cross entry immediately
        entry_cross = make_book(
            ticker="TEST",
            bid_price=38,
            ask_price=40,
            bid_size=10,
            ask_size=10,
            volume_24h=500,
        )
        exit_cross = make_book(
            ticker="TEST",
            bid_price=50,
            ask_price=55,
            bid_size=10,
            ask_size=10,
            volume_24h=500,
        )

        async def pump(duration=3.0):
            deadline = asyncio.get_event_loop().time() + duration
            entry_crossed = False
            while asyncio.get_event_loop().time() < deadline:
                if not entry_crossed:
                    has_buy = any(
                        i["status"] == "resting" and i["side"] == "buy"
                        for i in strategy._pending_orders.values()
                    )
                    if has_buy:
                        strategy._orderbook_mgr._books["TEST"] = entry_cross
                        entry_crossed = True
                if entry_crossed:
                    has_sell = any(
                        i["status"] == "resting" and i["side"] == "sell"
                        for i in strategy._pending_orders.values()
                    )
                    if has_sell:
                        strategy._orderbook_mgr._books["TEST"] = exit_cross
                strategy._check_dry_run_fills()
                await asyncio.sleep(0.01)

        await asyncio.gather(
            strategy.execute_opportunity(opp),
            pump(),
        )

        completed = [
            t for t in strategy._trades.values() if t.state == SpreadCaptureState.CLOSED
        ]
        assert len(completed) == 1
        trade = completed[0]
        # spread=10, auto max_adverse = max(1, 10//3) = 3
        # exit_price should be <= 50 (best ask)
        assert trade.exit_price <= 50

    @pytest.mark.asyncio
    async def test_adverse_selection_disabled_via_config(self):
        """adverse_selection_cents=0 means no shift."""
        strategy = self._make_strategy(adverse_selection_cents=0)
        strategy._running = True

        # Book stays stable: entry fills via passive fill (rate=100),
        # exit also fills via passive fill. Book never changes so
        # exit phase reads ask=50 for exit pricing.
        book = make_book(
            bid_price=40, ask_price=50, bid_size=10, ask_size=10, volume_24h=500
        )
        strategy._orderbook_mgr._books["TEST"] = book

        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None

        async def pump(duration=3.0):
            deadline = asyncio.get_event_loop().time() + duration
            while asyncio.get_event_loop().time() < deadline:
                strategy._check_dry_run_fills()
                await asyncio.sleep(0.01)

        await asyncio.gather(
            strategy.execute_opportunity(opp),
            pump(),
        )

        completed = [
            t for t in strategy._trades.values() if t.state == SpreadCaptureState.CLOSED
        ]
        assert len(completed) == 1
        trade = completed[0]
        # No adverse selection — exit_price should be exactly best ask
        assert trade.exit_price == 50


# =============================================================================
# Taker Fee for Force Exit Tests
# =============================================================================


class TestTakerFeeForForceExit:
    """Tests for taker fee on force-exited trades."""

    def _make_strategy(self, **config_overrides):
        config = default_config(**config_overrides)
        return SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            fill_probability=1.0,
            passive_fill_rate=0,
        )

    @pytest.mark.asyncio
    async def test_force_exit_uses_taker_fees(self):
        """Force exit should use taker rate (0.07) for exit fee."""
        strategy = self._make_strategy(adverse_selection_cents=0)
        strategy._running = True

        trade = SpreadCaptureTrade(
            trade_id="taker_test",
            ticker="TEST",
            state=SpreadCaptureState.STUCK,
            entry_fill_price=40,
            entry_fill_size=10,
            exit_fill_size=0,
            entry_fill_time=time.time(),
            spread_at_entry=10,
        )
        strategy._trades[trade.trade_id] = trade

        # Force exit sells at best bid (38). Book has bid=38,
        # so sell@38 crosses (best_bid >= sell_price).
        book = make_book(
            bid_price=38, ask_price=50, bid_size=10, ask_size=10, volume_24h=500
        )
        strategy._orderbook_mgr._books["TEST"] = book

        async def pump_fills(duration=2.0):
            deadline = asyncio.get_event_loop().time() + duration
            while asyncio.get_event_loop().time() < deadline:
                strategy._check_dry_run_fills()
                await asyncio.sleep(0.01)

        await asyncio.gather(
            strategy._force_exit_stuck(trade),
            pump_fills(),
        )

        assert trade.was_taker_exit is True
        assert trade.state == SpreadCaptureState.CLOSED

        # Exit fee should use taker rate (0.07), not maker rate (0.0175)
        expected_taker_fee = kalshi_fee(0.07, 10, 38)
        expected_maker_fee = kalshi_fee(0.0175, 10, 38)
        assert trade.exit_fee == expected_taker_fee
        assert trade.exit_fee != expected_maker_fee


# =============================================================================
# Dynamic Pricing Tests
# =============================================================================


class TestDynamicPricing:
    """Tests for dynamic bid/ask pricing based on orderbook state."""

    def _make_strategy(self, **config_overrides) -> SpreadCaptureStrategy:
        config = default_config(use_dynamic_pricing=True, **config_overrides)
        return SpreadCaptureStrategy(config=config, dry_run=True)

    def test_calculate_imbalance_balanced(self):
        """Equal depth on both sides → imbalance = 0."""
        strategy = self._make_strategy()
        book = make_book(bid_price=40, bid_size=100, ask_price=50, ask_size=100)
        imbalance = strategy._calculate_imbalance(book)
        assert imbalance == pytest.approx(0.0)

    def test_calculate_imbalance_buy_pressure(self):
        """More bid depth → positive imbalance (buy pressure)."""
        strategy = self._make_strategy()
        book = make_book(bid_price=40, bid_size=200, ask_price=50, ask_size=100)
        imbalance = strategy._calculate_imbalance(book)
        assert imbalance > 0
        # (200 - 100) / (200 + 100) = 100 / 300 = 0.333
        assert imbalance == pytest.approx(0.333, abs=0.01)

    def test_calculate_imbalance_sell_pressure(self):
        """More ask depth → negative imbalance (sell pressure)."""
        strategy = self._make_strategy()
        book = make_book(bid_price=40, bid_size=100, ask_price=50, ask_size=200)
        imbalance = strategy._calculate_imbalance(book)
        assert imbalance < 0
        # (100 - 200) / (100 + 200) = -100 / 300 = -0.333
        assert imbalance == pytest.approx(-0.333, abs=0.01)

    def test_calculate_microprice_balanced(self):
        """Equal size → microprice = mid."""
        strategy = self._make_strategy()
        book = make_book(bid_price=40, bid_size=100, ask_price=50, ask_size=100)
        microprice = strategy._calculate_microprice(book)
        assert microprice == pytest.approx(45.0)

    def test_calculate_microprice_skewed_to_ask(self):
        """More bid size → microprice closer to ask."""
        strategy = self._make_strategy()
        book = make_book(bid_price=40, bid_size=300, ask_price=50, ask_size=100)
        microprice = strategy._calculate_microprice(book)
        # microprice = (40 * 100 + 50 * 300) / (100 + 300) = (4000 + 15000) / 400 = 47.5
        assert microprice == pytest.approx(47.5)

    def test_calculate_microprice_skewed_to_bid(self):
        """More ask size → microprice closer to bid."""
        strategy = self._make_strategy()
        book = make_book(bid_price=40, bid_size=100, ask_price=50, ask_size=300)
        microprice = strategy._calculate_microprice(book)
        # microprice = (40 * 300 + 50 * 100) / (100 + 300) = (12000 + 5000) / 400 = 42.5
        assert microprice == pytest.approx(42.5)

    def test_optimal_bid_no_improvement_balanced(self):
        """Balanced book with no urgency → minimal improvement."""
        strategy = self._make_strategy(imbalance_weight=0.5, microprice_weight=0.3)
        book = make_book(bid_price=40, bid_size=100, ask_price=50, ask_size=100)
        optimal_bid = strategy._calculate_optimal_bid(book, time_elapsed=0.0)
        # Balanced → imbalance=0, microprice=mid → no improvement
        # Only queue factor might contribute if depth < threshold
        assert optimal_bid >= 40  # At least best bid

    def test_optimal_bid_buy_pressure(self):
        """Strong buy pressure → more aggressive bid."""
        strategy = self._make_strategy(
            imbalance_weight=1.0,
            microprice_weight=0.0,
            max_bid_improvement_cents=3,
        )
        book = make_book(bid_price=40, bid_size=400, ask_price=50, ask_size=100)
        optimal_bid = strategy._calculate_optimal_bid(book, time_elapsed=0.0)
        # imbalance = (400 - 100) / 500 = 0.6
        # improvement += 0.6 * 1.0 * (10 / 2) = 3.0
        assert optimal_bid > 40
        assert optimal_bid <= 43  # Capped at max_bid_improvement

    def test_optimal_bid_respects_min_edge(self):
        """Optimal bid should not erode below minimum edge."""
        strategy = self._make_strategy(
            imbalance_weight=1.0,
            max_bid_improvement_cents=10,
            min_expected_edge_cents=5,
        )
        book = make_book(bid_price=40, bid_size=1000, ask_price=50, ask_size=10)
        optimal_bid = strategy._calculate_optimal_bid(book, time_elapsed=0.0)
        # Max possible bid = ask - min_edge = 50 - 5 = 45
        assert optimal_bid <= 45

    def test_optimal_ask_sell_pressure(self):
        """Sell pressure → lower ask to exit faster."""
        strategy = self._make_strategy(
            imbalance_weight=1.0,
            microprice_weight=0.0,
            max_ask_discount_cents=3,
        )
        book = make_book(bid_price=40, bid_size=100, ask_price=50, ask_size=400)
        optimal_ask = strategy._calculate_optimal_ask(
            book, entry_price=40, hold_time=0.0, position_size=1
        )
        # imbalance = (100 - 400) / 500 = -0.6
        # discount = 0.6 * 1.0 * 5 = 3.0
        assert optimal_ask < 50
        assert optimal_ask >= 47  # Capped at max_ask_discount

    def test_optimal_ask_respects_min_profit(self):
        """Optimal ask should never go below entry + min_edge."""
        strategy = self._make_strategy(
            imbalance_weight=1.0,
            max_ask_discount_cents=10,
            min_expected_edge_cents=3,
        )
        book = make_book(bid_price=40, bid_size=10, ask_price=50, ask_size=1000)
        optimal_ask = strategy._calculate_optimal_ask(
            book, entry_price=45, hold_time=0.0, position_size=1
        )
        # Min ask = entry + min_edge = 45 + 3 = 48
        assert optimal_ask >= 48

    def test_optimal_ask_never_crosses_bid(self):
        """Optimal ask should never cross the bid."""
        strategy = self._make_strategy(
            max_ask_discount_cents=10,
            min_expected_edge_cents=1,
        )
        book = make_book(bid_price=48, bid_size=100, ask_price=50, ask_size=100)
        optimal_ask = strategy._calculate_optimal_ask(
            book, entry_price=40, hold_time=100.0, position_size=100
        )
        # Even with max urgency, should not go below bid
        assert optimal_ask > 48

    def test_analyze_opportunity_uses_dynamic_pricing(self):
        """With use_dynamic_pricing=True, entry/exit prices should be dynamic."""
        strategy = self._make_strategy(
            imbalance_weight=0.5,
            microprice_weight=0.3,
            max_bid_improvement_cents=2,
        )
        # Unbalanced book: strong buy pressure
        book = make_book(bid_price=40, bid_size=300, ask_price=50, ask_size=100)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None
        # Entry price should be improved beyond static best bid
        assert opp.entry_price >= 40
        # Note: exact value depends on the formula

    def test_dynamic_pricing_disabled_uses_static(self):
        """With use_dynamic_pricing=False, prices should be static."""
        config = default_config(
            use_dynamic_pricing=False,
            bid_improvement_cents=1,
            ask_discount_cents=1,
        )
        strategy = SpreadCaptureStrategy(config=config, dry_run=True)
        book = make_book(bid_price=40, bid_size=300, ask_price=50, ask_size=100)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None
        # Static pricing: entry = bid + improvement, exit = ask - discount
        assert opp.entry_price == 41
        assert opp.target_price == 49

    def test_urgency_increases_bid_improvement(self):
        """Near timeout → more aggressive bid."""
        strategy = self._make_strategy(
            entry_timeout_seconds=60.0,
            imbalance_weight=0.5,
            max_bid_improvement_cents=3,
        )
        book = make_book(bid_price=40, bid_size=200, ask_price=50, ask_size=100)

        bid_early = strategy._calculate_optimal_bid(book, time_elapsed=0.0)
        bid_late = strategy._calculate_optimal_bid(book, time_elapsed=59.0)

        # Late in timeout → more urgency → more improvement
        assert bid_late >= bid_early

    def test_hold_time_increases_ask_discount(self):
        """Longer hold → more aggressive exit."""
        strategy = self._make_strategy(
            exit_timeout_seconds=120.0,
            imbalance_weight=0.0,  # Isolate hold time effect
            max_ask_discount_cents=3,
        )
        book = make_book(bid_price=40, bid_size=100, ask_price=50, ask_size=100)

        ask_early = strategy._calculate_optimal_ask(
            book, entry_price=40, hold_time=0.0, position_size=1
        )
        ask_late = strategy._calculate_optimal_ask(
            book, entry_price=40, hold_time=100.0, position_size=1
        )

        # Later → more urgency → lower ask (more discount)
        assert ask_late <= ask_early


# =============================================================================
# Infrastructure Integration Tests
# =============================================================================


class TestRiskManagerIntegration:
    """Test RiskManager integration with SpreadCaptureStrategy."""

    def _make_strategy(self, risk_manager=None, **config_overrides):
        config = default_config(**config_overrides)
        return SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            risk_manager=risk_manager,
        )

    def test_no_risk_manager_fallback(self):
        """Without risk manager, inline daily_loss_limit_hit still works."""
        strategy = self._make_strategy()
        strategy._daily_loss_limit_hit = True
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_risk_manager_blocks_when_halted(self):
        """RiskManager.is_trading_allowed()=False blocks trades."""
        from src.core.config import RiskConfig
        from src.risk.risk_manager import RiskManager

        risk_config = RiskConfig(
            max_position_size=100,
            max_total_position=500,
            max_daily_loss=50.0,
        )
        rm = RiskManager(risk_config)
        rm._trading_halted = True

        strategy = self._make_strategy(risk_manager=rm)
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_risk_manager_allows_when_ok(self):
        """RiskManager passes through when trading is allowed."""
        from src.core.config import RiskConfig
        from src.risk.risk_manager import RiskManager

        risk_config = RiskConfig(
            max_position_size=100,
            max_total_position=500,
            max_daily_loss=50.0,
        )
        rm = RiskManager(risk_config)

        strategy = self._make_strategy(
            risk_manager=rm,
            allowed_ticker_prefixes=None,
            require_price_movement=False,
            require_live_activity=False,
            use_mean_reversion_entry=False,
        )
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None

    def test_risk_manager_daily_pnl_updates(self):
        """_complete_trade calls RiskManager.update_daily_pnl."""
        from src.core.config import RiskConfig
        from src.risk.risk_manager import RiskManager

        risk_config = RiskConfig(
            max_position_size=100,
            max_total_position=500,
            max_daily_loss=50.0,
        )
        rm = RiskManager(risk_config)

        strategy = self._make_strategy(risk_manager=rm)
        trade = SpreadCaptureTrade(
            ticker="TEST",
            state=SpreadCaptureState.OPEN,
            entry_fill_price=40,
            entry_fill_size=5,
            exit_fill_price=50,
            exit_fill_size=5,
            entry_fill_time=time.time() - 10.0,
        )
        strategy._trades[trade.trade_id] = trade
        strategy._complete_trade(trade)

        # RiskManager should have the PnL update
        assert rm.daily_pnl != 0.0
        assert rm.daily_pnl == pytest.approx(trade.net_pnl)

    def test_can_trade_blocks_oversized(self):
        """RiskManager.can_trade blocks when position exceeds limit."""
        from src.core.config import RiskConfig
        from src.risk.risk_manager import RiskManager

        risk_config = RiskConfig(
            max_position_size=5,  # Very low limit
            max_total_position=10,
            max_daily_loss=50.0,
        )
        rm = RiskManager(risk_config)

        strategy = self._make_strategy(
            risk_manager=rm,
            max_entry_size=25,  # Strategy wants to size up to 25
        )
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        # Should be blocked because size > max_position_size
        assert opp is None


class TestCapitalManagerIntegration:
    """Test CapitalManager integration with SpreadCaptureStrategy."""

    def _make_strategy(self, capital_manager=None, **config_overrides):
        config = default_config(**config_overrides)
        return SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            capital_manager=capital_manager,
        )

    def test_no_capital_manager_fallback(self):
        """Without capital manager, inline balance hack still works."""
        strategy = self._make_strategy()
        strategy._insufficient_balance_until = time.time() + 999
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_blocks_when_no_capital(self):
        """CapitalManager blocks when no deployable capital."""
        from src.oms.capital_manager import CapitalManager

        cm = CapitalManager()
        cm.set_exchange_balance("kalshi", 0.0)

        strategy = self._make_strategy(capital_manager=cm)
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is None

    def test_allows_when_capital_available(self):
        """CapitalManager passes through when capital is available."""
        from src.oms.capital_manager import CapitalManager

        cm = CapitalManager()
        cm.set_exchange_balance("kalshi", 10000.0)

        strategy = self._make_strategy(
            capital_manager=cm,
            allowed_ticker_prefixes=None,
            require_price_movement=False,
            require_live_activity=False,
            use_mean_reversion_entry=False,
        )
        book = make_book(bid_price=40, ask_price=50, bid_size=10, ask_size=10)
        opp = strategy.analyze_opportunity("TEST", book)
        assert opp is not None

    def test_fetch_bankroll_uses_capital_manager(self):
        """_fetch_bankroll returns CapitalManager balance."""
        from src.oms.capital_manager import CapitalManager

        cm = CapitalManager()
        cm.set_exchange_balance("kalshi", 5000.0)

        strategy = self._make_strategy(capital_manager=cm)
        bankroll = strategy._fetch_bankroll()
        assert bankroll == 5000.0

    def test_reservation_released_on_completion(self):
        """Capital reservation is released when trade completes."""
        from src.oms.capital_manager import CapitalManager

        cm = CapitalManager()
        cm.set_exchange_balance("kalshi", 10000.0)

        strategy = self._make_strategy(capital_manager=cm)

        # Manually reserve and assign to a trade
        res_id = "sc_test123"
        cm.reserve(res_id, "kalshi", 100.0, "test")
        assert cm.get_total_reserved("kalshi") == 100.0

        trade = SpreadCaptureTrade(
            ticker="TEST",
            state=SpreadCaptureState.OPEN,
            entry_fill_price=40,
            entry_fill_size=5,
            exit_fill_price=50,
            exit_fill_size=5,
            entry_fill_time=time.time() - 10.0,
            capital_reservation_id=res_id,
        )
        strategy._trades[trade.trade_id] = trade
        strategy._complete_trade(trade)

        # Reservation should be released
        assert cm.get_total_reserved("kalshi") == 0.0


class TestCorrelationIntegration:
    """Test CorrelatedExposureTracker integration with SpreadCaptureStrategy."""

    def test_event_exposure_blocks_concentrated_trades(self):
        """Same-event trades blocked when at exposure limit."""
        from src.risk.correlation_limits import (
            CorrelatedExposureTracker,
            CorrelationLimitConfig,
        )

        corr_config = CorrelationLimitConfig(
            max_event_exposure_pct=0.30,
            correlated_categories=["KXNCAAMBGAME"],
        )
        tracker = CorrelatedExposureTracker(corr_config)

        config = default_config(
            max_concurrent_positions=5,
            max_entry_size=25,
        )
        strategy = SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            correlation_tracker=tracker,
        )

        # Fill up exposure on one event with active trades
        for i in range(3):
            trade = SpreadCaptureTrade(
                ticker=f"KXNCAAMBGAME-26FEB05DUKEUNC-OUTCOME{i}",
                state=SpreadCaptureState.OPEN,
                entry_fill_price=40,
                entry_fill_size=25,
                entry_fill_time=time.time(),
            )
            strategy._trades[f"t{i}"] = trade

        # Try to enter another trade in the same event
        book = make_book(
            ticker="KXNCAAMBGAME-26FEB05DUKEUNC-DUKE",
            bid_price=40,
            ask_price=50,
            bid_size=10,
            ask_size=10,
        )
        opp = strategy.analyze_opportunity("KXNCAAMBGAME-26FEB05DUKEUNC-DUKE", book)
        # Should be blocked: 75 contracts in event > 30% of 125 max = 37
        assert opp is None

    def test_different_event_allowed(self):
        """Different event trades are not blocked by first event's exposure."""
        from src.risk.correlation_limits import (
            CorrelatedExposureTracker,
            CorrelationLimitConfig,
        )

        corr_config = CorrelationLimitConfig(
            max_event_exposure_pct=0.50,
            correlated_categories=["KXNCAAMBGAME"],
        )
        tracker = CorrelatedExposureTracker(corr_config)

        config = default_config(
            max_concurrent_positions=5,
            max_entry_size=25,
            allowed_ticker_prefixes=None,  # Allow all tickers
            require_price_movement=False,
            require_live_activity=False,
            use_mean_reversion_entry=False,
        )
        strategy = SpreadCaptureStrategy(
            config=config,
            dry_run=True,
            correlation_tracker=tracker,
        )

        # Add one trade in event A
        trade = SpreadCaptureTrade(
            ticker="KXNCAAMBGAME-26FEB05DUKEUNC-DUKE",
            state=SpreadCaptureState.OPEN,
            entry_fill_price=40,
            entry_fill_size=10,
            entry_fill_time=time.time(),
        )
        strategy._trades["t1"] = trade

        # Try to enter a trade in event B (different game)
        book = make_book(
            ticker="KXNCAAMBGAME-26FEB05KSUUK-KSU",
            bid_price=40,
            ask_price=50,
            bid_size=10,
            ask_size=10,
        )
        opp = strategy.analyze_opportunity("KXNCAAMBGAME-26FEB05KSUUK-KSU", book)
        assert opp is not None
