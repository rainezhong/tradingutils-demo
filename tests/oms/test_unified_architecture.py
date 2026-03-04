"""
Tests for the unified OMS architecture.

Tests the new features added for prediction market support:
- Outcome and Action enums
- 4-way API (buy_yes, buy_no, sell_yes, sell_no)
- Strategy attribution (strategy_id)
- Strategy-level queries
- Outcome-aware position tracking
"""

import pytest
from datetime import datetime
from unittest.mock import MagicMock

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.oms.models import (
    Action,
    Outcome,
    TrackedOrder,
    FailedOrder,
    SpreadLeg,
    PositionInventory,
    FailureReason,
)
from src.core.models import Position


class TestOutcomeEnum:
    """Tests for Outcome enum."""

    def test_yes_value(self):
        assert Outcome.YES.value == "yes"

    def test_no_value(self):
        assert Outcome.NO.value == "no"

    def test_opposite_yes(self):
        assert Outcome.YES.opposite == Outcome.NO

    def test_opposite_no(self):
        assert Outcome.NO.opposite == Outcome.YES


class TestActionEnum:
    """Tests for Action enum."""

    def test_buy_value(self):
        assert Action.BUY.value == "buy"

    def test_sell_value(self):
        assert Action.SELL.value == "sell"

    def test_opposite_buy(self):
        assert Action.BUY.opposite == Action.SELL

    def test_opposite_sell(self):
        assert Action.SELL.opposite == Action.BUY

    def test_to_side_buy(self):
        assert Action.BUY.to_side() == "buy"

    def test_to_side_sell(self):
        assert Action.SELL.to_side() == "sell"


class TestTrackedOrderWithOutcome:
    """Tests for TrackedOrder with outcome/action fields."""

    def test_create_order_with_outcome_action(self):
        """Test creating an order with outcome and action."""
        order = TrackedOrder(
            idempotency_key="TEST-001",
            exchange="kalshi",
            ticker="TICKER-A",
            side="buy",
            price=0.55,
            size=10,
            outcome=Outcome.YES,
            action=Action.BUY,
            strategy_id="my_strategy",
        )

        assert order.outcome == Outcome.YES
        assert order.action == Action.BUY
        assert order.strategy_id == "my_strategy"
        assert order.side == "buy"

    def test_create_order_derive_side_from_action(self):
        """Test that side can be derived from action."""
        order = TrackedOrder(
            idempotency_key="TEST-002",
            exchange="kalshi",
            ticker="TICKER-A",
            side="",  # Empty, should be derived
            price=0.55,
            size=10,
            outcome=Outcome.NO,
            action=Action.SELL,
        )

        assert order.side == "sell"

    def test_create_order_action_side_mismatch_raises(self):
        """Test that mismatched action and side raises ValueError."""
        with pytest.raises(ValueError, match="does not match side"):
            TrackedOrder(
                idempotency_key="TEST-003",
                exchange="kalshi",
                ticker="TICKER-A",
                side="buy",
                price=0.55,
                size=10,
                action=Action.SELL,  # Mismatch: SELL action but "buy" side
            )

    def test_create_order_backward_compatible(self):
        """Test that orders work without outcome/action (backward compatibility)."""
        order = TrackedOrder(
            idempotency_key="TEST-004",
            exchange="polymarket",
            ticker="TICKER-B",
            side="sell",
            price=0.45,
            size=5,
        )

        assert order.outcome is None
        assert order.action is None
        assert order.strategy_id is None
        assert order.side == "sell"


class TestFailedOrderWithOutcome:
    """Tests for FailedOrder with outcome/action fields."""

    def test_failed_order_captures_outcome_action(self):
        """Test that failed orders capture outcome/action."""
        failed = FailedOrder(
            idempotency_key="FAIL-001",
            exchange="kalshi",
            ticker="TICKER-A",
            side="buy",
            price=0.60,
            size=10,
            reason=FailureReason.INSUFFICIENT_FUNDS,
            error_message="Not enough balance",
            outcome=Outcome.YES,
            action=Action.BUY,
            strategy_id="test_strategy",
        )

        assert failed.outcome == Outcome.YES
        assert failed.action == Action.BUY
        assert failed.strategy_id == "test_strategy"


class TestSpreadLegWithOutcome:
    """Tests for SpreadLeg with outcome/action fields."""

    def test_spread_leg_with_outcome(self):
        """Test creating a spread leg with outcome/action."""
        leg = SpreadLeg(
            leg_id="SPREAD-001-L1",
            exchange="kalshi",
            ticker="TICKER-A",
            side="buy",
            price=0.45,
            size=10,
            outcome=Outcome.YES,
            action=Action.BUY,
        )

        assert leg.outcome == Outcome.YES
        assert leg.action == Action.BUY


class TestPositionInventoryOutcomeAware:
    """Tests for PositionInventory outcome-aware methods."""

    @pytest.fixture
    def inventory(self):
        """Create a fresh PositionInventory."""
        return PositionInventory()

    def test_position_key_without_outcome(self, inventory):
        """Test position key creation without outcome."""
        key = inventory._make_position_key("TICKER-A")
        assert key == "TICKER-A"

    def test_position_key_with_yes(self, inventory):
        """Test position key creation with YES outcome."""
        key = inventory._make_position_key("TICKER-A", Outcome.YES)
        assert key == "TICKER-A:YES"

    def test_position_key_with_no(self, inventory):
        """Test position key creation with NO outcome."""
        key = inventory._make_position_key("TICKER-A", Outcome.NO)
        assert key == "TICKER-A:NO"

    def test_parse_position_key_plain(self, inventory):
        """Test parsing plain position key."""
        ticker, outcome = inventory._parse_position_key("TICKER-A")
        assert ticker == "TICKER-A"
        assert outcome is None

    def test_parse_position_key_yes(self, inventory):
        """Test parsing YES position key."""
        ticker, outcome = inventory._parse_position_key("TICKER-A:YES")
        assert ticker == "TICKER-A"
        assert outcome == Outcome.YES

    def test_parse_position_key_no(self, inventory):
        """Test parsing NO position key."""
        ticker, outcome = inventory._parse_position_key("TICKER-A:NO")
        assert ticker == "TICKER-A"
        assert outcome == Outcome.NO

    def test_set_and_get_position_by_outcome(self, inventory):
        """Test setting and getting positions by outcome."""
        yes_pos = Position(
            ticker="TICKER-A",
            size=10,
            entry_price=0.45,
            current_price=0.50,
        )
        no_pos = Position(
            ticker="TICKER-A",
            size=5,
            entry_price=0.55,
            current_price=0.50,
        )

        # Set positions
        inventory.set_position_by_outcome("kalshi", "TICKER-A", yes_pos, Outcome.YES)
        inventory.set_position_by_outcome("kalshi", "TICKER-A", no_pos, Outcome.NO)

        # Get positions
        retrieved_yes = inventory.get_position_by_outcome(
            "kalshi", "TICKER-A", Outcome.YES
        )
        retrieved_no = inventory.get_position_by_outcome(
            "kalshi", "TICKER-A", Outcome.NO
        )

        assert retrieved_yes.size == 10
        assert retrieved_no.size == 5

    def test_get_outcome_positions(self, inventory):
        """Test getting all outcome positions for a ticker."""
        yes_pos = Position(
            ticker="TICKER-A", size=10, entry_price=0.45, current_price=0.50
        )
        no_pos = Position(
            ticker="TICKER-A", size=5, entry_price=0.55, current_price=0.50
        )
        plain_pos = Position(
            ticker="TICKER-A", size=3, entry_price=0.60, current_price=0.50
        )

        inventory.set_position_by_outcome("kalshi", "TICKER-A", yes_pos, Outcome.YES)
        inventory.set_position_by_outcome("kalshi", "TICKER-A", no_pos, Outcome.NO)
        inventory.set_position("kalshi", "TICKER-A", plain_pos)

        positions = inventory.get_outcome_positions("kalshi", "TICKER-A")

        assert Outcome.YES in positions
        assert Outcome.NO in positions
        assert None in positions
        assert positions[Outcome.YES].size == 10
        assert positions[Outcome.NO].size == 5
        assert positions[None].size == 3


class TestSignalWithOutcome:
    """Tests for Signal dataclass with outcome/action."""

    def test_signal_with_outcome_action(self):
        """Test creating a signal with outcome and action."""
        from strategies.base import Signal

        signal = Signal(
            ticker="TICKER-A",
            side="BID",
            price=0.55,
            size=10,
            confidence=0.8,
            reason="Test signal",
            timestamp=datetime.now(),
            outcome=Outcome.YES,
            action=Action.BUY,
        )

        assert signal.outcome == Outcome.YES
        assert signal.action == Action.BUY

    def test_signal_derive_side_from_action(self):
        """Test that signal side can be derived from action."""
        from strategies.base import Signal

        signal = Signal(
            ticker="TICKER-A",
            side="",  # Empty, should be derived
            price=0.55,
            size=10,
            confidence=0.8,
            reason="Test signal",
            timestamp=datetime.now(),
            action=Action.SELL,
        )

        assert signal.side == "ASK"

    def test_signal_to_oms_side(self):
        """Test converting signal side to OMS format."""
        from strategies.base import Signal

        bid_signal = Signal(
            ticker="TICKER-A",
            side="BID",
            price=0.55,
            size=10,
            confidence=0.8,
            reason="Test",
            timestamp=datetime.now(),
        )
        ask_signal = Signal(
            ticker="TICKER-A",
            side="ASK",
            price=0.55,
            size=10,
            confidence=0.8,
            reason="Test",
            timestamp=datetime.now(),
        )

        assert bid_signal.to_oms_side() == "buy"
        assert ask_signal.to_oms_side() == "sell"


class TestTradingSystemBuilder:
    """Tests for TradingSystemBuilder."""

    def test_builder_requires_exchange(self):
        """Test that builder requires at least one exchange."""
        from src.oms.bootstrap import TradingSystemBuilder

        builder = TradingSystemBuilder()

        with pytest.raises(ValueError, match="At least one exchange"):
            builder.build()

    def test_builder_creates_system(self):
        """Test that builder creates a valid system."""
        from src.oms.bootstrap import TradingSystemBuilder

        mock_client = MagicMock()
        mock_client.name = "test_exchange"
        # Mock the balance method to return a proper value
        mock_client.get_balance.return_value = 1000.0

        builder = TradingSystemBuilder()
        system = builder.with_exchange(mock_client).build()

        assert system is not None
        assert system.oms is not None
        assert system.capital_manager is not None
        assert system.spread_executor is not None

    def test_builder_with_initial_capital(self):
        """Test builder with initial capital."""
        from src.oms.bootstrap import TradingSystemBuilder

        mock_client = MagicMock()
        mock_client.name = "kalshi"
        # Mock the balance method to return a proper value
        mock_client.get_balance.return_value = 5000.0

        builder = TradingSystemBuilder()
        system = (
            builder.with_exchange(mock_client)
            .with_initial_capital("kalshi", 10000)
            .build()
        )

        # Capital manager should have the initial capital
        assert system.capital_manager is not None
