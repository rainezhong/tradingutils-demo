#!/usr/bin/env python3
"""Demo script for the automated backtest runner agent.

Shows how to use the BacktestRunnerAgent to test hypotheses
with full statistical validation.
"""

import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.backtest_runner import BacktestRunnerAgent

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)


def demo_crypto_backtest():
    """Demonstrate crypto latency arbitrage backtest."""
    print("\n" + "=" * 70)
    print("Crypto Latency Arbitrage Backtest")
    print("=" * 70 + "\n")

    # Check if data exists
    db_path = Path("data/btc_latency_probe.db")
    if not db_path.exists():
        print(f"Data file not found: {db_path}")
        print("Run the latency probe first to collect data:")
        print("  python3 scripts/latency_probe/run.py crypto --duration 3600")
        return

    # Initialize agent
    agent = BacktestRunnerAgent(
        db_path="data/backtest_results.db",
        enable_walk_forward=False,  # Disable for faster demo
        enable_sensitivity=True,  # Test parameter robustness
    )

    # Define hypothesis
    hypothesis = (
        "BTC spot price changes on Kraken predict Kalshi binary option mispricing "
        "in the 120-900 second time-to-expiry window"
    )

    # Adapter configuration
    adapter_config = {
        "type": "crypto-latency",
        "params": {
            "vol": 0.30,  # 30% annualized volatility
            "min_edge": 0.10,  # 10% minimum edge
            "slippage_cents": 3,  # 3 cent slippage
            "min_ttx_sec": 120,  # Min 2 minutes to expiry
            "max_ttx_sec": 900,  # Max 15 minutes to expiry
            "kelly_fraction": 0.5,  # Half Kelly sizing
            "max_bet_dollars": 50.0,  # Max $50 per trade
        },
    }

    # Data configuration
    data_config = {
        "type": "crypto",
        "path": str(db_path),
        "use_spot_price": True,
    }

    # Run backtest with validation
    print("Running backtest...\n")
    results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

    # Print results
    print(results.summary())

    # Export to JSON
    import json

    output_file = Path("data/backtest_results_demo.json")
    with open(output_file, "w") as f:
        json.dump(results.to_dict(), f, indent=2)
    print(f"\nResults saved to: {output_file}")


def demo_nba_backtest():
    """Demonstrate NBA mispricing backtest."""
    print("\n" + "=" * 70)
    print("NBA Mispricing Backtest")
    print("=" * 70 + "\n")

    # Find a recording file
    recordings_dir = Path("data/recordings")
    if not recordings_dir.exists():
        print(f"Recordings directory not found: {recordings_dir}")
        print("Run the NBA game recorder first to collect data")
        return

    # Find first available recording
    recordings = list(recordings_dir.glob("*.json"))
    if not recordings:
        print(f"No recording files found in {recordings_dir}")
        return

    recording_path = recordings[0]
    print(f"Using recording: {recording_path.name}\n")

    # Initialize agent
    agent = BacktestRunnerAgent(
        db_path="data/backtest_results.db",
        enable_walk_forward=False,
        enable_sensitivity=True,
    )

    # Define hypothesis
    hypothesis = (
        "Early-game score differentials (Q1-Q2) in NBA games create exploitable "
        "mispricings when compared to win probability model"
    )

    # Adapter configuration
    adapter_config = {
        "type": "nba-mispricing",
        "params": {
            "min_edge_cents": 3.0,  # 3 cent minimum edge
            "max_period": 2,  # Only Q1-Q2
            "position_size": 10,  # 10 contracts per trade
        },
    }

    # Data configuration
    data_config = {"type": "nba", "path": str(recording_path)}

    # Run backtest
    print("Running backtest...\n")
    results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

    # Print results
    print(results.summary())


def demo_blowout_backtest():
    """Demonstrate late-game blowout backtest."""
    print("\n" + "=" * 70)
    print("Late-Game Blowout Backtest")
    print("=" * 70 + "\n")

    # Find a recording file
    recordings_dir = Path("data/recordings")
    if not recordings_dir.exists():
        print(f"Recordings directory not found: {recordings_dir}")
        return

    recordings = list(recordings_dir.glob("*.json"))
    if not recordings:
        print(f"No recording files found in {recordings_dir}")
        return

    recording_path = recordings[0]
    print(f"Using recording: {recording_path.name}\n")

    # Initialize agent
    agent = BacktestRunnerAgent(
        db_path="data/backtest_results.db",
        enable_walk_forward=False,
        enable_sensitivity=True,
    )

    # Define hypothesis
    hypothesis = (
        "Large point differentials (>10 pts) in the final 10 minutes of NBA games "
        "lead to market overreaction that can be faded"
    )

    # Adapter configuration
    adapter_config = {
        "type": "blowout",
        "params": {
            "min_point_differential": 10,  # 10 point minimum lead
            "max_time_remaining_seconds": 600,  # Last 10 minutes
            "base_position_size": 5.0,  # Base position size
            "one_trade_per_game": True,  # Only one entry per game
        },
    }

    # Data configuration
    data_config = {"type": "nba", "path": str(recording_path)}

    # Run backtest
    print("Running backtest...\n")
    results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

    # Print results
    print(results.summary())


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Demo backtest runner agent")
    parser.add_argument(
        "strategy",
        choices=["crypto", "nba", "blowout"],
        help="Strategy to backtest",
    )
    args = parser.parse_args()

    if args.strategy == "crypto":
        demo_crypto_backtest()
    elif args.strategy == "nba":
        demo_nba_backtest()
    elif args.strategy == "blowout":
        demo_blowout_backtest()
