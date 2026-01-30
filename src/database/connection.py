"""Database connection management with async SQLAlchemy 2.0.

Provides connection pooling, session management, and transaction support.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.database.models import Base

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages database connections and sessions.

    Provides async connection pooling, session factory, and lifecycle management.

    Example:
        >>> db = DatabaseManager("postgresql+asyncpg://user:pass@localhost/db")
        >>> await db.initialize()
        >>> async with db.session() as session:
        ...     # Use session for queries
        ...     pass
        >>> await db.close()
    """

    def __init__(
        self,
        url: Optional[str] = None,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_timeout: float = 30.0,
        pool_recycle: int = 3600,
        echo: bool = False,
    ) -> None:
        """Initialize database manager.

        Args:
            url: Database URL (defaults to DATABASE_URL env var)
            pool_size: Number of persistent connections
            max_overflow: Max temporary connections above pool_size
            pool_timeout: Seconds to wait for connection from pool
            pool_recycle: Seconds before recycling a connection
            echo: Whether to log SQL statements
        """
        self._url = url or os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/tradingutils",
        )
        self._pool_size = pool_size
        self._max_overflow = max_overflow
        self._pool_timeout = pool_timeout
        self._pool_recycle = pool_recycle
        self._echo = echo

        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._initialized = False

    @property
    def engine(self) -> AsyncEngine:
        """Get the database engine."""
        if self._engine is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Get the session factory."""
        if self._session_factory is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")
        return self._session_factory

    async def initialize(self) -> None:
        """Initialize the database engine and session factory.

        Creates the connection pool and prepares the session factory.
        Should be called once at application startup.
        """
        if self._initialized:
            logger.warning("DatabaseManager already initialized")
            return

        logger.info("Initializing database connection to %s", self._url.split("@")[-1])

        # Create async engine with connection pooling
        self._engine = create_async_engine(
            self._url,
            pool_size=self._pool_size,
            max_overflow=self._max_overflow,
            pool_timeout=self._pool_timeout,
            pool_recycle=self._pool_recycle,
            echo=self._echo,
        )

        # Create session factory
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

        self._initialized = True
        logger.info("Database connection initialized successfully")

    async def close(self) -> None:
        """Close the database engine and release all connections.

        Should be called at application shutdown.
        """
        if self._engine is not None:
            logger.info("Closing database connections")
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._initialized = False

    async def create_tables(self) -> None:
        """Create all database tables.

        Uses SQLAlchemy metadata to create tables if they don't exist.
        For production, use Alembic migrations instead.
        """
        if not self._initialized:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        logger.info("Creating database tables")
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created successfully")

    async def drop_tables(self) -> None:
        """Drop all database tables.

        WARNING: This will delete all data. Use with caution.
        """
        if not self._initialized:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        logger.warning("Dropping all database tables")
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Database tables dropped")

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a database session with automatic cleanup.

        Yields a session that will be automatically closed after use.
        If an exception occurs, the session is rolled back.

        Yields:
            AsyncSession for database operations

        Example:
            >>> async with db.session() as session:
            ...     result = await session.execute(select(MarketModel))
            ...     markets = result.scalars().all()
        """
        if self._session_factory is None:
            raise RuntimeError("DatabaseManager not initialized. Call initialize() first.")

        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a session with explicit transaction control.

        Same as session() but provides clearer semantics for
        operations that require transaction management.

        Yields:
            AsyncSession with active transaction
        """
        async with self.session() as session:
            yield session

    async def health_check(self) -> bool:
        """Check database connectivity.

        Returns:
            True if database is reachable, False otherwise
        """
        if not self._initialized:
            return False

        try:
            async with self.session() as session:
                await session.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error("Database health check failed: %s", e)
            return False


# Global database manager instance
_db_manager: Optional[DatabaseManager] = None


def get_database_manager() -> DatabaseManager:
    """Get or create the global database manager.

    Returns:
        The global DatabaseManager instance
    """
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting database sessions.

    Intended for use with dependency injection in web frameworks.

    Yields:
        AsyncSession for database operations

    Example (FastAPI):
        >>> @app.get("/markets")
        ... async def get_markets(session: AsyncSession = Depends(get_session)):
        ...     return await session.execute(select(MarketModel))
    """
    db = get_database_manager()
    async with db.session() as session:
        yield session


def create_test_engine(database_url: Optional[str] = None) -> AsyncEngine:
    """Create an engine for testing with no connection pooling.

    Args:
        database_url: Test database URL (defaults to TEST_DATABASE_URL env var)

    Returns:
        AsyncEngine configured for testing
    """
    url = database_url or os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/tradingutils_test",
    )
    return create_async_engine(
        url,
        poolclass=NullPool,
        echo=False,
    )


class TestDatabaseManager(DatabaseManager):
    """Database manager for testing with isolated transactions.

    Each test gets a fresh transaction that is rolled back after the test,
    ensuring test isolation without needing to recreate tables.
    """

    def __init__(self, url: Optional[str] = None) -> None:
        """Initialize test database manager."""
        test_url = url or os.getenv(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/tradingutils_test",
        )
        super().__init__(
            url=test_url,
            pool_size=1,
            max_overflow=0,
            echo=False,
        )

    @asynccontextmanager
    async def test_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a test session that will be rolled back.

        All operations in the session are rolled back after the context exits,
        providing test isolation.

        Yields:
            AsyncSession for testing
        """
        if self._session_factory is None:
            raise RuntimeError("TestDatabaseManager not initialized")

        session = self._session_factory()
        try:
            yield session
            # Always rollback to ensure isolation
            await session.rollback()
        finally:
            await session.close()
