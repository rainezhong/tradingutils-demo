"""Base classes and data structures for arbitrage execution.

Provides the core abstractions for executing spread opportunities across
prediction market platforms. Includes execution state tracking, configuration,
and the abstract executor interface.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from src.core.exchange import Order, TradableMarket
from arb.spread_detector import Platform, SpreadOpportunity


class ExecutionState(Enum):
    """States for tracking execution progress."""
    IDLE = "idle"
    PLACING_BUY = "placing_buy"
    PLACING_SELL = "placing_sell"
    AWAITING_BUY_FILL = "awaiting_buy_fill"
    AWAITING_SELL_FILL = "awaiting_sell_fill"
    BOTH_PENDING = "both_pending"
    PARTIAL_FILL = "partial_fill"
    COMPLETED = "completed"
    FAILED = "failed"
    LEG_RISK = "leg_risk"


@dataclass
class ExecutionConfig:
    """Configuration for execution algorithms.

    Attributes:
        max_contracts: Maximum number of contracts per execution.
        limit_order_timeout_ms: Timeout for limit orders before escalation/cancel.
        market_order_slippage_cents: Slippage allowance for market-like orders.
        adaptive_switch_threshold_ms: Time before adaptive executor escalates price.
        min_edge_to_execute: Minimum edge required to proceed with execution.
        cancel_on_leg_risk: Whether to cancel unfilled leg on partial fill.
    """
    max_contracts: int = 100
    limit_order_timeout_ms: int = 5000
    market_order_slippage_cents: int = 2
    adaptive_switch_threshold_ms: int = 3000
    min_edge_to_execute: float = 0.02
    cancel_on_leg_risk: bool = True


@dataclass
class LegExecution:
    """Tracks execution state for one leg of an arbitrage trade.

    Attributes:
        platform: The platform for this leg.
        market_id: Market identifier on the platform.
        side: Order side ('buy' or 'sell').
        target_price: Target execution price.
        size: Number of contracts.
        order: The placed Order, if any.
        actual_price: Actual fill price, if filled.
        filled_size: Number of contracts filled.
        status: Current status of this leg.
    """
    platform: Platform
    market_id: str
    side: str  # 'buy' or 'sell'
    target_price: float
    size: int
    order: Optional[Order] = None
    actual_price: Optional[float] = None
    filled_size: int = 0
    status: str = "pending"  # pending, placed, filled, partial, canceled, failed

    @property
    def is_filled(self) -> bool:
        """Check if leg is completely filled."""
        return self.status == "filled" or (
            self.order is not None and self.order.is_filled
        )

    @property
    def is_active(self) -> bool:
        """Check if leg order is still active."""
        return self.order is not None and self.order.is_active


@dataclass
class ExecutionResult:
    """Result of an arbitrage execution attempt.

    Attributes:
        opportunity: The spread opportunity that was executed.
        state: Final execution state.
        buy_leg: Buy leg execution details.
        sell_leg: Sell leg execution details.
        theoretical_edge: Expected edge per contract from opportunity.
        captured_edge: Actual edge captured per contract.
        execution_time_ms: Total execution time in milliseconds.
        error_message: Error message if execution failed.
    """
    opportunity: SpreadOpportunity
    state: ExecutionState
    buy_leg: LegExecution
    sell_leg: LegExecution
    theoretical_edge: float
    captured_edge: float
    execution_time_ms: float
    error_message: Optional[str] = None

    @property
    def is_successful(self) -> bool:
        """Check if execution completed successfully."""
        return self.state == ExecutionState.COMPLETED

    @property
    def has_leg_risk(self) -> bool:
        """Check if execution resulted in leg risk (partial fill)."""
        return self.state == ExecutionState.LEG_RISK

    @property
    def total_contracts_filled(self) -> int:
        """Total contracts filled across both legs."""
        return min(self.buy_leg.filled_size, self.sell_leg.filled_size)

    @property
    def edge_capture_rate(self) -> float:
        """Ratio of captured edge to theoretical edge."""
        if self.theoretical_edge <= 0:
            return 0.0
        return self.captured_edge / self.theoretical_edge


class ArbExecutor(ABC):
    """Abstract base class for arbitrage execution algorithms.

    Subclasses implement different execution strategies (simultaneous limits,
    sequential market, adaptive, etc.) while sharing common infrastructure.
    """

    def __init__(
        self,
        buy_market: TradableMarket,
        sell_market: TradableMarket,
        config: Optional[ExecutionConfig] = None,
    ):
        """Initialize the executor.

        Args:
            buy_market: TradableMarket for the buy leg.
            sell_market: TradableMarket for the sell leg.
            config: Execution configuration. Uses defaults if not provided.
        """
        self.buy_market = buy_market
        self.sell_market = sell_market
        self.config = config or ExecutionConfig()
        self._state = ExecutionState.IDLE
        self._start_time: Optional[datetime] = None

    @property
    def state(self) -> ExecutionState:
        """Current execution state."""
        return self._state

    @abstractmethod
    def execute(self, opportunity: SpreadOpportunity) -> ExecutionResult:
        """Execute an arbitrage opportunity.

        Args:
            opportunity: The spread opportunity to execute.

        Returns:
            ExecutionResult with execution details and outcome.
        """
        pass

    @abstractmethod
    def cancel(self) -> bool:
        """Cancel any active orders from this executor.

        Returns:
            True if all orders were successfully canceled.
        """
        pass

    def _elapsed_ms(self) -> float:
        """Get elapsed time since execution started in milliseconds."""
        if self._start_time is None:
            return 0.0
        return (datetime.now() - self._start_time).total_seconds() * 1000

    def _create_buy_leg(
        self, opportunity: SpreadOpportunity, size: int
    ) -> LegExecution:
        """Create a buy leg execution from opportunity."""
        return LegExecution(
            platform=opportunity.buy_platform,
            market_id=opportunity.buy_market_id,
            side="buy",
            target_price=opportunity.buy_price,
            size=size,
        )

    def _create_sell_leg(
        self, opportunity: SpreadOpportunity, size: int
    ) -> LegExecution:
        """Create a sell leg execution from opportunity."""
        return LegExecution(
            platform=opportunity.sell_platform,
            market_id=opportunity.sell_market_id,
            side="sell",
            target_price=opportunity.sell_price,
            size=size,
        )

    def _calculate_captured_edge(
        self, buy_leg: LegExecution, sell_leg: LegExecution
    ) -> float:
        """Calculate actual captured edge per contract.

        For a complete arb: Buy YES at buy_price, sell YES at sell_price.
        Edge = sell_price - buy_price (both in cents).
        """
        if not buy_leg.is_filled or not sell_leg.is_filled:
            return 0.0

        buy_price = buy_leg.actual_price or buy_leg.target_price
        sell_price = sell_leg.actual_price or sell_leg.target_price

        # Edge in cents per contract
        return sell_price - buy_price
