"""Spread Recovery Service for crash recovery.

Handles recovery of incomplete spread executions after system restart:
- Detects incomplete spreads from database
- Determines appropriate recovery action
- Executes recovery (complete leg 2, rollback, or escalate)
- Logs all recovery actions for audit

Recovery Strategy:
1. LEG1_SUBMITTED: Check order status on exchange, update accordingly
2. LEG1_FILLED (no leg2): Attempt to complete leg 2 or rollback leg 1
3. LEG2_SUBMITTED: Check order status on exchange, update accordingly
4. ROLLBACK_PENDING: Check rollback status, update accordingly
5. PARTIAL: Evaluate whether to complete or close positions
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol

from src.database.models import SpreadExecutionModel, SpreadExecutionStatus
from src.database.repository import SpreadExecutionRepository


logger = logging.getLogger(__name__)


class RecoveryAction(str, Enum):
    """Possible recovery actions."""

    COMPLETE_LEG2 = "complete_leg2"  # Try to execute leg 2
    ROLLBACK_LEG1 = "rollback_leg1"  # Unwind leg 1 position
    CHECK_ORDER = "check_order"  # Check pending order status
    MARK_FAILED = "mark_failed"  # Mark as failed, needs manual review
    ESCALATE = "escalate"  # Needs manual intervention
    NO_ACTION = "no_action"  # Already complete or nothing to do


@dataclass
class RecoveryResult:
    """Result of a recovery attempt.

    Attributes:
        spread_id: The spread that was recovered
        action: The action that was taken
        success: Whether recovery succeeded
        new_status: New status after recovery
        message: Description of what happened
        details: Additional details
    """

    spread_id: str
    action: RecoveryAction
    success: bool
    new_status: SpreadExecutionStatus
    message: str
    details: Optional[Dict[str, Any]] = None


class ExchangeClient(Protocol):
    """Protocol for exchange clients used in recovery."""

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order status from exchange."""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        ...

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> Optional[Dict[str, Any]]:
        """Place a new order."""
        ...


class SpreadRecoveryService:
    """Service for recovering incomplete spread executions.

    On system startup, this service:
    1. Queries for incomplete spread executions
    2. Evaluates each spread's state
    3. Determines and executes appropriate recovery action
    4. Logs all actions for audit

    Example:
        recovery = SpreadRecoveryService(
            repository=spread_repo,
            kalshi_client=kalshi,
            polymarket_client=polymarket,
        )

        # Run recovery on startup
        results = await recovery.recover_all()

        for result in results:
            if not result.success:
                logger.error("Recovery failed: %s", result.message)
    """

    # Maximum recovery attempts before escalation
    MAX_RECOVERY_ATTEMPTS = 3

    def __init__(
        self,
        repository: SpreadExecutionRepository,
        kalshi_client: Optional[ExchangeClient] = None,
        polymarket_client: Optional[ExchangeClient] = None,
        alert_callback: Optional[Callable[[str, str, Dict], None]] = None,
        dry_run: bool = False,
    ):
        """Initialize recovery service.

        Args:
            repository: SpreadExecutionRepository for database access
            kalshi_client: Kalshi exchange client
            polymarket_client: Polymarket exchange client
            alert_callback: Callback for alerts (name, message, context)
            dry_run: If True, don't execute recovery actions
        """
        self._repository = repository
        self._kalshi = kalshi_client
        self._polymarket = polymarket_client
        self._alert_callback = alert_callback
        self._dry_run = dry_run

        self._clients: Dict[str, ExchangeClient] = {}
        if kalshi_client:
            self._clients["kalshi"] = kalshi_client
        if polymarket_client:
            self._clients["polymarket"] = polymarket_client

    async def recover_all(self) -> List[RecoveryResult]:
        """Recover all incomplete spread executions.

        This is the main entry point, typically called on startup.

        Returns:
            List of recovery results
        """
        logger.info("Starting spread recovery check...")

        # Get all incomplete executions
        incomplete = await self._repository.get_incomplete_executions()

        if not incomplete:
            logger.info("No incomplete spread executions found")
            return []

        logger.warning(
            "Found %d incomplete spread executions requiring recovery",
            len(incomplete),
        )

        results = []
        for spread in incomplete:
            try:
                result = await self.recover_spread(spread)
                results.append(result)

                if not result.success:
                    logger.error(
                        "Recovery failed for spread %s: %s",
                        spread.spread_id,
                        result.message,
                    )
                else:
                    logger.info(
                        "Recovery successful for spread %s: %s",
                        spread.spread_id,
                        result.message,
                    )

            except Exception as e:
                logger.exception(
                    "Exception during recovery of spread %s: %s",
                    spread.spread_id,
                    e,
                )
                results.append(
                    RecoveryResult(
                        spread_id=spread.spread_id,
                        action=RecoveryAction.ESCALATE,
                        success=False,
                        new_status=SpreadExecutionStatus.RECOVERY_NEEDED,
                        message=f"Exception during recovery: {e}",
                    )
                )

        # Log summary
        successful = sum(1 for r in results if r.success)
        failed = len(results) - successful
        logger.info(
            "Recovery complete: %d successful, %d failed",
            successful,
            failed,
        )

        return results

    async def recover_spread(
        self,
        spread: SpreadExecutionModel,
    ) -> RecoveryResult:
        """Recover a single spread execution.

        Args:
            spread: The spread execution to recover

        Returns:
            RecoveryResult with outcome
        """
        logger.info(
            "Recovering spread %s (status=%s, attempts=%d)",
            spread.spread_id,
            spread.status.value,
            spread.recovery_attempts,
        )

        # Check if max attempts exceeded
        if spread.recovery_attempts >= self.MAX_RECOVERY_ATTEMPTS:
            return await self._escalate(
                spread,
                f"Max recovery attempts ({self.MAX_RECOVERY_ATTEMPTS}) exceeded",
            )

        # Increment recovery attempts
        await self._repository.increment_recovery_attempts(spread.spread_id)

        # Determine action based on current status
        action = self._determine_action(spread)
        logger.info(
            "Determined recovery action for %s: %s",
            spread.spread_id,
            action.value,
        )

        # Execute action
        if action == RecoveryAction.NO_ACTION:
            return RecoveryResult(
                spread_id=spread.spread_id,
                action=action,
                success=True,
                new_status=spread.status,
                message="No recovery action needed",
            )

        elif action == RecoveryAction.CHECK_ORDER:
            return await self._check_pending_order(spread)

        elif action == RecoveryAction.COMPLETE_LEG2:
            return await self._complete_leg2(spread)

        elif action == RecoveryAction.ROLLBACK_LEG1:
            return await self._rollback_leg1(spread)

        elif action == RecoveryAction.MARK_FAILED:
            return await self._mark_failed(spread, "No viable recovery path")

        elif action == RecoveryAction.ESCALATE:
            return await self._escalate(spread, "Manual intervention required")

        return await self._escalate(spread, f"Unknown action: {action}")

    def _determine_action(self, spread: SpreadExecutionModel) -> RecoveryAction:
        """Determine the appropriate recovery action.

        Args:
            spread: Spread execution to analyze

        Returns:
            Appropriate RecoveryAction
        """
        status = spread.status

        if status == SpreadExecutionStatus.PENDING:
            # Never started - just mark as failed
            return RecoveryAction.MARK_FAILED

        elif status == SpreadExecutionStatus.LEG1_SUBMITTED:
            # Leg 1 was submitted but we don't know if it filled
            return RecoveryAction.CHECK_ORDER

        elif status == SpreadExecutionStatus.LEG1_FILLED:
            # Leg 1 filled but leg 2 not started - critical exposure!
            if spread.leg1_filled_size > 0:
                # Try to complete leg 2, or rollback if that fails
                return RecoveryAction.COMPLETE_LEG2
            return RecoveryAction.MARK_FAILED

        elif status == SpreadExecutionStatus.LEG2_SUBMITTED:
            # Leg 2 was submitted but we don't know if it filled
            return RecoveryAction.CHECK_ORDER

        elif status == SpreadExecutionStatus.PARTIAL:
            # Partial fill - need to evaluate
            if spread.leg1_filled_size > spread.leg2_filled_size:
                # More exposure on leg 1 - try to complete or rollback
                return RecoveryAction.ROLLBACK_LEG1
            return RecoveryAction.ESCALATE

        elif status == SpreadExecutionStatus.ROLLBACK_PENDING:
            # Rollback was in progress
            return RecoveryAction.CHECK_ORDER

        elif status == SpreadExecutionStatus.RECOVERY_NEEDED:
            # Already marked for manual intervention
            return RecoveryAction.ESCALATE

        elif status in (
            SpreadExecutionStatus.COMPLETED,
            SpreadExecutionStatus.FAILED,
            SpreadExecutionStatus.ROLLED_BACK,
        ):
            # Already in terminal state
            return RecoveryAction.NO_ACTION

        return RecoveryAction.ESCALATE

    async def _check_pending_order(
        self,
        spread: SpreadExecutionModel,
    ) -> RecoveryResult:
        """Check status of a pending order on the exchange.

        Args:
            spread: Spread with pending order

        Returns:
            RecoveryResult
        """
        # Determine which order to check
        if spread.status == SpreadExecutionStatus.LEG1_SUBMITTED:
            order_id = spread.leg1_order_id
            exchange = spread.leg1_exchange
            leg = "leg1"
        elif spread.status == SpreadExecutionStatus.LEG2_SUBMITTED:
            order_id = spread.leg2_order_id
            exchange = spread.leg2_exchange
            leg = "leg2"
        elif spread.status == SpreadExecutionStatus.ROLLBACK_PENDING:
            order_id = spread.rollback_order_id
            exchange = spread.leg1_exchange  # Rollback is on leg1 exchange
            leg = "rollback"
        else:
            return await self._escalate(spread, f"Cannot check order in status {spread.status}")

        if not order_id:
            return await self._escalate(spread, f"No order ID for {leg}")

        client = self._clients.get(exchange.lower())
        if not client:
            return await self._escalate(spread, f"No client for exchange {exchange}")

        if self._dry_run:
            return RecoveryResult(
                spread_id=spread.spread_id,
                action=RecoveryAction.CHECK_ORDER,
                success=True,
                new_status=spread.status,
                message=f"[DRY RUN] Would check {leg} order {order_id}",
            )

        # Check order on exchange
        try:
            order = client.get_order(order_id)
        except Exception as e:
            return await self._escalate(spread, f"Failed to get order from exchange: {e}")

        if not order:
            # Order not found - may have been canceled or expired
            return await self._mark_failed(spread, f"Order {order_id} not found on exchange")

        # Process based on order status
        order_status = order.get("status", "").lower()
        filled_size = order.get("filled_size", 0)
        fill_price = order.get("avg_fill_price") or order.get("price", 0)

        if order_status in ("filled", "closed") and filled_size > 0:
            # Order filled - update our records
            if leg == "leg1":
                await self._repository.update_leg1_fill(
                    spread.spread_id,
                    order_id,
                    filled_size,
                    Decimal(str(fill_price)),
                )
                # Now need to execute leg 2
                spread = await self._repository.get_by_spread_id(spread.spread_id)
                return await self._complete_leg2(spread)

            elif leg == "leg2":
                # Calculate profit
                profit = self._calculate_profit(spread, filled_size, fill_price)
                await self._repository.update_leg2_fill(
                    spread.spread_id,
                    order_id,
                    filled_size,
                    Decimal(str(fill_price)),
                    profit,
                )
                return RecoveryResult(
                    spread_id=spread.spread_id,
                    action=RecoveryAction.CHECK_ORDER,
                    success=True,
                    new_status=SpreadExecutionStatus.COMPLETED,
                    message=f"Leg 2 fill confirmed, spread complete",
                    details={"profit": float(profit) if profit else 0},
                )

            elif leg == "rollback":
                await self._repository.update_rollback(
                    spread.spread_id,
                    order_id,
                    filled_size,
                )
                return RecoveryResult(
                    spread_id=spread.spread_id,
                    action=RecoveryAction.CHECK_ORDER,
                    success=True,
                    new_status=SpreadExecutionStatus.ROLLED_BACK,
                    message="Rollback confirmed complete",
                )

        elif order_status in ("canceled", "expired", "rejected"):
            # Order was canceled
            if leg == "leg1":
                return await self._mark_failed(spread, "Leg 1 order was canceled/expired")
            elif leg == "leg2":
                # Leg 1 filled but leg 2 canceled - need to rollback
                return await self._rollback_leg1(spread)
            elif leg == "rollback":
                return await self._escalate(spread, "Rollback order canceled - manual intervention needed")

        elif order_status in ("open", "pending", "partial"):
            # Order still open - wait or escalate
            return await self._escalate(
                spread,
                f"Order {order_id} still {order_status} - may need manual intervention",
            )

        return await self._escalate(spread, f"Unknown order status: {order_status}")

    async def _complete_leg2(
        self,
        spread: SpreadExecutionModel,
    ) -> RecoveryResult:
        """Attempt to complete leg 2 of the spread.

        Args:
            spread: Spread with leg 1 filled

        Returns:
            RecoveryResult
        """
        client = self._clients.get(spread.leg2_exchange.lower())
        if not client:
            # Can't complete leg 2 - must rollback
            logger.warning(
                "No client for %s, attempting rollback instead",
                spread.leg2_exchange,
            )
            return await self._rollback_leg1(spread)

        if self._dry_run:
            return RecoveryResult(
                spread_id=spread.spread_id,
                action=RecoveryAction.COMPLETE_LEG2,
                success=True,
                new_status=spread.status,
                message=f"[DRY RUN] Would place leg 2 order: {spread.leg2_side} {spread.leg2_size}@{spread.leg2_price}",
            )

        # Place leg 2 order
        try:
            order = client.place_order(
                ticker=spread.leg2_ticker,
                side=spread.leg2_side,
                price=float(spread.leg2_price),
                size=spread.leg2_size,
            )

            if not order:
                # Failed to place order - attempt rollback
                return await self._rollback_leg1(spread)

            order_id = order.get("order_id") or order.get("id")

            # Update status to leg2 submitted
            await self._repository.update_status(
                spread.spread_id,
                SpreadExecutionStatus.LEG2_SUBMITTED,
            )

            # Check if immediately filled
            if order.get("status", "").lower() in ("filled", "closed"):
                filled_size = order.get("filled_size", spread.leg2_size)
                fill_price = order.get("avg_fill_price") or spread.leg2_price
                profit = self._calculate_profit(spread, filled_size, fill_price)

                await self._repository.update_leg2_fill(
                    spread.spread_id,
                    order_id,
                    filled_size,
                    Decimal(str(fill_price)),
                    profit,
                )

                return RecoveryResult(
                    spread_id=spread.spread_id,
                    action=RecoveryAction.COMPLETE_LEG2,
                    success=True,
                    new_status=SpreadExecutionStatus.COMPLETED,
                    message="Leg 2 completed successfully",
                    details={"profit": float(profit) if profit else 0},
                )

            return RecoveryResult(
                spread_id=spread.spread_id,
                action=RecoveryAction.COMPLETE_LEG2,
                success=True,
                new_status=SpreadExecutionStatus.LEG2_SUBMITTED,
                message=f"Leg 2 order placed: {order_id}",
                details={"order_id": order_id},
            )

        except Exception as e:
            logger.exception("Failed to place leg 2 order: %s", e)
            # Attempt rollback
            return await self._rollback_leg1(spread)

    async def _rollback_leg1(
        self,
        spread: SpreadExecutionModel,
    ) -> RecoveryResult:
        """Rollback leg 1 by placing an opposite order.

        Args:
            spread: Spread with leg 1 exposure to unwind

        Returns:
            RecoveryResult
        """
        if spread.leg1_filled_size == 0:
            return await self._mark_failed(spread, "No leg 1 exposure to rollback")

        client = self._clients.get(spread.leg1_exchange.lower())
        if not client:
            return await self._escalate(
                spread,
                f"No client for {spread.leg1_exchange} - cannot rollback",
            )

        # Determine rollback order
        rollback_side = "sell" if spread.leg1_side.lower() == "buy" else "buy"
        rollback_size = spread.leg1_filled_size

        if self._dry_run:
            return RecoveryResult(
                spread_id=spread.spread_id,
                action=RecoveryAction.ROLLBACK_LEG1,
                success=True,
                new_status=spread.status,
                message=f"[DRY RUN] Would place rollback: {rollback_side} {rollback_size}",
            )

        try:
            # Use market price (or slight slippage) for rollback
            rollback_price = float(spread.leg1_fill_price or spread.leg1_price)
            if rollback_side == "sell":
                rollback_price *= 0.98  # Accept 2% slippage on sell
            else:
                rollback_price *= 1.02  # Accept 2% slippage on buy

            order = client.place_order(
                ticker=spread.leg1_ticker,
                side=rollback_side,
                price=rollback_price,
                size=rollback_size,
            )

            if not order:
                return await self._escalate(spread, "Failed to place rollback order")

            order_id = order.get("order_id") or order.get("id")

            # Update status
            await self._repository.update_status(
                spread.spread_id,
                SpreadExecutionStatus.ROLLBACK_PENDING,
            )

            # Check if immediately filled
            if order.get("status", "").lower() in ("filled", "closed"):
                filled_size = order.get("filled_size", rollback_size)
                await self._repository.update_rollback(
                    spread.spread_id,
                    order_id,
                    filled_size,
                )

                return RecoveryResult(
                    spread_id=spread.spread_id,
                    action=RecoveryAction.ROLLBACK_LEG1,
                    success=True,
                    new_status=SpreadExecutionStatus.ROLLED_BACK,
                    message="Rollback completed successfully",
                )

            return RecoveryResult(
                spread_id=spread.spread_id,
                action=RecoveryAction.ROLLBACK_LEG1,
                success=True,
                new_status=SpreadExecutionStatus.ROLLBACK_PENDING,
                message=f"Rollback order placed: {order_id}",
                details={"order_id": order_id},
            )

        except Exception as e:
            logger.exception("Failed to place rollback order: %s", e)
            return await self._escalate(spread, f"Rollback failed: {e}")

    async def _mark_failed(
        self,
        spread: SpreadExecutionModel,
        reason: str,
    ) -> RecoveryResult:
        """Mark spread as failed.

        Args:
            spread: Spread to mark failed
            reason: Failure reason

        Returns:
            RecoveryResult
        """
        await self._repository.update_status(
            spread.spread_id,
            SpreadExecutionStatus.FAILED,
            reason,
        )

        return RecoveryResult(
            spread_id=spread.spread_id,
            action=RecoveryAction.MARK_FAILED,
            success=True,
            new_status=SpreadExecutionStatus.FAILED,
            message=f"Marked as failed: {reason}",
        )

    async def _escalate(
        self,
        spread: SpreadExecutionModel,
        reason: str,
    ) -> RecoveryResult:
        """Escalate spread for manual intervention.

        Args:
            spread: Spread requiring manual intervention
            reason: Escalation reason

        Returns:
            RecoveryResult
        """
        await self._repository.mark_recovery_needed(spread.spread_id, reason)

        # Send alert
        if self._alert_callback:
            try:
                self._alert_callback(
                    "spread_recovery_escalation",
                    f"Spread {spread.spread_id} requires manual intervention: {reason}",
                    {
                        "spread_id": spread.spread_id,
                        "status": spread.status.value,
                        "leg1_filled": spread.leg1_filled_size,
                        "leg2_filled": spread.leg2_filled_size,
                        "reason": reason,
                    },
                )
            except Exception as e:
                logger.error("Failed to send escalation alert: %s", e)

        return RecoveryResult(
            spread_id=spread.spread_id,
            action=RecoveryAction.ESCALATE,
            success=False,
            new_status=SpreadExecutionStatus.RECOVERY_NEEDED,
            message=f"Escalated for manual intervention: {reason}",
        )

    def _calculate_profit(
        self,
        spread: SpreadExecutionModel,
        leg2_filled_size: int,
        leg2_fill_price: float,
    ) -> Decimal:
        """Calculate actual profit from spread execution.

        Args:
            spread: Spread execution
            leg2_filled_size: Size filled on leg 2
            leg2_fill_price: Fill price on leg 2

        Returns:
            Calculated profit (may be negative)
        """
        # Determine which leg is buy/sell
        if spread.leg1_side.lower() == "buy":
            buy_price = float(spread.leg1_fill_price or spread.leg1_price)
            buy_size = spread.leg1_filled_size
            sell_price = leg2_fill_price
            sell_size = leg2_filled_size
        else:
            sell_price = float(spread.leg1_fill_price or spread.leg1_price)
            sell_size = spread.leg1_filled_size
            buy_price = leg2_fill_price
            buy_size = leg2_filled_size

        # Use minimum size for profit calculation
        effective_size = min(buy_size, sell_size)

        # Profit = (sell - buy) * size - fees
        gross_profit = (sell_price - buy_price) * effective_size
        fees = spread.total_fees or Decimal("0")
        profit = Decimal(str(gross_profit)) - fees

        return profit
