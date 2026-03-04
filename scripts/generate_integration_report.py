#!/usr/bin/env python3
"""
Phase 2 Integration Test Report Generator

Analyzes the 8-hour integration test log and metrics to generate
a comprehensive validation report.

Validates:
1. Exit fill confirmation (Bug #1) - All exits must be confirmed via WebSocket
2. Orderbook WebSocket reliability (Bug #2) - Entry success rate >50%
3. OMS WebSocket initialization (Bug #3) - Real-time fill detection
4. Balance drift (Bugs #7, #8) - Maximum drift <10¢
5. Position reconciliation (Bug #9) - No stranded positions
6. Exit price accuracy (Bug #6) - P&L matches actual fills
7. Overall system stability - No critical errors

Generates HTML and Markdown reports with pass/fail status for each bug fix.

Usage:
    python3 scripts/generate_integration_report.py LOG_FILE [--metrics METRICS.json] [--output report.html]

Options:
    LOG_FILE              Path to integration test log file
    --metrics PATH        Path to metrics JSON file (optional, auto-detected)
    --output PATH         Output report path (default: auto-generated)
    --format {html,md}    Report format (default: html)
    --threshold-file PATH Override validation thresholds from YAML file
    --help                Show this help message
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class ValidationThresholds:
    """Validation thresholds for bug fixes."""

    # Bug #1: Exit fill confirmation
    min_exit_confirmation_rate: float = 1.0  # 100% - all exits must be confirmed

    # Bug #2: Orderbook WebSocket reliability
    min_entry_success_rate: float = 0.5  # 50% - was 20% (4/5) before fix

    # Bug #3: OMS WebSocket initialization
    min_oms_ws_uptime: float = 0.95  # 95% - must stay connected

    # Bug #6: Exit price accuracy
    max_pnl_discrepancy_cents: int = 2  # Max 2¢ difference between logged and actual

    # Bug #7: Entry fees
    min_fee_logging_rate: float = 0.95  # 95% of entries must log fees

    # Bug #8: Balance tracking
    max_balance_drift_cents: int = 10  # Max 10¢ drift over 8 hours

    # Bug #9: Position reconciliation
    max_stranded_positions: int = 0  # Zero stranded positions allowed

    # System stability
    max_critical_errors: int = 0  # No critical errors
    max_error_rate_per_hour: float = 5.0  # Max 5 errors per hour


@dataclass
class BugValidation:
    """Validation result for a single bug fix."""

    bug_number: int
    bug_title: str
    description: str
    passed: bool
    actual_value: str
    expected_value: str
    details: List[str]


@dataclass
class IntegrationReport:
    """Complete integration test report."""

    # Metadata
    test_start: datetime
    test_end: datetime
    duration_hours: float
    log_file: str

    # Bug validations
    validations: List[BugValidation]

    # Summary stats
    total_bugs: int
    bugs_passed: int
    bugs_failed: int
    overall_pass: bool

    # Detailed metrics
    entry_attempts: int
    entry_successes: int
    entry_success_rate: float

    exit_attempts: int
    exit_successes: int
    exit_success_rate: float

    balance_drift_max_cents: int
    balance_drift_avg_cents: float

    trades_completed: int
    win_rate: float

    error_count: int
    warning_count: int
    critical_count: int

    orderbook_ws_reconnections: int
    oms_ws_reconnections: int
    orderbook_rest_fallbacks: int

    stranded_positions_found: int


class ReportGenerator:
    """Generates integration test reports from log and metrics files."""

    def __init__(
        self,
        log_file: Path,
        metrics_file: Optional[Path] = None,
        thresholds: Optional[ValidationThresholds] = None,
    ):
        self.log_file = log_file
        self.metrics_file = metrics_file
        self.thresholds = thresholds or ValidationThresholds()

        # Auto-detect metrics file if not provided
        if not self.metrics_file:
            self.metrics_file = self._auto_detect_metrics()

        # Parse log and metrics
        self.log_data = self._parse_log()
        self.metrics_data = self._load_metrics() if self.metrics_file else None

    def _auto_detect_metrics(self) -> Optional[Path]:
        """Auto-detect metrics file based on log file name."""
        # Replace .log with _metrics.json
        base = self.log_file.stem.replace("_test", "")
        metrics_path = self.log_file.parent / f"{base}_metrics.json"
        return metrics_path if metrics_path.exists() else None

    def _parse_log(self) -> Dict:
        """Parse log file for key events."""
        if not self.log_file.exists():
            raise FileNotFoundError(f"Log file not found: {self.log_file}")

        data = {
            "start_time": None,
            "end_time": None,
            "entry_attempts": 0,
            "entry_successes": 0,
            "entry_failures": 0,
            "exit_attempts": 0,
            "exit_successes": 0,
            "exit_failures": 0,
            "exit_confirmations": 0,
            "balance_drifts": [],
            "trades": [],
            "errors": [],
            "warnings": [],
            "criticals": [],
            "orderbook_ws_reconnections": 0,
            "oms_ws_reconnections": 0,
            "orderbook_rest_fallbacks": 0,
            "stranded_positions": 0,
            "entry_fee_logs": 0,
        }

        with open(self.log_file, "r") as f:
            for line in f:
                # Extract timestamp
                ts_match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
                if ts_match:
                    ts = datetime.strptime(ts_match.group(1), "%Y-%m-%d %H:%M:%S")
                    if not data["start_time"]:
                        data["start_time"] = ts
                    data["end_time"] = ts

                # Entry attempts
                if "Submitting BUY order" in line or "Attempting entry" in line:
                    data["entry_attempts"] += 1

                # Entry successes
                if "Entry order filled" in line or "Successfully entered position" in line:
                    data["entry_successes"] += 1

                # Entry failures
                if (
                    "Entry failed" in line
                    or "Entry order cancelled" in line
                    or "Entry order rejected" in line
                ):
                    data["entry_failures"] += 1

                # Entry fee logging
                if "Entry fee:" in line or "entry_fee=" in line:
                    data["entry_fee_logs"] += 1

                # Exit attempts
                if "Submitting SELL order" in line or "Attempting exit" in line:
                    data["exit_attempts"] += 1

                # Exit successes
                if "Exit order filled" in line or "Successfully exited position" in line:
                    data["exit_successes"] += 1

                # Exit confirmations (via WebSocket fill)
                if "Exit fill confirmed via WebSocket" in line or "Fill received for exit order" in line:
                    data["exit_confirmations"] += 1

                # Exit failures
                if (
                    "Exit failed" in line
                    or "Exit order cancelled" in line
                    or "Exit order rejected" in line
                ):
                    data["exit_failures"] += 1

                # Balance drift
                if "Balance drift detected" in line:
                    match = re.search(r"drift=([+-]?\d+)¢", line)
                    if match:
                        data["balance_drifts"].append(int(match.group(1)))

                # Trade outcomes
                if "Trade closed" in line or "Position closed" in line:
                    match = re.search(r"(?:profit|pnl)=([+-]?\d+\.?\d*)", line)
                    if match:
                        data["trades"].append(float(match.group(1)))

                # WebSocket reconnections
                if "Orderbook WebSocket reconnecting" in line or "Reconnecting to orderbook" in line:
                    data["orderbook_ws_reconnections"] += 1

                if "OMS WebSocket reconnecting" in line or "Reconnecting to OMS" in line:
                    data["oms_ws_reconnections"] += 1

                # REST fallback
                if "Using REST API fallback for orderbook" in line or "Orderbook WebSocket failed, using REST" in line:
                    data["orderbook_rest_fallbacks"] += 1

                # Stranded positions
                if "Stranded position found" in line or "Untracked position detected" in line:
                    data["stranded_positions"] += 1

                # Errors
                if " ERROR " in line or line.strip().startswith("ERROR"):
                    data["errors"].append(line.strip())

                if " WARNING " in line or line.strip().startswith("WARNING"):
                    data["warnings"].append(line.strip())

                if " CRITICAL " in line or line.strip().startswith("CRITICAL"):
                    data["criticals"].append(line.strip())

        return data

    def _load_metrics(self) -> Optional[Dict]:
        """Load metrics JSON file."""
        if not self.metrics_file or not self.metrics_file.exists():
            return None

        with open(self.metrics_file, "r") as f:
            return json.load(f)

    def _validate_bug1_exit_confirmation(self) -> BugValidation:
        """Bug #1: Exit fills must be confirmed via WebSocket."""
        exit_successes = self.log_data["exit_successes"]
        exit_confirmations = self.log_data["exit_confirmations"]

        confirmation_rate = (
            exit_confirmations / exit_successes if exit_successes > 0 else 1.0
        )

        passed = confirmation_rate >= self.thresholds.min_exit_confirmation_rate

        return BugValidation(
            bug_number=1,
            bug_title="Exit Fill Confirmation",
            description="All exit orders must be confirmed via WebSocket (not assumed filled)",
            passed=passed,
            actual_value=f"{exit_confirmations}/{exit_successes} ({confirmation_rate:.1%})",
            expected_value=f"{self.thresholds.min_exit_confirmation_rate:.1%}",
            details=[
                f"Exit attempts: {self.log_data['exit_attempts']}",
                f"Exit successes: {exit_successes}",
                f"Exit confirmations: {exit_confirmations}",
                "✓ PASS - All exits confirmed" if passed else "✗ FAIL - Some exits not confirmed",
            ],
        )

    def _validate_bug2_orderbook_ws(self) -> BugValidation:
        """Bug #2: Orderbook WebSocket must be reliable (>50% entry success)."""
        entry_attempts = self.log_data["entry_attempts"]
        entry_successes = self.log_data["entry_successes"]

        success_rate = entry_successes / entry_attempts if entry_attempts > 0 else 0.0

        passed = success_rate >= self.thresholds.min_entry_success_rate

        return BugValidation(
            bug_number=2,
            bug_title="Orderbook WebSocket Reliability",
            description="Entry success rate must be >50% (was 20% with broken WebSocket)",
            passed=passed,
            actual_value=f"{entry_successes}/{entry_attempts} ({success_rate:.1%})",
            expected_value=f"≥{self.thresholds.min_entry_success_rate:.1%}",
            details=[
                f"Entry attempts: {entry_attempts}",
                f"Entry successes: {entry_successes}",
                f"Entry failures: {self.log_data['entry_failures']}",
                f"WebSocket reconnections: {self.log_data['orderbook_ws_reconnections']}",
                f"REST fallbacks: {self.log_data['orderbook_rest_fallbacks']}",
                "✓ PASS - WebSocket reliable" if passed else "✗ FAIL - WebSocket unreliable",
            ],
        )

    def _validate_bug3_oms_ws(self) -> BugValidation:
        """Bug #3: OMS WebSocket must be initialized and stay connected."""
        # Check if we have metrics data with WebSocket status
        if self.metrics_data and "snapshots" in self.metrics_data:
            snapshots = self.metrics_data["snapshots"]
            oms_connected_count = sum(
                1 for s in snapshots if s.get("oms_ws_connected", False)
            )
            uptime = oms_connected_count / len(snapshots) if snapshots else 0.0

            passed = uptime >= self.thresholds.min_oms_ws_uptime

            return BugValidation(
                bug_number=3,
                bug_title="OMS WebSocket Initialization",
                description="OMS WebSocket must be initialized and maintain >95% uptime",
                passed=passed,
                actual_value=f"{uptime:.1%} uptime",
                expected_value=f"≥{self.thresholds.min_oms_ws_uptime:.1%}",
                details=[
                    f"Total snapshots: {len(snapshots)}",
                    f"Connected snapshots: {oms_connected_count}",
                    f"Reconnections: {self.log_data['oms_ws_reconnections']}",
                    "✓ PASS - OMS WebSocket stable" if passed else "✗ FAIL - OMS WebSocket unstable",
                ],
            )
        else:
            # No metrics data - check log for reconnections
            reconnections = self.log_data["oms_ws_reconnections"]
            passed = reconnections == 0  # No reconnections = stable

            return BugValidation(
                bug_number=3,
                bug_title="OMS WebSocket Initialization",
                description="OMS WebSocket must be initialized and stay connected",
                passed=passed,
                actual_value=f"{reconnections} reconnections",
                expected_value="0 reconnections",
                details=[
                    f"Reconnections: {reconnections}",
                    "✓ PASS - No reconnections" if passed else "✗ FAIL - Had reconnections",
                ],
            )

    def _validate_bug6_exit_price(self) -> BugValidation:
        """Bug #6: Exit price must use actual fill price, not limit price."""
        # This is hard to validate from logs alone without API comparison
        # For now, check that exits log actual fill prices (not just limit prices)

        # Look for exit price logging patterns
        exit_price_logs = 0
        fill_price_logs = 0

        with open(self.log_file, "r") as f:
            for line in f:
                if "Exit order submitted at" in line or "Exit limit price:" in line:
                    exit_price_logs += 1
                if "Exit filled at" in line or "Exit fill price:" in line:
                    fill_price_logs += 1

        # Should have actual fill price for each exit
        exit_successes = self.log_data["exit_successes"]
        fill_rate = fill_price_logs / exit_successes if exit_successes > 0 else 1.0

        passed = fill_rate >= 0.95  # 95% of exits should log actual fill price

        return BugValidation(
            bug_number=6,
            bug_title="Exit Price Accuracy",
            description="Exit P&L must use actual fill price, not limit price",
            passed=passed,
            actual_value=f"{fill_price_logs}/{exit_successes} exits log fill price ({fill_rate:.1%})",
            expected_value="≥95%",
            details=[
                f"Exit successes: {exit_successes}",
                f"Fill price logs: {fill_price_logs}",
                f"Limit price logs: {exit_price_logs}",
                "✓ PASS - Fill prices logged" if passed else "✗ FAIL - Missing fill prices",
            ],
        )

    def _validate_bug7_entry_fees(self) -> BugValidation:
        """Bug #7: Entry fees must be logged for all entries."""
        entry_successes = self.log_data["entry_successes"]
        entry_fee_logs = self.log_data["entry_fee_logs"]

        fee_rate = entry_fee_logs / entry_successes if entry_successes > 0 else 1.0

        passed = fee_rate >= self.thresholds.min_fee_logging_rate

        return BugValidation(
            bug_number=7,
            bug_title="Entry Fee Logging",
            description="Entry fees must be calculated and logged for all entries",
            passed=passed,
            actual_value=f"{entry_fee_logs}/{entry_successes} ({fee_rate:.1%})",
            expected_value=f"≥{self.thresholds.min_fee_logging_rate:.1%}",
            details=[
                f"Entry successes: {entry_successes}",
                f"Entry fee logs: {entry_fee_logs}",
                "✓ PASS - Fees logged" if passed else "✗ FAIL - Missing fee logs",
            ],
        )

    def _validate_bug8_balance_tracking(self) -> BugValidation:
        """Bug #8: Balance must be tracked and drift must be <10¢."""
        drifts = self.log_data["balance_drifts"]
        max_drift = max(abs(d) for d in drifts) if drifts else 0
        avg_drift = sum(abs(d) for d in drifts) / len(drifts) if drifts else 0.0

        passed = max_drift <= self.thresholds.max_balance_drift_cents

        return BugValidation(
            bug_number=8,
            bug_title="Balance Tracking",
            description="Balance must be queried and drift must be <10¢",
            passed=passed,
            actual_value=f"max={max_drift}¢, avg={avg_drift:.1f}¢",
            expected_value=f"max≤{self.thresholds.max_balance_drift_cents}¢",
            details=[
                f"Balance checks: {len(drifts)}",
                f"Max drift: {max_drift}¢",
                f"Avg drift: {avg_drift:.1f}¢",
                f"Total drift: {sum(abs(d) for d in drifts)}¢",
                "✓ PASS - Drift acceptable" if passed else "✗ FAIL - Drift too high",
            ],
        )

    def _validate_bug9_position_reconciliation(self) -> BugValidation:
        """Bug #9: No stranded positions allowed."""
        stranded = self.log_data["stranded_positions"]

        passed = stranded <= self.thresholds.max_stranded_positions

        return BugValidation(
            bug_number=9,
            bug_title="Position Reconciliation",
            description="No stranded positions allowed (must reconcile on startup)",
            passed=passed,
            actual_value=f"{stranded} stranded positions",
            expected_value=f"≤{self.thresholds.max_stranded_positions}",
            details=[
                f"Stranded positions found: {stranded}",
                "✓ PASS - No stranded positions" if passed else "✗ FAIL - Stranded positions detected",
            ],
        )

    def _validate_system_stability(self) -> BugValidation:
        """General system stability check."""
        critical_count = len(self.log_data["criticals"])
        error_count = len(self.log_data["errors"])

        # Calculate test duration
        if self.log_data["start_time"] and self.log_data["end_time"]:
            duration_hours = (
                self.log_data["end_time"] - self.log_data["start_time"]
            ).total_seconds() / 3600.0
        else:
            duration_hours = 1.0  # Default

        error_rate = error_count / duration_hours

        passed = (
            critical_count <= self.thresholds.max_critical_errors
            and error_rate <= self.thresholds.max_error_rate_per_hour
        )

        return BugValidation(
            bug_number=0,
            bug_title="System Stability",
            description="No critical errors and <5 errors/hour",
            passed=passed,
            actual_value=f"{critical_count} critical, {error_count} errors ({error_rate:.1f}/hr)",
            expected_value=f"0 critical, ≤{self.thresholds.max_error_rate_per_hour}/hr",
            details=[
                f"Duration: {duration_hours:.1f}h",
                f"Critical errors: {critical_count}",
                f"Errors: {error_count}",
                f"Warnings: {len(self.log_data['warnings'])}",
                f"Error rate: {error_rate:.1f}/hr",
                "✓ PASS - System stable" if passed else "✗ FAIL - System unstable",
            ],
        )

    def generate_report(self) -> IntegrationReport:
        """Generate complete integration report."""
        # Run all validations
        validations = [
            self._validate_bug1_exit_confirmation(),
            self._validate_bug2_orderbook_ws(),
            self._validate_bug3_oms_ws(),
            self._validate_bug6_exit_price(),
            self._validate_bug7_entry_fees(),
            self._validate_bug8_balance_tracking(),
            self._validate_bug9_position_reconciliation(),
            self._validate_system_stability(),
        ]

        # Calculate summary
        total_bugs = len([v for v in validations if v.bug_number > 0])
        bugs_passed = sum(1 for v in validations if v.bug_number > 0 and v.passed)
        bugs_failed = total_bugs - bugs_passed
        overall_pass = all(v.passed for v in validations)

        # Calculate duration
        if self.log_data["start_time"] and self.log_data["end_time"]:
            duration = self.log_data["end_time"] - self.log_data["start_time"]
            duration_hours = duration.total_seconds() / 3600.0
        else:
            duration_hours = 0.0

        # Calculate metrics
        entry_attempts = self.log_data["entry_attempts"]
        entry_successes = self.log_data["entry_successes"]
        entry_success_rate = entry_successes / entry_attempts if entry_attempts > 0 else 0.0

        exit_attempts = self.log_data["exit_attempts"]
        exit_successes = self.log_data["exit_successes"]
        exit_success_rate = exit_successes / exit_attempts if exit_attempts > 0 else 0.0

        drifts = self.log_data["balance_drifts"]
        balance_drift_max = max(abs(d) for d in drifts) if drifts else 0
        balance_drift_avg = sum(abs(d) for d in drifts) / len(drifts) if drifts else 0.0

        trades = self.log_data["trades"]
        trades_completed = len(trades)
        win_rate = sum(1 for t in trades if t > 0) / trades_completed if trades_completed > 0 else 0.0

        return IntegrationReport(
            test_start=self.log_data["start_time"] or datetime.now(),
            test_end=self.log_data["end_time"] or datetime.now(),
            duration_hours=duration_hours,
            log_file=str(self.log_file),
            validations=validations,
            total_bugs=total_bugs,
            bugs_passed=bugs_passed,
            bugs_failed=bugs_failed,
            overall_pass=overall_pass,
            entry_attempts=entry_attempts,
            entry_successes=entry_successes,
            entry_success_rate=entry_success_rate,
            exit_attempts=exit_attempts,
            exit_successes=exit_successes,
            exit_success_rate=exit_success_rate,
            balance_drift_max_cents=balance_drift_max,
            balance_drift_avg_cents=balance_drift_avg,
            trades_completed=trades_completed,
            win_rate=win_rate,
            error_count=len(self.log_data["errors"]),
            warning_count=len(self.log_data["warnings"]),
            critical_count=len(self.log_data["criticals"]),
            orderbook_ws_reconnections=self.log_data["orderbook_ws_reconnections"],
            oms_ws_reconnections=self.log_data["oms_ws_reconnections"],
            orderbook_rest_fallbacks=self.log_data["orderbook_rest_fallbacks"],
            stranded_positions_found=self.log_data["stranded_positions"],
        )

    def format_html(self, report: IntegrationReport) -> str:
        """Format report as HTML."""
        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Phase 2 Integration Test Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #333; border-bottom: 3px solid #007bff; padding-bottom: 10px; }}
        h2 {{ color: #555; margin-top: 30px; }}
        .summary {{ background: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0; }}
        .pass {{ color: #28a745; font-weight: bold; }}
        .fail {{ color: #dc3545; font-weight: bold; }}
        .status-badge {{ padding: 5px 15px; border-radius: 20px; color: white; font-weight: bold; display: inline-block; }}
        .status-pass {{ background: #28a745; }}
        .status-fail {{ background: #dc3545; }}
        table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #007bff; color: white; }}
        tr:hover {{ background: #f5f5f5; }}
        .metric {{ display: inline-block; margin: 10px 20px 10px 0; }}
        .metric-label {{ font-weight: bold; color: #666; }}
        .metric-value {{ font-size: 1.2em; color: #333; }}
        .details {{ background: #f8f9fa; padding: 10px; margin: 5px 0; border-left: 3px solid #007bff; }}
        .details ul {{ margin: 5px 0; padding-left: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Phase 2 Integration Test Report</h1>

        <div class="summary">
            <h2>Test Summary</h2>
            <div class="metric">
                <span class="metric-label">Overall Status:</span>
                <span class="status-badge {'status-pass' if report.overall_pass else 'status-fail'}">
                    {'PASS' if report.overall_pass else 'FAIL'}
                </span>
            </div>
            <div class="metric">
                <span class="metric-label">Duration:</span>
                <span class="metric-value">{report.duration_hours:.1f}h</span>
            </div>
            <div class="metric">
                <span class="metric-label">Start:</span>
                <span class="metric-value">{report.test_start.strftime('%Y-%m-%d %H:%M:%S')}</span>
            </div>
            <div class="metric">
                <span class="metric-label">End:</span>
                <span class="metric-value">{report.test_end.strftime('%Y-%m-%d %H:%M:%S')}</span>
            </div>
            <br>
            <div class="metric">
                <span class="metric-label">Bugs Fixed:</span>
                <span class="metric-value {'pass' if report.bugs_passed == report.total_bugs else 'fail'}">
                    {report.bugs_passed}/{report.total_bugs}
                </span>
            </div>
        </div>

        <h2>Bug Fix Validation</h2>
        <table>
            <tr>
                <th>Bug #</th>
                <th>Title</th>
                <th>Status</th>
                <th>Actual</th>
                <th>Expected</th>
            </tr>
"""
        for v in report.validations:
            if v.bug_number == 0:  # Skip system stability in bug table
                continue
            status_class = 'pass' if v.passed else 'fail'
            status_text = '✓ PASS' if v.passed else '✗ FAIL'
            html += f"""            <tr>
                <td>#{v.bug_number}</td>
                <td>{v.bug_title}</td>
                <td class="{status_class}">{status_text}</td>
                <td>{v.actual_value}</td>
                <td>{v.expected_value}</td>
            </tr>
"""
        html += """        </table>

        <h2>Detailed Results</h2>
"""
        for v in report.validations:
            status_class = 'pass' if v.passed else 'fail'
            status_badge = 'status-pass' if v.passed else 'status-fail'
            status_text = 'PASS' if v.passed else 'FAIL'
            html += f"""        <div class="details">
            <h3>
                Bug #{v.bug_number}: {v.bug_title}
                <span class="status-badge {status_badge}">{status_text}</span>
            </h3>
            <p>{v.description}</p>
            <ul>
"""
            for detail in v.details:
                html += f"                <li>{detail}</li>\n"
            html += """            </ul>
        </div>
"""

        html += f"""
        <h2>Performance Metrics</h2>
        <div class="summary">
            <h3>Entry Performance</h3>
            <div class="metric">
                <span class="metric-label">Attempts:</span>
                <span class="metric-value">{report.entry_attempts}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Successes:</span>
                <span class="metric-value">{report.entry_successes}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Success Rate:</span>
                <span class="metric-value {'pass' if report.entry_success_rate >= 0.5 else 'fail'}">
                    {report.entry_success_rate:.1%}
                </span>
            </div>

            <h3>Exit Performance</h3>
            <div class="metric">
                <span class="metric-label">Attempts:</span>
                <span class="metric-value">{report.exit_attempts}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Successes:</span>
                <span class="metric-value">{report.exit_successes}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Success Rate:</span>
                <span class="metric-value">{report.exit_success_rate:.1%}</span>
            </div>

            <h3>Balance Drift</h3>
            <div class="metric">
                <span class="metric-label">Max Drift:</span>
                <span class="metric-value {'pass' if report.balance_drift_max_cents <= 10 else 'fail'}">
                    {report.balance_drift_max_cents}¢
                </span>
            </div>
            <div class="metric">
                <span class="metric-label">Avg Drift:</span>
                <span class="metric-value">{report.balance_drift_avg_cents:.1f}¢</span>
            </div>

            <h3>Trading Results</h3>
            <div class="metric">
                <span class="metric-label">Trades Completed:</span>
                <span class="metric-value">{report.trades_completed}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Win Rate:</span>
                <span class="metric-value">{report.win_rate:.1%}</span>
            </div>

            <h3>System Health</h3>
            <div class="metric">
                <span class="metric-label">Errors:</span>
                <span class="metric-value {'fail' if report.error_count > 0 else 'pass'}">{report.error_count}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Warnings:</span>
                <span class="metric-value">{report.warning_count}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Critical:</span>
                <span class="metric-value {'fail' if report.critical_count > 0 else 'pass'}">{report.critical_count}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Orderbook WS Reconnections:</span>
                <span class="metric-value">{report.orderbook_ws_reconnections}</span>
            </div>
            <div class="metric">
                <span class="metric-label">OMS WS Reconnections:</span>
                <span class="metric-value">{report.oms_ws_reconnections}</span>
            </div>
            <div class="metric">
                <span class="metric-label">REST Fallbacks:</span>
                <span class="metric-value">{report.orderbook_rest_fallbacks}</span>
            </div>
            <div class="metric">
                <span class="metric-label">Stranded Positions:</span>
                <span class="metric-value {'fail' if report.stranded_positions_found > 0 else 'pass'}">
                    {report.stranded_positions_found}
                </span>
            </div>
        </div>

        <h2>Conclusion</h2>
        <div class="summary">
"""
        if report.overall_pass:
            html += """            <p class="pass">
                ✓ All critical bug fixes validated successfully!
                System is ready for production deployment.
            </p>
"""
        else:
            html += f"""            <p class="fail">
                ✗ {report.bugs_failed} bug fix(es) failed validation.
                Review failed validations above before proceeding to production.
            </p>
"""
        html += f"""        </div>

        <p style="margin-top: 40px; color: #999; text-align: center; font-size: 0.9em;">
            Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Log: {report.log_file}
        </p>
    </div>
</body>
</html>
"""
        return html

    def format_markdown(self, report: IntegrationReport) -> str:
        """Format report as Markdown."""
        md = f"""# Phase 2 Integration Test Report

## Test Summary

**Overall Status:** {'✓ PASS' if report.overall_pass else '✗ FAIL'}

- **Duration:** {report.duration_hours:.1f}h
- **Start:** {report.test_start.strftime('%Y-%m-%d %H:%M:%S')}
- **End:** {report.test_end.strftime('%Y-%m-%d %H:%M:%S')}
- **Bugs Fixed:** {report.bugs_passed}/{report.total_bugs}

## Bug Fix Validation

| Bug # | Title | Status | Actual | Expected |
|-------|-------|--------|--------|----------|
"""
        for v in report.validations:
            if v.bug_number == 0:
                continue
            status = '✓ PASS' if v.passed else '✗ FAIL'
            md += f"| #{v.bug_number} | {v.bug_title} | {status} | {v.actual_value} | {v.expected_value} |\n"

        md += "\n## Detailed Results\n\n"
        for v in report.validations:
            status = '✓ PASS' if v.passed else '✗ FAIL'
            md += f"### Bug #{v.bug_number}: {v.bug_title} — {status}\n\n"
            md += f"{v.description}\n\n"
            for detail in v.details:
                md += f"- {detail}\n"
            md += "\n"

        md += f"""## Performance Metrics

### Entry Performance
- **Attempts:** {report.entry_attempts}
- **Successes:** {report.entry_successes}
- **Success Rate:** {report.entry_success_rate:.1%}

### Exit Performance
- **Attempts:** {report.exit_attempts}
- **Successes:** {report.exit_successes}
- **Success Rate:** {report.exit_success_rate:.1%}

### Balance Drift
- **Max Drift:** {report.balance_drift_max_cents}¢
- **Avg Drift:** {report.balance_drift_avg_cents:.1f}¢

### Trading Results
- **Trades Completed:** {report.trades_completed}
- **Win Rate:** {report.win_rate:.1%}

### System Health
- **Errors:** {report.error_count}
- **Warnings:** {report.warning_count}
- **Critical:** {report.critical_count}
- **Orderbook WS Reconnections:** {report.orderbook_ws_reconnections}
- **OMS WS Reconnections:** {report.oms_ws_reconnections}
- **REST Fallbacks:** {report.orderbook_rest_fallbacks}
- **Stranded Positions:** {report.stranded_positions_found}

## Conclusion

"""
        if report.overall_pass:
            md += "✓ All critical bug fixes validated successfully! System is ready for production deployment.\n"
        else:
            md += f"✗ {report.bugs_failed} bug fix(es) failed validation. Review failed validations above before proceeding to production.\n"

        md += f"\n---\n*Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Log: {report.log_file}*\n"

        return md


def main():
    parser = argparse.ArgumentParser(
        description="Generate Phase 2 integration test report"
    )
    parser.add_argument("log_file", type=Path, help="Path to integration test log file")
    parser.add_argument("--metrics", type=Path, help="Path to metrics JSON file (auto-detected if not provided)")
    parser.add_argument("--output", type=Path, help="Output report path (auto-generated if not provided)")
    parser.add_argument("--format", choices=["html", "md"], default="html", help="Report format")
    parser.add_argument("--threshold-file", type=Path, help="Custom validation thresholds YAML file")

    args = parser.parse_args()

    # Validate log file exists
    if not args.log_file.exists():
        print(f"ERROR: Log file not found: {args.log_file}", file=sys.stderr)
        return 1

    # Load custom thresholds if provided
    thresholds = None
    if args.threshold_file:
        import yaml
        with open(args.threshold_file, "r") as f:
            threshold_data = yaml.safe_load(f)
            thresholds = ValidationThresholds(**threshold_data)

    # Generate report
    print("Generating integration test report...")
    print(f"  Log file: {args.log_file}")
    if args.metrics:
        print(f"  Metrics: {args.metrics}")

    generator = ReportGenerator(args.log_file, args.metrics, thresholds)
    report = generator.generate_report()

    # Format report
    if args.format == "html":
        content = generator.format_html(report)
        ext = ".html"
    else:
        content = generator.format_markdown(report)
        ext = ".md"

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        # Auto-generate from log file name
        base = args.log_file.stem.replace("_test", "")
        output_path = args.log_file.parent / f"{base}_report{ext}"

    # Write report
    with open(output_path, "w") as f:
        f.write(content)

    print(f"\n{'='*60}")
    print(f"Report generated: {output_path}")
    print(f"{'='*60}\n")

    # Print summary
    print(f"Overall Status: {'✓ PASS' if report.overall_pass else '✗ FAIL'}")
    print(f"Duration: {report.duration_hours:.1f}h")
    print(f"Bugs Fixed: {report.bugs_passed}/{report.total_bugs}")
    print(f"\nValidation Results:")
    for v in report.validations:
        status = '✓' if v.passed else '✗'
        print(f"  {status} Bug #{v.bug_number}: {v.bug_title}")

    print(f"\nPerformance:")
    print(f"  Entry Success: {report.entry_success_rate:.1%} ({report.entry_successes}/{report.entry_attempts})")
    print(f"  Exit Success: {report.exit_success_rate:.1%} ({report.exit_successes}/{report.exit_attempts})")
    print(f"  Balance Drift: max={report.balance_drift_max_cents}¢, avg={report.balance_drift_avg_cents:.1f}¢")
    print(f"  Trades: {report.trades_completed}, Win Rate: {report.win_rate:.1%}")
    print(f"  Errors: {report.error_count}, Warnings: {report.warning_count}, Critical: {report.critical_count}")

    if not report.overall_pass:
        print(f"\n⚠️  FAILED VALIDATIONS:")
        for v in report.validations:
            if not v.passed:
                print(f"  ✗ Bug #{v.bug_number}: {v.bug_title}")
                print(f"    Actual: {v.actual_value}")
                print(f"    Expected: {v.expected_value}")

    return 0 if report.overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
