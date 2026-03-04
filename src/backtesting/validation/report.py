"""Validation suite orchestrator and unified report.

Convenience function that runs selected analyses on a BacktestResult
and returns a ValidationSuite with all results.
"""

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..metrics import BacktestResult
from .trade_analysis import TradePnL, compute_trade_pnls
from .extended_metrics import ExtendedMetrics
from .monte_carlo import MonteCarloConfig, MonteCarloResult, MonteCarloSimulator
from .bootstrap import BootstrapAnalyzer, BootstrapConfig, BootstrapResult
from .permutation_test import PermutationConfig, PermutationResult, PermutationTester
from .walk_forward import WalkForwardResult


@dataclass
class ValidationSuite:
    """Container for all validation results."""

    extended: Optional[ExtendedMetrics] = None
    monte_carlo: Optional[MonteCarloResult] = None
    bootstrap: Optional[BootstrapResult] = None
    permutation: Optional[PermutationResult] = None
    walk_forward: Optional[WalkForwardResult] = None

    def report(self) -> str:
        """Generate a unified validation report."""
        sections = []

        if self.extended:
            sections.append(self.extended.report())

        if self.monte_carlo:
            sections.append(self.monte_carlo.report())

        if self.bootstrap:
            sections.append(self.bootstrap.report())

        if self.permutation:
            sections.append(self.permutation.report())

        if self.walk_forward:
            sections.append(self.walk_forward.report())

        if not sections:
            return "(no validation results)"

        return "\n\n".join(sections)


def run_validation_suite(
    result: BacktestResult,
    run_extended: bool = True,
    run_monte_carlo: bool = False,
    run_bootstrap: bool = False,
    run_permutation: bool = False,
    mc_config: Optional[MonteCarloConfig] = None,
    bs_config: Optional[BootstrapConfig] = None,
    perm_config: Optional[PermutationConfig] = None,
) -> ValidationSuite:
    """Run selected validation analyses on a BacktestResult.

    Args:
        result: The BacktestResult to validate.
        run_extended: Compute extended metrics (always recommended).
        run_monte_carlo: Run Monte Carlo simulation.
        run_bootstrap: Run bootstrap confidence intervals.
        run_permutation: Run permutation significance test.
        mc_config: Monte Carlo configuration.
        bs_config: Bootstrap configuration.
        perm_config: Permutation test configuration.

    Returns:
        ValidationSuite with all requested results.
    """
    suite = ValidationSuite()
    initial_bankroll = result.metrics.initial_bankroll

    # Extended metrics (computes trades internally)
    trades: Optional[List[TradePnL]] = None

    if run_extended:
        ext = ExtendedMetrics.compute(
            result.fills,
            result.settlements,
            result.bankroll_curve,
        )
        suite.extended = ext
        trades = ext.trades

    # Compute trades if not already done
    if trades is None:
        trades = compute_trade_pnls(result.fills, result.settlements)

    if run_monte_carlo:
        mc = MonteCarloSimulator(mc_config)
        suite.monte_carlo = mc.run(trades, initial_bankroll)

    if run_bootstrap:
        bs = BootstrapAnalyzer(bs_config)
        suite.bootstrap = bs.run(trades, initial_bankroll)

    if run_permutation:
        pt = PermutationTester(perm_config)
        suite.permutation = pt.run(trades)

    return suite
