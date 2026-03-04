"""Capital reservation system to prevent double-spending.

Ported from src/oms/capital_manager.py — converted threading.RLock to asyncio.Lock.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


@dataclass
class CapitalReservation:
    """A capital reservation for a pending trade."""

    reservation_id: str
    exchange: str
    amount: float
    purpose: str
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    opportunity_id: Optional[str] = None
    metadata: Dict = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at


@dataclass
class CapitalState:
    """Current state of capital for an exchange."""

    exchange: str
    total_balance: float = 0.0
    reserved: float = 0.0
    pending_settlements: float = 0.0
    last_sync: Optional[datetime] = None

    @property
    def available(self) -> float:
        return max(0.0, self.total_balance - self.reserved)

    @property
    def effective_available(self) -> float:
        return max(0.0, self.total_balance + self.pending_settlements - self.reserved)


class CapitalManager:
    """Manages capital reservations across exchanges.

    Async-safe version using asyncio.Lock.
    """

    def __init__(self, safety_margin: float = 0.05) -> None:
        self._safety_margin = safety_margin
        self._states: Dict[str, CapitalState] = {}
        self._reservations: Dict[str, CapitalReservation] = {}
        self._lock = asyncio.Lock()

    async def set_exchange_balance(self, exchange: str, balance: float) -> None:
        async with self._lock:
            if exchange not in self._states:
                self._states[exchange] = CapitalState(exchange=exchange)
            self._states[exchange].total_balance = balance
            self._states[exchange].last_sync = datetime.now()
            logger.info(
                "Balance updated: exchange=%s balance=$%.2f available=$%.2f",
                exchange,
                balance,
                self._states[exchange].available,
            )

    async def sync_from_exchange(self, client: Any) -> float:
        """Sync balance from an exchange client (must have async get_balance)."""
        balance_data = await client.get_balance()
        balance = (
            balance_data.balance_cents / 100.0
            if hasattr(balance_data, "balance_cents")
            else float(balance_data)
        )
        await self.set_exchange_balance(client.name, balance)
        return balance

    async def get_available_capital(self, exchange: str) -> float:
        async with self._lock:
            state = self._states.get(exchange)
            if not state:
                return 0.0
            available = state.available * (1.0 - self._safety_margin)
            return max(0.0, available)

    async def get_deployable_capital(
        self,
        exchange: str,
        emergency_reserve_pct: float = 0.25,
        pending_locked: float = 0.0,
    ) -> float:
        async with self._lock:
            state = self._states.get(exchange)
            if not state:
                return 0.0
            available = state.available * (1.0 - self._safety_margin)
            emergency_reserve = state.total_balance * emergency_reserve_pct
            deployable = available - emergency_reserve - pending_locked
            return max(0.0, deployable)

    async def get_total_reserved(self, exchange: str) -> float:
        async with self._lock:
            state = self._states.get(exchange)
            return state.reserved if state else 0.0

    async def get_capital_state(self, exchange: str) -> Optional[CapitalState]:
        async with self._lock:
            state = self._states.get(exchange)
            if state:
                return CapitalState(
                    exchange=state.exchange,
                    total_balance=state.total_balance,
                    reserved=state.reserved,
                    pending_settlements=state.pending_settlements,
                    last_sync=state.last_sync,
                )
            return None

    async def reserve(
        self,
        reservation_id: str,
        exchange: str,
        amount: float,
        purpose: str = "",
        ttl_seconds: Optional[float] = None,
        opportunity_id: Optional[str] = None,
    ) -> bool:
        if amount <= 0:
            raise ValueError(f"Reservation amount must be positive, got {amount}")

        async with self._lock:
            if reservation_id in self._reservations:
                logger.warning("Reservation already exists: id=%s", reservation_id)
                return False

            state = self._states.get(exchange)
            if not state:
                return False

            available = state.available * (1.0 - self._safety_margin)
            if available < amount:
                logger.warning(
                    "Insufficient capital: exchange=%s requested=$%.2f available=$%.2f",
                    exchange,
                    amount,
                    available,
                )
                return False

            expires_at = None
            if ttl_seconds is not None:
                expires_at = datetime.now() + timedelta(seconds=ttl_seconds)

            reservation = CapitalReservation(
                reservation_id=reservation_id,
                exchange=exchange,
                amount=amount,
                purpose=purpose,
                expires_at=expires_at,
                opportunity_id=opportunity_id,
            )

            state.reserved += amount
            self._reservations[reservation_id] = reservation
            logger.info(
                "Capital reserved: id=%s exchange=%s amount=$%.2f purpose=%s",
                reservation_id,
                exchange,
                amount,
                purpose,
            )
            return True

    async def release(self, reservation_id: str) -> Optional[float]:
        async with self._lock:
            reservation = self._reservations.pop(reservation_id, None)
            if not reservation:
                return None

            state = self._states.get(reservation.exchange)
            if state:
                state.reserved = max(0.0, state.reserved - reservation.amount)

            logger.info(
                "Capital released: id=%s exchange=%s amount=$%.2f",
                reservation_id,
                reservation.exchange,
                reservation.amount,
            )
            return reservation.amount

    async def release_for_opportunity(self, opportunity_id: str) -> float:
        total_released = 0.0
        to_release = []

        async with self._lock:
            for res_id, res in self._reservations.items():
                if res.opportunity_id == opportunity_id:
                    to_release.append(res_id)

        for res_id in to_release:
            amount = await self.release(res_id)
            if amount:
                total_released += amount

        return total_released

    async def check_and_release_expired(self) -> int:
        expired = []
        async with self._lock:
            for res_id, res in self._reservations.items():
                if res.is_expired:
                    expired.append(res_id)

        for res_id in expired:
            await self.release(res_id)

        return len(expired)

    async def adjust_for_fill(self, exchange: str, fill_value: float) -> None:
        async with self._lock:
            state = self._states.get(exchange)
            if state:
                state.total_balance += fill_value

    async def add_pending_settlement(self, exchange: str, amount: float) -> None:
        async with self._lock:
            if exchange not in self._states:
                self._states[exchange] = CapitalState(exchange=exchange)
            self._states[exchange].pending_settlements += amount

    async def clear_pending_settlements(self, exchange: str) -> float:
        async with self._lock:
            state = self._states.get(exchange)
            if state:
                pending = state.pending_settlements
                state.pending_settlements = 0.0
                return pending
            return 0.0

    async def get_summary(self) -> Dict:
        async with self._lock:
            total_balance = 0.0
            total_reserved = 0.0
            total_available = 0.0
            exchanges = {}

            for name, state in self._states.items():
                available = state.available * (1.0 - self._safety_margin)
                exchanges[name] = {
                    "balance": state.total_balance,
                    "reserved": state.reserved,
                    "available": available,
                    "pending_settlements": state.pending_settlements,
                    "last_sync": state.last_sync.isoformat()
                    if state.last_sync
                    else None,
                }
                total_balance += state.total_balance
                total_reserved += state.reserved
                total_available += available

            return {
                "total_balance": total_balance,
                "total_reserved": total_reserved,
                "total_available": total_available,
                "active_reservations": len(self._reservations),
                "exchanges": exchanges,
            }
