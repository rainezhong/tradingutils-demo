"""
Spread Data Collector

Collects and stores spread pair quotes for later backtesting.

Usage:
------
# Collect data continuously
from arb.spread_collector import SpreadCollector

collector = SpreadCollector(kalshi_client, db_path="data/spreads.db")
collector.start(interval_seconds=60)  # Collect every minute

# Later, stop collection
collector.stop()

# Load collected data for backtesting
from arb.spread_collector import load_spread_history

history = load_spread_history("data/spreads.db", ticker_a="TICKER1", ticker_b="TICKER2")
for row in history:
    print(f"{row['timestamp']}: combined={row['combined_ask']:.2f}, edge={row['dutch_edge']:.4f}")
"""

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from src.core.trading_state import get_trading_state

from .kalshi_scanner import (
    KalshiSpreadScanner,
    ComplementaryPair,
    get_all_known_pairs,
    discover_complementary_pairs,
)


# SQL for spread pairs table
CREATE_SPREAD_PAIRS_TABLE = """
CREATE TABLE IF NOT EXISTS spread_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id TEXT NOT NULL,
    ticker_a TEXT NOT NULL,
    ticker_b TEXT NOT NULL,
    event_ticker TEXT,
    event_title TEXT,
    match_type TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(ticker_a, ticker_b)
)
"""

CREATE_SPREAD_SNAPSHOTS_TABLE = """
CREATE TABLE IF NOT EXISTS spread_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,

    -- Market A quotes
    a_yes_bid REAL,
    a_yes_ask REAL,
    a_no_bid REAL,
    a_no_ask REAL,

    -- Market B quotes
    b_yes_bid REAL,
    b_yes_ask REAL,
    b_no_bid REAL,
    b_no_ask REAL,

    -- Calculated fields
    combined_yes_ask REAL,
    combined_yes_bid REAL,
    dutch_edge REAL,
    routing_edge_a REAL,
    routing_edge_b REAL,

    FOREIGN KEY (pair_id) REFERENCES spread_pairs(pair_id)
)
"""

CREATE_SPREAD_INDICES = [
    "CREATE INDEX IF NOT EXISTS idx_spread_snapshots_pair ON spread_snapshots(pair_id)",
    "CREATE INDEX IF NOT EXISTS idx_spread_snapshots_timestamp ON spread_snapshots(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_spread_snapshots_pair_time ON spread_snapshots(pair_id, timestamp)",
]


class SpreadDatabase:
    """SQLite database for spread pair data."""

    def __init__(self, db_path: str = "data/spreads.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def _init_db(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute(CREATE_SPREAD_PAIRS_TABLE)
        cursor.execute(CREATE_SPREAD_SNAPSHOTS_TABLE)
        for idx in CREATE_SPREAD_INDICES:
            cursor.execute(idx)
        conn.commit()

    def upsert_pair(self, pair: ComplementaryPair) -> str:
        """Insert or update a spread pair. Returns pair_id."""
        conn = self._get_conn()
        cursor = conn.cursor()

        pair_id = f"{pair.market_a.ticker}:{pair.market_b.ticker}"

        cursor.execute("""
            INSERT INTO spread_pairs (pair_id, ticker_a, ticker_b, event_ticker, event_title, match_type)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker_a, ticker_b) DO UPDATE SET
                event_title = excluded.event_title,
                match_type = excluded.match_type
        """, (
            pair_id,
            pair.market_a.ticker,
            pair.market_b.ticker,
            pair.event_ticker,
            pair.event_title,
            pair.match_type,
        ))
        conn.commit()
        return pair_id

    def add_snapshot(self, pair: ComplementaryPair, timestamp: Optional[str] = None) -> int:
        """Store a spread snapshot. Returns snapshot ID."""
        conn = self._get_conn()
        cursor = conn.cursor()

        pair_id = f"{pair.market_a.ticker}:{pair.market_b.ticker}"
        ts = timestamp or datetime.now(timezone.utc).isoformat()

        # Calculate routing edges
        a_yes = pair.market_a.yes_ask or 0
        a_no = pair.market_a.no_ask or 0
        b_yes = pair.market_b.yes_ask or 0
        b_no = pair.market_b.no_ask or 0

        routing_a = a_yes - b_no if b_no else None  # A exposure cheaper via B NO?
        routing_b = b_yes - a_no if a_no else None  # B exposure cheaper via A NO?

        cursor.execute("""
            INSERT INTO spread_snapshots (
                pair_id, timestamp,
                a_yes_bid, a_yes_ask, a_no_bid, a_no_ask,
                b_yes_bid, b_yes_ask, b_no_bid, b_no_ask,
                combined_yes_ask, combined_yes_bid, dutch_edge,
                routing_edge_a, routing_edge_b
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pair_id, ts,
            pair.market_a.yes_bid, pair.market_a.yes_ask,
            pair.market_a.no_bid, pair.market_a.no_ask,
            pair.market_b.yes_bid, pair.market_b.yes_ask,
            pair.market_b.no_bid, pair.market_b.no_ask,
            pair.combined_yes_ask, pair.combined_yes_bid,
            pair.dutch_book_edge,
            routing_a, routing_b,
        ))
        conn.commit()
        return cursor.lastrowid

    def get_pairs(self) -> List[Dict[str, Any]]:
        """Get all tracked spread pairs."""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM spread_pairs ORDER BY created_at DESC")
        return [dict(row) for row in cursor.fetchall()]

    def get_history(
        self,
        pair_id: Optional[str] = None,
        ticker_a: Optional[str] = None,
        ticker_b: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: int = 10000,
    ) -> List[Dict[str, Any]]:
        """
        Get historical spread snapshots.

        Args:
            pair_id: Filter by pair ID (ticker_a:ticker_b)
            ticker_a: Filter by first ticker
            ticker_b: Filter by second ticker
            start_time: ISO timestamp start
            end_time: ISO timestamp end
            limit: Max rows to return

        Returns:
            List of snapshot dicts
        """
        conn = self._get_conn()
        cursor = conn.cursor()

        # Build query
        query = "SELECT * FROM spread_snapshots WHERE 1=1"
        params = []

        if pair_id:
            query += " AND pair_id = ?"
            params.append(pair_id)
        elif ticker_a and ticker_b:
            query += " AND pair_id = ?"
            params.append(f"{ticker_a}:{ticker_b}")

        if start_time:
            query += " AND timestamp >= ?"
            params.append(start_time)

        if end_time:
            query += " AND timestamp <= ?"
            params.append(end_time)

        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_summary(self) -> Dict[str, Any]:
        """Get database summary stats."""
        conn = self._get_conn()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as count FROM spread_pairs")
        num_pairs = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM spread_snapshots")
        num_snapshots = cursor.fetchone()["count"]

        cursor.execute("""
            SELECT MIN(timestamp) as first, MAX(timestamp) as last
            FROM spread_snapshots
        """)
        row = cursor.fetchone()

        return {
            "num_pairs": num_pairs,
            "num_snapshots": num_snapshots,
            "first_snapshot": row["first"],
            "last_snapshot": row["last"],
        }

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None


class SpreadCollector:
    """
    Continuous spread data collector.

    Periodically fetches quotes for spread pairs and stores them in the database.
    """

    def __init__(
        self,
        kalshi_client,
        db_path: str = "data/spreads.db",
        ticker_pairs: Optional[List[Tuple[str, str]]] = None,
        auto_discover: bool = False,
    ):
        """
        Initialize collector.

        Args:
            kalshi_client: Kalshi API client
            db_path: Path to SQLite database
            ticker_pairs: List of (ticker_a, ticker_b) to track, or None for known pairs
            auto_discover: If True, discover pairs from parlay markets
        """
        self.client = kalshi_client
        self.db = SpreadDatabase(db_path)
        self.scanner = KalshiSpreadScanner(kalshi_client)

        self._ticker_pairs = ticker_pairs
        self._auto_discover = auto_discover
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Stats
        self.snapshots_collected = 0
        self.errors = 0
        self.last_collection_time: Optional[datetime] = None

    def _get_pairs_to_track(self) -> List[Tuple[str, str]]:
        """Get ticker pairs to track."""
        if self._ticker_pairs:
            return self._ticker_pairs

        if self._auto_discover:
            return discover_complementary_pairs(self.client, max_pages=3)

        return get_all_known_pairs()

    def collect_once(self) -> int:
        """
        Collect one round of snapshots.

        Returns:
            Number of snapshots collected
        """
        ticker_pairs = self._get_pairs_to_track()
        pairs = self.scanner.scan_known_pairs(ticker_pairs, delay_seconds=2.0)

        count = 0
        for pair in pairs:
            if pair.combined_yes_ask is not None:
                try:
                    self.db.upsert_pair(pair)
                    self.db.add_snapshot(pair)
                    count += 1
                except Exception as e:
                    self.errors += 1
                    print(f"Error storing snapshot: {e}")

        self.snapshots_collected += count
        self.last_collection_time = datetime.now(timezone.utc)
        return count

    def _run_loop(self, interval_seconds: float):
        """Background collection loop."""
        trading_state = get_trading_state()

        while not self._stop_event.is_set():
            # Pause if trading is active to avoid competing for rate limits
            if trading_state.should_pause():
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] Pausing collection: trading is active")
                trading_state.wait_while_paused(timeout=5.0)
                continue

            try:
                count = self.collect_once()
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] Collected {count} snapshots (total: {self.snapshots_collected})")
            except Exception as e:
                self.errors += 1
                print(f"Collection error: {e}")

            # Wait for next interval (interruptible)
            self._stop_event.wait(interval_seconds)

    def start(self, interval_seconds: float = 60.0):
        """
        Start continuous collection in background.

        Args:
            interval_seconds: Seconds between collection rounds
        """
        if self._thread is not None and self._thread.is_alive():
            print("Collector already running")
            return self

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval_seconds,),
            daemon=True,
        )
        self._thread.start()
        print(f"Started spread collector (interval: {interval_seconds}s)")
        return self

    def stop(self):
        """Stop collection."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        print(f"Stopped. Collected {self.snapshots_collected} snapshots, {self.errors} errors")

    def get_stats(self) -> Dict[str, Any]:
        """Get collector statistics."""
        db_stats = self.db.get_summary()
        return {
            **db_stats,
            "snapshots_this_session": self.snapshots_collected,
            "errors_this_session": self.errors,
            "last_collection": self.last_collection_time.isoformat() if self.last_collection_time else None,
        }


# Convenience functions

def load_spread_history(
    db_path: str = "data/spreads.db",
    ticker_a: Optional[str] = None,
    ticker_b: Optional[str] = None,
    pair_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    limit: int = 10000,
) -> List[Dict[str, Any]]:
    """
    Load spread history from database.

    Args:
        db_path: Path to database
        ticker_a: First ticker
        ticker_b: Second ticker
        pair_id: Or specify pair_id directly (ticker_a:ticker_b)
        start_time: ISO timestamp start
        end_time: ISO timestamp end
        limit: Max rows

    Returns:
        List of snapshot dicts with fields:
        - timestamp, a_yes_bid, a_yes_ask, b_yes_bid, b_yes_ask,
        - combined_yes_ask, dutch_edge, routing_edge_a, routing_edge_b
    """
    db = SpreadDatabase(db_path)
    try:
        return db.get_history(
            pair_id=pair_id,
            ticker_a=ticker_a,
            ticker_b=ticker_b,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
    finally:
        db.close()


def list_collected_pairs(db_path: str = "data/spreads.db") -> List[Dict[str, Any]]:
    """List all pairs in the database."""
    db = SpreadDatabase(db_path)
    try:
        return db.get_pairs()
    finally:
        db.close()


def get_collection_stats(db_path: str = "data/spreads.db") -> Dict[str, Any]:
    """Get database statistics."""
    db = SpreadDatabase(db_path)
    try:
        return db.get_summary()
    finally:
        db.close()
