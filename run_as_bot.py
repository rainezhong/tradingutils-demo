#!/usr/bin/env python3
"""Run an Avellaneda-Stoikov market-making bot on a Kalshi market.

DEMO VERSION - This is a demonstration version.
- Always runs in dry-run mode with mock clients
- No real API connections are made
- Strategy logic has been removed

Usage:
    python run_as_bot.py --ticker KXNBAGAME-XXX --dry-run
    python run_as_bot.py --list-markets --series KXNBAGAME

Examples:
    # List available (mock) markets
    python run_as_bot.py --list-markets --series KXNBAGAME

    # Simulate running on a ticker (demo mode)
    python run_as_bot.py --ticker DEMO-MARKET-001
"""

import argparse
import sys
import logging
from typing import Optional

# Add project root to path for imports
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# DEMO MODE: Force dry-run, use mock data
DEMO_MODE = True


def print_demo_banner():
    """Print demo mode banner."""
    print("=" * 60)
    print("  DEMO MODE - Trading Bot Demonstration Version")
    print("=" * 60)
    print("  - Always runs in dry-run mode")
    print("  - No real API connections are made")
    print("  - All market data is simulated")
    print("  - Strategy logic has been removed")
    print("=" * 60)
    print()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run an Avellaneda-Stoikov market-making bot on Kalshi (DEMO)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Market selection
    market_group = parser.add_argument_group("Market Selection")
    market_group.add_argument(
        "--ticker",
        type=str,
        help="Market ticker to trade (e.g., KXNBAGAME-26JAN21-CHI)",
    )
    market_group.add_argument(
        "--list-markets",
        action="store_true",
        help="List available markets and exit",
    )
    market_group.add_argument(
        "--series",
        type=str,
        default="KXNBAGAME",
        help="Series filter for --list-markets (default: KXNBAGAME)",
    )

    # AS model parameters (shown but not used in demo)
    as_group = parser.add_argument_group("AS Model Parameters (demo only)")
    as_group.add_argument(
        "--gamma",
        type=float,
        default=0.05,
        help="Risk aversion parameter (default: 0.05)",
    )
    as_group.add_argument(
        "--k",
        type=float,
        default=25.0,
        help="Liquidity slope parameter (default: 25.0)",
    )
    as_group.add_argument(
        "--horizon",
        type=float,
        default=300.0,
        help="Time horizon in seconds (default: 300)",
    )

    # Trading parameters
    trading_group = parser.add_argument_group("Trading Parameters (demo only)")
    trading_group.add_argument(
        "--max-position",
        type=int,
        default=10,
        help="Maximum position size in contracts (default: 10)",
    )
    trading_group.add_argument(
        "--quote-size",
        type=int,
        default=1,
        help="Size of each quote in contracts (default: 1)",
    )

    # Execution mode
    exec_group = parser.add_argument_group("Execution Mode")
    exec_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode - no real orders placed (default: True)",
    )
    exec_group.add_argument(
        "--live",
        action="store_true",
        help="Live trading mode - disabled in demo",
    )

    # Logging
    log_group = parser.add_argument_group("Logging")
    log_group.add_argument(
        "--verbose", "-v",
        action="count",
        default=0,
        help="Increase verbosity (-v for INFO, -vv for DEBUG)",
    )
    log_group.add_argument(
        "--log-file",
        type=str,
        help="Log to file in addition to console",
    )

    return parser.parse_args()


def setup_logging(verbosity: int, log_file: Optional[str] = None):
    """Configure logging based on verbosity level."""
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING

    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def main():
    """Main entry point."""
    print_demo_banner()

    args = parse_args()
    setup_logging(args.verbose, args.log_file)

    # DEMO MODE: Force dry-run
    if args.live:
        print("DEMO MODE: Live trading is disabled. Running in dry-run mode.")
        args.live = False
        args.dry_run = True

    if args.list_markets:
        # Show mock market list
        print(f"\nDemo markets (mock data):")
        print("-" * 60)
        print(f"{'Ticker':<40} {'Title':<20}")
        print("-" * 60)
        mock_markets = [
            ("DEMO-MARKET-001", "Demo Market 1"),
            ("DEMO-MARKET-002", "Demo Market 2"),
            ("DEMO-MARKET-003", "Demo Market 3"),
            ("DEMO-NBA-GAME-LAL", "Lakers vs Celtics"),
            ("DEMO-NBA-GAME-GSW", "Warriors vs Suns"),
        ]
        for ticker, title in mock_markets:
            print(f"{ticker:<40} {title:<20}")
        print("-" * 60)
        print("Note: This is demo mode. Real markets are not available.")
        return

    if not args.ticker:
        print("Error: --ticker is required (or use --list-markets)")
        sys.exit(1)

    # Show configuration (but don't actually run)
    print(f"\nDEMO MODE Configuration:")
    print("-" * 60)
    print(f"  Ticker:        {args.ticker}")
    print(f"  Gamma:         {args.gamma}")
    print(f"  K:             {args.k}")
    print(f"  Horizon:       {args.horizon}s")
    print(f"  Max Position:  {args.max_position}")
    print(f"  Quote Size:    {args.quote_size}")
    print(f"  Dry Run:       {args.dry_run}")
    print("-" * 60)
    print()
    print("Note: Bot functionality is disabled in demo mode.")
    print("In a real implementation, this would:")
    print("  1. Connect to Kalshi API")
    print("  2. Fetch market orderbook data")
    print("  3. Calculate optimal quotes using A-S model")
    print("  4. Place/update orders based on market conditions")
    print()
    print("Demo complete.")


if __name__ == "__main__":
    main()
