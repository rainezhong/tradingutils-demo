"""Capital reservation system to prevent double-spending.

Reserves capital before spread execution to ensure funds are available
for both legs. Releases capital on fill or timeout.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Optional

from src.core.exchange import ExchangeClient


logger = logging.getLogger(__name__)


@dataclass
class CapitalReservation:
    """A capital reservation for a pending trade.

    Attributes:
        reservation_id: Unique identifier for this reservation
        exchange: Exchange where capital is reserved
        amount: Amount of capital reserved (in dollars)
        purpose: Description of what the reservation is for
        created_at: When the reservation was made
        expires_at: When the reservation automatically expires
        opportunity_id: Optional reference to spread opportunity
        metadata: Additional tracking data
    """
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
        """Check if reservation has expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at


@dataclass
class CapitalState:
    """Current state of capital for an exchange.

    Attributes:
        exchange: Exchange name
        total_balance: Total balance on exchange
        reserved: Amount currently reserved
        pending_settlements: Amount pending from unsettled fills
        last_sync: Last time balance was synced with exchange
    """
    exchange: str
    total_balance: float = 0.0
    reserved: float = 0.0
    pending_settlements: float = 0.0
    last_sync: Optional[datetime] = None

    @property
    def available(self) -> float:
        """Calculate available capital (total - reserved)."""
        return max(0.0, self.total_balance - self.reserved)

    @property
    def effective_available(self) -> float:
        """Available including pending settlements."""
        return max(0.0, self.total_balance + self.pending_settlements - self.reserved)


class CapitalManager:
    """Manages capital reservations across exchanges.

    Prevents double-spending by reserving capital before trade execution
    and releasing it upon completion or timeout.

    Thread-safe for concurrent access.

    Example:
        >>> manager = CapitalManager()
        >>> manager.set_exchange_balance("kalshi", 10000.0)
        >>>
        >>> # Reserve capital for a spread trade
        >>> reserved = manager.reserve(
        ...     reservation_id="spread_001",
        ...     exchange="kalshi",
        ...     amount=500.0,
        ...     purpose="Spread leg 1",
        ...     ttl_seconds=30
        ... )
        >>> if reserved:
        ...     # Execute trade...
        ...     manager.release("spread_001")
        >>> else:
        ...     print("Insufficient capital")
    """

    def __init__(self, safety_margin: float = 0.05) -> None:
        """Initialize CapitalManager.

        Args:
            safety_margin: Fraction of balance to keep as safety buffer (0.05 = 5%)
        """
        self._safety_margin = safety_margin
        self._states: Dict[str, CapitalState] = {}
        self._reservations: Dict[str, CapitalReservation] = {}
        self._lock = threading.RLock()

    def set_exchange_balance(self, exchange: str, balance: float) -> None:
        """Set or update the total balance for an exchange.

        Args:
            exchange: Exchange name
            balance: Total available balance in dollars
        """
        with self._lock:
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

    def sync_from_exchange(self, client: ExchangeClient) -> float:
        """Sync balance from an exchange client.

        Args:
            client: Exchange client to query

        Returns:
            Updated balance
        """
        balance = client.get_balance()
        self.set_exchange_balance(client.name, balance)
        return balance

    def get_available_capital(self, exchange: str) -> float:
        """Get available capital for an exchange (total - reserved - safety margin).

        Args:
            exchange: Exchange name

        Returns:
            Available capital in dollars
        """
        with self._lock:
            state = self._states.get(exchange)
            if not state:
                return 0.0

            # Apply safety margin
            available = state.available * (1.0 - self._safety_margin)
            return max(0.0, available)

    def get_deployable_capital(
        self,
        exchange: str,
        emergency_reserve_pct: float = 0.25,
        pending_locked: float = 0.0,
    ) -> float:
        """Get capital available for deployment after all reserves.

        This is the capital that can actually be used for new trades,
        accounting for:
        - Current reservations
        - Safety margin
        - Emergency reserve (for stuck positions)
        - Pending resolution locked capital

        Args:
            exchange: Exchange name
            emergency_reserve_pct: Fraction of total balance to keep as emergency reserve
            pending_locked: Amount locked in pending resolutions

        Returns:
            Deployable capital in dollars
        """
        with self._lock:
            available = self.get_available_capital(exchange)
            state = self._states.get(exchange)

            if not state:
                return 0.0

            # Calculate emergency reserve from total balance
            emergency_reserve = state.total_balance * emergency_reserve_pct

            # Deployable = available - emergency reserve - pending locked
            deployable = available - emergency_reserve - pending_locked

            logger.debug(
                "Deployable capital: exchange=%s available=$%.2f emergency=$%.2f "
                "pending=$%.2f deployable=$%.2f",
                exchange,
                available,
                emergency_reserve,
                pending_locked,
                deployable,
            )

            return max(0.0, deployable)

    def get_total_reserved(self, exchange: str) -> float:
        """Get total reserved capital for an exchange.

        Args:
            exchange: Exchange name

        Returns:
            Total reserved amount
        """
        with self._lock:
            state = self._states.get(exchange)
            return state.reserved if state else 0.0

    def get_capital_state(self, exchange: str) -> Optional[CapitalState]:
        """Get full capital state for an exchange.

        Args:
            exchange: Exchange name

        Returns:
            CapitalState or None if exchange not tracked
        """
        with self._lock:
            state = self._states.get(exchange)
            if state:
                # Return a copy to prevent external modification
                return CapitalState(
                    exchange=state.exchange,
                    total_balance=state.total_balance,
                    reserved=state.reserved,
                    pending_settlements=state.pending_settlements,
                    last_sync=state.last_sync,
                )
            return None

    def reserve(
        self,
        reservation_id: str,
        exchange: str,
        amount: float,
        purpose: str = "",
        ttl_seconds: Optional[float] = None,
        opportunity_id: Optional[str] = None,
    ) -> bool:
        """Reserve capital for a pending trade.

        Args:
            reservation_id: Unique identifier for this reservation
            exchange: Exchange where capital is needed
            amount: Amount to reserve in dollars
            purpose: Description of what the reservation is for
            ttl_seconds: Time-to-live in seconds (auto-expires if set)
            opportunity_id: Optional reference to opportunity

        Returns:
            True if reservation successful, False if insufficient capital
        """
        if amount <= 0:
            raise ValueError(f"Reservation amount must be positive, got {amount}")

        with self._lock:
            # Check if reservation ID already exists
            if reservation_id in self._reservations:
                logger.warning(
                    "Reservation already exists: id=%s",
                    reservation_id,
                )
                return False

            # Check available capital
            available = self.get_available_capital(exchange)
            if available < amount:
                logger.warning(
                    "Insufficient capital: exchange=%s requested=$%.2f available=$%.2f",
                    exchange,
                    amount,
                    available,
                )
                return False

            # Create reservation
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

            # Update state
            state = self._states.get(exchange)
            if state:
                state.reserved += amount

            self._reservations[reservation_id] = reservation

            logger.info(
                "Capital reserved: id=%s exchange=%s amount=$%.2f purpose=%s expires=%s",
                reservation_id,
                exchange,
                amount,
                purpose,
                expires_at.isoformat() if expires_at else "never",
            )
            return True

    def release(self, reservation_id: str) -> Optional[float]:
        """Release a capital reservation.

        Args:
            reservation_id: ID of reservation to release

        Returns:
            Amount released, or None if reservation not found
        """
        with self._lock:
            reservation = self._reservations.pop(reservation_id, None)
            if not reservation:
                logger.debug("Reservation not found: id=%s", reservation_id)
                return None

            # Update state
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

    def release_for_opportunity(self, opportunity_id: str) -> float:
        """Release all reservations for an opportunity.

        Args:
            opportunity_id: Opportunity ID

        Returns:
            Total amount released
        """
        total_released = 0.0
        to_release = []

        with self._lock:
            for res_id, res in self._reservations.items():
                if res.opportunity_id == opportunity_id:
                    to_release.append(res_id)

        for res_id in to_release:
            amount = self.release(res_id)
            if amount:
                total_released += amount

        return total_released

    def check_and_release_expired(self) -> int:
        """Release all expired reservations.

        Returns:
            Number of reservations released
        """
        expired = []

        with self._lock:
            for res_id, res in self._reservations.items():
                if res.is_expired:
                    expired.append(res_id)

        for res_id in expired:
            logger.info("Releasing expired reservation: id=%s", res_id)
            self.release(res_id)

        return len(expired)

    def get_reservation(self, reservation_id: str) -> Optional[CapitalReservation]:
        """Get details of a reservation.

        Args:
            reservation_id: ID of reservation

        Returns:
            CapitalReservation or None if not found
        """
        with self._lock:
            res = self._reservations.get(reservation_id)
            if res:
                # Return a copy
                return CapitalReservation(
                    reservation_id=res.reservation_id,
                    exchange=res.exchange,
                    amount=res.amount,
                    purpose=res.purpose,
                    created_at=res.created_at,
                    expires_at=res.expires_at,
                    opportunity_id=res.opportunity_id,
                    metadata=res.metadata.copy(),
                )
            return None

    def get_all_reservations(self, exchange: Optional[str] = None) -> list[CapitalReservation]:
        """Get all active reservations.

        Args:
            exchange: Optional filter by exchange

        Returns:
            List of reservations
        """
        with self._lock:
            reservations = []
            for res in self._reservations.values():
                if exchange is None or res.exchange == exchange:
                    reservations.append(
                        CapitalReservation(
                            reservation_id=res.reservation_id,
                            exchange=res.exchange,
                            amount=res.amount,
                            purpose=res.purpose,
                            created_at=res.created_at,
                            expires_at=res.expires_at,
                            opportunity_id=res.opportunity_id,
                            metadata=res.metadata.copy(),
                        )
                    )
            return reservations

    def adjust_for_fill(self, exchange: str, fill_value: float) -> None:
        """Adjust capital for a fill event.

        When an order fills, we may receive or spend capital.
        This updates the balance accordingly.

        Args:
            exchange: Exchange where fill occurred
            fill_value: Value of fill (positive = received, negative = spent)
        """
        with self._lock:
            state = self._states.get(exchange)
            if state:
                state.total_balance += fill_value
                logger.debug(
                    "Capital adjusted for fill: exchange=%s delta=$%.2f new_balance=$%.2f",
                    exchange,
                    fill_value,
                    state.total_balance,
                )

    def add_pending_settlement(self, exchange: str, amount: float) -> None:
        """Add a pending settlement amount.

        Args:
            exchange: Exchange name
            amount: Settlement amount (positive = incoming)
        """
        with self._lock:
            if exchange not in self._states:
                self._states[exchange] = CapitalState(exchange=exchange)
            self._states[exchange].pending_settlements += amount

    def clear_pending_settlements(self, exchange: str) -> float:
        """Clear pending settlements (when settled).

        Args:
            exchange: Exchange name

        Returns:
            Amount that was pending
        """
        with self._lock:
            state = self._states.get(exchange)
            if state:
                pending = state.pending_settlements
                state.pending_settlements = 0.0
                return pending
            return 0.0

    def get_summary(self) -> Dict:
        """Get summary of capital state across all exchanges.

        Returns:
            Dictionary with summary data
        """
        with self._lock:
            total_balance = 0.0
            total_reserved = 0.0
            total_available = 0.0

            exchanges = {}
            for name, state in self._states.items():
                available = self.get_available_capital(name)
                exchanges[name] = {
                    "balance": state.total_balance,
                    "reserved": state.reserved,
                    "available": available,
                    "pending_settlements": state.pending_settlements,
                    "last_sync": state.last_sync.isoformat() if state.last_sync else None,
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
