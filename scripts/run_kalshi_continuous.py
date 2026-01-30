#!/usr/bin/env python3
"""Continuous runner for Kalshi crypto latency strategy with trade logging.

Runs the strategy continuously across 15-minute windows and logs all trades
to a CSV file for later analysis.

Usage:
    python scripts/run_kalshi_continuous.py                  # Paper trading
    python scripts/run_kalshi_continuous.py --live           # Live trading
    python scripts/run_kalshi_continuous.py --live --kelly 0.5  # Half-Kelly live
"""

import argparse
import csv
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.api_client import KalshiClient
from src.core.config import Config
from src.risk.risk_manager import RiskManager
from src.strategies.crypto_latency import CryptoLatencyConfig
from src.strategies.crypto_latency.kalshi_orchestrator import KalshiCryptoOrchestrator
from src.strategies.crypto_latency.kalshi_executor import KalshiOpportunity, KalshiExecutionResult


# Trade log file
TRADE_LOG_PATH = project_root / "data" / "crypto_latency_trades.csv"


def setup_logging(verbose: bool = False) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Continuous Kalshi crypto latency strategy with logging",
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live trading (default: paper trading)",
    )
    parser.add_argument(
        "--kelly",
        type=float,
        default=0.5,
        help="Kelly fraction (default: 0.5 = half-Kelly)",
    )
    parser.add_argument(
        "--edge",
        type=float,
        default=0.20,
        help="Minimum edge to trade (default: 0.20 = 20%%)",
    )
    parser.add_argument(
        "--max-exposure",
        type=float,
        default=50.0,
        help="Maximum total exposure (default: $50)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging",
    )

    return parser.parse_args()


class TradeLogger:
    """Logs trades to CSV for analysis."""

    def __init__(self, log_path: Path):
        self.log_path = log_path
        self._ensure_file()

    def _ensure_file(self):
        """Create CSV with headers if it doesn't exist."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.log_path.exists():
            with open(self.log_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp",
                    "ticker",
                    "asset",
                    "side",
                    "action",  # entry/exit/settlement
                    "contracts",
                    "price_cents",
                    "spot_price",
                    "strike_price",
                    "implied_prob",
                    "market_prob",
                    "edge",
                    "time_to_expiry_sec",
                    "order_id",
                    "pnl",
                    "cumulative_pnl",
                    "bankroll",
                    "mode",  # live/paper
                ])

    def log_entry(
        self,
        opportunity: KalshiOpportunity,
        result: KalshiExecutionResult,
        bankroll: float,
        mode: str,
    ):
        """Log a trade entry."""
        with open(self.log_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.utcnow().isoformat(),
                opportunity.market.ticker,
                opportunity.market.asset,
                opportunity.side,
                "entry",
                result.executed_size,
                result.executed_price,
                opportunity.spot_price,
                opportunity.market.strike_price,
                f"{opportunity.implied_prob:.4f}",
                f"{opportunity.market_prob:.4f}",
                f"{opportunity.edge:.4f}",
                opportunity.market.time_to_expiry_sec,
                result.order_id,
                "",  # No P&L on entry
                "",
                f"{bankroll:.2f}",
                mode,
            ])

    def log_exit(
        self,
        ticker: str,
        asset: str,
        side: str,
        contracts: int,
        price_cents: int,
        pnl: float,
        cumulative_pnl: float,
        bankroll: float,
        mode: str,
        reason: str = "settlement",
    ):
        """Log a trade exit."""
        with open(self.log_path, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.utcnow().isoformat(),
                ticker,
                asset,
                side,
                f"exit_{reason}",
                contracts,
                price_cents,
                "",  # No spot price on exit
                "",
                "",
                "",
                "",
                "",
                "",
                f"{pnl:.2f}",
                f"{cumulative_pnl:.2f}",
                f"{bankroll:.2f}",
                mode,
            ])


class DryRunKalshiClient:
    """Wrapper for paper trading."""

    def __init__(self, real_client: KalshiClient):
        self._client = real_client
        self._order_count = 0

    def __getattr__(self, name):
        return getattr(self._client, name)

    def place_order(self, **kwargs):
        self._order_count += 1
        logging.getLogger(__name__).info("[PAPER] Would place: %s", kwargs)
        return {
            "order": {
                "order_id": f"paper_{self._order_count}",
                "status": "simulated",
            }
        }


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    # Initialize
    logger.info("=" * 60)
    logger.info("KALSHI CRYPTO LATENCY - CONTINUOUS MODE")
    logger.info("=" * 60)
    logger.info("Mode: %s", "LIVE" if args.live else "PAPER")
    logger.info("Kelly: %.0f%%", args.kelly * 100)
    logger.info("Min Edge: %.0f%%", args.edge * 100)
    logger.info("Max Exposure: $%.2f", args.max_exposure)
    logger.info("Trade Log: %s", TRADE_LOG_PATH)
    logger.info("=" * 60)

    # Live confirmation
    if args.live:
        logger.warning("LIVE TRADING - Real money will be used!")
        confirm = input("Type 'LIVE' to confirm: ")
        if confirm != "LIVE":
            logger.info("Aborted.")
            return 1

    # Initialize client
    try:
        app_config = Config.load()
        kalshi_client = KalshiClient(config=app_config)

        # Get bankroll
        resp = kalshi_client._request("GET", "/portfolio/balance")
        bankroll = resp.get("balance", 0) / 100
        logger.info("Bankroll: $%.2f", bankroll)

    except Exception as e:
        logger.error("Failed to initialize: %s", e)
        return 1

    # Wrap for paper trading
    if not args.live:
        kalshi_client = DryRunKalshiClient(kalshi_client)

    # Strategy config
    config = CryptoLatencyConfig(
        min_edge_pct=args.edge,
        kelly_fraction=args.kelly,
        bankroll=bankroll,
        max_total_exposure=args.max_exposure,
        early_exit_enabled=True,
    )

    # Trade logger
    trade_logger = TradeLogger(TRADE_LOG_PATH)

    # Stats
    cumulative_pnl = 0.0
    total_trades = 0

    # Shutdown handling
    shutdown = threading.Event()

    def handle_signal(signum, frame):
        logger.info("Shutdown signal received...")
        shutdown.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Main loop - run continuously
    logger.info("Starting continuous trading loop...")
    logger.info("Press Ctrl+C to stop")

    while not shutdown.is_set():
        try:
            # Update bankroll
            if args.live:
                try:
                    resp = kalshi_client._request("GET", "/portfolio/balance")
                    bankroll = resp.get("balance", 0) / 100
                    config.bankroll = bankroll
                except:
                    pass

            # Create orchestrator for this cycle
            risk_config_obj = type('RiskConfig', (), {
                'max_position_size': int(args.max_exposure),
                'max_total_position': int(args.max_exposure),
                'max_daily_loss': args.max_exposure * 2,
                'max_loss_per_position': args.max_exposure * 0.5,
            })()

            from src.core.config import RiskConfig
            risk_config = RiskConfig(
                max_position_size=int(args.max_exposure),
                max_total_position=int(args.max_exposure),
                max_daily_loss=args.max_exposure * 2,
                max_loss_per_position=args.max_exposure * 0.5,
            )
            risk_manager = RiskManager(risk_config)

            orchestrator = KalshiCryptoOrchestrator(
                kalshi_client=kalshi_client,
                config=config,
                risk_manager=risk_manager,
            )

            # Track trades
            mode = "live" if args.live else "paper"

            def on_execution(result: KalshiExecutionResult):
                nonlocal total_trades
                if result.success:
                    total_trades += 1
                    trade_logger.log_entry(
                        opportunity=result.opportunity,
                        result=result,
                        bankroll=bankroll,
                        mode=mode,
                    )
                    logger.info(
                        "[TRADE #%d] %s %s %d @ %dc | edge=%.1f%%",
                        total_trades,
                        result.opportunity.market.asset,
                        result.opportunity.side.upper(),
                        result.executed_size,
                        result.executed_price,
                        result.opportunity.edge * 100,
                    )

            orchestrator.on_execution(on_execution)

            # Run for one 15-minute cycle (plus buffer)
            orchestrator.start()

            # Wait for cycle or shutdown
            cycle_duration = 900  # 15 minutes
            for _ in range(cycle_duration * 2):  # Check every 0.5 sec
                if shutdown.is_set():
                    break
                time.sleep(0.5)

            orchestrator.stop()

            # Log summary
            stats = orchestrator.get_stats()
            logger.info(
                "Cycle complete | Opportunities: %d | Trades: %d | Exposure: $%.2f",
                stats["opportunities_detected"],
                stats["opportunities_executed"],
                stats["executor_stats"]["total_exposure"],
            )

        except Exception as e:
            logger.error("Cycle error: %s", e)
            time.sleep(10)  # Wait before retry

    logger.info("=" * 60)
    logger.info("SHUTDOWN COMPLETE")
    logger.info("Total Trades: %d", total_trades)
    logger.info("Trade Log: %s", TRADE_LOG_PATH)
    logger.info("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
