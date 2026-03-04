"""SQLite recorder for latency probe data.

Manages shared schema tables (kalshi_snapshots, truth_readings,
market_settlements) plus source-specific extension tables.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .truth_source import TruthReading

logger = logging.getLogger(__name__)

BATCH_SIZE = 20  # Commit every N rows


class ProbeRecorder:
    """Records Kalshi snapshots, truth readings, and settlements to SQLite."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._pending = 0
        self._init_shared_tables()

    def _init_shared_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS kalshi_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                ticker TEXT NOT NULL,
                yes_bid INTEGER,
                yes_ask INTEGER,
                yes_mid REAL,
                strike REAL,
                close_time TEXT,
                seconds_to_close REAL,
                volume INTEGER,
                open_interest INTEGER
            );
            CREATE TABLE IF NOT EXISTS truth_readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                ticker TEXT NOT NULL,
                probability REAL NOT NULL,
                raw_value REAL,
                confidence REAL,
                metadata_json TEXT
            );
            CREATE TABLE IF NOT EXISTS market_settlements (
                ticker TEXT PRIMARY KEY,
                close_time TEXT NOT NULL,
                settled_yes INTEGER,
                expiration_value REAL,
                truth_probability_at_settle REAL,
                truth_predicted_yes INTEGER,
                kalshi_last_mid REAL,
                kalshi_predicted_yes INTEGER,
                truth_was_right INTEGER,
                kalshi_was_right INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_kalshi_snap_ts
                ON kalshi_snapshots(ts);
            CREATE INDEX IF NOT EXISTS idx_kalshi_snap_ticker_ts
                ON kalshi_snapshots(ticker, ts);
            CREATE INDEX IF NOT EXISTS idx_truth_ts
                ON truth_readings(ts);
            CREATE INDEX IF NOT EXISTS idx_truth_ticker_ts
                ON truth_readings(ticker, ts);
        """)
        self._conn.commit()

    def register_tables(self, sql: str) -> None:
        """Register source-specific extension tables.

        Args:
            sql: CREATE TABLE / CREATE INDEX statements
        """
        self._conn.executescript(sql)
        self._conn.commit()

    def record_kalshi_snapshot(
        self,
        ts: float,
        ticker: str,
        yes_bid: int,
        yes_ask: int,
        yes_mid: float,
        strike: Optional[float],
        close_time: Optional[str],
        seconds_to_close: Optional[float],
        volume: int = 0,
        open_interest: int = 0,
    ) -> None:
        self._conn.execute(
            "INSERT INTO kalshi_snapshots "
            "(ts, ticker, yes_bid, yes_ask, yes_mid, strike, close_time, "
            "seconds_to_close, volume, open_interest) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, ticker, yes_bid, yes_ask, yes_mid, strike, close_time,
             seconds_to_close, volume, open_interest),
        )
        self._maybe_commit()

    def record_truth_reading(
        self,
        ts: float,
        ticker: str,
        reading: TruthReading,
    ) -> None:
        meta_json = json.dumps(reading.metadata) if reading.metadata else None
        self._conn.execute(
            "INSERT INTO truth_readings "
            "(ts, ticker, probability, raw_value, confidence, metadata_json) "
            "VALUES (?,?,?,?,?,?)",
            (ts, ticker, reading.probability, reading.raw_value,
             reading.confidence, meta_json),
        )
        self._maybe_commit()

    def record_settlement(
        self,
        ticker: str,
        close_time: str,
        settled_yes: Optional[int],
        expiration_value: Optional[float],
        truth_prob: Optional[float],
        truth_predicted_yes: Optional[int],
        kalshi_last_mid: Optional[float],
        kalshi_predicted_yes: Optional[int],
        truth_was_right: Optional[int],
        kalshi_was_right: Optional[int],
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO market_settlements "
            "(ticker, close_time, settled_yes, expiration_value, "
            "truth_probability_at_settle, truth_predicted_yes, "
            "kalshi_last_mid, kalshi_predicted_yes, "
            "truth_was_right, kalshi_was_right) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ticker, close_time, settled_yes, expiration_value,
             truth_prob, truth_predicted_yes,
             kalshi_last_mid, kalshi_predicted_yes,
             truth_was_right, kalshi_was_right),
        )
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Run an arbitrary SQL statement (for extension table writes)."""
        cursor = self._conn.execute(sql, params)
        self._maybe_commit()
        return cursor

    def get_last_kalshi_mid(self, ticker: str) -> Optional[float]:
        """Get the last recorded yes_mid for a ticker."""
        row = self._conn.execute(
            "SELECT yes_mid FROM kalshi_snapshots "
            "WHERE ticker=? ORDER BY ts DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        return row[0] if row else None

    def flush(self) -> None:
        """Force commit any pending writes."""
        self._conn.commit()
        self._pending = 0

    def close(self) -> None:
        self.flush()
        self._conn.close()

    @property
    def connection(self) -> sqlite3.Connection:
        """Direct access for analyzer queries."""
        return self._conn

    def _maybe_commit(self) -> None:
        self._pending += 1
        if self._pending >= BATCH_SIZE:
            self._conn.commit()
            self._pending = 0
