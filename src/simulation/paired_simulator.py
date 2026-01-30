"""Paired market simulator for testing spread/arbitrage trading algorithms.

Provides a simulation framework for two correlated prediction markets where
Market 1 YES ≈ complement of Market 2 NO, enabling testing of spread trading
strategies that exploit mispricings between related instruments.
"""

import random
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MispricingConfig:
    """Configuration for injected arbitrage opportunities.

    Used to create deterministic test scenarios where specific mispricings
    are injected into the simulated market prices.

    Attributes:
        routing_edge_magnitude: How much cheaper one instrument is vs equivalent.
            Positive value = market 1 YES is cheaper than market 2 NO.
        routing_edge_market: Which market has the mispriced instrument (1 or 2).
        cross_market_spread: Bid/ask crossing magnitude. When positive, creates
            an arbitrage where best bid > best ask cross-market.
        dutch_discount: How much below $1.00 the combined cost is. When positive,
            buying both instruments costs less than guaranteed $1 payout.
        duration_steps: How long the mispricing lasts (0 = permanent).
    """
    routing_edge_magnitude: float = 0.0
    routing_edge_market: int = 1
    cross_market_spread: float = 0.0
    dutch_discount: float = 0.0
    duration_steps: int = 0


class PairedMarketSimulator:
    """Simulates two correlated prediction markets.

    Creates realistic paired market data for testing spread trading algorithms.
    The two markets are correlated such that Market 1 YES ≈ complement of Market 2 NO,
    as would be expected for complementary outcomes (e.g., Team A wins vs Team B wins).

    Supports injection of various mispricings (routing edges, cross-market arb,
    dutch book opportunities) for deterministic testing of detection algorithms.

    Attributes:
        name_1: Display name for market 1
        name_2: Display name for market 2
        base_probability: Starting probability for market 1 YES
        volatility: Standard deviation of price movements per step
        spread_range: Tuple of (min_spread, max_spread) for bid-ask spread
        correlation: How tightly the markets track each other (0-1)
        step_count: Number of steps simulated
    """

    def __init__(
        self,
        name_1: str,
        name_2: str,
        base_probability: float = 0.50,
        volatility: float = 0.02,
        spread_range: tuple[float, float] = (0.03, 0.06),
        correlation: float = 0.95,
        mispricing_config: Optional[MispricingConfig] = None,
        seed: Optional[int] = None,
    ) -> None:
        """Initialize the paired market simulator.

        Args:
            name_1: Display name for market 1
            name_2: Display name for market 2
            base_probability: Starting probability for market 1 YES (default 0.50)
            volatility: Price movement std dev per step (default 0.02 = 2%)
            spread_range: Min and max spread as fraction (default 3-6%)
            correlation: How tightly markets track each other (default 0.95)
            mispricing_config: Configuration for injected opportunities (optional)
            seed: Random seed for reproducibility (optional)

        Raises:
            ValueError: If parameters are out of valid ranges
        """
        if not name_1:
            raise ValueError("name_1 cannot be empty")
        if not name_2:
            raise ValueError("name_2 cannot be empty")
        if not 0.0 < base_probability < 1.0:
            raise ValueError(f"base_probability must be between 0 and 1 exclusive, got {base_probability}")
        if volatility < 0:
            raise ValueError(f"volatility cannot be negative, got {volatility}")
        if spread_range[0] < 0 or spread_range[1] < spread_range[0]:
            raise ValueError(f"invalid spread_range: {spread_range}")
        if not 0.0 <= correlation <= 1.0:
            raise ValueError(f"correlation must be between 0 and 1, got {correlation}")

        self.name_1 = name_1
        self.name_2 = name_2
        self.base_probability = base_probability
        self.volatility = volatility
        self.spread_range = spread_range
        self.correlation = correlation
        self.mispricing_config = mispricing_config or MispricingConfig()

        # Internal state
        self._m1_mid = base_probability
        self._m2_mid = 1.0 - base_probability  # Complementary
        self.step_count = 0
        self._mispricing_remaining = self.mispricing_config.duration_steps

        # RNG for reproducibility
        if seed is not None:
            random.seed(seed)
        self._rng = random.Random(seed)

        # Current spread values (regenerated each step)
        self._m1_spread = self._rng.uniform(*spread_range)
        self._m2_spread = self._rng.uniform(*spread_range)

    def poll_market_1(self) -> dict:
        """Return current market 1 data in LiveArbMonitor format.

        Returns:
            Dict with keys: name, yes_ask, no_ask, yes_bid, no_bid
        """
        return self._create_market_data(1)

    def poll_market_2(self) -> dict:
        """Return current market 2 data in LiveArbMonitor format.

        Returns:
            Dict with keys: name, yes_ask, no_ask, yes_bid, no_bid
        """
        return self._create_market_data(2)

    def step(self) -> tuple[dict, dict]:
        """Advance simulation by one step.

        Evolves prices using correlated random walk and regenerates spreads.

        Returns:
            Tuple of (market_1_data, market_2_data) dicts
        """
        # Evolve market 1 price
        change = self._rng.gauss(0, self.volatility)

        # Mean reversion at extremes
        if self._m1_mid < 0.1:
            change += self.volatility * 0.5
        elif self._m1_mid > 0.9:
            change -= self.volatility * 0.5

        self._m1_mid = max(0.01, min(0.99, self._m1_mid + change))

        # Market 2 is correlated complement with some noise
        correlation_noise = self._rng.gauss(0, self.volatility * (1 - self.correlation))
        self._m2_mid = max(0.01, min(0.99, (1.0 - self._m1_mid) + correlation_noise))

        # Regenerate spreads
        self._m1_spread = self._rng.uniform(*self.spread_range)
        self._m2_spread = self._rng.uniform(*self.spread_range)

        # Decrement mispricing duration
        if self._mispricing_remaining > 0:
            self._mispricing_remaining -= 1

        self.step_count += 1

        return self.poll_market_1(), self.poll_market_2()

    def get_current_opportunity(self, contract_size: int = 100) -> dict:
        """Calculate current arbitrage opportunities with fees.

        Computes routing edges, cross-market arb PnL, and dutch book profit
        based on current market prices and Kalshi fee structure.

        Args:
            contract_size: Number of contracts for fee calculations

        Returns:
            Dict with keys:
                - m1_data: Market 1 poll data
                - m2_data: Market 2 poll data
                - routing_edge_t1: Team 1 routing edge ($/contract)
                - routing_edge_t2: Team 2 routing edge ($/contract)
                - dutch_profit: Dutch book profit ($/contract)
                - arb_pnl_t1: Cross-market arb PnL for Team 1 exposure
                - arb_pnl_t2: Cross-market arb PnL for Team 2 exposure
        """
        # Import fee functions from live_arb
        from arb.live_arb import all_in_buy_cost, all_in_sell_proceeds

        m1 = self.poll_market_1()
        m2 = self.poll_market_2()

        # Entry costs (buying at ask)
        c_t1_yes = all_in_buy_cost(m1["yes_ask"], contract_size)
        c_t1_no = all_in_buy_cost(m1["no_ask"], contract_size)
        c_t2_yes = all_in_buy_cost(m2["yes_ask"], contract_size)
        c_t2_no = all_in_buy_cost(m2["no_ask"], contract_size)

        # Routing edges (same exposure, different instrument)
        # Team 1 exposure: YES m1 vs NO m2 (m1 YES wins same as m2 NO wins)
        routing_edge_t1 = c_t1_yes - c_t2_no
        # Team 2 exposure: YES m2 vs NO m1
        routing_edge_t2 = c_t2_yes - c_t1_no

        # Dutch book profit (buy cheapest legs for both exposures)
        t1_best = min(c_t1_yes, c_t2_no)
        t2_best = min(c_t2_yes, c_t1_no)
        dutch_profit = 1.0 - (t1_best + t2_best)

        # Cross-market arb (buy cheap, sell expensive simultaneously)
        arb_pnl_t1 = None
        arb_pnl_t2 = None

        if m1["yes_bid"] is not None and m2["no_bid"] is not None:
            # Team 1 arb: buy cheapest Team 1 exposure, sell most expensive
            buy_options = [
                ("m1_yes", c_t1_yes),
                ("m2_no", c_t2_no),
            ]
            sell_options = [
                ("m1_yes", all_in_sell_proceeds(m1["yes_bid"], contract_size)),
                ("m2_no", all_in_sell_proceeds(m2["no_bid"], contract_size)),
            ]
            best_buy = min(buy_options, key=lambda x: x[1])
            best_sell = max(sell_options, key=lambda x: x[1])
            arb_pnl_t1 = best_sell[1] - best_buy[1]

        if m2["yes_bid"] is not None and m1["no_bid"] is not None:
            # Team 2 arb: buy cheapest Team 2 exposure, sell most expensive
            buy_options = [
                ("m2_yes", c_t2_yes),
                ("m1_no", c_t1_no),
            ]
            sell_options = [
                ("m2_yes", all_in_sell_proceeds(m2["yes_bid"], contract_size)),
                ("m1_no", all_in_sell_proceeds(m1["no_bid"], contract_size)),
            ]
            best_buy = min(buy_options, key=lambda x: x[1])
            best_sell = max(sell_options, key=lambda x: x[1])
            arb_pnl_t2 = best_sell[1] - best_buy[1]

        return {
            "m1_data": m1,
            "m2_data": m2,
            "routing_edge_t1": routing_edge_t1,
            "routing_edge_t2": routing_edge_t2,
            "dutch_profit": dutch_profit,
            "arb_pnl_t1": arb_pnl_t1,
            "arb_pnl_t2": arb_pnl_t2,
        }

    def reset(self, base_probability: Optional[float] = None) -> None:
        """Reset the simulator to initial state.

        Args:
            base_probability: New starting probability (optional)
        """
        if base_probability is not None:
            if not 0.0 < base_probability < 1.0:
                raise ValueError(f"base_probability must be between 0 and 1 exclusive, got {base_probability}")
            self.base_probability = base_probability

        self._m1_mid = self.base_probability
        self._m2_mid = 1.0 - self.base_probability
        self.step_count = 0
        self._mispricing_remaining = self.mispricing_config.duration_steps
        self._m1_spread = self._rng.uniform(*self.spread_range)
        self._m2_spread = self._rng.uniform(*self.spread_range)

    def _create_market_data(self, market: int) -> dict:
        """Create market data dict for a given market.

        Args:
            market: 1 or 2

        Returns:
            Dict with name, yes_ask, no_ask, yes_bid, no_bid
        """
        if market == 1:
            name = self.name_1
            mid = self._m1_mid
            spread = self._m1_spread
        else:
            name = self.name_2
            mid = self._m2_mid
            spread = self._m2_spread

        half_spread = spread / 2

        # Base prices
        yes_mid = mid
        no_mid = 1.0 - mid

        yes_bid = max(0.01, yes_mid - half_spread)
        yes_ask = min(0.99, yes_mid + half_spread)
        no_bid = max(0.01, no_mid - half_spread)
        no_ask = min(0.99, no_mid + half_spread)

        # Apply mispricings if active
        if self._is_mispricing_active():
            yes_bid, yes_ask, no_bid, no_ask = self._apply_mispricing(
                market, yes_bid, yes_ask, no_bid, no_ask
            )

        return {
            "name": name,
            "yes_ask": round(yes_ask, 4),
            "no_ask": round(no_ask, 4),
            "yes_bid": round(yes_bid, 4),
            "no_bid": round(no_bid, 4),
        }

    def _is_mispricing_active(self) -> bool:
        """Check if mispricing injection is currently active."""
        config = self.mispricing_config
        has_mispricing = (
            config.routing_edge_magnitude != 0.0 or
            config.cross_market_spread != 0.0 or
            config.dutch_discount != 0.0
        )
        # Active if permanent (duration_steps=0) or duration remaining
        return has_mispricing and (
            config.duration_steps == 0 or self._mispricing_remaining > 0
        )

    def _apply_mispricing(
        self,
        market: int,
        yes_bid: float,
        yes_ask: float,
        no_bid: float,
        no_ask: float,
    ) -> tuple[float, float, float, float]:
        """Apply configured mispricings to prices.

        Args:
            market: 1 or 2
            yes_bid, yes_ask, no_bid, no_ask: Base prices

        Returns:
            Tuple of adjusted (yes_bid, yes_ask, no_bid, no_ask)
        """
        config = self.mispricing_config

        # Routing edge: make one instrument cheaper than equivalent
        if config.routing_edge_magnitude != 0.0:
            if config.routing_edge_market == market:
                if market == 1:
                    # Make m1 YES cheaper (lower ask)
                    yes_ask -= config.routing_edge_magnitude
                else:
                    # Make m2 NO cheaper (lower ask)
                    no_ask -= config.routing_edge_magnitude

        # Cross-market spread: create bid > ask situation
        if config.cross_market_spread != 0.0:
            if market == 1:
                # Increase m1 YES bid
                yes_bid += config.cross_market_spread / 2
            else:
                # Decrease m2 NO ask
                no_ask -= config.cross_market_spread / 2

        # Dutch discount: make combined cost < $1.00
        if config.dutch_discount != 0.0:
            # Reduce asks slightly to create dutch book
            discount_per_side = config.dutch_discount / 2
            yes_ask -= discount_per_side
            no_ask -= discount_per_side

        # Clamp to valid range
        yes_bid = max(0.01, min(0.99, yes_bid))
        yes_ask = max(0.01, min(0.99, yes_ask))
        no_bid = max(0.01, min(0.99, no_bid))
        no_ask = max(0.01, min(0.99, no_ask))

        return yes_bid, yes_ask, no_bid, no_ask
