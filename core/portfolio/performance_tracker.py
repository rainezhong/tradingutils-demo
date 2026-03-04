"""
Performance tracker for portfolio strategies.

Maintains SQLite database of all trades and calculates edge/variance estimates.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
import logging

from core.portfolio.types import StrategyStats, StrategyTrade


logger = logging.getLogger(__name__)


class PerformanceTracker:
    """Track strategy performance for portfolio allocation."""

    def __init__(self, db_path: str = "data/portfolio_trades.db"):
        """Initialize performance tracker.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_name TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size INTEGER NOT NULL,
                    pnl REAL,
                    settled_at REAL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategy_ts
                ON strategy_trades (strategy_name, timestamp)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ticker
                ON strategy_trades (ticker)
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_performance (
                    strategy_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    total_pnl REAL NOT NULL,
                    num_trades INTEGER NOT NULL,
                    edge REAL NOT NULL,
                    variance REAL NOT NULL,
                    PRIMARY KEY (strategy_name, date)
                )
            """)

            conn.commit()

    def record_trade(
        self,
        strategy_name: str,
        ticker: str,
        timestamp: datetime,
        side: str,
        price: float,
        size: int,
        pnl: Optional[float] = None,
        settled_at: Optional[datetime] = None,
    ) -> int:
        """Record a single trade.

        Args:
            strategy_name: Name of strategy that made trade
            ticker: Market ticker
            timestamp: Trade timestamp
            side: "buy" or "sell"
            price: Fill price
            size: Position size (contracts)
            pnl: Realized PnL (if position closed)
            settled_at: When position was closed

        Returns:
            Trade ID
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO strategy_trades
                (strategy_name, ticker, timestamp, side, price, size, pnl, settled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy_name,
                    ticker,
                    timestamp.timestamp(),
                    side,
                    price,
                    size,
                    pnl,
                    settled_at.timestamp() if settled_at else None,
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def update_trade_pnl(
        self,
        trade_id: int,
        pnl: float,
        settled_at: datetime,
    ):
        """Update trade with realized PnL.

        Args:
            trade_id: ID of trade to update
            pnl: Realized PnL
            settled_at: When position was closed
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE strategy_trades
                SET pnl = ?, settled_at = ?
                WHERE id = ?
                """,
                (pnl, settled_at.timestamp(), trade_id),
            )
            conn.commit()

    def record_backtest_fills(
        self,
        strategy_name: str,
        trades: List[Dict],
    ):
        """Record fills from backtest.

        Args:
            strategy_name: Name of strategy
            trades: List of trade dicts with keys:
                ticker, timestamp, side, price, size, pnl, settled_at
        """
        with sqlite3.connect(self.db_path) as conn:
            for trade in trades:
                conn.execute(
                    """
                    INSERT INTO strategy_trades
                    (strategy_name, ticker, timestamp, side, price, size, pnl, settled_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy_name,
                        trade["ticker"],
                        trade["timestamp"].timestamp(),
                        trade["side"],
                        trade["price"],
                        trade["size"],
                        trade.get("pnl"),
                        trade["settled_at"].timestamp()
                        if trade.get("settled_at")
                        else None,
                    ),
                )
            conn.commit()

        logger.info(
            f"Recorded {len(trades)} backtest fills for {strategy_name}"
        )

    def get_strategy_stats(
        self,
        strategy_name: str,
        lookback_days: int = 30,
    ) -> Optional[StrategyStats]:
        """Calculate performance statistics for a strategy.

        Args:
            strategy_name: Name of strategy
            lookback_days: How many days of history to analyze

        Returns:
            StrategyStats or None if insufficient data
        """
        cutoff = datetime.now() - timedelta(days=lookback_days)

        with sqlite3.connect(self.db_path) as conn:
            # Get settled trades with PnL
            cursor = conn.execute(
                """
                SELECT pnl, price, size
                FROM strategy_trades
                WHERE strategy_name = ?
                  AND timestamp >= ?
                  AND pnl IS NOT NULL
                ORDER BY timestamp
                """,
                (strategy_name, cutoff.timestamp()),
            )

            trades = cursor.fetchall()

        if not trades:
            return None

        pnls = [t[0] for t in trades]
        num_trades = len(pnls)

        # Calculate statistics
        total_pnl = sum(pnls)
        edge = total_pnl / num_trades  # Mean PnL per trade

        # Variance of returns
        variance = sum((p - edge) ** 2 for p in pnls) / num_trades
        std_dev = variance ** 0.5

        # Sharpe ratio (assuming zero risk-free rate)
        sharpe_ratio = edge / std_dev if std_dev > 0 else 0.0

        # Win rate and avg win/loss
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / num_trades if num_trades > 0 else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0

        return StrategyStats(
            strategy_name=strategy_name,
            total_pnl=total_pnl,
            num_trades=num_trades,
            edge=edge,
            variance=variance,
            std_dev=std_dev,
            sharpe_ratio=sharpe_ratio,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            lookback_days=lookback_days,
            last_updated=datetime.now(),
        )

    def get_all_strategy_names(self) -> List[str]:
        """Get list of all strategies with recorded trades."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT DISTINCT strategy_name
                FROM strategy_trades
                ORDER BY strategy_name
                """
            )
            return [row[0] for row in cursor.fetchall()]

    def get_trades_for_correlation(
        self,
        strategy_names: List[str],
        lookback_days: int = 30,
    ) -> Dict[str, List[StrategyTrade]]:
        """Get trades for correlation analysis.

        Args:
            strategy_names: List of strategies to fetch
            lookback_days: How many days of history

        Returns:
            Dict mapping strategy name to list of trades
        """
        cutoff = datetime.now() - timedelta(days=lookback_days)

        result = {name: [] for name in strategy_names}

        with sqlite3.connect(self.db_path) as conn:
            for strategy_name in strategy_names:
                cursor = conn.execute(
                    """
                    SELECT id, ticker, timestamp, side, price, size, pnl, settled_at
                    FROM strategy_trades
                    WHERE strategy_name = ?
                      AND timestamp >= ?
                    ORDER BY timestamp
                    """,
                    (strategy_name, cutoff.timestamp()),
                )

                for row in cursor.fetchall():
                    result[strategy_name].append(
                        StrategyTrade(
                            id=row[0],
                            strategy_name=strategy_name,
                            ticker=row[1],
                            timestamp=datetime.fromtimestamp(row[2]),
                            side=row[3],
                            price=row[4],
                            size=row[5],
                            pnl=row[6],
                            settled_at=datetime.fromtimestamp(row[7])
                            if row[7]
                            else None,
                        )
                    )

        return result

    def get_total_trades(self, strategy_name: str) -> int:
        """Get total number of trades for a strategy (all time)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*)
                FROM strategy_trades
                WHERE strategy_name = ?
                """,
                (strategy_name,),
            )
            return cursor.fetchone()[0]

    def get_trade_pnls(
        self,
        strategy_name: str,
        lookback_days: int = 30,
    ) -> List[float]:
        """Get list of trade PnLs for empirical Kelly calculation.

        Args:
            strategy_name: Name of strategy
            lookback_days: How many days of history to analyze

        Returns:
            List of settled trade PnLs
        """
        cutoff = datetime.now() - timedelta(days=lookback_days)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                SELECT pnl
                FROM strategy_trades
                WHERE strategy_name = ?
                  AND timestamp >= ?
                  AND pnl IS NOT NULL
                ORDER BY timestamp
                """,
                (strategy_name, cutoff.timestamp()),
            )

            return [row[0] for row in cursor.fetchall()]
