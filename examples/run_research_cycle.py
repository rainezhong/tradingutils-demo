#!/usr/bin/env python3
"""
Example: Running a complete research cycle with the Research Orchestrator.

This script demonstrates how to programmatically run the research pipeline,
customize configuration, and process results.
"""

import logging
from pathlib import Path

from agents.research_orchestrator import (
    DataSourceConfig,
    FilterConfig,
    NotificationConfig,
    OrchestratorConfig,
    ResearchOrchestrator,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)


def main():
    """Run research cycle with custom configuration."""

    # Create configuration programmatically
    config = OrchestratorConfig()

    # Configure data sources
    config.data_sources = [
        DataSourceConfig(
            name="BTC Latency Probe",
            type="crypto",
            db_path="data/btc_latency_probe.db",
            enabled=True
        ),
        # Add more sources as needed
        # DataSourceConfig(
        #     name="NBA Recordings",
        #     type="nba",
        #     recording_path="data/recordings/nba_game_001.json",
        #     enabled=False
        # ),
    ]

    # Configure quality filters
    config.filters = FilterConfig(
        min_sharpe=0.5,        # Minimum Sharpe ratio
        max_pvalue=0.05,       # Maximum p-value (95% significance)
        min_trades=20,         # Minimum trade count
        min_return_pct=0.0,    # Minimum return percentage
    )

    # Configure notifications
    config.notifications = NotificationConfig(
        enabled=False,          # Disable notifications for now
        min_sharpe_notify=1.0,  # Only notify for Sharpe >= 1.0
    )

    # Configure orchestrator behavior
    config.enable_walk_forward = True   # Enable train/test split validation
    config.enable_sensitivity = True    # Enable parameter sensitivity analysis
    config.max_hypotheses_per_pattern = 2  # Generate 2 hypotheses per pattern
    config.max_patterns_per_source = 5     # Process top 5 patterns per source

    # Create orchestrator
    logger.info("Initializing Research Orchestrator...")
    orchestrator = ResearchOrchestrator(config)

    # Run research cycle
    logger.info("Starting research cycle...")
    summary = orchestrator.research_cycle()

    # Process results
    logger.info("\n" + "=" * 80)
    logger.info("RESEARCH CYCLE COMPLETE")
    logger.info("=" * 80)

    logger.info(f"Duration: {summary.duration_seconds:.1f}s")
    logger.info(f"Patterns found: {summary.patterns_found}")
    logger.info(f"Hypotheses generated: {summary.hypotheses_generated}")
    logger.info(f"Backtests run: {summary.backtests_run}")
    logger.info(f"Backtests passed: {summary.backtests_passed}")
    logger.info(f"Reports generated: {summary.reports_generated}")

    if summary.promising_hypotheses:
        logger.info(f"\nPROMISING STRATEGIES:")
        for name in summary.promising_hypotheses:
            logger.info(f"  - {name}")
    else:
        logger.info("\nNo promising strategies found this cycle.")

    if summary.errors:
        logger.info(f"\nERRORS ({len(summary.errors)}):")
        for error in summary.errors[:5]:
            logger.info(f"  - {error}")

    # Query research database for results
    logger.info("\n" + "=" * 80)
    logger.info("QUERYING RESEARCH DATABASE")
    logger.info("=" * 80)

    db = orchestrator.research_db

    # Get all validated hypotheses
    all_hypotheses = []
    cursor = db.conn.cursor()
    cursor.execute("SELECT * FROM hypotheses WHERE status = 'validated' ORDER BY created_at DESC LIMIT 10")
    for row in cursor.fetchall():
        all_hypotheses.append(db._row_to_hypothesis(row))

    if all_hypotheses:
        logger.info(f"\nFound {len(all_hypotheses)} validated hypotheses:")
        for hyp in all_hypotheses:
            logger.info(f"  [{hyp.id}] {hyp.name}")

            # Get backtest results
            results = db.get_backtest_results(hyp.id)
            if results:
                latest = results[0]
                logger.info(f"      Sharpe: {latest.sharpe:.2f}, Win Rate: {latest.win_rate:.1f}%, Trades: {latest.num_trades}")
    else:
        logger.info("No validated hypotheses in database yet.")

    # Get all reports
    cursor.execute("SELECT * FROM reports ORDER BY created_at DESC LIMIT 10")
    reports = []
    for row in cursor.fetchall():
        reports.append(db._row_to_report(row))

    if reports:
        logger.info(f"\nGenerated {len(reports)} reports:")
        for report in reports:
            logger.info(f"  [{report.id}] {Path(report.notebook_path).name}")
            logger.info(f"      Recommendation: {report.recommendation}")

    logger.info("\n" + "=" * 80)
    logger.info("Done!")
    logger.info("=" * 80)


if __name__ == "__main__":
    main()
