"""Tests for smart early exit on adverse fair value moves.

Tests that the executor immediately exits positions when:
- Fair value has moved >10% against position AND
- Min hold time (5s) has passed AND
- Liquidity exists to exit
"""

import time
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest

from strategies.latency_arb.config import LatencyArbConfig
from strategies.latency_arb.executor import ArbPosition, LatencyArbExecutor
from strategies.latency_arb.market import KalshiMarket


class TestAdverseMoveEarlyExit:
    """Test immediate exit on adverse fair value moves."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_client = Mock()
        self.config = LatencyArbConfig(
            early_exit_enabled=True,
            min_hold_sec=5.0,
            adverse_move_threshold=0.10,
            early_exit_edge_threshold=0.02,
            early_exit_min_time_sec=30,
        )
        self.executor = LatencyArbExecutor(
            client=self.mock_client,
            config=self.config,
        )

    def test_adverse_move_yes_position_exits(self):
        """YES position exits when fair value drops >10%."""
        ticker = "TEST-ADVERSE-YES"

        # Create a YES position entered at fair value 0.70
        position = ArbPosition(
            ticker=ticker,
            side="yes",
            entry_price=68,  # Entered at 68 cents
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=6),  # Held for 6s
            entry_fair_value=0.70,
            entry_market_prob=0.68,
        )

        # Manually add position
        self.executor._positions[ticker] = position

        # Now fair value has dropped to 0.55 (15% drop = adverse move)
        current_fair = 0.55
        current_yes_price = 54
        current_no_price = 46
        time_to_expiry = 120  # Still 2 min to expiry

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=time_to_expiry,
        )

        # Should exit due to adverse move
        assert reason is not None
        assert "adverse_move" in reason
        assert "15.0%" in reason  # 0.70 - 0.55 = 0.15

    def test_adverse_move_no_position_exits(self):
        """NO position exits when fair value rises >10%."""
        ticker = "TEST-ADVERSE-NO"

        # Create a NO position entered at fair value 0.30
        position = ArbPosition(
            ticker=ticker,
            side="no",
            entry_price=68,  # Entered at 68 cents (NO side)
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=6),
            entry_fair_value=0.30,  # Betting against YES = fair value 0.30
            entry_market_prob=0.32,
        )

        self.executor._positions[ticker] = position

        # Fair value has risen to 0.45 (15% rise = adverse for NO position)
        current_fair = 0.45
        current_yes_price = 46
        current_no_price = 54
        time_to_expiry = 120

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=time_to_expiry,
        )

        # Should exit due to adverse move
        assert reason is not None
        assert "adverse_move" in reason
        assert "15.0%" in reason

    def test_no_exit_if_hold_time_too_short(self):
        """Don't exit on adverse move if held < min_hold_sec (anti-whipsaw)."""
        ticker = "TEST-HOLD-TIME"

        # Position held for only 3 seconds (< 5s min)
        position = ArbPosition(
            ticker=ticker,
            side="yes",
            entry_price=68,
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=3),
            entry_fair_value=0.70,
            entry_market_prob=0.68,
        )

        self.executor._positions[ticker] = position

        # Fair value drops 15% (would trigger exit normally)
        current_fair = 0.55
        current_yes_price = 54
        current_no_price = 46

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=120,
        )

        # Should NOT exit due to adverse move (hold time too short)
        # Might exit for other reasons (edge_gone), but not adverse_move
        if reason:
            assert "adverse_move" not in reason

    def test_no_exit_if_move_below_threshold(self):
        """Don't exit if adverse move is <10%."""
        ticker = "TEST-SMALL-MOVE"

        position = ArbPosition(
            ticker=ticker,
            side="yes",
            entry_price=68,
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=6),
            entry_fair_value=0.70,
            entry_market_prob=0.68,
        )

        self.executor._positions[ticker] = position

        # Fair value drops only 5% (below 10% threshold)
        current_fair = 0.65
        current_yes_price = 64
        current_no_price = 36

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=120,
        )

        # Should NOT exit due to adverse move
        if reason:
            assert "adverse_move" not in reason

    def test_no_exit_if_no_liquidity(self):
        """Don't exit on adverse move if bid is 0 (no liquidity)."""
        ticker = "TEST-NO-LIQ"

        position = ArbPosition(
            ticker=ticker,
            side="yes",
            entry_price=68,
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=6),
            entry_fair_value=0.70,
            entry_market_prob=0.68,
        )

        self.executor._positions[ticker] = position

        # Fair value drops 15%, but bid is 0 (no liquidity)
        current_fair = 0.55
        current_yes_price = 0  # NO BID
        current_no_price = 100

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=120,
        )

        # Should NOT exit due to adverse move (no liquidity)
        if reason:
            assert "adverse_move" not in reason

    def test_adverse_move_takes_priority_over_edge_gone(self):
        """Adverse move exit happens before edge_gone check."""
        ticker = "TEST-PRIORITY"

        position = ArbPosition(
            ticker=ticker,
            side="yes",
            entry_price=68,
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=6),
            entry_fair_value=0.70,
            entry_market_prob=0.68,
        )

        self.executor._positions[ticker] = position

        # Fair value drops 15% AND edge is gone (both conditions true)
        current_fair = 0.55
        current_yes_price = 56  # Market prob = 0.56, edge = -0.01 (negative)
        current_no_price = 44

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=120,
        )

        # Should exit with adverse_move reason (checked first)
        assert reason is not None
        assert "adverse_move" in reason

    def test_time_expiring_takes_priority_over_adverse_move(self):
        """Time-based exit comes AFTER adverse move check."""
        ticker = "TEST-TIME-PRIORITY"

        position = ArbPosition(
            ticker=ticker,
            side="yes",
            entry_price=68,
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=6),
            entry_fair_value=0.70,
            entry_market_prob=0.68,
        )

        self.executor._positions[ticker] = position

        # Both conditions: adverse move AND close to expiry
        current_fair = 0.55
        current_yes_price = 54
        current_no_price = 46
        time_to_expiry = 20  # < 30s (early_exit_min_time_sec)

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=time_to_expiry,
        )

        # Should exit with adverse_move reason (checked before time)
        assert reason is not None
        # Adverse move check happens first, so we get that reason
        assert "adverse_move" in reason

    def test_favorable_move_no_exit(self):
        """Don't exit if fair value moves in our favor."""
        ticker = "TEST-FAVORABLE"

        position = ArbPosition(
            ticker=ticker,
            side="yes",
            entry_price=68,
            size=10,
            entry_time=datetime.utcnow() - timedelta(seconds=6),
            entry_fair_value=0.70,
            entry_market_prob=0.68,
        )

        self.executor._positions[ticker] = position

        # Fair value INCREASES to 0.85 (favorable for YES position)
        current_fair = 0.85
        current_yes_price = 80
        current_no_price = 20

        reason = self.executor.check_early_exit(
            ticker=ticker,
            current_yes_price=current_yes_price,
            current_no_price=current_no_price,
            current_fair_value=current_fair,
            time_to_expiry_sec=120,
        )

        # Should NOT exit (move is favorable)
        if reason:
            assert "adverse_move" not in reason
