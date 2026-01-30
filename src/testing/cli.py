#!/usr/bin/env python3
"""
CLI for the Arbitrage Testing Framework.

Usage:
    python -m src.testing.cli run --opportunities data/opportunities.json
    python -m src.testing.cli analyze --journal test_results/session/journal.json
    python -m src.testing.cli report --journal test_results/session/journal.json --format markdown
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def cmd_run(args: argparse.Namespace) -> int:
    """Run a test scenario."""
    # Use standalone demo mode for --demo flag (doesn't need OMS/executor)
    if args.demo:
        return _run_demo_standalone(args)

    from src.testing import ArbitrageTestHarness
    from src.arbitrage.config import ArbitrageConfig

    # Load opportunities
    if args.opportunities:
        with open(args.opportunities) as f:
            opportunities = json.load(f)
        logger.info(f"Loaded {len(opportunities)} opportunities from {args.opportunities}")
    else:
        logger.error("No opportunities file provided. Use --demo for demo mode.")
        return 1

    # Create output directory
    output_dir = Path(args.output) if args.output else Path("test_results")
    session_id = args.session_id or f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Load config
    config = ArbitrageConfig()
    if args.config:
        config = ArbitrageConfig.from_yaml(Path(args.config))

    # Create harness
    harness = ArbitrageTestHarness(
        config=config,
        initial_capital=args.capital,
        output_dir=output_dir,
        session_id=session_id,
        enable_live_display=not args.quiet,
    )

    market_client = _create_market_client(args)

    if market_client is None:
        logger.error("No market data client available. Use --demo for demo mode.")
        return 1

    try:
        harness.setup(market_data_client=market_client)

        # Run scenario
        analysis = harness.run_scenario(
            opportunities=opportunities,
            delay_between_trades_ms=args.delay,
        )

        # Generate reports
        paths = harness.generate_reports(analysis)

        print("\n" + "=" * 60)
        print("SESSION COMPLETE")
        print("=" * 60)
        print(f"Session ID:    {session_id}")
        print(f"Total Trades:  {analysis.total_trades}")
        print(f"Win Rate:      {analysis.win_rate:.1%}")
        print(f"Total P&L:     ${analysis.total_pnl_usd:+.2f}")
        print(f"Profit Factor: {analysis.profit_factor:.2f}")
        print()
        print("Reports saved to:")
        for report_type, path in paths.items():
            print(f"  {report_type}: {path}")
        print("=" * 60)

        return 0

    finally:
        harness.teardown()


def _run_demo_standalone(args: argparse.Namespace) -> int:
    """
    Run a standalone demo that tests journaling/analysis without OMS.

    This simulates trades directly to demonstrate the testing framework
    capabilities without requiring full exchange integration.
    """
    import random
    from src.testing import (
        TradeJournal, SessionAnalyzer, ReportGenerator,
        TradeJournalStatus, LiveMetricsDisplay,
    )
    from src.testing.models import (
        InputSnapshot, QuoteSnapshot, DecisionRecord, ExecutionEventType,
    )

    num_trades = args.num_trades
    output_dir = Path(args.output) if args.output else Path("test_results")
    session_id = args.session_id or f"demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    session_dir = output_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Running standalone demo with {num_trades} simulated trades")

    # Initialize journal
    journal = TradeJournal(
        session_id=session_id,
        output_dir=session_dir / "journal",
        auto_save=True,
    )

    # Initialize live display if not quiet
    display = None
    if not args.quiet:
        display = LiveMetricsDisplay(
            session_id=session_id,
            initial_capital=args.capital,
        )
        display.start(total_trades=num_trades)

    # Simulate trades
    for i in range(num_trades):
        spread_id = f"DEMO-SPREAD-{i:04d}"

        # Generate random market conditions
        base_price = random.uniform(0.30, 0.70)
        spread = random.uniform(0.02, 0.10)
        leg1_ask = base_price
        leg2_bid = base_price + spread
        size = random.randint(5, 25)

        # Calculate expected profit (after ~7% fees)
        gross_profit = spread * size
        expected_fees = gross_profit * 0.14  # ~7% each leg
        expected_net = gross_profit - expected_fees

        now = datetime.now()

        # Create input snapshot
        input_snapshot = InputSnapshot(
            leg1_quote=QuoteSnapshot(
                exchange="demo_exchange_a",
                ticker=f"DEMO-{i:03d}-YES",
                bid=leg1_ask - 0.02,
                ask=leg1_ask,
                mid=leg1_ask - 0.01,
                spread=0.02,
                bid_size=100,
                ask_size=size + 10,
                timestamp=now,
                age_ms=random.uniform(50, 500),
            ),
            leg2_quote=QuoteSnapshot(
                exchange="demo_exchange_b",
                ticker=f"DEMO-{i:03d}-YES",
                bid=leg2_bid,
                ask=leg2_bid + 0.02,
                mid=leg2_bid + 0.01,
                spread=0.02,
                bid_size=size + 10,
                ask_size=100,
                timestamp=now,
                age_ms=random.uniform(50, 500),
            ),
            expected_gross_spread=gross_profit,
            expected_net_spread=expected_net,
            expected_fees=expected_fees,
            capital_available=args.capital,
            active_positions=i,
            active_spreads=1,
        )

        # Create decision record
        decision_record = DecisionRecord(
            opportunity_rank=1,
            total_opportunities=random.randint(1, 5),
            edge_cents=spread * 100,
            roi_pct=expected_net / (leg1_ask * size),
            liquidity_score=random.uniform(0.6, 1.0),
            filters_passed=["min_edge", "min_roi", "min_liquidity"],
            filters_failed=[],
            decision_reason=f"Demo trade {i + 1}",
            alternative_opportunities=[],
        )

        # Start trade in journal
        journal.start_trade(spread_id, input_snapshot, decision_record)
        journal.record_event(spread_id, ExecutionEventType.DECISION, {})
        journal.record_event(spread_id, ExecutionEventType.LEG1_SUBMITTED, {})

        # Simulate execution outcome (70% success rate)
        outcome = random.random()

        if outcome < 0.70:
            # Success - both legs fill
            slippage1 = random.uniform(-0.005, 0.015)  # Sometimes favorable
            slippage2 = random.uniform(-0.005, 0.015)

            leg1_actual = leg1_ask + slippage1
            leg2_actual = leg2_bid - slippage2

            journal.record_event(spread_id, ExecutionEventType.LEG1_FILLED,
                               {"price": leg1_actual, "size": size})
            journal.record_event(spread_id, ExecutionEventType.LEG2_SUBMITTED, {})
            journal.record_event(spread_id, ExecutionEventType.LEG2_FILLED,
                               {"price": leg2_actual, "size": size})

            status = TradeJournalStatus.SUCCESS
            actual_leg1_fee = leg1_actual * size * 0.07
            actual_leg2_fee = leg2_actual * size * 0.07

            entry = journal.complete_trade(
                spread_id=spread_id,
                status=status,
                leg1_actual_price=leg1_actual,
                leg1_actual_size=size,
                leg2_actual_price=leg2_actual,
                leg2_actual_size=size,
                actual_leg1_fee=actual_leg1_fee,
                actual_leg2_fee=actual_leg2_fee,
            )

        elif outcome < 0.85:
            # Partial fill
            fill_ratio = random.uniform(0.3, 0.8)
            filled_size = int(size * fill_ratio)

            leg1_actual = leg1_ask + random.uniform(0, 0.02)
            leg2_actual = leg2_bid - random.uniform(0, 0.02)

            journal.record_event(spread_id, ExecutionEventType.LEG1_FILLED,
                               {"price": leg1_actual, "size": filled_size})
            journal.record_event(spread_id, ExecutionEventType.LEG2_SUBMITTED, {})
            journal.record_event(spread_id, ExecutionEventType.LEG2_PARTIAL,
                               {"price": leg2_actual, "size": filled_size})

            status = TradeJournalStatus.PARTIAL
            actual_leg1_fee = leg1_actual * filled_size * 0.07
            actual_leg2_fee = leg2_actual * filled_size * 0.07

            entry = journal.complete_trade(
                spread_id=spread_id,
                status=status,
                leg1_actual_price=leg1_actual,
                leg1_actual_size=filled_size,
                leg2_actual_price=leg2_actual,
                leg2_actual_size=filled_size,
                actual_leg1_fee=actual_leg1_fee,
                actual_leg2_fee=actual_leg2_fee,
            )

        elif outcome < 0.95:
            # Rollback - leg2 failed
            leg1_actual = leg1_ask + random.uniform(0, 0.02)
            rollback_price = leg1_actual - random.uniform(0.01, 0.03)  # Sell at loss

            journal.record_event(spread_id, ExecutionEventType.LEG1_FILLED,
                               {"price": leg1_actual, "size": size})
            journal.record_event(spread_id, ExecutionEventType.LEG2_SUBMITTED, {})
            journal.record_event(spread_id, ExecutionEventType.LEG2_TIMEOUT, {})
            journal.record_event(spread_id, ExecutionEventType.ROLLBACK_STARTED, {})
            journal.record_event(spread_id, ExecutionEventType.ROLLBACK_COMPLETED,
                               {"price": rollback_price})

            status = TradeJournalStatus.ROLLED_BACK
            rollback_loss = (leg1_actual - rollback_price) * size
            actual_leg1_fee = leg1_actual * size * 0.07

            entry = journal.complete_trade(
                spread_id=spread_id,
                status=status,
                leg1_actual_price=leg1_actual,
                leg1_actual_size=size,
                leg2_actual_price=None,
                leg2_actual_size=0,
                actual_leg1_fee=actual_leg1_fee,
                actual_leg2_fee=0,
                rollback_loss=rollback_loss,
            )

        else:
            # Complete failure
            journal.record_event(spread_id, ExecutionEventType.LEG1_TIMEOUT, {})
            journal.record_event(spread_id, ExecutionEventType.ERROR,
                               {"message": "Order rejected"})

            status = TradeJournalStatus.FAILED

            entry = journal.complete_trade(
                spread_id=spread_id,
                status=status,
                leg1_actual_price=None,
                leg1_actual_size=0,
                leg2_actual_price=None,
                leg2_actual_size=0,
                actual_leg1_fee=0,
                actual_leg2_fee=0,
                error_message="Order rejected by exchange",
            )

        # Update live display
        if display:
            pnl = entry.pnl_breakdown.actual_net_profit or 0.0
            display.update_trade_complete(status, pnl, entry)

    # Save journal
    journal_path = journal.save_all()
    logger.info(f"Journal saved to {journal_path}")

    # Analyze results
    analyzer = SessionAnalyzer(journal)
    analysis = analyzer.analyze()

    # Show final summary
    if display:
        display.show_final_summary(analysis)
        display.stop()

    # Generate reports
    reporter = ReportGenerator(journal, analyzer)
    report_dir = session_dir / "reports"
    paths = reporter.generate_all_reports(analysis, report_dir)

    print("\n" + "=" * 60)
    print("DEMO SESSION COMPLETE")
    print("=" * 60)
    print(f"Session ID:    {session_id}")
    print(f"Total Trades:  {analysis.total_trades}")
    print(f"  Successful:  {analysis.successful_trades}")
    print(f"  Partial:     {analysis.partial_trades}")
    print(f"  Rolled Back: {analysis.rolled_back_trades}")
    print(f"  Failed:      {analysis.failed_trades}")
    print()
    print(f"Win Rate:      {analysis.win_rate:.1%}")
    print(f"Total P&L:     ${analysis.total_pnl_usd:+.2f}")
    print(f"Profit Factor: {analysis.profit_factor:.2f}")
    print(f"Max Drawdown:  ${analysis.max_drawdown_usd:.2f}")
    print()

    if analysis.warnings:
        print(f"Warnings ({len(analysis.warnings)}):")
        for w in analysis.warnings[:3]:
            print(f"  [{w.level.value.upper()}] {w.message}")
        if len(analysis.warnings) > 3:
            print(f"  ... and {len(analysis.warnings) - 3} more")
        print()

    print("Reports saved to:")
    for report_type, path in paths.items():
        print(f"  {report_type}: {path}")
    print("=" * 60)

    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    """Analyze an existing journal."""
    from src.testing import TradeJournal, SessionAnalyzer, ReportGenerator

    journal_path = Path(args.journal)
    if not journal_path.exists():
        logger.error(f"Journal file not found: {journal_path}")
        return 1

    logger.info(f"Loading journal from {journal_path}")
    journal = TradeJournal.load_from_json(journal_path)
    logger.info(f"Loaded {len(journal.entries)} entries")

    analyzer = SessionAnalyzer(journal)
    analysis = analyzer.analyze()

    # Print analysis summary
    print("\n" + "=" * 60)
    print(f"SESSION ANALYSIS: {analysis.session_id}")
    print("=" * 60)
    print(f"Duration:      {analysis.duration_seconds:.1f}s")
    print(f"Total Trades:  {analysis.total_trades}")
    print(f"  Successful:  {analysis.successful_trades}")
    print(f"  Partial:     {analysis.partial_trades}")
    print(f"  Rolled Back: {analysis.rolled_back_trades}")
    print(f"  Failed:      {analysis.failed_trades}")
    print()
    print(f"Win Rate:      {analysis.win_rate:.1%}")
    print(f"Total P&L:     ${analysis.total_pnl_usd:+.2f}")
    print(f"Profit Factor: {analysis.profit_factor:.2f}")
    print(f"Max Drawdown:  ${analysis.max_drawdown_usd:.2f} ({analysis.max_drawdown_pct:.1%})")
    print()

    # Loss breakdown
    breakdown = analysis.loss_breakdown
    if breakdown.total_loss_usd > 0:
        print("LOSS BREAKDOWN:")
        print(f"  Slippage (Leg 1): ${breakdown.slippage_leg1_usd:.2f}")
        print(f"  Slippage (Leg 2): ${breakdown.slippage_leg2_usd:.2f}")
        print(f"  Fee Variance:     ${breakdown.fees_exceeded_usd:.2f}")
        print(f"  Partial Fills:    ${breakdown.partial_fill_usd:.2f}")
        print(f"  Rollback Costs:   ${breakdown.rollback_cost_usd:.2f}")
        print(f"  TOTAL:            ${breakdown.total_loss_usd:.2f}")
        print()

    # Warnings
    if analysis.warnings:
        print(f"WARNINGS ({len(analysis.warnings)}):")
        for w in analysis.warnings:
            print(f"  [{w.level.value.upper()}] {w.message}")
        print()

    print("=" * 60)

    # Show per-trade breakdown if verbose
    if args.verbose:
        print("\nPER-TRADE BREAKDOWN:")
        print("-" * 80)
        for trade in analyzer.get_loss_breakdown_by_trade():
            print(
                f"{trade['journal_id']}: {trade['status']:12} "
                f"expected=${trade['expected_pnl']:.4f} actual=${trade['actual_pnl'] or 0:.4f} "
                f"loss_cat={trade['primary_loss_category']}"
            )

    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generate reports from a journal."""
    from src.testing import TradeJournal, SessionAnalyzer, ReportGenerator

    journal_path = Path(args.journal)
    if not journal_path.exists():
        logger.error(f"Journal file not found: {journal_path}")
        return 1

    journal = TradeJournal.load_from_json(journal_path)
    analyzer = SessionAnalyzer(journal)
    analysis = analyzer.analyze()
    reporter = ReportGenerator(journal, analyzer)

    output_dir = Path(args.output) if args.output else journal_path.parent / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.format == "all" or args.format == "markdown":
        md_path = output_dir / f"report_{analysis.session_id}.md"
        reporter.generate_markdown_report(analysis, md_path)
        print(f"Markdown report: {md_path}")

    if args.format == "all" or args.format == "json":
        json_path = output_dir / f"report_{analysis.session_id}.json"
        reporter.generate_json_report(analysis, json_path)
        print(f"JSON report: {json_path}")

    if args.format == "all" or args.format == "summary":
        summary = reporter.generate_summary_table(analysis)
        summary += "\n\n"
        summary += reporter.generate_loss_table(analysis.loss_breakdown)

        if args.output:
            summary_path = output_dir / f"summary_{analysis.session_id}.txt"
            with open(summary_path, "w") as f:
                f.write(summary)
            print(f"Summary: {summary_path}")
        else:
            print(summary)

    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Inspect a specific trade from a journal."""
    from src.testing import TradeJournal

    journal_path = Path(args.journal)
    if not journal_path.exists():
        logger.error(f"Journal file not found: {journal_path}")
        return 1

    journal = TradeJournal.load_from_json(journal_path)

    if args.trade_id:
        entry = journal.get_entry(args.trade_id)
        if not entry:
            logger.error(f"Trade not found: {args.trade_id}")
            return 1
        entries = [entry]
    elif args.worst:
        losing = journal.get_losing_entries()
        if not losing:
            print("No losing trades found")
            return 0
        entries = [min(losing, key=lambda e: e.pnl_breakdown.actual_net_profit or 0)]
    elif args.best:
        profitable = journal.get_profitable_entries()
        if not profitable:
            print("No profitable trades found")
            return 0
        entries = [max(profitable, key=lambda e: e.pnl_breakdown.actual_net_profit or 0)]
    else:
        entries = journal.entries[:5]  # First 5 by default

    for entry in entries:
        print("\n" + "=" * 70)
        print(f"TRADE: {entry.journal_id}")
        print("=" * 70)
        print(f"Spread ID:  {entry.spread_id}")
        print(f"Status:     {entry.status.value}")
        print(f"Duration:   {entry.total_duration_ms}ms")
        print()

        # Input state
        print("INPUT STATE:")
        snap = entry.input_snapshot
        print(f"  Leg 1: {snap.leg1_quote.exchange}/{snap.leg1_quote.ticker}")
        print(f"         bid={snap.leg1_quote.bid:.4f} ask={snap.leg1_quote.ask:.4f} age={snap.leg1_quote.age_ms:.0f}ms")
        print(f"  Leg 2: {snap.leg2_quote.exchange}/{snap.leg2_quote.ticker}")
        print(f"         bid={snap.leg2_quote.bid:.4f} ask={snap.leg2_quote.ask:.4f} age={snap.leg2_quote.age_ms:.0f}ms")
        print(f"  Expected profit: ${snap.expected_net_spread:.4f}")
        print()

        # Decision
        print("DECISION:")
        dec = entry.decision_record
        print(f"  Rank: {dec.opportunity_rank}/{dec.total_opportunities}")
        print(f"  Edge: {dec.edge_cents:.2f} cents, ROI: {dec.roi_pct:.2%}")
        print(f"  Reason: {dec.decision_reason}")
        print()

        # P&L
        print("P&L BREAKDOWN:")
        pnl = entry.pnl_breakdown
        print(f"  Expected gross: ${pnl.expected_gross_profit:.4f}")
        print(f"  Expected net:   ${pnl.expected_net_profit:.4f}")
        print(f"  Actual gross:   ${pnl.actual_gross_profit:.4f}" if pnl.actual_gross_profit else "  Actual gross:   N/A")
        print(f"  Actual net:     ${pnl.actual_net_profit:.4f}" if pnl.actual_net_profit else "  Actual net:     N/A")
        print()
        print(f"  Leg 1: expected={pnl.leg1_expected_price:.4f} actual={pnl.leg1_actual_price or 'N/A'} slippage=${pnl.leg1_slippage_cost:.4f}")
        print(f"  Leg 2: expected={pnl.leg2_expected_price:.4f} actual={pnl.leg2_actual_price or 'N/A'} slippage=${pnl.leg2_slippage_cost:.4f}")
        print(f"  Fee variance:     ${pnl.fee_variance:.4f}")
        print(f"  Partial fill:     ${pnl.partial_fill_loss:.4f}")
        print(f"  Rollback loss:    ${pnl.rollback_loss:.4f}")
        print(f"  Primary category: {pnl.primary_loss_category.value}")
        print()

        # What-if
        print("WHAT-IF:")
        wif = entry.what_if_analysis
        print(f"  Optimal profit:       ${wif.optimal_profit:.4f}")
        print(f"  Maker fee savings:    ${wif.maker_fee_savings:.4f}")
        print(f"  Timing loss:          ${wif.timing_loss:.4f}")
        print(f"  Detection prices ok:  {wif.would_profit_at_detection_prices}")
        print()

        # Timeline
        if args.verbose:
            print("EXECUTION TIMELINE:")
            for event in entry.execution_events:
                print(f"  +{event.elapsed_ms:7.1f}ms  {event.event_type.value}")
                if event.details:
                    for k, v in event.details.items():
                        print(f"              {k}: {v}")

        if entry.error_message:
            print(f"\nERROR: {entry.error_message}")

    return 0


def _create_market_client(args: argparse.Namespace):
    """Create a real market data client based on args."""
    # TODO: Implement based on your actual client setup
    # This would load credentials and create the appropriate client
    logger.warning("Real market client not configured. Use --demo for testing.")
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Arbitrage Testing Framework CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run demo test with 10 trades
  python -m src.testing.cli run --demo --num-trades 10

  # Run with custom opportunities file
  python -m src.testing.cli run --opportunities data/opps.json --capital 5000

  # Analyze existing journal
  python -m src.testing.cli analyze --journal test_results/session/journal.json

  # Generate reports
  python -m src.testing.cli report --journal test_results/session/journal.json --format all

  # Inspect worst trade
  python -m src.testing.cli inspect --journal test_results/session/journal.json --worst -v
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run a test scenario")
    run_parser.add_argument("--opportunities", "-o", help="JSON file with opportunities")
    run_parser.add_argument("--output", help="Output directory")
    run_parser.add_argument("--session-id", help="Session identifier")
    run_parser.add_argument("--config", help="ArbitrageConfig YAML file")
    run_parser.add_argument("--capital", type=float, default=10000.0, help="Initial capital")
    run_parser.add_argument("--delay", type=int, default=0, help="Delay between trades (ms)")
    run_parser.add_argument("--quiet", "-q", action="store_true", help="Disable live display")
    run_parser.add_argument("--demo", action="store_true", help="Use demo mode with mock data")
    run_parser.add_argument("--num-trades", type=int, default=10, help="Number of demo trades")

    # Analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze a journal")
    analyze_parser.add_argument("--journal", "-j", required=True, help="Journal JSON file")
    analyze_parser.add_argument("--verbose", "-v", action="store_true", help="Show per-trade details")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate reports")
    report_parser.add_argument("--journal", "-j", required=True, help="Journal JSON file")
    report_parser.add_argument("--output", "-o", help="Output directory")
    report_parser.add_argument(
        "--format", "-f",
        choices=["markdown", "json", "summary", "all"],
        default="all",
        help="Report format",
    )

    # Inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect specific trades")
    inspect_parser.add_argument("--journal", "-j", required=True, help="Journal JSON file")
    inspect_parser.add_argument("--trade-id", "-t", help="Specific trade ID to inspect")
    inspect_parser.add_argument("--worst", action="store_true", help="Inspect worst trade")
    inspect_parser.add_argument("--best", action="store_true", help="Inspect best trade")
    inspect_parser.add_argument("--verbose", "-v", action="store_true", help="Show execution timeline")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "run": cmd_run,
        "analyze": cmd_analyze,
        "report": cmd_report,
        "inspect": cmd_inspect,
    }

    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
