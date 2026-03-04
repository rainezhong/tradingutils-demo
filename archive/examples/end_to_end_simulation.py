#!/usr/bin/env python3
"""End-to-end simulation example demonstrating the complete market-making system.

This example shows:
1. Setting up the full integration stack
2. Running a market-making simulation
3. Monitoring performance and risk metrics
4. Multi-market operation

Run from project root:
    python examples/end_to_end_simulation.py
"""

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import RiskConfig as CoreRiskConfig
from src.engine import MarketMakingEngine, MultiMarketEngine
from src.engine.multi_market_engine import StatusPrinter
from src.execution.mock_api_client import MockAPIClient
from src.market_making.config import MarketMakerConfig
from src.market_making.models import MarketState
from src.simulation import (
    MarketSimulator,
    create_simulator,
    get_scenario,
)


def demo_single_market_simulation():
    """Demonstrate single-market simulation."""
    print("=" * 70)
    print("DEMO 1: Single Market Simulation")
    print("=" * 70)

    # Create API client (mock for simulation)
    api_client = MockAPIClient()

    # Configure strategy
    mm_config = MarketMakerConfig(
        target_spread=0.04,  # 4% target spread
        quote_size=10,
        max_position=50,
        inventory_skew_factor=0.01,
    )

    # Configure risk limits
    risk_config = CoreRiskConfig(
        max_position_size=50,
        max_total_position=100,
        max_loss_per_position=25.0,
        max_daily_loss=100.0,
    )

    # Create engine
    engine = MarketMakingEngine(
        ticker="SIM-SINGLE",
        api_client=api_client,
        mm_config=mm_config,
        risk_config=risk_config,
    )

    # Create simulator
    simulator = MarketSimulator(
        ticker="SIM-SINGLE",
        initial_mid=0.50,
        volatility=0.02,
        spread_range=(0.03, 0.06),
        seed=42,
    )

    print("\nConfiguration:")
    print(f"  Target Spread: {mm_config.target_spread:.1%}")
    print(f"  Quote Size: {mm_config.quote_size}")
    print(f"  Max Position: {mm_config.max_position}")
    print(f"  Max Daily Loss: ${risk_config.max_daily_loss}")
    print()

    # Run simulation
    n_steps = 100
    print(f"Running {n_steps} simulation steps...")
    print()

    for i in range(n_steps):
        # Generate market state from simulator
        sim_state = simulator.generate_market_state()

        # Convert to engine's expected format
        market = MarketState(
            ticker="SIM-SINGLE",
            timestamp=sim_state.timestamp,
            best_bid=sim_state.bid,
            best_ask=sim_state.ask,
            mid_price=sim_state.mid,
            bid_size=100,
            ask_size=100,
        )

        # Process market update
        engine.on_market_update(market)

        # Print progress every 25 steps
        if (i + 1) % 25 == 0:
            status = engine.get_status()
            pos = status["market_maker"]["position"]
            print(
                f"  Step {i + 1:3d}: mid={sim_state.mid:.4f}, "
                f"pos={pos['contracts']:+3d}, "
                f"pnl=${pos['total_pnl']:+7.2f}"
            )

    # Final results
    status = engine.get_status()
    pos = status["market_maker"]["position"]
    stats = status["market_maker"]["stats"]
    risk = status["risk"]

    print()
    print("=" * 50)
    print("SIMULATION RESULTS")
    print("=" * 50)
    print(f"Position: {pos['contracts']} contracts")
    print(f"Avg Entry: {pos['avg_entry_price']:.4f}")
    print(f"Unrealized P&L: ${pos['unrealized_pnl']:+.2f}")
    print(f"Realized P&L: ${pos['realized_pnl']:+.2f}")
    print(f"Total P&L: ${pos['total_pnl']:+.2f}")
    print()
    print(f"Quotes Generated: {stats['quotes_generated']}")
    print(f"Fills: {stats['quotes_filled']}")
    print(f"Volume: {stats['total_volume']}")
    print()
    print("Risk Utilization:")
    print(f"  Position: {risk['position_limit_utilization']:.1%}")
    print(f"  Total Position: {risk['total_limit_utilization']:.1%}")
    print(f"  Daily Loss: {risk['daily_loss_utilization']:.1%}")


def demo_multi_market_simulation():
    """Demonstrate multi-market simulation with different scenarios."""
    print("\n" + "=" * 70)
    print("DEMO 2: Multi-Market Simulation")
    print("=" * 70)

    # Create shared API client
    api_client = MockAPIClient()

    # Create multi-market engine
    multi_engine = MultiMarketEngine(
        api_client=api_client,
        default_mm_config=MarketMakerConfig(
            target_spread=0.04,
            quote_size=10,
            max_position=30,
        ),
        global_risk_config=CoreRiskConfig(
            max_position_size=30,
            max_total_position=150,  # 5 markets x 30
            max_loss_per_position=20.0,
            max_daily_loss=200.0,
        ),
    )

    # Add markets with different scenarios
    scenarios = [
        ("STABLE-MKT", "stable_market", "Stable, low volatility"),
        ("VOLATILE-MKT", "volatile_market", "High volatility"),
        ("TREND-UP", "trending_up", "Trending upward"),
        ("TREND-DOWN", "trending_down", "Trending downward"),
        ("MEAN-REV", "mean_reverting", "Mean reverting"),
    ]

    simulators = {}

    print("\nMarkets:")
    for ticker, scenario_name, description in scenarios:
        multi_engine.add_market(ticker)
        config = get_scenario(scenario_name)
        simulators[ticker] = create_simulator(config, ticker)
        print(f"  {ticker}: {description}")

    print()

    # Create status printer
    printer = StatusPrinter(multi_engine)

    # Run simulation
    n_steps = 50
    print(f"Running {n_steps} simulation steps across all markets...")
    print()

    for i in range(n_steps):
        # Update each market
        for ticker, simulator in simulators.items():
            sim_state = simulator.generate_market_state()

            market = MarketState(
                ticker=ticker,
                timestamp=sim_state.timestamp,
                best_bid=sim_state.bid,
                best_ask=sim_state.ask,
                mid_price=sim_state.mid,
                bid_size=100,
                ask_size=100,
            )

            multi_engine.on_market_update(ticker, market)

        # Print status every 10 steps
        if (i + 1) % 10 == 0:
            print(f"Step {i + 1}: {printer.format_compact()}")

    # Final status
    printer.print_status(force=True)


def demo_risk_management():
    """Demonstrate risk management in action."""
    print("\n" + "=" * 70)
    print("DEMO 3: Risk Management")
    print("=" * 70)

    api_client = MockAPIClient()

    # Very tight risk limits for demonstration
    risk_config = CoreRiskConfig(
        max_position_size=20,
        max_total_position=40,
        max_loss_per_position=10.0,
        max_daily_loss=25.0,
    )

    engine = MarketMakingEngine(
        ticker="RISK-TEST",
        api_client=api_client,
        mm_config=MarketMakerConfig(
            target_spread=0.04,
            quote_size=10,
            max_position=20,
        ),
        risk_config=risk_config,
    )

    # Create volatile simulator
    simulator = MarketSimulator(
        ticker="RISK-TEST",
        initial_mid=0.50,
        volatility=0.05,  # High volatility
        seed=42,
    )

    print("\nTight Risk Limits:")
    print(f"  Max Position: {risk_config.max_position_size}")
    print(f"  Max Daily Loss: ${risk_config.max_daily_loss}")
    print()

    print("Running until risk limit triggered...")
    print()

    for i in range(200):
        sim_state = simulator.generate_market_state()

        market = MarketState(
            ticker="RISK-TEST",
            timestamp=sim_state.timestamp,
            best_bid=sim_state.bid,
            best_ask=sim_state.ask,
            mid_price=sim_state.mid,
            bid_size=100,
            ask_size=100,
        )

        engine.on_market_update(market)

        # Check if trading halted
        if not engine.risk_manager.is_trading_allowed():
            print(f"Step {i + 1}: TRADING HALTED - Risk limit breached!")
            break

        # Print every 20 steps
        if (i + 1) % 20 == 0:
            metrics = engine.risk_manager.get_risk_metrics()
            print(
                f"Step {i + 1}: loss_util={metrics['daily_loss_utilization']:.1%}, "
                f"pos_util={metrics['position_limit_utilization']:.1%}"
            )

    # Final status
    status = engine.get_status()
    print()
    print("Final State:")
    print(f"  Trading Allowed: {engine.risk_manager.is_trading_allowed()}")
    print(f"  Force Closes: {status['engine']['force_closes']}")
    print(f"  Daily P&L: ${engine.risk_manager.daily_pnl:.2f}")


def demo_scenario_comparison():
    """Compare performance across different market scenarios."""
    print("\n" + "=" * 70)
    print("DEMO 4: Scenario Comparison")
    print("=" * 70)

    scenarios_to_test = [
        "stable_market",
        "volatile_market",
        "trending_up",
        "trending_down",
        "mean_reverting",
    ]

    results = []
    n_steps = 100

    print(f"\nRunning {n_steps} steps for each scenario...")
    print()

    for scenario_name in scenarios_to_test:
        api_client = MockAPIClient()
        config = get_scenario(scenario_name)
        simulator = create_simulator(config, "TEST")

        engine = MarketMakingEngine(
            ticker="TEST",
            api_client=api_client,
            mm_config=MarketMakerConfig(
                target_spread=0.04,
                quote_size=10,
                max_position=50,
            ),
            risk_config=CoreRiskConfig(
                max_position_size=50,
                max_total_position=100,
                max_loss_per_position=25.0,
                max_daily_loss=100.0,
            ),
        )

        for _ in range(n_steps):
            sim_state = simulator.generate_market_state()

            market = MarketState(
                ticker="TEST",
                timestamp=sim_state.timestamp,
                best_bid=sim_state.bid,
                best_ask=sim_state.ask,
                mid_price=sim_state.mid,
                bid_size=100,
                ask_size=100,
            )

            engine.on_market_update(market)

        status = engine.get_status()
        pos = status["market_maker"]["position"]
        stats = status["market_maker"]["stats"]

        results.append(
            {
                "scenario": scenario_name,
                "pnl": pos["total_pnl"],
                "fills": stats["quotes_filled"],
                "volume": stats["total_volume"],
                "final_pos": pos["contracts"],
            }
        )

    # Print comparison
    print("=" * 70)
    print(f"{'Scenario':<20} {'P&L':>10} {'Fills':>8} {'Volume':>10} {'Final Pos':>10}")
    print("=" * 70)

    for r in results:
        print(
            f"{r['scenario']:<20} "
            f"${r['pnl']:>+9.2f} "
            f"{r['fills']:>8d} "
            f"{r['volume']:>10d} "
            f"{r['final_pos']:>+10d}"
        )


def main():
    """Run all demos."""
    print()
    print("=" * 70)
    print("END-TO-END MARKET-MAKING SIMULATION")
    print("=" * 70)
    print()
    print("This example demonstrates the complete integration of:")
    print("  - MarketMakingEngine (strategy)")
    print("  - QuoteManager (execution)")
    print("  - RiskManager (safety)")
    print("  - MarketSimulator (testing)")
    print()

    demo_single_market_simulation()
    demo_multi_market_simulation()
    demo_risk_management()
    demo_scenario_comparison()

    print()
    print("=" * 70)
    print("All demos complete!")
    print("=" * 70)


if __name__ == "__main__":
    main()
