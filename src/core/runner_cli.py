#!/usr/bin/env python3
"""Generic Strategy Runner CLI.

Run any strategy that implements the TradingStrategy interface with
unified configuration, dry-run support, and lifecycle management.

Usage:
    # Run a strategy with config file
    python -m src.core.runner_cli --strategy src.strategies.crypto_latency.strategy.CryptoLatencyStrategy --config config/crypto.yaml

    # Dry run mode (no real orders)
    python -m src.core.runner_cli --strategy my_module.MyStrategy --config config.yaml --dry-run

    # With verbose logging
    python -m src.core.runner_cli -s my_module.MyStrategy -c config.yaml --dry-run -v

    # List available strategies
    python -m src.core.runner_cli --list-strategies
"""

import argparse
import asyncio
import importlib
import logging
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Type

import yaml

from src.core.utils import setup_logger
from strategies.base import TradingStrategy, StrategyConfig


logger = setup_logger(__name__)


# ==============================================================================
# Strategy Registry
# ==============================================================================


# Known strategies for discovery (add new strategies here)
STRATEGY_REGISTRY: Dict[str, str] = {
    "crypto_latency": "src.strategies.crypto_latency.strategy.CryptoLatencyStrategy",
    "late_game_blowout": "src.strategies.late_game_blowout.BlowoutStrategy",
    "nba_mispricing": "src.strategies.nba_mispricing.NBAMispricingStrategy",
}


def list_strategies() -> None:
    """Print available strategies."""
    print("\nAvailable Strategies:")
    print("-" * 60)
    for name, path in sorted(STRATEGY_REGISTRY.items()):
        # Try to import and get docstring
        try:
            cls = load_strategy_class(path)
            doc = (cls.__doc__ or "No description").split("\n")[0]
            print(f"  {name:25} {doc[:50]}")
        except Exception as e:
            print(f"  {name:25} (load error: {e})")
    print()
    print("Use --strategy <name> or --strategy <full.module.path.ClassName>")
    print()


def load_strategy_class(strategy_path: str) -> Type[TradingStrategy]:
    """Load a strategy class from a dotted module path.

    Args:
        strategy_path: Either a registry name (e.g., 'crypto_latency') or
                       full path (e.g., 'src.strategies.my_strategy.MyStrategy')

    Returns:
        Strategy class

    Raises:
        ImportError: If module cannot be imported
        AttributeError: If class not found in module
        TypeError: If class doesn't inherit from TradingStrategy
    """
    # Check registry first
    if strategy_path in STRATEGY_REGISTRY:
        strategy_path = STRATEGY_REGISTRY[strategy_path]

    # Split module and class name
    if "." not in strategy_path:
        raise ValueError(
            f"Strategy path must be 'module.path.ClassName', got '{strategy_path}'"
        )

    module_path, class_name = strategy_path.rsplit(".", 1)

    # Import module
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(f"Cannot import module '{module_path}': {e}") from e

    # Get class
    try:
        strategy_cls = getattr(module, class_name)
    except AttributeError as e:
        raise AttributeError(
            f"Module '{module_path}' has no class '{class_name}'"
        ) from e

    # Validate it's a TradingStrategy
    if not isinstance(strategy_cls, type) or not issubclass(
        strategy_cls, TradingStrategy
    ):
        raise TypeError(f"'{strategy_path}' is not a TradingStrategy subclass")

    return strategy_cls


# ==============================================================================
# Configuration Loading
# ==============================================================================


@dataclass
class RunnerConfig:
    """Configuration for the strategy runner.

    Attributes:
        strategy_path: Dotted path to strategy class
        config_path: Path to strategy config YAML
        dry_run: If True, don't execute real orders
        duration_seconds: How long to run (0 = forever)
        tick_interval: Seconds between market updates
        exchange: Which exchange to use ('kalshi', 'polymarket')
        log_level: Logging verbosity
        metrics_port: Port for Prometheus metrics (0 = disabled)
    """

    strategy_path: str
    config_path: Optional[str] = None
    dry_run: bool = True
    duration_seconds: int = 0
    tick_interval: float = 1.0
    exchange: str = "kalshi"
    log_level: str = "INFO"
    metrics_port: int = 0

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "RunnerConfig":
        """Create config from parsed arguments."""
        return cls(
            strategy_path=args.strategy,
            config_path=args.config,
            dry_run=args.dry_run,
            duration_seconds=args.duration,
            tick_interval=args.tick_interval,
            exchange=args.exchange,
            log_level="DEBUG" if args.verbose else "INFO",
            metrics_port=args.metrics_port,
        )


def load_strategy_config(config_path: str) -> Dict[str, Any]:
    """Load strategy configuration from YAML file.

    Args:
        config_path: Path to YAML config file

    Returns:
        Configuration dictionary
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path) as f:
        config = yaml.safe_load(f)

    if config is None:
        return {}

    return config


# ==============================================================================
# API Client Factory
# ==============================================================================


def create_api_client(exchange: str, dry_run: bool = True):
    """Create an API client for the specified exchange.

    Args:
        exchange: Exchange name ('kalshi' or 'polymarket')
        dry_run: If True, wrap in DryRunAPIClient

    Returns:
        API client instance
    """
    if exchange.lower() == "kalshi":
        from src.core.api_client import KalshiClient
        from src.core.config import get_config

        config = get_config()
        client = KalshiClient(config)

        if dry_run:
            from src.execution.dry_run_client import DryRunAPIClient

            client = DryRunAPIClient(client)
            logger.info("Using DryRunAPIClient (no real orders)")

        return client

    elif exchange.lower() == "polymarket":
        # Try the newer client first
        try:
            from src.polymarket.client import PolymarketClient

            client = PolymarketClient()
            if dry_run:
                from src.execution.dry_run_client import DryRunAPIClient

                client = DryRunAPIClient(client)
                logger.info("Using DryRunAPIClient for Polymarket")
            return client
        except ImportError:
            from poly_utils.poly_wrapper import PolymarketWrapped

            client = PolymarketWrapped()
            logger.warning("Using legacy PolymarketWrapped client")
            return client

    else:
        raise ValueError(f"Unknown exchange: {exchange}. Use 'kalshi' or 'polymarket'")


# ==============================================================================
# Strategy Runner
# ==============================================================================


class StrategyRunner:
    """Runs a trading strategy with lifecycle management.

    Handles:
    - Strategy instantiation and configuration
    - Market data polling loop
    - Signal handling for graceful shutdown
    - Statistics and logging
    """

    def __init__(
        self,
        strategy_cls: Type[TradingStrategy],
        client,
        strategy_config: Dict[str, Any],
        runner_config: RunnerConfig,
    ) -> None:
        """Initialize the runner.

        Args:
            strategy_cls: Strategy class to instantiate
            client: API client for market data and orders
            strategy_config: Configuration dict for the strategy
            runner_config: Runner configuration
        """
        self._strategy_cls = strategy_cls
        self._client = client
        self._strategy_config = strategy_config
        self._runner_config = runner_config
        self._strategy: Optional[TradingStrategy] = None
        self._running = False
        self._shutdown_event = (
            asyncio.Event() if asyncio.get_event_loop().is_running() else None
        )

    def _create_strategy(self) -> TradingStrategy:
        """Create strategy instance from config."""
        # Build StrategyConfig from dict if strategy expects it
        config = self._strategy_config

        # Check if strategy has custom config class
        if hasattr(self._strategy_cls, "CONFIG_CLASS"):
            config_cls = self._strategy_cls.CONFIG_CLASS
            config = config_cls(**self._strategy_config)
        elif "name" in config and "tickers" in config:
            # Standard StrategyConfig
            config = StrategyConfig(**config)

        # Instantiate strategy
        return self._strategy_cls(
            client=self._client,
            config=config,
        )

    def _setup_signal_handlers(self) -> None:
        """Setup SIGINT/SIGTERM handlers for graceful shutdown."""

        def handle_shutdown(signum, frame):
            logger.info("Shutdown signal received, stopping...")
            self._running = False

        signal.signal(signal.SIGINT, handle_shutdown)
        signal.signal(signal.SIGTERM, handle_shutdown)

    def run(self) -> int:
        """Run the strategy synchronously.

        Returns:
            Exit code (0 = success, 1 = error)
        """
        self._setup_signal_handlers()

        logger.info("=" * 60)
        logger.info("Strategy Runner")
        logger.info("=" * 60)
        logger.info("Strategy: %s", self._runner_config.strategy_path)
        logger.info("Exchange: %s", self._runner_config.exchange)
        logger.info("Dry Run: %s", self._runner_config.dry_run)
        logger.info("Tick Interval: %.2fs", self._runner_config.tick_interval)
        if self._runner_config.duration_seconds > 0:
            logger.info("Duration: %d seconds", self._runner_config.duration_seconds)
        else:
            logger.info("Duration: Until stopped (Ctrl+C)")
        logger.info("=" * 60)

        # Create strategy
        try:
            self._strategy = self._create_strategy()
            logger.info("Strategy instantiated: %s", type(self._strategy).__name__)
        except Exception as e:
            logger.error("Failed to create strategy: %s", e)
            return 1

        # Start strategy
        try:
            self._strategy.start()
            logger.info("Strategy started")
        except Exception as e:
            logger.error("Failed to start strategy: %s", e)
            return 1

        # Run main loop
        self._running = True
        start_time = time.time()
        tick_count = 0

        try:
            while self._running:
                # Check duration limit
                if self._runner_config.duration_seconds > 0:
                    elapsed = time.time() - start_time
                    if elapsed >= self._runner_config.duration_seconds:
                        logger.info("Duration limit reached, stopping")
                        break

                # Get market updates and process
                try:
                    self._tick()
                    tick_count += 1
                except Exception as e:
                    logger.error("Error in tick: %s", e, exc_info=True)

                # Wait for next tick
                time.sleep(self._runner_config.tick_interval)

        except KeyboardInterrupt:
            logger.info("Interrupted by user")

        finally:
            # Stop strategy
            if self._strategy:
                try:
                    self._strategy.stop()
                    logger.info("Strategy stopped")
                except Exception as e:
                    logger.error("Error stopping strategy: %s", e)

            # Print summary
            self._print_summary(tick_count, time.time() - start_time)

        return 0

    def _tick(self) -> None:
        """Execute one tick of the strategy loop."""
        if not self._strategy:
            return

        # Get tickers from strategy config
        tickers = []
        if hasattr(self._strategy._config, "tickers"):
            tickers = self._strategy._config.tickers
        elif isinstance(self._strategy._config, dict):
            tickers = self._strategy._config.get("tickers", [])

        # Process each market
        for ticker in tickers:
            try:
                # Get market state
                market_data = self._client.get_market(ticker)
                if market_data is None:
                    continue

                # Convert to MarketState if needed
                from src.core.models import MarketState

                if not isinstance(market_data, MarketState):
                    market_state = MarketState(
                        ticker=ticker,
                        bid=market_data.get("yes_bid", 0),
                        ask=market_data.get("yes_ask", 100),
                        last_price=market_data.get("last_price"),
                        volume=market_data.get("volume", 0),
                        timestamp=datetime.now(),
                    )
                else:
                    market_state = market_data

                # Run strategy
                order_ids = self._strategy.on_market_update(market_state)
                if order_ids:
                    logger.info("Placed %d order(s) for %s", len(order_ids), ticker)

            except Exception as e:
                logger.warning("Error processing %s: %s", ticker, e)

    def _print_summary(self, tick_count: int, duration: float) -> None:
        """Print run summary statistics."""
        logger.info("=" * 60)
        logger.info("Run Summary")
        logger.info("=" * 60)
        logger.info("Duration: %.1f seconds", duration)
        logger.info("Ticks: %d", tick_count)

        if self._strategy and hasattr(self._strategy, "_state"):
            state = self._strategy._state
            logger.info("Signals Generated: %d", state.signals_generated)
            logger.info("Orders Placed: %d", state.orders_placed)
            logger.info("Fills Received: %d", state.fills_received)
            logger.info("Errors: %d", state.errors)

        # Print dry run stats if available
        if hasattr(self._client, "get_stats"):
            stats = self._client.get_stats()
            logger.info("-" * 40)
            logger.info("Dry Run Statistics:")
            # Handle both DryRunStats object and dict
            if hasattr(stats, "orders_would_place"):
                logger.info("  Orders Would Place: %d", stats.orders_would_place)
                logger.info("  Orders Would Fill: %d", stats.orders_would_fill)
                logger.info("  Total Volume: %d", stats.total_volume)
            elif isinstance(stats, dict):
                logger.info(
                    "  Orders Would Place: %d", stats.get("orders_would_place", 0)
                )
                logger.info(
                    "  Orders Would Fill: %d", stats.get("orders_would_fill", 0)
                )
                logger.info("  Total Volume: %d", stats.get("total_volume", 0))

        logger.info("=" * 60)


# ==============================================================================
# Async Runner (for async strategies)
# ==============================================================================


class AsyncStrategyRunner(StrategyRunner):
    """Async version of StrategyRunner for async strategies."""

    async def run_async(self) -> int:
        """Run the strategy asynchronously.

        Returns:
            Exit code (0 = success, 1 = error)
        """
        loop = asyncio.get_event_loop()

        # Setup shutdown event
        self._shutdown_event = asyncio.Event()

        def handle_shutdown():
            logger.info("Shutdown signal received")
            self._shutdown_event.set()

        loop.add_signal_handler(signal.SIGINT, handle_shutdown)
        loop.add_signal_handler(signal.SIGTERM, handle_shutdown)

        logger.info("=" * 60)
        logger.info("Async Strategy Runner")
        logger.info("=" * 60)
        logger.info("Strategy: %s", self._runner_config.strategy_path)
        logger.info("Dry Run: %s", self._runner_config.dry_run)
        logger.info("=" * 60)

        # Create and start strategy
        try:
            self._strategy = self._create_strategy()
            self._strategy.start()
        except Exception as e:
            logger.error("Failed to start strategy: %s", e)
            return 1

        start_time = time.time()
        tick_count = 0

        try:
            while not self._shutdown_event.is_set():
                # Check duration
                if self._runner_config.duration_seconds > 0:
                    if time.time() - start_time >= self._runner_config.duration_seconds:
                        break

                # Run tick
                self._tick()
                tick_count += 1

                # Async sleep
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self._runner_config.tick_interval,
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    pass  # Normal timeout, continue

        finally:
            if self._strategy:
                self._strategy.stop()
            self._print_summary(tick_count, time.time() - start_time)

        return 0


# ==============================================================================
# CLI Entry Point
# ==============================================================================


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run a trading strategy with unified configuration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "-s",
        "--strategy",
        type=str,
        help="Strategy class path (e.g., 'src.strategies.my_strategy.MyStrategy') or registry name",
    )

    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default=None,
        help="Path to strategy configuration YAML file",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Run in dry-run mode (no real orders) [default: True]",
    )

    parser.add_argument(
        "--live",
        action="store_true",
        default=False,
        help="Run in live mode (REAL orders - use with caution!)",
    )

    parser.add_argument(
        "-d",
        "--duration",
        type=int,
        default=0,
        help="Run duration in seconds (0 = run forever)",
    )

    parser.add_argument(
        "-t",
        "--tick-interval",
        type=float,
        default=1.0,
        help="Seconds between market updates [default: 1.0]",
    )

    parser.add_argument(
        "-e",
        "--exchange",
        type=str,
        default="kalshi",
        choices=["kalshi", "polymarket"],
        help="Exchange to trade on [default: kalshi]",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    parser.add_argument(
        "--metrics-port",
        type=int,
        default=0,
        help="Port for Prometheus metrics (0 = disabled)",
    )

    parser.add_argument(
        "--list-strategies",
        action="store_true",
        help="List available strategies and exit",
    )

    parser.add_argument(
        "--async",
        dest="use_async",
        action="store_true",
        help="Use async runner",
    )

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # List strategies and exit
    if args.list_strategies:
        list_strategies()
        return 0

    # Validate required arguments
    if not args.strategy:
        print("Error: --strategy is required")
        print("Use --list-strategies to see available strategies")
        return 1

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Determine dry-run mode
    dry_run = not args.live
    if args.live:
        logger.warning("=" * 60)
        logger.warning("⚠️  LIVE TRADING MODE")
        logger.warning("Real orders will be placed with real money!")
        logger.warning("=" * 60)
        confirm = input("Type 'CONFIRM' to proceed: ")
        if confirm != "CONFIRM":
            logger.info("Aborted.")
            return 1

    # Build runner config
    runner_config = RunnerConfig(
        strategy_path=args.strategy,
        config_path=args.config,
        dry_run=dry_run,
        duration_seconds=args.duration,
        tick_interval=args.tick_interval,
        exchange=args.exchange,
        log_level="DEBUG" if args.verbose else "INFO",
        metrics_port=args.metrics_port,
    )

    # Load strategy class
    try:
        strategy_cls = load_strategy_class(args.strategy)
        logger.info("Loaded strategy class: %s", strategy_cls.__name__)
    except (ImportError, AttributeError, TypeError) as e:
        logger.error("Failed to load strategy: %s", e)
        return 1

    # Load strategy config
    strategy_config: Dict[str, Any] = {}
    if args.config:
        try:
            strategy_config = load_strategy_config(args.config)
            logger.info("Loaded config from: %s", args.config)
        except FileNotFoundError as e:
            logger.error("Config file not found: %s", e)
            return 1
        except yaml.YAMLError as e:
            logger.error("Invalid YAML config: %s", e)
            return 1

    # Create API client
    try:
        client = create_api_client(
            exchange=args.exchange,
            dry_run=dry_run,
        )
    except Exception as e:
        logger.error("Failed to create API client: %s", e)
        return 1

    # Create and run runner
    if args.use_async:
        runner = AsyncStrategyRunner(
            strategy_cls=strategy_cls,
            client=client,
            strategy_config=strategy_config,
            runner_config=runner_config,
        )
        return asyncio.run(runner.run_async())
    else:
        runner = StrategyRunner(
            strategy_cls=strategy_cls,
            client=client,
            strategy_config=strategy_config,
            runner_config=runner_config,
        )
        return runner.run()


if __name__ == "__main__":
    sys.exit(main())
