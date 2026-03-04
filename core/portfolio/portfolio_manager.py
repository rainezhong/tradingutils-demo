"""
Portfolio manager for multi-strategy execution.

Orchestrates performance tracking, correlation estimation, and allocation
optimization. Updates strategy bankrolls transparently.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import logging

from core.portfolio.types import (
    StrategyStats,
    AllocationResult,
    PortfolioConfig,
)
from core.portfolio.performance_tracker import PerformanceTracker
from core.portfolio.correlation_estimator import CorrelationEstimator
from core.portfolio.allocation_optimizer import AllocationOptimizer


logger = logging.getLogger(__name__)


class PortfolioManager:
    """Manage portfolio allocation across multiple strategies."""

    def __init__(
        self,
        config: PortfolioConfig,
        total_bankroll: float,
    ):
        """Initialize portfolio manager.

        Args:
            config: Portfolio configuration
            total_bankroll: Total bankroll across all strategies
        """
        self.config = config
        self.total_bankroll = total_bankroll

        # Components
        self.performance_tracker = PerformanceTracker(config.trade_db_path)
        self.correlation_estimator = CorrelationEstimator(config)
        self.allocation_optimizer = AllocationOptimizer(config.allocation)

        # State
        self.strategies: Dict[str, any] = {}  # strategy_name -> strategy instance
        self.current_allocations: Dict[str, float] = {}
        self.last_rebalance: Optional[datetime] = None
        self.last_bankroll: float = total_bankroll

        self._running = False
        self._rebalance_task: Optional[asyncio.Task] = None

    def register_strategy(self, strategy_name: str, strategy: any):
        """Register a strategy for portfolio management.

        Args:
            strategy_name: Name of strategy
            strategy: Strategy instance (must have config.bankroll attribute)
        """
        self.strategies[strategy_name] = strategy
        logger.info(f"Registered strategy: {strategy_name}")

    async def start(self):
        """Start portfolio manager (begins rebalancing loop)."""
        if self._running:
            logger.warning("Portfolio manager already running")
            return

        self._running = True

        # Initial allocation
        await self.rebalance(reason="initial")

        # Start rebalancing loop
        self._rebalance_task = asyncio.create_task(self._rebalance_loop())

        logger.info("Portfolio manager started")

    async def stop(self):
        """Stop portfolio manager."""
        if not self._running:
            return

        self._running = False

        if self._rebalance_task:
            self._rebalance_task.cancel()
            try:
                await self._rebalance_task
            except asyncio.CancelledError:
                pass

        logger.info("Portfolio manager stopped")

    async def _rebalance_loop(self):
        """Periodic rebalancing loop."""
        while self._running:
            try:
                # Wait for next rebalance interval
                await asyncio.sleep(self.config.rebalance_interval_sec)

                # Check if rebalance needed
                if self._should_rebalance():
                    await self.rebalance(reason="scheduled")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in rebalance loop: {e}", exc_info=True)
                await asyncio.sleep(60)  # Back off on error

    def _should_rebalance(self) -> bool:
        """Check if rebalancing is needed (performance-triggered).

        Returns:
            True if rebalance needed
        """
        if not self.last_rebalance:
            return True

        # Rate limit: respect minimum interval
        time_since_last = (datetime.now() - self.last_rebalance).total_seconds()
        if time_since_last < self.config.rebalance_min_interval_sec:
            return False

        # Check bankroll change
        current_bankroll = self._calculate_current_bankroll()
        pct_change = abs(current_bankroll - self.last_bankroll) / self.last_bankroll

        if pct_change >= self.config.rebalance_on_pnl_change_pct:
            logger.info(
                f"Bankroll changed {pct_change:.1%}, triggering rebalance"
            )
            return True

        return False

    def _calculate_current_bankroll(self) -> float:
        """Calculate current total bankroll from strategy configs."""
        return sum(
            strategy._config.bankroll
            for strategy in self.strategies.values()
        )

    async def rebalance(self, reason: str = "manual"):
        """Rebalance portfolio allocations.

        Args:
            reason: Reason for rebalancing
        """
        logger.info(f"Rebalancing portfolio (reason: {reason})")

        # Get strategy names
        strategy_names = sorted(self.strategies.keys())

        if not strategy_names:
            logger.warning("No strategies registered")
            return

        # Single strategy mode: allocate 100%
        if len(strategy_names) == 1:
            strategy_name = strategy_names[0]
            self.current_allocations = {strategy_name: 1.0}
            self._update_strategy_bankrolls({strategy_name: 1.0})
            logger.info(f"Single strategy mode: {strategy_name} = 100%")
            return

        # Get performance stats
        strategy_stats = {}
        for strategy_name in strategy_names:
            stats = self.performance_tracker.get_strategy_stats(
                strategy_name,
                lookback_days=self.config.lookback_days,
            )

            if not stats:
                logger.warning(f"No stats for {strategy_name}, skipping")
                continue

            # Check minimum trades
            total_trades = self.performance_tracker.get_total_trades(
                strategy_name
            )
            if total_trades < self.config.allocation.min_trades_per_strategy:
                logger.warning(
                    f"{strategy_name} has only {total_trades} trades "
                    f"(need {self.config.allocation.min_trades_per_strategy}), "
                    f"skipping"
                )
                continue

            strategy_stats[strategy_name] = stats

        if not strategy_stats:
            logger.warning("No strategies with sufficient stats")
            return

        # Filter to strategies with stats
        active_strategy_names = sorted(strategy_stats.keys())

        # Get trades for correlation analysis
        strategy_trades = self.performance_tracker.get_trades_for_correlation(
            active_strategy_names,
            lookback_days=self.config.lookback_days,
        )

        # Fit copula (Gaussian or student-t)
        copula = self.correlation_estimator.fit_copula(
            strategy_trades,
            copula_type=self.config.copula_type,
            df=self.config.copula_df,
        )

        # Get trade PnLs for empirical Kelly (if enabled)
        trade_pnls = None
        if self.config.allocation.use_empirical_kelly:
            trade_pnls = {}
            for strategy_name in active_strategy_names:
                pnls = self.performance_tracker.get_trade_pnls(
                    strategy_name,
                    lookback_days=self.config.lookback_days,
                )
                trade_pnls[strategy_name] = pnls

        # Calculate optimal allocations (using copula)
        allocation_result = self.allocation_optimizer.calculate_allocations(
            strategy_stats,
            strategy_names=active_strategy_names,
            rebalance_reason=reason,
            trade_pnls=trade_pnls,
            copula=copula,
        )

        # Update allocations
        self.current_allocations = allocation_result.allocations
        self.last_rebalance = datetime.now()
        self.last_bankroll = self.total_bankroll

        # Update strategy bankrolls
        self._update_strategy_bankrolls(allocation_result.allocations)

        # Log results
        self._log_allocation_result(allocation_result, strategy_stats)

    def _update_strategy_bankrolls(self, allocations: Dict[str, float]):
        """Update strategy bankrolls based on allocations.

        Args:
            allocations: Dict mapping strategy name to fraction
        """
        for strategy_name, fraction in allocations.items():
            if strategy_name not in self.strategies:
                continue

            strategy = self.strategies[strategy_name]
            new_bankroll = self.total_bankroll * fraction

            # Update strategy config
            strategy._config.bankroll = new_bankroll

            logger.info(
                f"Updated {strategy_name} bankroll: ${new_bankroll:,.2f} "
                f"({fraction:.1%})"
            )

        # Zero out strategies not in allocation
        for strategy_name, strategy in self.strategies.items():
            if strategy_name not in allocations:
                strategy._config.bankroll = 0.0
                logger.info(f"Zeroed {strategy_name} bankroll (no allocation)")

    def _log_allocation_result(
        self,
        result: AllocationResult,
        strategy_stats: Dict[str, StrategyStats],
    ):
        """Log allocation result.

        Args:
            result: Allocation result
            strategy_stats: Strategy performance stats
        """
        logger.info("=" * 60)
        logger.info("PORTFOLIO ALLOCATION")
        logger.info("=" * 60)

        logger.info(f"Total Allocated: {result.total_allocated:.1%}")
        logger.info(f"Expected Growth Rate: {result.expected_growth_rate:.4f}")
        logger.info(f"Portfolio Variance: {result.portfolio_variance:.6f}")
        logger.info(f"Portfolio Sharpe: {result.portfolio_sharpe:.2f}")
        logger.info(f"Rebalance Reason: {result.rebalance_reason}")

        logger.info("\nStrategy Allocations:")
        logger.info("-" * 60)

        for strategy_name, fraction in sorted(
            result.allocations.items(),
            key=lambda x: x[1],
            reverse=True
        ):
            stats = strategy_stats[strategy_name]
            bankroll = self.total_bankroll * fraction

            logger.info(
                f"{strategy_name:30s} {fraction:6.1%}  ${bankroll:10,.2f}  "
                f"edge={stats.edge:7.2f}  sharpe={stats.sharpe_ratio:5.2f}"
            )

        logger.info("=" * 60)

    def get_status(self) -> Dict:
        """Get current portfolio status.

        Returns:
            Dict with status information
        """
        return {
            "total_bankroll": self.total_bankroll,
            "current_allocations": self.current_allocations,
            "last_rebalance": self.last_rebalance,
            "num_strategies": len(self.strategies),
            "running": self._running,
        }

    def get_aggregate_pnl(self) -> float:
        """Get aggregate P&L across all strategies.

        Returns:
            Total P&L
        """
        total_pnl = 0.0

        for strategy_name in self.strategies.keys():
            stats = self.performance_tracker.get_strategy_stats(
                strategy_name,
                lookback_days=999999,  # All time
            )
            if stats:
                total_pnl += stats.total_pnl

        return total_pnl
