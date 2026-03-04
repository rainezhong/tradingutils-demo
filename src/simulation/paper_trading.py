"""Paper trading system with live market data and simulated execution.

Connects to real Kalshi market data while tracking simulated orders, positions,
and P&L locally. This enables testing strategies against live market conditions
without risking capital.

Key features:
- Live market data pass-through
- Continuous background fill checking for resting orders
- Full Kalshi fee model (maker/taker)
- Realized + unrealized P&L tracking
- Cash balance management
- JSON state persistence
"""

import json
import logging
import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.interfaces import APIClient
from ..core.models import MarketState
from ..core.utils import utc_now


logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class PaperFill:
    """Record of a simulated fill.

    Attributes:
        fill_id: Unique identifier for this fill
        order_id: ID of the order that was filled
        ticker: Market identifier
        side: 'BID' or 'ASK'
        price: Execution price (0-1 as probability)
        size: Number of contracts filled
        fee: Transaction fee in dollars
        timestamp: When the fill occurred
    """

    fill_id: str
    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    fee: float
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "fee": self.fee,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperFill":
        """Create from dictionary."""
        return cls(
            fill_id=data["fill_id"],
            order_id=data["order_id"],
            ticker=data["ticker"],
            side=data["side"],
            price=data["price"],
            size=data["size"],
            fee=data["fee"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


@dataclass
class PaperOrder:
    """Record of a paper order.

    Attributes:
        order_id: Unique identifier for this order
        ticker: Market identifier
        side: 'BID' or 'ASK'
        price: Limit price (0-1 as probability)
        size: Total number of contracts
        filled_size: Number of contracts filled so far
        status: OPEN, FILLED, PARTIALLY_FILLED, or CANCELED
        fills: List of fills for this order
        created_at: When the order was placed
        updated_at: Last time the order was updated
    """

    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    created_at: datetime
    updated_at: datetime
    filled_size: int = 0
    status: str = "OPEN"
    fills: List[PaperFill] = field(default_factory=list)

    @property
    def remaining_size(self) -> int:
        """Get unfilled size."""
        return self.size - self.filled_size

    @property
    def is_open(self) -> bool:
        """Check if order can still be filled."""
        return self.status in ("OPEN", "PARTIALLY_FILLED")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "order_id": self.order_id,
            "ticker": self.ticker,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "filled_size": self.filled_size,
            "status": self.status,
            "fills": [f.to_dict() for f in self.fills],
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperOrder":
        """Create from dictionary."""
        order = cls(
            order_id=data["order_id"],
            ticker=data["ticker"],
            side=data["side"],
            price=data["price"],
            size=data["size"],
            filled_size=data["filled_size"],
            status=data["status"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
        )
        order.fills = [PaperFill.from_dict(f) for f in data.get("fills", [])]
        return order


@dataclass
class PaperPosition:
    """Represents a paper trading position.

    Attributes:
        ticker: Market identifier
        size: Position size (positive=long, negative=short, 0=flat)
        avg_entry_price: Volume-weighted average entry price
        realized_pnl: Realized P&L from closed portions (in dollars)
        total_fees: Total fees paid for this position
    """

    ticker: str
    size: int = 0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    total_fees: float = 0.0

    @property
    def is_long(self) -> bool:
        """Check if position is long."""
        return self.size > 0

    @property
    def is_short(self) -> bool:
        """Check if position is short."""
        return self.size < 0

    @property
    def is_flat(self) -> bool:
        """Check if position is flat."""
        return self.size == 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "ticker": self.ticker,
            "size": self.size,
            "avg_entry_price": self.avg_entry_price,
            "realized_pnl": self.realized_pnl,
            "total_fees": self.total_fees,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperPosition":
        """Create from dictionary."""
        return cls(
            ticker=data["ticker"],
            size=data["size"],
            avg_entry_price=data["avg_entry_price"],
            realized_pnl=data["realized_pnl"],
            total_fees=data["total_fees"],
        )


# =============================================================================
# Fee Calculation
# =============================================================================


def calculate_fee(price: float, size: int, maker: bool = False) -> float:
    """Calculate Kalshi trading fee.

    Kalshi fee formula: round_up(rate * contracts * price * (1-price))

    Args:
        price: Contract price (0-1 as probability)
        size: Number of contracts
        maker: If True, use maker rate (0.0175); otherwise taker rate (0.07)

    Returns:
        Fee in dollars, rounded up to nearest cent
    """
    rate = 0.0175 if maker else 0.07
    fee = rate * size * price * (1.0 - price)
    # Round up to nearest cent
    return math.ceil(fee * 100) / 100


# =============================================================================
# Paper Trading Client
# =============================================================================


class PaperTradingClient(APIClient):
    """Paper trading client with live market data and simulated execution.

    Wraps a real API client (e.g., KalshiClient) to:
    - Pass through all market data reads to the real exchange
    - Simulate order execution locally with realistic fill logic
    - Track positions, P&L, and cash balance
    - Optionally persist state to JSON for session continuity

    Example:
        >>> from src.core.api_client import KalshiClient, Config
        >>> config = Config.from_yaml('config.yaml')
        >>> kalshi = KalshiClient(config)
        >>> paper = PaperTradingClient(
        ...     market_data_client=kalshi,
        ...     initial_balance=10000.0,
        ...     persist_path=Path("paper_state.json"),
        ... )
        >>> paper.start()
        >>>
        >>> # Get live market data
        >>> market = paper.get_market_data("KXNBA-TEAM-YES")
        >>> print(f"Live bid/ask: {market.bid}/{market.ask}")
        >>>
        >>> # Place paper order
        >>> order_id = paper.place_order("KXNBA-TEAM-YES", "BID", 0.45, 50)
        >>> status = paper.get_order_status(order_id)
        >>>
        >>> paper.stop()
        >>> paper.save_state()

    Attributes:
        initial_balance: Starting cash balance
        fill_probability: Probability of fills occurring (for simulation variance)
        poll_interval_ms: Background polling interval in milliseconds
    """

    def __init__(
        self,
        market_data_client: APIClient,
        initial_balance: float = 10000.0,
        persist_path: Optional[Path] = None,
        fill_probability: float = 0.95,
        poll_interval_ms: int = 1000,
    ) -> None:
        """Initialize paper trading client.

        Args:
            market_data_client: Real API client for live market data
            initial_balance: Starting cash balance in dollars
            persist_path: Optional path for JSON state persistence
            fill_probability: Probability of resting order fills (0-1)
            poll_interval_ms: Background fill check interval in milliseconds
        """
        if initial_balance <= 0:
            raise ValueError(f"initial_balance must be positive, got {initial_balance}")
        if not 0.0 <= fill_probability <= 1.0:
            raise ValueError(f"fill_probability must be 0-1, got {fill_probability}")
        if poll_interval_ms <= 0:
            raise ValueError(
                f"poll_interval_ms must be positive, got {poll_interval_ms}"
            )

        self._market_data_client = market_data_client
        self._initial_balance = initial_balance
        self._current_balance = initial_balance
        self._persist_path = persist_path
        self._fill_probability = fill_probability
        self._poll_interval_s = poll_interval_ms / 1000.0

        # Internal state
        self._orders: Dict[str, PaperOrder] = {}
        self._positions: Dict[str, PaperPosition] = {}
        self._fills: List[PaperFill] = []
        self._last_market_data: Dict[str, MarketState] = {}

        # Threading
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Stats
        self._started_at: Optional[datetime] = None

    # =========================================================================
    # APIClient Interface Implementation
    # =========================================================================

    def place_order(
        self,
        ticker: str,
        side: str,
        price: float,
        size: int,
    ) -> str:
        """Place a paper order.

        Orders may fill immediately if marketable, or rest in the book
        to be filled later when the market moves.

        Args:
            ticker: Market identifier
            side: 'BID' or 'ASK' (also accepts 'buy'/'sell')
            price: Limit price (0-1 as probability)
            size: Number of contracts

        Returns:
            Generated order ID

        Raises:
            ValueError: If parameters are invalid
        """
        # Validate inputs
        if not ticker:
            raise ValueError("ticker cannot be empty")
        if price < 0 or price > 1:
            raise ValueError(f"price must be 0-1, got {price}")
        if size <= 0:
            raise ValueError(f"size must be positive, got {size}")

        # Normalize side
        normalized_side = self._normalize_side(side)

        # Check balance for buying
        order_cost = price * size
        with self._lock:
            if normalized_side == "BID" and order_cost > self._current_balance:
                raise ValueError(
                    f"Insufficient balance: need ${order_cost:.2f}, have ${self._current_balance:.2f}"
                )

        # Generate order ID
        order_id = f"paper_{uuid.uuid4().hex[:12]}"
        now = utc_now()

        # Create order
        order = PaperOrder(
            order_id=order_id,
            ticker=ticker,
            side=normalized_side,
            price=price,
            size=size,
            created_at=now,
            updated_at=now,
        )

        with self._lock:
            self._orders[order_id] = order

        logger.info(
            "[PAPER] Order placed: %s %s %d @ %.4f (id=%s)",
            normalized_side,
            ticker,
            size,
            price,
            order_id,
        )

        # Publish to dashboard
        self._publish_order_to_dashboard(order)

        # Check for immediate fill
        self._check_order_fill(order_id)

        return order_id

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a paper order.

        Args:
            order_id: Order ID to cancel

        Returns:
            True if canceled, False if not found or already filled/canceled
        """
        with self._lock:
            order = self._orders.get(order_id)
            if not order:
                return False

            if not order.is_open:
                return False

            order.status = "CANCELED"
            order.updated_at = utc_now()

            # Publish to dashboard
            self._publish_order_to_dashboard(order)

        logger.info("[PAPER] Order canceled: %s", order_id)
        return True

    def get_order_status(self, order_id: str) -> dict:
        """Get status of a paper order.

        Args:
            order_id: Order ID to check

        Returns:
            Dictionary with order status details

        Raises:
            ValueError: If order not found
        """
        with self._lock:
            order = self._orders.get(order_id)
            if not order:
                raise ValueError(f"Order not found: {order_id}")

            return {
                "order_id": order.order_id,
                "status": order.status,
                "filled_size": order.filled_size,
                "remaining_size": order.remaining_size,
                "fills": [
                    {
                        "fill_id": f.fill_id,
                        "price": f.price,
                        "size": f.size,
                        "fee": f.fee,
                        "timestamp": f.timestamp.isoformat(),
                    }
                    for f in order.fills
                ],
            }

    def get_market_data(self, ticker: str) -> MarketState:
        """Get live market data from the real exchange.

        Args:
            ticker: Market identifier

        Returns:
            Current MarketState from the real exchange
        """
        market = self._market_data_client.get_market_data(ticker)

        with self._lock:
            self._last_market_data[ticker] = market

        return market

    # =========================================================================
    # Paper Trading Methods
    # =========================================================================

    def start(self) -> "PaperTradingClient":
        """Start background fill checking.

        Launches a thread that periodically checks resting orders
        against current market prices for fills.

        Returns:
            Self for chaining
        """
        if self._thread is not None and self._thread.is_alive():
            return self

        self._stop_event.clear()
        self._started_at = utc_now()
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="PaperTradingClient",
        )
        self._thread.start()
        logger.info(
            "[PAPER] Started background fill checking (interval: %.1fs)",
            self._poll_interval_s,
        )
        return self

    def stop(self) -> None:
        """Stop background fill checking."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            logger.info("[PAPER] Stopped background fill checking")

    def check_fills(self) -> List[PaperFill]:
        """Manually check all open orders for fills.

        Returns:
            List of new fills that occurred
        """
        new_fills: List[PaperFill] = []

        with self._lock:
            open_orders = [oid for oid, order in self._orders.items() if order.is_open]

        for order_id in open_orders:
            fills = self._check_order_fill(order_id)
            new_fills.extend(fills)

        return new_fills

    def get_balance(self) -> float:
        """Get current cash balance.

        Returns:
            Current balance in dollars
        """
        with self._lock:
            return self._current_balance

    def get_positions(self) -> Dict[str, PaperPosition]:
        """Get all positions.

        Returns:
            Dictionary mapping ticker to PaperPosition
        """
        with self._lock:
            return {k: PaperPosition(**v.to_dict()) for k, v in self._positions.items()}

    def get_fills(
        self,
        ticker: Optional[str] = None,
        limit: int = 100,
    ) -> List[PaperFill]:
        """Get fills, optionally filtered by ticker.

        Args:
            ticker: Optional ticker to filter by
            limit: Maximum number of fills to return

        Returns:
            List of PaperFill objects, most recent first
        """
        with self._lock:
            fills = self._fills
            if ticker:
                fills = [f for f in fills if f.ticker == ticker]
            return sorted(fills, key=lambda f: f.timestamp, reverse=True)[:limit]

    def get_pnl_report(self) -> dict:
        """Get comprehensive P&L report.

        Returns:
            Dictionary with:
            - initial_balance: Starting balance
            - current_balance: Current cash balance
            - total_realized_pnl: Sum of realized P&L from all positions
            - total_unrealized_pnl: Sum of unrealized P&L from open positions
            - total_pnl: Realized + unrealized
            - total_fees: Total fees paid
            - positions: Dict of position details
        """
        with self._lock:
            total_realized = sum(p.realized_pnl for p in self._positions.values())
            total_fees = sum(p.total_fees for p in self._positions.values())

            # Calculate unrealized P&L based on last known market prices
            total_unrealized = 0.0
            position_details = {}

            for ticker, position in self._positions.items():
                market = self._last_market_data.get(ticker)
                unrealized = 0.0

                if market and position.size != 0:
                    # Use mid price for unrealized P&L
                    current_price = market.mid
                    # P&L = (current - entry) * size for longs
                    # For shorts (negative size), this naturally inverts
                    unrealized = (
                        current_price - position.avg_entry_price
                    ) * position.size

                total_unrealized += unrealized

                position_details[ticker] = {
                    "size": position.size,
                    "avg_entry_price": position.avg_entry_price,
                    "realized_pnl": position.realized_pnl,
                    "unrealized_pnl": unrealized,
                    "fees": position.total_fees,
                }

            return {
                "initial_balance": self._initial_balance,
                "current_balance": self._current_balance,
                "total_realized_pnl": total_realized,
                "total_unrealized_pnl": total_unrealized,
                "total_pnl": total_realized + total_unrealized,
                "total_fees": total_fees,
                "positions": position_details,
            }

    def save_state(self) -> None:
        """Save current state to JSON file.

        Raises:
            ValueError: If no persist_path was configured
        """
        if not self._persist_path:
            raise ValueError("No persist_path configured")

        with self._lock:
            state = {
                "version": 1,
                "saved_at": utc_now().isoformat(),
                "initial_balance": self._initial_balance,
                "current_balance": self._current_balance,
                "orders": {oid: o.to_dict() for oid, o in self._orders.items()},
                "positions": {t: p.to_dict() for t, p in self._positions.items()},
                "fills": [f.to_dict() for f in self._fills],
            }

        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._persist_path, "w") as f:
            json.dump(state, f, indent=2)

        logger.info("[PAPER] State saved to %s", self._persist_path)

    def load_state(self) -> None:
        """Load state from JSON file.

        Raises:
            ValueError: If no persist_path was configured
            FileNotFoundError: If state file doesn't exist
        """
        if not self._persist_path:
            raise ValueError("No persist_path configured")

        if not self._persist_path.exists():
            raise FileNotFoundError(f"State file not found: {self._persist_path}")

        with open(self._persist_path) as f:
            state = json.load(f)

        with self._lock:
            self._initial_balance = state["initial_balance"]
            self._current_balance = state["current_balance"]
            self._orders = {
                oid: PaperOrder.from_dict(data)
                for oid, data in state.get("orders", {}).items()
            }
            self._positions = {
                ticker: PaperPosition.from_dict(data)
                for ticker, data in state.get("positions", {}).items()
            }
            self._fills = [PaperFill.from_dict(data) for data in state.get("fills", [])]

        logger.info(
            "[PAPER] State loaded from %s (balance: $%.2f)",
            self._persist_path,
            self._current_balance,
        )

    def reset(self) -> None:
        """Reset all state to initial values."""
        with self._lock:
            self._current_balance = self._initial_balance
            self._orders.clear()
            self._positions.clear()
            self._fills.clear()
            self._last_market_data.clear()

        logger.info("[PAPER] State reset (balance: $%.2f)", self._initial_balance)

    def print_summary(self) -> None:
        """Print a summary of the paper trading session."""
        report = self.get_pnl_report()

        print("\n" + "=" * 60)
        print("PAPER TRADING SUMMARY")
        print("=" * 60)
        print(f"Initial Balance:    ${report['initial_balance']:>12,.2f}")
        print(f"Current Balance:    ${report['current_balance']:>12,.2f}")
        print("-" * 60)
        print(f"Realized P&L:       ${report['total_realized_pnl']:>12,.2f}")
        print(f"Unrealized P&L:     ${report['total_unrealized_pnl']:>12,.2f}")
        print(f"Total P&L:          ${report['total_pnl']:>12,.2f}")
        print(f"Total Fees:         ${report['total_fees']:>12,.2f}")
        print("-" * 60)

        if report["positions"]:
            print("\nPositions:")
            for ticker, pos in report["positions"].items():
                if pos["size"] != 0:
                    print(
                        f"  {ticker}: {pos['size']:+d} contracts "
                        f"@ {pos['avg_entry_price']:.4f} "
                        f"(unrealized: ${pos['unrealized_pnl']:+.2f})"
                    )

        with self._lock:
            open_orders = [o for o in self._orders.values() if o.is_open]
            if open_orders:
                print(f"\nOpen Orders: {len(open_orders)}")
                for order in open_orders[:5]:  # Show first 5
                    print(
                        f"  {order.order_id[:12]}... {order.side} "
                        f"{order.remaining_size}/{order.size} @ {order.price:.4f}"
                    )
                if len(open_orders) > 5:
                    print(f"  ... and {len(open_orders) - 5} more")

            print(f"\nTotal Fills: {len(self._fills)}")

        print("=" * 60)

    # =========================================================================
    # Internal Methods
    # =========================================================================

    def _normalize_side(self, side: str) -> str:
        """Normalize order side to BID/ASK."""
        side_upper = side.upper()
        if side_upper in ("BUY", "BID"):
            return "BID"
        elif side_upper in ("SELL", "ASK"):
            return "ASK"
        else:
            raise ValueError(
                f"Invalid side: {side}, must be 'BID', 'ASK', 'buy', or 'sell'"
            )

    def _poll_loop(self) -> None:
        """Background polling loop for fill checking."""
        while not self._stop_event.is_set():
            try:
                # Refresh market data for tickers with open orders
                with self._lock:
                    tickers = {o.ticker for o in self._orders.values() if o.is_open}

                for ticker in tickers:
                    try:
                        self.get_market_data(ticker)
                    except Exception as e:
                        logger.debug(
                            "Failed to refresh market data for %s: %s", ticker, e
                        )

                # Check for fills
                self.check_fills()

            except Exception as e:
                logger.error("[PAPER] Error in poll loop: %s", e)

            self._stop_event.wait(self._poll_interval_s)

    def _check_order_fill(self, order_id: str) -> List[PaperFill]:
        """Check if an order should fill and execute the fill.

        Args:
            order_id: Order ID to check

        Returns:
            List of fills that occurred (may be empty)
        """
        new_fills: List[PaperFill] = []

        with self._lock:
            order = self._orders.get(order_id)
            if not order or not order.is_open:
                return new_fills

            market = self._last_market_data.get(order.ticker)
            if not market:
                # Try to fetch market data
                pass  # Will be fetched on next poll cycle

        # Get fresh market data if not cached
        if not market:
            try:
                market = self.get_market_data(order.ticker)
            except Exception as e:
                logger.debug("Cannot check fill for %s: %s", order_id, e)
                return new_fills

        # Determine if order should fill
        fill_price: Optional[float] = None
        is_maker = False

        with self._lock:
            order = self._orders.get(order_id)
            if not order or not order.is_open:
                return new_fills

            if order.side == "BID":
                # BID fills if price >= market ask (taking liquidity)
                # or if market ask drops to/below order price (resting order fills)
                if order.price >= market.ask:
                    fill_price = market.ask
                    is_maker = False  # Crossing the spread = taker
                elif market.ask <= order.price:
                    # Resting bid fills when ask drops to our price
                    fill_price = order.price
                    is_maker = True
            else:  # ASK
                # ASK fills if price <= market bid (taking liquidity)
                # or if market bid rises to/above order price (resting order fills)
                if order.price <= market.bid:
                    fill_price = market.bid
                    is_maker = False  # Crossing the spread = taker
                elif market.bid >= order.price:
                    # Resting ask fills when bid rises to our price
                    fill_price = order.price
                    is_maker = True

        if fill_price is not None:
            # Apply fill probability for resting orders
            if is_maker:
                import random

                if random.random() > self._fill_probability:
                    return new_fills

            # Execute the fill
            fill = self._execute_fill(order_id, fill_price, is_maker)
            if fill:
                new_fills.append(fill)

        return new_fills

    def _execute_fill(
        self,
        order_id: str,
        fill_price: float,
        is_maker: bool,
    ) -> Optional[PaperFill]:
        """Execute a fill for an order.

        Args:
            order_id: Order to fill
            fill_price: Execution price
            is_maker: Whether this is a maker fill (affects fees)

        Returns:
            The fill that occurred, or None if fill failed
        """
        with self._lock:
            order = self._orders.get(order_id)
            if not order or not order.is_open:
                return None

            fill_size = order.remaining_size
            fee = calculate_fee(fill_price, fill_size, maker=is_maker)

            # Create fill
            fill = PaperFill(
                fill_id=f"fill_{uuid.uuid4().hex[:12]}",
                order_id=order_id,
                ticker=order.ticker,
                side=order.side,
                price=fill_price,
                size=fill_size,
                fee=fee,
                timestamp=utc_now(),
            )

            # Update order
            order.fills.append(fill)
            order.filled_size += fill_size
            order.updated_at = utc_now()

            if order.filled_size >= order.size:
                order.status = "FILLED"
            else:
                order.status = "PARTIALLY_FILLED"

            # Update balance and position
            self._update_balance_and_position(fill)
            self._fills.append(fill)

        logger.info(
            "[PAPER] Fill: %s %s %d @ %.4f (fee: $%.2f, %s)",
            fill.side,
            fill.ticker,
            fill.size,
            fill.price,
            fill.fee,
            "maker" if is_maker else "taker",
        )

        return fill

    def _update_balance_and_position(self, fill: PaperFill) -> None:
        """Update balance and position after a fill.

        Must be called with lock held.

        Args:
            fill: The fill to process
        """
        ticker = fill.ticker

        # Get or create position
        if ticker not in self._positions:
            self._positions[ticker] = PaperPosition(ticker=ticker)

        position = self._positions[ticker]
        position.total_fees += fill.fee

        if fill.side == "BID":
            # Buying: deduct cost from balance
            cost = fill.price * fill.size + fill.fee
            self._current_balance -= cost

            if position.size >= 0:
                # Adding to long or opening long
                total_cost = (position.avg_entry_price * position.size) + (
                    fill.price * fill.size
                )
                new_size = position.size + fill.size
                position.avg_entry_price = (
                    total_cost / new_size if new_size > 0 else 0.0
                )
                position.size = new_size
            else:
                # Closing short position
                # Realized P&L = (entry - exit) * contracts for shorts
                close_size = min(fill.size, abs(position.size))
                pnl = (position.avg_entry_price - fill.price) * close_size
                position.realized_pnl += pnl

                remaining_buy = fill.size - close_size
                position.size += fill.size

                if remaining_buy > 0 and position.size > 0:
                    # We've flipped from short to long
                    position.avg_entry_price = fill.price
                elif position.size == 0:
                    position.avg_entry_price = 0.0

        else:  # ASK
            # Selling: add proceeds to balance
            proceeds = fill.price * fill.size - fill.fee
            self._current_balance += proceeds

            if position.size <= 0:
                # Adding to short or opening short
                total_cost = (position.avg_entry_price * abs(position.size)) + (
                    fill.price * fill.size
                )
                new_size = position.size - fill.size
                position.avg_entry_price = (
                    total_cost / abs(new_size) if new_size != 0 else 0.0
                )
                position.size = new_size
            else:
                # Closing long position
                # Realized P&L = (exit - entry) * contracts for longs
                close_size = min(fill.size, position.size)
                pnl = (fill.price - position.avg_entry_price) * close_size
                position.realized_pnl += pnl

                remaining_sell = fill.size - close_size
                position.size -= fill.size

                if remaining_sell > 0 and position.size < 0:
                    # We've flipped from long to short
                    position.avg_entry_price = fill.price
                elif position.size == 0:
                    position.avg_entry_price = 0.0

        # Publish to dashboard
        self._publish_fill_to_dashboard(fill)
        self._publish_position_to_dashboard(ticker)
        self._publish_summary_to_dashboard()

    def _publish_order_to_dashboard(self, order: PaperOrder) -> None:
        """Publish order update to dashboard (no-op, dashboard removed)."""
        pass

    def _publish_position_to_dashboard(self, ticker: str) -> None:
        """Publish position update to dashboard (no-op, dashboard removed)."""
        pass

    def _publish_fill_to_dashboard(self, fill: PaperFill) -> None:
        """Publish fill to dashboard (no-op, dashboard removed)."""
        pass

    def _publish_summary_to_dashboard(self) -> None:
        """Publish P&L summary to dashboard (no-op, dashboard removed)."""
        pass
