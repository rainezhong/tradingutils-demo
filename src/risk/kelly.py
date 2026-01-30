"""Kelly Criterion calculations for position sizing.

Implements the Kelly Criterion formula for optimal bet sizing:
    f* = (p × b - q) / b

Where:
    f* = optimal fraction of bankroll to bet
    p = probability of winning
    q = probability of losing (1 - p)
    b = odds received (win amount / loss amount)

For arbitrage, we adjust for execution risk (leg risk, slippage).
"""

import logging
from dataclasses import dataclass
from typing import Optional


logger = logging.getLogger(__name__)


@dataclass
class KellyResult:
    """Result from Kelly Criterion calculation.

    Attributes:
        fraction: Optimal Kelly fraction (f*)
        recommended_bet: Recommended bet amount based on bankroll
        half_kelly: Conservative half-Kelly bet amount
        capped_fraction: Fraction after applying max cap
    """
    fraction: float
    recommended_bet: float
    half_kelly: float
    capped_fraction: float

    @property
    def is_positive_ev(self) -> bool:
        """Whether the expected value is positive."""
        return self.fraction > 0


class KellyCalculator:
    """Calculator for Kelly Criterion position sizing.

    Provides optimal bet sizing based on edge and win probability,
    with safety caps to avoid overbetting.

    Example:
        >>> calculator = KellyCalculator(max_fraction=0.25)
        >>> result = calculator.calculate_for_arb(
        ...     edge_per_contract=0.03,  # 3 cents
        ...     max_loss_per_contract=0.05,  # 5 cents slippage
        ...     execution_success_rate=0.90,
        ...     bankroll=10000.0
        ... )
        >>> print(f"Recommended bet: ${result.half_kelly:.2f}")
    """

    def __init__(self, max_fraction: float = 0.25) -> None:
        """Initialize KellyCalculator.

        Args:
            max_fraction: Maximum Kelly fraction (cap to avoid ruin).
                         Default 0.25 (25%) is conservative.
        """
        if not 0 < max_fraction <= 1:
            raise ValueError(f"max_fraction must be in (0, 1], got {max_fraction}")

        self.max_fraction = max_fraction

    def calculate(
        self,
        win_probability: float,
        win_amount: float,
        loss_amount: float,
        bankroll: float,
    ) -> KellyResult:
        """Calculate Kelly Criterion for a general bet.

        Args:
            win_probability: Probability of winning (0-1)
            win_amount: Amount won if successful (positive)
            loss_amount: Amount lost if unsuccessful (positive)
            bankroll: Total available capital

        Returns:
            KellyResult with optimal sizing
        """
        if not 0 < win_probability < 1:
            raise ValueError(f"win_probability must be in (0, 1), got {win_probability}")
        if win_amount <= 0:
            raise ValueError(f"win_amount must be positive, got {win_amount}")
        if loss_amount <= 0:
            raise ValueError(f"loss_amount must be positive, got {loss_amount}")
        if bankroll <= 0:
            raise ValueError(f"bankroll must be positive, got {bankroll}")

        p = win_probability
        q = 1 - p
        b = win_amount / loss_amount

        # Kelly formula: f* = (p × b - q) / b
        fraction = (p * b - q) / b

        # Handle negative EV (Kelly says don't bet)
        if fraction <= 0:
            return KellyResult(
                fraction=fraction,
                recommended_bet=0.0,
                half_kelly=0.0,
                capped_fraction=0.0,
            )

        # Apply cap to avoid overbetting
        capped_fraction = min(fraction, self.max_fraction)

        recommended_bet = capped_fraction * bankroll
        half_kelly = (capped_fraction / 2) * bankroll

        logger.debug(
            "Kelly calculation: p=%.3f, b=%.3f, f*=%.3f, capped=%.3f, bet=$%.2f",
            p, b, fraction, capped_fraction, half_kelly
        )

        return KellyResult(
            fraction=fraction,
            recommended_bet=recommended_bet,
            half_kelly=half_kelly,
            capped_fraction=capped_fraction,
        )

    def calculate_for_arb(
        self,
        edge_per_contract: float,
        max_loss_per_contract: float,
        execution_success_rate: float,
        bankroll: float,
        use_half_kelly: bool = True,
    ) -> KellyResult:
        """Calculate Kelly Criterion specifically for arbitrage trades.

        For arbitrage:
        - p = execution success rate (adjusted for leg risk)
        - Win amount = edge per contract
        - Loss amount = max loss per contract (slippage if leg fails)

        Args:
            edge_per_contract: Expected profit per contract if successful
            max_loss_per_contract: Maximum loss per contract if execution fails
            execution_success_rate: Historical success rate (0-1)
            bankroll: Total available capital
            use_half_kelly: If True, returns half-Kelly as recommended bet

        Returns:
            KellyResult with optimal sizing for arb

        Example:
            Edge = 3 cents, max_loss = 5 cents (slippage)
            p = 0.90, q = 0.10, b = 0.6
            f* = (0.90 × 0.6 - 0.10) / 0.6 = 0.73
            Half-Kelly = 0.365, capped at 25% → bet 25%
        """
        if edge_per_contract <= 0:
            logger.warning("Non-positive edge: %.4f, returning zero bet", edge_per_contract)
            return KellyResult(
                fraction=0.0,
                recommended_bet=0.0,
                half_kelly=0.0,
                capped_fraction=0.0,
            )

        if max_loss_per_contract <= 0:
            # If no potential loss, treat as risk-free (but still cap it)
            logger.warning(
                "Non-positive max loss: %.4f, using edge as loss",
                max_loss_per_contract
            )
            max_loss_per_contract = edge_per_contract

        result = self.calculate(
            win_probability=execution_success_rate,
            win_amount=edge_per_contract,
            loss_amount=max_loss_per_contract,
            bankroll=bankroll,
        )

        logger.info(
            "Kelly for arb: edge=%.4f, max_loss=%.4f, success_rate=%.2f, "
            "f*=%.3f, half_kelly=$%.2f",
            edge_per_contract,
            max_loss_per_contract,
            execution_success_rate,
            result.fraction,
            result.half_kelly,
        )

        return result

    def calculate_contracts(
        self,
        kelly_bet_amount: float,
        price_per_contract: float,
        min_contracts: int = 1,
    ) -> int:
        """Convert Kelly bet amount to number of contracts.

        Args:
            kelly_bet_amount: Dollar amount from Kelly calculation
            price_per_contract: Cost per contract
            min_contracts: Minimum contracts (default 1)

        Returns:
            Number of contracts to trade
        """
        if price_per_contract <= 0:
            return 0

        contracts = int(kelly_bet_amount / price_per_contract)
        return max(min_contracts, contracts) if contracts > 0 else 0
