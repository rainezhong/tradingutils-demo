"""Pending resolution tracking for positions awaiting market settlement.

Tracks capital locked in positions that are waiting for market resolution
(e.g., prediction markets settling at expiration). This capital cannot
be redeployed until the position resolves.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclass
class PendingResolution:
    """A position awaiting market resolution.

    Attributes:
        position_id: Unique identifier for this position
        exchange: Exchange where the position is held
        ticker: Market ticker symbol
        capital_locked: Amount of capital locked in this position
        contracts: Number of contracts held
        expected_resolution: When the market is expected to resolve
        created_at: When this pending resolution was recorded
        metadata: Additional tracking data
    """
    position_id: str
    exchange: str
    ticker: str
    capital_locked: float
    contracts: int = 0
    expected_resolution: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict = field(default_factory=dict)

    @property
    def is_overdue(self) -> bool:
        """Check if resolution is past expected date."""
        if self.expected_resolution is None:
            return False
        return datetime.now() > self.expected_resolution

    @property
    def days_until_resolution(self) -> Optional[float]:
        """Days until expected resolution (negative if overdue)."""
        if self.expected_resolution is None:
            return None
        delta = self.expected_resolution - datetime.now()
        return delta.total_seconds() / 86400


class PendingResolutionTracker:
    """Tracks capital locked in positions awaiting resolution.

    Thread-safe tracker for managing pending resolutions across exchanges.
    Used by CapitalManager to calculate truly deployable capital.

    Example:
        >>> tracker = PendingResolutionTracker()
        >>> tracker.add_pending(
        ...     position_id="pos_001",
        ...     exchange="kalshi",
        ...     ticker="BTC-100K-DEC",
        ...     capital_locked=500.0,
        ...     contracts=100,
        ...     expected_resolution=datetime(2024, 12, 31)
        ... )
        >>> locked = tracker.get_total_locked("kalshi")
        >>> print(f"Locked capital: ${locked:.2f}")
    """

    def __init__(self) -> None:
        """Initialize the tracker."""
        self._pending: Dict[str, PendingResolution] = {}
        self._lock = threading.RLock()

    def add_pending(
        self,
        position_id: str,
        exchange: str,
        ticker: str,
        capital_locked: float,
        contracts: int = 0,
        expected_resolution: Optional[datetime] = None,
        metadata: Optional[Dict] = None,
    ) -> PendingResolution:
        """Add a pending resolution to track.

        Args:
            position_id: Unique identifier for this position
            exchange: Exchange where position is held
            ticker: Market ticker symbol
            capital_locked: Amount of capital locked
            contracts: Number of contracts
            expected_resolution: When market should resolve
            metadata: Additional tracking data

        Returns:
            The created PendingResolution

        Raises:
            ValueError: If position_id already exists
        """
        if capital_locked < 0:
            raise ValueError(f"capital_locked must be non-negative, got {capital_locked}")

        with self._lock:
            if position_id in self._pending:
                raise ValueError(f"Position already tracked: {position_id}")

            pending = PendingResolution(
                position_id=position_id,
                exchange=exchange,
                ticker=ticker,
                capital_locked=capital_locked,
                contracts=contracts,
                expected_resolution=expected_resolution,
                metadata=metadata or {},
            )

            self._pending[position_id] = pending

            logger.info(
                "Added pending resolution: id=%s exchange=%s ticker=%s locked=$%.2f",
                position_id,
                exchange,
                ticker,
                capital_locked,
            )

            return pending

    def resolve(self, position_id: str) -> Optional[float]:
        """Mark a position as resolved and release its locked capital.

        Args:
            position_id: ID of position to resolve

        Returns:
            Amount of capital that was locked, or None if not found
        """
        with self._lock:
            pending = self._pending.pop(position_id, None)
            if not pending:
                logger.debug("Position not found for resolution: %s", position_id)
                return None

            logger.info(
                "Resolved position: id=%s exchange=%s locked=$%.2f",
                position_id,
                pending.exchange,
                pending.capital_locked,
            )

            return pending.capital_locked

    def update_locked(self, position_id: str, new_locked: float) -> bool:
        """Update the locked capital for a position.

        Args:
            position_id: ID of position to update
            new_locked: New locked amount

        Returns:
            True if updated, False if not found
        """
        with self._lock:
            pending = self._pending.get(position_id)
            if not pending:
                return False

            old_locked = pending.capital_locked
            pending.capital_locked = new_locked

            logger.debug(
                "Updated locked capital: id=%s $%.2f -> $%.2f",
                position_id,
                old_locked,
                new_locked,
            )

            return True

    def get_pending(self, position_id: str) -> Optional[PendingResolution]:
        """Get a pending resolution by ID.

        Args:
            position_id: ID of position

        Returns:
            PendingResolution or None if not found
        """
        with self._lock:
            pending = self._pending.get(position_id)
            if pending:
                # Return a copy
                return PendingResolution(
                    position_id=pending.position_id,
                    exchange=pending.exchange,
                    ticker=pending.ticker,
                    capital_locked=pending.capital_locked,
                    contracts=pending.contracts,
                    expected_resolution=pending.expected_resolution,
                    created_at=pending.created_at,
                    metadata=pending.metadata.copy(),
                )
            return None

    def get_total_locked(self, exchange: Optional[str] = None) -> float:
        """Get total capital locked in pending resolutions.

        Args:
            exchange: Optional filter by exchange

        Returns:
            Total locked capital
        """
        with self._lock:
            total = 0.0
            for pending in self._pending.values():
                if exchange is None or pending.exchange == exchange:
                    total += pending.capital_locked
            return total

    def get_all_pending(
        self, exchange: Optional[str] = None
    ) -> List[PendingResolution]:
        """Get all pending resolutions.

        Args:
            exchange: Optional filter by exchange

        Returns:
            List of PendingResolution objects
        """
        with self._lock:
            results = []
            for pending in self._pending.values():
                if exchange is None or pending.exchange == exchange:
                    results.append(
                        PendingResolution(
                            position_id=pending.position_id,
                            exchange=pending.exchange,
                            ticker=pending.ticker,
                            capital_locked=pending.capital_locked,
                            contracts=pending.contracts,
                            expected_resolution=pending.expected_resolution,
                            created_at=pending.created_at,
                            metadata=pending.metadata.copy(),
                        )
                    )
            return results

    def get_overdue(self) -> List[PendingResolution]:
        """Get all pending resolutions past their expected resolution date.

        Returns:
            List of overdue PendingResolution objects
        """
        with self._lock:
            return [
                PendingResolution(
                    position_id=p.position_id,
                    exchange=p.exchange,
                    ticker=p.ticker,
                    capital_locked=p.capital_locked,
                    contracts=p.contracts,
                    expected_resolution=p.expected_resolution,
                    created_at=p.created_at,
                    metadata=p.metadata.copy(),
                )
                for p in self._pending.values()
                if p.is_overdue
            ]

    def get_resolving_soon(self, days: float = 1.0) -> List[PendingResolution]:
        """Get pending resolutions expected to resolve within N days.

        Args:
            days: Number of days to look ahead

        Returns:
            List of PendingResolution objects resolving soon
        """
        with self._lock:
            results = []
            for pending in self._pending.values():
                days_until = pending.days_until_resolution
                if days_until is not None and 0 <= days_until <= days:
                    results.append(
                        PendingResolution(
                            position_id=pending.position_id,
                            exchange=pending.exchange,
                            ticker=pending.ticker,
                            capital_locked=pending.capital_locked,
                            contracts=pending.contracts,
                            expected_resolution=pending.expected_resolution,
                            created_at=pending.created_at,
                            metadata=pending.metadata.copy(),
                        )
                    )
            return results

    def get_summary(self) -> Dict:
        """Get summary of pending resolutions.

        Returns:
            Dictionary with summary statistics
        """
        with self._lock:
            by_exchange: Dict[str, float] = {}
            total_locked = 0.0
            overdue_count = 0

            for pending in self._pending.values():
                total_locked += pending.capital_locked
                by_exchange[pending.exchange] = (
                    by_exchange.get(pending.exchange, 0.0) + pending.capital_locked
                )
                if pending.is_overdue:
                    overdue_count += 1

            return {
                "total_pending": len(self._pending),
                "total_locked": total_locked,
                "by_exchange": by_exchange,
                "overdue_count": overdue_count,
            }

    def clear_all(self) -> int:
        """Clear all pending resolutions.

        Returns:
            Number of resolutions cleared
        """
        with self._lock:
            count = len(self._pending)
            self._pending.clear()
            logger.info("Cleared %d pending resolutions", count)
            return count
