"""Permutation test for statistical significance of backtest results.

Shuffles win/loss labels among trades while preserving magnitude distribution,
then computes p-values for net PnL and win rate.
"""

import random
from dataclasses import dataclass
from typing import List, Optional

from .trade_analysis import TradePnL


@dataclass
class PermutationConfig:
    n_permutations: int = 10000
    seed: Optional[int] = None


@dataclass
class PermutationResult:
    """Permutation test results."""

    n_permutations: int
    observed_pnl: float
    observed_win_rate: float
    pnl_p_value: float
    win_rate_p_value: float
    is_significant_5pct: bool
    is_significant_1pct: bool

    def report(self) -> str:
        lines = [
            f"--- Permutation Test (n={self.n_permutations}) ---",
            f"  Observed PnL:          ${self.observed_pnl:+.4f}",
            f"  Observed win rate:     {self.observed_win_rate:.1%}",
            f"  PnL p-value:           {self.pnl_p_value:.4f}",
            f"  Win rate p-value:      {self.win_rate_p_value:.4f}",
            f"  Significant (5%):      {'YES' if self.is_significant_5pct else 'NO'}",
            f"  Significant (1%):      {'YES' if self.is_significant_1pct else 'NO'}",
        ]
        return "\n".join(lines)


class PermutationTester:
    """Test statistical significance via label permutation."""

    def __init__(self, config: Optional[PermutationConfig] = None):
        self._config = config or PermutationConfig()

    def run(self, trades: List[TradePnL]) -> Optional[PermutationResult]:
        """Run permutation test.

        Shuffles win/loss assignments among trades, preserving the
        distribution of absolute magnitudes.

        Args:
            trades: Trade PnL records from the backtest.

        Returns:
            PermutationResult or None if insufficient trades.
        """
        if len(trades) < 2:
            return None

        rng = random.Random(self._config.seed)
        n_perms = self._config.n_permutations
        n_trades = len(trades)

        # Observed
        observed_pnl = sum(t.net_pnl for t in trades)
        observed_wr = sum(1 for t in trades if t.is_winner) / n_trades

        # Absolute magnitudes and signs
        magnitudes = [abs(t.net_pnl) for t in trades]
        signs = [1 if t.is_winner else -1 for t in trades]

        pnl_exceed = 0
        wr_exceed = 0

        for _ in range(n_perms):
            # Shuffle signs
            shuffled_signs = list(signs)
            rng.shuffle(shuffled_signs)

            perm_pnl = sum(m * s for m, s in zip(magnitudes, shuffled_signs))
            perm_wr = sum(1 for s in shuffled_signs if s > 0) / n_trades

            if perm_pnl >= observed_pnl:
                pnl_exceed += 1
            if perm_wr >= observed_wr:
                wr_exceed += 1

        pnl_p = pnl_exceed / n_perms
        wr_p = wr_exceed / n_perms

        return PermutationResult(
            n_permutations=n_perms,
            observed_pnl=observed_pnl,
            observed_win_rate=observed_wr,
            pnl_p_value=pnl_p,
            win_rate_p_value=wr_p,
            is_significant_5pct=pnl_p < 0.05,
            is_significant_1pct=pnl_p < 0.01,
        )
