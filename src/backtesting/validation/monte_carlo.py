"""Monte Carlo simulation for backtest validation.

Three modes:
- SEQUENCE: shuffle trade order (tests order-dependence)
- RESAMPLE: sample trades with replacement (alternate histories)
- NULL: randomly flip trade signs (strongest null hypothesis)
"""

import math
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from .trade_analysis import TradePnL, compute_trade_pnls


class MonteCarloMode(Enum):
    SEQUENCE = "sequence"
    RESAMPLE = "resample"
    NULL = "null"


@dataclass
class MonteCarloConfig:
    n_simulations: int = 10000
    mode: MonteCarloMode = MonteCarloMode.SEQUENCE
    seed: Optional[int] = None


@dataclass
class MonteCarloResult:
    """Results from Monte Carlo simulation."""

    mode: MonteCarloMode
    n_simulations: int
    observed_pnl: float
    observed_max_dd: float

    # Terminal PnL distribution
    pnl_mean: float
    pnl_median: float
    pnl_std: float
    pnl_pct_5: float
    pnl_pct_25: float
    pnl_pct_75: float
    pnl_pct_95: float
    prob_negative: float  # P(terminal PnL < 0)

    # Max drawdown distribution
    dd_mean: float
    dd_median: float
    dd_pct_95: float
    prob_dd_over_50: float  # P(max DD > 50%)

    # Rank of observed vs simulations
    pnl_percentile: float  # where observed PnL falls in simulated dist

    def report(self) -> str:
        lines = [
            f"--- Monte Carlo ({self.mode.value}, n={self.n_simulations}) ---",
            f"  Observed PnL:          ${self.observed_pnl:+.4f}",
            f"  Observed max DD:       {self.observed_max_dd:.1f}%",
            "",
            "  Simulated PnL distribution:",
            f"    Mean:                ${self.pnl_mean:+.4f}",
            f"    Median:              ${self.pnl_median:+.4f}",
            f"    Std:                 ${self.pnl_std:.4f}",
            f"    5th pctile:          ${self.pnl_pct_5:+.4f}",
            f"    95th pctile:         ${self.pnl_pct_95:+.4f}",
            f"    P(negative):         {self.prob_negative:.1%}",
            "",
            "  Simulated max drawdown distribution:",
            f"    Mean:                {self.dd_mean:.1f}%",
            f"    Median:              {self.dd_median:.1f}%",
            f"    95th pctile:         {self.dd_pct_95:.1f}%",
            f"    P(DD > 50%):         {self.prob_dd_over_50:.1%}",
            "",
            f"  Observed PnL rank:     {self.pnl_percentile:.1f}th percentile",
        ]
        return "\n".join(lines)


class MonteCarloSimulator:
    """Run Monte Carlo simulations on backtest trades."""

    def __init__(self, config: Optional[MonteCarloConfig] = None):
        self._config = config or MonteCarloConfig()

    def run(
        self,
        trades: List[TradePnL],
        initial_bankroll: float = 100.0,
    ) -> Optional[MonteCarloResult]:
        """Run Monte Carlo simulation.

        Args:
            trades: List of TradePnL from the backtest.
            initial_bankroll: Starting bankroll for equity curve.

        Returns:
            MonteCarloResult or None if insufficient trades.
        """
        if len(trades) < 2:
            return None

        rng = random.Random(self._config.seed)
        mode = self._config.mode
        n_sims = self._config.n_simulations
        pnls = [t.net_pnl for t in trades]
        observed_pnl = sum(pnls)
        observed_dd = _max_dd_from_pnls(pnls, initial_bankroll)

        terminal_pnls = []
        max_dds = []

        for _ in range(n_sims):
            sim_pnls = _simulate(pnls, mode, rng)
            terminal_pnls.append(sum(sim_pnls))
            max_dds.append(_max_dd_from_pnls(sim_pnls, initial_bankroll))

        terminal_pnls.sort()
        max_dds.sort()

        n = len(terminal_pnls)
        prob_neg = sum(1 for p in terminal_pnls if p < 0) / n
        prob_dd50 = sum(1 for d in max_dds if d > 50) / n

        # Rank of observed PnL
        rank = sum(1 for p in terminal_pnls if p <= observed_pnl) / n * 100

        return MonteCarloResult(
            mode=mode,
            n_simulations=n_sims,
            observed_pnl=observed_pnl,
            observed_max_dd=observed_dd,
            pnl_mean=sum(terminal_pnls) / n,
            pnl_median=terminal_pnls[n // 2],
            pnl_std=_std(terminal_pnls),
            pnl_pct_5=terminal_pnls[int(n * 0.05)],
            pnl_pct_25=terminal_pnls[int(n * 0.25)],
            pnl_pct_75=terminal_pnls[int(n * 0.75)],
            pnl_pct_95=terminal_pnls[int(n * 0.95)],
            prob_negative=prob_neg,
            dd_mean=sum(max_dds) / n,
            dd_median=max_dds[n // 2],
            dd_pct_95=max_dds[int(n * 0.95)],
            prob_dd_over_50=prob_dd50,
            pnl_percentile=rank,
        )


def _simulate(
    pnls: List[float],
    mode: MonteCarloMode,
    rng: random.Random,
) -> List[float]:
    """Generate one simulated trade sequence."""
    if mode == MonteCarloMode.SEQUENCE:
        shuffled = list(pnls)
        rng.shuffle(shuffled)
        return shuffled
    elif mode == MonteCarloMode.RESAMPLE:
        return [rng.choice(pnls) for _ in range(len(pnls))]
    elif mode == MonteCarloMode.NULL:
        return [p * rng.choice([-1, 1]) for p in pnls]
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _max_dd_from_pnls(pnls: List[float], initial: float) -> float:
    """Compute max drawdown % from a sequence of PnL values."""
    bankroll = initial
    peak = initial
    max_dd = 0.0
    for p in pnls:
        bankroll += p
        if bankroll > peak:
            peak = bankroll
        if peak > 0:
            dd = (peak - bankroll) / peak * 100
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _std(values: List[float]) -> float:
    """Standard deviation of a list."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return math.sqrt(var)
