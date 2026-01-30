"""Pre-built spread trading scenarios for testing arbitrage algorithms.

Provides factory functions to create paired market simulators configured
for various spread/arbitrage testing conditions.
"""

from dataclasses import dataclass
from typing import Optional

from .paired_simulator import MispricingConfig, PairedMarketSimulator


@dataclass
class SpreadScenarioConfig:
    """Configuration for a spread trading simulation scenario.

    Attributes:
        name: Unique scenario identifier
        description: Human-readable description of the scenario
        base_probability: Starting probability for market 1 YES
        volatility: Price movement std dev per step
        spread_range: Tuple of (min_spread, max_spread) for bid-ask spread
        correlation: How tightly markets track each other (0-1)
        mispricing: Configuration for injected mispricings
        seed: Random seed for reproducibility
    """
    name: str
    description: str
    base_probability: float = 0.50
    volatility: float = 0.02
    spread_range: tuple[float, float] = (0.03, 0.06)
    correlation: float = 0.95
    mispricing: Optional[MispricingConfig] = None
    seed: Optional[int] = None


# =============================================================================
# Pre-built Spread Scenario Configurations
# =============================================================================


NO_OPPORTUNITY = SpreadScenarioConfig(
    name="no_opportunity",
    description="Efficient markets with perfect correlation, no arbitrage",
    base_probability=0.50,
    volatility=0.02,
    spread_range=(0.03, 0.06),
    correlation=1.0,  # Perfect correlation
    mispricing=None,
    seed=42,
)

ROUTING_EDGE = SpreadScenarioConfig(
    name="routing_edge",
    description="One instrument is consistently cheaper for same exposure",
    base_probability=0.50,
    volatility=0.02,
    spread_range=(0.03, 0.06),
    correlation=0.95,
    mispricing=MispricingConfig(
        routing_edge_magnitude=0.02,  # 2 cent edge
        routing_edge_market=1,
        duration_steps=0,  # Permanent
    ),
    seed=42,
)

CROSS_MARKET_ARB = SpreadScenarioConfig(
    name="cross_market_arb",
    description="Bid-ask crossing creates immediate arbitrage opportunity",
    base_probability=0.50,
    volatility=0.02,
    spread_range=(0.03, 0.06),
    correlation=0.95,
    mispricing=MispricingConfig(
        cross_market_spread=0.015,  # 1.5 cent crossing
        duration_steps=0,
    ),
    seed=42,
)

DUTCH_BOOK = SpreadScenarioConfig(
    name="dutch_book",
    description="Combined cost is less than $1.00 payout",
    base_probability=0.50,
    volatility=0.02,
    spread_range=(0.03, 0.06),
    correlation=0.95,
    mispricing=MispricingConfig(
        dutch_discount=0.02,  # 2 cent discount
        duration_steps=0,
    ),
    seed=42,
)

FEES_EXCEED_PROFIT = SpreadScenarioConfig(
    name="fees_exceed_profit",
    description="Small mispricing that fees eliminate",
    base_probability=0.50,
    volatility=0.02,
    spread_range=(0.03, 0.06),
    correlation=0.95,
    mispricing=MispricingConfig(
        cross_market_spread=0.003,  # 0.3 cent crossing (fees ~0.5-1 cent)
        duration_steps=0,
    ),
    seed=42,
)

HIGH_VOLATILITY = SpreadScenarioConfig(
    name="high_volatility",
    description="Rapid opportunity changes with high volatility",
    base_probability=0.50,
    volatility=0.08,  # 4x normal volatility
    spread_range=(0.05, 0.10),  # Wider spreads
    correlation=0.85,  # Lower correlation
    mispricing=None,
    seed=42,
)

TRANSIENT_OPPORTUNITY = SpreadScenarioConfig(
    name="transient_opportunity",
    description="Brief arbitrage window that closes quickly",
    base_probability=0.50,
    volatility=0.02,
    spread_range=(0.03, 0.06),
    correlation=0.95,
    mispricing=MispricingConfig(
        cross_market_spread=0.02,
        duration_steps=5,  # Only lasts 5 steps
    ),
    seed=42,
)

LARGE_DUTCH_BOOK = SpreadScenarioConfig(
    name="large_dutch_book",
    description="Large dutch book opportunity for testing",
    base_probability=0.50,
    volatility=0.01,  # Low volatility
    spread_range=(0.02, 0.03),  # Tight spreads
    correlation=0.98,
    mispricing=MispricingConfig(
        dutch_discount=0.08,  # 8 cent discount (enough to overcome fees)
        duration_steps=0,
    ),
    seed=42,
)

COMPETING_OPPORTUNITIES = SpreadScenarioConfig(
    name="competing_opportunities",
    description="Multiple opportunity types competing",
    base_probability=0.50,
    volatility=0.02,
    spread_range=(0.03, 0.06),
    correlation=0.95,
    mispricing=MispricingConfig(
        routing_edge_magnitude=0.01,
        routing_edge_market=1,
        cross_market_spread=0.01,
        dutch_discount=0.01,
        duration_steps=0,
    ),
    seed=42,
)


# Collection of all pre-built spread scenarios
SPREAD_SCENARIOS = {
    "no_opportunity": NO_OPPORTUNITY,
    "routing_edge": ROUTING_EDGE,
    "cross_market_arb": CROSS_MARKET_ARB,
    "dutch_book": DUTCH_BOOK,
    "fees_exceed_profit": FEES_EXCEED_PROFIT,
    "high_volatility": HIGH_VOLATILITY,
    "transient_opportunity": TRANSIENT_OPPORTUNITY,
    "large_dutch_book": LARGE_DUTCH_BOOK,
    "competing_opportunities": COMPETING_OPPORTUNITIES,
}


# =============================================================================
# Factory Functions
# =============================================================================


def create_spread_simulator(
    config: SpreadScenarioConfig,
    name_1: str = "Team A",
    name_2: str = "Team B",
) -> PairedMarketSimulator:
    """Create a PairedMarketSimulator from a scenario configuration.

    Args:
        config: SpreadScenarioConfig defining market behavior
        name_1: Display name for market 1 (default "Team A")
        name_2: Display name for market 2 (default "Team B")

    Returns:
        Configured PairedMarketSimulator instance
    """
    return PairedMarketSimulator(
        name_1=name_1,
        name_2=name_2,
        base_probability=config.base_probability,
        volatility=config.volatility,
        spread_range=config.spread_range,
        correlation=config.correlation,
        mispricing_config=config.mispricing,
        seed=config.seed,
    )


def get_spread_scenario(name: str) -> SpreadScenarioConfig:
    """Get a pre-built spread scenario configuration by name.

    Args:
        name: Scenario name (e.g., 'no_opportunity', 'routing_edge')

    Returns:
        SpreadScenarioConfig for the requested scenario

    Raises:
        ValueError: If scenario name is not found
    """
    if name not in SPREAD_SCENARIOS:
        available = ", ".join(SPREAD_SCENARIOS.keys())
        raise ValueError(f"Unknown spread scenario: '{name}'. Available: {available}")
    return SPREAD_SCENARIOS[name]


def list_spread_scenarios() -> list[str]:
    """Get list of available spread scenario names.

    Returns:
        List of scenario name strings
    """
    return list(SPREAD_SCENARIOS.keys())
