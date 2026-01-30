"""Tests for the spread recovery service."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.database.models import SpreadExecutionModel, SpreadExecutionStatus
from src.oms.recovery import (
    RecoveryAction,
    RecoveryResult,
    SpreadRecoveryService,
)


@pytest.fixture
def mock_repository():
    """Create a mock repository."""
    repo = AsyncMock()
    repo.get_incomplete_executions = AsyncMock(return_value=[])
    repo.get_by_spread_id = AsyncMock(return_value=None)
    repo.update_status = AsyncMock()
    repo.update_leg1_fill = AsyncMock()
    repo.update_leg2_fill = AsyncMock()
    repo.update_rollback = AsyncMock()
    repo.increment_recovery_attempts = AsyncMock()
    repo.mark_recovery_needed = AsyncMock()
    return repo


@pytest.fixture
def mock_kalshi_client():
    """Create a mock Kalshi client."""
    client = MagicMock()
    client.get_order = MagicMock(return_value=None)
    client.cancel_order = MagicMock(return_value=True)
    client.place_order = MagicMock(return_value=None)
    return client


@pytest.fixture
def mock_polymarket_client():
    """Create a mock Polymarket client."""
    client = MagicMock()
    client.get_order = MagicMock(return_value=None)
    client.cancel_order = MagicMock(return_value=True)
    client.place_order = MagicMock(return_value=None)
    return client


@pytest.fixture
def recovery_service(mock_repository, mock_kalshi_client, mock_polymarket_client):
    """Create a recovery service with mocked dependencies."""
    return SpreadRecoveryService(
        repository=mock_repository,
        kalshi_client=mock_kalshi_client,
        polymarket_client=mock_polymarket_client,
    )


@pytest.fixture
def incomplete_spread():
    """Create an incomplete spread execution model."""
    return SpreadExecutionModel(
        spread_id="SPREAD-TEST123",
        opportunity_id="opp-456",
        status=SpreadExecutionStatus.LEG1_FILLED,
        leg1_exchange="kalshi",
        leg1_ticker="MARKET-YES",
        leg1_side="buy",
        leg1_price=Decimal("0.45"),
        leg1_size=100,
        leg1_order_id="order-123",
        leg1_filled_size=100,
        leg1_fill_price=Decimal("0.45"),
        leg2_exchange="polymarket",
        leg2_ticker="0x123abc",
        leg2_side="sell",
        leg2_price=Decimal("0.48"),
        leg2_size=100,
        leg2_order_id=None,
        leg2_filled_size=0,
        leg2_fill_price=None,
        expected_profit=Decimal("3.00"),
        total_fees=Decimal("0.00"),
        recovery_attempts=0,
    )


class TestSpreadRecoveryService:
    """Test suite for SpreadRecoveryService."""

    @pytest.mark.asyncio
    async def test_recover_all_no_incomplete(self, recovery_service, mock_repository):
        """Test recovery when no incomplete spreads exist."""
        mock_repository.get_incomplete_executions.return_value = []

        results = await recovery_service.recover_all()

        assert results == []
        mock_repository.get_incomplete_executions.assert_called_once()

    @pytest.mark.asyncio
    async def test_recover_all_with_incomplete(
        self, recovery_service, mock_repository, incomplete_spread
    ):
        """Test recovery with incomplete spreads."""
        mock_repository.get_incomplete_executions.return_value = [incomplete_spread]
        mock_repository.get_by_spread_id.return_value = incomplete_spread

        # Mock successful leg 2 placement
        recovery_service._clients["polymarket"].place_order.return_value = {
            "order_id": "leg2-order-789",
            "status": "filled",
            "filled_size": 100,
            "avg_fill_price": 0.48,
        }

        results = await recovery_service.recover_all()

        assert len(results) == 1
        assert results[0].spread_id == "SPREAD-TEST123"

    @pytest.mark.asyncio
    async def test_determine_action_pending(self, recovery_service, incomplete_spread):
        """Test action determination for PENDING status."""
        incomplete_spread.status = SpreadExecutionStatus.PENDING
        action = recovery_service._determine_action(incomplete_spread)
        assert action == RecoveryAction.MARK_FAILED

    @pytest.mark.asyncio
    async def test_determine_action_leg1_submitted(self, recovery_service, incomplete_spread):
        """Test action determination for LEG1_SUBMITTED status."""
        incomplete_spread.status = SpreadExecutionStatus.LEG1_SUBMITTED
        action = recovery_service._determine_action(incomplete_spread)
        assert action == RecoveryAction.CHECK_ORDER

    @pytest.mark.asyncio
    async def test_determine_action_leg1_filled(self, recovery_service, incomplete_spread):
        """Test action determination for LEG1_FILLED status."""
        incomplete_spread.status = SpreadExecutionStatus.LEG1_FILLED
        incomplete_spread.leg1_filled_size = 100
        action = recovery_service._determine_action(incomplete_spread)
        assert action == RecoveryAction.COMPLETE_LEG2

    @pytest.mark.asyncio
    async def test_determine_action_leg2_submitted(self, recovery_service, incomplete_spread):
        """Test action determination for LEG2_SUBMITTED status."""
        incomplete_spread.status = SpreadExecutionStatus.LEG2_SUBMITTED
        action = recovery_service._determine_action(incomplete_spread)
        assert action == RecoveryAction.CHECK_ORDER

    @pytest.mark.asyncio
    async def test_determine_action_completed(self, recovery_service, incomplete_spread):
        """Test action determination for COMPLETED status."""
        incomplete_spread.status = SpreadExecutionStatus.COMPLETED
        action = recovery_service._determine_action(incomplete_spread)
        assert action == RecoveryAction.NO_ACTION

    @pytest.mark.asyncio
    async def test_max_recovery_attempts_exceeded(
        self, recovery_service, mock_repository, incomplete_spread
    ):
        """Test that max recovery attempts triggers escalation."""
        incomplete_spread.recovery_attempts = 5

        result = await recovery_service.recover_spread(incomplete_spread)

        assert result.action == RecoveryAction.ESCALATE
        assert not result.success
        assert "Max recovery attempts" in result.message

    @pytest.mark.asyncio
    async def test_complete_leg2_success(
        self, recovery_service, mock_repository, incomplete_spread
    ):
        """Test successful leg 2 completion."""
        mock_repository.get_by_spread_id.return_value = incomplete_spread

        recovery_service._clients["polymarket"].place_order.return_value = {
            "order_id": "leg2-order-789",
            "status": "filled",
            "filled_size": 100,
            "avg_fill_price": 0.48,
        }

        result = await recovery_service._complete_leg2(incomplete_spread)

        assert result.success
        assert result.action == RecoveryAction.COMPLETE_LEG2
        assert result.new_status == SpreadExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_complete_leg2_no_client_triggers_rollback(
        self, recovery_service, mock_repository, incomplete_spread
    ):
        """Test that missing client triggers rollback."""
        # Remove polymarket client
        del recovery_service._clients["polymarket"]

        mock_repository.get_by_spread_id.return_value = incomplete_spread

        # Mock successful rollback
        recovery_service._clients["kalshi"].place_order.return_value = {
            "order_id": "rollback-order",
            "status": "filled",
            "filled_size": 100,
        }

        result = await recovery_service._complete_leg2(incomplete_spread)

        # Should attempt rollback instead
        assert result.action == RecoveryAction.ROLLBACK_LEG1

    @pytest.mark.asyncio
    async def test_rollback_leg1_success(
        self, recovery_service, mock_repository, incomplete_spread
    ):
        """Test successful leg 1 rollback."""
        mock_repository.get_by_spread_id.return_value = incomplete_spread

        recovery_service._clients["kalshi"].place_order.return_value = {
            "order_id": "rollback-order",
            "status": "filled",
            "filled_size": 100,
        }

        result = await recovery_service._rollback_leg1(incomplete_spread)

        assert result.success
        assert result.action == RecoveryAction.ROLLBACK_LEG1
        assert result.new_status == SpreadExecutionStatus.ROLLED_BACK

    @pytest.mark.asyncio
    async def test_check_pending_order_filled(
        self, recovery_service, mock_repository, incomplete_spread
    ):
        """Test checking pending order that is filled."""
        incomplete_spread.status = SpreadExecutionStatus.LEG1_SUBMITTED
        mock_repository.get_by_spread_id.return_value = incomplete_spread

        recovery_service._clients["kalshi"].get_order.return_value = {
            "status": "filled",
            "filled_size": 100,
            "avg_fill_price": 0.45,
        }

        # Also mock successful leg 2 completion
        recovery_service._clients["polymarket"].place_order.return_value = {
            "order_id": "leg2-order",
            "status": "filled",
            "filled_size": 100,
            "avg_fill_price": 0.48,
        }

        result = await recovery_service._check_pending_order(incomplete_spread)

        # Should have checked order and then attempted leg 2
        recovery_service._clients["kalshi"].get_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_pending_order_canceled(
        self, recovery_service, mock_repository, incomplete_spread
    ):
        """Test checking pending order that was canceled."""
        incomplete_spread.status = SpreadExecutionStatus.LEG1_SUBMITTED
        mock_repository.get_by_spread_id.return_value = incomplete_spread

        recovery_service._clients["kalshi"].get_order.return_value = {
            "status": "canceled",
            "filled_size": 0,
        }

        result = await recovery_service._check_pending_order(incomplete_spread)

        assert result.action == RecoveryAction.MARK_FAILED
        assert result.new_status == SpreadExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_dry_run_mode(
        self, mock_repository, mock_kalshi_client, mock_polymarket_client, incomplete_spread
    ):
        """Test that dry run mode doesn't execute actions."""
        service = SpreadRecoveryService(
            repository=mock_repository,
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            dry_run=True,
        )

        result = await service._complete_leg2(incomplete_spread)

        assert result.success
        assert "[DRY RUN]" in result.message
        mock_polymarket_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_callback_on_escalation(
        self, mock_repository, mock_kalshi_client, mock_polymarket_client, incomplete_spread
    ):
        """Test that alert callback is called on escalation."""
        alert_callback = MagicMock()

        service = SpreadRecoveryService(
            repository=mock_repository,
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            alert_callback=alert_callback,
        )

        result = await service._escalate(incomplete_spread, "Test escalation")

        alert_callback.assert_called_once()
        call_args = alert_callback.call_args[0]
        assert "spread_recovery_escalation" in call_args[0]

    @pytest.mark.asyncio
    async def test_calculate_profit(self, recovery_service, incomplete_spread):
        """Test profit calculation."""
        profit = recovery_service._calculate_profit(
            incomplete_spread,
            leg2_filled_size=100,
            leg2_fill_price=0.48,
        )

        # Buy at 0.45, sell at 0.48 = 0.03 * 100 = 3.0 profit
        expected = Decimal("3.0") - (incomplete_spread.total_fees or Decimal("0"))
        # Use approximate comparison due to float conversion
        assert abs(float(profit) - float(expected)) < 0.01


class TestRecoveryAction:
    """Test RecoveryAction enum."""

    def test_action_values(self):
        """Test that all actions have string values."""
        assert RecoveryAction.COMPLETE_LEG2.value == "complete_leg2"
        assert RecoveryAction.ROLLBACK_LEG1.value == "rollback_leg1"
        assert RecoveryAction.ESCALATE.value == "escalate"


class TestRecoveryResult:
    """Test RecoveryResult dataclass."""

    def test_result_creation(self):
        """Test creating a recovery result."""
        result = RecoveryResult(
            spread_id="SPREAD-123",
            action=RecoveryAction.COMPLETE_LEG2,
            success=True,
            new_status=SpreadExecutionStatus.COMPLETED,
            message="Leg 2 completed",
        )

        assert result.spread_id == "SPREAD-123"
        assert result.success
        assert result.new_status == SpreadExecutionStatus.COMPLETED

    def test_result_with_details(self):
        """Test creating a recovery result with details."""
        result = RecoveryResult(
            spread_id="SPREAD-123",
            action=RecoveryAction.COMPLETE_LEG2,
            success=True,
            new_status=SpreadExecutionStatus.COMPLETED,
            message="Leg 2 completed",
            details={"profit": 1.50, "order_id": "order-456"},
        )

        assert result.details["profit"] == 1.50
        assert result.details["order_id"] == "order-456"
