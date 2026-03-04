"""Research Orchestrator - Coordinates Autonomous Research Pipeline

This orchestrator coordinates all research agents to run a complete research cycle:
1. Data Scout scans databases for patterns
2. Hypothesis Generator creates trading hypotheses from patterns
3. Backtest Runner validates hypotheses with statistical testing
4. Report Generator creates comprehensive Jupyter notebooks
5. Research DB tracks all hypotheses, backtests, and reports

The orchestrator filters results by quality thresholds and sends notifications
for promising strategies.
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from agents.backtest_runner import (
    BacktestResults,
    BacktestRunnerAgent,
)
from agents.data_scout import DataScoutAgent, Hypothesis as PatternHypothesis
from agents.hypothesis_generator import HypothesisGeneratorAgent, TradingHypothesis
from agents.report_generator import HypothesisInfo, ReportGeneratorAgent
from research.research_db import BacktestResult as DBBacktestResult
from research.research_db import Hypothesis as DBHypothesis
from research.research_db import ResearchDB

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class FilterConfig:
    """Quality filters for hypotheses."""

    min_sharpe: float = 0.5
    max_pvalue: float = 0.05
    min_trades: int = 20
    min_return_pct: float = 0.0


@dataclass
class DataSourceConfig:
    """Configuration for a data source to scan."""

    name: str
    type: str  # "nba" or "crypto"
    db_path: Optional[str] = None
    recording_path: Optional[str] = None
    enabled: bool = True


@dataclass
class NotificationConfig:
    """Notification settings."""

    enabled: bool = False
    email: Optional[str] = None
    slack_webhook: Optional[str] = None
    min_sharpe_notify: float = 1.0


@dataclass
class OrchestratorConfig:
    """Complete orchestrator configuration."""

    data_sources: List[DataSourceConfig] = field(default_factory=list)
    filters: FilterConfig = field(default_factory=FilterConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    research_db_path: str = "data/research.db"
    reports_dir: str = "research/reports"
    enable_walk_forward: bool = True
    enable_sensitivity: bool = True
    max_hypotheses_per_pattern: int = 2
    max_patterns_per_source: int = 5

    @classmethod
    def from_yaml(cls, path: str) -> "OrchestratorConfig":
        """Load configuration from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)

        # Parse data sources
        sources = [
            DataSourceConfig(**src) for src in data.get("data_sources", [])
        ]

        # Parse filters
        filters = FilterConfig(**data.get("filters", {}))

        # Parse notifications
        notifications = NotificationConfig(**data.get("notifications", {}))

        return cls(
            data_sources=sources,
            filters=filters,
            notifications=notifications,
            research_db_path=data.get("research_db_path", "data/research.db"),
            reports_dir=data.get("reports_dir", "research/reports"),
            enable_walk_forward=data.get("enable_walk_forward", True),
            enable_sensitivity=data.get("enable_sensitivity", True),
            max_hypotheses_per_pattern=data.get("max_hypotheses_per_pattern", 2),
            max_patterns_per_source=data.get("max_patterns_per_source", 5),
        )


# ---------------------------------------------------------------------------
# Research Orchestrator
# ---------------------------------------------------------------------------


@dataclass
class ResearchCycleSummary:
    """Summary of a complete research cycle."""

    started_at: datetime
    completed_at: datetime
    duration_seconds: float

    patterns_found: int
    hypotheses_generated: int
    backtests_run: int
    backtests_passed: int
    reports_generated: int

    promising_hypotheses: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ResearchOrchestrator:
    """Coordinates the entire research pipeline.

    This orchestrator runs the complete autonomous research cycle:
    1. Scout for statistical patterns in trading data
    2. Generate hypotheses from detected patterns
    3. Run backtests with full validation (walk-forward, sensitivity)
    4. Generate reports for promising strategies
    5. Track everything in the research database
    6. Send notifications for exceptional findings

    Usage:
        config = OrchestratorConfig.from_yaml("config/research.yaml")
        orchestrator = ResearchOrchestrator(config)
        summary = await orchestrator.research_cycle()
    """

    def __init__(self, config: OrchestratorConfig):
        """Initialize the research orchestrator.

        Args:
            config: Orchestrator configuration
        """
        self.config = config

        # Initialize all agents
        self.research_db = ResearchDB(config.research_db_path)
        self.hypothesis_gen = HypothesisGeneratorAgent()
        self.backtest_runner = BacktestRunnerAgent(
            enable_walk_forward=config.enable_walk_forward,
            enable_sensitivity=config.enable_sensitivity,
        )
        self.report_generator = ReportGeneratorAgent(
            reports_dir=Path(config.reports_dir),
            min_sharpe_deploy=1.0,
            min_sharpe_paper=0.5,
        )

        logger.info("Research orchestrator initialized")

    def research_cycle(self) -> ResearchCycleSummary:
        """Run one complete research cycle.

        Returns:
            Summary of research cycle results
        """
        started_at = datetime.now()
        logger.info("=" * 80)
        logger.info("STARTING RESEARCH CYCLE")
        logger.info("=" * 80)

        summary = ResearchCycleSummary(
            started_at=started_at,
            completed_at=started_at,  # Updated at end
            duration_seconds=0.0,
            patterns_found=0,
            hypotheses_generated=0,
            backtests_run=0,
            backtests_passed=0,
            reports_generated=0,
        )

        # Process each data source
        for source in self.config.data_sources:
            if not source.enabled:
                logger.info(f"Skipping disabled source: {source.name}")
                continue

            logger.info(f"\n{'=' * 80}")
            logger.info(f"Processing data source: {source.name}")
            logger.info(f"{'=' * 80}")

            try:
                # Step 1: Scout for patterns
                patterns = self._scout_patterns(source)
                summary.patterns_found += len(patterns)

                # Limit patterns per source
                patterns = patterns[:self.config.max_patterns_per_source]

                logger.info(f"Found {len(patterns)} patterns in {source.name}")

                # Step 2-5: Process each pattern
                for i, pattern in enumerate(patterns, 1):
                    logger.info(f"\n--- Pattern {i}/{len(patterns)}: {pattern.ticker} ---")

                    try:
                        result = self._process_pattern(pattern, source)

                        summary.hypotheses_generated += result["hypotheses_generated"]
                        summary.backtests_run += result["backtests_run"]
                        summary.backtests_passed += result["backtests_passed"]
                        summary.reports_generated += result["reports_generated"]
                        summary.promising_hypotheses.extend(result["promising"])

                    except Exception as e:
                        error_msg = f"Error processing pattern {pattern.ticker}: {e}"
                        logger.error(error_msg)
                        summary.errors.append(error_msg)
                        continue

            except Exception as e:
                error_msg = f"Error processing source {source.name}: {e}"
                logger.error(error_msg)
                summary.errors.append(error_msg)
                continue

        # Finalize summary
        completed_at = datetime.now()
        summary.completed_at = completed_at
        summary.duration_seconds = (completed_at - started_at).total_seconds()

        # Print summary
        self._print_summary(summary)

        return summary

    def _scout_patterns(self, source: DataSourceConfig) -> List[PatternHypothesis]:
        """Scout for patterns in a data source.

        Args:
            source: Data source configuration

        Returns:
            List of detected patterns
        """
        if source.type == "crypto":
            db_path = source.db_path or "data/btc_latency_probe.db"
        else:
            # NBA uses recordings, not DB
            return []

        with DataScoutAgent(db_path) as scout:
            patterns = scout.scan_for_patterns(min_snapshots=100)

        # Sort by confidence and return top patterns
        patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns

    def _process_pattern(
        self,
        pattern: PatternHypothesis,
        source: DataSourceConfig
    ) -> Dict[str, Any]:
        """Process a single pattern through the complete pipeline.

        Args:
            pattern: Detected pattern
            source: Data source configuration

        Returns:
            Dictionary with processing results
        """
        result = {
            "hypotheses_generated": 0,
            "backtests_run": 0,
            "backtests_passed": 0,
            "reports_generated": 0,
            "promising": [],
        }

        # Step 2: Generate hypotheses from pattern
        pattern_data = {
            "market_type": source.type,
            "observation": pattern.description,
            "statistics": pattern.metadata,
            "timeframe": pattern.metadata.get("timestamp", ""),
        }

        hypotheses = self.hypothesis_gen.generate_from_pattern(
            pattern_data,
            num_hypotheses=self.config.max_hypotheses_per_pattern
        )

        result["hypotheses_generated"] = len(hypotheses)
        logger.info(f"Generated {len(hypotheses)} hypotheses")

        # Step 3-5: Process each hypothesis
        for hypothesis in hypotheses:
            try:
                processed = self._process_hypothesis(hypothesis, source, pattern)

                result["backtests_run"] += 1
                if processed["passed"]:
                    result["backtests_passed"] += 1
                if processed["report_generated"]:
                    result["reports_generated"] += 1
                if processed["is_promising"]:
                    result["promising"].append(hypothesis.name)

            except Exception as e:
                logger.error(f"Error processing hypothesis {hypothesis.name}: {e}")
                continue

        return result

    def _process_hypothesis(
        self,
        hypothesis: TradingHypothesis,
        source: DataSourceConfig,
        pattern: PatternHypothesis
    ) -> Dict[str, Any]:
        """Process a single hypothesis: backtest, validate, report.

        Args:
            hypothesis: Trading hypothesis to test
            source: Data source configuration
            pattern: Original pattern that generated this hypothesis

        Returns:
            Dictionary with processing results
        """
        result = {
            "passed": False,
            "report_generated": False,
            "is_promising": False,
        }

        logger.info(f"Testing hypothesis: {hypothesis.name}")

        # Save hypothesis to research DB
        db_hypothesis = DBHypothesis(
            id=None,
            name=hypothesis.name,
            description=hypothesis.description,
            source="research_orchestrator",
            created_at=datetime.now(),
            status="backtesting",
            metadata={
                "market_type": hypothesis.market_type,
                "theoretical_basis": hypothesis.theoretical_basis,
                "confidence": hypothesis.confidence,
                "novelty_score": hypothesis.novelty_score,
                "pattern_type": pattern.pattern_type,
                "pattern_ticker": pattern.ticker,
            }
        )

        hyp_id = self.research_db.save_hypothesis(db_hypothesis)

        # Step 3: Run backtest
        try:
            backtest_results = self._run_backtest(hypothesis, source)
        except Exception as e:
            logger.error(f"Backtest failed: {e}")
            # Update status to rejected
            db_hypothesis.id = hyp_id
            db_hypothesis.status = "rejected"
            self.research_db.save_hypothesis(db_hypothesis)
            return result

        # Step 4: Check if results pass filters
        passed = self._check_filters(backtest_results)
        result["passed"] = passed

        if not passed:
            logger.info(f"Hypothesis failed filters: {hypothesis.name}")
            db_hypothesis.id = hyp_id
            db_hypothesis.status = "rejected"
            self.research_db.save_hypothesis(db_hypothesis)
            return result

        logger.info(f"Hypothesis passed filters: {hypothesis.name}")

        # Save backtest results to research DB
        db_backtest = DBBacktestResult(
            id=None,
            hypothesis_id=hyp_id,
            sharpe=backtest_results.validation.sharpe_ratio,
            max_drawdown=backtest_results.validation.max_drawdown_pct,
            win_rate=backtest_results.validation.win_rate_pct,
            p_value=backtest_results.validation.p_value,
            num_trades=backtest_results.validation.total_trades,
            config={
                "strategy_type": backtest_results.strategy_type,
                "data_source": backtest_results.data_source,
            },
            created_at=datetime.now(),
        )

        backtest_id = self.research_db.save_backtest_results(hyp_id, db_backtest)

        # Update hypothesis status
        db_hypothesis.id = hyp_id
        db_hypothesis.status = "validated"
        self.research_db.save_hypothesis(db_hypothesis)

        # Step 5: Generate report
        try:
            report_path = self._generate_report(hypothesis, backtest_results)
            result["report_generated"] = True

            # Determine recommendation
            recommendation = self._get_recommendation(backtest_results)

            # Save report to research DB
            self.research_db.save_report(
                hyp_id,
                report_path,
                recommendation=recommendation,
                backtest_id=backtest_id
            )

            logger.info(f"Generated report: {report_path}")

        except Exception as e:
            logger.error(f"Report generation failed: {e}")

        # Check if promising (notify-worthy)
        is_promising = self._is_promising(backtest_results)
        result["is_promising"] = is_promising

        if is_promising:
            logger.info(f"*** PROMISING STRATEGY FOUND: {hypothesis.name} ***")
            self._send_notification(hypothesis, backtest_results)

        return result

    def _run_backtest(
        self,
        hypothesis: TradingHypothesis,
        source: DataSourceConfig
    ) -> BacktestResults:
        """Run backtest for a hypothesis.

        Args:
            hypothesis: Hypothesis to test
            source: Data source configuration

        Returns:
            Backtest results
        """
        # Map hypothesis to adapter config
        adapter_config = self._hypothesis_to_adapter_config(hypothesis)

        # Map source to data config
        data_config = self._source_to_data_config(source)

        # Run backtest
        results = self.backtest_runner.test_hypothesis(
            hypothesis=hypothesis.description,
            adapter_config=adapter_config,
            data_config=data_config,
        )

        return results

    def _hypothesis_to_adapter_config(
        self,
        hypothesis: TradingHypothesis
    ) -> Dict[str, Any]:
        """Convert hypothesis to adapter configuration.

        Args:
            hypothesis: Trading hypothesis

        Returns:
            Adapter configuration dict
        """
        # Map market type to adapter type
        market_type = hypothesis.market_type.lower()

        if market_type == "crypto":
            return {
                "type": "crypto-latency",
                "params": {
                    "vol": 0.30,
                    "min_edge": 0.10,
                    "slippage_cents": 3,
                    "min_ttx_sec": 120,
                    "max_ttx_sec": 900,
                }
            }
        elif market_type == "nba":
            return {
                "type": "nba-mispricing",
                "params": {
                    "min_edge_cents": 3.0,
                    "max_period": 2,
                    "position_size": 10,
                }
            }
        else:
            # Default to generic adapter
            return {
                "type": "nba-mispricing",  # Fallback
                "params": {}
            }

    def _source_to_data_config(self, source: DataSourceConfig) -> Dict[str, Any]:
        """Convert source config to data feed configuration.

        Args:
            source: Data source configuration

        Returns:
            Data config dict
        """
        if source.type == "crypto":
            return {
                "type": "crypto",
                "path": source.db_path or "data/btc_latency_probe.db",
                "use_spot_price": True,
            }
        elif source.type == "nba":
            return {
                "type": "nba",
                "path": source.recording_path or "data/recordings/nba_game_001.json",
            }
        else:
            raise ValueError(f"Unknown source type: {source.type}")

    def _check_filters(self, results: BacktestResults) -> bool:
        """Check if backtest results pass quality filters.

        Args:
            results: Backtest results

        Returns:
            True if passed all filters
        """
        filters = self.config.filters
        validation = results.validation

        # Sharpe ratio filter
        if validation.sharpe_ratio < filters.min_sharpe:
            logger.debug(f"Failed Sharpe filter: {validation.sharpe_ratio:.2f} < {filters.min_sharpe}")
            return False

        # P-value filter
        if validation.p_value > filters.max_pvalue:
            logger.debug(f"Failed p-value filter: {validation.p_value:.4f} > {filters.max_pvalue}")
            return False

        # Trade count filter
        if validation.total_trades < filters.min_trades:
            logger.debug(f"Failed trade count filter: {validation.total_trades} < {filters.min_trades}")
            return False

        # Return filter
        if results.backtest_result.metrics.return_pct < filters.min_return_pct:
            logger.debug(f"Failed return filter: {results.backtest_result.metrics.return_pct:.1f}% < {filters.min_return_pct}")
            return False

        return True

    def _is_promising(self, results: BacktestResults) -> bool:
        """Check if results are promising enough for notification.

        Args:
            results: Backtest results

        Returns:
            True if promising
        """
        if not self.config.notifications.enabled:
            return False

        min_sharpe = self.config.notifications.min_sharpe_notify
        return results.validation.sharpe_ratio >= min_sharpe

    def _generate_report(
        self,
        hypothesis: TradingHypothesis,
        results: BacktestResults
    ) -> str:
        """Generate research report for hypothesis.

        Args:
            hypothesis: Trading hypothesis
            results: Backtest results

        Returns:
            Path to generated report
        """
        hypothesis_info = HypothesisInfo(
            name=hypothesis.name,
            description=hypothesis.description,
            market_type=hypothesis.market_type,
            strategy_family=results.strategy_type,
            parameters={},  # Would extract from adapter config
            data_source=results.data_source,
            time_period=None,
        )

        report_path = self.report_generator.generate(
            hypothesis_info,
            results.backtest_result
        )

        return report_path

    def _get_recommendation(self, results: BacktestResults) -> str:
        """Get deployment recommendation from results.

        Args:
            results: Backtest results

        Returns:
            Recommendation string: "deploy", "paper", or "reject"
        """
        sharpe = results.validation.sharpe_ratio
        trades = results.validation.total_trades
        pvalue = results.validation.p_value

        # Must be profitable and significant
        if results.backtest_result.metrics.net_pnl <= 0 or pvalue > 0.05:
            return "reject"

        # Deploy tier
        if sharpe >= 1.0 and trades >= 50:
            return "deploy"

        # Paper tier
        if sharpe >= 0.5 and trades >= 20:
            return "paper"

        return "reject"

    def _send_notification(
        self,
        hypothesis: TradingHypothesis,
        results: BacktestResults
    ):
        """Send notification for promising finding.

        Args:
            hypothesis: Trading hypothesis
            results: Backtest results
        """
        if not self.config.notifications.enabled:
            return

        logger.info(f"Sending notification for: {hypothesis.name}")

        # Format notification message
        message = self._format_notification(hypothesis, results)

        # Send via configured channels
        if self.config.notifications.email:
            self._send_email(message)

        if self.config.notifications.slack_webhook:
            self._send_slack(message)

    def _format_notification(
        self,
        hypothesis: TradingHypothesis,
        results: BacktestResults
    ) -> str:
        """Format notification message.

        Args:
            hypothesis: Trading hypothesis
            results: Backtest results

        Returns:
            Formatted message
        """
        v = results.validation
        m = results.backtest_result.metrics

        return f"""
PROMISING STRATEGY DETECTED

Name: {hypothesis.name}
Market: {hypothesis.market_type}
Confidence: {hypothesis.confidence}

PERFORMANCE:
- Sharpe Ratio: {v.sharpe_ratio:.2f}
- Return: {m.return_pct:+.1f}%
- Win Rate: {v.win_rate_pct:.1f}%
- Max Drawdown: {v.max_drawdown_pct:.1f}%

VALIDATION:
- P-value: {v.p_value:.4f} {'***' if v.p_value < 0.001 else '**' if v.p_value < 0.01 else '*' if v.p_value < 0.05 else ''}
- Total Trades: {v.total_trades}
- Profit Factor: {v.profit_factor:.2f}

DESCRIPTION:
{hypothesis.description}

THEORETICAL BASIS:
{hypothesis.theoretical_basis}
"""

    def _send_email(self, message: str):
        """Send email notification (placeholder).

        Args:
            message: Message to send
        """
        # TODO: Implement email sending
        logger.info("Email notification: (not implemented)")
        logger.info(message)

    def _send_slack(self, message: str):
        """Send Slack notification (placeholder).

        Args:
            message: Message to send
        """
        # TODO: Implement Slack webhook
        logger.info("Slack notification: (not implemented)")
        logger.info(message)

    def _print_summary(self, summary: ResearchCycleSummary):
        """Print research cycle summary.

        Args:
            summary: Cycle summary
        """
        logger.info("\n" + "=" * 80)
        logger.info("RESEARCH CYCLE SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Duration: {summary.duration_seconds:.1f}s")
        logger.info(f"Patterns Found: {summary.patterns_found}")
        logger.info(f"Hypotheses Generated: {summary.hypotheses_generated}")
        logger.info(f"Backtests Run: {summary.backtests_run}")
        logger.info(f"Backtests Passed: {summary.backtests_passed}")
        logger.info(f"Reports Generated: {summary.reports_generated}")

        if summary.promising_hypotheses:
            logger.info(f"\nPROMISING STRATEGIES ({len(summary.promising_hypotheses)}):")
            for name in summary.promising_hypotheses:
                logger.info(f"  - {name}")

        if summary.errors:
            logger.info(f"\nERRORS ({len(summary.errors)}):")
            for error in summary.errors[:5]:  # Show first 5 errors
                logger.info(f"  - {error}")

        logger.info("=" * 80)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    """CLI entry point for research orchestrator."""
    parser = argparse.ArgumentParser(
        description="Research Orchestrator - Autonomous Trading Research Pipeline"
    )

    parser.add_argument(
        "--mode",
        choices=["manual", "daily", "weekly"],
        default="manual",
        help="Research mode (manual=one cycle, daily/weekly=future scheduling)"
    )

    parser.add_argument(
        "--config",
        default="config/research_orchestrator.yaml",
        help="Path to configuration file"
    )

    parser.add_argument(
        "--min-sharpe",
        type=float,
        help="Override minimum Sharpe ratio filter"
    )

    parser.add_argument(
        "--max-pvalue",
        type=float,
        help="Override maximum p-value filter"
    )

    parser.add_argument(
        "--min-trades",
        type=int,
        help="Override minimum trade count filter"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Load configuration
    config_path = Path(args.config)

    if config_path.exists():
        logger.info(f"Loading config from {config_path}")
        config = OrchestratorConfig.from_yaml(str(config_path))
    else:
        logger.warning(f"Config file not found: {config_path}, using defaults")
        config = OrchestratorConfig()

        # Add default crypto source
        config.data_sources.append(
            DataSourceConfig(
                name="BTC Latency Probe",
                type="crypto",
                db_path="data/btc_latency_probe.db",
                enabled=True
            )
        )

    # Apply CLI overrides
    if args.min_sharpe is not None:
        config.filters.min_sharpe = args.min_sharpe
    if args.max_pvalue is not None:
        config.filters.max_pvalue = args.max_pvalue
    if args.min_trades is not None:
        config.filters.min_trades = args.min_trades

    # Create orchestrator
    orchestrator = ResearchOrchestrator(config)

    # Run research cycle
    if args.mode == "manual":
        logger.info("Running manual research cycle...")
        summary = orchestrator.research_cycle()

        # Exit with success if any promising strategies found
        sys.exit(0 if summary.promising_hypotheses else 1)

    elif args.mode in ["daily", "weekly"]:
        logger.info(f"{args.mode.capitalize()} mode not yet implemented")
        logger.info("Run this script from cron for scheduled research cycles")
        sys.exit(0)


if __name__ == "__main__":
    main()
