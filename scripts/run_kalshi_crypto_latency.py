#!/usr/bin/env python3
"""CLI entry point for Kalshi crypto latency arbitrage strategy.

Usage:
    python scripts/run_kalshi_crypto_latency.py              # Default: 60 seconds
    python scripts/run_kalshi_crypto_latency.py --duration 3600  # Run for 1 hour
    python scripts/run_kalshi_crypto_latency.py --live       # Enable live trading
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.api_client import KalshiClient
from src.core.config import Config, RiskConfig
from src.risk.risk_manager import RiskManager
from strategies.crypto_latency import CryptoLatencyConfig
from strategies.crypto_latency.kalshi_orchestrator import run_kalshi_orchestrator


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Reduce noise
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Kalshi crypto latency arbitrage strategy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run for 60 seconds (dry run - no orders)
    python scripts/run_kalshi_crypto_latency.py

    # Run for 1 hour
    python scripts/run_kalshi_crypto_latency.py --duration 3600

    # Enable live trading (requires API keys)
    python scripts/run_kalshi_crypto_latency.py --live --duration 300

    # Custom edge threshold
    python scripts/run_kalshi_crypto_latency.py --edge 0.15
        """,
    )

    # Duration
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Duration to run in seconds (default: 60)",
    )

    # Live trading
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading (default: dry run, no orders placed)",
    )

    # Configuration
    parser.add_argument(
        "--edge",
        type=float,
        default=0.20,
        help="Minimum edge to trade (default: 0.20 = 20%%)",
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

    # Kelly sizing
    parser.add_argument(
        "--kelly",
        type=float,
        default=0.5,
        help="Kelly fraction (0.5=half-Kelly, 1.0=full Kelly, 0=fixed sizing)",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=None,
        help="Bankroll for Kelly sizing (default: auto-detect from account)",
    )

    # Symbols
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        help="Binance symbols to track (default: BTCUSDT ETHUSDT SOLUSDT)",
    )

    # Verbosity
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


class DryRunKalshiClient:
    """Wrapper that intercepts order placement for dry runs."""

    def __init__(self, real_client: KalshiClient):
        self._client = real_client
        self._dry_run = True
        self._order_count = 0
        self._simulated_orders = {}  # order_id -> order details

    def __getattr__(self, name):
        """Proxy all attributes to real client."""
        return getattr(self._client, name)

    def place_order(self, **kwargs):
        """Intercept order placement in dry run mode."""
        if self._dry_run:
            self._order_count += 1
            order_id = f"dry_run_{self._order_count}"
            # Store order details for simulated fills
            self._simulated_orders[order_id] = {
                "side": kwargs.get("side", "yes"),
                "yes_price": kwargs.get("yes_price"),
                "no_price": kwargs.get("no_price"),
                "count": kwargs.get("count", 1),
            }
            logging.getLogger(__name__).info(
                "[DRY RUN] Would place order: %s",
                kwargs,
            )
            return {
                "order": {
                    "order_id": order_id,
                    "status": "simulated",
                }
            }
        return self._client.place_order(**kwargs)

    def get_fills(self, order_id: str = None, **kwargs):
        """Return simulated fills for dry run orders."""
        if self._dry_run and order_id and order_id.startswith("dry_run_"):
            order = self._simulated_orders.get(order_id, {})
            side = order.get("side", "yes")
            price = (
                order.get(f"{side}_price")
                or order.get("yes_price")
                or order.get("no_price")
                or 50
            )
            count = order.get("count", 1)
            # Simulate immediate fill at the limit price
            return {
                "fills": [
                    {
                        f"{side}_price": price,
                        "count": count,
                        "order_id": order_id,
                    }
                ]
            }
        return self._client.get_fills(order_id=order_id, **kwargs)


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Setup logging
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Initialize Kalshi client early to get bankroll if needed
    logger.info("Initializing Kalshi client...")

    try:
        # Load config (will use env vars or config file)
        app_config = Config.load()
        kalshi_client = KalshiClient(config=app_config)
    except Exception as e:
        logger.error("Failed to initialize Kalshi client: %s", e)
        return 1

    # Auto-detect bankroll from account if not specified
    bankroll = args.bankroll
    if bankroll is None:
        try:
            resp = kalshi_client._request("GET", "/portfolio/balance")
            bankroll = resp.get("balance", 0) / 100  # Convert cents to dollars
            logger.info("Auto-detected bankroll: $%.2f", bankroll)
        except Exception as e:
            logger.warning(
                "Could not get account balance, using default bankroll: %s", e
            )
            bankroll = args.max_exposure

    # Build strategy config
    config = CryptoLatencyConfig(
        symbols=args.symbols,
        min_edge_pct=args.edge,
        base_position_usd=args.size,
        max_total_exposure=args.max_exposure,
        max_daily_loss=args.max_loss,
        kelly_fraction=args.kelly,
        bankroll=bankroll,
        paper_mode=not args.live,  # Not used directly, but for reference
    )

    logger.info("=" * 60)
    logger.info("KALSHI CRYPTO LATENCY ARBITRAGE")
    logger.info("=" * 60)
    logger.info("Mode: %s", "LIVE TRADING" if args.live else "DRY RUN (no orders)")
    logger.info("Duration: %d seconds", args.duration)
    logger.info("Symbols: %s", config.symbols)
    logger.info("Min Edge: %.1f%%", config.min_edge_pct * 100)
    logger.info(
        "Kelly Fraction: %.1f (%.0f%% Kelly)",
        config.kelly_fraction,
        config.kelly_fraction * 100,
    )
    logger.info("Bankroll: $%.2f", config.bankroll)
    logger.info("Max Exposure: $%.2f", config.max_total_exposure)
    logger.info("Max Daily Loss: $%.2f", config.max_daily_loss)
    logger.info("=" * 60)

    # Live mode confirmation
    if args.live:
        logger.warning("WARNING: LIVE TRADING MODE - Real orders will be placed!")
        confirm = input("Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            logger.info("Aborted.")
            return 1

    # Check if authenticated for live trading
    if args.live and not kalshi_client.is_authenticated:
        logger.error(
            "Live trading requires API authentication. "
            "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH"
        )
        return 1

    # Wrap in dry run client if not live
    if not args.live:
        kalshi_client = DryRunKalshiClient(kalshi_client)
        logger.info("Dry run mode: orders will be simulated")

    # Test connection
    logger.info("Testing Kalshi connection...")
    try:
        status = kalshi_client.get_exchange_status()
        if status.get("trading_active"):
            logger.info("Kalshi exchange is active")
        else:
            logger.warning("Kalshi trading may be inactive: %s", status)
    except Exception as e:
        logger.warning("Could not get exchange status: %s", e)

    # Initialize risk manager
    # Scale position limits based on max exposure
    max_pos_size = min(
        int(config.max_position_per_market), int(config.max_total_exposure)
    )
    risk_config = RiskConfig(
        max_position_size=max_pos_size,
        max_total_position=int(config.max_total_exposure),
        max_daily_loss=config.max_daily_loss,
        max_loss_per_position=min(max_pos_size * 0.5, config.max_daily_loss),
    )
    risk_manager = RiskManager(risk_config)

    # Run orchestrator
    try:
        logger.info("Starting strategy...")
        run_kalshi_orchestrator(
            kalshi_client=kalshi_client,
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


if __name__ == "__main__":
    sys.exit(main())
