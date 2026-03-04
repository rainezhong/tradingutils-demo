#!/usr/bin/env python3
"""CLI entry point for crypto latency arbitrage strategy.

Usage:
    python scripts/run_crypto_latency.py --paper     # Paper trading mode
    python scripts/run_crypto_latency.py --live      # Live trading (careful!)
    python scripts/run_crypto_latency.py --duration 3600  # Run for 1 hour
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.config import RiskConfig
from src.polymarket.client import PolymarketClient
from src.risk.risk_manager import RiskManager
from strategies.crypto_latency import (
    CryptoLatencyConfig,
)
from strategies.crypto_latency.orchestrator import run_orchestrator


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise from external libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Crypto latency arbitrage strategy for Polymarket",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run in paper trading mode (default)
    python scripts/run_crypto_latency.py --paper

    # Run in live mode (be careful!)
    python scripts/run_crypto_latency.py --live

    # Run for a specific duration
    python scripts/run_crypto_latency.py --paper --duration 3600

    # Custom configuration
    python scripts/run_crypto_latency.py --paper --edge 0.15 --size 50
        """,
    )

    # Mode
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--paper",
        action="store_true",
        help="Run in paper trading mode (simulated execution)",
    )
    mode.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode (real money!)",
    )

    # Duration
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Duration to run in seconds (default: run until interrupted)",
    )

    # Configuration overrides
    parser.add_argument(
        "--edge",
        type=float,
        default=0.10,
        help="Minimum edge to trade (default: 0.10 = 10%%)",
    )
    parser.add_argument(
        "--size",
        type=float,
        default=75.0,
        help="Base position size in USD (default: 75)",
    )
    parser.add_argument(
        "--max-exposure",
        type=float,
        default=500.0,
        help="Maximum total exposure in USD (default: 500)",
    )
    parser.add_argument(
        "--max-loss",
        type=float,
        default=200.0,
        help="Maximum daily loss in USD (default: 200)",
    )

    # Symbols
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        help="Binance symbols to trade (default: BTCUSDT ETHUSDT SOLUSDT)",
    )

    # Verbosity
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Live mode confirmation
    if args.live:
        logger.warning("=" * 60)
        logger.warning("WARNING: LIVE TRADING MODE")
        logger.warning("This will execute REAL trades with REAL money!")
        logger.warning("=" * 60)

        confirm = input("Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            logger.info("Aborted.")
            return 1

    # Check for required environment variables
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    if not private_key and args.live:
        logger.error(
            "POLYMARKET_PRIVATE_KEY environment variable required for live trading"
        )
        return 1

    # Build configuration
    config = CryptoLatencyConfig(
        symbols=args.symbols,
        min_edge_pct=args.edge,
        base_position_usd=args.size,
        max_total_exposure=args.max_exposure,
        max_daily_loss=args.max_loss,
        paper_mode=args.paper,
    )

    logger.info("Configuration:")
    logger.info("  Mode: %s", "PAPER" if config.paper_mode else "LIVE")
    logger.info("  Symbols: %s", config.symbols)
    logger.info("  Min Edge: %.1f%%", config.min_edge_pct * 100)
    logger.info("  Base Position: $%.2f", config.base_position_usd)
    logger.info("  Max Exposure: $%.2f", config.max_total_exposure)
    logger.info("  Max Daily Loss: $%.2f", config.max_daily_loss)

    # Initialize Polymarket client
    logger.info("Connecting to Polymarket...")
    try:
        polymarket = PolymarketClient(
            private_key=private_key,
            use_websocket=True,
        )
        polymarket.connect()
    except Exception as e:
        logger.error("Failed to connect to Polymarket: %s", e)
        return 1

    # Initialize risk manager
    risk_config = RiskConfig(
        max_position_size=int(config.max_position_per_market),
        max_total_position=int(config.max_total_exposure),
        max_daily_loss=config.max_daily_loss,
        max_loss_per_position=config.max_position_per_market
        * 0.5,  # 50% max loss per position
    )
    risk_manager = RiskManager(risk_config)

    # Run orchestrator
    try:
        logger.info("Starting crypto latency strategy...")
        run_orchestrator(
            polymarket_client=polymarket,
            config=config,
            risk_manager=risk_manager,
            duration_sec=args.duration,
        )

        return 0

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 0

    except Exception as e:
        logger.exception("Strategy failed: %s", e)
        return 1

    finally:
        # Cleanup
        try:
            polymarket.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
