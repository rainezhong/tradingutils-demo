#!/usr/bin/env python3
"""Demo script for NBA Even-Skill Mispricing Strategy.

Tests the edge detection with even-skill assumption using simulated market data.
Demonstrates:
1. Both-direction trading (YES when market < fair value, NO when market > fair value)
2. Conservative/Moderate/Aggressive presets
3. Market staleness detection for trading past first half
4. Kelly sizing for position scaling

Run from project root:
    python test_results/nba_mispricing_demo.py
"""

import sys
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Tuple, List

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.simulation import (
    PairedMarketSimulator,
    MispricingConfig,
)


@dataclass
class SimulatedGameState:
    """Simulated NBA game state."""
    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int
    time_remaining: str

    @property
    def score_differential(self) -> int:
        return self.home_score - self.away_score


@dataclass
class AggressivenessConfig:
    """Aggressiveness configuration for trading."""
    name: str
    min_edge_cents: float
    position_scale_factor: float
    use_kelly_sizing: bool
    kelly_fraction: float = 0.25
    max_position_per_game: int = 100
    score_staleness_threshold: int = 15
    extend_past_first_half: bool = True


# Preset configurations
CONSERVATIVE = AggressivenessConfig(
    name="Conservative",
    min_edge_cents=5.0,
    position_scale_factor=0.5,
    use_kelly_sizing=False,
)

MODERATE = AggressivenessConfig(
    name="Moderate",
    min_edge_cents=3.0,
    position_scale_factor=1.0,
    use_kelly_sizing=False,
)

AGGRESSIVE = AggressivenessConfig(
    name="Aggressive",
    min_edge_cents=1.0,
    position_scale_factor=2.0,
    use_kelly_sizing=True,
)


def calculate_win_probability(score_diff: int, period: int, time_remaining_seconds: int) -> float:
    """
    Calculate win probability from score (assuming even skill).

    Uses a logistic model calibrated for NBA games. The fair value is purely
    based on the current score and time remaining, not team skill.
    """
    import numpy as np

    total_time = 2880  # 48 minutes

    if period <= 4:
        time_elapsed = (period - 1) * 720 + (720 - time_remaining_seconds)
    else:
        time_elapsed = total_time - 60

    game_completion = min(time_elapsed / total_time, 0.99)
    k = 0.35 + (2.5 * game_completion)
    base_prob = 1.0 / (1.0 + np.exp(-k * score_diff))

    if period >= 4:
        period_weight = 1.0
    else:
        period_weight = 0.6 + (0.4 * period / 4.0)

    win_prob = base_prob * (0.5 + 0.5 * period_weight)
    return float(np.clip(win_prob, 0.02, 0.98))


def parse_time_remaining(time_str: str) -> int:
    """Parse time string to seconds."""
    parts = time_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    return 0


def is_market_stale(score_diff: int, market_price: float, threshold: int = 15) -> bool:
    """Check if market hasn't adjusted to a lopsided score."""
    is_lopsided_score = abs(score_diff) >= threshold
    is_market_near_even = 0.40 <= market_price <= 0.60
    return is_lopsided_score and is_market_near_even


def calculate_edge(
    home_win_prob: float,
    market_mid: float,
    config: AggressivenessConfig
) -> Optional[Tuple[float, str]]:
    """
    Calculate edge between score-implied fair value and market price.

    Uses even-skill assumption: fair value is purely from score/time.
    Trade in BOTH directions depending on which side has edge.

    Returns:
        Tuple of (edge_cents, side_to_buy) or None if below threshold
    """
    fair_value_cents = home_win_prob * 100
    market_mid_cents = market_mid * 100

    # Calculate absolute edge
    edge = abs(fair_value_cents - market_mid_cents)

    if edge < config.min_edge_cents:
        return None

    # Determine direction
    if fair_value_cents > market_mid_cents:
        return (edge, "YES")  # Market underpricing, buy YES
    else:
        return (edge, "NO")  # Market overpricing, buy NO


def calculate_position_size(edge_cents: float, config: AggressivenessConfig, base_size: int = 10) -> int:
    """Calculate position size based on edge and aggressiveness settings."""
    if config.use_kelly_sizing:
        kelly_size = (edge_cents / 100) * config.kelly_fraction * base_size * 10
        return max(1, min(int(kelly_size), config.max_position_per_game))
    else:
        scaled = int(base_size * config.position_scale_factor)
        return max(1, min(scaled, config.max_position_per_game))


def run_demo():
    """Run the NBA even-skill mispricing strategy demo."""
    print("=" * 70)
    print("NBA EVEN-SKILL MISPRICING STRATEGY DEMO")
    print("=" * 70)
    print()
    print("Key concept: Assume both teams are evenly matched in skill.")
    print("Fair value comes from current score + time remaining only.")
    print("Trade whenever market differs from score-implied probability.")
    print()

    BASE_POSITION_SIZE = 10

    # Simulate game states covering different scenarios
    game_scenarios = [
        # First half scenarios
        SimulatedGameState("001", "LAL", "BOS", 28, 24, 1, "3:45"),   # Q1, LAL up 4
        SimulatedGameState("001", "LAL", "BOS", 52, 48, 2, "6:30"),   # Q2, LAL up 4
        SimulatedGameState("001", "LAL", "BOS", 45, 52, 2, "2:15"),   # Q2, LAL down 7
        SimulatedGameState("001", "LAL", "BOS", 58, 55, 2, "0:30"),   # Q2 end, LAL up 3
        # Staleness test: Q3 with lopsided score but market near 50%
        SimulatedGameState("001", "LAL", "BOS", 85, 65, 3, "5:00"),   # Q3, LAL up 20
        # Q3 without staleness (market has adjusted)
        SimulatedGameState("001", "LAL", "BOS", 78, 75, 3, "3:00"),   # Q3, close game
    ]

    # Simulated market prices (market mid in 0-1 scale)
    # Markets that are "stale" or mispriced relative to score
    market_mids = [
        0.52,  # Market says 52% LAL, but score implies ~58%
        0.48,  # Market says 48% LAL, but score implies ~55%
        0.55,  # Market says 55% LAL, but score implies ~42%
        0.50,  # Market says 50% LAL, but score implies ~54%
        0.55,  # STALE: Market says 55% LAL, but score implies ~85%
        0.52,  # Market close to fair value
    ]

    for config in [CONSERVATIVE, MODERATE, AGGRESSIVE]:
        print("=" * 70)
        print(f"PRESET: {config.name.upper()}")
        print("=" * 70)
        print(f"  Min edge: {config.min_edge_cents}c")
        print(f"  Position scale: {config.position_scale_factor}x")
        print(f"  Kelly sizing: {config.use_kelly_sizing}")
        if config.use_kelly_sizing:
            print(f"  Kelly fraction: {config.kelly_fraction}")
        print(f"  Staleness threshold: {config.score_staleness_threshold} points")
        print(f"  Extend past first half: {config.extend_past_first_half}")
        print()

        trades_executed = 0
        total_edge_captured = 0.0
        total_contracts = 0
        yes_trades = 0
        no_trades = 0
        staleness_trades = 0

        for i, (game, market_mid) in enumerate(zip(game_scenarios, market_mids)):
            print(f"--- Scenario {i+1}: Q{game.period} {game.time_remaining} remaining ---")
            print(f"Score: {game.home_team} {game.home_score} - {game.away_score} {game.away_team}")
            print(f"Score Differential: {game.score_differential:+d} (home perspective)")

            # Calculate score-implied win probability (even skill)
            time_secs = parse_time_remaining(game.time_remaining)
            home_win_prob = calculate_win_probability(
                game.score_differential,
                game.period,
                time_secs
            )

            print(f"Score-implied fair value: {home_win_prob:.1%} ({home_win_prob*100:.1f}c)")
            print(f"Market mid price: {market_mid:.1%} ({market_mid*100:.1f}c)")

            # Check period and staleness
            is_first_half = game.period <= 2
            stale = is_market_stale(game.score_differential, market_mid, config.score_staleness_threshold)

            if not is_first_half:
                if config.extend_past_first_half and stale:
                    print(f"  [STALE MARKET] Large score diff ({game.score_differential:+d}) but market near 50%")
                    print(f"  Allowing trade past first half due to staleness")
                elif not config.extend_past_first_half:
                    print(f"  >>> Past first half - SKIPPING <<<")
                    print()
                    continue
                else:
                    print(f"  >>> Past first half, market not stale - SKIPPING <<<")
                    print()
                    continue

            # Calculate edge
            edge_info = calculate_edge(home_win_prob, market_mid, config)

            if edge_info:
                edge_cents, side = edge_info
                position_size = calculate_position_size(edge_cents, config, BASE_POSITION_SIZE)

                print()
                print(f">>> MISPRICING DETECTED <<<")
                print(f"Edge: {edge_cents:.1f}c")
                print(f"Direction: {side} (market {'underpricing' if side == 'YES' else 'overpricing'} home)")
                print(f"Position size: {position_size} contracts")

                if side == "YES":
                    print(f"Action: BUY {game.home_team} YES")
                    yes_trades += 1
                else:
                    print(f"Action: BUY {game.home_team} NO")
                    no_trades += 1

                trades_executed += 1
                total_edge_captured += edge_cents
                total_contracts += position_size

                if not is_first_half and stale:
                    staleness_trades += 1

            else:
                print(f"  No actionable edge (threshold: {config.min_edge_cents}c)")

            print()

        # Summary for this preset
        print("-" * 70)
        print(f"SUMMARY ({config.name})")
        print("-" * 70)
        print(f"Scenarios evaluated: {len(game_scenarios)}")
        print(f"Trades executed: {trades_executed}")
        print(f"  YES trades: {yes_trades}")
        print(f"  NO trades: {no_trades}")
        print(f"  Staleness override trades: {staleness_trades}")
        print(f"Total contracts: {total_contracts}")
        print(f"Total edge captured: {total_edge_captured:.1f}c")
        if trades_executed > 0:
            print(f"Average edge per trade: {total_edge_captured/trades_executed:.1f}c")
            print(f"Average contracts per trade: {total_contracts/trades_executed:.1f}")
        print()
        print()

    # Final comparison
    print("=" * 70)
    print("PRESET COMPARISON")
    print("=" * 70)
    print()
    print("                    Conservative    Moderate    Aggressive")
    print("Min edge (cents)          5.0          3.0          1.0")
    print("Position scale            0.5x         1.0x         2.0x")
    print("Kelly sizing              No           No           Yes")
    print()
    print("Trade characteristics:")
    print("  Conservative: Fewer trades, larger edges only, smaller positions")
    print("  Moderate:     Balanced approach, reasonable edge threshold")
    print("  Aggressive:   More trades, small edges, scaled positions")
    print()
    print("=" * 70)
    print("Demo complete!")
    print("=" * 70)


if __name__ == "__main__":
    run_demo()
