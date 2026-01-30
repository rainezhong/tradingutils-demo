"""Repository layer for database operations.

Provides async CRUD operations, query methods, and transaction support
using SQLAlchemy 2.0 patterns.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Generic, List, Optional, Sequence, Type, TypeVar
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.database.models import (
    BalanceModel,
    Base,
    FillModel,
    MarketModel,
    OpportunityModel,
    OpportunityStatus,
    OrderModel,
    OrderStatus,
    Platform,
    PositionModel,
    SpreadExecutionModel,
    SpreadExecutionStatus,
    SystemEventModel,
    TradeModel,
)

logger = logging.getLogger(__name__)

# Generic type for repository models
ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Base repository with common CRUD operations.

    Provides standard create, read, update, delete operations
    that can be inherited by specific repositories.
    """

    model_class: Type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        """Initialize repository with session.

        Args:
            session: SQLAlchemy async session
        """
        self._session = session

    async def get_by_id(self, id: UUID) -> Optional[ModelT]:
        """Get entity by ID.

        Args:
            id: Entity UUID

        Returns:
            Entity if found, None otherwise
        """
        result = await self._session.execute(
            select(self.model_class).where(self.model_class.id == id)
        )
        return result.scalar_one_or_none()

    async def get_all(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[ModelT]:
        """Get all entities with pagination.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of entities
        """
        result = await self._session.execute(
            select(self.model_class)
            .order_by(self.model_class.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def create(self, entity: ModelT) -> ModelT:
        """Create a new entity.

        Args:
            entity: Entity to create

        Returns:
            Created entity with generated ID
        """
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def create_many(self, entities: List[ModelT]) -> List[ModelT]:
        """Create multiple entities.

        Args:
            entities: Entities to create

        Returns:
            Created entities with generated IDs
        """
        self._session.add_all(entities)
        await self._session.flush()
        for entity in entities:
            await self._session.refresh(entity)
        return entities

    async def update(self, entity: ModelT) -> ModelT:
        """Update an entity.

        Args:
            entity: Entity with updated values

        Returns:
            Updated entity
        """
        await self._session.merge(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return entity

    async def delete(self, id: UUID) -> bool:
        """Delete an entity by ID.

        Args:
            id: Entity UUID

        Returns:
            True if deleted, False if not found
        """
        result = await self._session.execute(
            delete(self.model_class).where(self.model_class.id == id)
        )
        return result.rowcount > 0

    async def count(self) -> int:
        """Count total entities.

        Returns:
            Total count
        """
        result = await self._session.execute(
            select(func.count()).select_from(self.model_class)
        )
        return result.scalar_one()


class MarketRepository(BaseRepository[MarketModel]):
    """Repository for market operations."""

    model_class = MarketModel

    async def get_by_ticker(
        self,
        platform: Platform,
        ticker: str,
    ) -> Optional[MarketModel]:
        """Get market by platform and ticker.

        Args:
            platform: Trading platform
            ticker: Market ticker

        Returns:
            Market if found, None otherwise
        """
        result = await self._session.execute(
            select(MarketModel).where(
                MarketModel.platform == platform,
                MarketModel.ticker == ticker,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_external_id(
        self,
        platform: Platform,
        external_id: str,
    ) -> Optional[MarketModel]:
        """Get market by platform and external ID.

        Args:
            platform: Trading platform
            external_id: External market ID

        Returns:
            Market if found, None otherwise
        """
        result = await self._session.execute(
            select(MarketModel).where(
                MarketModel.platform == platform,
                MarketModel.external_id == external_id,
            )
        )
        return result.scalar_one_or_none()

    async def upsert(self, market: MarketModel) -> MarketModel:
        """Insert or update a market.

        Args:
            market: Market to upsert

        Returns:
            Upserted market
        """
        existing = await self.get_by_external_id(market.platform, market.external_id)
        if existing:
            existing.ticker = market.ticker
            existing.title = market.title
            existing.category = market.category
            existing.close_time = market.close_time
            existing.status = market.status
            existing.metadata_ = market.metadata_
            return await self.update(existing)
        return await self.create(market)

    async def get_active_markets(
        self,
        platform: Optional[Platform] = None,
        category: Optional[str] = None,
    ) -> Sequence[MarketModel]:
        """Get active markets with optional filters.

        Args:
            platform: Filter by platform
            category: Filter by category

        Returns:
            List of active markets
        """
        from src.database.models import MarketStatus

        query = select(MarketModel).where(MarketModel.status == MarketStatus.ACTIVE)

        if platform:
            query = query.where(MarketModel.platform == platform)
        if category:
            query = query.where(MarketModel.category == category)

        query = query.order_by(MarketModel.close_time.asc())
        result = await self._session.execute(query)
        return result.scalars().all()

    async def get_expiring_soon(
        self,
        hours: int = 24,
        platform: Optional[Platform] = None,
    ) -> Sequence[MarketModel]:
        """Get markets expiring within given hours.

        Args:
            hours: Hours until expiration
            platform: Filter by platform

        Returns:
            List of expiring markets
        """
        from src.database.models import MarketStatus

        cutoff = datetime.utcnow() + timedelta(hours=hours)
        query = select(MarketModel).where(
            MarketModel.status == MarketStatus.ACTIVE,
            MarketModel.close_time <= cutoff,
            MarketModel.close_time > datetime.utcnow(),
        )

        if platform:
            query = query.where(MarketModel.platform == platform)

        query = query.order_by(MarketModel.close_time.asc())
        result = await self._session.execute(query)
        return result.scalars().all()


class OpportunityRepository(BaseRepository[OpportunityModel]):
    """Repository for arbitrage opportunity operations."""

    model_class = OpportunityModel

    async def get_open_opportunities(
        self,
        min_roi: float = 0.0,
        limit: int = 100,
    ) -> Sequence[OpportunityModel]:
        """Get open opportunities with minimum ROI.

        Args:
            min_roi: Minimum ROI threshold
            limit: Maximum results

        Returns:
            List of open opportunities
        """
        result = await self._session.execute(
            select(OpportunityModel)
            .where(
                OpportunityModel.status == OpportunityStatus.OPEN,
                OpportunityModel.roi >= Decimal(str(min_roi)),
            )
            .options(
                selectinload(OpportunityModel.kalshi_market),
                selectinload(OpportunityModel.polymarket_market),
            )
            .order_by(OpportunityModel.roi.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_recent_opportunities(
        self,
        hours: int = 24,
        status: Optional[OpportunityStatus] = None,
    ) -> Sequence[OpportunityModel]:
        """Get opportunities from recent hours.

        Args:
            hours: Hours to look back
            status: Filter by status

        Returns:
            List of recent opportunities
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        query = select(OpportunityModel).where(
            OpportunityModel.detected_at >= cutoff
        )

        if status:
            query = query.where(OpportunityModel.status == status)

        query = query.order_by(OpportunityModel.detected_at.desc())
        result = await self._session.execute(query)
        return result.scalars().all()

    async def create_with_orders(
        self,
        opportunity: OpportunityModel,
        orders: List[OrderModel],
    ) -> OpportunityModel:
        """Create opportunity with associated orders atomically.

        Args:
            opportunity: Opportunity to create
            orders: Orders to associate

        Returns:
            Created opportunity with orders
        """
        # Create opportunity first
        self._session.add(opportunity)
        await self._session.flush()

        # Associate orders with opportunity
        for order in orders:
            order.opportunity_id = opportunity.id
            self._session.add(order)

        await self._session.flush()
        await self._session.refresh(opportunity)
        return opportunity

    async def update_status(
        self,
        id: UUID,
        status: OpportunityStatus,
    ) -> Optional[OpportunityModel]:
        """Update opportunity status.

        Args:
            id: Opportunity ID
            status: New status

        Returns:
            Updated opportunity if found
        """
        await self._session.execute(
            update(OpportunityModel)
            .where(OpportunityModel.id == id)
            .values(status=status)
        )
        return await self.get_by_id(id)

    async def expire_old_opportunities(
        self,
        hours: int = 1,
    ) -> int:
        """Mark old open opportunities as expired.

        Args:
            hours: Hours after which to expire

        Returns:
            Number of expired opportunities
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await self._session.execute(
            update(OpportunityModel)
            .where(
                OpportunityModel.status == OpportunityStatus.OPEN,
                OpportunityModel.detected_at < cutoff,
            )
            .values(status=OpportunityStatus.EXPIRED)
        )
        return result.rowcount

    async def get_stats(
        self,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """Get opportunity statistics.

        Args:
            hours: Hours to analyze

        Returns:
            Statistics dictionary
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        # Get counts by status
        result = await self._session.execute(
            select(
                OpportunityModel.status,
                func.count().label("count"),
                func.avg(OpportunityModel.roi).label("avg_roi"),
            )
            .where(OpportunityModel.detected_at >= cutoff)
            .group_by(OpportunityModel.status)
        )

        stats = {"total": 0, "by_status": {}}
        for row in result:
            stats["by_status"][row.status.value] = {
                "count": row.count,
                "avg_roi": float(row.avg_roi) if row.avg_roi else 0,
            }
            stats["total"] += row.count

        return stats


class OrderRepository(BaseRepository[OrderModel]):
    """Repository for order operations."""

    model_class = OrderModel

    async def get_by_external_id(
        self,
        platform: Platform,
        external_order_id: str,
    ) -> Optional[OrderModel]:
        """Get order by platform and external ID.

        Args:
            platform: Trading platform
            external_order_id: External order ID

        Returns:
            Order if found
        """
        result = await self._session.execute(
            select(OrderModel).where(
                OrderModel.platform == platform,
                OrderModel.external_order_id == external_order_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_open_orders(
        self,
        platform: Optional[Platform] = None,
        ticker: Optional[str] = None,
    ) -> Sequence[OrderModel]:
        """Get open orders with optional filters.

        Args:
            platform: Filter by platform
            ticker: Filter by ticker

        Returns:
            List of open orders
        """
        query = select(OrderModel).where(
            OrderModel.status.in_([
                OrderStatus.PENDING,
                OrderStatus.OPEN,
                OrderStatus.PARTIALLY_FILLED,
            ])
        )

        if platform:
            query = query.where(OrderModel.platform == platform)
        if ticker:
            query = query.where(OrderModel.ticker == ticker)

        query = query.order_by(OrderModel.created_at.desc())
        result = await self._session.execute(query)
        return result.scalars().all()

    async def get_orders_for_opportunity(
        self,
        opportunity_id: UUID,
    ) -> Sequence[OrderModel]:
        """Get orders for an opportunity.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            List of orders
        """
        result = await self._session.execute(
            select(OrderModel)
            .where(OrderModel.opportunity_id == opportunity_id)
            .options(selectinload(OrderModel.fills))
            .order_by(OrderModel.created_at.asc())
        )
        return result.scalars().all()

    async def update_fill(
        self,
        id: UUID,
        filled_size: int,
        status: Optional[OrderStatus] = None,
    ) -> Optional[OrderModel]:
        """Update order fill status.

        Args:
            id: Order ID
            filled_size: New filled size
            status: New status (auto-calculated if not provided)

        Returns:
            Updated order
        """
        order = await self.get_by_id(id)
        if not order:
            return None

        order.filled_size = filled_size

        # Auto-calculate status if not provided
        if status is None:
            if filled_size >= order.size:
                status = OrderStatus.FILLED
            elif filled_size > 0:
                status = OrderStatus.PARTIALLY_FILLED
            else:
                status = order.status

        order.status = status
        return await self.update(order)

    async def cancel_order(self, id: UUID) -> Optional[OrderModel]:
        """Cancel an order.

        Args:
            id: Order ID

        Returns:
            Canceled order
        """
        order = await self.get_by_id(id)
        if not order:
            return None

        order.status = OrderStatus.CANCELED
        return await self.update(order)


class TradeRepository(BaseRepository[TradeModel]):
    """Repository for trade operations."""

    model_class = TradeModel

    async def get_trades_for_opportunity(
        self,
        opportunity_id: UUID,
    ) -> Sequence[TradeModel]:
        """Get trades for an opportunity.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            List of trades
        """
        result = await self._session.execute(
            select(TradeModel)
            .where(TradeModel.opportunity_id == opportunity_id)
            .options(
                selectinload(TradeModel.kalshi_order),
                selectinload(TradeModel.polymarket_order),
            )
            .order_by(TradeModel.opened_at.asc())
        )
        return result.scalars().all()

    async def get_recent_trades(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[TradeModel]:
        """Get recent trades.

        Args:
            hours: Hours to look back
            limit: Maximum results

        Returns:
            List of trades
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await self._session.execute(
            select(TradeModel)
            .where(TradeModel.opened_at >= cutoff)
            .order_by(TradeModel.opened_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_profit_summary(
        self,
        hours: int = 24,
    ) -> Dict[str, Decimal]:
        """Get profit summary for period.

        Args:
            hours: Hours to analyze

        Returns:
            Summary with gross, fees, net profit
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await self._session.execute(
            select(
                func.sum(TradeModel.gross_profit).label("gross"),
                func.sum(TradeModel.fees).label("fees"),
                func.sum(TradeModel.net_profit).label("net"),
                func.count().label("count"),
            )
            .where(TradeModel.closed_at >= cutoff)
        )
        row = result.one()
        return {
            "gross_profit": row.gross or Decimal("0"),
            "fees": row.fees or Decimal("0"),
            "net_profit": row.net or Decimal("0"),
            "trade_count": row.count,
        }


class PositionRepository(BaseRepository[PositionModel]):
    """Repository for position operations."""

    model_class = PositionModel

    async def get_by_ticker(
        self,
        platform: Platform,
        ticker: str,
    ) -> Optional[PositionModel]:
        """Get position by platform and ticker.

        Args:
            platform: Trading platform
            ticker: Position ticker

        Returns:
            Position if found
        """
        result = await self._session.execute(
            select(PositionModel).where(
                PositionModel.platform == platform,
                PositionModel.ticker == ticker,
            )
        )
        return result.scalar_one_or_none()

    async def get_open_positions(
        self,
        platform: Optional[Platform] = None,
    ) -> Sequence[PositionModel]:
        """Get all open positions (non-zero size).

        Args:
            platform: Filter by platform

        Returns:
            List of open positions
        """
        query = select(PositionModel).where(PositionModel.size != 0)

        if platform:
            query = query.where(PositionModel.platform == platform)

        query = query.order_by(PositionModel.unrealized_pnl.desc())
        result = await self._session.execute(query)
        return result.scalars().all()

    async def upsert(self, position: PositionModel) -> PositionModel:
        """Insert or update a position.

        Args:
            position: Position to upsert

        Returns:
            Upserted position
        """
        existing = await self.get_by_ticker(position.platform, position.ticker)
        if existing:
            existing.size = position.size
            existing.entry_price = position.entry_price
            existing.current_price = position.current_price
            existing.unrealized_pnl = position.unrealized_pnl
            existing.realized_pnl = position.realized_pnl
            return await self.update(existing)
        return await self.create(position)

    async def update_price(
        self,
        platform: Platform,
        ticker: str,
        current_price: Decimal,
    ) -> Optional[PositionModel]:
        """Update position price and recalculate P&L.

        Args:
            platform: Trading platform
            ticker: Position ticker
            current_price: New current price

        Returns:
            Updated position
        """
        position = await self.get_by_ticker(platform, ticker)
        if not position or position.size == 0:
            return None

        position.current_price = current_price
        # Recalculate unrealized P&L
        price_diff = current_price - position.entry_price
        position.unrealized_pnl = price_diff * position.size / Decimal("100")
        return await self.update(position)

    async def get_total_exposure(
        self,
        platform: Optional[Platform] = None,
    ) -> Decimal:
        """Get total position exposure.

        Args:
            platform: Filter by platform

        Returns:
            Total exposure value
        """
        query = select(
            func.sum(func.abs(PositionModel.size) * PositionModel.current_price / 100)
        ).where(PositionModel.size != 0)

        if platform:
            query = query.where(PositionModel.platform == platform)

        result = await self._session.execute(query)
        return result.scalar_one() or Decimal("0")

    async def close_position(
        self,
        platform: Platform,
        ticker: str,
        realized_pnl: Decimal,
    ) -> Optional[PositionModel]:
        """Close a position.

        Args:
            platform: Trading platform
            ticker: Position ticker
            realized_pnl: Realized P&L from closing

        Returns:
            Closed position
        """
        position = await self.get_by_ticker(platform, ticker)
        if not position:
            return None

        position.realized_pnl += realized_pnl + position.unrealized_pnl
        position.size = 0
        position.unrealized_pnl = Decimal("0")
        return await self.update(position)


class FillRepository(BaseRepository[FillModel]):
    """Repository for fill operations."""

    model_class = FillModel

    async def get_fills_for_order(
        self,
        order_id: UUID,
    ) -> Sequence[FillModel]:
        """Get fills for an order.

        Args:
            order_id: Order ID

        Returns:
            List of fills
        """
        result = await self._session.execute(
            select(FillModel)
            .where(FillModel.order_id == order_id)
            .order_by(FillModel.filled_at.asc())
        )
        return result.scalars().all()

    async def get_recent_fills(
        self,
        platform: Optional[Platform] = None,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[FillModel]:
        """Get recent fills.

        Args:
            platform: Filter by platform
            hours: Hours to look back
            limit: Maximum results

        Returns:
            List of fills
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        query = select(FillModel).where(FillModel.filled_at >= cutoff)

        if platform:
            query = query.where(FillModel.platform == platform)

        query = query.order_by(FillModel.filled_at.desc()).limit(limit)
        result = await self._session.execute(query)
        return result.scalars().all()

    async def get_fill_summary(
        self,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """Get fill summary for period.

        Args:
            hours: Hours to analyze

        Returns:
            Summary with volume, fees, count
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await self._session.execute(
            select(
                FillModel.platform,
                func.count().label("count"),
                func.sum(FillModel.size).label("volume"),
                func.sum(FillModel.fee).label("fees"),
            )
            .where(FillModel.filled_at >= cutoff)
            .group_by(FillModel.platform)
        )

        summary = {"total": {"count": 0, "volume": 0, "fees": Decimal("0")}}
        for row in result:
            summary[row.platform.value] = {
                "count": row.count,
                "volume": row.volume or 0,
                "fees": row.fees or Decimal("0"),
            }
            summary["total"]["count"] += row.count
            summary["total"]["volume"] += row.volume or 0
            summary["total"]["fees"] += row.fees or Decimal("0")

        return summary


class BalanceRepository(BaseRepository[BalanceModel]):
    """Repository for balance operations."""

    model_class = BalanceModel

    async def get_latest(
        self,
        platform: Platform,
    ) -> Optional[BalanceModel]:
        """Get latest balance for platform.

        Args:
            platform: Trading platform

        Returns:
            Latest balance record
        """
        result = await self._session.execute(
            select(BalanceModel)
            .where(BalanceModel.platform == platform)
            .order_by(BalanceModel.recorded_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_history(
        self,
        platform: Platform,
        hours: int = 24,
    ) -> Sequence[BalanceModel]:
        """Get balance history for platform.

        Args:
            platform: Trading platform
            hours: Hours to look back

        Returns:
            List of balance records
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await self._session.execute(
            select(BalanceModel)
            .where(
                BalanceModel.platform == platform,
                BalanceModel.recorded_at >= cutoff,
            )
            .order_by(BalanceModel.recorded_at.asc())
        )
        return result.scalars().all()

    async def record_balance(
        self,
        platform: Platform,
        available: Decimal,
        reserved: Decimal,
    ) -> BalanceModel:
        """Record a new balance snapshot.

        Args:
            platform: Trading platform
            available: Available balance
            reserved: Reserved balance

        Returns:
            Created balance record
        """
        balance = BalanceModel(
            platform=platform,
            available=available,
            reserved=reserved,
            total=available + reserved,
        )
        return await self.create(balance)

    async def get_all_latest(self) -> Dict[Platform, BalanceModel]:
        """Get latest balance for all platforms.

        Returns:
            Dictionary of platform to latest balance
        """
        result = {}
        for platform in Platform:
            balance = await self.get_latest(platform)
            if balance:
                result[platform] = balance
        return result


class SystemEventRepository(BaseRepository[SystemEventModel]):
    """Repository for system event operations."""

    model_class = SystemEventModel

    async def log_event(
        self,
        event_type: str,
        message: str,
        severity: str = "INFO",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SystemEventModel:
        """Log a system event.

        Args:
            event_type: Event type identifier
            message: Event message
            severity: Event severity
            metadata: Additional metadata

        Returns:
            Created event
        """
        event = SystemEventModel(
            event_type=event_type,
            severity=severity,
            message=message,
            metadata_=metadata,
        )
        return await self.create(event)

    async def get_events(
        self,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[SystemEventModel]:
        """Get system events with filters.

        Args:
            event_type: Filter by event type
            severity: Filter by severity
            hours: Hours to look back
            limit: Maximum results

        Returns:
            List of events
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        query = select(SystemEventModel).where(
            SystemEventModel.created_at >= cutoff
        )

        if event_type:
            query = query.where(SystemEventModel.event_type == event_type)
        if severity:
            query = query.where(SystemEventModel.severity == severity)

        query = query.order_by(SystemEventModel.created_at.desc()).limit(limit)
        result = await self._session.execute(query)
        return result.scalars().all()

    async def get_errors(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[SystemEventModel]:
        """Get error and critical events.

        Args:
            hours: Hours to look back
            limit: Maximum results

        Returns:
            List of error events
        """
        return await self.get_events(
            severity="ERROR",
            hours=hours,
            limit=limit,
        )


class SpreadExecutionRepository(BaseRepository[SpreadExecutionModel]):
    """Repository for spread execution operations.

    Provides persistence for spread execution state, enabling
    crash recovery for multi-leg trades.
    """

    model_class = SpreadExecutionModel

    async def get_by_spread_id(
        self,
        spread_id: str,
    ) -> Optional[SpreadExecutionModel]:
        """Get spread execution by spread ID.

        Args:
            spread_id: Unique spread identifier

        Returns:
            SpreadExecutionModel if found
        """
        result = await self._session.execute(
            select(SpreadExecutionModel).where(
                SpreadExecutionModel.spread_id == spread_id
            )
        )
        return result.scalar_one_or_none()

    async def get_incomplete_executions(
        self,
        limit: int = 100,
    ) -> Sequence[SpreadExecutionModel]:
        """Get all incomplete spread executions.

        These are spreads that were interrupted and need recovery.

        Args:
            limit: Maximum results

        Returns:
            List of incomplete spread executions
        """
        result = await self._session.execute(
            select(SpreadExecutionModel)
            .where(
                SpreadExecutionModel.status.notin_([
                    SpreadExecutionStatus.COMPLETED,
                    SpreadExecutionStatus.FAILED,
                    SpreadExecutionStatus.ROLLED_BACK,
                ])
            )
            .order_by(SpreadExecutionModel.started_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_with_leg1_exposure(self) -> Sequence[SpreadExecutionModel]:
        """Get spreads with unhedged leg 1 exposure.

        These are the most critical for recovery - leg 1 filled
        but leg 2 not executed.

        Returns:
            List of spread executions with leg 1 exposure
        """
        result = await self._session.execute(
            select(SpreadExecutionModel)
            .where(
                SpreadExecutionModel.leg1_filled_size > 0,
                SpreadExecutionModel.leg2_filled_size == 0,
                SpreadExecutionModel.status.notin_([
                    SpreadExecutionStatus.COMPLETED,
                    SpreadExecutionStatus.FAILED,
                    SpreadExecutionStatus.ROLLED_BACK,
                ])
            )
            .order_by(SpreadExecutionModel.started_at.asc())
        )
        return result.scalars().all()

    async def update_status(
        self,
        spread_id: str,
        status: SpreadExecutionStatus,
        error_message: Optional[str] = None,
    ) -> Optional[SpreadExecutionModel]:
        """Update spread execution status.

        Args:
            spread_id: Spread identifier
            status: New status
            error_message: Optional error message

        Returns:
            Updated spread execution
        """
        values: Dict[str, Any] = {"status": status}
        if error_message:
            values["error_message"] = error_message
        if status in (
            SpreadExecutionStatus.COMPLETED,
            SpreadExecutionStatus.FAILED,
            SpreadExecutionStatus.ROLLED_BACK,
        ):
            values["completed_at"] = datetime.utcnow()

        await self._session.execute(
            update(SpreadExecutionModel)
            .where(SpreadExecutionModel.spread_id == spread_id)
            .values(**values)
        )
        return await self.get_by_spread_id(spread_id)

    async def update_leg1_fill(
        self,
        spread_id: str,
        order_id: str,
        filled_size: int,
        fill_price: Decimal,
    ) -> Optional[SpreadExecutionModel]:
        """Update leg 1 fill information.

        Args:
            spread_id: Spread identifier
            order_id: Exchange order ID
            filled_size: Filled size
            fill_price: Average fill price

        Returns:
            Updated spread execution
        """
        await self._session.execute(
            update(SpreadExecutionModel)
            .where(SpreadExecutionModel.spread_id == spread_id)
            .values(
                leg1_order_id=order_id,
                leg1_filled_size=filled_size,
                leg1_fill_price=fill_price,
                status=SpreadExecutionStatus.LEG1_FILLED,
            )
        )
        return await self.get_by_spread_id(spread_id)

    async def update_leg2_fill(
        self,
        spread_id: str,
        order_id: str,
        filled_size: int,
        fill_price: Decimal,
        actual_profit: Optional[Decimal] = None,
    ) -> Optional[SpreadExecutionModel]:
        """Update leg 2 fill information.

        Args:
            spread_id: Spread identifier
            order_id: Exchange order ID
            filled_size: Filled size
            fill_price: Average fill price
            actual_profit: Calculated actual profit

        Returns:
            Updated spread execution
        """
        values: Dict[str, Any] = {
            "leg2_order_id": order_id,
            "leg2_filled_size": filled_size,
            "leg2_fill_price": fill_price,
            "status": SpreadExecutionStatus.COMPLETED,
            "completed_at": datetime.utcnow(),
        }
        if actual_profit is not None:
            values["actual_profit"] = actual_profit

        await self._session.execute(
            update(SpreadExecutionModel)
            .where(SpreadExecutionModel.spread_id == spread_id)
            .values(**values)
        )
        return await self.get_by_spread_id(spread_id)

    async def update_rollback(
        self,
        spread_id: str,
        order_id: str,
        filled_size: int,
    ) -> Optional[SpreadExecutionModel]:
        """Update rollback information.

        Args:
            spread_id: Spread identifier
            order_id: Rollback order ID
            filled_size: Rollback filled size

        Returns:
            Updated spread execution
        """
        await self._session.execute(
            update(SpreadExecutionModel)
            .where(SpreadExecutionModel.spread_id == spread_id)
            .values(
                rollback_order_id=order_id,
                rollback_filled_size=filled_size,
                status=SpreadExecutionStatus.ROLLED_BACK,
                completed_at=datetime.utcnow(),
            )
        )
        return await self.get_by_spread_id(spread_id)

    async def increment_recovery_attempts(
        self,
        spread_id: str,
    ) -> Optional[SpreadExecutionModel]:
        """Increment recovery attempt counter.

        Args:
            spread_id: Spread identifier

        Returns:
            Updated spread execution
        """
        spread = await self.get_by_spread_id(spread_id)
        if spread:
            await self._session.execute(
                update(SpreadExecutionModel)
                .where(SpreadExecutionModel.spread_id == spread_id)
                .values(
                    recovery_attempts=spread.recovery_attempts + 1,
                    last_recovery_at=datetime.utcnow(),
                )
            )
            return await self.get_by_spread_id(spread_id)
        return None

    async def mark_recovery_needed(
        self,
        spread_id: str,
        error_message: str,
    ) -> Optional[SpreadExecutionModel]:
        """Mark spread as needing manual recovery.

        Args:
            spread_id: Spread identifier
            error_message: Description of why recovery failed

        Returns:
            Updated spread execution
        """
        return await self.update_status(
            spread_id,
            SpreadExecutionStatus.RECOVERY_NEEDED,
            error_message,
        )

    async def get_recent_executions(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> Sequence[SpreadExecutionModel]:
        """Get recent spread executions.

        Args:
            hours: Hours to look back
            limit: Maximum results

        Returns:
            List of spread executions
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        result = await self._session.execute(
            select(SpreadExecutionModel)
            .where(SpreadExecutionModel.started_at >= cutoff)
            .order_by(SpreadExecutionModel.started_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_execution_stats(
        self,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """Get spread execution statistics.

        Args:
            hours: Hours to analyze

        Returns:
            Statistics dictionary
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        result = await self._session.execute(
            select(
                SpreadExecutionModel.status,
                func.count().label("count"),
                func.sum(SpreadExecutionModel.actual_profit).label("total_profit"),
            )
            .where(SpreadExecutionModel.started_at >= cutoff)
            .group_by(SpreadExecutionModel.status)
        )

        stats: Dict[str, Any] = {"total": 0, "by_status": {}, "total_profit": Decimal("0")}
        for row in result:
            stats["by_status"][row.status.value] = {
                "count": row.count,
                "profit": float(row.total_profit) if row.total_profit else 0,
            }
            stats["total"] += row.count
            if row.total_profit:
                stats["total_profit"] += row.total_profit

        return stats
