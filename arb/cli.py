"""Command-line interface for the arbitrage system.

Provides operator controls for:
- Starting/stopping the system
- Viewing status and positions
- Managing the circuit breaker
- Listing opportunities

Usage:
    python -m arb.cli start [--paper] [--config config.yaml]
    python -m arb.cli stop
    python -m arb.cli status
    python -m arb.cli positions
    python -m arb.cli opportunities [--min-roi 0.05]
    python -m arb.cli circuit-breaker status
    python -m arb.cli circuit-breaker reset --confirm
"""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import click

from .config import ArbitrageConfig
from .orchestrator import ArbitrageOrchestrator


# PID file for tracking running instance
PID_FILE = Path("/tmp/arbitrage_system.pid")
STATE_FILE = Path("/tmp/arbitrage_system.state")


def write_pid():
    """Write current process PID to file."""
    PID_FILE.write_text(str(os.getpid()))


def read_pid() -> Optional[int]:
    """Read PID from file if exists."""
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except ValueError:
            return None
    return None


def is_running() -> bool:
    """Check if the system is already running."""
    pid = read_pid()
    if pid is None:
        return False

    try:
        # Check if process exists
        os.kill(pid, 0)
        return True
    except OSError:
        # Process doesn't exist
        PID_FILE.unlink(missing_ok=True)
        return False


def save_state(orchestrator: ArbitrageOrchestrator):
    """Save orchestrator state to file."""
    status = orchestrator.get_status()
    STATE_FILE.write_text(json.dumps(status, indent=2, default=str))


def load_state() -> Optional[dict]:
    """Load state from file if exists."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return None
    return None


@click.group()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.pass_context
def cli(ctx, config):
    """Kalshi/Polymarket Arbitrage System CLI."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


@cli.command()
@click.option(
    "--paper/--live", default=True, help="Paper trading mode (default: paper)"
)
@click.option("--daemon", "-d", is_flag=True, help="Run in background")
@click.pass_context
def start(ctx, paper, daemon):
    """Start the arbitrage system."""
    if is_running():
        click.echo(
            "Error: Arbitrage system is already running (PID: {})".format(read_pid())
        )
        click.echo("Use 'stop' command to stop it first.")
        sys.exit(1)

    config_path = ctx.obj.get("config_path")

    if not paper:
        if not click.confirm(
            "WARNING: Live trading mode will use REAL MONEY. Continue?",
            default=False,
        ):
            click.echo("Aborted.")
            return

    click.echo("Starting arbitrage system...")
    click.echo(f"  Mode: {'Paper' if paper else 'LIVE'}")
    if config_path:
        click.echo(f"  Config: {config_path}")

    if daemon:
        click.echo("Running in daemon mode...")
        # Fork to background
        if os.fork() > 0:
            click.echo("System started in background.")
            return

        # Detach from terminal
        os.setsid()
        if os.fork() > 0:
            os._exit(0)

    # Write PID file
    write_pid()

    async def run_system():
        from .runner import create_orchestrator

        orchestrator = await create_orchestrator(
            config_path=config_path,
            paper_mode=paper,
        )

        # Save state periodically
        async def state_saver():
            while orchestrator.is_running:
                save_state(orchestrator)
                await asyncio.sleep(10)

        try:
            asyncio.create_task(state_saver())
            await orchestrator.start()
        finally:
            PID_FILE.unlink(missing_ok=True)
            STATE_FILE.unlink(missing_ok=True)

    try:
        asyncio.run(run_system())
    except KeyboardInterrupt:
        click.echo("\nShutting down...")
    finally:
        PID_FILE.unlink(missing_ok=True)


@cli.command()
def stop():
    """Stop the arbitrage system."""
    pid = read_pid()

    if pid is None:
        click.echo("Arbitrage system is not running.")
        return

    if not is_running():
        click.echo("Arbitrage system is not running (stale PID file).")
        PID_FILE.unlink(missing_ok=True)
        return

    click.echo(f"Stopping arbitrage system (PID: {pid})...")

    try:
        os.kill(pid, signal.SIGTERM)
        click.echo("Stop signal sent. System will shut down gracefully.")
    except OSError as e:
        click.echo(f"Error stopping system: {e}")
        sys.exit(1)


@cli.command()
def status():
    """Show system status."""
    if not is_running():
        click.echo("Arbitrage system is not running.")

        # Try to show last known state
        state = load_state()
        if state:
            click.echo("\nLast known state:")
            _print_status(state)
        return

    # Load current state
    state = load_state()
    if not state:
        click.echo("System is running but no state available yet.")
        return

    click.echo("Arbitrage System Status")
    click.echo("=" * 50)
    _print_status(state)


def _print_status(state: dict):
    """Print formatted status."""
    # State info
    state_info = state.get("state", {})
    click.echo(f"\nRunning: {state_info.get('running', False)}")
    click.echo(f"Paused: {state_info.get('paused', False)}")
    if state_info.get("started_at"):
        click.echo(f"Started: {state_info['started_at']}")
        uptime = state_info.get("uptime_seconds", 0)
        hours, remainder = divmod(int(uptime), 3600)
        minutes, seconds = divmod(remainder, 60)
        click.echo(f"Uptime: {hours}h {minutes}m {seconds}s")
    if state_info.get("last_scan_at"):
        click.echo(f"Last Scan: {state_info['last_scan_at']}")

    # Stats
    stats = state.get("stats", {})
    click.echo("\nStatistics:")
    click.echo(f"  Opportunities Detected: {stats.get('opportunities_detected', 0)}")
    click.echo(f"  Trades Executed: {stats.get('trades_executed', 0)}")
    click.echo(f"  Successful Trades: {stats.get('successful_trades', 0)}")
    click.echo(f"  Success Rate: {stats.get('success_rate', 0):.1%}")
    click.echo(f"  Total Profit: ${stats.get('total_profit', 0):.2f}")

    # Circuit breaker
    cb = state.get("circuit_breaker", {})
    cb_state = cb.get("state", "unknown")
    click.echo(f"\nCircuit Breaker: {cb_state.upper()}")
    if cb.get("current_trip"):
        trip = cb["current_trip"]
        click.echo(f"  Reason: {trip.get('reason')}")
        click.echo(f"  Timestamp: {trip.get('timestamp')}")

    # Config summary
    config = state.get("config", {})
    click.echo("\nConfiguration:")
    click.echo(f"  Paper Mode: {config.get('paper_mode', True)}")
    click.echo(f"  Scan Interval: {config.get('scan_interval', 1.0)}s")
    click.echo(f"  Max Concurrent: {config.get('max_concurrent_spreads', 3)}")


@cli.command()
def positions():
    """Show current positions."""
    state = load_state()
    if not state:
        click.echo("No state available. Is the system running?")
        return

    active = state.get("active_spreads", 0)
    click.echo(f"Active Spreads: {active}")

    # Would need more detailed position info from the system
    click.echo("\n(Detailed position view not yet implemented)")


@cli.command()
@click.option("--min-roi", type=float, default=0.02, help="Minimum ROI filter")
@click.option("--limit", type=int, default=10, help="Maximum opportunities to show")
def opportunities(min_roi, limit):
    """List current arbitrage opportunities."""
    click.echo("Scanning for opportunities...")

    async def scan():
        try:
            from .runner import create_orchestrator

            # Create minimal orchestrator just for scanning
            orchestrator = await create_orchestrator(paper_mode=True)

            if not orchestrator._detector:
                click.echo("Error: Quote source not available")
                return

            # Run a scan
            opps = orchestrator._detector.scan_all_pairs()

            if not opps:
                click.echo("No opportunities found.")
                return

            click.echo(f"\nFound {len(opps)} opportunities (showing top {limit}):\n")
            click.echo(
                f"{'Type':<20} {'Buy':<12} {'Sell':<12} {'Edge':>8} {'ROI':>8} {'Profit':>10}"
            )
            click.echo("-" * 72)

            for opp in opps[:limit]:
                if opp.roi < min_roi:
                    continue

                click.echo(
                    f"{opp.opportunity.opportunity_type:<20} "
                    f"{opp.opportunity.buy_platform.value:<12} "
                    f"{opp.opportunity.sell_platform.value:<12} "
                    f"{opp.net_edge:>8.4f} "
                    f"{opp.roi:>7.2%} "
                    f"${opp.estimated_profit:>9.2f}"
                )

        except Exception as e:
            click.echo(f"Error scanning: {e}")

    asyncio.run(scan())


@cli.group("circuit-breaker")
def circuit_breaker():
    """Circuit breaker management commands."""
    pass


@circuit_breaker.command("status")
def cb_status():
    """Show circuit breaker status."""
    state = load_state()
    if not state:
        click.echo("No state available. Is the system running?")
        return

    cb = state.get("circuit_breaker", {})

    click.echo("Circuit Breaker Status")
    click.echo("=" * 50)
    click.echo(f"\nState: {cb.get('state', 'unknown').upper()}")
    click.echo(f"Is Closed: {cb.get('is_closed', False)}")

    # Metrics
    metrics = cb.get("metrics", {})
    click.echo("\nMetrics:")
    click.echo(f"  Total Trades: {metrics.get('total_trades', 0)}")
    click.echo(f"  Successful: {metrics.get('successful_trades', 0)}")
    click.echo(f"  Failed: {metrics.get('failed_trades', 0)}")
    click.echo(f"  Error Rate: {metrics.get('error_rate', 0):.2%}")
    click.echo(f"  Fill Rate: {metrics.get('fill_rate', 1.0):.2%}")
    click.echo(f"  Daily P&L: ${metrics.get('daily_pnl', 0):.2f}")
    click.echo(f"  Daily Loss: ${metrics.get('daily_loss', 0):.2f}")
    click.echo(f"  Avg Latency: {metrics.get('avg_latency', 0):.3f}s")
    click.echo(f"  P95 Latency: {metrics.get('p95_latency', 0):.3f}s")

    # Thresholds
    thresholds = cb.get("thresholds", {})
    click.echo("\nThresholds:")
    click.echo(f"  Max Daily Loss: ${thresholds.get('max_daily_loss', 500):.2f}")
    click.echo(f"  Max Error Rate: {thresholds.get('max_error_rate', 0.10):.2%}")
    click.echo(f"  Min Fill Rate: {thresholds.get('min_fill_rate', 0.70):.2%}")
    click.echo(f"  Max API Latency: {thresholds.get('max_api_latency', 2.0):.1f}s")

    # Current trip
    trip = cb.get("current_trip")
    if trip:
        click.echo("\nCurrent Trip:")
        click.echo(f"  Reason: {trip.get('reason')}")
        click.echo(f"  Metric: {trip.get('metric')}")
        click.echo(f"  Value: {trip.get('value'):.4f}")
        click.echo(f"  Threshold: {trip.get('threshold'):.4f}")
        click.echo(f"  Time: {trip.get('timestamp')}")

    click.echo(f"\nTrip Count: {cb.get('trip_count', 0)}")


@circuit_breaker.command("reset")
@click.option("--confirm", is_flag=True, required=True, help="Confirm reset")
@click.option("--operator", "-o", required=True, help="Operator ID for audit")
def cb_reset(confirm, operator):
    """Reset the circuit breaker after a trip."""
    if not confirm:
        click.echo("Error: Must specify --confirm to reset")
        return

    if not is_running():
        click.echo("Error: System is not running")
        return

    click.echo(f"Resetting circuit breaker as operator: {operator}")

    # Would need IPC mechanism to communicate with running process
    # For now, this is a placeholder
    click.echo("\nNote: Direct circuit breaker reset requires IPC mechanism.")
    click.echo("Please restart the system to reset the circuit breaker.")


@cli.command()
def config():
    """Show current configuration."""
    # Load from file or show defaults
    cfg = ArbitrageConfig()

    click.echo("Arbitrage Configuration")
    click.echo("=" * 50)

    for key, value in cfg.to_dict().items():
        click.echo(f"  {key}: {value}")


@cli.command()
def version():
    """Show version information."""
    click.echo("Kalshi/Polymarket Arbitrage System")
    click.echo("Version: 1.0.0")


@cli.command()
@click.option("--verbose", "-v", is_flag=True, help="Show detailed output")
@click.pass_context
def preflight(ctx, verbose):
    """Run preflight checks without starting the system.

    Verifies:
    - API connectivity (Kalshi, Polymarket)
    - Account balances
    - Unexpected positions
    - Database connectivity
    - Incomplete spread recovery
    - Configuration validation
    """
    config_path = ctx.obj.get("config_path")

    click.echo("Running preflight checks...")
    click.echo("=" * 50)

    async def run_checks():
        from .preflight import PreflightChecker, CheckStatus
        from .config import ArbitrageConfig

        # Load config
        if config_path:
            config = ArbitrageConfig.from_yaml(config_path)
        else:
            config = ArbitrageConfig()

        # Initialize components
        kalshi_client = None
        polymarket_client = None
        db_manager = None
        recovery_service = None

        try:
            from src.exchanges.kalshi import KalshiExchange
            from src.exchanges.polymarket import PolymarketExchange

            kalshi_client = KalshiExchange()
            polymarket_client = PolymarketExchange()
        except ImportError as e:
            KalshiExchange = None  # type: ignore
            PolymarketExchange = None  # type: ignore
            click.echo(f"Warning: Exchange clients not available: {e}")

        try:
            from src.database.connection import get_database_manager

            db_manager = get_database_manager()
            await db_manager.initialize()
        except Exception as e:
            click.echo(f"Warning: Database not available: {e}")

        # Create checker
        checker = PreflightChecker(
            kalshi_client=kalshi_client,
            polymarket_client=polymarket_client,
            database_manager=db_manager,
            recovery_service=recovery_service,
            config=config,
        )

        # Run checks
        result = await checker.run_all_checks()

        # Display results
        click.echo("")
        for check in result.checks:
            if check.status == CheckStatus.PASSED:
                status_icon = click.style("✓", fg="green")
            elif check.status == CheckStatus.FAILED:
                status_icon = click.style("✗", fg="red")
            elif check.status == CheckStatus.WARNING:
                status_icon = click.style("!", fg="yellow")
            else:
                status_icon = click.style("-", fg="white")

            click.echo(f"  {status_icon} {check.name}: {check.message}")

            if verbose and check.details:
                for key, value in check.details.items():
                    click.echo(f"      {key}: {value}")

        click.echo("")
        click.echo("=" * 50)

        if result.passed:
            click.echo(click.style("PREFLIGHT PASSED", fg="green", bold=True))
        else:
            click.echo(click.style("PREFLIGHT FAILED", fg="red", bold=True))
            for failure in result.failures:
                click.echo(
                    click.style(f"  - {failure.name}: {failure.message}", fg="red")
                )

        click.echo(f"\nTotal time: {result.total_duration_ms:.0f}ms")

        # Get balance summary
        balances = checker.get_balance_summary()
        if balances["total"] > 0:
            click.echo("\nBalance Summary:")
            click.echo(f"  Kalshi:      ${balances['kalshi']:.2f}")
            click.echo(f"  Polymarket:  ${balances['polymarket']:.2f}")
            click.echo(f"  Total:       ${balances['total']:.2f}")

        return result.passed

    success = asyncio.run(run_checks())
    sys.exit(0 if success else 1)


def main():
    """Main entry point."""
    cli(obj={})


if __name__ == "__main__":
    main()
