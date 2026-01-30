"""Execution metrics collection and analysis.

Tracks execution performance including fill rates, edge capture,
execution times, and leg risk incidents.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from arb.spread_detector import Platform

from .base import ExecutionResult, ExecutionState


@dataclass
class ExecutionMetrics:
    """Aggregate metrics for execution performance.

    Attributes:
        total_attempts: Total number of execution attempts.
        successful_completions: Executions that completed successfully.
        leg_risk_incidents: Executions that resulted in leg risk.
        failed_executions: Executions that failed entirely.
        total_theoretical_edge: Sum of expected edge across all attempts.
        total_captured_edge: Sum of actually captured edge.
        total_contracts: Total contracts traded (successful only).
        execution_times_ms: List of execution times for completed trades.
    """
    total_attempts: int = 0
    successful_completions: int = 0
    leg_risk_incidents: int = 0
    failed_executions: int = 0
    total_theoretical_edge: float = 0.0
    total_captured_edge: float = 0.0
    total_contracts: int = 0
    execution_times_ms: List[float] = field(default_factory=list)

    @property
    def fill_rate(self) -> float:
        """Ratio of successful completions to total attempts."""
        if self.total_attempts == 0:
            return 0.0
        return self.successful_completions / self.total_attempts

    @property
    def edge_capture_rate(self) -> float:
        """Ratio of captured edge to theoretical edge."""
        if self.total_theoretical_edge <= 0:
            return 0.0
        return self.total_captured_edge / self.total_theoretical_edge

    @property
    def leg_risk_rate(self) -> float:
        """Ratio of leg risk incidents to total attempts."""
        if self.total_attempts == 0:
            return 0.0
        return self.leg_risk_incidents / self.total_attempts

    @property
    def failure_rate(self) -> float:
        """Ratio of failed executions to total attempts."""
        if self.total_attempts == 0:
            return 0.0
        return self.failed_executions / self.total_attempts

    @property
    def avg_execution_time_ms(self) -> float:
        """Average execution time in milliseconds."""
        if not self.execution_times_ms:
            return 0.0
        return sum(self.execution_times_ms) / len(self.execution_times_ms)

    @property
    def p50_execution_time_ms(self) -> float:
        """Median (p50) execution time in milliseconds."""
        if not self.execution_times_ms:
            return 0.0
        sorted_times = sorted(self.execution_times_ms)
        n = len(sorted_times)
        mid = n // 2
        if n % 2 == 0:
            return (sorted_times[mid - 1] + sorted_times[mid]) / 2
        return sorted_times[mid]

    @property
    def p95_execution_time_ms(self) -> float:
        """95th percentile execution time in milliseconds."""
        if not self.execution_times_ms:
            return 0.0
        sorted_times = sorted(self.execution_times_ms)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def avg_edge_per_contract(self) -> float:
        """Average captured edge per contract."""
        if self.total_contracts == 0:
            return 0.0
        return self.total_captured_edge / self.total_contracts

    def to_dict(self) -> dict:
        """Convert metrics to dictionary."""
        return {
            "total_attempts": self.total_attempts,
            "successful_completions": self.successful_completions,
            "leg_risk_incidents": self.leg_risk_incidents,
            "failed_executions": self.failed_executions,
            "fill_rate": self.fill_rate,
            "edge_capture_rate": self.edge_capture_rate,
            "leg_risk_rate": self.leg_risk_rate,
            "failure_rate": self.failure_rate,
            "total_theoretical_edge": self.total_theoretical_edge,
            "total_captured_edge": self.total_captured_edge,
            "total_contracts": self.total_contracts,
            "avg_execution_time_ms": self.avg_execution_time_ms,
            "p50_execution_time_ms": self.p50_execution_time_ms,
            "p95_execution_time_ms": self.p95_execution_time_ms,
            "avg_edge_per_contract": self.avg_edge_per_contract,
        }


@dataclass
class PlatformMetrics:
    """Metrics broken down by platform pair."""
    buy_platform: Platform
    sell_platform: Platform
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)


class MetricsCollector:
    """Collects and aggregates execution metrics.

    Tracks both aggregate metrics and per-platform breakdowns.
    """

    def __init__(self):
        """Initialize the metrics collector."""
        self._aggregate = ExecutionMetrics()
        self._by_platform_pair: Dict[tuple, PlatformMetrics] = {}
        self._by_algorithm: Dict[str, ExecutionMetrics] = {}
        self._results: List[ExecutionResult] = []
        self._start_time: Optional[datetime] = None

    @property
    def aggregate(self) -> ExecutionMetrics:
        """Get aggregate metrics across all executions."""
        return self._aggregate

    @property
    def results(self) -> List[ExecutionResult]:
        """Get all recorded execution results."""
        return self._results.copy()

    @property
    def uptime_seconds(self) -> float:
        """Seconds since collector started."""
        if self._start_time is None:
            return 0.0
        return (datetime.now() - self._start_time).total_seconds()

    def start(self) -> None:
        """Mark the start time for uptime tracking."""
        self._start_time = datetime.now()

    def record_execution(
        self,
        result: ExecutionResult,
        algorithm: Optional[str] = None,
    ) -> None:
        """Record an execution result.

        Args:
            result: The execution result to record.
            algorithm: Optional algorithm name for per-algorithm tracking.
        """
        self._results.append(result)

        # Update aggregate metrics
        self._update_metrics(self._aggregate, result)

        # Update platform-pair metrics
        platform_key = (
            result.opportunity.buy_platform,
            result.opportunity.sell_platform,
        )
        if platform_key not in self._by_platform_pair:
            self._by_platform_pair[platform_key] = PlatformMetrics(
                buy_platform=platform_key[0],
                sell_platform=platform_key[1],
            )
        self._update_metrics(
            self._by_platform_pair[platform_key].metrics, result
        )

        # Update algorithm metrics
        if algorithm:
            if algorithm not in self._by_algorithm:
                self._by_algorithm[algorithm] = ExecutionMetrics()
            self._update_metrics(self._by_algorithm[algorithm], result)

    def _update_metrics(
        self, metrics: ExecutionMetrics, result: ExecutionResult
    ) -> None:
        """Update a metrics object with an execution result."""
        metrics.total_attempts += 1
        metrics.total_theoretical_edge += (
            result.theoretical_edge * result.total_contracts_filled
        )

        if result.state == ExecutionState.COMPLETED:
            metrics.successful_completions += 1
            metrics.total_captured_edge += (
                result.captured_edge * result.total_contracts_filled
            )
            metrics.total_contracts += result.total_contracts_filled
            metrics.execution_times_ms.append(result.execution_time_ms)
        elif result.state == ExecutionState.LEG_RISK:
            metrics.leg_risk_incidents += 1
        else:
            metrics.failed_executions += 1

    def get_platform_metrics(
        self, buy_platform: Platform, sell_platform: Platform
    ) -> Optional[ExecutionMetrics]:
        """Get metrics for a specific platform pair."""
        key = (buy_platform, sell_platform)
        if key in self._by_platform_pair:
            return self._by_platform_pair[key].metrics
        return None

    def get_algorithm_metrics(self, algorithm: str) -> Optional[ExecutionMetrics]:
        """Get metrics for a specific algorithm."""
        return self._by_algorithm.get(algorithm)

    def get_all_platform_metrics(self) -> List[PlatformMetrics]:
        """Get metrics for all platform pairs."""
        return list(self._by_platform_pair.values())

    def get_all_algorithm_metrics(self) -> Dict[str, ExecutionMetrics]:
        """Get metrics for all algorithms."""
        return self._by_algorithm.copy()

    def get_recent_results(self, limit: int = 100) -> List[ExecutionResult]:
        """Get most recent execution results."""
        return self._results[-limit:]

    def get_summary(self) -> dict:
        """Get a summary of all metrics."""
        return {
            "aggregate": self._aggregate.to_dict(),
            "uptime_seconds": self.uptime_seconds,
            "total_results": len(self._results),
            "platform_pairs": len(self._by_platform_pair),
            "algorithms": list(self._by_algorithm.keys()),
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._aggregate = ExecutionMetrics()
        self._by_platform_pair.clear()
        self._by_algorithm.clear()
        self._results.clear()
        self._start_time = None
