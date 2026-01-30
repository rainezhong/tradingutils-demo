"""Entry point for the arbitrage system.

Provides factory functions to create and run the orchestrator with
all dependencies properly configured.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from src.core.config import RiskConfig

from .config import ArbitrageConfig
from .orchestrator import ArbitrageOrchestrator
from .preflight import PreflightChecker, run_preflight


logger = logging.getLogger(__name__)


async def create_orchestrator(
    config_path: Optional[str] = None,
    paper_mode: Optional[bool] = None,
) -> ArbitrageOrchestrator:
    """Create an orchestrator with all dependencies.

    This factory function sets up the full dependency tree:
    - Exchange clients (Kalshi, Polymarket)
    - Quote provider
    - Order management system
    - Spread executor
    - Capital manager
    - Risk manager
    - Metrics collector
    - Alert manager

    Args:
        config_path: Optional path to config YAML file
        paper_mode: Override paper mode setting (None = use config)

    Returns:
        Configured ArbitrageOrchestrator ready to start
    """
    # Load configuration
    if config_path:
        config = ArbitrageConfig.from_yaml(config_path)
    else:
        config = ArbitrageConfig()

    if paper_mode is not None:
        config.paper_mode = paper_mode

    logger.info(
        "Creating orchestrator (paper_mode=%s)",
        config.paper_mode,
    )

    # Initialize components based on mode
    quote_source = None
    spread_executor = None
    capital_manager = None
    risk_manager = None
    metrics_collector = None
    alert_manager = None

    try:
        # Try to import and initialize real components
        from src.exchanges.kalshi import KalshiExchange
        from src.exchanges.polymarket import PolymarketExchange
        from src.matching.quote_provider import LiveQuoteMarketMatcher
        from src.oms.spread_executor import SpreadExecutor
        from src.oms.capital_manager import CapitalManager
        from src.oms.order_manager import OrderManagementSystem
        from src.risk.risk_manager import RiskManager
        from src.monitoring.metrics import MetricsCollector
        from src.monitoring.alerts import AlertManager, AlertConfig

        # Create exchange clients
        kalshi = KalshiExchange()
        polymarket = PolymarketExchange()

        # Wrap in paper trading if needed
        if config.paper_mode:
            try:
                from src.simulation.paper_trading import PaperTradingWrapper
                kalshi = PaperTradingWrapper(kalshi)
                polymarket = PaperTradingWrapper(polymarket)
                logger.info("Paper trading wrappers applied")
            except ImportError:
                logger.warning("Paper trading wrapper not available")

        # Quote provider
        quote_source = LiveQuoteMarketMatcher(
            kalshi_exchange=kalshi,
            poly_exchange=polymarket,
        )

        # Capital manager
        capital_manager = CapitalManager()

        # Order management system
        oms = OrderManagementSystem()
        oms.register_exchange("kalshi", kalshi)
        oms.register_exchange("polymarket", polymarket)

        # Spread executor
        spread_executor = SpreadExecutor(
            oms=oms,
            capital_manager=capital_manager,
        )

        # Risk manager
        risk_config = RiskConfig(
            max_position_size=config.max_position_per_market,
            max_total_position=config.max_position_per_market * 5,
            max_daily_loss=config.max_daily_loss,
        )
        risk_manager = RiskManager(risk_config)

        # Metrics collector
        try:
            metrics_collector = MetricsCollector(port=config.metrics_port)
            metrics_collector.start()
            logger.info("Metrics collector started on port %d", config.metrics_port)
        except Exception as e:
            logger.warning("Failed to start metrics collector: %s", e)

        # Alert manager (optional)
        try:
            alert_config = AlertConfig()
            alert_manager = AlertManager(alert_config)
        except Exception as e:
            logger.warning("Failed to initialize alert manager: %s", e)

    except ImportError as e:
        logger.warning(
            "Some components not available, running with reduced functionality: %s",
            e,
        )

    # Create orchestrator
    orchestrator = ArbitrageOrchestrator(
        config=config,
        quote_source=quote_source,
        spread_executor=spread_executor,
        capital_manager=capital_manager,
        risk_manager=risk_manager,
        metrics_collector=metrics_collector,
        alert_manager=alert_manager,
    )

    return orchestrator


async def run(
    config_path: Optional[str] = None,
    paper_mode: bool = True,
    skip_preflight: bool = False,
) -> None:
    """Run the arbitrage system.

    Main entry point for running the system. Sets up logging,
    runs preflight checks, creates the orchestrator, and runs until interrupted.

    Args:
        config_path: Optional path to config YAML file
        paper_mode: Whether to run in paper trading mode
        skip_preflight: Skip preflight checks (not recommended)
    """
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logger.info("=" * 60)
    logger.info("Kalshi/Polymarket Arbitrage System")
    logger.info("=" * 60)
    logger.info("Paper mode: %s", paper_mode)
    if config_path:
        logger.info("Config file: %s", config_path)
    logger.info("=" * 60)

    # Load config early for preflight
    if config_path:
        config = ArbitrageConfig.from_yaml(config_path)
    else:
        config = ArbitrageConfig()

    if paper_mode is not None:
        config.paper_mode = paper_mode

    # Run preflight checks
    if not skip_preflight:
        logger.info("Running preflight checks...")
        try:
            # Initialize minimal components for preflight
            kalshi_client = None
            polymarket_client = None
            db_manager = None
            recovery_service = None

            try:
                from src.exchanges.kalshi import KalshiExchange
                from src.exchanges.polymarket import PolymarketExchange

                kalshi_client = KalshiExchange()
                polymarket_client = PolymarketExchange()
            except ImportError:
                logger.warning("Exchange clients not available for preflight")

            try:
                from src.database.connection import get_database_manager

                db_manager = get_database_manager()
                await db_manager.initialize()
            except Exception as e:
                logger.warning("Database not available for preflight: %s", e)

            try:
                from src.database.repository import SpreadExecutionRepository
                from src.oms.recovery import SpreadRecoveryService

                if db_manager:
                    async with db_manager.session() as session:
                        repo = SpreadExecutionRepository(session)
                        recovery_service = SpreadRecoveryService(
                            repository=repo,
                            kalshi_client=kalshi_client,
                            polymarket_client=polymarket_client,
                        )
            except Exception as e:
                logger.warning("Recovery service not available: %s", e)

            # Run preflight
            preflight_result = await run_preflight(
                kalshi_client=kalshi_client,
                polymarket_client=polymarket_client,
                database_manager=db_manager,
                recovery_service=recovery_service,
                config=config,
                exit_on_failure=True,
            )

            logger.info("Preflight checks passed!")
            logger.info(preflight_result.summary())

        except SystemExit:
            raise
        except Exception as e:
            logger.error("Preflight error: %s", e)
            if not paper_mode:
                logger.critical("Preflight failed in live mode - aborting")
                raise SystemExit(1)
            logger.warning("Continuing despite preflight error (paper mode)")
    else:
        logger.warning("Preflight checks SKIPPED - not recommended!")

    # Create and run orchestrator
    orchestrator = await create_orchestrator(
        config_path=config_path,
        paper_mode=paper_mode,
    )

    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    finally:
        await orchestrator.stop()

    logger.info("Arbitrage system stopped")


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Kalshi/Polymarket Arbitrage System"
    )
    parser.add_argument(
        "--config",
        "-c",
        help="Path to configuration YAML file",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Run in paper trading mode (default)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in live trading mode (use with caution)",
    )

    args = parser.parse_args()

    paper_mode = True
    if args.live:
        paper_mode = False
        logger.warning("LIVE TRADING MODE - Real money at risk!")

    asyncio.run(run(
        config_path=args.config,
        paper_mode=paper_mode,
    ))


if __name__ == "__main__":
    main()
