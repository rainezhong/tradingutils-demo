"""Spread Execution Persistence Layer.

Provides checkpointing for spread executions to enable crash recovery.
Wraps the SpreadExecutor to persist state at each transition.
"""

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import SpreadExecutionModel, SpreadExecutionStatus
from src.database.repository import SpreadExecutionRepository
from src.oms.models import (
    SpreadExecutionResult,
    SpreadExecutionStatus as OMSSpreadStatus,
    SpreadLeg,
)


logger = logging.getLogger(__name__)


def _map_oms_status_to_db(oms_status: OMSSpreadStatus) -> SpreadExecutionStatus:
    """Map OMS status enum to database status enum."""
    mapping = {
        OMSSpreadStatus.PENDING: SpreadExecutionStatus.PENDING,
        OMSSpreadStatus.LEG1_SUBMITTED: SpreadExecutionStatus.LEG1_SUBMITTED,
        OMSSpreadStatus.LEG1_FILLED: SpreadExecutionStatus.LEG1_FILLED,
        OMSSpreadStatus.LEG2_SUBMITTED: SpreadExecutionStatus.LEG2_SUBMITTED,
        OMSSpreadStatus.COMPLETED: SpreadExecutionStatus.COMPLETED,
        OMSSpreadStatus.PARTIAL: SpreadExecutionStatus.PARTIAL,
        OMSSpreadStatus.ROLLBACK_PENDING: SpreadExecutionStatus.ROLLBACK_PENDING,
        OMSSpreadStatus.ROLLED_BACK: SpreadExecutionStatus.ROLLED_BACK,
        OMSSpreadStatus.FAILED: SpreadExecutionStatus.FAILED,
    }
    return mapping.get(oms_status, SpreadExecutionStatus.PENDING)


class SpreadPersistenceManager:
    """Manages persistence of spread execution state.

    Provides methods to checkpoint spread state at each transition,
    enabling recovery after crashes.

    Example:
        persistence = SpreadPersistenceManager(session)

        # Create checkpoint when starting spread
        spread_id = await persistence.create_spread(
            opportunity_id="opp-123",
            leg1_exchange="kalshi",
            leg1_ticker="MARKET-YES",
            ...
        )

        # Update on leg 1 fill
        await persistence.update_leg1_fill(
            spread_id=spread_id,
            order_id="order-456",
            filled_size=100,
            fill_price=0.45,
        )

        # Update on completion
        await persistence.mark_completed(
            spread_id=spread_id,
            actual_profit=1.50,
        )
    """

    def __init__(self, session: AsyncSession):
        """Initialize persistence manager.

        Args:
            session: SQLAlchemy async session
        """
        self._session = session
        self._repository = SpreadExecutionRepository(session)

    async def create_spread(
        self,
        opportunity_id: str,
        leg1_exchange: str,
        leg1_ticker: str,
        leg1_side: str,
        leg1_price: float,
        leg1_size: int,
        leg2_exchange: str,
        leg2_ticker: str,
        leg2_side: str,
        leg2_price: float,
        leg2_size: int,
        expected_profit: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create a new spread execution checkpoint.

        Args:
            opportunity_id: ID of the opportunity being executed
            leg1_*: Leg 1 order details
            leg2_*: Leg 2 order details
            expected_profit: Expected profit from the spread
            metadata: Additional metadata

        Returns:
            Generated spread_id for tracking
        """
        spread_id = f"SPREAD-{uuid.uuid4().hex[:12].upper()}"

        spread = SpreadExecutionModel(
            spread_id=spread_id,
            opportunity_id=opportunity_id,
            status=SpreadExecutionStatus.PENDING,
            leg1_exchange=leg1_exchange,
            leg1_ticker=leg1_ticker,
            leg1_side=leg1_side,
            leg1_price=Decimal(str(leg1_price)),
            leg1_size=leg1_size,
            leg2_exchange=leg2_exchange,
            leg2_ticker=leg2_ticker,
            leg2_side=leg2_side,
            leg2_price=Decimal(str(leg2_price)),
            leg2_size=leg2_size,
            expected_profit=Decimal(str(expected_profit)),
            metadata_=metadata,
        )

        await self._repository.create(spread)
        logger.info("Created spread checkpoint: %s", spread_id)

        return spread_id

    async def update_leg1_submitted(
        self,
        spread_id: str,
        order_id: str,
    ) -> None:
        """Update checkpoint when leg 1 order is submitted.

        Args:
            spread_id: Spread identifier
            order_id: Exchange order ID
        """
        spread = await self._repository.get_by_spread_id(spread_id)
        if spread:
            spread.leg1_order_id = order_id
            spread.status = SpreadExecutionStatus.LEG1_SUBMITTED
            await self._repository.update(spread)
            logger.debug("Updated spread %s: leg1_submitted", spread_id)

    async def update_leg1_fill(
        self,
        spread_id: str,
        order_id: str,
        filled_size: int,
        fill_price: float,
    ) -> None:
        """Update checkpoint when leg 1 is filled.

        Args:
            spread_id: Spread identifier
            order_id: Exchange order ID
            filled_size: Number of contracts filled
            fill_price: Average fill price
        """
        await self._repository.update_leg1_fill(
            spread_id,
            order_id,
            filled_size,
            Decimal(str(fill_price)),
        )
        logger.debug(
            "Updated spread %s: leg1_filled (size=%d, price=%.4f)",
            spread_id,
            filled_size,
            fill_price,
        )

    async def update_leg2_submitted(
        self,
        spread_id: str,
        order_id: str,
    ) -> None:
        """Update checkpoint when leg 2 order is submitted.

        Args:
            spread_id: Spread identifier
            order_id: Exchange order ID
        """
        spread = await self._repository.get_by_spread_id(spread_id)
        if spread:
            spread.leg2_order_id = order_id
            spread.status = SpreadExecutionStatus.LEG2_SUBMITTED
            await self._repository.update(spread)
            logger.debug("Updated spread %s: leg2_submitted", spread_id)

    async def update_leg2_fill(
        self,
        spread_id: str,
        order_id: str,
        filled_size: int,
        fill_price: float,
        actual_profit: Optional[float] = None,
        total_fees: float = 0.0,
    ) -> None:
        """Update checkpoint when leg 2 is filled.

        Args:
            spread_id: Spread identifier
            order_id: Exchange order ID
            filled_size: Number of contracts filled
            fill_price: Average fill price
            actual_profit: Calculated actual profit
            total_fees: Total fees paid
        """
        profit = Decimal(str(actual_profit)) if actual_profit is not None else None

        spread = await self._repository.get_by_spread_id(spread_id)
        if spread:
            spread.leg2_order_id = order_id
            spread.leg2_filled_size = filled_size
            spread.leg2_fill_price = Decimal(str(fill_price))
            spread.status = SpreadExecutionStatus.COMPLETED
            spread.completed_at = datetime.utcnow()
            if profit is not None:
                spread.actual_profit = profit
            spread.total_fees = Decimal(str(total_fees))
            await self._repository.update(spread)

        logger.debug(
            "Updated spread %s: leg2_filled (size=%d, price=%.4f, profit=%.4f)",
            spread_id,
            filled_size,
            fill_price,
            actual_profit or 0,
        )

    async def update_rollback_started(
        self,
        spread_id: str,
        order_id: str,
    ) -> None:
        """Update checkpoint when rollback order is submitted.

        Args:
            spread_id: Spread identifier
            order_id: Rollback order ID
        """
        spread = await self._repository.get_by_spread_id(spread_id)
        if spread:
            spread.rollback_order_id = order_id
            spread.status = SpreadExecutionStatus.ROLLBACK_PENDING
            await self._repository.update(spread)
            logger.debug("Updated spread %s: rollback_started", spread_id)

    async def update_rollback_complete(
        self,
        spread_id: str,
        filled_size: int,
    ) -> None:
        """Update checkpoint when rollback is complete.

        Args:
            spread_id: Spread identifier
            filled_size: Number of contracts rolled back
        """
        spread = await self._repository.get_by_spread_id(spread_id)
        if spread:
            spread.rollback_filled_size = filled_size
            spread.status = SpreadExecutionStatus.ROLLED_BACK
            spread.completed_at = datetime.utcnow()
            await self._repository.update(spread)
            logger.debug("Updated spread %s: rolled_back", spread_id)

    async def mark_failed(
        self,
        spread_id: str,
        error_message: str,
    ) -> None:
        """Mark spread as failed.

        Args:
            spread_id: Spread identifier
            error_message: Failure reason
        """
        await self._repository.update_status(
            spread_id,
            SpreadExecutionStatus.FAILED,
            error_message,
        )
        logger.warning("Marked spread %s as failed: %s", spread_id, error_message)

    async def get_spread(self, spread_id: str) -> Optional[SpreadExecutionModel]:
        """Get spread execution by ID.

        Args:
            spread_id: Spread identifier

        Returns:
            SpreadExecutionModel if found
        """
        return await self._repository.get_by_spread_id(spread_id)

    async def sync_from_result(
        self,
        result: SpreadExecutionResult,
    ) -> None:
        """Sync database state from a SpreadExecutionResult.

        Useful for keeping database in sync with in-memory state.

        Args:
            result: SpreadExecutionResult from SpreadExecutor
        """
        spread = await self._repository.get_by_spread_id(result.spread_id)
        if not spread:
            logger.warning(
                "Cannot sync - spread %s not found in database",
                result.spread_id,
            )
            return

        # Update status
        spread.status = _map_oms_status_to_db(result.status)

        # Update leg 1
        if result.leg1.order:
            spread.leg1_order_id = result.leg1.order.order_id
            spread.leg1_filled_size = result.leg1.actual_fill_size
            if result.leg1.actual_fill_price:
                spread.leg1_fill_price = Decimal(str(result.leg1.actual_fill_price))

        # Update leg 2
        if result.leg2.order:
            spread.leg2_order_id = result.leg2.order.order_id
            spread.leg2_filled_size = result.leg2.actual_fill_size
            if result.leg2.actual_fill_price:
                spread.leg2_fill_price = Decimal(str(result.leg2.actual_fill_price))

        # Update rollback
        if result.rollback_order:
            spread.rollback_order_id = result.rollback_order.order_id
            spread.rollback_filled_size = result.rollback_order.filled_size

        # Update profit/timing
        if result.actual_profit is not None:
            spread.actual_profit = Decimal(str(result.actual_profit))
        spread.total_fees = Decimal(str(result.total_fees))

        if result.is_complete:
            spread.completed_at = result.completed_at or datetime.utcnow()

        if result.error:
            spread.error_message = result.error

        await self._repository.update(spread)
        logger.debug("Synced spread %s from result", result.spread_id)


class PersistentSpreadExecutorMixin:
    """Mixin to add persistence to SpreadExecutor.

    Add this mixin to SpreadExecutor to automatically checkpoint
    state at each transition.

    Example:
        class PersistentSpreadExecutor(PersistentSpreadExecutorMixin, SpreadExecutor):
            pass

        executor = PersistentSpreadExecutor(oms, capital_manager, persistence_manager)
    """

    _persistence: Optional[SpreadPersistenceManager] = None

    def set_persistence(self, persistence: SpreadPersistenceManager) -> None:
        """Set the persistence manager.

        Args:
            persistence: SpreadPersistenceManager instance
        """
        self._persistence = persistence

    async def _checkpoint_spread_created(
        self,
        spread_id: str,
        opportunity_id: str,
        leg1: SpreadLeg,
        leg2: SpreadLeg,
        expected_profit: float,
    ) -> None:
        """Checkpoint when spread execution starts."""
        if self._persistence:
            await self._persistence.create_spread(
                opportunity_id=opportunity_id,
                leg1_exchange=leg1.exchange,
                leg1_ticker=leg1.ticker,
                leg1_side=leg1.side,
                leg1_price=leg1.price,
                leg1_size=leg1.size,
                leg2_exchange=leg2.exchange,
                leg2_ticker=leg2.ticker,
                leg2_side=leg2.side,
                leg2_price=leg2.price,
                leg2_size=leg2.size,
                expected_profit=expected_profit,
            )

    async def _checkpoint_leg1_submitted(
        self,
        spread_id: str,
        order_id: str,
    ) -> None:
        """Checkpoint when leg 1 is submitted."""
        if self._persistence:
            await self._persistence.update_leg1_submitted(spread_id, order_id)

    async def _checkpoint_leg1_filled(
        self,
        spread_id: str,
        order_id: str,
        filled_size: int,
        fill_price: float,
    ) -> None:
        """Checkpoint when leg 1 is filled."""
        if self._persistence:
            await self._persistence.update_leg1_fill(
                spread_id, order_id, filled_size, fill_price
            )

    async def _checkpoint_leg2_submitted(
        self,
        spread_id: str,
        order_id: str,
    ) -> None:
        """Checkpoint when leg 2 is submitted."""
        if self._persistence:
            await self._persistence.update_leg2_submitted(spread_id, order_id)

    async def _checkpoint_completed(
        self,
        spread_id: str,
        order_id: str,
        filled_size: int,
        fill_price: float,
        actual_profit: float,
        total_fees: float,
    ) -> None:
        """Checkpoint when spread is completed."""
        if self._persistence:
            await self._persistence.update_leg2_fill(
                spread_id, order_id, filled_size, fill_price, actual_profit, total_fees
            )

    async def _checkpoint_rollback_started(
        self,
        spread_id: str,
        order_id: str,
    ) -> None:
        """Checkpoint when rollback starts."""
        if self._persistence:
            await self._persistence.update_rollback_started(spread_id, order_id)

    async def _checkpoint_rolled_back(
        self,
        spread_id: str,
        filled_size: int,
    ) -> None:
        """Checkpoint when rollback completes."""
        if self._persistence:
            await self._persistence.update_rollback_complete(spread_id, filled_size)

    async def _checkpoint_failed(
        self,
        spread_id: str,
        error_message: str,
    ) -> None:
        """Checkpoint when spread fails."""
        if self._persistence:
            await self._persistence.mark_failed(spread_id, error_message)
