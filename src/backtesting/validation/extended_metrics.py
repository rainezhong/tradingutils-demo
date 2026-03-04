"""Risk-adjusted performance metrics computed from BacktestResult.

Provides Sharpe, Sortino, Calmar, profit factor, Ulcer index, and more.
All computations are pure Python (no numpy).
"""

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from src.core.models import Fill

from .trade_analysis import (
    TradePnL,
    TradeDistribution,
    compute_trade_pnls,
    compute_trade_distribution,
)


@dataclass
class ExtendedMetrics:
    """Extended risk-adjusted metrics for a backtest result."""

    # Risk-adjusted returns
    sharpe_ratio: Optional[float]
    sortino_ratio: Optional[float]
    calmar_ratio: Optional[float]

    # Trade quality
    profit_factor: Optional[float]
    expected_value: float  # avg net PnL per trade
    avg_winner: float
    avg_loser: float
    payoff_ratio: Optional[float]  # avg_winner / abs(avg_loser)

    # Drawdown
    max_drawdown_pct: float
    ulcer_index: Optional[float]

    # Distribution
    distribution: Optional[TradeDistribution]

    # Trade list (for downstream consumers)
    trades: List[TradePnL]

    @staticmethod
    def compute(
        fills: List[Fill],
        settlements: Dict[str, Optional[float]],
        bankroll_curve: List[Tuple[datetime, float]],
    ) -> "ExtendedMetrics":
        """Compute extended metrics from backtest result data.

        Args:
            fills: List of Fill objects.
            settlements: ticker -> settlement price.
            bankroll_curve: List of (timestamp, bankroll) tuples.

        Returns:
            ExtendedMetrics instance.
        """
        trades = compute_trade_pnls(fills, settlements)
        distribution = compute_trade_distribution(trades)

        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]

        avg_winner = (sum(t.net_pnl for t in winners) / len(winners)) if winners else 0.0
        avg_loser = (sum(t.net_pnl for t in losers) / len(losers)) if losers else 0.0
        ev = (sum(t.net_pnl for t in trades) / len(trades)) if trades else 0.0

        # Profit factor: gross_wins / abs(gross_losses)
        gross_wins = sum(t.net_pnl for t in winners)
        gross_losses = abs(sum(t.net_pnl for t in losers))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else None

        # Payoff ratio
        payoff_ratio = (avg_winner / abs(avg_loser)) if avg_loser < 0 else None

        # Daily returns from bankroll curve
        daily_returns = _compute_daily_returns(bankroll_curve)

        sharpe = _sharpe_ratio(daily_returns) if len(daily_returns) >= 2 else None
        sortino = _sortino_ratio(daily_returns) if len(daily_returns) >= 2 else None

        # Drawdown
        max_dd = _max_drawdown_pct(bankroll_curve)
        ulcer = _ulcer_index(bankroll_curve) if len(bankroll_curve) >= 2 else None

        # Calmar = annualized return / max drawdown
        calmar = None
        if max_dd > 0 and len(daily_returns) >= 2:
            total_return = sum(daily_returns)
            n_days = len(daily_returns)
            if n_days > 0:
                ann_return = total_return * (252 / n_days)
                calmar = ann_return / (max_dd / 100.0)

        return ExtendedMetrics(
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            profit_factor=profit_factor,
            expected_value=ev,
            avg_winner=avg_winner,
            avg_loser=avg_loser,
            payoff_ratio=payoff_ratio,
            max_drawdown_pct=max_dd,
            ulcer_index=ulcer,
            distribution=distribution,
            trades=trades,
        )

    def report(self) -> str:
        """Human-readable multi-line report."""
        lines = [
            "--- Extended Metrics ---",
        ]

        def _fmt(v: Optional[float], fmt: str = ".2f") -> str:
            return f"{v:{fmt}}" if v is not None else "N/A"

        lines.append(f"  Sharpe ratio:          {_fmt(self.sharpe_ratio)}")
        lines.append(f"  Sortino ratio:         {_fmt(self.sortino_ratio)}")
        lines.append(f"  Calmar ratio:          {_fmt(self.calmar_ratio)}")
        lines.append(f"  Profit factor:         {_fmt(self.profit_factor)}")
        lines.append(f"  EV per trade:          ${self.expected_value:+.4f}")
        lines.append(f"  Avg winner:            ${self.avg_winner:+.4f}")
        lines.append(f"  Avg loser:             ${self.avg_loser:+.4f}")
        lines.append(f"  Payoff ratio:          {_fmt(self.payoff_ratio)}")
        lines.append(f"  Max drawdown:          {self.max_drawdown_pct:.1f}%")
        lines.append(f"  Ulcer index:           {_fmt(self.ulcer_index)}")

        dist = self.distribution
        if dist:
            lines.append("")
            lines.append("--- Trade Distribution ---")
            lines.append(f"  Count:                 {dist.count}")
            lines.append(f"  Mean PnL:              ${dist.mean:+.4f}")
            lines.append(f"  Median PnL:            ${dist.median:+.4f}")
            lines.append(f"  Std dev:               ${dist.std:.4f}")
            lines.append(f"  Skewness:              {dist.skewness:.2f}")
            lines.append(f"  Kurtosis (excess):     {dist.kurtosis:.2f}")
            lines.append(f"  5th pctile:            ${dist.pct_5:+.4f}")
            lines.append(f"  25th pctile:           ${dist.pct_25:+.4f}")
            lines.append(f"  75th pctile:           ${dist.pct_75:+.4f}")
            lines.append(f"  95th pctile:           ${dist.pct_95:+.4f}")
            lines.append(f"  Max consec wins:       {dist.max_consecutive_wins}")
            lines.append(f"  Max consec losses:     {dist.max_consecutive_losses}")

        return "\n".join(lines)


def _compute_daily_returns(
    bankroll_curve: List[Tuple[datetime, float]],
) -> List[float]:
    """Compute daily percentage returns from a bankroll curve.

    Groups bankroll snapshots by date and computes day-over-day returns.
    If all data falls within a single day, returns per-observation returns.
    """
    if len(bankroll_curve) < 2:
        return []

    # Group by date
    daily: Dict[str, float] = {}
    for ts, val in bankroll_curve:
        if isinstance(ts, datetime):
            key = ts.strftime("%Y-%m-%d")
        else:
            key = str(ts)
        daily[key] = val  # last value of the day

    values = list(daily.values())

    if len(values) < 2:
        # All in one day — use per-observation returns
        values = [v for _, v in bankroll_curve]

    returns = []
    for i in range(1, len(values)):
        if values[i - 1] != 0:
            returns.append((values[i] - values[i - 1]) / abs(values[i - 1]))
    return returns


def _sharpe_ratio(returns: List[float], risk_free: float = 0.0) -> Optional[float]:
    """Annualized Sharpe ratio from a list of periodic returns."""
    if len(returns) < 2:
        return None
    n = len(returns)
    mean_r = sum(returns) / n - risk_free
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance)
    if std == 0:
        return None
    return (mean_r / std) * math.sqrt(252)


def _sortino_ratio(returns: List[float], risk_free: float = 0.0) -> Optional[float]:
    """Annualized Sortino ratio (downside deviation only)."""
    if len(returns) < 2:
        return None
    n = len(returns)
    mean_r = sum(returns) / n - risk_free
    downside = [min(0.0, r) for r in returns]
    down_var = sum(d ** 2 for d in downside) / n
    down_std = math.sqrt(down_var)
    if down_std == 0:
        return None
    return (mean_r / down_std) * math.sqrt(252)


def _max_drawdown_pct(bankroll_curve: List[Tuple[datetime, float]]) -> float:
    """Max drawdown as a percentage from a bankroll curve."""
    if not bankroll_curve:
        return 0.0
    peak = bankroll_curve[0][1]
    max_dd = 0.0
    for _, val in bankroll_curve:
        if val > peak:
            peak = val
        if peak > 0:
            dd = (peak - val) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _ulcer_index(bankroll_curve: List[Tuple[datetime, float]]) -> Optional[float]:
    """Ulcer Index: RMS of percentage drawdowns from peak.

    Measures both depth and duration of drawdowns.
    """
    if len(bankroll_curve) < 2:
        return None
    peak = bankroll_curve[0][1]
    sum_sq = 0.0
    n = 0
    for _, val in bankroll_curve:
        if val > peak:
            peak = val
        if peak > 0:
            dd_pct = (peak - val) / peak * 100
            sum_sq += dd_pct ** 2
            n += 1
    if n == 0:
        return None
    return math.sqrt(sum_sq / n)
