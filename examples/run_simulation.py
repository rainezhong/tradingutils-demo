#!/usr/bin/env python3
"""Example usage of the simulation framework.

Demonstrates how to:
1. Create market simulators with different behaviors
2. Use pre-built scenarios
3. Run a simple trading strategy
4. Analyze simulation results

Run from project root:
    python examples/run_simulation.py
"""

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.simulation import (
    MarketSimulator,
    SimulatedAPIClient,
    TrendingSimulator,
    MeanRevertingSimulator,
    create_api_client,
    get_scenario,
    list_scenarios,
    run_simulation,
    stable_market,
    volatile_market,
    trending_up,
    mean_reverting,
)


def demo_basic_simulator():
    """Demonstrate basic MarketSimulator usage."""
    print("=" * 60)
    print("DEMO 1: Basic Market Simulator")
    print("=" * 60)

    # Create a simple simulator
    sim = MarketSimulator(
        ticker="DEMO-MKT",
        initial_mid=0.50,
        volatility=0.02,
        spread_range=(0.03, 0.05),
        seed=42,  # For reproducibility
    )

    # Generate a sequence of market states
    print("\nGenerating 10 market states...")
    states = sim.simulate_sequence(10)

    for i, state in enumerate(states):
        print(f"  Step {i+1}: bid={state.bid:.4f}, ask={state.ask:.4f}, "
              f"mid={state.mid:.4f}, spread={state.spread:.4f}")

    print(f"\nFinal mid price: {sim.mid_price:.4f}")
    print(f"Total steps: {sim.step_count}")


def demo_trending_simulator():
    """Demonstrate trending market simulation."""
    print("\n" + "=" * 60)
    print("DEMO 2: Trending Market Simulator")
    print("=" * 60)

    # Create upward trending simulator
    sim_up = TrendingSimulator(
        ticker="TREND-UP",
        initial_mid=0.30,
        volatility=0.01,
        drift=0.005,  # Positive drift = upward trend
        seed=42,
    )

    # Create downward trending simulator
    sim_down = TrendingSimulator(
        ticker="TREND-DOWN",
        initial_mid=0.70,
        volatility=0.01,
        drift=-0.005,  # Negative drift = downward trend
        seed=42,
    )

    print("\nRunning 50 steps each...")

    states_up = sim_up.simulate_sequence(50)
    states_down = sim_down.simulate_sequence(50)

    print(f"\nUpward trend: {0.30:.2f} -> {states_up[-1].mid:.2f}")
    print(f"Downward trend: {0.70:.2f} -> {states_down[-1].mid:.2f}")


def demo_mean_reverting_simulator():
    """Demonstrate mean-reverting market simulation."""
    print("\n" + "=" * 60)
    print("DEMO 3: Mean-Reverting Market Simulator")
    print("=" * 60)

    sim = MeanRevertingSimulator(
        ticker="MEAN-REV",
        initial_mid=0.80,  # Start far from fair value
        fair_value=0.50,
        reversion_speed=0.15,
        volatility=0.02,
        seed=42,
    )

    print(f"\nStarting at {sim.mid_price:.2f}, fair value = 0.50")
    print("Running 100 steps...")

    states = sim.simulate_sequence(100)

    # Show trajectory
    checkpoints = [0, 24, 49, 74, 99]
    for i in checkpoints:
        print(f"  Step {i+1}: mid = {states[i].mid:.4f}")

    print(f"\nFinal mid: {states[-1].mid:.4f} (reverted toward 0.50)")


def demo_simulated_api():
    """Demonstrate SimulatedAPIClient for order management."""
    print("\n" + "=" * 60)
    print("DEMO 4: Simulated API Client")
    print("=" * 60)

    # Create client with stable market
    client = stable_market("API-DEMO")

    # Get initial market state
    market = client.get_market_data("API-DEMO")
    print(f"\nInitial market: bid={market.bid:.4f}, ask={market.ask:.4f}")

    # Place some orders
    print("\nPlacing orders...")

    # Order that should fill (aggressive buy at ask)
    order1 = client.place_order("API-DEMO", "buy", market.ask + 0.05, 10)
    print(f"  Order 1 (aggressive buy): {order1[:8]}...")

    # Order that won't fill immediately (passive bid below market)
    order2 = client.place_order("API-DEMO", "buy", market.bid - 0.10, 5)
    print(f"  Order 2 (passive bid): {order2[:8]}...")

    # Check order statuses
    status1 = client.get_order_status(order1)
    status2 = client.get_order_status(order2)

    print(f"\nOrder 1 status: {status1['status']}, filled: {status1['filled_size']}")
    print(f"Order 2 status: {status2['status']}, filled: {status2['filled_size']}")

    # Run some simulation steps
    print("\nRunning 20 simulation steps...")
    states = client.run_steps(20)

    # Check if passive order filled
    status2 = client.get_order_status(order2)
    print(f"Order 2 after steps: {status2['status']}")

    # Show fills
    fills = client.get_all_fills()
    print(f"\nTotal fills: {len(fills)}")
    for fill in fills[:3]:  # Show first 3
        print(f"  {fill.side} {fill.size} @ {fill.price:.4f}")


def demo_scenarios():
    """Demonstrate pre-built scenarios."""
    print("\n" + "=" * 60)
    print("DEMO 5: Pre-built Scenarios")
    print("=" * 60)

    print("\nAvailable scenarios:")
    for name in list_scenarios():
        config = get_scenario(name)
        print(f"  - {name}: {config.description}")

    # Test each scenario
    print("\nTesting scenarios (20 steps each)...")
    for name in ["stable_market", "volatile_market", "trending_up", "mean_reverting"]:
        client = create_api_client(get_scenario(name))
        states = client.run_steps(20)

        mids = [s.mid for s in states]
        volatility = max(mids) - min(mids)
        print(f"  {name}: range = {volatility:.4f}")


def demo_strategy_simulation():
    """Demonstrate running a trading strategy through simulation."""
    print("\n" + "=" * 60)
    print("DEMO 6: Strategy Simulation")
    print("=" * 60)

    client = create_api_client(get_scenario("stable_market"))
    orders_placed = 0
    last_order_step = -10

    def simple_strategy(c: SimulatedAPIClient) -> None:
        """Simple strategy: place a bid every 10 steps."""
        nonlocal orders_placed, last_order_step

        # Only place order every 10 steps
        step = c.simulator.step_count
        if step - last_order_step >= 10:
            market = c.get_market_data("SIM-MARKET")
            # Place a passive bid
            c.place_order("SIM-MARKET", "buy", market.bid, 5)
            orders_placed += 1
            last_order_step = step

    print("\nRunning strategy for 100 steps...")
    result = run_simulation(
        client=client,
        strategy=simple_strategy,
        n_steps=100,
        scenario_name="stable_market",
    )

    print(f"\nResults:")
    print(f"  Steps run: {result.n_steps}")
    print(f"  Orders placed: {orders_placed}")
    print(f"  Fills: {result.n_fills}")
    print(f"  Volume traded: {result.total_volume}")
    print(f"  Final position: {result.final_position}")

    # Show price path summary
    print(f"\nPrice path:")
    print(f"  Start: {result.price_path[0]:.4f}")
    print(f"  End: {result.price_path[-1]:.4f}")
    print(f"  Min: {min(result.price_path):.4f}")
    print(f"  Max: {max(result.price_path):.4f}")


def main():
    """Run all demos."""
    print("SIMULATION FRAMEWORK DEMO")
    print("=" * 60)

    demo_basic_simulator()
    demo_trending_simulator()
    demo_mean_reverting_simulator()
    demo_simulated_api()
    demo_scenarios()
    demo_strategy_simulation()

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
