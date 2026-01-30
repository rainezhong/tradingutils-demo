"""SpreadDetector integration handler for arbitrage execution.

Provides the bridge between SpreadDetector alerts and execution algorithms.
Manages exchange clients, executor selection, and result tracking.
"""

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Dict, Optional

from src.core.exchange import ExchangeClient, TradableMarket
from arb.spread_detector import Platform, SpreadAlert, SpreadOpportunity

from .algorithms import (
    AdaptiveExecutor,
    SequentialMarketExecutor,
    SimultaneousLimitExecutor,
)
from .base import ArbExecutor, ExecutionConfig, ExecutionResult
from .leg_tracker import LegRiskManager
from .metrics import MetricsCollector

if TYPE_CHECKING:
    from src.risk.position_sizer import PositionSizer


logger = logging.getLogger(__name__)


class ArbExecutionHandler:
    """Handler for executing arbitrage opportunities from SpreadDetector.

    Integrates with SpreadDetector via the on_alert callback, manages
    exchange clients, creates appropriate executors, and tracks metrics.

    Usage:
        handler = ArbExecutionHandler(
            exchange_clients={
                Platform.KALSHI: kalshi_client,
                Platform.POLYMARKET: poly_client,
            },
            algorithm="adaptive",
        )
        detector = SpreadDetector(
            market_matcher=matcher,
            on_alert=handler.on_alert,
        )
        detector.start()
    """

    ALGORITHMS = {
        "simultaneous": SimultaneousLimitExecutor,
        "sequential": SequentialMarketExecutor,
        "adaptive": AdaptiveExecutor,
    }

    def __init__(
        self,
        exchange_clients: Dict[Platform, ExchangeClient],
        algorithm: str = "adaptive",
        config: Optional[ExecutionConfig] = None,
        on_execution: Optional[Callable[[ExecutionResult], None]] = None,
        auto_execute: bool = True,
        position_sizer: Optional["PositionSizer"] = None,
    ):
        """Initialize the execution handler.

        Args:
            exchange_clients: Map of Platform to ExchangeClient.
            algorithm: Execution algorithm to use ('simultaneous', 'sequential', 'adaptive').
            config: Execution configuration.
            on_execution: Optional callback for execution results.
            auto_execute: If True, automatically execute on alerts. If False, queue only.
            position_sizer: Optional PositionSizer for calculating optimal trade sizes.
        """
        self.exchanges = exchange_clients
        self.algorithm = algorithm
        self.config = config or ExecutionConfig()
        self.on_execution = on_execution
        self.auto_execute = auto_execute
        self.position_sizer = position_sizer

        self.metrics = MetricsCollector()
        self.leg_risk_manager = LegRiskManager(config=self.config)

        self._active_execution: Optional[ArbExecutor] = None
        self._queued_alerts: list = []
        self._is_executing = False

        self.metrics.start()

    def on_alert(self, alert: SpreadAlert) -> None:
        """Callback for SpreadDetector alerts.

        This is the main entry point when a spread opportunity is detected.

        Args:
            alert: The spread alert from SpreadDetector.
        """
        opportunity = alert.opportunity

        # Check minimum edge threshold
        if opportunity.net_edge_per_contract < self.config.min_edge_to_execute:
            logger.debug(
                f"Skipping alert: edge {opportunity.net_edge_per_contract:.4f} "
                f"< min {self.config.min_edge_to_execute:.4f}"
            )
            return

        # Check if we have the required exchange clients
        if not self._has_required_clients(opportunity):
            logger.warning(
                f"Missing exchange client for platforms: "
                f"{opportunity.buy_platform.value}, {opportunity.sell_platform.value}"
            )
            return

        if self.auto_execute:
            if self._is_executing:
                # Queue for later
                self._queued_alerts.append(alert)
                logger.debug(f"Queued alert {alert.alert_id}, currently executing")
            else:
                self._execute_opportunity(opportunity)
        else:
            self._queued_alerts.append(alert)

    def execute_next_queued(self) -> Optional[ExecutionResult]:
        """Execute the next queued alert.

        Returns:
            ExecutionResult if an alert was executed, None if queue empty.
        """
        if not self._queued_alerts:
            return None

        alert = self._queued_alerts.pop(0)
        return self._execute_opportunity(alert.opportunity)

    def execute_opportunity(
        self, opportunity: SpreadOpportunity
    ) -> Optional[ExecutionResult]:
        """Manually execute a specific opportunity.

        Args:
            opportunity: The opportunity to execute.

        Returns:
            ExecutionResult from the execution.
        """
        if not self._has_required_clients(opportunity):
            logger.error("Missing required exchange clients")
            return None

        return self._execute_opportunity(opportunity)

    def _execute_opportunity(
        self, opportunity: SpreadOpportunity
    ) -> Optional[ExecutionResult]:
        """Execute an opportunity and record results.

        Args:
            opportunity: The opportunity to execute.

        Returns:
            ExecutionResult from the execution, or None if skipped.
        """
        self._is_executing = True
        original_max_contracts = None

        try:
            # Calculate optimal size using position sizer
            if self.position_sizer:
                sizing_result = self.position_sizer.calculate_size(
                    opportunity=opportunity,
                    execution_metrics=self.metrics.aggregate,
                )
                size = sizing_result.recommended_size

                if size == 0:
                    logger.info(
                        f"Skipping opportunity: {sizing_result.limiting_factor} "
                        f"(details: {sizing_result.details})"
                    )
                    return None

                # Temporarily adjust config.max_contracts to enforce sizing
                # The executor uses min(opportunity.max_contracts, config.max_contracts)
                original_max_contracts = self.config.max_contracts
                self.config.max_contracts = min(size, opportunity.max_contracts)

                logger.info(
                    f"Position sized: {self.config.max_contracts} contracts "
                    f"(limiting_factor={sizing_result.limiting_factor})"
                )
            else:
                size = min(opportunity.max_contracts, self.config.max_contracts)
                original_max_contracts = None

            # Get tradable markets
            buy_market = self._get_market(
                opportunity.buy_platform, opportunity.buy_market_id
            )
            sell_market = self._get_market(
                opportunity.sell_platform, opportunity.sell_market_id
            )

            # Create executor
            executor = self._create_executor(buy_market, sell_market)
            self._active_execution = executor

            # Execute
            effective_size = min(opportunity.max_contracts, self.config.max_contracts)
            logger.info(
                f"Executing {self.algorithm} arb ({effective_size} contracts): "
                f"BUY {opportunity.buy_outcome} @ {opportunity.buy_price} on {opportunity.buy_platform.value}, "
                f"SELL {opportunity.sell_outcome} @ {opportunity.sell_price} on {opportunity.sell_platform.value}"
            )

            result = executor.execute(opportunity)

            # Record metrics
            self.metrics.record_execution(result, algorithm=self.algorithm)

            # Handle leg risk if needed
            if result.has_leg_risk:
                self._handle_leg_risk(result, buy_market, sell_market)

            # Invoke callback
            if self.on_execution:
                self.on_execution(result)

            logger.info(
                f"Execution complete: {result.state.value}, "
                f"captured_edge={result.captured_edge:.4f}, "
                f"time={result.execution_time_ms:.1f}ms"
            )

            return result

        finally:
            self._is_executing = False
            self._active_execution = None
            # Restore original max_contracts if we modified it
            if original_max_contracts is not None:
                self.config.max_contracts = original_max_contracts

    def _create_executor(
        self, buy_market: TradableMarket, sell_market: TradableMarket
    ) -> ArbExecutor:
        """Create an executor for the given markets."""
        executor_class = self.ALGORITHMS.get(self.algorithm)

        if executor_class is None:
            raise ValueError(f"Unknown algorithm: {self.algorithm}")

        return executor_class(buy_market, sell_market, self.config)

    def _get_market(self, platform: Platform, market_id: str) -> TradableMarket:
        """Get a TradableMarket from an exchange client."""
        client = self.exchanges[platform]
        return client.get_market(market_id)

    def _has_required_clients(self, opportunity: SpreadOpportunity) -> bool:
        """Check if we have clients for both platforms."""
        return (
            opportunity.buy_platform in self.exchanges
            and opportunity.sell_platform in self.exchanges
        )

    def _handle_leg_risk(
        self,
        result: ExecutionResult,
        buy_market: TradableMarket,
        sell_market: TradableMarket,
    ) -> None:
        """Handle leg risk from an execution result."""
        buy_filled = result.buy_leg.is_filled
        sell_filled = result.sell_leg.is_filled

        if buy_filled and not sell_filled:
            self.leg_risk_manager.handle_leg_risk(
                filled_leg=result.buy_leg,
                unfilled_leg=result.sell_leg,
                unfilled_market=sell_market,
            )
        elif sell_filled and not buy_filled:
            self.leg_risk_manager.handle_leg_risk(
                filled_leg=result.sell_leg,
                unfilled_leg=result.buy_leg,
                unfilled_market=buy_market,
            )

    def cancel_active(self) -> bool:
        """Cancel any active execution.

        Returns:
            True if there was an active execution that was canceled.
        """
        if self._active_execution:
            return self._active_execution.cancel()
        return False

    def clear_queue(self) -> int:
        """Clear the alert queue.

        Returns:
            Number of alerts that were cleared.
        """
        count = len(self._queued_alerts)
        self._queued_alerts.clear()
        return count

    @property
    def queue_size(self) -> int:
        """Number of alerts in the queue."""
        return len(self._queued_alerts)

    @property
    def is_executing(self) -> bool:
        """Whether an execution is currently in progress."""
        return self._is_executing

    def get_metrics_summary(self) -> dict:
        """Get summary of execution metrics."""
        return {
            "execution_metrics": self.metrics.get_summary(),
            "leg_risk_summary": self.leg_risk_manager.get_summary(),
            "queue_size": self.queue_size,
            "algorithm": self.algorithm,
        }

    def set_algorithm(self, algorithm: str) -> None:
        """Change the execution algorithm.

        Args:
            algorithm: New algorithm to use.

        Raises:
            ValueError: If algorithm is not recognized.
        """
        if algorithm not in self.ALGORITHMS:
            raise ValueError(
                f"Unknown algorithm: {algorithm}. "
                f"Available: {list(self.ALGORITHMS.keys())}"
            )
        self.algorithm = algorithm
