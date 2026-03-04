"""Position tracking and P&L accounting for the unified backtest framework.

PositionTracker maintains per-ticker positions, tracks the bankroll curve,
and computes portfolio-level metrics (drawdown, Sharpe, etc.) at settlement.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.core.models import Fill


@dataclass
class PositionRecord:
    """Tracks one side of a single ticker position."""

    ticker: str
    size: int = 0  # positive = long YES, negative = short
    avg_entry: float = 0.0  # weighted average entry price (dollars)
    realized_pnl: float = 0.0


class PositionTracker:
    """Tracks positions, bankroll curve, and computes settlement P&L.

    All prices are in *dollars* (0.0-1.0 for prediction markets).
    """

    def __init__(self, initial_bankroll: float = 10000.0):
        self._initial_bankroll = initial_bankroll
        self._bankroll = initial_bankroll
        self._positions: Dict[str, PositionRecord] = {}
        self._bankroll_curve: List[Tuple[datetime, float]] = []
        self._total_fees: float = 0.0
        self._peak_bankroll: float = initial_bankroll
        self._max_drawdown: float = 0.0
        self._fill_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_fill(self, fill: Fill) -> None:
        """Update positions and bankroll from a Fill."""
        pos = self._positions.setdefault(
            fill.ticker,
            PositionRecord(ticker=fill.ticker),
        )

        cost = fill.price * fill.size  # notional outlay
        self._total_fees += fill.fee
        self._fill_count += 1

        if fill.side == "BID":
            # Buying contracts
            if pos.size >= 0:
                # Adding to long or opening long
                total_cost = pos.avg_entry * pos.size + cost
                pos.size += fill.size
                pos.avg_entry = total_cost / pos.size if pos.size else 0.0
            else:
                # Closing short
                close_qty = min(fill.size, abs(pos.size))
                pnl = (pos.avg_entry - fill.price) * close_qty
                pos.realized_pnl += pnl
                pos.size += fill.size
                if pos.size > 0:
                    pos.avg_entry = fill.price
                elif pos.size == 0:
                    pos.avg_entry = 0.0
            self._bankroll -= cost + fill.fee
        else:
            # Selling contracts
            if pos.size <= 0:
                # Adding to short or opening short
                total_cost = abs(pos.avg_entry * pos.size) + cost
                pos.size -= fill.size
                pos.avg_entry = total_cost / abs(pos.size) if pos.size else 0.0
            else:
                # Closing long
                close_qty = min(fill.size, pos.size)
                pnl = (fill.price - pos.avg_entry) * close_qty
                pos.realized_pnl += pnl
                pos.size -= fill.size
                if pos.size < 0:
                    pos.avg_entry = fill.price
                elif pos.size == 0:
                    pos.avg_entry = 0.0
            self._bankroll += fill.price * fill.size - fill.fee

        # Track bankroll curve & drawdown
        ts = fill.timestamp or datetime.now()
        self._bankroll_curve.append((ts, self._bankroll))
        if self._bankroll > self._peak_bankroll:
            self._peak_bankroll = self._bankroll
        if self._peak_bankroll > 0:
            dd = (self._peak_bankroll - self._bankroll) / self._peak_bankroll
            if dd > self._max_drawdown:
                self._max_drawdown = dd

    def settle(self, settlements: Dict[str, Optional[float]]) -> None:
        """Settle all open positions at the given settlement prices.

        Args:
            settlements: ticker -> settlement value (1.0 = YES, 0.0 = NO).
                         None values are treated as 0 (total loss).
        """
        for ticker, pos in self._positions.items():
            if pos.size == 0:
                continue
            settle_price = settlements.get(ticker)
            if settle_price is None:
                settle_price = 0.0

            if pos.size > 0:
                pnl = (settle_price - pos.avg_entry) * pos.size
            else:
                pnl = (pos.avg_entry - settle_price) * abs(pos.size)

            pos.realized_pnl += pnl
            self._bankroll += settle_price * pos.size
            pos.size = 0
            pos.avg_entry = 0.0

        ts = datetime.now()
        self._bankroll_curve.append((ts, self._bankroll))
        if self._bankroll > self._peak_bankroll:
            self._peak_bankroll = self._bankroll
        if self._peak_bankroll > 0:
            dd = (self._peak_bankroll - self._bankroll) / self._peak_bankroll
            if dd > self._max_drawdown:
                self._max_drawdown = dd

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_position(self, ticker: str) -> int:
        pos = self._positions.get(ticker)
        return pos.size if pos else 0

    def get_total_position(self) -> int:
        return sum(abs(p.size) for p in self._positions.values())

    @property
    def bankroll(self) -> float:
        return self._bankroll

    @property
    def bankroll_curve(self) -> List[Tuple[datetime, float]]:
        return list(self._bankroll_curve)

    def compute_metrics(self) -> Dict[str, Any]:
        """Return a dict of portfolio-level metrics."""
        total_realized = sum(p.realized_pnl for p in self._positions.values())
        net_pnl = self._bankroll - self._initial_bankroll
        return_pct = (
            (net_pnl / self._initial_bankroll * 100) if self._initial_bankroll else 0.0
        )

        return {
            "initial_bankroll": self._initial_bankroll,
            "final_bankroll": self._bankroll,
            "net_pnl": net_pnl,
            "return_pct": return_pct,
            "total_realized_pnl": total_realized,
            "total_fees": self._total_fees,
            "max_drawdown_pct": self._max_drawdown * 100,
            "peak_bankroll": self._peak_bankroll,
            "fill_count": self._fill_count,
            "open_positions": {
                t: p.size for t, p in self._positions.items() if p.size != 0
            },
        }
