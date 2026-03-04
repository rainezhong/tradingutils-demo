"""Bootstrap confidence interval estimation for backtest metrics.

Resamples trades with replacement to compute confidence intervals on
net PnL, win rate, avg PnL per trade, Sharpe, profit factor, and max DD.
"""

import math
import random
from dataclasses import dataclass
from typing import List, Optional

from .trade_analysis import TradePnL


@dataclass
class ConfidenceInterval:
    """A single confidence interval for a metric."""

    lower: float
    upper: float
    point_estimate: float
    confidence_level: float  # e.g. 0.95


@dataclass
class BootstrapConfig:
    n_samples: int = 10000
    confidence_level: float = 0.95
    seed: Optional[int] = None


@dataclass
class BootstrapResult:
    """Confidence intervals for key metrics."""

    n_samples: int
    confidence_level: float

    net_pnl: ConfidenceInterval
    win_rate: ConfidenceInterval
    avg_pnl_per_trade: ConfidenceInterval
    sharpe: ConfidenceInterval
    profit_factor: ConfidenceInterval
    max_drawdown: ConfidenceInterval

    def report(self) -> str:
        lines = [
            f"--- Bootstrap CIs ({self.confidence_level:.0%}, n={self.n_samples}) ---",
        ]
        for name, ci in [
            ("Net PnL", self.net_pnl),
            ("Win rate", self.win_rate),
            ("Avg PnL/trade", self.avg_pnl_per_trade),
            ("Sharpe ratio", self.sharpe),
            ("Profit factor", self.profit_factor),
            ("Max drawdown %", self.max_drawdown),
        ]:
            lines.append(
                f"  {name:<20s} {ci.point_estimate:+.4f}  "
                f"[{ci.lower:+.4f}, {ci.upper:+.4f}]"
            )
        return "\n".join(lines)


class BootstrapAnalyzer:
    """Compute bootstrap confidence intervals from trade data."""

    def __init__(self, config: Optional[BootstrapConfig] = None):
        self._config = config or BootstrapConfig()

    def run(
        self,
        trades: List[TradePnL],
        initial_bankroll: float = 100.0,
    ) -> Optional[BootstrapResult]:
        """Run bootstrap analysis.

        Args:
            trades: Trade PnL records from the backtest.
            initial_bankroll: Starting bankroll for drawdown calc.

        Returns:
            BootstrapResult or None if insufficient trades.
        """
        if len(trades) < 2:
            return None

        rng = random.Random(self._config.seed)
        n = self._config.n_samples
        alpha = 1 - self._config.confidence_level
        n_trades = len(trades)

        # Observed metrics
        obs = _compute_sample_metrics(trades, initial_bankroll)

        # Bootstrap
        pnl_samples = []
        wr_samples = []
        avg_samples = []
        sharpe_samples = []
        pf_samples = []
        dd_samples = []

        for _ in range(n):
            sample = [rng.choice(trades) for _ in range(n_trades)]
            m = _compute_sample_metrics(sample, initial_bankroll)
            pnl_samples.append(m["net_pnl"])
            wr_samples.append(m["win_rate"])
            avg_samples.append(m["avg_pnl"])
            sharpe_samples.append(m["sharpe"])
            pf_samples.append(m["profit_factor"])
            dd_samples.append(m["max_dd"])

        def _ci(samples: List[float], point: float) -> ConfidenceInterval:
            s = sorted(samples)
            lo = s[int(len(s) * alpha / 2)]
            hi = s[int(len(s) * (1 - alpha / 2))]
            return ConfidenceInterval(lo, hi, point, self._config.confidence_level)

        return BootstrapResult(
            n_samples=n,
            confidence_level=self._config.confidence_level,
            net_pnl=_ci(pnl_samples, obs["net_pnl"]),
            win_rate=_ci(wr_samples, obs["win_rate"]),
            avg_pnl_per_trade=_ci(avg_samples, obs["avg_pnl"]),
            sharpe=_ci(sharpe_samples, obs["sharpe"]),
            profit_factor=_ci(pf_samples, obs["profit_factor"]),
            max_drawdown=_ci(dd_samples, obs["max_dd"]),
        )


def _compute_sample_metrics(
    trades: List[TradePnL],
    initial_bankroll: float,
) -> dict:
    """Compute metrics for a single bootstrap sample."""
    pnls = [t.net_pnl for t in trades]
    n = len(pnls)
    net = sum(pnls)
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]

    win_rate = len(winners) / n if n > 0 else 0.0
    avg_pnl = net / n if n > 0 else 0.0

    # Sharpe on per-trade returns
    if n >= 2:
        mean = avg_pnl
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) if std > 0 else 0.0
    else:
        sharpe = 0.0

    gross_wins = sum(winners)
    gross_losses = abs(sum(losers))
    pf = (gross_wins / gross_losses) if gross_losses > 0 else 0.0

    # Max drawdown
    bankroll = initial_bankroll
    peak = initial_bankroll
    max_dd = 0.0
    for p in pnls:
        bankroll += p
        if bankroll > peak:
            peak = bankroll
        if peak > 0:
            dd = (peak - bankroll) / peak * 100
            if dd > max_dd:
                max_dd = dd

    return {
        "net_pnl": net,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "sharpe": sharpe,
        "profit_factor": pf,
        "max_dd": max_dd,
    }
