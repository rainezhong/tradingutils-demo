"""Pipeline orchestrator for end-to-end data collection and analysis.

This module provides a DataPipeline class that orchestrates the full workflow:
1. Scan for new markets
2. Log current snapshots
3. Analyze and rank markets
4. Generate report
5. Check system health
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.core import Config, MarketDatabase, get_config, setup_logger, utc_now_iso
from src.collectors import Scanner, Logger
from src.analysis import MarketRanker
from src.automation import HealthCheck, HealthStatus

logger = setup_logger(__name__)


@dataclass
class PipelineResult:
    """Result of a pipeline stage."""
    stage: str
    success: bool
    count: Optional[int] = None
    error: Optional[str] = None
    data: Any = None
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "success": self.success,
            "count": self.count,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class DataPipeline:
    """
    Orchestrates the full data collection and analysis pipeline.

    Stages:
    1. scan - Discover new markets from API
    2. log - Capture current market snapshots
    3. analyze - Calculate metrics and rank markets
    4. report - Generate summary report
    5. health - Verify system health
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        scanner: Optional[Scanner] = None,
        data_logger: Optional[Logger] = None,
        ranker: Optional[MarketRanker] = None,
    ):
        """
        Initialize the pipeline.

        Args:
            config: Configuration instance
            scanner: Scanner instance
            data_logger: Logger instance
            ranker: MarketRanker instance
        """
        self.config = config or get_config()
        self.scanner = scanner or Scanner(self.config)
        self.data_logger = data_logger or Logger(self.config)
        self.ranker = ranker or MarketRanker()
        self.health_checker = HealthCheck(self.config)

        self._results: dict[str, PipelineResult] = {}

    def run_full_pipeline(
        self,
        skip_on_error: bool = True,
        min_score: float = 12.0,
        analysis_days: int = 3,
    ) -> dict[str, dict]:
        """
        Run the complete data pipeline.

        Args:
            skip_on_error: Continue to next stage on non-critical failures
            min_score: Minimum score for analysis ranking
            analysis_days: Days of data to analyze

        Returns:
            Dictionary of stage results
        """
        logger.info("Starting full pipeline run")
        print("Starting data pipeline...\n")

        # Stage 1: Scan markets
        scan_result = self._run_scan_stage()
        self._results["scan"] = scan_result
        if not scan_result.success and not skip_on_error:
            logger.error("Pipeline aborted at scan stage")
            return self._get_results_dict()

        # Stage 2: Log snapshots
        log_result = self._run_log_stage()
        self._results["log"] = log_result
        if not log_result.success and not skip_on_error:
            logger.error("Pipeline aborted at log stage")
            return self._get_results_dict()

        # Stage 3: Analyze markets
        analyze_result = self._run_analyze_stage(
            min_score=min_score,
            days=analysis_days,
        )
        self._results["analyze"] = analyze_result
        if not analyze_result.success and not skip_on_error:
            logger.error("Pipeline aborted at analyze stage")
            return self._get_results_dict()

        # Stage 4: Generate report
        report_result = self._run_report_stage(
            min_score=min_score,
            days=analysis_days,
        )
        self._results["report"] = report_result

        # Stage 5: Health check
        health_result = self._run_health_stage()
        self._results["health"] = health_result

        logger.info("Pipeline completed")
        return self._get_results_dict()

    def _run_scan_stage(self) -> PipelineResult:
        """Run the market scanning stage."""
        print("[1/5] Scanning for markets...")
        logger.info("Running scan stage")

        try:
            count = self.scanner.scan_and_save()
            print(f"      Found {count} markets")
            return PipelineResult(
                stage="scan",
                success=True,
                count=count,
            )
        except Exception as e:
            logger.error(f"Scan stage failed: {e}")
            print(f"      FAILED: {e}")
            return PipelineResult(
                stage="scan",
                success=False,
                error=str(e),
            )

    def _run_log_stage(self) -> PipelineResult:
        """Run the snapshot logging stage."""
        print("[2/5] Logging market snapshots...")
        logger.info("Running log stage")

        try:
            count = self.data_logger.log_snapshots(show_progress=False)
            print(f"      Logged {count} snapshots")
            return PipelineResult(
                stage="log",
                success=True,
                count=count,
            )
        except Exception as e:
            logger.error(f"Log stage failed: {e}")
            print(f"      FAILED: {e}")
            return PipelineResult(
                stage="log",
                success=False,
                error=str(e),
            )

    def _run_analyze_stage(
        self,
        min_score: float,
        days: int,
    ) -> PipelineResult:
        """Run the market analysis stage."""
        print("[3/5] Analyzing markets...")
        logger.info("Running analyze stage")

        try:
            top_markets = self.ranker.get_top_markets(
                n=20,
                min_score=min_score,
                days=days,
            )
            count = len(top_markets)
            print(f"      Found {count} markets with score >= {min_score}")
            return PipelineResult(
                stage="analyze",
                success=True,
                count=count,
                data=top_markets,
            )
        except Exception as e:
            logger.error(f"Analyze stage failed: {e}")
            print(f"      FAILED: {e}")
            return PipelineResult(
                stage="analyze",
                success=False,
                error=str(e),
            )

    def _run_report_stage(
        self,
        min_score: float,
        days: int,
    ) -> PipelineResult:
        """Generate and save a report."""
        print("[4/5] Generating report...")
        logger.info("Running report stage")

        try:
            # Create reports directory if needed
            reports_dir = Path("reports")
            reports_dir.mkdir(exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = reports_dir / f"market_report_{timestamp}.csv"

            # Export rankings
            self.ranker.export_to_csv(
                filename=str(filename),
                days=days,
                min_score=min_score,
            )

            print(f"      Saved report to {filename}")
            return PipelineResult(
                stage="report",
                success=True,
                data=str(filename),
            )
        except Exception as e:
            logger.error(f"Report stage failed: {e}")
            print(f"      FAILED: {e}")
            return PipelineResult(
                stage="report",
                success=False,
                error=str(e),
            )

    def _run_health_stage(self) -> PipelineResult:
        """Run system health checks."""
        print("[5/5] Checking system health...")
        logger.info("Running health stage")

        try:
            status = self.health_checker.run_all_checks()
            if status.healthy:
                print("      System is healthy")
            else:
                print(f"      WARNING: {len(status.issues)} issue(s) found")
                for issue in status.issues:
                    print(f"        - {issue}")

            return PipelineResult(
                stage="health",
                success=status.healthy,
                data=status,
                error="; ".join(status.issues) if not status.healthy else None,
            )
        except Exception as e:
            logger.error(f"Health stage failed: {e}")
            print(f"      FAILED: {e}")
            return PipelineResult(
                stage="health",
                success=False,
                error=str(e),
            )

    def _get_results_dict(self) -> dict[str, dict]:
        """Convert results to dictionary format."""
        return {
            stage: result.to_dict()
            for stage, result in self._results.items()
        }

    def get_stage_result(self, stage: str) -> Optional[PipelineResult]:
        """Get result for a specific stage."""
        return self._results.get(stage)

    def run_stage(self, stage: str, **kwargs) -> PipelineResult:
        """
        Run a single pipeline stage.

        Args:
            stage: Stage name (scan, log, analyze, report, health)
            **kwargs: Stage-specific arguments

        Returns:
            PipelineResult for the stage
        """
        stages = {
            "scan": self._run_scan_stage,
            "log": self._run_log_stage,
            "analyze": lambda: self._run_analyze_stage(
                min_score=kwargs.get("min_score", 12.0),
                days=kwargs.get("days", 3),
            ),
            "report": lambda: self._run_report_stage(
                min_score=kwargs.get("min_score", 12.0),
                days=kwargs.get("days", 3),
            ),
            "health": self._run_health_stage,
        }

        if stage not in stages:
            raise ValueError(f"Unknown stage: {stage}. Valid stages: {list(stages.keys())}")

        result = stages[stage]()
        self._results[stage] = result
        return result

    def close(self) -> None:
        """Clean up resources."""
        self.scanner.close()
        self.data_logger.close()
        logger.debug("Pipeline resources cleaned up")


def main():
    """CLI entry point for the pipeline."""
    import argparse

    parser = argparse.ArgumentParser(description="Run data pipeline")
    parser.add_argument("--config", type=str, default=None, help="Path to config file")
    parser.add_argument("--skip-errors", action="store_true", help="Continue on failures")
    parser.add_argument("--min-score", type=float, default=12.0, help="Minimum market score")
    parser.add_argument("--days", type=int, default=3, help="Days of data to analyze")
    parser.add_argument(
        "--stage",
        type=str,
        choices=["scan", "log", "analyze", "report", "health"],
        default=None,
        help="Run a single stage only",
    )
    args = parser.parse_args()

    if args.config:
        from src.core import set_config
        config = Config.from_yaml(args.config)
        set_config(config)

    pipeline = DataPipeline()

    try:
        if args.stage:
            print(f"Running single stage: {args.stage}\n")
            result = pipeline.run_stage(
                args.stage,
                min_score=args.min_score,
                days=args.days,
            )
            print(f"\nResult: {'SUCCESS' if result.success else 'FAILED'}")
            if result.error:
                print(f"Error: {result.error}")
        else:
            results = pipeline.run_full_pipeline(
                skip_on_error=args.skip_errors,
                min_score=args.min_score,
                analysis_days=args.days,
            )

            # Summary
            print("\n" + "=" * 50)
            print("Pipeline Summary")
            print("=" * 50)
            all_success = True
            for stage, result in results.items():
                status = "OK" if result["success"] else "FAILED"
                if not result["success"]:
                    all_success = False
                print(f"  {stage:<10}: {status}")

            print("=" * 50)
            print(f"Overall: {'SUCCESS' if all_success else 'FAILED'}")

            return 0 if all_success else 1
    finally:
        pipeline.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
