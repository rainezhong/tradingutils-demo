"""Position sizing module for arbitrage execution.

Calculates optimal position sizes based on:
- Available capital (after emergency reserves)
- Market liquidity
- Kelly Criterion (edge-based sizing)
- Risk manager limits

The position size is the minimum of all constraints.
"""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from .kelly import KellyCalculator, KellyResult

if TYPE_CHECKING:
    from arb.execution.metrics import ExecutionMetrics
    from arb.spread_detector import SpreadOpportunity
    from src.oms.capital_manager import CapitalManager
    from src.risk.risk_manager import RiskManager


logger = logging.getLogger(__name__)


@dataclass
class SizingConfig:
    """Configuration for position sizing.

    Attributes:
        max_capital_fraction: Maximum fraction of capital to deploy at once (0-1)
        emergency_reserve_fraction: Fraction to reserve for stuck positions (0-1)
        kelly_fraction_cap: Maximum Kelly bet fraction (0-1)
        use_half_kelly: Whether to use half-Kelly for conservatism
        default_execution_success_rate: Default success rate if no metrics available
        default_max_loss_cents: Default max loss per contract (slippage estimate)
    """
    max_capital_fraction: float = 0.5
    emergency_reserve_fraction: float = 0.25
    kelly_fraction_cap: float = 0.25
    use_half_kelly: bool = True
    default_execution_success_rate: float = 0.85
    default_max_loss_cents: float = 0.05  # 5 cents default slippage

    def __post_init__(self) -> None:
        """Validate configuration."""
        if not 0 < self.max_capital_fraction <= 1:
            raise ValueError(
                f"max_capital_fraction must be in (0, 1], got {self.max_capital_fraction}"
            )
        if not 0 <= self.emergency_reserve_fraction < 1:
            raise ValueError(
                f"emergency_reserve_fraction must be in [0, 1), got {self.emergency_reserve_fraction}"
            )
        if not 0 < self.kelly_fraction_cap <= 1:
            raise ValueError(
                f"kelly_fraction_cap must be in (0, 1], got {self.kelly_fraction_cap}"
            )


@dataclass
class SizingResult:
    """Result from position size calculation.

    Attributes:
        recommended_size: Recommended number of contracts to trade
        limiting_factor: Which constraint limited the size
        details: Breakdown of each constraint's contribution
        kelly_result: The Kelly calculation result (if applicable)
    """
    recommended_size: int
    limiting_factor: str
    details: dict = field(default_factory=dict)
    kelly_result: Optional[KellyResult] = None

    @property
    def can_trade(self) -> bool:
        """Whether the recommended size allows trading."""
        return self.recommended_size > 0


class PositionSizer:
    """Calculates optimal position sizes for arbitrage opportunities.

    Integrates capital management, risk limits, and Kelly Criterion
    to determine the optimal trade size.

    Example:
        >>> sizer = PositionSizer(
        ...     capital_manager=capital_mgr,
        ...     risk_manager=risk_mgr,
        ...     kelly_calculator=kelly_calc,
        ...     config=SizingConfig()
        ... )
        >>> result = sizer.calculate_size(opportunity, execution_metrics)
        >>> if result.can_trade:
        ...     execute(opportunity, size=result.recommended_size)
    """

    def __init__(
        self,
        capital_manager: "CapitalManager",
        risk_manager: Optional["RiskManager"] = None,
        kelly_calculator: Optional[KellyCalculator] = None,
        config: Optional[SizingConfig] = None,
    ) -> None:
        """Initialize PositionSizer.

        Args:
            capital_manager: CapitalManager for checking available capital
            risk_manager: Optional RiskManager for position limits
            kelly_calculator: Optional KellyCalculator (created if not provided)
            config: SizingConfig (defaults created if not provided)
        """
        self.capital_manager = capital_manager
        self.risk_manager = risk_manager
        self.kelly_calculator = kelly_calculator or KellyCalculator(max_fraction=0.25)
        self.config = config or SizingConfig()

        # Update Kelly calculator's max fraction to match config
        self.kelly_calculator.max_fraction = self.config.kelly_fraction_cap

    def calculate_size(
        self,
        opportunity: "SpreadOpportunity",
        execution_metrics: Optional["ExecutionMetrics"] = None,
    ) -> SizingResult:
        """Calculate optimal position size for an opportunity.

        Size = min(
            capital_limited_size,
            liquidity_limited_size,
            kelly_criterion_size,
            risk_limit_size
        )

        Args:
            opportunity: The spread opportunity to size
            execution_metrics: Optional historical execution metrics

        Returns:
            SizingResult with recommended size and limiting factor
        """
        details = {}
        sizes = []

        # 1. Calculate capital-limited size
        capital_size = self._calculate_capital_size(opportunity)
        details["capital_size"] = capital_size
        if capital_size > 0:
            sizes.append(("capital", capital_size))

        # 2. Get liquidity-limited size from opportunity
        liquidity_size = opportunity.max_contracts
        details["liquidity_size"] = liquidity_size
        if liquidity_size > 0:
            sizes.append(("liquidity", liquidity_size))

        # 3. Calculate Kelly-criterion size
        kelly_result = self._calculate_kelly_size(opportunity, execution_metrics)
        kelly_size = kelly_result.recommended_bet if kelly_result else 0
        if kelly_size > 0:
            # Convert Kelly bet amount to contracts
            avg_price = (opportunity.buy_price + opportunity.sell_price) / 2
            kelly_contracts = self.kelly_calculator.calculate_contracts(
                kelly_bet_amount=kelly_result.half_kelly if self.config.use_half_kelly else kelly_result.recommended_bet,
                price_per_contract=avg_price,
            )
            details["kelly_size"] = kelly_contracts
            details["kelly_fraction"] = kelly_result.capped_fraction
            if kelly_contracts > 0:
                sizes.append(("kelly", kelly_contracts))
        else:
            details["kelly_size"] = 0
            details["kelly_fraction"] = 0.0

        # 4. Check risk manager limits
        if self.risk_manager:
            risk_size = self._calculate_risk_limit_size()
            details["risk_limit_size"] = risk_size
            if risk_size > 0:
                sizes.append(("risk_limit", risk_size))
        else:
            details["risk_limit_size"] = None

        # Determine final size and limiting factor
        if not sizes:
            return SizingResult(
                recommended_size=0,
                limiting_factor="no_capacity",
                details=details,
                kelly_result=kelly_result,
            )

        # Find minimum size and its factor
        limiting_factor, min_size = min(sizes, key=lambda x: x[1])

        logger.info(
            "Position sizing: recommended=%d, limiting_factor=%s, details=%s",
            min_size,
            limiting_factor,
            details,
        )

        return SizingResult(
            recommended_size=min_size,
            limiting_factor=limiting_factor,
            details=details,
            kelly_result=kelly_result,
        )

    def _calculate_capital_size(self, opportunity: "SpreadOpportunity") -> int:
        """Calculate size based on available capital.

        Uses deployable capital (after emergency reserve) divided by 2
        to cover both legs of the spread.
        """
        # Get deployable capital for each exchange
        buy_capital = self._get_deployable_capital(opportunity.buy_platform.value)
        sell_capital = self._get_deployable_capital(opportunity.sell_platform.value)

        # Use the minimum available across both platforms
        available = min(buy_capital, sell_capital)

        if available <= 0:
            logger.debug("No capital available for sizing")
            return 0

        # Calculate maximum contracts based on capital
        # Need capital for both legs, so divide by 2
        # Then divide by cost per contract (buy price)
        cost_per_contract = opportunity.buy_price
        if cost_per_contract <= 0:
            return 0

        # Apply max capital fraction limit
        deployable = available * self.config.max_capital_fraction
        max_contracts = int(deployable / cost_per_contract)

        logger.debug(
            "Capital sizing: available=$%.2f, deployable=$%.2f, contracts=%d",
            available,
            deployable,
            max_contracts,
        )

        return max_contracts

    def _get_deployable_capital(self, exchange: str) -> float:
        """Get deployable capital for an exchange."""
        # Try to use get_deployable_capital if available
        if hasattr(self.capital_manager, 'get_deployable_capital'):
            return self.capital_manager.get_deployable_capital(
                exchange=exchange,
                emergency_reserve_pct=self.config.emergency_reserve_fraction,
            )

        # Fallback to get_available_capital
        available = self.capital_manager.get_available_capital(exchange)
        state = self.capital_manager.get_capital_state(exchange)

        if not state:
            return 0.0

        # Apply emergency reserve
        emergency = state.total_balance * self.config.emergency_reserve_fraction
        return max(0.0, available - emergency)

    def _calculate_kelly_size(
        self,
        opportunity: "SpreadOpportunity",
        execution_metrics: Optional["ExecutionMetrics"],
    ) -> Optional[KellyResult]:
        """Calculate Kelly-criterion based size."""
        # Get execution success rate
        if execution_metrics and execution_metrics.total_attempts > 0:
            # Use fill_rate adjusted for leg risk
            fill_rate = execution_metrics.fill_rate
            leg_risk_rate = execution_metrics.leg_risk_rate
            success_rate = fill_rate * (1 - leg_risk_rate)
        else:
            success_rate = self.config.default_execution_success_rate

        # Get edge per contract
        edge = opportunity.net_edge_per_contract
        if edge <= 0:
            return None

        # Estimate max loss (slippage scenario)
        max_loss = self.config.default_max_loss_cents

        # Get total bankroll (sum across all exchanges)
        bankroll = self._get_total_bankroll()
        if bankroll <= 0:
            return None

        try:
            result = self.kelly_calculator.calculate_for_arb(
                edge_per_contract=edge,
                max_loss_per_contract=max_loss,
                execution_success_rate=success_rate,
                bankroll=bankroll,
                use_half_kelly=self.config.use_half_kelly,
            )
            return result
        except ValueError as e:
            logger.warning("Kelly calculation failed: %s", e)
            return None

    def _get_total_bankroll(self) -> float:
        """Get total bankroll across all exchanges."""
        summary = self.capital_manager.get_summary()
        return summary.get("total_available", 0.0)

    def _calculate_risk_limit_size(self) -> int:
        """Calculate maximum size allowed by risk limits."""
        if not self.risk_manager:
            return float('inf')

        metrics = self.risk_manager.get_risk_metrics()

        # Get position limit from config
        max_position = self.risk_manager.config.max_position_size

        # Calculate remaining capacity
        total_position = metrics.get("total_position", 0)
        max_total = self.risk_manager.config.max_total_position
        remaining_total = max_total - total_position

        # Use the more restrictive limit
        return min(max_position, remaining_total)
