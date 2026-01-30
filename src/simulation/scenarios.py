"""Pre-built simulation scenarios for testing trading algorithms.

Provides factory functions to create simulators configured for various
market conditions. These can be used directly or as templates for
custom scenarios.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from .market_simulator import (
    MarketSimulator,
    MeanRevertingSimulator,
    TrendingSimulator,
)
from .simulated_api_client import SimulatedAPIClient


@dataclass
class ScenarioConfig:
    """Configuration for a simulation scenario."""

    name: str
    description: str
    initial_mid: float
    volatility: float
    spread_range: tuple[float, float]
    drift: float = 0.0
    fair_value: float = 0.50
    reversion_speed: float = 0.0
    seed: Optional[int] = None


# =============================================================================
# Pre-built Scenario Configurations
# =============================================================================


STABLE_MARKET = ScenarioConfig(
    name="stable_market",
    description="Low volatility market with consistent tight spreads",
    initial_mid=0.50,
    volatility=0.005,  # Very low volatility
    spread_range=(0.02, 0.03),  # Tight spreads
    seed=42,
)

VOLATILE_MARKET = ScenarioConfig(
    name="volatile_market",
    description="High volatility market with wide, variable spreads",
    initial_mid=0.50,
    volatility=0.05,  # High volatility
    spread_range=(0.05, 0.10),  # Wide spreads
    seed=42,
)

TRENDING_UP = ScenarioConfig(
    name="trending_up",
    description="Gradual upward price movement",
    initial_mid=0.30,  # Start low
    volatility=0.02,
    spread_range=(0.03, 0.05),
    drift=0.003,  # Positive drift
    seed=42,
)

TRENDING_DOWN = ScenarioConfig(
    name="trending_down",
    description="Gradual downward price movement",
    initial_mid=0.70,  # Start high
    volatility=0.02,
    spread_range=(0.03, 0.05),
    drift=-0.003,  # Negative drift
    seed=42,
)

MEAN_REVERTING = ScenarioConfig(
    name="mean_reverting",
    description="Price oscillates around fair value",
    initial_mid=0.50,
    volatility=0.03,
    spread_range=(0.03, 0.05),
    fair_value=0.50,
    reversion_speed=0.15,  # Moderate reversion
    seed=42,
)

CHOPPY_MARKET = ScenarioConfig(
    name="choppy_market",
    description="High volatility with mean reversion (lots of reversals)",
    initial_mid=0.50,
    volatility=0.04,
    spread_range=(0.04, 0.07),
    fair_value=0.50,
    reversion_speed=0.25,  # Strong reversion
    seed=42,
)

WIDE_SPREAD = ScenarioConfig(
    name="wide_spread",
    description="Illiquid market with very wide spreads",
    initial_mid=0.50,
    volatility=0.02,
    spread_range=(0.08, 0.15),  # Very wide
    seed=42,
)

TIGHT_SPREAD = ScenarioConfig(
    name="tight_spread",
    description="Very liquid market with minimal spreads",
    initial_mid=0.50,
    volatility=0.015,
    spread_range=(0.01, 0.02),  # Very tight
    seed=42,
)

# Collection of all pre-built scenarios
SCENARIOS = {
    "stable_market": STABLE_MARKET,
    "volatile_market": VOLATILE_MARKET,
    "trending_up": TRENDING_UP,
    "trending_down": TRENDING_DOWN,
    "mean_reverting": MEAN_REVERTING,
    "choppy_market": CHOPPY_MARKET,
    "wide_spread": WIDE_SPREAD,
    "tight_spread": TIGHT_SPREAD,
}


# =============================================================================
# Factory Functions
# =============================================================================


def create_simulator(
    config: ScenarioConfig,
    ticker: str = "SIM-MARKET",
) -> MarketSimulator:
    """Create a MarketSimulator from a scenario configuration.

    Args:
        config: ScenarioConfig defining market behavior
        ticker: Market identifier

    Returns:
        Configured MarketSimulator (or subclass)
    """
    # Use trending simulator if drift is specified
    if config.drift != 0.0:
        return TrendingSimulator(
            ticker=ticker,
            initial_mid=config.initial_mid,
            volatility=config.volatility,
            spread_range=config.spread_range,
            drift=config.drift,
            seed=config.seed,
        )

    # Use mean-reverting simulator if reversion is specified
    if config.reversion_speed > 0:
        return MeanRevertingSimulator(
            ticker=ticker,
            initial_mid=config.initial_mid,
            volatility=config.volatility,
            spread_range=config.spread_range,
            fair_value=config.fair_value,
            reversion_speed=config.reversion_speed,
            seed=config.seed,
        )

    # Default to basic simulator
    return MarketSimulator(
        ticker=ticker,
        initial_mid=config.initial_mid,
        volatility=config.volatility,
        spread_range=config.spread_range,
        seed=config.seed,
    )


def create_api_client(
    config: ScenarioConfig,
    ticker: str = "SIM-MARKET",
    fill_probability: float = 1.0,
) -> SimulatedAPIClient:
    """Create a SimulatedAPIClient from a scenario configuration.

    Args:
        config: ScenarioConfig defining market behavior
        ticker: Market identifier
        fill_probability: Probability of fills occurring (0-1)

    Returns:
        Configured SimulatedAPIClient with embedded simulator
    """
    simulator = create_simulator(config, ticker)
    return SimulatedAPIClient(simulator, fill_probability=fill_probability)


def get_scenario(name: str) -> ScenarioConfig:
    """Get a pre-built scenario configuration by name.

    Args:
        name: Scenario name (e.g., 'stable_market', 'volatile_market')

    Returns:
        ScenarioConfig for the requested scenario

    Raises:
        ValueError: If scenario name is not found
    """
    if name not in SCENARIOS:
        available = ", ".join(SCENARIOS.keys())
        raise ValueError(f"Unknown scenario: '{name}'. Available: {available}")
    return SCENARIOS[name]


def list_scenarios() -> list[str]:
    """Get list of available scenario names.

    Returns:
        List of scenario name strings
    """
    return list(SCENARIOS.keys())


# =============================================================================
# Convenience Functions for Quick Setup
# =============================================================================


def stable_market(ticker: str = "SIM-MARKET") -> SimulatedAPIClient:
    """Create a stable market simulation.

    Low volatility, tight spreads. Good for testing basic functionality.
    """
    return create_api_client(STABLE_MARKET, ticker)


def volatile_market(ticker: str = "SIM-MARKET") -> SimulatedAPIClient:
    """Create a volatile market simulation.

    High volatility, wide spreads. Tests algorithm robustness.
    """
    return create_api_client(VOLATILE_MARKET, ticker)


def trending_up(ticker: str = "SIM-MARKET") -> SimulatedAPIClient:
    """Create an upward trending market simulation.

    Gradual price increase. Tests trend-following strategies.
    """
    return create_api_client(TRENDING_UP, ticker)


def trending_down(ticker: str = "SIM-MARKET") -> SimulatedAPIClient:
    """Create a downward trending market simulation.

    Gradual price decrease. Tests short strategies and stops.
    """
    return create_api_client(TRENDING_DOWN, ticker)


def mean_reverting(ticker: str = "SIM-MARKET") -> SimulatedAPIClient:
    """Create a mean-reverting market simulation.

    Price oscillates around fair value. Tests mean-reversion strategies.
    """
    return create_api_client(MEAN_REVERTING, ticker)


# =============================================================================
# Simulation Runner
# =============================================================================


@dataclass
class SimulationResult:
    """Results from running a simulation."""

    scenario_name: str
    n_steps: int
    n_fills: int
    total_volume: int
    pnl: float
    final_position: int
    price_path: list[float]
    fill_prices: list[float]


def run_simulation(
    client: SimulatedAPIClient,
    strategy: Callable[[SimulatedAPIClient], None],
    n_steps: int,
    scenario_name: str = "custom",
) -> SimulationResult:
    """Run a trading strategy through a simulation.

    Args:
        client: SimulatedAPIClient to use
        strategy: Callable that takes client and executes one step of strategy
        n_steps: Number of simulation steps to run
        scenario_name: Name for result tracking

    Returns:
        SimulationResult with performance statistics
    """
    price_path = []
    fill_prices = []
    position = 0
    realized_pnl = 0.0

    for _ in range(n_steps):
        # Run strategy step
        strategy(client)

        # Advance simulation
        market = client.step()
        price_path.append(market.mid)

        # Track new fills
        fills = client.get_all_fills()
        for fill in fills[len(fill_prices):]:
            fill_prices.append(fill.price)

            # Update position and PnL
            if fill.side == "BID":
                position += fill.size
            else:
                position -= fill.size

    # Calculate final metrics
    total_volume = sum(f.size for f in client.get_all_fills())

    return SimulationResult(
        scenario_name=scenario_name,
        n_steps=n_steps,
        n_fills=len(client.get_all_fills()),
        total_volume=total_volume,
        pnl=realized_pnl,
        final_position=position,
        price_path=price_path,
        fill_prices=fill_prices,
    )
