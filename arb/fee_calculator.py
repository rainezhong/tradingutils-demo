"""Fee calculator for net spread analysis after trading fees.

Wraps the existing fee calculations from arb/spread_detector.py and provides
additional analysis methods for the arbitrage system.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional, Union

from arb.spread_detector import (
    Platform,
    calculate_fee,
    all_in_buy_cost,
    all_in_sell_proceeds,
)

from .config import ArbitrageConfig


logger = logging.getLogger(__name__)


@dataclass
class SpreadAnalysis:
    """Analysis of a spread opportunity after fees.

    Attributes:
        gross_spread: Raw spread before fees
        net_spread: Net spread after all fees
        buy_fee: Fee for the buy side
        sell_fee: Fee for the sell side
        total_fees: Total fees for the trade
        roi: Return on investment (net_spread / capital_required)
        capital_required: Total capital needed for the trade
        estimated_profit: Net profit in USD for the given size
    """

    gross_spread: float
    net_spread: float
    buy_fee: float
    sell_fee: float
    total_fees: float
    roi: float
    capital_required: float
    estimated_profit: float

    @property
    def is_profitable(self) -> bool:
        """Whether the spread is profitable after fees."""
        return self.net_spread > 0


class FeeCalculator:
    """Calculates net spreads after trading fees for both platforms.

    Uses fee structures:
    - Kalshi: 7% of profit (P * (1-P) * contracts * rate)
    - Polymarket: 2% taker fee + gas (~$0.05)

    Example:
        calculator = FeeCalculator(config)

        # Analyze a cross-platform spread
        analysis = calculator.calculate_net_spread(
            buy_platform=Platform.KALSHI,
            buy_price=0.45,
            sell_platform=Platform.POLYMARKET,
            sell_price=0.48,
            size=100,
        )

        if analysis.is_profitable:
            print(f"Net spread: {analysis.net_spread:.4f}")
            print(f"ROI: {analysis.roi:.2%}")
            print(f"Estimated profit: ${analysis.estimated_profit:.2f}")
    """

    def __init__(self, config: Optional[ArbitrageConfig] = None):
        """Initialize fee calculator.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self._config = config or ArbitrageConfig()

    def calculate_net_spread(
        self,
        buy_platform: Platform,
        buy_price: float,
        sell_platform: Platform,
        sell_price: float,
        size: int,
        buy_maker: bool = False,
        sell_maker: bool = False,
        apply_safety_margin: bool = False,
    ) -> SpreadAnalysis:
        """Calculate the net spread after all trading fees.

        For cross-platform arbitrage, calculates the all-in cost to buy
        on one platform and all-in proceeds from selling on another.

        Args:
            buy_platform: Platform to buy on
            buy_price: Price to buy at (0-1 scale)
            sell_platform: Platform to sell on
            sell_price: Price to sell at (0-1 scale)
            size: Number of contracts
            buy_maker: Whether buy is a maker order
            sell_maker: Whether sell is a maker order
            apply_safety_margin: If True, inflate fees by safety margin for conservative estimate

        Returns:
            SpreadAnalysis with all fee calculations
        """
        # Calculate all-in costs and proceeds per contract
        buy_all_in = all_in_buy_cost(buy_platform, buy_price, size, buy_maker)
        sell_all_in = all_in_sell_proceeds(sell_platform, sell_price, size, sell_maker)

        # Calculate individual fees
        buy_fee = calculate_fee(buy_platform, buy_price, size, buy_maker)
        sell_fee = calculate_fee(sell_platform, sell_price, size, sell_maker)

        # Add gas estimate for Polymarket
        if buy_platform == Platform.POLYMARKET:
            buy_fee += self._config.polymarket_gas_estimate
        if sell_platform == Platform.POLYMARKET:
            sell_fee += self._config.polymarket_gas_estimate

        # Apply safety margin if requested (for conservative filtering)
        if apply_safety_margin:
            safety_multiplier = 1.0 + self._config.fee_safety_margin
            buy_fee *= safety_multiplier
            sell_fee *= safety_multiplier

        total_fees = buy_fee + sell_fee

        # Gross and net spread per contract
        gross_spread = sell_price - buy_price

        # Net spread accounts for safety margin in fees
        if apply_safety_margin:
            # Recalculate net spread with inflated fees
            net_spread = (
                gross_spread - (total_fees / size) if size > 0 else gross_spread
            )
        else:
            net_spread = sell_all_in - buy_all_in

        # Capital required is the cost to buy
        capital_required = buy_price * size + buy_fee

        # ROI calculation
        roi = net_spread * size / capital_required if capital_required > 0 else 0

        # Estimated profit
        estimated_profit = net_spread * size

        return SpreadAnalysis(
            gross_spread=gross_spread,
            net_spread=net_spread,
            buy_fee=buy_fee,
            sell_fee=sell_fee,
            total_fees=total_fees,
            roi=roi,
            capital_required=capital_required,
            estimated_profit=estimated_profit,
        )

    def calculate_net_spread_conservative(
        self,
        buy_platform: Platform,
        buy_price: float,
        sell_platform: Platform,
        sell_price: float,
        size: int,
    ) -> SpreadAnalysis:
        """Calculate net spread using conservative assumptions for filtering.

        Always uses:
        - Taker fees (worst case)
        - Fee safety margin applied

        This should be used when deciding whether to take a trade.
        Use calculate_net_spread() for actual profit estimation after the decision.

        Args:
            buy_platform: Platform to buy on
            buy_price: Price to buy at (0-1 scale)
            sell_platform: Platform to sell on
            sell_price: Price to sell at (0-1 scale)
            size: Number of contracts

        Returns:
            SpreadAnalysis with conservative fee calculations
        """
        return self.calculate_net_spread(
            buy_platform=buy_platform,
            buy_price=buy_price,
            sell_platform=sell_platform,
            sell_price=sell_price,
            size=size,
            buy_maker=False,  # Assume taker (worst case)
            sell_maker=False,  # Assume taker (worst case)
            apply_safety_margin=True,  # Add 15% buffer
        )

    def calculate_dutch_book_spread(
        self,
        platform_a: Platform,
        price_a: float,
        platform_b: Platform,
        price_b: float,
        size: int,
    ) -> SpreadAnalysis:
        """Calculate spread for dutch book opportunity (buy both sides < $1).

        In a dutch book, you buy YES on one platform and NO on another,
        guaranteeing profit if both sides sum to less than $1 after fees.

        Args:
            platform_a: First platform (buy YES/NO)
            price_a: Price on first platform
            platform_b: Second platform (buy opposite)
            price_b: Price on second platform
            size: Number of contracts

        Returns:
            SpreadAnalysis with dutch book calculations
        """
        # Both are buys in a dutch book
        cost_a = all_in_buy_cost(platform_a, price_a, size)
        cost_b = all_in_buy_cost(platform_b, price_b, size)

        fee_a = calculate_fee(platform_a, price_a, size)
        fee_b = calculate_fee(platform_b, price_b, size)

        # Add gas for Polymarket
        if platform_a == Platform.POLYMARKET:
            fee_a += self._config.polymarket_gas_estimate
        if platform_b == Platform.POLYMARKET:
            fee_b += self._config.polymarket_gas_estimate

        total_fees = fee_a + fee_b

        # Gross spread is 1 - sum of raw prices
        gross_spread = 1.0 - (price_a + price_b)

        # Net spread is 1 - sum of all-in costs
        combined_cost = cost_a + cost_b
        net_spread = 1.0 - combined_cost

        # Capital required is sum of both buys
        capital_required = (price_a + price_b) * size + total_fees

        # ROI calculation
        roi = net_spread * size / capital_required if capital_required > 0 else 0

        # Estimated profit
        estimated_profit = net_spread * size

        return SpreadAnalysis(
            gross_spread=gross_spread,
            net_spread=net_spread,
            buy_fee=fee_a + fee_b,  # Both are buys
            sell_fee=0,  # No sell in dutch book
            total_fees=total_fees,
            roi=roi,
            capital_required=capital_required,
            estimated_profit=estimated_profit,
        )

    def get_breakeven_spread(
        self,
        buy_platform: Platform,
        buy_price: float,
        sell_platform: Platform,
        size: int,
    ) -> float:
        """Calculate the minimum sell price needed to break even.

        Useful for determining if a spread opportunity exists before
        checking exact prices.

        Args:
            buy_platform: Platform to buy on
            buy_price: Price to buy at
            sell_platform: Platform to sell on
            size: Number of contracts

        Returns:
            Minimum sell price needed to break even
        """
        buy_cost = all_in_buy_cost(buy_platform, buy_price, size)

        # Account for sell fees - iterate to find breakeven
        # Start with buy cost as minimum sell price

        # Adjust for sell fees (simplified - fees are small percentage)
        sell_fee_rate = 0.02 if sell_platform == Platform.POLYMARKET else 0.07
        gas = (
            self._config.polymarket_gas_estimate
            if sell_platform == Platform.POLYMARKET
            else 0
        )

        # breakeven: sell_price - fee = buy_cost
        # sell_price * (1 - rate) - gas/size = buy_cost
        # sell_price = (buy_cost + gas/size) / (1 - rate)
        breakeven = (buy_cost + gas / size) / (1 - sell_fee_rate)

        return breakeven

    def estimate_slippage_impact(
        self,
        platform: Platform,
        price: float,
        size: int,
        depth_at_price: int,
    ) -> float:
        """Estimate price impact due to order size vs available depth.

        Args:
            platform: Trading platform
            price: Current best price
            size: Desired order size
            depth_at_price: Available contracts at best price

        Returns:
            Estimated slippage as fraction (0.01 = 1 cent slippage per contract)
        """
        if depth_at_price >= size:
            return 0.0

        # Simple linear estimate - in practice would walk the book
        fill_ratio = depth_at_price / size
        unfilled_ratio = 1 - fill_ratio

        # Assume 1 cent slippage per 10% of order unfilled at best
        estimated_slippage = unfilled_ratio * 0.10 * 0.01

        return estimated_slippage

    def calculate_min_profitable_size(
        self,
        buy_platform: Platform,
        buy_price: float,
        sell_platform: Platform,
        sell_price: float,
        buy_maker: bool = False,
        sell_maker: bool = False,
    ) -> Union[int, float]:
        """Calculate minimum contracts needed for profitability.

        Determines the minimum number of contracts required for an opportunity
        to be profitable after all fees. This accounts for fixed costs (gas)
        and variable fee rates.

        Args:
            buy_platform: Platform to buy on
            buy_price: Price to buy at (0-1 scale)
            sell_platform: Platform to sell on
            sell_price: Price to sell at (0-1 scale)
            buy_maker: Whether buy is a maker order
            sell_maker: Whether sell is a maker order

        Returns:
            Minimum number of contracts, or 0 if always profitable (no fixed costs),
            or float('inf') if never profitable at this spread.
        """
        # Calculate gross edge per contract
        gross_edge = sell_price - buy_price
        if gross_edge <= 0:
            return float("inf")  # Never profitable with no gross edge

        # Get fee rates based on maker/taker
        buy_fee_rate = self._get_fee_rate(buy_platform, buy_maker)
        sell_fee_rate = self._get_fee_rate(sell_platform, sell_maker)

        # Calculate variable fees per contract
        # For Kalshi, fee is on profit: P * (1-P) * rate
        # For Polymarket, fee is on trade value: price * rate
        if buy_platform == Platform.KALSHI:
            buy_var_fee = buy_price * (1 - buy_price) * buy_fee_rate
        else:
            buy_var_fee = buy_price * buy_fee_rate

        if sell_platform == Platform.KALSHI:
            sell_var_fee = sell_price * (1 - sell_price) * sell_fee_rate
        else:
            sell_var_fee = sell_price * sell_fee_rate

        # Net edge per contract after variable fees
        net_edge_per_contract = gross_edge - buy_var_fee - sell_var_fee

        if net_edge_per_contract <= 0:
            return float("inf")  # Never profitable at these rates

        # Fixed costs (gas for Polymarket)
        fixed_costs = 0.0
        if buy_platform == Platform.POLYMARKET:
            fixed_costs += self._config.polymarket_gas_estimate
        if sell_platform == Platform.POLYMARKET:
            fixed_costs += self._config.polymarket_gas_estimate

        if fixed_costs == 0:
            return 1  # No fixed costs, any size is profitable

        # min_size = fixed_costs / net_edge_per_contract
        min_size = math.ceil(fixed_costs / net_edge_per_contract)
        return max(1, min_size)

    def _get_fee_rate(self, platform: Platform, is_maker: bool) -> float:
        """Get the fee rate for a platform based on order type.

        Args:
            platform: Trading platform
            is_maker: Whether this is a maker order

        Returns:
            Fee rate as a decimal (e.g., 0.07 for 7%)
        """
        if platform == Platform.KALSHI:
            return (
                self._config.kalshi_maker_fee_rate
                if is_maker
                else self._config.kalshi_fee_rate
            )
        else:  # Polymarket
            return (
                self._config.polymarket_maker_fee
                if is_maker
                else self._config.polymarket_taker_fee
            )
