"""Arbitrage Orchestrator - Main loop connecting detection to execution.

The orchestrator ties together:
- OpportunityDetector: Scans for profitable spreads
- SpreadExecutor: Executes two-leg trades with rollback
- CapitalManager: Manages capital reservations
- RiskManager: Enforces position and loss limits
- CircuitBreaker: System-level safety controls
- MetricsCollector: Records trading metrics
"""

import asyncio
import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from arb.spread_detector import SpreadOpportunity

from .config import ArbitrageConfig
from .circuit_breaker import CircuitBreaker, CircuitBreakerState
from .detector import OpportunityDetector, RankedOpportunity
from .fee_calculator import FeeCalculator


logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of attempting to execute an opportunity.

    Attributes:
        opportunity: The opportunity that was attempted
        success: Whether execution succeeded
        spread_id: ID of the spread execution (if submitted)
        actual_profit: Actual profit (if completed)
        error: Error message (if failed)
        latency_seconds: Execution time
    """

    opportunity: SpreadOpportunity
    success: bool
    spread_id: Optional[str] = None
    actual_profit: Optional[float] = None
    error: Optional[str] = None
    latency_seconds: float = 0.0


@dataclass
class OrchestratorState:
    """Current state of the orchestrator.

    Attributes:
        running: Whether the main loop is running
        paused: Whether execution is paused (detection continues)
        started_at: When the orchestrator started
        last_scan_at: When the last scan completed
        opportunities_detected: Total opportunities found
        trades_executed: Total trades attempted
        successful_trades: Total successful trades
        total_profit: Cumulative profit
    """

    running: bool = False
    paused: bool = False
    started_at: Optional[datetime] = None
    last_scan_at: Optional[datetime] = None
    opportunities_detected: int = 0
    trades_executed: int = 0
    successful_trades: int = 0
    total_profit: float = 0.0


class ArbitrageOrchestrator:
    """Main orchestrator for the arbitrage system.

    Coordinates the detection loop, execution pipeline, and all subsystems.
    Supports graceful shutdown via signals and programmatic control.

    Example:
        # Create orchestrator with all dependencies
        orchestrator = ArbitrageOrchestrator(
            config=config,
            quote_source=quote_provider,
            spread_executor=executor,
            capital_manager=capital_mgr,
            risk_manager=risk_mgr,
        )

        # Start the main loop
        await orchestrator.start()

        # ... runs until stopped or signal received ...

        # Graceful shutdown
        await orchestrator.stop()
    """

    def __init__(
        self,
        config: Optional[ArbitrageConfig] = None,
        quote_source: Optional[Any] = None,
        spread_executor: Optional[Any] = None,
        capital_manager: Optional[Any] = None,
        risk_manager: Optional[Any] = None,
        metrics_collector: Optional[Any] = None,
        alert_manager: Optional[Any] = None,
    ):
        """Initialize the orchestrator.

        Args:
            config: Arbitrage configuration
            quote_source: Source for market quotes (implements QuoteSource protocol)
            spread_executor: SpreadExecutor for trade execution
            capital_manager: CapitalManager for capital reservations
            risk_manager: RiskManager for position limits
            metrics_collector: MetricsCollector for recording metrics
            alert_manager: AlertManager for sending alerts
        """
        self._config = config or ArbitrageConfig()
        self._quote_source = quote_source
        self._spread_executor = spread_executor
        self._capital_manager = capital_manager
        self._risk_manager = risk_manager
        self._metrics = metrics_collector
        self._alert_manager = alert_manager

        # Initialize components
        self._fee_calc = FeeCalculator(self._config)
        self._circuit_breaker = CircuitBreaker(
            self._config,
            alert_callback=self._on_circuit_breaker_alert,
        )

        # Detector will be initialized when quote_source is available
        self._detector: Optional[OpportunityDetector] = None

        # State
        self._state = OrchestratorState()
        self._active_spreads: Dict[str, SpreadOpportunity] = {}
        self._recent_executions: List[ExecutionResult] = []

        # Background tasks
        self._detection_task: Optional[asyncio.Task] = None
        self._reconciliation_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_opportunity: Optional[Callable[[RankedOpportunity], None]] = None
        self._on_execution: Optional[Callable[[ExecutionResult], None]] = None

    @property
    def state(self) -> OrchestratorState:
        """Current orchestrator state."""
        return self._state

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Access to the circuit breaker."""
        return self._circuit_breaker

    @property
    def is_running(self) -> bool:
        """Whether the orchestrator is running."""
        return self._state.running

    def set_on_opportunity(self, callback: Callable[[RankedOpportunity], None]) -> None:
        """Set callback for detected opportunities.

        Args:
            callback: Function(opportunity) -> None
        """
        self._on_opportunity = callback

    def set_on_execution(self, callback: Callable[[ExecutionResult], None]) -> None:
        """Set callback for execution results.

        Args:
            callback: Function(result) -> None
        """
        self._on_execution = callback

    async def start(self) -> None:
        """Start the orchestrator main loop.

        Sets up signal handlers and starts background tasks for:
        - Detection loop (scans for opportunities)
        - Reconciliation loop (syncs positions)
        """
        if self._state.running:
            logger.warning("Orchestrator already running")
            return

        logger.info("Starting arbitrage orchestrator...")

        # Initialize detector if quote source available
        if self._quote_source and not self._detector:
            self._detector = OpportunityDetector(
                quote_source=self._quote_source,
                fee_calculator=self._fee_calc,
                config=self._config,
            )

        # Set up signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))

        # Update state
        self._state.running = True
        self._state.started_at = datetime.now()

        # Start background tasks
        self._detection_task = asyncio.create_task(
            self._detection_loop(), name="detection_loop"
        )
        self._reconciliation_task = asyncio.create_task(
            self._reconciliation_loop(), name="reconciliation_loop"
        )

        logger.info(
            "Orchestrator started (paper_mode=%s, scan_interval=%.1fs)",
            self._config.paper_mode,
            self._config.scan_interval_seconds,
        )

        # Wait for tasks
        try:
            await asyncio.gather(
                self._detection_task,
                self._reconciliation_task,
            )
        except asyncio.CancelledError:
            logger.info("Orchestrator tasks cancelled")

    async def stop(self) -> None:
        """Stop the orchestrator gracefully.

        Cancels background tasks and waits for them to complete.
        """
        if not self._state.running:
            return

        logger.info("Stopping arbitrage orchestrator...")

        self._state.running = False

        # Cancel background tasks
        if self._detection_task:
            self._detection_task.cancel()
        if self._reconciliation_task:
            self._reconciliation_task.cancel()

        # Wait for tasks to complete
        tasks = [t for t in [self._detection_task, self._reconciliation_task] if t]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("Orchestrator stopped")

    def pause(self) -> None:
        """Pause execution (detection continues but no trades executed)."""
        self._state.paused = True
        logger.info("Orchestrator paused")

    def resume(self) -> None:
        """Resume execution after pause."""
        self._state.paused = False
        logger.info("Orchestrator resumed")

    async def _detection_loop(self) -> None:
        """Main detection loop - scans for opportunities and executes."""
        logger.info("Detection loop started")

        while self._state.running:
            try:
                # Check circuit breaker
                cb_state = self._circuit_breaker.check()
                if cb_state == CircuitBreakerState.OPEN:
                    logger.warning("Circuit breaker open, skipping detection cycle")
                    await asyncio.sleep(self._config.scan_interval_seconds)
                    continue

                # Scan for opportunities
                opportunities = await self._scan_opportunities()

                if opportunities:
                    self._state.opportunities_detected += len(opportunities)

                    # Process opportunities (unless paused)
                    if not self._state.paused:
                        for opp in opportunities:
                            # Check if we can execute more spreads
                            if (
                                len(self._active_spreads)
                                >= self._config.max_concurrent_spreads
                            ):
                                logger.debug(
                                    "Max concurrent spreads reached (%d)",
                                    self._config.max_concurrent_spreads,
                                )
                                break

                            # Attempt execution
                            result = await self._execute_opportunity(opp)

                            if result.success:
                                self._state.successful_trades += 1
                                if result.actual_profit:
                                    self._state.total_profit += result.actual_profit

                self._state.last_scan_at = datetime.now()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error in detection loop: %s", e, exc_info=True)
                self._circuit_breaker.record_trade(success=False, latency=0)

            await asyncio.sleep(self._config.scan_interval_seconds)

        logger.info("Detection loop stopped")

    async def _reconciliation_loop(self) -> None:
        """Background loop for position reconciliation."""
        logger.info("Reconciliation loop started")

        while self._state.running:
            try:
                await self._reconcile_positions()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Error in reconciliation loop: %s", e, exc_info=True)

            await asyncio.sleep(self._config.reconciliation_interval_seconds)

        logger.info("Reconciliation loop stopped")

    async def _scan_opportunities(self) -> List[RankedOpportunity]:
        """Scan for opportunities using the detector.

        Returns:
            List of ranked opportunities
        """
        if not self._detector:
            return []

        start_time = time.time()

        # Run scan in executor to avoid blocking
        loop = asyncio.get_event_loop()
        opportunities = await loop.run_in_executor(None, self._detector.scan_all_pairs)

        latency = time.time() - start_time
        self._circuit_breaker.record_latency(latency)

        # Record metrics
        if self._metrics:
            for opp in opportunities:
                self._metrics.record_opportunity(
                    category=opp.opportunity.opportunity_type,
                    platforms=f"{opp.opportunity.buy_platform.value}_{opp.opportunity.sell_platform.value}",
                )

        # Callbacks
        for opp in opportunities:
            if self._on_opportunity:
                try:
                    self._on_opportunity(opp)
                except Exception as e:
                    logger.error("Opportunity callback error: %s", e)

        return opportunities

    async def _execute_opportunity(
        self, ranked_opp: RankedOpportunity
    ) -> ExecutionResult:
        """Execute a single opportunity.

        Args:
            ranked_opp: The ranked opportunity to execute

        Returns:
            ExecutionResult with outcome
        """
        opp = ranked_opp.opportunity
        start_time = time.time()

        logger.info(
            "Executing opportunity: %s %s -> %s, edge=%.4f, est_profit=$%.2f",
            opp.opportunity_type,
            opp.buy_platform.value,
            opp.sell_platform.value,
            ranked_opp.net_edge,
            ranked_opp.estimated_profit,
        )

        self._state.trades_executed += 1

        # Check risk limits
        if self._risk_manager:
            can_trade, reason = self._risk_manager.can_trade(
                ticker=opp.buy_market_id,
                side="buy",
                size=opp.max_contracts,
            )
            if not can_trade:
                logger.warning("Risk check failed: %s", reason)
                return ExecutionResult(
                    opportunity=opp,
                    success=False,
                    error=f"Risk check failed: {reason}",
                    latency_seconds=time.time() - start_time,
                )

        # Execute via spread executor
        if not self._spread_executor:
            logger.warning("No spread executor configured, skipping execution")
            return ExecutionResult(
                opportunity=opp,
                success=False,
                error="No spread executor configured",
                latency_seconds=time.time() - start_time,
            )

        try:
            # Track as active
            spread_id = f"pending-{int(time.time() * 1000)}"
            self._active_spreads[spread_id] = opp

            # Execute (sync call, may need to be async in future)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                self._spread_executor.execute_from_opportunity,
                opp,
            )

            latency = time.time() - start_time

            # Update tracking
            if spread_id in self._active_spreads:
                del self._active_spreads[spread_id]

            if result.spread_id:
                self._active_spreads[result.spread_id] = opp

            # Record to circuit breaker
            success = result.is_successful
            actual_profit = result.actual_profit or 0.0
            self._circuit_breaker.record_trade(
                success=success,
                latency=latency,
                pnl=actual_profit,
            )

            # Record order fills
            if result.leg1.order:
                self._circuit_breaker.record_order(
                    filled=result.leg1.status.value == "filled"
                )
            if result.leg2.order:
                self._circuit_breaker.record_order(
                    filled=result.leg2.status.value == "filled"
                )

            # Record metrics
            if self._metrics:
                status = "filled" if success else result.status.value
                self._metrics.record_trade(
                    platform=f"{opp.buy_platform.value}-{opp.sell_platform.value}",
                    status=status,
                    strategy="arbitrage",
                    profit=actual_profit,
                    latency=latency,
                )

            # Update risk manager
            if self._risk_manager and actual_profit != 0:
                self._risk_manager.update_daily_pnl(actual_profit)

            exec_result = ExecutionResult(
                opportunity=opp,
                success=success,
                spread_id=result.spread_id,
                actual_profit=actual_profit,
                error=result.error,
                latency_seconds=latency,
            )

            self._recent_executions.append(exec_result)
            if len(self._recent_executions) > 100:
                self._recent_executions = self._recent_executions[-100:]

            # Callback
            if self._on_execution:
                try:
                    self._on_execution(exec_result)
                except Exception as e:
                    logger.error("Execution callback error: %s", e)

            if success:
                logger.info(
                    "Execution successful: spread_id=%s, profit=$%.2f, latency=%.2fs",
                    result.spread_id,
                    actual_profit,
                    latency,
                )
            else:
                logger.warning(
                    "Execution failed: spread_id=%s, error=%s, latency=%.2fs",
                    result.spread_id,
                    result.error,
                    latency,
                )

            return exec_result

        except Exception as e:
            latency = time.time() - start_time
            logger.error("Execution error: %s", e, exc_info=True)

            # Clean up tracking
            if spread_id in self._active_spreads:
                del self._active_spreads[spread_id]

            self._circuit_breaker.record_trade(
                success=False,
                latency=latency,
            )

            return ExecutionResult(
                opportunity=opp,
                success=False,
                error=str(e),
                latency_seconds=latency,
            )

    async def _reconcile_positions(self) -> None:
        """Reconcile positions with exchanges."""
        if not self._capital_manager:
            return

        logger.debug("Running position reconciliation...")

        # Release expired capital reservations
        if hasattr(self._capital_manager, "check_and_release_expired"):
            released = self._capital_manager.check_and_release_expired()
            if released > 0:
                logger.info("Released %d expired capital reservations", released)

        # Clean up completed active spreads
        if self._spread_executor:
            active = self._spread_executor.get_active_spreads()
            active_ids = {s.spread_id for s in active}

            for spread_id in list(self._active_spreads.keys()):
                if spread_id not in active_ids and not spread_id.startswith("pending-"):
                    del self._active_spreads[spread_id]

        # Update metrics
        if self._metrics and self._risk_manager:
            risk_metrics = self._risk_manager.get_risk_metrics()
            self._metrics.update_risk_utilization(
                position_pct=risk_metrics["position_limit_utilization"] * 100,
                total_pct=risk_metrics["total_limit_utilization"] * 100,
                daily_loss_pct=risk_metrics["daily_loss_utilization"] * 100,
            )
            self._metrics.update_daily_pnl(risk_metrics["daily_pnl"])

    def _on_circuit_breaker_alert(self, reason: str, details: str) -> None:
        """Handle circuit breaker alerts.

        Args:
            reason: Why the breaker tripped
            details: Additional details
        """
        logger.critical("CIRCUIT BREAKER ALERT: %s - %s", reason, details)

        if self._alert_manager:
            try:
                asyncio.create_task(
                    self._alert_manager.send_alert(
                        name="circuit_breaker",
                        severity="critical",
                        message=f"Circuit breaker tripped: {reason}",
                        context={"reason": reason, "details": details},
                    )
                )
            except Exception as e:
                logger.error("Failed to send circuit breaker alert: %s", e)

    def get_status(self) -> Dict:
        """Get comprehensive status of the orchestrator.

        Returns:
            Dictionary with state, circuit breaker, and execution info
        """
        return {
            "state": {
                "running": self._state.running,
                "paused": self._state.paused,
                "started_at": (
                    self._state.started_at.isoformat()
                    if self._state.started_at
                    else None
                ),
                "last_scan_at": (
                    self._state.last_scan_at.isoformat()
                    if self._state.last_scan_at
                    else None
                ),
                "uptime_seconds": (
                    (datetime.now() - self._state.started_at).total_seconds()
                    if self._state.started_at
                    else 0
                ),
            },
            "stats": {
                "opportunities_detected": self._state.opportunities_detected,
                "trades_executed": self._state.trades_executed,
                "successful_trades": self._state.successful_trades,
                "total_profit": self._state.total_profit,
                "success_rate": (
                    self._state.successful_trades / self._state.trades_executed
                    if self._state.trades_executed > 0
                    else 0
                ),
            },
            "active_spreads": len(self._active_spreads),
            "circuit_breaker": self._circuit_breaker.get_status(),
            "config": {
                "paper_mode": self._config.paper_mode,
                "scan_interval": self._config.scan_interval_seconds,
                "max_concurrent_spreads": self._config.max_concurrent_spreads,
            },
            "detector_stats": (self._detector.get_stats() if self._detector else None),
        }

    def get_recent_executions(self, limit: int = 10) -> List[Dict]:
        """Get recent execution results.

        Args:
            limit: Maximum number of results to return

        Returns:
            List of execution result dictionaries
        """
        return [
            {
                "success": r.success,
                "spread_id": r.spread_id,
                "actual_profit": r.actual_profit,
                "error": r.error,
                "latency_seconds": r.latency_seconds,
                "opportunity_type": r.opportunity.opportunity_type,
                "buy_platform": r.opportunity.buy_platform.value,
                "sell_platform": r.opportunity.sell_platform.value,
            }
            for r in self._recent_executions[-limit:]
        ]

    def get_active_opportunities(self) -> List[Dict]:
        """Get currently tracked opportunities.

        Returns:
            List of active spread opportunity dictionaries
        """
        return [
            {
                "spread_id": spread_id,
                "type": opp.opportunity_type,
                "buy_platform": opp.buy_platform.value,
                "buy_price": opp.buy_price,
                "sell_platform": opp.sell_platform.value,
                "sell_price": opp.sell_price,
                "max_contracts": opp.max_contracts,
                "estimated_profit": opp.estimated_profit_usd,
            }
            for spread_id, opp in self._active_spreads.items()
        ]
