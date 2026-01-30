"""Tests for preflight checks."""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.arbitrage.config import ArbitrageConfig
from src.arbitrage.preflight import (
    CheckResult,
    CheckStatus,
    PreflightChecker,
    PreflightResult,
    run_preflight,
)


@pytest.fixture
def config():
    """Create test configuration."""
    return ArbitrageConfig(paper_mode=True)


@pytest.fixture
def mock_kalshi_client():
    """Create mock Kalshi client."""
    client = AsyncMock()

    # Mock balance response
    balance = MagicMock()
    balance.balance_dollars = 500.0
    client.get_balance.return_value = balance

    # Mock positions
    client.get_positions.return_value = []

    # Mock exchange status
    client.get_exchange_status.return_value = {"status": "open"}

    return client


@pytest.fixture
def mock_polymarket_client():
    """Create mock Polymarket client."""
    client = AsyncMock()

    # Mock balance
    client.get_balance.return_value = 500.0

    # Mock positions
    client.get_open_orders.return_value = []

    # Mock health check
    client.health_check.return_value = "healthy"

    return client


@pytest.fixture
def mock_database():
    """Create mock database manager."""
    db = AsyncMock()
    db.health_check.return_value = True
    return db


@pytest.fixture
def mock_recovery():
    """Create mock recovery service."""
    recovery = AsyncMock()
    recovery.recover_all.return_value = []
    return recovery


@pytest.fixture
def checker(mock_kalshi_client, mock_polymarket_client, mock_database, mock_recovery, config):
    """Create preflight checker with all mocks."""
    return PreflightChecker(
        kalshi_client=mock_kalshi_client,
        polymarket_client=mock_polymarket_client,
        database_manager=mock_database,
        recovery_service=mock_recovery,
        config=config,
    )


class TestCheckResult:
    """Tests for CheckResult dataclass."""

    def test_passed_property(self):
        """Test passed property."""
        result = CheckResult(
            name="test",
            status=CheckStatus.PASSED,
            message="OK",
        )
        assert result.passed
        assert not result.failed

    def test_failed_property(self):
        """Test failed property."""
        result = CheckResult(
            name="test",
            status=CheckStatus.FAILED,
            message="Error",
        )
        assert result.failed
        assert not result.passed

    def test_warning_not_failed(self):
        """Test that warning is not considered failed."""
        result = CheckResult(
            name="test",
            status=CheckStatus.WARNING,
            message="Warning",
        )
        assert not result.failed
        assert not result.passed


class TestPreflightResult:
    """Tests for PreflightResult dataclass."""

    def test_passed_when_all_pass(self):
        """Test passed property when all checks pass."""
        result = PreflightResult(
            checks=[
                CheckResult(name="check1", status=CheckStatus.PASSED, message="OK"),
                CheckResult(name="check2", status=CheckStatus.PASSED, message="OK"),
            ]
        )
        assert result.passed
        assert len(result.failures) == 0

    def test_failed_when_any_fails(self):
        """Test passed property when any check fails."""
        result = PreflightResult(
            checks=[
                CheckResult(name="check1", status=CheckStatus.PASSED, message="OK"),
                CheckResult(name="check2", status=CheckStatus.FAILED, message="Error"),
            ]
        )
        assert not result.passed
        assert len(result.failures) == 1

    def test_warnings_list(self):
        """Test warnings property."""
        result = PreflightResult(
            checks=[
                CheckResult(name="check1", status=CheckStatus.PASSED, message="OK"),
                CheckResult(name="check2", status=CheckStatus.WARNING, message="Warn"),
            ]
        )
        assert result.passed  # Warnings don't cause failure
        assert len(result.warnings) == 1

    def test_summary(self):
        """Test summary generation."""
        result = PreflightResult(
            checks=[
                CheckResult(name="check1", status=CheckStatus.PASSED, message="OK", duration_ms=10),
                CheckResult(name="check2", status=CheckStatus.FAILED, message="Error", duration_ms=20),
            ]
        )
        summary = result.summary()
        assert "1 passed" in summary
        assert "1 failed" in summary
        assert "FAILED" in summary


class TestPreflightChecker:
    """Tests for PreflightChecker."""

    @pytest.mark.asyncio
    async def test_run_all_checks_success(self, checker):
        """Test running all checks successfully."""
        result = await checker.run_all_checks()

        assert result.passed
        assert len(result.checks) == 9
        assert result.started_at is not None
        assert result.completed_at is not None

    @pytest.mark.asyncio
    async def test_kalshi_api_check_success(self, checker):
        """Test Kalshi API health check."""
        result = await checker._check_kalshi_api()

        assert result.status == CheckStatus.PASSED
        assert "reachable" in result.message.lower()

    @pytest.mark.asyncio
    async def test_kalshi_api_check_failure(self, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test Kalshi API health check failure."""
        kalshi = AsyncMock()
        kalshi.get_exchange_status.side_effect = Exception("Connection refused")

        checker = PreflightChecker(
            kalshi_client=kalshi,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
        )

        result = await checker._check_kalshi_api()
        assert result.status == CheckStatus.FAILED
        assert "unreachable" in result.message.lower()

    @pytest.mark.asyncio
    async def test_kalshi_api_check_skipped(self, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test Kalshi API check skipped when no client."""
        checker = PreflightChecker(
            kalshi_client=None,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
        )

        result = await checker._check_kalshi_api()
        assert result.status == CheckStatus.SKIPPED

    @pytest.mark.asyncio
    async def test_balance_check_success(self, checker):
        """Test balance check passes with sufficient funds."""
        result = await checker._check_kalshi_balance()

        assert result.status == CheckStatus.PASSED
        assert "$500.00" in result.message
        assert checker._kalshi_balance == 500.0

    @pytest.mark.asyncio
    async def test_balance_check_insufficient(self, mock_kalshi_client, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test balance check fails with insufficient funds."""
        # Set low balance
        balance = MagicMock()
        balance.balance_dollars = 50.0
        mock_kalshi_client.get_balance.return_value = balance

        checker = PreflightChecker(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
            min_balance_kalshi=100.0,
        )

        result = await checker._check_kalshi_balance()
        assert result.status == CheckStatus.FAILED
        assert "below minimum" in result.message.lower()

    @pytest.mark.asyncio
    async def test_positions_check_no_unexpected(self, checker):
        """Test positions check passes with no unexpected positions."""
        result = await checker._check_kalshi_positions()

        assert result.status == CheckStatus.PASSED
        assert "OK" in result.message

    @pytest.mark.asyncio
    async def test_positions_check_unexpected_warning(self, mock_kalshi_client, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test positions check warns on unexpected positions."""
        # Add unexpected position
        position = MagicMock()
        position.ticker = "UNEXPECTED-YES"
        position.position = 100
        mock_kalshi_client.get_positions.return_value = [position]

        checker = PreflightChecker(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
            expected_positions={"kalshi": []},  # No expected positions
        )

        result = await checker._check_kalshi_positions()
        assert result.status == CheckStatus.WARNING
        assert "unexpected" in result.message.lower()

    @pytest.mark.asyncio
    async def test_positions_check_expected_ok(self, mock_kalshi_client, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test positions check passes with expected positions."""
        # Add expected position
        position = MagicMock()
        position.ticker = "EXPECTED-YES"
        position.position = 100
        mock_kalshi_client.get_positions.return_value = [position]

        checker = PreflightChecker(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
            expected_positions={"kalshi": ["EXPECTED-YES"]},
        )

        result = await checker._check_kalshi_positions()
        assert result.status == CheckStatus.PASSED

    @pytest.mark.asyncio
    async def test_database_check_success(self, checker):
        """Test database connectivity check."""
        result = await checker._check_database()

        assert result.status == CheckStatus.PASSED
        assert "reachable" in result.message.lower()

    @pytest.mark.asyncio
    async def test_database_check_failure(self, mock_kalshi_client, mock_polymarket_client, mock_recovery, config):
        """Test database check failure."""
        db = AsyncMock()
        db.health_check.return_value = False

        checker = PreflightChecker(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=db,
            recovery_service=mock_recovery,
            config=config,
        )

        result = await checker._check_database()
        assert result.status == CheckStatus.FAILED

    @pytest.mark.asyncio
    async def test_incomplete_spreads_none(self, checker):
        """Test incomplete spreads check with none found."""
        result = await checker._check_incomplete_spreads()

        assert result.status == CheckStatus.PASSED
        assert "no incomplete" in result.message.lower()

    @pytest.mark.asyncio
    async def test_incomplete_spreads_recovered(self, mock_kalshi_client, mock_polymarket_client, mock_database, config):
        """Test incomplete spreads recovered."""
        recovery = AsyncMock()

        # Mock successful recovery
        recovery_result = MagicMock()
        recovery_result.success = True
        recovery_result.spread_id = "SPREAD-123"
        recovery.recover_all.return_value = [recovery_result]

        checker = PreflightChecker(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=recovery,
            config=config,
        )

        result = await checker._check_incomplete_spreads()
        assert result.status == CheckStatus.PASSED
        assert "recovered 1" in result.message.lower()

    @pytest.mark.asyncio
    async def test_incomplete_spreads_failed_recovery(self, mock_kalshi_client, mock_polymarket_client, mock_database, config):
        """Test incomplete spreads with failed recovery."""
        recovery = AsyncMock()

        # Mock failed recovery
        recovery_result = MagicMock()
        recovery_result.success = False
        recovery_result.spread_id = "SPREAD-123"
        recovery_result.message = "Recovery failed"
        recovery.recover_all.return_value = [recovery_result]

        checker = PreflightChecker(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=recovery,
            config=config,
        )

        result = await checker._check_incomplete_spreads()
        assert result.status == CheckStatus.FAILED
        assert "failed recovery" in result.message.lower()

    @pytest.mark.asyncio
    async def test_config_check_valid(self, checker):
        """Test config validation check."""
        result = await checker._check_config()

        assert result.status == CheckStatus.PASSED
        assert "valid" in result.message.lower()

    @pytest.mark.asyncio
    async def test_config_check_live_mode_warning(self, mock_kalshi_client, mock_polymarket_client, mock_database, mock_recovery):
        """Test config warns on live mode."""
        config = ArbitrageConfig(paper_mode=False)

        checker = PreflightChecker(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
        )

        result = await checker._check_config()
        assert result.status == CheckStatus.WARNING
        assert "LIVE" in result.message

    @pytest.mark.asyncio
    async def test_get_balance_summary(self, checker):
        """Test balance summary."""
        # Run balance checks first
        await checker._check_kalshi_balance()
        await checker._check_polymarket_balance()

        summary = checker.get_balance_summary()
        assert summary["kalshi"] == 500.0
        assert summary["polymarket"] == 500.0
        assert summary["total"] == 1000.0

    @pytest.mark.asyncio
    async def test_check_timeout(self, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test that slow checks timeout."""
        import asyncio

        kalshi = AsyncMock()

        async def slow_operation():
            await asyncio.sleep(60)  # Longer than timeout
            return {"status": "open"}

        kalshi.get_exchange_status = slow_operation

        checker = PreflightChecker(
            kalshi_client=kalshi,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
        )
        checker.CHECK_TIMEOUT_SECONDS = 0.1  # Short timeout for test

        result = await checker.run_all_checks()

        # Find the kalshi check
        kalshi_check = next(c for c in result.checks if c.name == "kalshi_api_health")
        assert kalshi_check.status == CheckStatus.FAILED
        assert "timed out" in kalshi_check.message.lower()


class TestRunPreflight:
    """Tests for run_preflight convenience function."""

    @pytest.mark.asyncio
    async def test_run_preflight_success(self, mock_kalshi_client, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test run_preflight succeeds."""
        result = await run_preflight(
            kalshi_client=mock_kalshi_client,
            polymarket_client=mock_polymarket_client,
            database_manager=mock_database,
            recovery_service=mock_recovery,
            config=config,
            exit_on_failure=False,
        )

        assert result.passed

    @pytest.mark.asyncio
    async def test_run_preflight_failure_raises(self, mock_polymarket_client, mock_database, mock_recovery, config):
        """Test run_preflight raises on failure."""
        kalshi = AsyncMock()
        kalshi.get_exchange_status.side_effect = Exception("Connection refused")

        # Set low balance to trigger failure
        balance = MagicMock()
        balance.balance_dollars = 10.0
        kalshi.get_balance.return_value = balance
        kalshi.get_positions.return_value = []

        with pytest.raises(SystemExit) as exc_info:
            await run_preflight(
                kalshi_client=kalshi,
                polymarket_client=mock_polymarket_client,
                database_manager=mock_database,
                recovery_service=mock_recovery,
                config=config,
                exit_on_failure=True,
            )

        assert exc_info.value.code == 1
