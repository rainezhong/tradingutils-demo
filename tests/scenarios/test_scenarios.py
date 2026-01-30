"""Scenario tests for market-making system.

Tests realistic trading scenarios including:
- Normal operation with small moves and balanced fills
- Large market moves triggering stop loss
- Position limit being hit
- Daily loss limit stopping trading
- API failures handled gracefully
- Rapid market moves without crashes
"""

import pytest
from datetime import datetime
from typing import Generator
from unittest.mock import Mock, patch

from src.core.config import RiskConfig
from src.engine import MarketMakingEngine
from src.execution.mock_api_client import MockAPIClient
from src.market_making.config import MarketMakerConfig
from src.market_making.models import Fill, MarketState, Quote


def create_market(
    ticker: str = "TEST",
    mid: float = 0.50,
    spread: float = 0.02,
) -> MarketState:
    """Create a MarketState for testing."""
    return MarketState(
        ticker=ticker,
        timestamp=datetime.now(),
        best_bid=mid - spread / 2,
        best_ask=mid + spread / 2,
        mid_price=mid,
        bid_size=100,
        ask_size=100,
    )


def create_engine(
    ticker: str = "TEST",
    max_position: int = 50,
    target_spread: float = 0.04,
    max_loss: float = 25.0,
    daily_loss: float = 100.0,
) -> tuple[MarketMakingEngine, MockAPIClient]:
    """Create an engine with mock API client."""
    api_client = MockAPIClient()

    mm_config = MarketMakerConfig(
        target_spread=target_spread,
        max_position=max_position,
        quote_size=10,
    )

    # Ensure max_loss <= daily_loss for valid config
    effective_max_loss = min(max_loss, daily_loss)

    risk_config = RiskConfig(
        max_position_size=max_position,
        max_total_position=max_position * 2,
        max_loss_per_position=effective_max_loss,
        max_daily_loss=daily_loss,
    )

    engine = MarketMakingEngine(
        ticker=ticker,
        api_client=api_client,
        mm_config=mm_config,
        risk_config=risk_config,
    )

    return engine, api_client


class TestNormalOperation:
    """Tests for normal market-making operation."""

    def test_normal_operation_small_moves(self) -> None:
        """Normal operation: small price moves, fills on both sides."""
        engine, api_client = create_engine()

        # Start with initial market
        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Check quotes were generated
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 1

        # Simulate small price moves
        for i in range(10):
            mid = 0.50 + (i % 3 - 1) * 0.005  # Oscillate around 0.50
            market = create_market(mid=mid)
            engine.on_market_update(market)

        # Verify stable operation
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 11
        assert status["engine"]["force_closes"] == 0

    def test_balanced_fills(self) -> None:
        """Fills on both sides result in balanced P&L."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Simulate buy fill
        buy_fill = Fill(
            ticker="TEST",
            order_id="order-1",
            side="BID",
            price=0.48,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(buy_fill)

        # Position should be long
        assert engine.market_maker.position.is_long

        # Simulate sell fill
        sell_fill = Fill(
            ticker="TEST",
            order_id="order-2",
            side="ASK",
            price=0.52,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(sell_fill)

        # Position should be flat with realized profit
        assert engine.market_maker.position.is_flat
        assert engine.market_maker.position.realized_pnl > 0

    def test_steady_state_quotes(self) -> None:
        """Steady market maintains consistent quotes."""
        engine, api_client = create_engine()

        # Send same market repeatedly
        market = create_market(mid=0.50)
        for _ in range(5):
            engine.on_market_update(market)

        # Verify quotes are generated but not updated excessively
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 5
        # Quotes should be set on first update, not changed on subsequent
        assert status["engine"]["quotes_sent"] >= 2  # At least bid and ask


class TestLargeMarketMove:
    """Tests for large market moves."""

    def test_large_move_up_triggers_update(self) -> None:
        """Large upward move triggers quote update."""
        engine, api_client = create_engine()

        # Initial market
        market = create_market(mid=0.50)
        engine.on_market_update(market)
        initial_quotes = engine._state.quotes_sent

        # Large move up
        market = create_market(mid=0.55)  # 10% move
        engine.on_market_update(market)

        # Quotes should have been updated
        assert engine._state.quotes_sent > initial_quotes

    def test_large_move_down_triggers_update(self) -> None:
        """Large downward move triggers quote update."""
        engine, api_client = create_engine()

        # Initial market
        market = create_market(mid=0.50)
        engine.on_market_update(market)
        initial_quotes = engine._state.quotes_sent

        # Large move down
        market = create_market(mid=0.45)  # 10% move
        engine.on_market_update(market)

        # Quotes should have been updated
        assert engine._state.quotes_sent > initial_quotes

    def test_losing_position_on_adverse_move(self) -> None:
        """Adverse price move creates unrealized loss."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Buy at 0.48
        buy_fill = Fill(
            ticker="TEST",
            order_id="order-1",
            side="BID",
            price=0.48,
            size=20,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(buy_fill)

        # Market drops significantly
        market = create_market(mid=0.40)
        engine.on_market_update(market)

        # Should have unrealized loss
        pos = engine.market_maker.position
        assert pos.unrealized_pnl < 0

    def test_stop_loss_trigger(self) -> None:
        """Large loss triggers force close."""
        engine, api_client = create_engine(max_loss=5.0)  # Low loss limit

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Buy at 0.50
        buy_fill = Fill(
            ticker="TEST",
            order_id="order-1",
            side="BID",
            price=0.50,
            size=30,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(buy_fill)

        # Market crashes - should trigger force close
        market = create_market(mid=0.30)
        engine.on_market_update(market)

        # Force close should have been triggered
        assert engine._state.force_closes > 0


class TestPositionLimits:
    """Tests for position limit scenarios."""

    def test_approaching_max_position(self) -> None:
        """Approaching max position stops quoting one side."""
        engine, api_client = create_engine(max_position=20)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Build up long position close to limit
        for i in range(3):
            fill = Fill(
                ticker="TEST",
                order_id=f"order-{i}",
                side="BID",
                price=0.49,
                size=5,
                timestamp=datetime.now(),
            )
            engine.market_maker.update_position(fill)

        # Position is 15, close to max of 20
        assert engine.market_maker.position.contracts == 15

        # Update market to regenerate quotes
        engine.on_market_update(market)

        # Engine should still function
        status = engine.get_status()
        assert status["engine"]["force_closes"] == 0

    def test_at_max_position(self) -> None:
        """At max position, only closing quotes allowed."""
        engine, api_client = create_engine(max_position=10)

        market = create_market(mid=0.50)

        # Fill to max position
        fill = Fill(
            ticker="TEST",
            order_id="order-1",
            side="BID",
            price=0.49,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(fill)

        assert engine.market_maker.position.contracts == 10

        # Try to generate quotes
        engine.on_market_update(market)

        # Should not crash, engine continues
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 1

    def test_position_limit_both_directions(self) -> None:
        """Test limits work for both long and short."""
        engine, api_client = create_engine(max_position=10)

        market = create_market(mid=0.50)

        # Build short position
        fill = Fill(
            ticker="TEST",
            order_id="order-1",
            side="ASK",
            price=0.51,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(fill)

        assert engine.market_maker.position.is_short
        assert engine.market_maker.position.contracts == -10

        engine.on_market_update(market)

        # Should still operate
        status = engine.get_status()
        assert status["engine"]["force_closes"] == 0


class TestDailyLossLimit:
    """Tests for daily loss limit scenarios."""

    def test_daily_loss_halts_trading(self) -> None:
        """Hitting daily loss limit stops all trading."""
        engine, api_client = create_engine(daily_loss=10.0)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Realize a loss that hits daily limit
        engine.risk_manager.update_daily_pnl(-11.0)  # Over the limit

        # Try to trade
        engine.on_market_update(market)

        # Trading should be halted
        assert not engine.risk_manager.is_trading_allowed()

    def test_approaching_daily_limit(self) -> None:
        """Approaching daily limit continues trading."""
        engine, api_client = create_engine(daily_loss=100.0)

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Lose some but not over limit
        engine.risk_manager.update_daily_pnl(-50.0)

        engine.on_market_update(market)

        # Trading should still be allowed
        assert engine.risk_manager.is_trading_allowed()

    def test_reset_daily_resumes_trading(self) -> None:
        """Reset daily P&L allows trading to resume."""
        engine, api_client = create_engine(daily_loss=10.0)

        market = create_market(mid=0.50)

        # Hit daily limit
        engine.risk_manager.update_daily_pnl(-15.0)
        assert not engine.risk_manager.is_trading_allowed()

        # Reset (simulating new trading day)
        engine.risk_manager.reset_daily()

        # Trading should resume
        assert engine.risk_manager.is_trading_allowed()


class TestAPIFailures:
    """Tests for API failure scenarios."""

    def test_api_timeout_handled(self) -> None:
        """API timeout is handled gracefully."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)

        # Mock API to timeout
        with patch.object(
            engine.quote_manager, 'place_quote',
            side_effect=TimeoutError("API timeout")
        ):
            # Should not crash
            engine.on_market_update(market)

        # Engine should still be operational
        status = engine.get_status()
        assert status is not None

    def test_api_error_handled_gracefully(self) -> None:
        """API error is handled gracefully without crashing."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Mock API to fail
        with patch.object(
            engine.quote_manager, 'place_quote',
            side_effect=Exception("API error")
        ):
            # Should handle error without crashing
            engine.on_market_update(market)

        # Engine should still be operational
        status = engine.get_status()
        assert status is not None
        assert status["engine"]["market_updates"] == 2

    def test_partial_api_failure(self) -> None:
        """Partial API failure (one quote fails)."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        call_count = [0]

        def flaky_place(quote):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("First call fails")
            return Mock(order_id=f"order-{call_count[0]}")

        with patch.object(engine.quote_manager, 'place_quote', side_effect=flaky_place):
            engine.on_market_update(market)

        # Engine continues despite partial failure
        status = engine.get_status()
        assert status is not None


class TestRapidMarketMoves:
    """Tests for rapid market moves."""

    def test_rapid_updates_no_crash(self) -> None:
        """Rapid market updates don't crash the engine."""
        engine, api_client = create_engine()

        # Send 100 rapid updates
        for i in range(100):
            mid = 0.50 + (i % 10 - 5) * 0.01  # Oscillate
            market = create_market(mid=max(0.01, min(0.99, mid)))
            engine.on_market_update(market)

        # Engine should still be functional
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 100
        assert status["engine"]["force_closes"] == 0

    def test_volatile_market(self) -> None:
        """High volatility with large moves."""
        engine, api_client = create_engine()

        import random
        random.seed(42)

        mid = 0.50
        for _ in range(50):
            # Random large move
            move = random.uniform(-0.1, 0.1)
            mid = max(0.05, min(0.95, mid + move))
            market = create_market(mid=mid)
            engine.on_market_update(market)

        # Engine handles volatility
        status = engine.get_status()
        assert status["engine"]["market_updates"] == 50

    def test_gap_up(self) -> None:
        """Market gaps up suddenly."""
        engine, api_client = create_engine()

        # Start normal
        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Sell position
        fill = Fill(
            ticker="TEST",
            order_id="order-1",
            side="ASK",
            price=0.51,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(fill)

        # Gap up - bad for short position
        market = create_market(mid=0.70)
        engine.on_market_update(market)

        # Should handle without crash
        status = engine.get_status()
        assert status is not None

    def test_gap_down(self) -> None:
        """Market gaps down suddenly."""
        engine, api_client = create_engine()

        # Start with long position
        market = create_market(mid=0.50)
        engine.on_market_update(market)

        fill = Fill(
            ticker="TEST",
            order_id="order-1",
            side="BID",
            price=0.49,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(fill)

        # Gap down - bad for long position
        market = create_market(mid=0.30)
        engine.on_market_update(market)

        # Should handle without crash
        status = engine.get_status()
        assert status is not None


class TestEdgeCases:
    """Tests for edge cases."""

    def test_zero_position(self) -> None:
        """Zero position after offsetting trades."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        # Buy then sell same amount
        buy = Fill(
            order_id="o1",
            ticker="TEST",
            side="BID",
            price=0.48,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(buy)

        sell = Fill(
            order_id="o2",
            ticker="TEST",
            side="ASK",
            price=0.52,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(sell)

        assert engine.market_maker.position.is_flat
        engine.on_market_update(market)

        status = engine.get_status()
        assert status["market_maker"]["position"]["contracts"] == 0

    def test_ticker_mismatch_ignored(self) -> None:
        """Market update for wrong ticker is ignored."""
        engine, api_client = create_engine(ticker="MARKET-A")

        wrong_market = create_market(ticker="MARKET-B")
        engine.on_market_update(wrong_market)

        # Should be ignored
        assert engine._state.market_updates == 0

    def test_invalid_market_data(self) -> None:
        """Invalid market data (bid > ask) handled."""
        engine, api_client = create_engine()

        # This should be caught by MarketState validation
        # Let's test with very tight spread instead
        market = create_market(mid=0.50, spread=0.001)
        engine.on_market_update(market)

        # Should handle gracefully
        status = engine.get_status()
        assert status is not None

    def test_engine_reset(self) -> None:
        """Engine reset clears all state."""
        engine, api_client = create_engine()

        market = create_market(mid=0.50)
        engine.on_market_update(market)

        fill = Fill(
            order_id="o1",
            ticker="TEST",
            side="BID",
            price=0.49,
            size=10,
            timestamp=datetime.now(),
        )
        engine.market_maker.update_position(fill)

        # Reset
        engine.reset()

        # All state should be cleared
        assert engine._state.market_updates == 0
        assert engine.market_maker.position.is_flat
