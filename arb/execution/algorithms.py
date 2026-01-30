"""Arbitrage execution algorithms.

Implements three execution strategies for spread opportunities:
- SimultaneousLimitExecutor: Post limit orders on both sides at once
- SequentialMarketExecutor: Execute fast side first, then slow side
- AdaptiveExecutor: Start with limits, escalate to aggressive prices
"""

import time
from datetime import datetime
from typing import List, Optional, Tuple

from src.core.exchange import Order, OrderBook, TradableMarket
from arb.spread_detector import SpreadOpportunity

from .base import (
    ArbExecutor,
    ExecutionConfig,
    ExecutionResult,
    ExecutionState,
    LegExecution,
)


class SimultaneousLimitExecutor(ArbExecutor):
    """Execute arbitrage by posting limit orders on both platforms simultaneously.

    Places buy limit at opportunity's buy_price and sell limit at sell_price.
    Waits for both to fill within timeout. Handles leg risk if only one fills.

    State Flow: IDLE -> BOTH_PENDING -> (poll) -> COMPLETED | LEG_RISK | FAILED
    """

    def execute(self, opportunity: SpreadOpportunity) -> ExecutionResult:
        """Execute the arbitrage opportunity with simultaneous limit orders.

        Args:
            opportunity: The spread opportunity to execute.

        Returns:
            ExecutionResult with execution details.
        """
        self._start_time = datetime.now()
        self._state = ExecutionState.IDLE

        # Determine execution size
        size = min(opportunity.max_contracts, self.config.max_contracts)

        # Create leg tracking
        buy_leg = self._create_buy_leg(opportunity, size)
        sell_leg = self._create_sell_leg(opportunity, size)

        try:
            # Place both orders simultaneously
            self._state = ExecutionState.BOTH_PENDING

            buy_order = self.buy_market.buy(buy_leg.target_price, size)
            buy_leg.order = buy_order
            buy_leg.status = "placed"

            sell_order = self.sell_market.sell(sell_leg.target_price, size)
            sell_leg.order = sell_order
            sell_leg.status = "placed"

            # Poll for fills with timeout
            timeout_ms = self.config.limit_order_timeout_ms
            poll_interval_ms = 100

            while self._elapsed_ms() < timeout_ms:
                # Refresh order status
                buy_leg = self._update_leg_status(buy_leg, self.buy_market)
                sell_leg = self._update_leg_status(sell_leg, self.sell_market)

                # Check if both filled
                if buy_leg.is_filled and sell_leg.is_filled:
                    self._state = ExecutionState.COMPLETED
                    break

                time.sleep(poll_interval_ms / 1000)

            # Handle timeout / partial fills
            if self._state != ExecutionState.COMPLETED:
                buy_filled = buy_leg.is_filled
                sell_filled = sell_leg.is_filled

                if buy_filled and not sell_filled:
                    # Leg risk: buy filled, sell didn't
                    self._state = ExecutionState.LEG_RISK
                    if self.config.cancel_on_leg_risk and sell_leg.order:
                        self.sell_market.cancel_order(sell_leg.order.order_id)
                        sell_leg.status = "canceled"
                elif sell_filled and not buy_filled:
                    # Leg risk: sell filled, buy didn't
                    self._state = ExecutionState.LEG_RISK
                    if self.config.cancel_on_leg_risk and buy_leg.order:
                        self.buy_market.cancel_order(buy_leg.order.order_id)
                        buy_leg.status = "canceled"
                else:
                    # Neither filled - cancel both
                    self._state = ExecutionState.FAILED
                    if buy_leg.order:
                        self.buy_market.cancel_order(buy_leg.order.order_id)
                        buy_leg.status = "canceled"
                    if sell_leg.order:
                        self.sell_market.cancel_order(sell_leg.order.order_id)
                        sell_leg.status = "canceled"

            captured_edge = self._calculate_captured_edge(buy_leg, sell_leg)

            return ExecutionResult(
                opportunity=opportunity,
                state=self._state,
                buy_leg=buy_leg,
                sell_leg=sell_leg,
                theoretical_edge=opportunity.net_edge_per_contract,
                captured_edge=captured_edge,
                execution_time_ms=self._elapsed_ms(),
            )

        except Exception as e:
            self._state = ExecutionState.FAILED
            # Attempt to cancel any placed orders
            self._cancel_leg_order(buy_leg, self.buy_market)
            self._cancel_leg_order(sell_leg, self.sell_market)

            return ExecutionResult(
                opportunity=opportunity,
                state=self._state,
                buy_leg=buy_leg,
                sell_leg=sell_leg,
                theoretical_edge=opportunity.net_edge_per_contract,
                captured_edge=0.0,
                execution_time_ms=self._elapsed_ms(),
                error_message=str(e),
            )

    def cancel(self) -> bool:
        """Cancel all active orders."""
        success = True
        # Cancel would be called externally with stored leg references
        # This is a simplified implementation
        return success

    def _update_leg_status(
        self, leg: LegExecution, market: TradableMarket
    ) -> LegExecution:
        """Update leg execution status from market."""
        if leg.order is None:
            return leg

        # Get fresh order status
        orders = market.get_orders(status=None)
        for order in orders:
            if order.order_id == leg.order.order_id:
                leg.order = order
                leg.filled_size = order.filled_size
                if order.is_filled:
                    leg.status = "filled"
                    leg.actual_price = order.price
                elif order.filled_size > 0:
                    leg.status = "partial"
                break

        return leg

    def _cancel_leg_order(
        self, leg: LegExecution, market: TradableMarket
    ) -> None:
        """Attempt to cancel a leg's order."""
        if leg.order and leg.order.is_active:
            try:
                market.cancel_order(leg.order.order_id)
                leg.status = "canceled"
            except Exception:
                pass


class SequentialMarketExecutor(ArbExecutor):
    """Execute arbitrage by executing fast side first, then slow side.

    Uses aggressive limit orders (simulating market orders) for speed.
    Executes the "fast" side first, then calculates remaining edge and
    executes the slow side if profitable.

    State Flow: IDLE -> PLACING_BUY -> AWAITING_BUY_FILL -> PLACING_SELL
                     -> AWAITING_SELL_FILL -> COMPLETED
    """

    def __init__(
        self,
        buy_market: TradableMarket,
        sell_market: TradableMarket,
        config: Optional[ExecutionConfig] = None,
        fast_side: str = "buy",
    ):
        """Initialize the sequential executor.

        Args:
            buy_market: TradableMarket for the buy leg.
            sell_market: TradableMarket for the sell leg.
            config: Execution configuration.
            fast_side: Which leg to execute first ('buy' or 'sell').
        """
        super().__init__(buy_market, sell_market, config)
        self.fast_side = fast_side
        self._buy_leg: Optional[LegExecution] = None
        self._sell_leg: Optional[LegExecution] = None

    def execute(self, opportunity: SpreadOpportunity) -> ExecutionResult:
        """Execute the arbitrage opportunity sequentially.

        Args:
            opportunity: The spread opportunity to execute.

        Returns:
            ExecutionResult with execution details.
        """
        self._start_time = datetime.now()
        self._state = ExecutionState.IDLE

        size = min(opportunity.max_contracts, self.config.max_contracts)

        buy_leg = self._create_buy_leg(opportunity, size)
        sell_leg = self._create_sell_leg(opportunity, size)
        self._buy_leg = buy_leg
        self._sell_leg = sell_leg

        try:
            if self.fast_side == "buy":
                # Execute buy first
                buy_leg = self._execute_fast_leg(
                    buy_leg, self.buy_market, "buy"
                )
                if not buy_leg.is_filled:
                    self._state = ExecutionState.FAILED
                    return self._build_result(opportunity, buy_leg, sell_leg)

                # Check remaining edge
                remaining_edge = self._calculate_remaining_edge(
                    buy_leg, opportunity.sell_price
                )
                if remaining_edge < self.config.min_edge_to_execute:
                    self._state = ExecutionState.LEG_RISK
                    return self._build_result(opportunity, buy_leg, sell_leg)

                # Execute sell
                sell_leg = self._execute_slow_leg(
                    sell_leg, self.sell_market, "sell"
                )
            else:
                # Execute sell first
                sell_leg = self._execute_fast_leg(
                    sell_leg, self.sell_market, "sell"
                )
                if not sell_leg.is_filled:
                    self._state = ExecutionState.FAILED
                    return self._build_result(opportunity, buy_leg, sell_leg)

                # Check remaining edge
                remaining_edge = self._calculate_remaining_edge_sell_first(
                    sell_leg, opportunity.buy_price
                )
                if remaining_edge < self.config.min_edge_to_execute:
                    self._state = ExecutionState.LEG_RISK
                    return self._build_result(opportunity, buy_leg, sell_leg)

                # Execute buy
                buy_leg = self._execute_slow_leg(
                    buy_leg, self.buy_market, "buy"
                )

            # Determine final state
            if buy_leg.is_filled and sell_leg.is_filled:
                self._state = ExecutionState.COMPLETED
            elif buy_leg.is_filled or sell_leg.is_filled:
                self._state = ExecutionState.LEG_RISK
            else:
                self._state = ExecutionState.FAILED

            return self._build_result(opportunity, buy_leg, sell_leg)

        except Exception as e:
            self._state = ExecutionState.FAILED
            self._cancel_leg_order(buy_leg, self.buy_market)
            self._cancel_leg_order(sell_leg, self.sell_market)

            return ExecutionResult(
                opportunity=opportunity,
                state=self._state,
                buy_leg=buy_leg,
                sell_leg=sell_leg,
                theoretical_edge=opportunity.net_edge_per_contract,
                captured_edge=0.0,
                execution_time_ms=self._elapsed_ms(),
                error_message=str(e),
            )

    def cancel(self) -> bool:
        """Cancel all active orders."""
        success = True
        if self._buy_leg:
            self._cancel_leg_order(self._buy_leg, self.buy_market)
        if self._sell_leg:
            self._cancel_leg_order(self._sell_leg, self.sell_market)
        return success

    def _execute_fast_leg(
        self, leg: LegExecution, market: TradableMarket, side: str
    ) -> LegExecution:
        """Execute a leg with aggressive (market-like) pricing."""
        if side == "buy":
            self._state = ExecutionState.PLACING_BUY
        else:
            self._state = ExecutionState.PLACING_SELL

        order = self._place_market_order(market, side, leg.size)
        leg.order = order
        leg.status = "placed"

        # Wait for fill
        if side == "buy":
            self._state = ExecutionState.AWAITING_BUY_FILL
        else:
            self._state = ExecutionState.AWAITING_SELL_FILL

        leg = self._wait_for_fill(leg, market, timeout_ms=2000)
        return leg

    def _execute_slow_leg(
        self, leg: LegExecution, market: TradableMarket, side: str
    ) -> LegExecution:
        """Execute a leg with limit near market."""
        if side == "buy":
            self._state = ExecutionState.PLACING_BUY
        else:
            self._state = ExecutionState.PLACING_SELL

        # Place slightly aggressive limit
        ob = market.get_orderbook()
        if side == "buy":
            price = min(99, (ob.best_ask or leg.target_price) + 1)
            order = market.buy(price, leg.size)
        else:
            price = max(1, (ob.best_bid or leg.target_price) - 1)
            order = market.sell(price, leg.size)

        leg.order = order
        leg.status = "placed"

        if side == "buy":
            self._state = ExecutionState.AWAITING_BUY_FILL
        else:
            self._state = ExecutionState.AWAITING_SELL_FILL

        leg = self._wait_for_fill(leg, market, timeout_ms=3000)
        return leg

    def _place_market_order(
        self, market: TradableMarket, side: str, size: int
    ) -> Order:
        """Place a market-like order with slippage."""
        ob = market.get_orderbook()
        slippage = self.config.market_order_slippage_cents

        if side == "buy":
            price = min(99, (ob.best_ask or 50) + slippage)
            return market.buy(price, size)
        else:
            price = max(1, (ob.best_bid or 50) - slippage)
            return market.sell(price, size)

    def _wait_for_fill(
        self, leg: LegExecution, market: TradableMarket, timeout_ms: int
    ) -> LegExecution:
        """Wait for a leg to fill with timeout."""
        start = datetime.now()
        poll_interval_ms = 50

        while (datetime.now() - start).total_seconds() * 1000 < timeout_ms:
            leg = self._update_leg_status(leg, market)
            if leg.is_filled:
                return leg
            time.sleep(poll_interval_ms / 1000)

        return leg

    def _update_leg_status(
        self, leg: LegExecution, market: TradableMarket
    ) -> LegExecution:
        """Update leg execution status from market."""
        if leg.order is None:
            return leg

        orders = market.get_orders(status=None)
        for order in orders:
            if order.order_id == leg.order.order_id:
                leg.order = order
                leg.filled_size = order.filled_size
                if order.is_filled:
                    leg.status = "filled"
                    leg.actual_price = order.price
                elif order.filled_size > 0:
                    leg.status = "partial"
                break

        return leg

    def _cancel_leg_order(
        self, leg: LegExecution, market: TradableMarket
    ) -> None:
        """Attempt to cancel a leg's order."""
        if leg.order and leg.order.is_active:
            try:
                market.cancel_order(leg.order.order_id)
                leg.status = "canceled"
            except Exception:
                pass

    def _calculate_remaining_edge(
        self, buy_leg: LegExecution, target_sell_price: float
    ) -> float:
        """Calculate remaining edge after buy fill."""
        buy_price = buy_leg.actual_price or buy_leg.target_price
        return target_sell_price - buy_price

    def _calculate_remaining_edge_sell_first(
        self, sell_leg: LegExecution, target_buy_price: float
    ) -> float:
        """Calculate remaining edge after sell fill."""
        sell_price = sell_leg.actual_price or sell_leg.target_price
        return sell_price - target_buy_price

    def _build_result(
        self,
        opportunity: SpreadOpportunity,
        buy_leg: LegExecution,
        sell_leg: LegExecution,
    ) -> ExecutionResult:
        """Build execution result."""
        captured_edge = self._calculate_captured_edge(buy_leg, sell_leg)
        return ExecutionResult(
            opportunity=opportunity,
            state=self._state,
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            theoretical_edge=opportunity.net_edge_per_contract,
            captured_edge=captured_edge,
            execution_time_ms=self._elapsed_ms(),
        )


class AdaptiveExecutor(ArbExecutor):
    """Execute arbitrage with escalating price aggression.

    Starts with limit orders at target prices. If not filled within threshold,
    cancels and replaces with more aggressive prices. Continues escalating
    until filled or max escalation reached.

    State Flow: IDLE -> BOTH_PENDING -> (escalate) -> BOTH_PENDING -> ...
                     -> COMPLETED | LEG_RISK
    """

    # Default escalation steps: (time_ms, slippage_cents)
    DEFAULT_ESCALATION = [(0, 0), (2000, 1), (3500, 2), (5000, 5)]

    def __init__(
        self,
        buy_market: TradableMarket,
        sell_market: TradableMarket,
        config: Optional[ExecutionConfig] = None,
        escalation_steps: Optional[List[Tuple[int, int]]] = None,
    ):
        """Initialize the adaptive executor.

        Args:
            buy_market: TradableMarket for the buy leg.
            sell_market: TradableMarket for the sell leg.
            config: Execution configuration.
            escalation_steps: List of (time_ms, slippage_cents) tuples.
        """
        super().__init__(buy_market, sell_market, config)
        self.escalation_steps = escalation_steps or self.DEFAULT_ESCALATION
        self._buy_leg: Optional[LegExecution] = None
        self._sell_leg: Optional[LegExecution] = None

    def execute(self, opportunity: SpreadOpportunity) -> ExecutionResult:
        """Execute the arbitrage opportunity with adaptive pricing.

        Args:
            opportunity: The spread opportunity to execute.

        Returns:
            ExecutionResult with execution details.
        """
        self._start_time = datetime.now()
        self._state = ExecutionState.IDLE

        size = min(opportunity.max_contracts, self.config.max_contracts)

        buy_leg = self._create_buy_leg(opportunity, size)
        sell_leg = self._create_sell_leg(opportunity, size)
        self._buy_leg = buy_leg
        self._sell_leg = sell_leg

        current_escalation_idx = 0

        try:
            while current_escalation_idx < len(self.escalation_steps):
                _, slippage = self.escalation_steps[current_escalation_idx]

                # Place or replace orders at current escalation level
                buy_leg, sell_leg = self._place_orders_at_escalation(
                    buy_leg, sell_leg, opportunity, slippage
                )
                self._state = ExecutionState.BOTH_PENDING

                # Determine timeout for this escalation level
                if current_escalation_idx + 1 < len(self.escalation_steps):
                    next_time, _ = self.escalation_steps[current_escalation_idx + 1]
                    timeout_ms = next_time - self._elapsed_ms()
                else:
                    timeout_ms = self.config.limit_order_timeout_ms - self._elapsed_ms()

                if timeout_ms <= 0:
                    current_escalation_idx += 1
                    continue

                # Poll for fills
                poll_interval_ms = 100
                poll_start = datetime.now()

                while (datetime.now() - poll_start).total_seconds() * 1000 < timeout_ms:
                    buy_leg = self._update_leg_status(buy_leg, self.buy_market)
                    sell_leg = self._update_leg_status(sell_leg, self.sell_market)

                    if buy_leg.is_filled and sell_leg.is_filled:
                        self._state = ExecutionState.COMPLETED
                        return self._build_result(opportunity, buy_leg, sell_leg)

                    time.sleep(poll_interval_ms / 1000)

                # Check for partial fills before escalating
                if buy_leg.is_filled or sell_leg.is_filled:
                    break

                current_escalation_idx += 1

            # Final state determination
            if buy_leg.is_filled and sell_leg.is_filled:
                self._state = ExecutionState.COMPLETED
            elif buy_leg.is_filled or sell_leg.is_filled:
                self._state = ExecutionState.LEG_RISK
                # Cancel the unfilled leg
                if not buy_leg.is_filled and buy_leg.order:
                    self._cancel_leg_order(buy_leg, self.buy_market)
                if not sell_leg.is_filled and sell_leg.order:
                    self._cancel_leg_order(sell_leg, self.sell_market)
            else:
                self._state = ExecutionState.FAILED
                self._cancel_leg_order(buy_leg, self.buy_market)
                self._cancel_leg_order(sell_leg, self.sell_market)

            return self._build_result(opportunity, buy_leg, sell_leg)

        except Exception as e:
            self._state = ExecutionState.FAILED
            self._cancel_leg_order(buy_leg, self.buy_market)
            self._cancel_leg_order(sell_leg, self.sell_market)

            return ExecutionResult(
                opportunity=opportunity,
                state=self._state,
                buy_leg=buy_leg,
                sell_leg=sell_leg,
                theoretical_edge=opportunity.net_edge_per_contract,
                captured_edge=0.0,
                execution_time_ms=self._elapsed_ms(),
                error_message=str(e),
            )

    def cancel(self) -> bool:
        """Cancel all active orders."""
        success = True
        if self._buy_leg:
            self._cancel_leg_order(self._buy_leg, self.buy_market)
        if self._sell_leg:
            self._cancel_leg_order(self._sell_leg, self.sell_market)
        return success

    def _place_orders_at_escalation(
        self,
        buy_leg: LegExecution,
        sell_leg: LegExecution,
        opportunity: SpreadOpportunity,
        slippage: int,
    ) -> Tuple[LegExecution, LegExecution]:
        """Place or replace orders at the current escalation level."""
        # Cancel existing orders if any
        if buy_leg.order and buy_leg.order.is_active:
            self.buy_market.cancel_order(buy_leg.order.order_id)
        if sell_leg.order and sell_leg.order.is_active:
            self.sell_market.cancel_order(sell_leg.order.order_id)

        # Calculate prices with slippage
        buy_price = min(99, opportunity.buy_price + slippage)
        sell_price = max(1, opportunity.sell_price - slippage)

        # Place new orders (only if not already filled)
        if not buy_leg.is_filled:
            buy_order = self.buy_market.buy(buy_price, buy_leg.size)
            buy_leg.order = buy_order
            buy_leg.status = "placed"

        if not sell_leg.is_filled:
            sell_order = self.sell_market.sell(sell_price, sell_leg.size)
            sell_leg.order = sell_order
            sell_leg.status = "placed"

        return buy_leg, sell_leg

    def _update_leg_status(
        self, leg: LegExecution, market: TradableMarket
    ) -> LegExecution:
        """Update leg execution status from market."""
        if leg.order is None:
            return leg

        orders = market.get_orders(status=None)
        for order in orders:
            if order.order_id == leg.order.order_id:
                leg.order = order
                leg.filled_size = order.filled_size
                if order.is_filled:
                    leg.status = "filled"
                    leg.actual_price = order.price
                elif order.filled_size > 0:
                    leg.status = "partial"
                break

        return leg

    def _cancel_leg_order(
        self, leg: LegExecution, market: TradableMarket
    ) -> None:
        """Attempt to cancel a leg's order."""
        if leg.order and leg.order.is_active:
            try:
                market.cancel_order(leg.order.order_id)
                leg.status = "canceled"
            except Exception:
                pass

    def _build_result(
        self,
        opportunity: SpreadOpportunity,
        buy_leg: LegExecution,
        sell_leg: LegExecution,
    ) -> ExecutionResult:
        """Build execution result."""
        captured_edge = self._calculate_captured_edge(buy_leg, sell_leg)
        return ExecutionResult(
            opportunity=opportunity,
            state=self._state,
            buy_leg=buy_leg,
            sell_leg=sell_leg,
            theoretical_edge=opportunity.net_edge_per_contract,
            captured_edge=captured_edge,
            execution_time_ms=self._elapsed_ms(),
        )
