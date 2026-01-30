"""SQLite database operations for market data storage."""

import sqlite3
from pathlib import Path
from typing import Optional


class MarketDatabase:
    """Manages SQLite database for market data and snapshots."""

    def __init__(self, db_path: str = "data/markets.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def _get_connection(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def init_db(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS markets (
                ticker TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT,
                close_time TEXT,
                status TEXT,
                volume_24h INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
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
                FOREIGN KEY (ticker) REFERENCES markets(ticker)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_ticker
            ON snapshots(ticker)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
            ON snapshots(timestamp)
        """)

        conn.commit()

    def upsert_market(self, market: dict) -> None:
        """Insert or update a market record."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO markets (ticker, title, category, close_time, status, volume_24h)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                title = excluded.title,
                category = excluded.category,
                close_time = excluded.close_time,
                status = excluded.status,
                volume_24h = excluded.volume_24h
        """, (
            market.get("ticker"),
            market.get("title"),
            market.get("category"),
            market.get("close_time"),
            market.get("status"),
            market.get("volume_24h"),
        ))

        conn.commit()

    def get_active_markets(self) -> list[dict]:
        """Get all tracked markets."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM markets WHERE status = 'open'")
        rows = cursor.fetchall()

        return [dict(row) for row in rows]

    def add_snapshot(self, snap: dict) -> None:
        """Store a market snapshot."""
        conn = self._get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO snapshots
            (ticker, timestamp, yes_bid, yes_ask, spread_cents, spread_pct, mid_price, volume_24h)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            snap.get("ticker"),
            snap.get("timestamp"),
            snap.get("yes_bid"),
            snap.get("yes_ask"),
            snap.get("spread_cents"),
            snap.get("spread_pct"),
            snap.get("mid_price"),
            snap.get("volume_24h"),
        ))

        conn.commit()

    def get_summary_stats(self) -> dict:
        """Get aggregate statistics from database."""
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
        spread_stats = cursor.fetchone()

        return {
            "total_markets": total_markets,
            "total_snapshots": total_snapshots,
            "avg_spread_cents": spread_stats["avg_spread_cents"],
            "avg_spread_pct": spread_stats["avg_spread_pct"],
            "min_spread": spread_stats["min_spread"],
            "max_spread": spread_stats["max_spread"],
        }

    def close(self) -> None:
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None
