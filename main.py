#!/usr/bin/env python3
"""Main entry point for the Kalshi market data collection system.

DEMO VERSION - This is a demonstration version with mock API clients.
No real trading occurs. Strategy logic has been removed.

Usage:
    python main.py scan [--min-volume 1000] [--min-days 7]
    python main.py log [--tickers TICKER1,TICKER2]
    python main.py analyze [--days 3] [--min-score 12] [--top 10] [--export FILE]
    python main.py analyze --strategy market_making --min-suitability 6
    python main.py analyze --show-strategies
    python main.py schedule [--daemon]
    python main.py monitor
    python main.py healthcheck [--alert-if-unhealthy]
    python main.py pipeline [--skip-errors]
"""

import argparse
import sys
from typing import Optional

from src.core import Config, get_config, set_config, setup_logger
from src.collectors import Scanner, Logger
from src.analysis import MarketRanker, TradingStrategy
from src.automation import MarketMakerScheduler, SystemMonitor, HealthCheck, NBAGameScheduler

logger = setup_logger(__name__)


def print_demo_banner():
    """Print demo mode banner."""
    print("=" * 60)
    print("  DEMO MODE - Trading Utils Demonstration Version")
    print("=" * 60)
    print("  - No real API connections are made")
    print("  - All data is simulated/mocked")
    print("  - Strategy logic has been removed")
    print("  - For educational purposes only")
    print("=" * 60)
    print()


def cmd_scan(args: argparse.Namespace) -> int:
    """Execute the scan command."""
    print("=== Market Scanner ===\n")

    scanner = Scanner()
    try:
        count = scanner.scan_and_save(
            min_volume=args.min_volume,
            min_days_until_close=args.min_days,
        )
        print(f"\nScanned and saved {count} markets")
        return 0
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        print(f"Error: {e}")
        return 1
    finally:
        scanner.close()


def cmd_log(args: argparse.Namespace) -> int:
    """Execute the log command."""
    print("=== Data Logger ===\n")

    data_logger = Logger()
    try:
        if args.bulk:
            # Fast bulk mode - ~25 API calls instead of 2400+
            count = data_logger.log_snapshots_bulk(show_progress=not args.quiet)
        else:
            tickers = None
            if args.tickers:
                tickers = [t.strip() for t in args.tickers.split(",")]
            count = data_logger.log_snapshots(
                tickers=tickers,
                show_progress=not args.quiet,
            )
        print(f"\nLogged {count} snapshots")
        return 0
    except Exception as e:
        logger.error(f"Logging failed: {e}")
        print(f"Error: {e}")
        return 1
    finally:
        data_logger.close()


def cmd_analyze(args: argparse.Namespace) -> int:
    """Execute the analyze command."""
    print("=== Market Analysis ===\n")

    try:
        # Check if database has any markets first
        from src.core import MarketDatabase
        try:
            db = MarketDatabase()
        except Exception as e:
            print(f"Failed to create database connection: {e}")
            import traceback
            traceback.print_exc()
            return 1

        try:
            db.init_db()
        except Exception as e:
            print(f"Failed to initialize database: {e}")
            import traceback
            traceback.print_exc()
            return 1

        try:
            markets = db.get_all_markets()
        except Exception as e:
            print(f"Failed to get markets: {e}")
            import traceback
            traceback.print_exc()
            return 1

        if not markets:
            print("No markets in database. Run 'python3 main.py scan' first to populate data.")
            return 1

        ranker = MarketRanker()

        # Check if filtering by strategy
        if args.strategy:
            # Validate strategy name
            try:
                TradingStrategy(args.strategy)
            except ValueError:
                valid = [s.value for s in TradingStrategy]
                print(f"Invalid strategy '{args.strategy}'")
                print(f"Valid strategies: {', '.join(valid)}")
                return 1

            markets = ranker.get_markets_by_strategy(
                strategy=args.strategy,
                min_suitability=args.min_suitability,
                days=args.days,
            )

            if markets.empty:
                print(f"No markets suitable for {args.strategy} strategy")
                return 0

            print(f"Markets for {args.strategy} (suitability >= {args.min_suitability}):\n")
            print("-" * 90)
            print(f"{'Rank':<5} {'Ticker':<25} {'Suitability':<12} {'Score':<8} {'Spread%':<10} {'Volume':<12}")
            print("-" * 90)

            for idx, row in markets.iterrows():
                spread = row.get('avg_spread_pct', 0) or 0
                volume = row.get('avg_volume', 0) or 0
                suitability = row.get('strategy_suitability', 0) or 0
                print(f"{idx + 1:<5} {row['ticker']:<25} {suitability:<12.1f} {row['score']:<8.1f} {spread:<10.2f} {volume:<12.0f}")

            print("-" * 90)
            return 0

        if args.export:
            # Export to CSV
            path = ranker.export_to_csv(
                filename=args.export,
                days=args.days,
                min_score=args.min_score,
                include_strategies=args.show_strategies,
            )
            print(f"Exported rankings to {path}")
        else:
            # Display top markets
            include_strategies = args.show_strategies
            top_markets = ranker.get_top_markets(
                n=args.top,
                min_score=args.min_score,
                days=args.days,
                include_strategies=include_strategies,
            )

            if top_markets.empty:
                print("No markets meet the criteria")
                return 0

            print(f"Top {len(top_markets)} Markets (score >= {args.min_score}):\n")

            if include_strategies:
                print("-" * 110)
                print(f"{'Rank':<5} {'Ticker':<25} {'Score':<8} {'Spread%':<10} {'Volume':<12} {'Best Strategy':<20} {'Suit.':<6}")
                print("-" * 110)

                for idx, row in top_markets.iterrows():
                    spread = row.get('avg_spread_pct', 0) or 0
                    volume = row.get('avg_volume', 0) or 0
                    best_strategy = row.get('best_strategy', '-') or '-'
                    strategy_score = row.get('strategy_score', 0) or 0
                    print(f"{idx + 1:<5} {row['ticker']:<25} {row['score']:<8.1f} {spread:<10.2f} {volume:<12.0f} {best_strategy:<20} {strategy_score:<6.1f}")

                print("-" * 110)
            else:
                print("-" * 80)
                print(f"{'Rank':<5} {'Ticker':<25} {'Score':<8} {'Spread%':<10} {'Volume':<12}")
                print("-" * 80)

                for idx, row in top_markets.iterrows():
                    spread = row.get('avg_spread_pct', 0) or 0
                    volume = row.get('avg_volume', 0) or 0
                    print(f"{idx + 1:<5} {row['ticker']:<25} {row['score']:<8.1f} {spread:<10.2f} {volume:<12.0f}")

                print("-" * 80)

        return 0
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        print(f"Error: {e}")
        return 1


def cmd_schedule(args: argparse.Namespace) -> int:
    """Execute the schedule command."""
    print("=== Scheduler ===\n")

    scheduler = MarketMakerScheduler()

    if args.run_once:
        print(f"Running job: {args.run_once}")
        try:
            scheduler.run_once(args.run_once)
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1
        finally:
            scheduler._cleanup()
    else:
        if args.daemon:
            print("Starting scheduler as daemon...")
            # In production, you'd use proper daemonization
            # For now, just run in foreground
        else:
            print("Starting scheduler (Ctrl+C to stop)...")

        scheduler.run_forever()
        return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    """Execute the monitor command."""
    monitor = SystemMonitor()
    try:
        monitor.display()
        return 0
    except Exception as e:
        logger.error(f"Monitor failed: {e}")
        print(f"Error: {e}")
        return 1
    finally:
        monitor.close()


def cmd_healthcheck(args: argparse.Namespace) -> int:
    """Execute the healthcheck command."""
    checker = HealthCheck()
    status = checker.run_all_checks()
    print(status)

    if args.alert_if_unhealthy and not status.healthy:
        return 1
    return 0


def cmd_pipeline(args: argparse.Namespace) -> int:
    """Execute the full pipeline."""
    from pipeline import DataPipeline

    print("=== Data Pipeline ===\n")

    pipeline = DataPipeline()
    try:
        results = pipeline.run_full_pipeline(skip_on_error=args.skip_errors)

        print("\n" + "=" * 50)
        print("Pipeline Summary:")
        print("=" * 50)
        for stage, result in results.items():
            status = "OK" if result.get("success") else "FAILED"
            print(f"  {stage}: {status}")
            if result.get("count"):
                print(f"    Count: {result['count']}")
            if result.get("error"):
                print(f"    Error: {result['error']}")

        # Return 1 if any stage failed
        if any(not r.get("success") for r in results.values()):
            return 1
        return 0
    except Exception as e:
        logger.error(f"Pipeline failed: {e}")
        print(f"Error: {e}")
        return 1
    finally:
        pipeline.close()


def cmd_run_simulation(args: argparse.Namespace) -> int:
    """Run market-making with simulated data."""
    print("=== Market-Making Simulation ===\n")

    try:
        from src.core.config import RiskConfig
        from src.engine import MarketMakingEngine
        from src.execution.mock_api_client import MockAPIClient
        from src.market_making.config import MarketMakerConfig
        from src.simulation import get_scenario, create_simulator

        # Create simulator from scenario
        try:
            scenario_config = get_scenario(args.scenario)
        except ValueError as e:
            print(f"Error: {e}")
            return 1

        simulator = create_simulator(scenario_config, args.ticker)

        # Create mock API client that wraps the simulator
        api_client = MockAPIClient()

        # Create configs
        mm_config = MarketMakerConfig(
            target_spread=args.spread,
            max_position=args.max_position,
            quote_size=10,
        )

        risk_config = RiskConfig(
            max_position_size=args.max_position,
            max_total_position=args.max_position * 2,
            max_loss_per_position=25.0,
            max_daily_loss=100.0,
        )

        # Create engine
        engine = MarketMakingEngine(
            ticker=args.ticker,
            api_client=api_client,
            mm_config=mm_config,
            risk_config=risk_config,
        )

        print(f"Ticker: {args.ticker}")
        print(f"Scenario: {args.scenario}")
        print(f"Steps: {args.steps}")
        print(f"Target Spread: {args.spread:.1%}")
        print(f"Max Position: {args.max_position}")
        print()

        # Run simulation
        for i in range(args.steps):
            market = simulator.generate_market_state()

            # Convert to market_making MarketState
            from src.market_making.models import MarketState as MMMarketState
            mm_market = MMMarketState(
                ticker=args.ticker,
                timestamp=market.timestamp,
                best_bid=market.bid,
                best_ask=market.ask,
                mid_price=market.mid,
                bid_size=100,
                ask_size=100,
            )

            engine.on_market_update(mm_market)

            if args.verbose and (i + 1) % 10 == 0:
                status = engine.get_status()
                pos = status["market_maker"]["position"]
                print(
                    f"Step {i+1}: mid={market.mid:.4f}, "
                    f"pos={pos['contracts']}, "
                    f"pnl=${pos['total_pnl']:.2f}"
                )

        # Print final status
        status = engine.get_status()
        pos = status["market_maker"]["position"]
        stats = status["market_maker"]["stats"]

        print("\n" + "=" * 50)
        print("SIMULATION COMPLETE")
        print("=" * 50)
        print(f"Position: {pos['contracts']} contracts")
        print(f"Avg Entry: {pos['avg_entry_price']:.4f}")
        print(f"Unrealized P&L: ${pos['unrealized_pnl']:.2f}")
        print(f"Realized P&L: ${pos['realized_pnl']:.2f}")
        print(f"Total P&L: ${pos['total_pnl']:.2f}")
        print(f"Quotes Generated: {stats['quotes_generated']}")
        print(f"Fills: {stats['quotes_filled']}")
        print(f"Volume: {stats['total_volume']}")

        return 0

    except Exception as e:
        logger.error(f"Simulation failed: {e}")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def cmd_run_single(args: argparse.Namespace) -> int:
    """Run market-making on a single market."""
    print("=== Single Market Trading ===\n")
    print(f"Ticker: {args.ticker}")
    print(f"Target Spread: {args.spread:.1%}")
    print(f"Quote Size: {args.size}")
    print(f"Max Position: {args.max_position}")
    print()

    if args.dry_run:
        print("DRY RUN MODE - Real market data, orders logged but not placed")
        print("-" * 50)

        try:
            from src.core.config import RiskConfig
            from src.engine import MarketMakingEngine
            from src.execution.dry_run_client import DryRunAPIClient
            from src.market_making.config import MarketMakerConfig

            # In a full implementation, you would create the real API client here
            # real_client = create_real_api_client()
            # For now, we'll use None and the dry run client will error on market data
            # You should replace this with your actual API client creation
            dry_run_client = DryRunAPIClient(real_client=None, simulate_fills=True)

            mm_config = MarketMakerConfig(
                target_spread=args.spread,
                max_position=args.max_position,
                quote_size=args.size,
            )

            risk_config = RiskConfig(
                max_position_size=args.max_position,
                max_total_position=args.max_position * 2,
                max_loss_per_position=args.max_loss,
                max_daily_loss=args.daily_loss,
            )

            engine = MarketMakingEngine(
                ticker=args.ticker,
                api_client=dry_run_client,
                mm_config=mm_config,
                risk_config=risk_config,
            )

            print(f"\nDry run engine created for {args.ticker}")
            print("Note: Connect a real API client to receive live market data.")
            print("Orders will be logged with [DRY RUN] prefix but not executed.")

            # Print summary at the end
            dry_run_client.print_summary()

            return 0

        except Exception as e:
            logger.error(f"Dry run failed: {e}")
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1

    elif args.paper:
        print("Paper trading mode - simulated data, no API connection")
        print("Use --dry-run for real market data without executing orders.")
        print("This would connect to the market and run the strategy.")

    else:
        print("WARNING: Live trading is not yet implemented.")
        print("Use --paper for paper trading or --dry-run for dry run mode.")

    return 0


def cmd_run_multi(args: argparse.Namespace) -> int:
    """Run market-making on multiple markets."""
    print("=== Multi-Market Trading ===\n")

    tickers = [t.strip() for t in args.tickers.split(",")]
    print(f"Markets: {', '.join(tickers)}")
    print(f"Target Spread: {args.spread:.1%}")
    print(f"Max Total Position: {args.max_total_position}")
    print()

    if args.dry_run:
        print("DRY RUN MODE - Real market data, orders logged but not placed")
        print("-" * 50)

        try:
            from src.core.config import RiskConfig
            from src.engine import MultiMarketEngine
            from src.execution.dry_run_client import DryRunAPIClient
            from src.market_making.config import MarketMakerConfig

            # Create dry run client (replace None with real client for live data)
            dry_run_client = DryRunAPIClient(real_client=None, simulate_fills=True)

            mm_config = MarketMakerConfig(
                target_spread=args.spread,
                max_position=args.max_total_position // len(tickers),
            )

            risk_config = RiskConfig(
                max_position_size=args.max_total_position // len(tickers),
                max_total_position=args.max_total_position,
                max_loss_per_position=args.daily_loss / len(tickers),
                max_daily_loss=args.daily_loss,
            )

            engine = MultiMarketEngine(
                api_client=dry_run_client,
                default_mm_config=mm_config,
                global_risk_config=risk_config,
            )

            for ticker in tickers:
                engine.add_market(ticker)
                print(f"  Added market: {ticker}")

            print(f"\nDry run engine created for {len(tickers)} markets")
            print("Note: Connect a real API client to receive live market data.")
            print("Orders will be logged with [DRY RUN] prefix but not executed.")

            dry_run_client.print_summary()

            return 0

        except Exception as e:
            logger.error(f"Dry run failed: {e}")
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1

    elif args.paper:
        print("Paper trading mode - simulated data, no API connection")
        print("Use --dry-run for real market data without executing orders.")

    else:
        print("WARNING: Live trading is not yet implemented.")
        print("Use --paper for paper trading or --dry-run for dry run mode.")

    return 0


def cmd_nba_record(args: argparse.Namespace) -> int:
    """Run NBA game auto-scheduler."""
    print("=== NBA Game Auto-Scheduler ===\n")

    scheduler = NBAGameScheduler(
        poll_interval=args.poll_interval,
        demo=args.demo,
        verbose=args.verbose,
    )

    if args.once:
        print("Running single poll...")
        started = scheduler.poll_once()
        print(f"Started {started} new recorder(s)")

        status = scheduler.get_status()
        print(f"Active: {status['active_count']}, Completed today: {status['completed_count']}")

        for game in status["active_games"]:
            print(f"  Recording: {game['matchup']} ({game['frames']} frames)")
        return 0
    else:
        print(f"Poll interval: {args.poll_interval}s")
        print("Press Ctrl+C to stop...")
        print()

        try:
            scheduler.run_forever()
        except KeyboardInterrupt:
            print("\nShutdown requested...")
            scheduler.stop()

        return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """Backtest strategy on historical data."""
    print("=== Strategy Backtest ===\n")

    print(f"Ticker: {args.ticker}")
    print(f"Days: {args.days}")
    print(f"Target Spread: {args.spread:.1%}")
    print()

    try:
        from src.core import MarketDatabase

        db = MarketDatabase()

        # Get historical snapshots
        from datetime import datetime, timedelta
        end_time = datetime.now()
        start_time = end_time - timedelta(days=args.days)

        snapshots = db.get_snapshots_in_range(
            args.ticker,
            start_time.isoformat(),
            end_time.isoformat(),
        )

        if not snapshots:
            print(f"No historical data found for {args.ticker}")
            return 1

        print(f"Found {len(snapshots)} snapshots")
        print()

        # Run backtest simulation
        from src.core.config import RiskConfig
        from src.engine import MarketMakingEngine
        from src.execution.mock_api_client import MockAPIClient
        from src.market_making.config import MarketMakerConfig
        from src.market_making.models import MarketState as MMMarketState

        api_client = MockAPIClient()

        mm_config = MarketMakerConfig(
            target_spread=args.spread,
            max_position=50,
        )

        risk_config = RiskConfig(
            max_position_size=50,
            max_total_position=100,
            max_loss_per_position=25.0,
            max_daily_loss=100.0,
        )

        engine = MarketMakingEngine(
            ticker=args.ticker,
            api_client=api_client,
            mm_config=mm_config,
            risk_config=risk_config,
        )

        # Process each snapshot
        for snap in snapshots:
            if snap.yes_bid is None or snap.yes_ask is None:
                continue

            mm_market = MMMarketState(
                ticker=args.ticker,
                timestamp=datetime.now(),
                best_bid=snap.yes_bid / 100.0,
                best_ask=snap.yes_ask / 100.0,
                mid_price=snap.mid_price / 100.0 if snap.mid_price else (snap.yes_bid + snap.yes_ask) / 200.0,
                bid_size=snap.orderbook_bid_depth or 0,
                ask_size=snap.orderbook_ask_depth or 0,
            )

            engine.on_market_update(mm_market)

        # Print results
        status = engine.get_status()
        pos = status["market_maker"]["position"]
        stats = status["market_maker"]["stats"]

        print("=" * 50)
        print("BACKTEST RESULTS")
        print("=" * 50)
        print(f"Snapshots Processed: {len(snapshots)}")
        print(f"Final Position: {pos['contracts']}")
        print(f"Total P&L: ${pos['total_pnl']:.2f}")
        print(f"Realized P&L: ${pos['realized_pnl']:.2f}")
        print(f"Unrealized P&L: ${pos['unrealized_pnl']:.2f}")
        print(f"Quotes Generated: {stats['quotes_generated']}")
        print(f"Fills: {stats['quotes_filled']}")
        print(f"Volume: {stats['total_volume']}")

        if args.output:
            import json
            with open(args.output, 'w') as f:
                json.dump({
                    "ticker": args.ticker,
                    "days": args.days,
                    "snapshots": len(snapshots),
                    "final_position": pos['contracts'],
                    "total_pnl": pos['total_pnl'],
                    "realized_pnl": pos['realized_pnl'],
                    "quotes_generated": stats['quotes_generated'],
                    "fills": stats['quotes_filled'],
                    "volume": stats['total_volume'],
                }, f, indent=2)
            print(f"\nResults saved to {args.output}")

        return 0

    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def main() -> int:
    """Main entry point."""
    print_demo_banner()

    parser = argparse.ArgumentParser(
        description="Kalshi Market Data Collection System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Scan command
    scan_parser = subparsers.add_parser("scan", help="Scan for markets")
    scan_parser.add_argument("--min-volume", type=int, default=None, help="Minimum 24h volume")
    scan_parser.add_argument("--min-days", type=int, default=7, help="Minimum days until close")

    # Log command
    log_parser = subparsers.add_parser("log", help="Log market snapshots")
    log_parser.add_argument("--tickers", type=str, default=None, help="Comma-separated tickers")
    log_parser.add_argument("--quiet", action="store_true", help="Suppress progress output")
    log_parser.add_argument("--bulk", action="store_true", help="Fast bulk mode - fetches all markets in ~25 API calls")

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze and rank markets")
    analyze_parser.add_argument("--days", type=int, default=3, help="Days of data to analyze")
    analyze_parser.add_argument("--min-score", type=float, default=12.0, help="Minimum score threshold")
    analyze_parser.add_argument("--top", type=int, default=10, help="Number of top markets to show")
    analyze_parser.add_argument("--export", type=str, default=None, help="Export to CSV file")
    analyze_parser.add_argument(
        "--strategy",
        type=str,
        default=None,
        help="Filter by strategy (market_making, spread_trading, momentum, scalping, arbitrage, event_trading)",
    )
    analyze_parser.add_argument(
        "--min-suitability",
        type=float,
        default=6.0,
        help="Minimum strategy suitability score (default: 6.0)",
    )
    analyze_parser.add_argument(
        "--show-strategies",
        action="store_true",
        help="Show strategy labels in output",
    )

    # Schedule command
    schedule_parser = subparsers.add_parser("schedule", help="Start the scheduler")
    schedule_parser.add_argument("--daemon", action="store_true", help="Run as daemon")
    schedule_parser.add_argument(
        "--run-once",
        type=str,
        choices=["scan_markets", "log_data", "analyze_markets"],
        default=None,
        help="Run a specific job once",
    )

    # Monitor command
    subparsers.add_parser("monitor", help="Display system status")

    # Healthcheck command
    health_parser = subparsers.add_parser("healthcheck", help="Run health checks")
    health_parser.add_argument(
        "--alert-if-unhealthy",
        action="store_true",
        help="Exit with code 1 if unhealthy",
    )

    # Pipeline command
    pipeline_parser = subparsers.add_parser("pipeline", help="Run full data pipeline")
    pipeline_parser.add_argument(
        "--skip-errors",
        action="store_true",
        help="Continue on non-critical failures",
    )

    # Market-Making Commands
    # run-simulation command
    sim_parser = subparsers.add_parser("run-simulation", help="Run market-making with simulated data")
    sim_parser.add_argument("--ticker", type=str, default="SIM-MARKET", help="Simulated ticker name")
    sim_parser.add_argument("--steps", type=int, default=100, help="Number of simulation steps")
    sim_parser.add_argument("--scenario", type=str, default="stable_market",
                           help="Scenario: stable_market, volatile_market, trending_up, trending_down, mean_reverting")
    sim_parser.add_argument("--spread", type=float, default=0.04, help="Target spread (e.g., 0.04 for 4%%)")
    sim_parser.add_argument("--max-position", type=int, default=50, help="Maximum position size")
    sim_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    # run-single command
    single_parser = subparsers.add_parser("run-single", help="Run market-making on single market")
    single_parser.add_argument("ticker", type=str, help="Market ticker")
    single_parser.add_argument("--paper", action="store_true", help="Paper trading mode (simulated data, no API)")
    single_parser.add_argument("--dry-run", action="store_true", help="Dry run mode (real data, orders logged but not placed)")
    single_parser.add_argument("--spread", type=float, default=0.04, help="Target spread")
    single_parser.add_argument("--size", type=int, default=20, help="Quote size")
    single_parser.add_argument("--max-position", type=int, default=50, help="Maximum position")
    single_parser.add_argument("--max-loss", type=float, default=50.0, help="Max loss per position (dollars)")
    single_parser.add_argument("--daily-loss", type=float, default=200.0, help="Max daily loss (dollars)")

    # run-multi command
    multi_parser = subparsers.add_parser("run-multi", help="Run market-making on multiple markets")
    multi_parser.add_argument("tickers", type=str, help="Comma-separated market tickers")
    multi_parser.add_argument("--paper", action="store_true", help="Paper trading mode (simulated data)")
    multi_parser.add_argument("--dry-run", action="store_true", help="Dry run mode (real data, orders logged)")
    multi_parser.add_argument("--spread", type=float, default=0.04, help="Target spread")
    multi_parser.add_argument("--max-total-position", type=int, default=200, help="Max total position")
    multi_parser.add_argument("--daily-loss", type=float, default=500.0, help="Max daily loss")

    # backtest command
    backtest_parser = subparsers.add_parser("backtest", help="Backtest strategy on historical data")
    backtest_parser.add_argument("ticker", type=str, help="Market ticker")
    backtest_parser.add_argument("--days", type=int, default=7, help="Days of historical data")
    backtest_parser.add_argument("--spread", type=float, default=0.04, help="Target spread")
    backtest_parser.add_argument("--output", type=str, default=None, help="Output file for results")

    # nba-record command
    nba_parser = subparsers.add_parser("nba-record", help="Auto-detect and record NBA games")
    nba_parser.add_argument("--once", action="store_true", help="Poll once and exit (for testing)")
    nba_parser.add_argument("--poll-interval", type=int, default=300, help="Poll interval in seconds (default: 300)")
    nba_parser.add_argument("--demo", action="store_true", help="Use Kalshi demo API")
    nba_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")

    args = parser.parse_args()

    # Load config
    if args.config:
        config = Config.from_yaml(args.config)
        set_config(config)

    # Route to appropriate command
    if args.command == "scan":
        return cmd_scan(args)
    elif args.command == "log":
        return cmd_log(args)
    elif args.command == "analyze":
        return cmd_analyze(args)
    elif args.command == "schedule":
        return cmd_schedule(args)
    elif args.command == "monitor":
        return cmd_monitor(args)
    elif args.command == "healthcheck":
        return cmd_healthcheck(args)
    elif args.command == "pipeline":
        return cmd_pipeline(args)
    elif args.command == "run-simulation":
        return cmd_run_simulation(args)
    elif args.command == "run-single":
        return cmd_run_single(args)
    elif args.command == "run-multi":
        return cmd_run_multi(args)
    elif args.command == "backtest":
        return cmd_backtest(args)
    elif args.command == "nba-record":
        return cmd_nba_record(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
