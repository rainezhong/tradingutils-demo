#!/usr/bin/env python3
"""
NBA Underdog Value Betting Strategy Runner

Based on empirical analysis showing 15-20¢ underdogs have +12.40¢ EV per $1.

Usage:
    # Default (moderate preset: 15-20¢ range)
    python3 scripts/run_nba_underdog.py

    # Conservative (5 contracts, only 15-20¢)
    python3 scripts/run_nba_underdog.py --preset conservative

    # Aggressive (20 contracts, 10-30¢ range + favorites)
    python3 scripts/run_nba_underdog.py --preset aggressive

    # Custom parameters
    python3 scripts/run_nba_underdog.py --min-price 10 --max-price 25 --position-size 15

    # Dry run (no real orders)
    python3 scripts/run_nba_underdog.py --dry-run
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.nba_underdog_strategy import NBAUnderdogStrategy, NBAUnderdogConfig
from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient


def setup_logging(verbose: bool = False):
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


async def main():
    parser = argparse.ArgumentParser(
        description="NBA Underdog Value Betting Strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Empirical Performance (from historical analysis):
  15-20¢ underdogs: +12.40¢ per $1 EV (103 games, 30.1% win rate vs 17.7% implied)
  10-30¢ underdogs: +6.77¢ per $1 EV (401 games, ROI: 30.7%)

Presets:
  conservative: 15-20¢ only, 5 contracts, max 10 positions
  moderate:     10-30¢ range, 10 contracts, max 20 positions (default)
  aggressive:   10-30¢ + favorites, 20 contracts, max 40 positions

Examples:
  python3 scripts/run_nba_underdog.py --preset conservative
  python3 scripts/run_nba_underdog.py --min-price 15 --max-price 20 --position-size 10
  python3 scripts/run_nba_underdog.py --dry-run  # Test without placing real orders
        """,
    )

    parser.add_argument(
        "--preset",
        choices=["conservative", "moderate", "aggressive"],
        help="Use a preset configuration",
    )
    parser.add_argument(
        "--min-price",
        type=int,
        help="Minimum underdog price in cents (default: 15)",
    )
    parser.add_argument(
        "--max-price",
        type=int,
        help="Maximum underdog price in cents (default: 20)",
    )
    parser.add_argument(
        "--position-size",
        type=int,
        help="Number of contracts per bet",
    )
    parser.add_argument(
        "--max-positions",
        type=int,
        help="Maximum concurrent positions",
    )
    parser.add_argument(
        "--enable-favorites",
        action="store_true",
        help="Also bet on high-value favorites (90-100¢)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan markets but don't place orders",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    logger = logging.getLogger(__name__)

    # Build config
    if args.preset:
        if args.preset == "conservative":
            config = NBAUnderdogConfig.conservative()
        elif args.preset == "aggressive":
            config = NBAUnderdogConfig.aggressive()
        else:
            config = NBAUnderdogConfig.moderate()
        logger.info(f"Using {args.preset} preset")
    else:
        config = NBAUnderdogConfig()
        logger.info("Using default (moderate) configuration")

    # Apply custom parameters
    if args.min_price is not None:
        config.min_price_cents = args.min_price
    if args.max_price is not None:
        config.max_price_cents = args.max_price
    if args.position_size is not None:
        config.position_size = args.position_size
    if args.max_positions is not None:
        config.max_positions = args.max_positions
    if args.enable_favorites:
        config.enable_favorites = True

    # Log configuration
    logger.info("=" * 60)
    logger.info("NBA UNDERDOG VALUE BETTING STRATEGY")
    logger.info("=" * 60)
    logger.info(f"Price range: {config.min_price_cents}-{config.max_price_cents}¢")
    logger.info(f"Position size: {config.position_size} contracts")
    logger.info(f"Max positions: {config.max_positions}")
    logger.info(f"Favorites enabled: {config.enable_favorites}")
    if config.enable_favorites:
        logger.info(f"Favorite range: {config.favorite_min_price_cents}-{config.favorite_max_price_cents}¢")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info("=" * 60)

    if args.dry_run:
        logger.warning("DRY RUN MODE - No orders will be placed")

    # Initialize exchange client
    logger.info("Initializing Kalshi exchange client...")
    try:
        exchange = KalshiExchangeClient.from_env()
    except Exception:
        logger.info("Production credentials not found, trying demo...")
        exchange = KalshiExchangeClient.from_env(demo=True)
    await exchange.connect()

    try:
        # Create strategy
        strategy = NBAUnderdogStrategy(exchange, config, dry_run=args.dry_run)

        # Run
        logger.info("Starting strategy loop (Ctrl+C to stop)...")
        await strategy.run()

    except KeyboardInterrupt:
        logger.info("Strategy stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1
    finally:
        await exchange.exit()
        logger.info("Exchange client closed")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
