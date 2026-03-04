"""Per-trade PnL computation and distribution statistics.

Foundation module for all validation analyses. Converts raw fills +
settlements into per-trade PnL records and computes distribution stats.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.core.models import Fill


@dataclass
class TradePnL:
    """Single trade P&L record."""

    ticker: str
    side: str  # 'BID' or 'ASK'
    price: float
    size: int
    fee: float
    settlement: Optional[float]
    gross_pnl: float  # before fees
    net_pnl: float  # after fees
    return_pct: float  # net_pnl / cost
    is_winner: bool


@dataclass
class TradeDistribution:
    """Distribution statistics for a set of trades."""

    count: int
    mean: float
    median: float
    std: float
    skewness: float
    kurtosis: float
    pct_5: float
    pct_25: float
    pct_75: float
    pct_95: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    total_pnl: float
    win_count: int
    loss_count: int


def compute_trade_pnls(
    fills: List[Fill],
    settlements: Dict[str, Optional[float]],
) -> List[TradePnL]:
    """Compute per-trade PnL from fills and settlement prices.

    Each fill is treated as an independent trade that settles at the
    market's settlement price.

    Args:
        fills: List of Fill objects from the backtest.
        settlements: ticker -> settlement value (1.0 = YES, 0.0 = NO).

    Returns:
        List of TradePnL records, one per fill with a known settlement.
    """
    trades = []
    for f in fills:
        settle = settlements.get(f.ticker)
        if settle is None:
            continue

        if f.side == "BID":
            gross = (settle - f.price) * f.size
            cost = f.price * f.size
        else:
            gross = (f.price - settle) * f.size
            cost = (1.0 - f.price) * f.size  # selling YES = buying NO

        net = gross - f.fee
        ret = (net / cost) if cost > 0 else 0.0

        trades.append(TradePnL(
            ticker=f.ticker,
            side=f.side,
            price=f.price,
            size=f.size,
            fee=f.fee,
            settlement=settle,
            gross_pnl=gross,
            net_pnl=net,
            return_pct=ret,
            is_winner=net > 0,
        ))
    return trades


def compute_trade_distribution(trades: List[TradePnL]) -> Optional[TradeDistribution]:
    """Compute distribution statistics from trade PnL records.

    Uses pure stdlib math (no numpy dependency).

    Returns:
        TradeDistribution or None if no trades.
    """
    if not trades:
        return None

    pnls = [t.net_pnl for t in trades]
    n = len(pnls)
    sorted_pnls = sorted(pnls)

    # Basic stats
    mean = sum(pnls) / n
    median = _percentile(sorted_pnls, 50)
    total = sum(pnls)

    # Variance and higher moments
    if n < 2:
        std = 0.0
        skew = 0.0
        kurt = 0.0
    else:
        variance = sum((x - mean) ** 2 for x in pnls) / (n - 1)
        std = math.sqrt(variance)

        if std > 0 and n >= 3:
            m3 = sum((x - mean) ** 3 for x in pnls) / n
            skew = m3 / (std ** 3)
        else:
            skew = 0.0

        if std > 0 and n >= 4:
            m4 = sum((x - mean) ** 4 for x in pnls) / n
            kurt = m4 / (std ** 4) - 3.0  # excess kurtosis
        else:
            kurt = 0.0

    # Consecutive wins/losses
    max_wins, max_losses = _max_consecutive(trades)

    # Win/loss counts
    win_count = sum(1 for t in trades if t.is_winner)
    loss_count = sum(1 for t in trades if not t.is_winner)

    return TradeDistribution(
        count=n,
        mean=mean,
        median=median,
        std=std,
        skewness=skew,
        kurtosis=kurt,
        pct_5=_percentile(sorted_pnls, 5),
        pct_25=_percentile(sorted_pnls, 25),
        pct_75=_percentile(sorted_pnls, 75),
        pct_95=_percentile(sorted_pnls, 95),
        max_consecutive_wins=max_wins,
        max_consecutive_losses=max_losses,
        total_pnl=total,
        win_count=win_count,
        loss_count=loss_count,
    )


def _percentile(sorted_data: List[float], pct: float) -> float:
    """Compute percentile from pre-sorted data using linear interpolation."""
    n = len(sorted_data)
    if n == 0:
        return 0.0
    if n == 1:
        return sorted_data[0]

    k = (pct / 100.0) * (n - 1)
    floor_k = int(k)
    ceil_k = min(floor_k + 1, n - 1)
    frac = k - floor_k
    return sorted_data[floor_k] + frac * (sorted_data[ceil_k] - sorted_data[floor_k])


def _max_consecutive(trades: List[TradePnL]) -> tuple:
    """Return (max_consecutive_wins, max_consecutive_losses)."""
    max_w = 0
    max_l = 0
    cur_w = 0
    cur_l = 0

    for t in trades:
        if t.is_winner:
            cur_w += 1
            cur_l = 0
        else:
            cur_l += 1
            cur_w = 0
        max_w = max(max_w, cur_w)
        max_l = max(max_l, cur_l)

    return max_w, max_l
