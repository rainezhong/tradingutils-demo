"""Database layer with SQLAlchemy ORM and Redis caching.

This module provides:
- SQLAlchemy ORM models for markets, opportunities, orders, trades
- Repository pattern for database operations
- Redis caching for real-time data
- Async database connection management
"""

from src.database.connection import DatabaseManager, get_session
from src.database.models import (
    Base,
    BalanceModel,
    FillModel,
    MarketModel,
    OpportunityModel,
    OrderModel,
    Platform,
    PositionModel,
    SystemEventModel,
    TradeModel,
)
from src.database.repository import (
    BalanceRepository,
    FillRepository,
    MarketRepository,
    OpportunityRepository,
    OrderRepository,
    PositionRepository,
    TradeRepository,
)
from src.database.cache import MarketCache

__all__ = [
    # Connection
    "DatabaseManager",
    "get_session",
    # Models
    "Base",
    "Platform",
    "MarketModel",
    "OpportunityModel",
    "OrderModel",
    "TradeModel",
    "PositionModel",
    "FillModel",
    "BalanceModel",
    "SystemEventModel",
    # Repositories
    "MarketRepository",
    "OpportunityRepository",
    "OrderRepository",
    "TradeRepository",
    "PositionRepository",
    "FillRepository",
    "BalanceRepository",
    # Cache
    "MarketCache",
]
