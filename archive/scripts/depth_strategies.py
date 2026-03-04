#!/usr/bin/env python3
"""
Depth-Based Trading Strategies CLI

Two strategies available:

1. Liquidity Provider (--mode liquidity)
   - Posts passive orders inside the spread
   - Captures spread when both sides fill
   - Lower risk, more consistent

2. Depth Scalper (--mode scalp)
   - Aggressively sweeps thin orderbooks
   - Immediately exits for profit
   - Higher risk, more volatile
   - Best in 4th quarter of NBA totals (use --4th-quarter)

Usage:
    # Dry run liquidity provider on NBA totals
    python scripts/depth_strategies.py --mode liquidity --sport nba_totals

    # Live scalping (REAL MONEY)
    python scripts/depth_strategies.py --mode scalp --sport nba_totals --live

    # Scalping only in 4th quarter (recommended for NBA totals)
    python scripts/depth_strategies.py --mode scalp --sport nba_totals --4th-quarter --live

    # 4th quarter with <5 minutes remaining (crunch time)
    python scripts/depth_strategies.py --mode scalp --4th-quarter --max-minutes 5 --live

    # Custom parameters
    python scripts/depth_strategies.py --mode liquidity --min-spread 8 --quote-size 15
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from enum import Enum
from typing import List, Optional, Set

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kalshi_utils.client_wrapper import KalshiWrapped
from src.strategies.liquidity_provider import LiquidityConfig, LiquidityProvider
from src.strategies.depth_scalper import DepthScalper, ScalpConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class StrategyMode(Enum):
    LIQUIDITY = "liquidity"
    SCALP = "scalp"


class SportType(Enum):
    NBA = "nba"
    NBA_TOTALS = "nba_totals"
    NCAAB = "ncaab"
    NHL = "nhl"
    UCL = "ucl"
    TENNIS = "tennis"


def get_markets(wrapper: KalshiWrapped, sport: SportType, status: str = "open") -> List:
    """Get markets for the specified sport."""
    if sport == SportType.NBA:
        return wrapper.GetAllNBAMarkets(status=status)
    elif sport == SportType.NBA_TOTALS:
        return wrapper.GetAllNBATotalMarkets(status=status)
    elif sport == SportType.NCAAB:
        return wrapper.GetALLNCAAMBMarkets(status=status)
    elif sport == SportType.NHL:
        return wrapper.GetAllNHLMarkets(status=status)
    elif sport == SportType.UCL:
        return wrapper.GetAllUCLMarkets(status=status)
    elif sport == SportType.TENNIS:
        return wrapper.GetALLTennisMarkets(status=status)
    return []


def filter_markets_by_spread(
    markets: List, min_spread: float, max_spread: float = 100
) -> List[str]:
    """Filter markets by spread and return tickers."""
    tickers = []
    for m in markets:
        data = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
        yes_bid = (data.get("yes_bid") or 0) / 100.0
        yes_ask = (data.get("yes_ask") or 100) / 100.0
        spread_cents = (yes_ask - yes_bid) * 100

        if spread_cents >= min_spread and spread_cents <= max_spread:
            tickers.append(data.get("ticker", ""))

    return [t for t in tickers if t]


def filter_markets_by_tickers(markets: List, tickers: Optional[Set[str]]) -> List[str]:
    """Filter markets to specific tickers."""
    if not tickers:
        return [
            (m.model_dump() if hasattr(m, "model_dump") else m.__dict__).get(
                "ticker", ""
            )
            for m in markets
        ]

    result = []
    for m in markets:
        data = m.model_dump() if hasattr(m, "model_dump") else m.__dict__
        ticker = data.get("ticker", "")
        event_ticker = data.get("event_ticker", "")
        if ticker in tickers or event_ticker in tickers:
            result.append(ticker)

    return result


async def run_liquidity_provider(
    tickers: List[str],
    config: LiquidityConfig,
    dry_run: bool,
    use_polling: bool = False,
    poll_interval: float = 2.0,
) -> None:
    """Run the liquidity provider strategy."""
    strategy = LiquidityProvider(
        config=config,
        dry_run=dry_run,
        log_dir="data/liquidity_trades",
        use_polling=use_polling,
        poll_interval=poll_interval,
    )

    # Handle Ctrl+C
    loop = asyncio.get_event_loop()

    def shutdown():
        logger.info("Received shutdown signal")
        asyncio.create_task(strategy.stop())

    loop.add_signal_handler(signal.SIGINT, shutdown)
    loop.add_signal_handler(signal.SIGTERM, shutdown)

    try:
        await strategy.start(tickers)
    except KeyboardInterrupt:
        pass
    finally:
        strategy.print_status()


async def run_depth_scalper(
    tickers: List[str],
    config: ScalpConfig,
    dry_run: bool,
    use_polling: bool = False,
    poll_interval: float = 2.0,
) -> None:
    """Run the depth scalper strategy."""
    strategy = DepthScalper(
        config=config,
        dry_run=dry_run,
        log_dir="data/scalp_trades",
        use_polling=use_polling,
        poll_interval=poll_interval,
    )

    # Handle Ctrl+C
    loop = asyncio.get_event_loop()

    def shutdown():
        logger.info("Received shutdown signal")
        asyncio.create_task(strategy.stop())

    loop.add_signal_handler(signal.SIGINT, shutdown)
    loop.add_signal_handler(signal.SIGTERM, shutdown)

    try:
        await strategy.start(tickers)
    except KeyboardInterrupt:
        pass
    finally:
        strategy.print_status()


def main():
    parser = argparse.ArgumentParser(
        description="Depth-Based Trading Strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run liquidity provider
  python scripts/depth_strategies.py --mode liquidity --sport nba_totals

  # Live scalping (REAL MONEY)
  python scripts/depth_strategies.py --mode scalp --sport nba_totals --live

  # Custom parameters
  python scripts/depth_strategies.py --mode liquidity --min-spread 8 --quote-size 15

  # Single ticker
  python scripts/depth_strategies.py --mode liquidity --ticker KXNBATOTAL-26FEB03BOSDAL-229

Strategy Comparison:
  liquidity - Passive quoting, lower risk, captures spread
  scalp     - Aggressive entry, higher risk, momentum-based
        """,
    )

    # Mode selection
    parser.add_argument(
        "--mode",
        type=str,
        required=True,
        choices=["liquidity", "scalp"],
        help="Strategy mode: liquidity (passive) or scalp (aggressive)",
    )

    # Trading mode
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode (default)",
    )
    mode_group.add_argument(
        "--live",
        action="store_true",
        help="Live trading mode (REAL MONEY)",
    )

    # Sport selection
    parser.add_argument(
        "--sport",
        type=str,
        default="nba_totals",
        choices=["nba", "nba_totals", "ncaab", "nhl", "ucl", "tennis"],
        help="Sport to trade (default: nba_totals)",
    )

    # Ticker filter
    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="Only trade specific ticker(s), comma-separated",
    )

    # Common parameters
    parser.add_argument(
        "--min-spread",
        type=int,
        default=None,
        help="Minimum spread in cents (default: 5 for liquidity, 8 for scalp)",
    )
    parser.add_argument(
        "--max-spread",
        type=int,
        default=30,
        help="Maximum spread in cents (default: 30)",
    )

    # Liquidity provider parameters
    parser.add_argument(
        "--edge",
        type=int,
        default=1,
        help="Quote edge in cents (default: 1)",
    )
    parser.add_argument(
        "--quote-size",
        type=int,
        default=10,
        help="Contracts per quote (default: 10)",
    )
    parser.add_argument(
        "--max-inventory",
        type=int,
        default=50,
        help="Maximum inventory (default: 50)",
    )
    parser.add_argument(
        "--fill-timeout",
        type=float,
        default=60.0,
        help="Seconds to wait for second fill (default: 60)",
    )

    # Scalper parameters
    parser.add_argument(
        "--max-depth-at-best",
        type=int,
        default=30,
        help="Max contracts at best price for scalp entry (default: 30)",
    )
    parser.add_argument(
        "--stop-loss",
        type=int,
        default=5,
        help="Stop loss in cents (default: 5)",
    )
    parser.add_argument(
        "--max-hold",
        type=float,
        default=30.0,
        help="Max hold time in seconds (default: 30)",
    )
    parser.add_argument(
        "--direction",
        type=str,
        default="buy",
        choices=["buy", "sell", "both"],
        help="Scalp direction (default: buy - recommended)",
    )

    # Game time filter (NBA totals - best in 4th quarter)
    parser.add_argument(
        "--4th-quarter",
        action="store_true",
        dest="fourth_quarter",
        help="Only trade in 4th quarter or later (NBA totals)",
    )
    parser.add_argument(
        "--min-period",
        type=int,
        default=4,
        help="Minimum period to trade (1-5, default: 4 for 4th quarter)",
    )
    parser.add_argument(
        "--max-minutes",
        type=int,
        default=12,
        help="Max minutes remaining in quarter (default: 12 = any time in quarter)",
    )

    # Risk parameters
    parser.add_argument(
        "--max-daily-loss",
        type=float,
        default=100.0,
        help="Max daily loss in USD (default: 100)",
    )

    # Connection mode
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Use polling instead of WebSocket (more reliable for dry runs)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds (default: 2.0)",
    )

    # Misc
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Set logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Confirm live trading
    dry_run = not args.live
    if args.live:
        logger.warning("=" * 60)
        logger.warning("LIVE TRADING MODE - REAL MONEY AT RISK!")
        logger.warning("=" * 60)
        confirm = input("Type 'YES' to confirm: ")
        if confirm != "YES":
            logger.info("Aborted.")
            return 1

    # Connect to Kalshi
    logger.info("Connecting to Kalshi API...")
    wrapper = KalshiWrapped()
    balance = wrapper.GetBalance()
    logger.info(f"Connected! Balance: ${balance:.2f}")

    # Get markets
    sport = SportType(args.sport)
    markets = get_markets(wrapper, sport)
    logger.info(f"Found {len(markets)} {sport.value} markets")

    # Filter by tickers if specified
    ticker_filter = None
    if args.ticker:
        ticker_filter = set(t.strip() for t in args.ticker.split(","))

    tickers = filter_markets_by_tickers(markets, ticker_filter)

    # Set default min_spread based on mode
    min_spread = args.min_spread
    if min_spread is None:
        min_spread = 5 if args.mode == "liquidity" else 8

    # Further filter by spread
    tickers = filter_markets_by_spread(markets, min_spread, args.max_spread)

    if not tickers:
        logger.error(f"No markets found with spread >= {min_spread}c")
        return 1

    logger.info(f"Trading {len(tickers)} markets with spread >= {min_spread}c")

    # Print configuration
    logger.info("=" * 60)
    logger.info(f"Strategy: {args.mode.upper()}")
    logger.info(f"Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    logger.info(f"Sport: {sport.value.upper()}")
    logger.info(f"Markets: {len(tickers)}")
    logger.info(f"Min spread: {min_spread}c")
    logger.info(f"Max daily loss: ${args.max_daily_loss}")
    logger.info("=" * 60)

    # Run strategy
    if args.mode == "liquidity":
        config = LiquidityConfig(
            min_spread_cents=min_spread,
            edge_cents=args.edge,
            quote_size=args.quote_size,
            max_inventory=args.max_inventory,
            fill_timeout_seconds=args.fill_timeout,
            max_daily_loss=args.max_daily_loss,
            max_spread_cents=args.max_spread,
        )

        logger.info(f"Edge: {config.edge_cents}c")
        logger.info(f"Quote size: {config.quote_size}")
        logger.info(f"Max inventory: {config.max_inventory}")
        if args.poll:
            logger.info(f"Polling mode: {args.poll_interval}s interval")

        asyncio.run(
            run_liquidity_provider(
                tickers,
                config,
                dry_run,
                use_polling=args.poll,
                poll_interval=args.poll_interval,
            )
        )

    else:  # scalp
        config = ScalpConfig(
            min_spread_cents=min_spread,
            max_depth_at_best=args.max_depth_at_best,
            stop_loss_cents=args.stop_loss,
            max_hold_seconds=args.max_hold,
            direction=args.direction,
            max_daily_loss=args.max_daily_loss,
            require_4th_quarter=args.fourth_quarter,
            min_period=args.min_period,
            max_minutes_remaining=args.max_minutes,
        )

        logger.info(f"Max depth at best: {config.max_depth_at_best}")
        logger.info(f"Stop loss: {config.stop_loss_cents}c")
        logger.info(f"Max hold: {config.max_hold_seconds}s")
        logger.info(f"Direction: {config.direction}")
        if config.require_4th_quarter:
            logger.info(
                f"Game time filter: Q{config.min_period}+ with <={config.max_minutes_remaining} min"
            )
        if args.poll:
            logger.info(f"Polling mode: {args.poll_interval}s interval")

        asyncio.run(
            run_depth_scalper(
                tickers,
                config,
                dry_run,
                use_polling=args.poll,
                poll_interval=args.poll_interval,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
