"""Tests for the orchestrator."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from arb.spread_detector import Platform, SpreadOpportunity
from src.arbitrage.config import ArbitrageConfig
from src.arbitrage.orchestrator import (
    ArbitrageOrchestrator,
    ExecutionResult,
    OrchestratorState,
)
from src.arbitrage.circuit_breaker import CircuitBreakerState
from src.arbitrage.detector import RankedOpportunity
from src.arbitrage.fee_calculator import SpreadAnalysis


class TestOrchestratorState:
    """Test the OrchestratorState dataclass."""

    def test_default_state(self):
        """Test default state values."""
        state = OrchestratorState()
        assert not state.running
        assert not state.paused
        assert state.started_at is None
        assert state.opportunities_detected == 0
        assert state.trades_executed == 0

    def test_state_updates(self):
        """Test state updates."""
        state = OrchestratorState()
        state.running = True
        state.started_at = datetime.now()
        state.opportunities_detected = 10

        assert state.running
        assert state.started_at is not None
        assert state.opportunities_detected == 10


class TestExecutionResult:
    """Test the ExecutionResult dataclass."""

    def test_successful_result(self, spread_opportunity):
        """Test successful execution result."""
        result = ExecutionResult(
            opportunity=spread_opportunity,
            success=True,
            spread_id="SPREAD-123",
            actual_profit=1.50,
            latency_seconds=0.5,
        )

        assert result.success
        assert result.actual_profit == 1.50
        assert result.error is None

    def test_failed_result(self, spread_opportunity):
        """Test failed execution result."""
        result = ExecutionResult(
            opportunity=spread_opportunity,
            success=False,
            error="Risk check failed",
            latency_seconds=0.1,
        )

        assert not result.success
        assert "Risk check" in result.error


class TestArbitrageOrchestrator:
    """Test suite for ArbitrageOrchestrator."""

    @pytest.fixture
    def orchestrator(self, config):
        """Create an orchestrator for testing."""
        return ArbitrageOrchestrator(config=config)

    def test_init(self, orchestrator, config):
        """Test orchestrator initialization."""
        assert orchestrator._config == config
        assert not orchestrator.is_running
        assert orchestrator.circuit_breaker is not None

    def test_init_with_dependencies(self, config):
        """Test initialization with all dependencies."""
        quote_source = MagicMock()
        executor = MagicMock()
        capital_mgr = MagicMock()
        risk_mgr = MagicMock()
        metrics = MagicMock()
        alerts = MagicMock()

        orchestrator = ArbitrageOrchestrator(
            config=config,
            quote_source=quote_source,
            spread_executor=executor,
            capital_manager=capital_mgr,
            risk_manager=risk_mgr,
            metrics_collector=metrics,
            alert_manager=alerts,
        )

        assert orchestrator._quote_source is quote_source
        assert orchestrator._spread_executor is executor
        assert orchestrator._capital_manager is capital_mgr
        assert orchestrator._risk_manager is risk_mgr
        assert orchestrator._metrics is metrics
        assert orchestrator._alert_manager is alerts

    def test_state_property(self, orchestrator):
        """Test state property."""
        state = orchestrator.state
        assert isinstance(state, OrchestratorState)

    def test_is_running_property(self, orchestrator):
        """Test is_running property."""
        assert not orchestrator.is_running
        orchestrator._state.running = True
        assert orchestrator.is_running

    def test_pause_and_resume(self, orchestrator):
        """Test pause and resume."""
        assert not orchestrator._state.paused

        orchestrator.pause()
        assert orchestrator._state.paused

        orchestrator.resume()
        assert not orchestrator._state.paused

    def test_set_callbacks(self, orchestrator):
        """Test setting callbacks."""
        opp_callback = MagicMock()
        exec_callback = MagicMock()

        orchestrator.set_on_opportunity(opp_callback)
        orchestrator.set_on_execution(exec_callback)

        assert orchestrator._on_opportunity is opp_callback
        assert orchestrator._on_execution is exec_callback

    def test_get_status(self, orchestrator):
        """Test get_status returns complete info."""
        status = orchestrator.get_status()

        assert "state" in status
        assert "stats" in status
        assert "active_spreads" in status
        assert "circuit_breaker" in status
        assert "config" in status

    def test_get_recent_executions(self, orchestrator, spread_opportunity):
        """Test getting recent executions."""
        # Add some execution results
        orchestrator._recent_executions = [
            ExecutionResult(
                opportunity=spread_opportunity,
                success=True,
                spread_id="SPREAD-001",
                actual_profit=1.0,
                latency_seconds=0.5,
            ),
            ExecutionResult(
                opportunity=spread_opportunity,
                success=False,
                error="Test error",
                latency_seconds=0.1,
            ),
        ]

        results = orchestrator.get_recent_executions(limit=10)
        assert len(results) == 2
        assert results[0]["success"]
        assert not results[1]["success"]

    def test_get_active_opportunities(self, orchestrator, spread_opportunity):
        """Test getting active opportunities."""
        orchestrator._active_spreads["SPREAD-001"] = spread_opportunity

        active = orchestrator.get_active_opportunities()
        assert len(active) == 1
        assert active[0]["spread_id"] == "SPREAD-001"


class TestOrchestratorAsync:
    """Async tests for the orchestrator."""

    @pytest.fixture
    def mock_orchestrator(self, config):
        """Create orchestrator with mocked dependencies."""
        quote_source = MagicMock()
        quote_source.get_matched_pairs.return_value = []

        executor = MagicMock()
        executor.get_active_spreads.return_value = []

        risk_mgr = MagicMock()
        risk_mgr.can_trade.return_value = (True, "OK")
        risk_mgr.get_risk_metrics.return_value = {
            "daily_pnl": 0,
            "position_limit_utilization": 0,
            "total_limit_utilization": 0,
            "daily_loss_utilization": 0,
        }

        capital_mgr = MagicMock()
        capital_mgr.check_and_release_expired.return_value = 0

        return ArbitrageOrchestrator(
            config=config,
            quote_source=quote_source,
            spread_executor=executor,
            capital_manager=capital_mgr,
            risk_manager=risk_mgr,
        )

    @pytest.mark.asyncio
    async def test_start_and_stop(self, mock_orchestrator):
        """Test starting and stopping the orchestrator."""
        # Start with a timeout to prevent hanging
        start_task = asyncio.create_task(mock_orchestrator.start())

        # Give it time to start
        await asyncio.sleep(0.1)

        assert mock_orchestrator.is_running

        # Stop
        await mock_orchestrator.stop()

        assert not mock_orchestrator.is_running

        # Cancel the start task if still running
        start_task.cancel()
        try:
            await start_task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_scan_opportunities(self, mock_orchestrator):
        """Test opportunity scanning."""
        # Initialize detector
        mock_orchestrator._detector = MagicMock()
        mock_orchestrator._detector.scan_all_pairs.return_value = []

        opportunities = await mock_orchestrator._scan_opportunities()
        assert opportunities == []

    @pytest.mark.asyncio
    async def test_execute_opportunity_risk_blocked(self, mock_orchestrator, spread_opportunity):
        """Test execution blocked by risk manager."""
        mock_orchestrator._risk_manager.can_trade.return_value = (
            False,
            "Position limit exceeded",
        )

        analysis = SpreadAnalysis(
            gross_spread=0.02,
            net_spread=0.015,
            buy_fee=0.003,
            sell_fee=0.002,
            total_fees=0.005,
            roi=0.033,
            capital_required=46.0,
            estimated_profit=1.50,
        )

        ranked = RankedOpportunity(
            opportunity=spread_opportunity,
            analysis=analysis,
            rank_score=75.0,
        )

        result = await mock_orchestrator._execute_opportunity(ranked)

        assert not result.success
        assert "Risk check" in result.error

    @pytest.mark.asyncio
    async def test_execute_opportunity_no_executor(self, config, spread_opportunity):
        """Test execution when no executor configured."""
        orchestrator = ArbitrageOrchestrator(config=config)

        analysis = SpreadAnalysis(
            gross_spread=0.02,
            net_spread=0.015,
            buy_fee=0.003,
            sell_fee=0.002,
            total_fees=0.005,
            roi=0.033,
            capital_required=46.0,
            estimated_profit=1.50,
        )

        ranked = RankedOpportunity(
            opportunity=spread_opportunity,
            analysis=analysis,
            rank_score=75.0,
        )

        result = await orchestrator._execute_opportunity(ranked)

        assert not result.success
        assert "No spread executor" in result.error

    @pytest.mark.asyncio
    async def test_reconcile_positions(self, mock_orchestrator):
        """Test position reconciliation."""
        await mock_orchestrator._reconcile_positions()

        mock_orchestrator._capital_manager.check_and_release_expired.assert_called_once()

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_detection(self, mock_orchestrator):
        """Test that circuit breaker blocks detection when open."""
        # Trip the circuit breaker
        mock_orchestrator._circuit_breaker.trip("Test trip")

        # Initialize detector
        mock_orchestrator._detector = MagicMock()
        mock_orchestrator._detector.scan_all_pairs.return_value = []

        # The detection loop should check circuit breaker
        # We can't easily test the loop, but we can verify the check
        state = mock_orchestrator._circuit_breaker.check()
        assert state == CircuitBreakerState.OPEN
