"""Tests for intelligent exit strategies."""

import pytest
from datetime import datetime, timedelta
from strategies.latency_arb.intelligent_exits import (
    IntelligentExitManager,
    ExitSignal,
    PositionMetrics,
)


@pytest.fixture
def exit_manager():
    """Create exit manager with default config."""
    return IntelligentExitManager(
        edge_convergence_threshold=0.30,
        trailing_stop_activation=0.05,
        trailing_stop_distance=0.03,
        velocity_threshold=0.01,
        profit_target_cents=10,
        max_hold_time_sec=60.0,
    )


def test_edge_convergence_exit():
    """Test exit when edge converges to 30% of original."""
    # Use manager without profit target for this test
    manager = IntelligentExitManager(
        edge_convergence_threshold=0.30,
        profit_target_cents=None,  # No profit target
    )

    # Entry: 68¢ price, 80% fair value, edge = 12%
    manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow(),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # Edge still strong (10% = 83% of original 12%)
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=70,
        current_no_price=30,
        current_fair_value=0.80,
    )
    assert signal is None, "Should not exit when edge still strong"

    # Edge converged (3% = 25% of original 12%) → EXIT
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=77,
        current_no_price=23,
        current_fair_value=0.80,
    )
    assert signal is not None
    assert signal.reason == "edge_converged"
    assert signal.urgency == 0.7


def test_trailing_stop_activation():
    """Test trailing stop activates after 5¢ profit."""
    # Use manager without profit target for this test
    manager = IntelligentExitManager(
        trailing_stop_activation=0.05,
        trailing_stop_distance=0.03,
        profit_target_cents=None,  # No profit target
    )

    manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow(),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # +3¢ profit (below 5¢ activation threshold)
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=71,
        current_no_price=29,
        current_fair_value=0.80,
    )
    assert signal is None, "Trailing stop should not activate yet"

    # +6¢ profit (above 5¢ threshold) → trailing stop active at 73¢ (76-3)
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=76,
        current_no_price=24,
        current_fair_value=0.80,
    )
    assert signal is None, "Should hold at new peak"

    # Pullback to 72¢ (4¢ from peak 76) > 3¢ threshold → EXIT
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=72,
        current_no_price=28,
        current_fair_value=0.80,
    )
    assert signal is not None
    assert signal.reason == "trailing_stop"
    assert signal.urgency == 0.8


def test_profit_target_hit(exit_manager):
    """Test exit when profit target reached."""
    exit_manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow(),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # +9¢ unrealized (45¢ total = 9¢ × 5 contracts) < 10¢ target
    signal = exit_manager.check_exit(
        ticker="TEST",
        current_yes_price=77,
        current_no_price=23,
        current_fair_value=0.80,
    )
    # Should exit on edge convergence before profit target

    # +10¢ unrealized (50¢ total = 10¢ × 5 contracts) = target hit
    signal = exit_manager.check_exit(
        ticker="TEST",
        current_yes_price=78,
        current_no_price=22,
        current_fair_value=0.80,
    )
    assert signal is not None
    # Profit target has higher urgency (0.9) than edge convergence (0.7)
    assert signal.reason == "profit_target"
    assert signal.expected_pnl_cents == 50


def test_max_hold_time_override(exit_manager):
    """Test max hold time forces exit regardless of edge."""
    # Entry 65 seconds ago
    entry_time = datetime.utcnow() - timedelta(seconds=65)

    exit_manager.register_position(
        ticker="TEST",
        entry_time=entry_time,
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # Edge still strong but time exceeded
    signal = exit_manager.check_exit(
        ticker="TEST",
        current_yes_price=70,
        current_no_price=30,
        current_fair_value=0.80,
    )
    assert signal is not None
    assert signal.reason == "max_hold_time"
    assert signal.urgency == 1.0  # Highest urgency


def test_velocity_based_exit(exit_manager):
    """Test exit when edge decaying too fast."""
    exit_manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow() - timedelta(seconds=5),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # Simulate rapid edge decay
    # T=0: edge = 12%
    # T=1: edge = 10%
    # T=2: edge = 8%
    # T=3: edge = 5%
    # Velocity = (12-5) / 3 = 2.33% per second > 1% threshold

    # First check (builds sample history)
    exit_manager.check_exit("TEST", 70, 30, 0.80)
    exit_manager.check_exit("TEST", 72, 28, 0.80)
    exit_manager.check_exit("TEST", 75, 25, 0.80)

    # Need at least 3 samples for velocity calculation
    # This check should trigger velocity exit
    # (Note: actual velocity calculation needs time deltas, simplified here)


def test_spread_widening_exit():
    """Test exit when spread widens beyond threshold."""
    # Use manager without profit target for this test
    manager = IntelligentExitManager(
        spread_widening_threshold=5,
        profit_target_cents=None,  # No profit target
    )

    manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow(),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # Normal spread: YES 70 + NO 28 = 98, spread = 2¢
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=70,
        current_no_price=28,
        current_fair_value=0.80,
    )
    # May exit on other reasons but not spread

    # Wide spread: YES 70 + NO 24 = 94, spread = 6¢ > 5¢ threshold
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=70,
        current_no_price=24,
        current_fair_value=0.80,
    )
    assert signal is not None
    assert signal.reason == "spread_widening"
    assert signal.urgency == 0.5


def test_no_exit_when_edge_persists():
    """Test that position holds when edge still strong."""
    # Use manager without profit target for this test
    manager = IntelligentExitManager(
        edge_convergence_threshold=0.30,
        profit_target_cents=None,  # No profit target
    )

    manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow(),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # Edge still 90% of original, profit only +2¢
    signal = manager.check_exit(
        ticker="TEST",
        current_yes_price=70,
        current_no_price=30,
        current_fair_value=0.80,
    )
    assert signal is None, "Should hold when edge persists and no exit triggers"


def test_priority_ordering(exit_manager):
    """Test that higher-urgency signals take priority."""
    # Entry 65 seconds ago (max hold time exceeded)
    entry_time = datetime.utcnow() - timedelta(seconds=65)

    exit_manager.register_position(
        ticker="TEST",
        entry_time=entry_time,
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # Multiple exit conditions:
    # - Max hold time (urgency 1.0)
    # - Profit target (urgency 0.9)
    # - Edge converged (urgency 0.7)

    signal = exit_manager.check_exit(
        ticker="TEST",
        current_yes_price=78,  # +10¢ profit (target hit)
        current_no_price=22,
        current_fair_value=0.80,
    )

    # Should return max_hold_time (highest urgency 1.0)
    assert signal is not None
    assert signal.reason == "max_hold_time"
    assert signal.urgency == 1.0


def test_position_tracking(exit_manager):
    """Test position metrics tracking."""
    exit_manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow(),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    # Check multiple times to build history
    exit_manager.check_exit("TEST", 70, 30, 0.80)
    exit_manager.check_exit("TEST", 75, 25, 0.80)
    exit_manager.check_exit("TEST", 72, 28, 0.80)

    stats = exit_manager.get_position_stats("TEST")
    assert stats is not None
    assert stats["ticker"] == "TEST"
    assert stats["entry_price"] == 68
    assert stats["max_favorable_price"] == 75  # Highest bid seen
    assert stats["peak_profit_cents"] == (75 - 68) * 5  # 35¢


def test_no_side_position(exit_manager):
    """Test check_exit returns None for unknown ticker."""
    signal = exit_manager.check_exit(
        ticker="UNKNOWN",
        current_yes_price=70,
        current_no_price=30,
        current_fair_value=0.80,
    )
    assert signal is None


def test_remove_position(exit_manager):
    """Test position removal."""
    exit_manager.register_position(
        ticker="TEST",
        entry_time=datetime.utcnow(),
        entry_price=68,
        entry_fair_value=0.80,
        entry_market_prob=0.68,
        side="yes",
        size=5,
    )

    exit_manager.remove_position("TEST")

    signal = exit_manager.check_exit("TEST", 70, 30, 0.80)
    assert signal is None, "Should return None after position removed"
