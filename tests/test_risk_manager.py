"""Comprehensive tests for the RiskManager safety-critical component.

Tests cover:
- Normal operation (allows trades)
- Position limits (blocks trades when exceeded)
- Loss limits (triggers force close)
- Daily limits (blocks all trades when exceeded)
- Multiple positions across markets
- Edge cases (position = 0, exactly at limit)
"""

import pytest
from datetime import datetime

from src.core.config import RiskConfig
from src.core.models import Position
from src.risk.risk_manager import RiskManager


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def default_config() -> RiskConfig:
    """Create default risk configuration for tests."""
    return RiskConfig(
        max_position_size=100,
        max_total_position=500,
        max_loss_per_position=50.0,
        max_daily_loss=200.0,
        warning_threshold_pct=0.80,
        critical_threshold_pct=0.95,
    )


@pytest.fixture
def strict_config() -> RiskConfig:
    """Create strict risk configuration for edge case testing."""
    return RiskConfig(
        max_position_size=10,
        max_total_position=20,
        max_loss_per_position=10.0,
        max_daily_loss=25.0,
        warning_threshold_pct=0.80,
        critical_threshold_pct=0.95,
    )


@pytest.fixture
def risk_manager(default_config: RiskConfig) -> RiskManager:
    """Create RiskManager with default configuration."""
    return RiskManager(default_config)


@pytest.fixture
def strict_risk_manager(strict_config: RiskConfig) -> RiskManager:
    """Create RiskManager with strict configuration."""
    return RiskManager(strict_config)


def create_position(
    ticker: str,
    size: int = 0,
    entry_price: float = 50.0,
    current_price: float = 50.0,
    unrealized_pnl: float = 0.0,
) -> Position:
    """Helper to create Position instances for testing."""
    return Position(
        ticker=ticker,
        size=size,
        entry_price=entry_price,
        current_price=current_price,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=0.0,
        opened_at=datetime.now(),
    )


# =============================================================================
# Initialization Tests
# =============================================================================


class TestRiskManagerInit:
    """Tests for RiskManager initialization."""

    def test_init_with_valid_config(self, default_config: RiskConfig) -> None:
        """RiskManager initializes correctly with valid config."""
        rm = RiskManager(default_config)
        assert rm.config == default_config
        assert rm.positions == {}
        assert rm.daily_pnl == 0.0
        assert rm.is_trading_allowed() is True

    def test_init_with_invalid_config_type(self) -> None:
        """RiskManager rejects non-RiskConfig objects."""
        with pytest.raises(TypeError, match="must be RiskConfig"):
            RiskManager({"max_position_size": 100})  # type: ignore

    def test_init_with_none_config(self) -> None:
        """RiskManager rejects None config."""
        with pytest.raises(TypeError, match="must be RiskConfig"):
            RiskManager(None)  # type: ignore


# =============================================================================
# Normal Operation Tests - Trade Allowed
# =============================================================================


class TestCanTradeAllowed:
    """Tests for scenarios where trades should be allowed."""

    def test_first_trade_allowed(self, risk_manager: RiskManager) -> None:
        """First trade with no existing positions is allowed."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 10)
        assert allowed is True
        assert reason == "Trade allowed"

    def test_trade_within_position_limit(self, risk_manager: RiskManager) -> None:
        """Trade within position limit is allowed."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 50)
        assert allowed is True
        assert reason == "Trade allowed"

    def test_trade_at_position_limit(self, risk_manager: RiskManager) -> None:
        """Trade exactly at position limit is allowed."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 100)
        assert allowed is True
        assert reason == "Trade allowed"

    def test_trade_with_existing_position_same_direction(
        self, risk_manager: RiskManager
    ) -> None:
        """Trade adding to existing position is allowed if within limit."""
        existing = create_position("TICKER-A", size=40)
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "buy", 30, current_position=existing
        )
        assert allowed is True

    def test_trade_closing_position(self, risk_manager: RiskManager) -> None:
        """Trade that closes a position is allowed."""
        existing = create_position("TICKER-A", size=50)
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "sell", 50, current_position=existing
        )
        assert allowed is True

    def test_trade_reversing_position(self, risk_manager: RiskManager) -> None:
        """Trade that reverses position direction is allowed."""
        existing = create_position("TICKER-A", size=30)
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "sell", 60, current_position=existing
        )
        assert allowed is True
        # Result would be -30 position, which is within limit

    def test_sell_trade_allowed(self, risk_manager: RiskManager) -> None:
        """Sell trade (short) is allowed within limits."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "sell", 50)
        assert allowed is True

    def test_multiple_markets_within_total_limit(
        self, risk_manager: RiskManager
    ) -> None:
        """Trades across multiple markets within total limit are allowed."""
        # Register positions in multiple markets
        risk_manager.register_position("TICKER-A", create_position("TICKER-A", size=100))
        risk_manager.register_position("TICKER-B", create_position("TICKER-B", size=100))
        risk_manager.register_position("TICKER-C", create_position("TICKER-C", size=100))

        # Total is 300, limit is 500, so 100 more is allowed
        allowed, reason = risk_manager.can_trade("TICKER-D", "buy", 100)
        assert allowed is True


# =============================================================================
# Position Limit Tests - Trade Blocked
# =============================================================================


class TestCanTradePositionLimitBlocked:
    """Tests for trades blocked due to position limits."""

    def test_trade_exceeds_position_limit(self, risk_manager: RiskManager) -> None:
        """Trade exceeding position limit is blocked."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 101)
        assert allowed is False
        assert "Position limit exceeded" in reason
        assert "101 > 100" in reason

    def test_trade_with_existing_exceeds_position_limit(
        self, risk_manager: RiskManager
    ) -> None:
        """Trade that would exceed position limit with existing is blocked."""
        existing = create_position("TICKER-A", size=80)
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "buy", 30, current_position=existing
        )
        assert allowed is False
        assert "Position limit exceeded" in reason

    def test_short_position_exceeds_limit(self, risk_manager: RiskManager) -> None:
        """Short position exceeding limit is blocked."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "sell", 101)
        assert allowed is False
        assert "Position limit exceeded" in reason

    def test_add_to_short_exceeds_limit(self, risk_manager: RiskManager) -> None:
        """Adding to short position that would exceed limit is blocked."""
        existing = create_position("TICKER-A", size=-80)
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "sell", 30, current_position=existing
        )
        assert allowed is False
        assert "Position limit exceeded" in reason


# =============================================================================
# Total Position Limit Tests
# =============================================================================


class TestCanTradeTotalLimitBlocked:
    """Tests for trades blocked due to total position limit."""

    def test_trade_exceeds_total_limit(self, risk_manager: RiskManager) -> None:
        """Trade exceeding total position limit is blocked."""
        # Register positions close to total limit (450 total)
        risk_manager.register_position("TICKER-A", create_position("TICKER-A", size=90))
        risk_manager.register_position("TICKER-B", create_position("TICKER-B", size=90))
        risk_manager.register_position("TICKER-C", create_position("TICKER-C", size=90))
        risk_manager.register_position("TICKER-D", create_position("TICKER-D", size=90))
        risk_manager.register_position("TICKER-E", create_position("TICKER-E", size=90))
        # Total is 450, limit is 500

        # Try to add 60 (within position limit but would make total 510 > 500)
        allowed, reason = risk_manager.can_trade("TICKER-F", "buy", 60)
        assert allowed is False
        assert "Total position limit exceeded" in reason

    def test_trade_at_total_limit(self, risk_manager: RiskManager) -> None:
        """Trade exactly at total limit is allowed."""
        risk_manager.register_position("TICKER-A", create_position("TICKER-A", size=100))
        risk_manager.register_position("TICKER-B", create_position("TICKER-B", size=100))
        risk_manager.register_position("TICKER-C", create_position("TICKER-C", size=100))
        risk_manager.register_position("TICKER-D", create_position("TICKER-D", size=100))
        # Total is 400, can add 100 more

        allowed, reason = risk_manager.can_trade("TICKER-E", "buy", 100)
        assert allowed is True

    def test_total_limit_considers_absolute_positions(
        self, risk_manager: RiskManager
    ) -> None:
        """Total limit counts absolute position sizes (long + short)."""
        # Mix of long and short positions
        risk_manager.register_position("TICKER-A", create_position("TICKER-A", size=100))
        risk_manager.register_position("TICKER-B", create_position("TICKER-B", size=-100))
        risk_manager.register_position("TICKER-C", create_position("TICKER-C", size=100))
        risk_manager.register_position("TICKER-D", create_position("TICKER-D", size=-100))
        # Total absolute is 400

        # Should only allow 100 more
        allowed, reason = risk_manager.can_trade("TICKER-E", "buy", 101)
        assert allowed is False


# =============================================================================
# Daily Loss Limit Tests
# =============================================================================


class TestCanTradeDailyLossBlocked:
    """Tests for trades blocked due to daily loss limit."""

    def test_trade_blocked_at_daily_loss_limit(
        self, risk_manager: RiskManager
    ) -> None:
        """Trade blocked when daily loss limit is reached."""
        risk_manager.update_daily_pnl(-200.0)  # At limit

        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 10)
        assert allowed is False
        assert "Trading halted" in reason or "Daily loss" in reason

    def test_trade_blocked_past_daily_loss_limit(
        self, risk_manager: RiskManager
    ) -> None:
        """Trade blocked when daily loss exceeds limit."""
        risk_manager.update_daily_pnl(-250.0)  # Past limit

        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 10)
        assert allowed is False
        assert "Trading halted" in reason or "Daily loss" in reason

    def test_trade_allowed_below_daily_loss_limit(
        self, risk_manager: RiskManager
    ) -> None:
        """Trade allowed when below daily loss limit."""
        risk_manager.update_daily_pnl(-150.0)  # Below limit

        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 10)
        assert allowed is True


# =============================================================================
# Losing Position Tests
# =============================================================================


class TestCanTradeLosingPosition:
    """Tests for handling trades with losing positions."""

    def test_block_adding_to_losing_long(self, risk_manager: RiskManager) -> None:
        """Blocked from adding to a losing long position beyond threshold."""
        losing_position = create_position(
            "TICKER-A",
            size=50,
            entry_price=60.0,
            current_price=40.0,
            unrealized_pnl=-50.0,  # At loss threshold
        )
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "buy", 10, current_position=losing_position
        )
        assert allowed is False
        assert "Cannot add to losing position" in reason

    def test_block_adding_to_losing_short(self, risk_manager: RiskManager) -> None:
        """Blocked from adding to a losing short position beyond threshold."""
        losing_position = create_position(
            "TICKER-A",
            size=-50,
            entry_price=40.0,
            current_price=60.0,
            unrealized_pnl=-50.0,  # At loss threshold
        )
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "sell", 10, current_position=losing_position
        )
        assert allowed is False
        assert "Cannot add to losing position" in reason

    def test_allow_closing_losing_position(self, risk_manager: RiskManager) -> None:
        """Allowed to close a losing position."""
        losing_position = create_position(
            "TICKER-A",
            size=50,
            unrealized_pnl=-50.0,
        )
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "sell", 50, current_position=losing_position
        )
        assert allowed is True

    def test_allow_reducing_losing_position(self, risk_manager: RiskManager) -> None:
        """Allowed to reduce a losing position."""
        losing_position = create_position(
            "TICKER-A",
            size=50,
            unrealized_pnl=-50.0,
        )
        allowed, reason = risk_manager.can_trade(
            "TICKER-A", "sell", 30, current_position=losing_position
        )
        assert allowed is True


# =============================================================================
# Input Validation Tests
# =============================================================================


class TestCanTradeValidation:
    """Tests for input validation in can_trade."""

    def test_invalid_size_zero(self, risk_manager: RiskManager) -> None:
        """Rejects zero trade size."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", 0)
        assert allowed is False
        assert "must be positive" in reason

    def test_invalid_size_negative(self, risk_manager: RiskManager) -> None:
        """Rejects negative trade size."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "buy", -10)
        assert allowed is False
        assert "must be positive" in reason

    def test_invalid_side(self, risk_manager: RiskManager) -> None:
        """Rejects invalid trade side."""
        allowed, reason = risk_manager.can_trade("TICKER-A", "long", 10)
        assert allowed is False
        assert "Invalid side" in reason


# =============================================================================
# Force Close Tests
# =============================================================================


class TestShouldForceClose:
    """Tests for should_force_close functionality."""

    def test_no_force_close_flat_position(self, risk_manager: RiskManager) -> None:
        """Flat position never triggers force close."""
        position = create_position("TICKER-A", size=0, unrealized_pnl=-100.0)
        assert risk_manager.should_force_close("TICKER-A", position) is False

    def test_no_force_close_profitable_position(
        self, risk_manager: RiskManager
    ) -> None:
        """Profitable position doesn't trigger force close."""
        position = create_position("TICKER-A", size=50, unrealized_pnl=25.0)
        assert risk_manager.should_force_close("TICKER-A", position) is False

    def test_no_force_close_small_loss(self, risk_manager: RiskManager) -> None:
        """Small loss doesn't trigger force close."""
        position = create_position("TICKER-A", size=50, unrealized_pnl=-25.0)
        assert risk_manager.should_force_close("TICKER-A", position) is False

    def test_force_close_at_loss_threshold(self, risk_manager: RiskManager) -> None:
        """Position at loss threshold triggers force close."""
        position = create_position("TICKER-A", size=50, unrealized_pnl=-50.0)
        assert risk_manager.should_force_close("TICKER-A", position) is True

    def test_force_close_exceeds_loss_threshold(
        self, risk_manager: RiskManager
    ) -> None:
        """Position exceeding loss threshold triggers force close."""
        position = create_position("TICKER-A", size=50, unrealized_pnl=-75.0)
        assert risk_manager.should_force_close("TICKER-A", position) is True

    def test_force_close_daily_loss_limit(self, risk_manager: RiskManager) -> None:
        """Position triggers force close when daily loss limit breached."""
        # Add realized loss
        risk_manager.update_daily_pnl(-150.0)

        # Position with unrealized loss that pushes over limit
        position = create_position("TICKER-A", size=50, unrealized_pnl=-60.0)
        risk_manager.register_position("TICKER-A", position)

        # Total loss = 150 + 60 = 210 > 200
        assert risk_manager.should_force_close("TICKER-A", position) is True


# =============================================================================
# Position Registration Tests
# =============================================================================


class TestRegisterPosition:
    """Tests for register_position functionality."""

    def test_register_new_position(self, risk_manager: RiskManager) -> None:
        """Register a new position."""
        position = create_position("TICKER-A", size=50)
        risk_manager.register_position("TICKER-A", position)

        assert "TICKER-A" in risk_manager.positions
        assert risk_manager.positions["TICKER-A"].size == 50

    def test_update_existing_position(self, risk_manager: RiskManager) -> None:
        """Update an existing position."""
        position1 = create_position("TICKER-A", size=50)
        risk_manager.register_position("TICKER-A", position1)

        position2 = create_position("TICKER-A", size=75)
        risk_manager.register_position("TICKER-A", position2)

        assert risk_manager.positions["TICKER-A"].size == 75

    def test_register_flat_position_removes(self, risk_manager: RiskManager) -> None:
        """Registering a flat position removes it from tracking."""
        position1 = create_position("TICKER-A", size=50)
        risk_manager.register_position("TICKER-A", position1)
        assert "TICKER-A" in risk_manager.positions

        position2 = create_position("TICKER-A", size=0)
        risk_manager.register_position("TICKER-A", position2)
        assert "TICKER-A" not in risk_manager.positions

    def test_register_mismatched_ticker_raises(
        self, risk_manager: RiskManager
    ) -> None:
        """Registering with mismatched ticker raises ValueError."""
        position = create_position("TICKER-A", size=50)
        with pytest.raises(ValueError, match="doesn't match"):
            risk_manager.register_position("TICKER-B", position)


# =============================================================================
# Daily P&L Tests
# =============================================================================


class TestUpdateDailyPnl:
    """Tests for update_daily_pnl functionality."""

    def test_add_profit(self, risk_manager: RiskManager) -> None:
        """Add realized profit to daily P&L."""
        risk_manager.update_daily_pnl(50.0)
        assert risk_manager.daily_pnl == 50.0

    def test_add_loss(self, risk_manager: RiskManager) -> None:
        """Add realized loss to daily P&L."""
        risk_manager.update_daily_pnl(-50.0)
        assert risk_manager.daily_pnl == -50.0

    def test_cumulative_updates(self, risk_manager: RiskManager) -> None:
        """Multiple updates accumulate correctly."""
        risk_manager.update_daily_pnl(100.0)
        risk_manager.update_daily_pnl(-30.0)
        risk_manager.update_daily_pnl(20.0)
        assert risk_manager.daily_pnl == 90.0

    def test_daily_loss_limit_halts_trading(self, risk_manager: RiskManager) -> None:
        """Breaching daily loss limit halts trading."""
        risk_manager.update_daily_pnl(-200.0)
        assert risk_manager.is_trading_allowed() is False
        assert risk_manager._trading_halted is True

    def test_invalid_pnl_value(self, risk_manager: RiskManager) -> None:
        """Invalid P&L value raises ValueError."""
        import math

        with pytest.raises(ValueError, match="Invalid realized_pnl"):
            risk_manager.update_daily_pnl(math.nan)


# =============================================================================
# Daily Reset Tests
# =============================================================================


class TestResetDaily:
    """Tests for reset_daily functionality."""

    def test_reset_clears_pnl(self, risk_manager: RiskManager) -> None:
        """Reset clears daily P&L."""
        risk_manager.update_daily_pnl(-100.0)
        risk_manager.reset_daily()
        assert risk_manager.daily_pnl == 0.0

    def test_reset_resumes_trading(self, risk_manager: RiskManager) -> None:
        """Reset resumes trading after halt."""
        risk_manager.update_daily_pnl(-250.0)
        assert risk_manager.is_trading_allowed() is False

        risk_manager.reset_daily()
        assert risk_manager.is_trading_allowed() is True
        assert risk_manager._trading_halted is False

    def test_reset_preserves_positions(self, risk_manager: RiskManager) -> None:
        """Reset preserves existing positions."""
        position = create_position("TICKER-A", size=50)
        risk_manager.register_position("TICKER-A", position)
        risk_manager.reset_daily()

        assert "TICKER-A" in risk_manager.positions
        assert risk_manager.positions["TICKER-A"].size == 50


# =============================================================================
# Risk Metrics Tests
# =============================================================================


class TestGetRiskMetrics:
    """Tests for get_risk_metrics functionality."""

    def test_empty_metrics(self, risk_manager: RiskManager) -> None:
        """Metrics for empty risk manager."""
        metrics = risk_manager.get_risk_metrics()

        assert metrics["total_position"] == 0
        assert metrics["daily_pnl"] == 0.0
        assert metrics["total_unrealized_pnl"] == 0.0
        assert metrics["position_limit_utilization"] == 0.0
        assert metrics["total_limit_utilization"] == 0.0
        assert metrics["daily_loss_utilization"] == 0
        assert metrics["trading_halted"] is False
        assert metrics["positions"] == {}

    def test_metrics_with_positions(self, risk_manager: RiskManager) -> None:
        """Metrics with multiple positions."""
        risk_manager.register_position(
            "TICKER-A",
            create_position("TICKER-A", size=50, unrealized_pnl=10.0),
        )
        risk_manager.register_position(
            "TICKER-B",
            create_position("TICKER-B", size=-30, unrealized_pnl=-5.0),
        )
        risk_manager.update_daily_pnl(-20.0)

        metrics = risk_manager.get_risk_metrics()

        assert metrics["total_position"] == 80  # 50 + 30
        assert metrics["daily_pnl"] == -20.0
        assert metrics["total_unrealized_pnl"] == 5.0  # 10 - 5
        assert metrics["position_limit_utilization"] == 0.5  # 50/100
        assert metrics["total_limit_utilization"] == 0.16  # 80/500
        assert len(metrics["positions"]) == 2

    def test_metrics_trading_halted(self, risk_manager: RiskManager) -> None:
        """Metrics reflect trading halted state."""
        risk_manager.update_daily_pnl(-250.0)

        metrics = risk_manager.get_risk_metrics()
        assert metrics["trading_halted"] is True


# =============================================================================
# Is Trading Allowed Tests
# =============================================================================


class TestIsTradingAllowed:
    """Tests for is_trading_allowed functionality."""

    def test_trading_allowed_initially(self, risk_manager: RiskManager) -> None:
        """Trading is allowed initially."""
        assert risk_manager.is_trading_allowed() is True

    def test_trading_blocked_by_daily_loss(self, risk_manager: RiskManager) -> None:
        """Trading blocked when daily loss limit breached."""
        risk_manager.update_daily_pnl(-200.0)
        assert risk_manager.is_trading_allowed() is False

    def test_trading_blocked_by_total_unrealized(
        self, risk_manager: RiskManager
    ) -> None:
        """Trading blocked when total loss (realized + unrealized) breaches limit."""
        risk_manager.update_daily_pnl(-150.0)
        risk_manager.register_position(
            "TICKER-A",
            create_position("TICKER-A", size=50, unrealized_pnl=-60.0),
        )
        # Total = 150 + 60 = 210 > 200
        assert risk_manager.is_trading_allowed() is False


# =============================================================================
# Edge Cases Tests
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_exactly_at_position_limit(self, strict_risk_manager: RiskManager) -> None:
        """Trade exactly at position limit is allowed."""
        allowed, _ = strict_risk_manager.can_trade("TICKER-A", "buy", 10)
        assert allowed is True

    def test_one_over_position_limit(self, strict_risk_manager: RiskManager) -> None:
        """Trade one over position limit is blocked."""
        allowed, _ = strict_risk_manager.can_trade("TICKER-A", "buy", 11)
        assert allowed is False

    def test_exactly_at_total_limit(self, strict_risk_manager: RiskManager) -> None:
        """Total position exactly at limit."""
        strict_risk_manager.register_position(
            "TICKER-A", create_position("TICKER-A", size=10)
        )
        allowed, _ = strict_risk_manager.can_trade("TICKER-B", "buy", 10)
        assert allowed is True

    def test_one_over_total_limit(self, strict_risk_manager: RiskManager) -> None:
        """Total position one over limit."""
        strict_risk_manager.register_position(
            "TICKER-A", create_position("TICKER-A", size=10)
        )
        allowed, _ = strict_risk_manager.can_trade("TICKER-B", "buy", 11)
        assert allowed is False

    def test_position_size_zero_existing(self, risk_manager: RiskManager) -> None:
        """Trading with existing flat position."""
        existing = create_position("TICKER-A", size=0)
        allowed, _ = risk_manager.can_trade(
            "TICKER-A", "buy", 50, current_position=existing
        )
        assert allowed is True

    def test_loss_exactly_at_threshold(self, strict_risk_manager: RiskManager) -> None:
        """Loss exactly at threshold triggers force close."""
        position = create_position("TICKER-A", size=50, unrealized_pnl=-10.0)
        assert strict_risk_manager.should_force_close("TICKER-A", position) is True

    def test_loss_just_below_threshold(self, strict_risk_manager: RiskManager) -> None:
        """Loss just below threshold doesn't trigger force close."""
        position = create_position("TICKER-A", size=50, unrealized_pnl=-9.99)
        assert strict_risk_manager.should_force_close("TICKER-A", position) is False

    def test_daily_loss_exactly_at_limit(self, strict_risk_manager: RiskManager) -> None:
        """Daily loss exactly at limit halts trading."""
        strict_risk_manager.update_daily_pnl(-25.0)
        assert strict_risk_manager.is_trading_allowed() is False

    def test_daily_loss_just_below_limit(self, strict_risk_manager: RiskManager) -> None:
        """Daily loss just below limit allows trading."""
        strict_risk_manager.update_daily_pnl(-24.99)
        assert strict_risk_manager.is_trading_allowed() is True


# =============================================================================
# Multiple Markets Tests
# =============================================================================


class TestMultipleMarkets:
    """Tests for handling multiple markets simultaneously."""

    def test_independent_position_tracking(self, risk_manager: RiskManager) -> None:
        """Positions in different markets are tracked independently."""
        risk_manager.register_position(
            "TICKER-A", create_position("TICKER-A", size=50)
        )
        risk_manager.register_position(
            "TICKER-B", create_position("TICKER-B", size=-30)
        )
        risk_manager.register_position(
            "TICKER-C", create_position("TICKER-C", size=20)
        )

        assert len(risk_manager.positions) == 3
        assert risk_manager.positions["TICKER-A"].size == 50
        assert risk_manager.positions["TICKER-B"].size == -30
        assert risk_manager.positions["TICKER-C"].size == 20

    def test_total_exposure_calculation(self, risk_manager: RiskManager) -> None:
        """Total exposure calculated correctly across markets."""
        risk_manager.register_position(
            "TICKER-A", create_position("TICKER-A", size=50)
        )
        risk_manager.register_position(
            "TICKER-B", create_position("TICKER-B", size=-30)
        )

        metrics = risk_manager.get_risk_metrics()
        assert metrics["total_position"] == 80  # |50| + |-30|

    def test_force_close_specific_market(self, risk_manager: RiskManager) -> None:
        """Force close check is market-specific."""
        ok_position = create_position("TICKER-A", size=50, unrealized_pnl=-10.0)
        bad_position = create_position("TICKER-B", size=50, unrealized_pnl=-60.0)

        risk_manager.register_position("TICKER-A", ok_position)
        risk_manager.register_position("TICKER-B", bad_position)

        assert risk_manager.should_force_close("TICKER-A", ok_position) is False
        assert risk_manager.should_force_close("TICKER-B", bad_position) is True


# =============================================================================
# RiskConfig Validation Tests
# =============================================================================


class TestRiskConfigValidation:
    """Tests for RiskConfig validation."""

    def test_valid_config(self) -> None:
        """Valid config passes validation."""
        config = RiskConfig(
            max_position_size=100,
            max_total_position=500,
            max_loss_per_position=50.0,
            max_daily_loss=200.0,
        )
        assert config.max_position_size == 100

    def test_invalid_position_size_zero(self) -> None:
        """Zero position size is rejected."""
        with pytest.raises(ValueError, match="max_position_size must be positive"):
            RiskConfig(max_position_size=0)

    def test_invalid_position_size_negative(self) -> None:
        """Negative position size is rejected."""
        with pytest.raises(ValueError, match="max_position_size must be positive"):
            RiskConfig(max_position_size=-10)

    def test_invalid_total_less_than_single(self) -> None:
        """Total position less than single position is rejected."""
        with pytest.raises(ValueError, match="must be >="):
            RiskConfig(max_position_size=100, max_total_position=50)

    def test_invalid_daily_less_than_position_loss(self) -> None:
        """Daily loss less than position loss is rejected."""
        with pytest.raises(ValueError, match="must be >="):
            RiskConfig(max_loss_per_position=100.0, max_daily_loss=50.0)

    def test_invalid_warning_threshold(self) -> None:
        """Warning threshold outside 0-1 is rejected."""
        with pytest.raises(ValueError, match="warning_threshold_pct must be between"):
            RiskConfig(warning_threshold_pct=1.5)

    def test_invalid_critical_threshold(self) -> None:
        """Critical threshold outside 0-1 is rejected."""
        with pytest.raises(ValueError, match="critical_threshold_pct must be between"):
            RiskConfig(critical_threshold_pct=0.0)

    def test_invalid_warning_greater_than_critical(self) -> None:
        """Warning >= critical is rejected."""
        with pytest.raises(ValueError, match="must be <"):
            RiskConfig(warning_threshold_pct=0.95, critical_threshold_pct=0.90)

    def test_config_from_dict(self) -> None:
        """Config can be created from dictionary."""
        data = {
            "max_position_size": 200,
            "max_total_position": 1000,
            "max_loss_per_position": 75.0,
            "max_daily_loss": 300.0,
        }
        config = RiskConfig.from_dict(data)
        assert config.max_position_size == 200
        assert config.max_total_position == 1000
