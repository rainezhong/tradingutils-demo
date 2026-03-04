"""
Smart Spread Data Collector

Optimized for spread trading backtesting:
1. Focuses on high-value pairs (tight spreads, high volume)
2. Collects at game-relevant times (not 3am)
3. Increases frequency when spreads are tight
4. Stores only what matters for backtesting

Usage:
------
# Quick start
python -m arb.smart_collector

# Or from code
from arb.smart_collector import SmartCollector
collector = SmartCollector(kalshi_client)
collector.run(duration_hours=4)
"""

import sqlite3
import time
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Any
from dataclasses import dataclass

from core.trading_state import get_trading_state


@dataclass
class SpreadSnapshot:
    """Minimal snapshot for spread backtesting."""

    timestamp: str
    pair_id: str
    a_yes_bid: float
    a_yes_ask: float
    b_yes_bid: float
    b_yes_ask: float
    combined_ask: float
    edge: float  # 1.0 - combined_ask (positive = opportunity)

    @property
    def is_opportunity(self) -> bool:
        return self.edge > 0


class SmartSpreadDB:
    """Lightweight database optimized for spread data."""

    def __init__(self, db_path: str = "data/smart_spreads.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                pair_id TEXT NOT NULL,
                a_bid REAL, a_ask REAL,
                b_bid REAL, b_ask REAL,
                combined REAL,
                edge REAL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pair_time ON snapshots(pair_id, timestamp)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_edge ON snapshots(edge)")

        # Opportunities table - only stores when edge > 0
        conn.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY,
                pair_id TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                peak_edge REAL,
                snapshots INTEGER DEFAULT 1
            )
        """)
        conn.commit()
        conn.close()

    def add_snapshot(self, snap: SpreadSnapshot) -> int:
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            """
            INSERT INTO snapshots (timestamp, pair_id, a_bid, a_ask, b_bid, b_ask, combined, edge)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                snap.timestamp,
                snap.pair_id,
                snap.a_yes_bid,
                snap.a_yes_ask,
                snap.b_yes_bid,
                snap.b_yes_ask,
                snap.combined_ask,
                snap.edge,
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
        conn.close()
        return row_id

    def record_opportunity(self, pair_id: str, edge: float, timestamp: str):
        """Track opportunity windows."""
        conn = sqlite3.connect(self.db_path)

        # Check if there's an open opportunity for this pair
        cur = conn.execute(
            """
            SELECT id, peak_edge, snapshots FROM opportunities
            WHERE pair_id = ? AND end_time IS NULL
            ORDER BY start_time DESC LIMIT 1
        """,
            (pair_id,),
        )
        row = cur.fetchone()

        if row:
            # Update existing opportunity
            opp_id, peak, snaps = row
            conn.execute(
                """
                UPDATE opportunities
                SET peak_edge = MAX(peak_edge, ?), snapshots = snapshots + 1
                WHERE id = ?
            """,
                (edge, opp_id),
            )
        else:
            # New opportunity
            conn.execute(
                """
                INSERT INTO opportunities (pair_id, start_time, peak_edge)
                VALUES (?, ?, ?)
            """,
                (pair_id, timestamp, edge),
            )

        conn.commit()
        conn.close()

    def close_opportunity(self, pair_id: str, timestamp: str):
        """Close an opportunity window."""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """
            UPDATE opportunities SET end_time = ?
            WHERE pair_id = ? AND end_time IS NULL
        """,
            (timestamp, pair_id),
        )
        conn.commit()
        conn.close()

    def get_stats(self) -> Dict[str, Any]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        stats = {}
        stats["total_snapshots"] = conn.execute(
            "SELECT COUNT(*) FROM snapshots"
        ).fetchone()[0]
        stats["total_opportunities"] = conn.execute(
            "SELECT COUNT(*) FROM opportunities"
        ).fetchone()[0]

        row = conn.execute("""
            SELECT MIN(timestamp) as first, MAX(timestamp) as last,
                   AVG(edge) as avg_edge, MAX(edge) as max_edge
            FROM snapshots
        """).fetchone()
        stats["first_snapshot"] = row["first"]
        stats["last_snapshot"] = row["last"]
        stats["avg_edge"] = row["avg_edge"]
        stats["max_edge"] = row["max_edge"]

        # Positive edge count
        stats["positive_edge_snapshots"] = conn.execute(
            "SELECT COUNT(*) FROM snapshots WHERE edge > 0"
        ).fetchone()[0]

        conn.close()
        return stats


class SmartCollector:
    """
    Intelligent spread data collector.

    Strategy:
    - Poll faster when spreads are tight (edge near 0 or positive)
    - Poll slower when spreads are wide (no opportunity)
    - Focus on pairs with historical opportunities
    - Skip pairs with no liquidity
    """

    # Polling intervals based on edge
    FAST_INTERVAL = 5  # When edge > -0.01 (within 1%)
    NORMAL_INTERVAL = 15  # When edge > -0.03 (within 3%)
    SLOW_INTERVAL = 60  # When edge < -0.03 (wide spread)

    def __init__(self, kalshi_client, db_path: str = "data/smart_spreads.db"):
        self.client = kalshi_client
        self.db = SmartSpreadDB(db_path)

        # State
        self._pair_edges: Dict[str, float] = {}  # Last known edge per pair
        self._pair_last_poll: Dict[str, float] = {}  # Last poll time
        self._stop_event = threading.Event()

        # Stats
        self.snapshots_collected = 0
        self.opportunities_found = 0
        self.api_calls = 0

    def _get_api(self):
        """Get underlying API client."""
        return self.client._api if hasattr(self.client, "_api") else self.client

    def _fetch_pair(self, ticker_a: str, ticker_b: str) -> Optional[SpreadSnapshot]:
        """Fetch quotes for a pair."""
        api = self._get_api()

        try:
            self.api_calls += 1
            m1 = api.get_market(ticker_a).get("market", {})
            time.sleep(0.15)  # Rate limit

            self.api_calls += 1
            m2 = api.get_market(ticker_b).get("market", {})

            def to_dollars(v):
                if v is None:
                    return 0.0
                return v / 100.0 if v > 1 else float(v)

            a_bid = to_dollars(m1.get("yes_bid"))
            a_ask = to_dollars(m1.get("yes_ask"))
            b_bid = to_dollars(m2.get("yes_bid"))
            b_ask = to_dollars(m2.get("yes_ask"))

            # Skip if no liquidity
            if a_ask == 0 or b_ask == 0:
                return None

            combined = a_ask + b_ask
            edge = 1.0 - combined

            return SpreadSnapshot(
                timestamp=datetime.now(timezone.utc).isoformat(),
                pair_id=f"{ticker_a}:{ticker_b}",
                a_yes_bid=a_bid,
                a_yes_ask=a_ask,
                b_yes_bid=b_bid,
                b_yes_ask=b_ask,
                combined_ask=combined,
                edge=edge,
            )
        except Exception:
            return None

    def _should_poll(self, pair_id: str) -> bool:
        """Determine if we should poll this pair now."""
        now = time.time()
        last_poll = self._pair_last_poll.get(pair_id, 0)
        last_edge = self._pair_edges.get(pair_id, -0.05)

        # Determine interval based on last known edge
        if last_edge > -0.01:
            interval = self.FAST_INTERVAL
        elif last_edge > -0.03:
            interval = self.NORMAL_INTERVAL
        else:
            interval = self.SLOW_INTERVAL

        return (now - last_poll) >= interval

    def collect_once(self, pairs: List[Tuple[str, str]]) -> int:
        """
        Collect one round of data.

        Returns number of snapshots collected.
        """
        trading_state = get_trading_state()
        collected = 0

        for ticker_a, ticker_b in pairs:
            pair_id = f"{ticker_a}:{ticker_b}"

            # Pause if trading is active to avoid competing for rate limits
            if trading_state.should_pause():
                trading_state.wait_while_paused(timeout=5.0)
                continue

            if not self._should_poll(pair_id):
                continue

            snap = self._fetch_pair(ticker_a, ticker_b)
            if snap is None:
                continue

            # Update state
            self._pair_edges[pair_id] = snap.edge
            self._pair_last_poll[pair_id] = time.time()

            # Store snapshot
            self.db.add_snapshot(snap)
            collected += 1
            self.snapshots_collected += 1

            # Track opportunities
            if snap.is_opportunity:
                self.db.record_opportunity(pair_id, snap.edge, snap.timestamp)
                self.opportunities_found += 1
                print(f"  ** OPPORTUNITY: {pair_id} edge=${snap.edge:+.4f} **")
            else:
                # Close any open opportunity for this pair
                self.db.close_opportunity(pair_id, snap.timestamp)

            # Small delay between pairs
            time.sleep(0.2)

        return collected

    def run(
        self,
        pairs: Optional[List[Tuple[str, str]]] = None,
        duration_hours: Optional[float] = None,
        show_progress: bool = True,
    ):
        """
        Run the collector.

        Args:
            pairs: Pairs to track (None = discover automatically)
            duration_hours: How long to run (None = until stopped)
            show_progress: Print status updates
        """
        # Get pairs
        if pairs is None:
            from arb.kalshi_scanner import get_all_known_pairs

            pairs = get_all_known_pairs()

        if show_progress:
            print("Smart Collector started")
            print(f"  Pairs: {len(pairs)}")
            print(
                f"  Duration: {duration_hours}h"
                if duration_hours
                else "  Duration: Until stopped"
            )
            print(f"  Fast poll: {self.FAST_INTERVAL}s (edge > -1%)")
            print(f"  Normal poll: {self.NORMAL_INTERVAL}s (edge > -3%)")
            print(f"  Slow poll: {self.SLOW_INTERVAL}s (edge < -3%)")
            print()

        start_time = time.time()
        end_time = start_time + (duration_hours * 3600) if duration_hours else None
        iteration = 0

        self._stop_event.clear()

        try:
            while not self._stop_event.is_set():
                iteration += 1

                # Check duration
                if end_time and time.time() >= end_time:
                    break

                # Collect
                self.collect_once(pairs)

                if show_progress and iteration % 10 == 0:
                    elapsed = (time.time() - start_time) / 60
                    self.db.get_stats()
                    print(
                        f"[{elapsed:.1f}m] Snapshots: {self.snapshots_collected}, "
                        f"Opportunities: {self.opportunities_found}, "
                        f"API calls: {self.api_calls}"
                    )

                # Brief pause before next round
                time.sleep(1)

        except KeyboardInterrupt:
            print("\nStopping...")

        if show_progress:
            elapsed = (time.time() - start_time) / 60
            print("\nCollection complete")
            print(f"  Duration: {elapsed:.1f} minutes")
            print(f"  Snapshots: {self.snapshots_collected}")
            print(f"  Opportunities found: {self.opportunities_found}")
            print(f"  API calls: {self.api_calls}")

    def stop(self):
        """Stop the collector."""
        self._stop_event.set()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Smart Spread Collector")
    parser.add_argument("--hours", type=float, help="Duration in hours")
    parser.add_argument("--stats", action="store_true", help="Show database stats")
    parser.add_argument("--db", default="data/smart_spreads.db", help="Database path")
    args = parser.parse_args()

    if args.stats:
        db = SmartSpreadDB(args.db)
        stats = db.get_stats()
        print("Smart Spread Database Stats")
        print("=" * 40)
        for k, v in stats.items():
            print(f"  {k}: {v}")
        return

    # Import here to avoid circular imports
    import sys

    sys.path.insert(0, ".")
    from src.core.api_client import KalshiClient
    from src.core.config import get_config

    print("Connecting to Kalshi...")
    config = get_config()
    client = KalshiClient(config)

    collector = SmartCollector(client, db_path=args.db)
    collector.run(duration_hours=args.hours)


if __name__ == "__main__":
    main()
