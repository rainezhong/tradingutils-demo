"""Market simulator for testing trading algorithms.

Generates realistic market data without requiring live API connections.
Supports various market conditions and scenarios.
"""

import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

from src.core.interfaces import DataProvider
from src.core.models import Fill, MarketState, Quote


class MarketSimulator(DataProvider):
    """Simulates realistic market data for testing.

    Generates price movements using random walk with configurable volatility,
    and creates realistic bid/ask spreads around the mid price.

    Attributes:
        ticker: Market identifier
        mid_price: Current mid price (0.0 to 1.0 as probability)
        volatility: Standard deviation of price movements per step
        spread_range: Tuple of (min_spread, max_spread) for bid-ask spread
        current_time: Simulated current time
        step_count: Number of steps simulated
    """

    def __init__(
        self,
        ticker: str,
        initial_mid: float = 0.50,
        volatility: float = 0.02,
        spread_range: tuple[float, float] = (0.03, 0.06),
        seed: Optional[int] = None,
    ) -> None:
        """Initialize the market simulator.

        Args:
            ticker: Market identifier
            initial_mid: Starting mid price (default 0.50 = 50%)
            volatility: Price movement std dev per step (default 0.02 = 2%)
            spread_range: Min and max spread as fraction (default 3-6%)
            seed: Random seed for reproducibility (optional)
        """
        if not ticker:
            raise ValueError("ticker cannot be empty")
        if not 0.0 <= initial_mid <= 1.0:
            raise ValueError(f"initial_mid must be between 0 and 1, got {initial_mid}")
        if volatility < 0:
            raise ValueError(f"volatility cannot be negative, got {volatility}")
        if spread_range[0] < 0 or spread_range[1] < spread_range[0]:
            raise ValueError(f"invalid spread_range: {spread_range}")

        self.ticker = ticker
        self.mid_price = initial_mid
        self.volatility = volatility
        self.spread_range = spread_range
        self.current_time = datetime.now()
        self.step_count = 0
        self._last_price: Optional[float] = None
        self._volume = 0

        # For reproducibility
        if seed is not None:
            random.seed(seed)
        self._rng = random.Random(seed)

    def get_market_state(self, ticker: str) -> MarketState:
        """Get current market state for a ticker.

        Args:
            ticker: Market identifier (must match simulator's ticker)

        Returns:
            Current MarketState

        Raises:
            ValueError: If ticker doesn't match
        """
        if ticker != self.ticker:
            raise ValueError(f"Simulator is for ticker '{self.ticker}', not '{ticker}'")
        return self._create_market_state()

    def generate_market_state(self) -> MarketState:
        """Generate the next market state with price evolution.

        Advances the simulation by one step, moving the price according
        to random walk dynamics.

        Returns:
            New MarketState with evolved price
        """
        # Evolve price using random walk
        self._evolve_price()

        # Advance time
        self.current_time += timedelta(seconds=1)
        self.step_count += 1

        # Occasionally generate some volume
        if self._rng.random() < 0.3:  # 30% chance of trade
            self._volume += self._rng.randint(1, 10)

        return self._create_market_state()

    def simulate_sequence(self, n: int) -> list[MarketState]:
        """Generate n consecutive market states.

        Simulates a sequence of market states where each state
        evolves from the previous one.

        Args:
            n: Number of states to generate

        Returns:
            List of MarketState objects in chronological order
        """
        if n <= 0:
            raise ValueError(f"n must be positive, got {n}")

        states = []
        for _ in range(n):
            states.append(self.generate_market_state())
        return states

    def simulate_fill(
        self,
        quote: Quote,
        current_market: MarketState,
    ) -> Optional[Fill]:
        """Determine if a quote would fill against current market.

        A quote fills if:
        - BID quote: market ask crosses down to or below bid price
        - ASK quote: market bid crosses up to or above ask price

        Args:
            quote: Quote to check for fill
            current_market: Current market state

        Returns:
            Fill object if quote would fill, None otherwise
        """
        if not quote.is_active:
            return None

        fill_price: Optional[float] = None

        if quote.side == "BID":
            # BID fills when market ask <= quote price
            if current_market.ask <= quote.price:
                fill_price = min(quote.price, current_market.ask)
        else:  # ASK
            # ASK fills when market bid >= quote price
            if current_market.bid >= quote.price:
                fill_price = max(quote.price, current_market.bid)

        if fill_price is not None:
            return Fill(
                ticker=quote.ticker,
                side=quote.side,
                price=fill_price,
                size=quote.remaining_size,
                order_id=quote.order_id or str(uuid.uuid4()),
                fill_id=str(uuid.uuid4()),
                timestamp=current_market.timestamp,
                fee=0.0,
            )

        return None

    def reset(self, initial_mid: Optional[float] = None) -> None:
        """Reset the simulator to initial state.

        Args:
            initial_mid: New initial mid price (optional)
        """
        if initial_mid is not None:
            if not 0.0 <= initial_mid <= 1.0:
                raise ValueError(f"initial_mid must be between 0 and 1, got {initial_mid}")
            self.mid_price = initial_mid
        else:
            self.mid_price = 0.50

        self.current_time = datetime.now()
        self.step_count = 0
        self._last_price = None
        self._volume = 0

    def _evolve_price(self) -> None:
        """Evolve the mid price using random walk."""
        # Random walk with mean reversion tendency at extremes
        change = self._rng.gauss(0, self.volatility)

        # Add mean reversion when near boundaries
        if self.mid_price < 0.1:
            change += self.volatility * 0.5  # Push up from low
        elif self.mid_price > 0.9:
            change -= self.volatility * 0.5  # Push down from high

        self._last_price = self.mid_price
        self.mid_price = max(0.01, min(0.99, self.mid_price + change))

    def _create_market_state(self) -> MarketState:
        """Create a MarketState from current simulator state."""
        # Generate spread within range
        spread = self._rng.uniform(*self.spread_range)
        half_spread = spread / 2

        # Calculate bid/ask ensuring they stay in valid range
        bid = max(0.01, self.mid_price - half_spread)
        ask = min(0.99, self.mid_price + half_spread)

        # Ensure bid < ask
        if bid >= ask:
            mid = (bid + ask) / 2
            bid = mid - 0.01
            ask = mid + 0.01

        return MarketState(
            ticker=self.ticker,
            timestamp=self.current_time,
            bid=bid,
            ask=ask,
            last_price=self._last_price,
            volume=self._volume,
        )


class TrendingSimulator(MarketSimulator):
    """Market simulator with directional trend bias.

    Extends MarketSimulator to add a drift component to price evolution,
    creating trending up or down markets.
    """

    def __init__(
        self,
        ticker: str,
        initial_mid: float = 0.50,
        volatility: float = 0.02,
        spread_range: tuple[float, float] = (0.03, 0.06),
        drift: float = 0.001,
        seed: Optional[int] = None,
    ) -> None:
        """Initialize trending simulator.

        Args:
            ticker: Market identifier
            initial_mid: Starting mid price
            volatility: Price movement std dev per step
            spread_range: Min and max spread as fraction
            drift: Directional bias per step (positive = up, negative = down)
            seed: Random seed for reproducibility
        """
        super().__init__(ticker, initial_mid, volatility, spread_range, seed)
        self.drift = drift

    def _evolve_price(self) -> None:
        """Evolve price with trend component."""
        # Random component
        change = self._rng.gauss(0, self.volatility)

        # Add trend
        change += self.drift

        # Mean reversion at extremes still applies
        if self.mid_price < 0.1:
            change += self.volatility * 0.3
        elif self.mid_price > 0.9:
            change -= self.volatility * 0.3

        self._last_price = self.mid_price
        self.mid_price = max(0.01, min(0.99, self.mid_price + change))


class MeanRevertingSimulator(MarketSimulator):
    """Market simulator with mean reversion dynamics.

    Price tends to oscillate around a fair value, useful for testing
    mean-reversion strategies.
    """

    def __init__(
        self,
        ticker: str,
        initial_mid: float = 0.50,
        volatility: float = 0.02,
        spread_range: tuple[float, float] = (0.03, 0.06),
        fair_value: float = 0.50,
        reversion_speed: float = 0.1,
        seed: Optional[int] = None,
    ) -> None:
        """Initialize mean-reverting simulator.

        Args:
            ticker: Market identifier
            initial_mid: Starting mid price
            volatility: Price movement std dev per step
            spread_range: Min and max spread as fraction
            fair_value: Price that market reverts to (0-1)
            reversion_speed: How quickly price reverts (0-1, higher = faster)
            seed: Random seed for reproducibility
        """
        super().__init__(ticker, initial_mid, volatility, spread_range, seed)

        if not 0.0 <= fair_value <= 1.0:
            raise ValueError(f"fair_value must be between 0 and 1, got {fair_value}")
        if not 0.0 <= reversion_speed <= 1.0:
            raise ValueError(f"reversion_speed must be between 0 and 1, got {reversion_speed}")

        self.fair_value = fair_value
        self.reversion_speed = reversion_speed

    def _evolve_price(self) -> None:
        """Evolve price with mean reversion."""
        # Random component
        change = self._rng.gauss(0, self.volatility)

        # Mean reversion component
        deviation = self.fair_value - self.mid_price
        change += deviation * self.reversion_speed

        self._last_price = self.mid_price
        self.mid_price = max(0.01, min(0.99, self.mid_price + change))
