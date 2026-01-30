"""SQLite database operations for market data storage."""

import sqlite3
from pathlib import Path
from typing import Optional

from .config import get_config
from .models import Market, Snapshot, SummaryStats
from .utils import get_db_connection, setup_logger, utc_now_iso

logger = setup_logger(__name__)


# SQL statements for table creation
CREATE_MARKETS_TABLE = """
CREATE TABLE IF NOT EXISTS markets (
    ticker TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT,
    close_time TEXT,
    status TEXT,
    volume_24h INTEGER,
    open_interest INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    yes_bid INTEGER,
    yes_ask INTEGER,
    spread_cents INTEGER,
    spread_pct REAL,
    mid_price REAL,
    volume_24h INTEGER,
    open_interest INTEGER,
    orderbook_bid_depth INTEGER,
    orderbook_ask_depth INTEGER,
    FOREIGN KEY (ticker) REFERENCES markets(ticker)
)
"""

CREATE_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_snapshots_ticker ON snapshots(ticker)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON snapshots(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_timestamp ON snapshots(ticker, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_markets_status ON markets(status)",
]


class MarketDatabase:
    """Manages SQLite database for market data and snapshots."""

    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file. Uses config default if not provided.
        """
        config = get_config()
        self.db_path = Path(db_path or config.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def init_db(self) -> None:
        """Create tables and indices if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute(CREATE_MARKETS_TABLE)
        cursor.execute(CREATE_SNAPSHOTS_TABLE)

        for index_sql in CREATE_INDICES:
            cursor.execute(index_sql)

        # Migration: Add missing columns for older databases
        cursor.execute("PRAGMA table_info(markets)")
        columns = {row[1] for row in cursor.fetchall()}

        migrations = [
            ("open_interest", "INTEGER"),
            ("created_at", "TEXT"),
            ("updated_at", "TEXT"),
            ("category", "TEXT"),
            ("close_time", "TEXT"),
            ("status", "TEXT"),
            ("volume_24h", "INTEGER"),
        ]

        for col_name, col_type in migrations:
            if col_name not in columns:
                cursor.execute(f"ALTER TABLE markets ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added missing '{col_name}' column to markets table")

        # Migration: Add missing columns for snapshots table
        cursor.execute("PRAGMA table_info(snapshots)")
        snapshot_columns = {row[1] for row in cursor.fetchall()}

        snapshot_migrations = [
            ("open_interest", "INTEGER"),
            ("orderbook_bid_depth", "INTEGER"),
            ("orderbook_ask_depth", "INTEGER"),
            ("volume_24h", "INTEGER"),
            ("spread_pct", "REAL"),
            ("mid_price", "REAL"),
        ]

        for col_name, col_type in snapshot_migrations:
            if col_name not in snapshot_columns:
                cursor.execute(f"ALTER TABLE snapshots ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added missing '{col_name}' column to snapshots table")

        conn.commit()
        logger.info(f"Database initialized at {self.db_path}")

    def upsert_market(self, market: Market) -> None:
        """
        Insert or update a market record.

        Args:
            market: Market instance to store
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO markets (
                ticker, title, category, close_time, status,
                volume_24h, open_interest, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                title = excluded.title,
                category = excluded.category,
                close_time = excluded.close_time,
                status = excluded.status,
                volume_24h = excluded.volume_24h,
                open_interest = excluded.open_interest,
                updated_at = excluded.updated_at
        """, (
            market.ticker,
            market.title,
            market.category,
            market.close_time,
            market.status,
            market.volume_24h,
            market.open_interest,
            market.created_at,
            utc_now_iso(),
        ))

        conn.commit()

    def get_market(self, ticker: str) -> Optional[Market]:
        """
        Get a market by ticker.

        Args:
            ticker: Market ticker

        Returns:
            Market instance or None if not found
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM markets WHERE ticker = ?", (ticker,))
        row = cursor.fetchone()

        if row:
            return Market(
                ticker=row["ticker"],
                title=row["title"],
                category=row["category"],
                close_time=row["close_time"],
                status=row["status"],
                volume_24h=row["volume_24h"],
                open_interest=row["open_interest"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        return None

    def get_active_markets(self) -> list[Market]:
        """
        Get all markets with status='open'.

        Returns:
            List of Market instances
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM markets WHERE status = 'open'")
        rows = cursor.fetchall()

        return [
            Market(
                ticker=row["ticker"],
                title=row["title"],
                category=row["category"],
                close_time=row["close_time"],
                status=row["status"],
                volume_24h=row["volume_24h"],
                open_interest=row["open_interest"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def get_all_markets(self) -> list[Market]:
        """
        Get all markets regardless of status.

        Returns:
            List of Market instances
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM markets")
        rows = cursor.fetchall()

        return [
            Market(
                ticker=row["ticker"],
                title=row["title"],
                category=row["category"],
                close_time=row["close_time"],
                status=row["status"],
                volume_24h=row["volume_24h"],
                open_interest=row["open_interest"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def add_snapshot(self, snapshot: Snapshot) -> int:
        """
        Store a market snapshot.

        Args:
            snapshot: Snapshot instance to store

        Returns:
            ID of inserted row
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO snapshots (
                ticker, timestamp, yes_bid, yes_ask, spread_cents,
                spread_pct, mid_price, volume_24h, open_interest,
                orderbook_bid_depth, orderbook_ask_depth
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snapshot.ticker,
            snapshot.timestamp,
            snapshot.yes_bid,
            snapshot.yes_ask,
            snapshot.spread_cents,
            snapshot.spread_pct,
            snapshot.mid_price,
            snapshot.volume_24h,
            snapshot.open_interest,
            snapshot.orderbook_bid_depth,
            snapshot.orderbook_ask_depth,
        ))

        conn.commit()
        return cursor.lastrowid

    def get_snapshots(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
    ) -> list[Snapshot]:
        """
        Get snapshots, optionally filtered by ticker.

        Args:
            ticker: Filter by market ticker
            limit: Maximum number of results

        Returns:
            List of Snapshot instances
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        if ticker:
            cursor.execute(
                "SELECT * FROM snapshots WHERE ticker = ? ORDER BY timestamp DESC LIMIT ?",
                (ticker, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM snapshots ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )

        rows = cursor.fetchall()

        return [
            Snapshot(
                id=row["id"],
                ticker=row["ticker"],
                timestamp=row["timestamp"],
                yes_bid=row["yes_bid"],
                yes_ask=row["yes_ask"],
                spread_cents=row["spread_cents"],
                spread_pct=row["spread_pct"],
                mid_price=row["mid_price"],
                volume_24h=row["volume_24h"],
                open_interest=row["open_interest"],
                orderbook_bid_depth=row["orderbook_bid_depth"],
                orderbook_ask_depth=row["orderbook_ask_depth"],
            )
            for row in rows
        ]

    def get_summary_stats(self) -> SummaryStats:
        """
        Get aggregate statistics from database.

        Returns:
            SummaryStats instance
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as count FROM markets")
        total_markets = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM snapshots")
        total_snapshots = cursor.fetchone()["count"]

        cursor.execute("""
            SELECT
                AVG(spread_cents) as avg_spread_cents,
                AVG(spread_pct) as avg_spread_pct,
                MIN(spread_cents) as min_spread,
                MAX(spread_cents) as max_spread
            FROM snapshots
            WHERE spread_cents IS NOT NULL
        """)
        row = cursor.fetchone()

        return SummaryStats(
            total_markets=total_markets,
            total_snapshots=total_snapshots,
            avg_spread_cents=row["avg_spread_cents"],
            avg_spread_pct=row["avg_spread_pct"],
            min_spread=row["min_spread"],
            max_spread=row["max_spread"],
        )

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
            logger.debug("Database connection closed")


def create_database(db_path: Optional[str] = None) -> MarketDatabase:
    """
    Create and initialize a new database.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Initialized MarketDatabase instance
    """
    db = MarketDatabase(db_path)
    db.init_db()
    return db
