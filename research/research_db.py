"""
Research tracking database for managing hypothesis lifecycle.

This module provides a SQLite database to track:
- Hypotheses (generated strategies)
- Backtest results
- Reports (notebooks)
- Deployments to live trading
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class Hypothesis:
    """Represents a trading strategy hypothesis."""
    id: Optional[int]
    name: str
    description: str
    source: str  # e.g., "mcp_research", "manual", "ml_generator"
    created_at: datetime
    status: str  # "pending", "backtesting", "validated", "rejected", "deployed"
    metadata: Dict[str, Any]


@dataclass
class BacktestResult:
    """Represents backtest results for a hypothesis."""
    id: Optional[int]
    hypothesis_id: int
    sharpe: float
    max_drawdown: float
    win_rate: float
    p_value: float
    num_trades: int
    config: Dict[str, Any]
    created_at: datetime


@dataclass
class Report:
    """Represents a generated research report."""
    id: Optional[int]
    hypothesis_id: int
    backtest_id: Optional[int]
    notebook_path: str
    recommendation: str  # "deploy", "reject", "needs_work"
    created_at: datetime


@dataclass
class Deployment:
    """Represents a deployed strategy."""
    id: Optional[int]
    hypothesis_id: int
    deployed_at: datetime
    status: str  # "active", "paused", "retired"
    allocation: float  # capital allocation in dollars


class ResearchDB:
    """Database manager for research tracking."""

    def __init__(self, db_path: str = "data/research.db"):
        """Initialize database connection and create tables if needed."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        """Create database schema."""
        cursor = self.conn.cursor()

        # Hypotheses table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS hypotheses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                status TEXT NOT NULL,
                metadata TEXT NOT NULL
            )
        """)

        # Backtests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS backtests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER NOT NULL,
                sharpe REAL NOT NULL,
                max_drawdown REAL NOT NULL,
                win_rate REAL NOT NULL,
                p_value REAL NOT NULL,
                num_trades INTEGER NOT NULL,
                config TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
            )
        """)

        # Reports table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER NOT NULL,
                backtest_id INTEGER,
                notebook_path TEXT NOT NULL,
                recommendation TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id),
                FOREIGN KEY (backtest_id) REFERENCES backtests(id)
            )
        """)

        # Deployments table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deployments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis_id INTEGER NOT NULL,
                deployed_at TIMESTAMP NOT NULL,
                status TEXT NOT NULL,
                allocation REAL NOT NULL,
                FOREIGN KEY (hypothesis_id) REFERENCES hypotheses(id)
            )
        """)

        # Create indices for common queries (separate statements per memory note)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hypotheses_status
            ON hypotheses(status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_hypotheses_source
            ON hypotheses(source)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_backtests_hypothesis
            ON backtests(hypothesis_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_reports_hypothesis
            ON reports(hypothesis_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_reports_backtest
            ON reports(backtest_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_deployments_hypothesis
            ON deployments(hypothesis_id)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_deployments_status
            ON deployments(status)
        """)

        self.conn.commit()

    def save_hypothesis(self, hypothesis: Hypothesis) -> int:
        """
        Save a hypothesis to the database.

        Args:
            hypothesis: Hypothesis object to save

        Returns:
            The ID of the saved hypothesis
        """
        cursor = self.conn.cursor()

        if hypothesis.id is None:
            # Insert new hypothesis
            cursor.execute("""
                INSERT INTO hypotheses (name, description, source, created_at, status, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                hypothesis.name,
                hypothesis.description,
                hypothesis.source,
                hypothesis.created_at,
                hypothesis.status,
                json.dumps(hypothesis.metadata)
            ))
            self.conn.commit()
            return cursor.lastrowid
        else:
            # Update existing hypothesis
            cursor.execute("""
                UPDATE hypotheses
                SET name = ?, description = ?, source = ?, created_at = ?,
                    status = ?, metadata = ?
                WHERE id = ?
            """, (
                hypothesis.name,
                hypothesis.description,
                hypothesis.source,
                hypothesis.created_at,
                hypothesis.status,
                json.dumps(hypothesis.metadata),
                hypothesis.id
            ))
            self.conn.commit()
            return hypothesis.id

    def get_pending_hypotheses(self) -> List[Hypothesis]:
        """
        Get all hypotheses with status 'pending'.

        Returns:
            List of pending hypotheses
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM hypotheses
            WHERE status = 'pending'
            ORDER BY created_at ASC
        """)

        rows = cursor.fetchall()
        return [self._row_to_hypothesis(row) for row in rows]

    def get_hypothesis(self, hypothesis_id: int) -> Optional[Hypothesis]:
        """
        Get a hypothesis by ID.

        Args:
            hypothesis_id: The hypothesis ID

        Returns:
            Hypothesis object or None if not found
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM hypotheses WHERE id = ?", (hypothesis_id,))
        row = cursor.fetchone()
        return self._row_to_hypothesis(row) if row else None

    def save_backtest_results(
        self,
        hypothesis_id: int,
        results: BacktestResult
    ) -> int:
        """
        Save backtest results for a hypothesis.

        Args:
            hypothesis_id: The hypothesis ID
            results: BacktestResult object

        Returns:
            The ID of the saved backtest
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO backtests
            (hypothesis_id, sharpe, max_drawdown, win_rate, p_value, num_trades, config, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            hypothesis_id,
            results.sharpe,
            results.max_drawdown,
            results.win_rate,
            results.p_value,
            results.num_trades,
            json.dumps(results.config),
            results.created_at
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_backtest_results(self, hypothesis_id: int) -> List[BacktestResult]:
        """
        Get all backtest results for a hypothesis.

        Args:
            hypothesis_id: The hypothesis ID

        Returns:
            List of backtest results
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM backtests
            WHERE hypothesis_id = ?
            ORDER BY created_at DESC
        """, (hypothesis_id,))

        rows = cursor.fetchall()
        return [self._row_to_backtest(row) for row in rows]

    def save_report(
        self,
        hypothesis_id: int,
        notebook_path: str,
        recommendation: str = "needs_review",
        backtest_id: Optional[int] = None
    ) -> int:
        """
        Save a research report.

        Args:
            hypothesis_id: The hypothesis ID
            notebook_path: Path to the notebook file
            recommendation: Recommendation ("deploy", "reject", "needs_work")
            backtest_id: Optional backtest ID this report is based on

        Returns:
            The ID of the saved report
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO reports (hypothesis_id, backtest_id, notebook_path, recommendation, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (
            hypothesis_id,
            backtest_id,
            notebook_path,
            recommendation,
            datetime.now()
        ))
        self.conn.commit()
        return cursor.lastrowid

    def get_reports(self, hypothesis_id: int) -> List[Report]:
        """
        Get all reports for a hypothesis.

        Args:
            hypothesis_id: The hypothesis ID

        Returns:
            List of reports
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM reports
            WHERE hypothesis_id = ?
            ORDER BY created_at DESC
        """, (hypothesis_id,))

        rows = cursor.fetchall()
        return [self._row_to_report(row) for row in rows]

    def mark_deployed(
        self,
        hypothesis_id: int,
        allocation: float,
        status: str = "active"
    ) -> int:
        """
        Mark a hypothesis as deployed to live trading.

        Args:
            hypothesis_id: The hypothesis ID
            allocation: Capital allocation in dollars
            status: Deployment status (default: "active")

        Returns:
            The ID of the deployment record
        """
        cursor = self.conn.cursor()

        # Update hypothesis status
        cursor.execute("""
            UPDATE hypotheses
            SET status = 'deployed'
            WHERE id = ?
        """, (hypothesis_id,))

        # Create deployment record
        cursor.execute("""
            INSERT INTO deployments (hypothesis_id, deployed_at, status, allocation)
            VALUES (?, ?, ?, ?)
        """, (
            hypothesis_id,
            datetime.now(),
            status,
            allocation
        ))

        self.conn.commit()
        return cursor.lastrowid

    def get_deployments(
        self,
        status: Optional[str] = None
    ) -> List[Deployment]:
        """
        Get all deployments, optionally filtered by status.

        Args:
            status: Optional status filter ("active", "paused", "retired")

        Returns:
            List of deployments
        """
        cursor = self.conn.cursor()

        if status:
            cursor.execute("""
                SELECT * FROM deployments
                WHERE status = ?
                ORDER BY deployed_at DESC
            """, (status,))
        else:
            cursor.execute("""
                SELECT * FROM deployments
                ORDER BY deployed_at DESC
            """)

        rows = cursor.fetchall()
        return [self._row_to_deployment(row) for row in rows]

    def update_deployment_status(
        self,
        deployment_id: int,
        status: str
    ):
        """
        Update deployment status.

        Args:
            deployment_id: The deployment ID
            status: New status ("active", "paused", "retired")
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE deployments
            SET status = ?
            WHERE id = ?
        """, (status, deployment_id))
        self.conn.commit()

    def _row_to_hypothesis(self, row: sqlite3.Row) -> Hypothesis:
        """Convert database row to Hypothesis object."""
        return Hypothesis(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
            status=row["status"],
            metadata=json.loads(row["metadata"])
        )

    def _row_to_backtest(self, row: sqlite3.Row) -> BacktestResult:
        """Convert database row to BacktestResult object."""
        return BacktestResult(
            id=row["id"],
            hypothesis_id=row["hypothesis_id"],
            sharpe=row["sharpe"],
            max_drawdown=row["max_drawdown"],
            win_rate=row["win_rate"],
            p_value=row["p_value"],
            num_trades=row["num_trades"],
            config=json.loads(row["config"]),
            created_at=datetime.fromisoformat(row["created_at"])
        )

    def _row_to_report(self, row: sqlite3.Row) -> Report:
        """Convert database row to Report object."""
        return Report(
            id=row["id"],
            hypothesis_id=row["hypothesis_id"],
            backtest_id=row["backtest_id"],
            notebook_path=row["notebook_path"],
            recommendation=row["recommendation"],
            created_at=datetime.fromisoformat(row["created_at"])
        )

    def _row_to_deployment(self, row: sqlite3.Row) -> Deployment:
        """Convert database row to Deployment object."""
        return Deployment(
            id=row["id"],
            hypothesis_id=row["hypothesis_id"],
            deployed_at=datetime.fromisoformat(row["deployed_at"]),
            status=row["status"],
            allocation=row["allocation"]
        )

    def close(self):
        """Close database connection."""
        self.conn.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
