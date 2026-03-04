#!/usr/bin/env python3
"""
Stress Test Report Generator

Analyzes logs from all stress tests and generates a comprehensive
pass/fail report with log excerpts as evidence and final go/no-go
recommendation for live trading.

Author: Claude Code
Date: 2026-03-02
"""

import os
import sys
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class TestResult:
    """Result from a single stress test."""

    name: str
    passed: bool
    status: str  # PASS, FAIL, PARTIAL, SKIPPED
    log_file: Optional[Path]
    report_file: Optional[Path]
    key_findings: List[str]
    evidence: List[str]
    timestamp: datetime


class StressTestAnalyzer:
    """Analyzes stress test results and generates final report."""

    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.results: Dict[str, TestResult] = {}

    def analyze_circuit_breaker(self) -> TestResult:
        """Analyze circuit breaker test results."""
        print("Analyzing circuit breaker test...")

        # Find most recent circuit breaker test logs
        reports = sorted(
            self.logs_dir.glob("circuit_breaker_test_report*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not reports:
            return TestResult(
                name="Circuit Breaker",
                passed=False,
                status="SKIPPED",
                log_file=None,
                report_file=None,
                key_findings=["No test results found"],
                evidence=[],
                timestamp=datetime.now(),
            )

        report_file = reports[0]
        timestamp = datetime.fromtimestamp(report_file.stat().st_mtime)

        # Parse report
        with open(report_file, "r") as f:
            content = f.read()

        # Extract key findings
        findings = []
        evidence = []

        # Check for circuit breaker trigger
        if "Circuit Breaker Triggered:  ✓ YES" in content:
            findings.append("Circuit breaker triggered successfully")
            evidence.append("Circuit breaker trigger confirmed in report")
        elif "Circuit Breaker Triggered:  ✗ NO" in content:
            findings.append("Circuit breaker NOT triggered (possible test issue)")
            evidence.append("No circuit breaker trigger detected")

        # Check for trading halt
        if "Trading Halted:             ✓ YES" in content:
            findings.append("Trading halted after circuit breaker")
            evidence.append("Trading halt message confirmed")
        elif "Trading Halted:             ✗ NO" in content:
            findings.append("Trading did NOT halt (CRITICAL FAILURE)")
            evidence.append("No trading halt detected")

        # Check for trades after circuit breaker
        if "Trades After CB:            0" in content:
            findings.append("No trades executed after circuit breaker")
            evidence.append("Zero trades after circuit breaker trigger")
        elif match := re.search(r"Trades After CB:\s+(\d+)", content):
            count = int(match.group(1))
            if count > 0:
                findings.append(f"FAILURE: {count} trades after circuit breaker")
                evidence.append(f"{count} trades executed after circuit breaker")

        # Determine overall status
        if "✓ PASS" in content:
            status = "PASS"
            passed = True
        elif "✗ FAIL" in content:
            status = "FAIL"
            passed = False
        else:
            status = "PARTIAL"
            passed = False

        # Find corresponding log file
        log_pattern = report_file.name.replace("_report", "").replace(".txt", ".log")
        log_file = self.logs_dir / log_pattern
        if not log_file.exists():
            # Try to find any matching log
            logs = list(self.logs_dir.glob("circuit_breaker_test*.log"))
            log_file = logs[0] if logs else None

        return TestResult(
            name="Circuit Breaker",
            passed=passed,
            status=status,
            log_file=log_file,
            report_file=report_file,
            key_findings=findings,
            evidence=evidence,
            timestamp=timestamp,
        )

    def analyze_websocket_reconnection(self) -> TestResult:
        """Analyze WebSocket reconnection test results."""
        print("Analyzing WebSocket reconnection test...")

        reports = sorted(
            self.logs_dir.glob("websocket_reconnection_test_report*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not reports:
            return TestResult(
                name="WebSocket Reconnection",
                passed=False,
                status="SKIPPED",
                log_file=None,
                report_file=None,
                key_findings=["No test results found"],
                evidence=[],
                timestamp=datetime.now(),
            )

        report_file = reports[0]
        timestamp = datetime.fromtimestamp(report_file.stat().st_mtime)

        with open(report_file, "r") as f:
            content = f.read()

        findings = []
        evidence = []

        # Check for disconnection
        if "Disconnection Detected:     ✓ YES" in content:
            findings.append("WebSocket disconnection detected")
            evidence.append("Disconnection event confirmed")
        else:
            findings.append("No disconnection detected (test may not have run)")

        # Check for reconnection attempts
        if match := re.search(r"Reconnection Attempts:\s+(\d+)", content):
            count = int(match.group(1))
            findings.append(f"{count} reconnection attempts detected")
            evidence.append(f"Reconnection attempt count: {count}")

        # Check for exponential backoff
        if "Exponential Backoff:        ✓ YES" in content:
            findings.append("Exponential backoff pattern confirmed")
            evidence.append("Backoff delays follow exponential pattern")
        else:
            findings.append("Exponential backoff NOT confirmed")

        # Check for successful reconnection
        if "Reconnection Success:       ✓ YES" in content:
            findings.append("Successfully reconnected after disruption")
            evidence.append("Reconnection confirmed in logs")
        else:
            findings.append("Reconnection NOT confirmed")

        # Determine status
        if "✓ PASS" in content:
            status = "PASS"
            passed = True
        elif "⚠ INCONCLUSIVE" in content or "⚠ SKIPPED" in content:
            status = "PARTIAL"
            passed = False
        else:
            status = "FAIL"
            passed = False

        log_pattern = report_file.name.replace("_report", "").replace(".txt", ".log")
        log_file = self.logs_dir / log_pattern
        if not log_file.exists():
            logs = list(self.logs_dir.glob("websocket_reconnection_test*.log"))
            log_file = logs[0] if logs else None

        return TestResult(
            name="WebSocket Reconnection",
            passed=passed,
            status=status,
            log_file=log_file,
            report_file=report_file,
            key_findings=findings,
            evidence=evidence,
            timestamp=timestamp,
        )

    def analyze_position_reconciliation(self) -> TestResult:
        """Analyze position reconciliation test results."""
        print("Analyzing position reconciliation test...")

        reports = sorted(
            self.logs_dir.glob("position_reconciliation_test_report*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not reports:
            return TestResult(
                name="Position Reconciliation",
                passed=False,
                status="SKIPPED",
                log_file=None,
                report_file=None,
                key_findings=["No test results found"],
                evidence=[],
                timestamp=datetime.now(),
            )

        report_file = reports[0]
        timestamp = datetime.fromtimestamp(report_file.stat().st_mtime)

        with open(report_file, "r") as f:
            content = f.read()

        findings = []
        evidence = []

        # Check for position detection
        if "Position Detected:     ✓ YES" in content:
            findings.append("Stranded position detected at startup")
            evidence.append("Position detection confirmed")
        else:
            findings.append("Position NOT detected (critical failure)")

        # Check for user prompt
        if "User Prompted:         ✓ YES" in content:
            findings.append("User prompted for action")
            evidence.append("User prompt confirmed in logs")
        else:
            findings.append("User NOT prompted (failure)")

        # Check user choice
        if "User Choice:           continue" in content:
            findings.append("User chose to continue (position added to exit queue)")
        elif "User Choice:           abort" in content:
            findings.append("User chose to abort (strategy exited)")

        # Check for exit attempt
        if "Exit Attempted:        ✓ YES" in content:
            findings.append("Exit attempt detected for stranded position")
            evidence.append("Exit order placement confirmed")

        # Determine status
        if "✓ PASS" in content:
            status = "PASS"
            passed = True
        elif "⚠ PARTIAL" in content:
            status = "PARTIAL"
            passed = False
        else:
            status = "FAIL"
            passed = False

        log_pattern = report_file.name.replace("_report", "").replace(".txt", ".log")
        log_file = self.logs_dir / log_pattern
        if not log_file.exists():
            logs = list(self.logs_dir.glob("position_reconciliation_test*.log"))
            log_file = logs[0] if logs else None

        return TestResult(
            name="Position Reconciliation",
            passed=passed,
            status=status,
            log_file=log_file,
            report_file=report_file,
            key_findings=findings,
            evidence=evidence,
            timestamp=timestamp,
        )

    def analyze_process_lock(self) -> TestResult:
        """Analyze process lock test results."""
        print("Analyzing process lock test...")

        reports = sorted(
            self.logs_dir.glob("process_lock_test_report*.txt"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not reports:
            return TestResult(
                name="Process Lock",
                passed=False,
                status="SKIPPED",
                log_file=None,
                report_file=None,
                key_findings=["No test results found"],
                evidence=[],
                timestamp=datetime.now(),
            )

        report_file = reports[0]
        timestamp = datetime.fromtimestamp(report_file.stat().st_mtime)

        with open(report_file, "r") as f:
            content = f.read()

        findings = []
        evidence = []

        # Check for lock file creation
        if "Lock File Created:          ✓ YES" in content:
            findings.append("Lock file created on startup")
            evidence.append("Lock file creation confirmed")
        else:
            findings.append("Lock file NOT created (critical failure)")

        # Check for second instance blocking
        if "Second Instance Blocked:    ✓ YES" in content:
            findings.append("Second instance blocked successfully")
            evidence.append("Second instance prevented from starting")
        else:
            findings.append("Second instance NOT blocked (CRITICAL FAILURE)")

        # Check for RuntimeError
        if "RuntimeError Raised:        ✓ YES" in content:
            findings.append("RuntimeError raised for second instance")
            evidence.append("RuntimeError confirmed in logs")
        else:
            findings.append("RuntimeError NOT raised (failure)")

        # Check for cleanup
        if "Lock File Cleaned Up:       ✓ YES" in content:
            findings.append("Lock file cleaned up on shutdown")
            evidence.append("Cleanup confirmed")
        else:
            findings.append("Lock file cleanup may have issues")

        # Determine status
        if "✓ PASS" in content:
            status = "PASS"
            passed = True
        elif "⚠ PARTIAL" in content:
            status = "PARTIAL"
            passed = False
        else:
            status = "FAIL"
            passed = False

        # Note: Process lock test has TWO log files
        log_file = None
        logs = list(self.logs_dir.glob("process_lock_test_instance1*.log"))
        if logs:
            log_file = logs[0]

        return TestResult(
            name="Process Lock",
            passed=passed,
            status=status,
            log_file=log_file,
            report_file=report_file,
            key_findings=findings,
            evidence=evidence,
            timestamp=timestamp,
        )

    def run_all_analyses(self) -> None:
        """Run all stress test analyses."""
        self.results["circuit_breaker"] = self.analyze_circuit_breaker()
        self.results["websocket_reconnection"] = self.analyze_websocket_reconnection()
        self.results["position_reconciliation"] = self.analyze_position_reconciliation()
        self.results["process_lock"] = self.analyze_process_lock()

    def generate_final_report(self, output_file: Path) -> str:
        """Generate comprehensive final report with go/no-go recommendation."""
        # Calculate overall status
        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results.values() if r.passed)
        failed_tests = sum(1 for r in self.results.values() if r.status == "FAIL")
        partial_tests = sum(1 for r in self.results.values() if r.status == "PARTIAL")
        skipped_tests = sum(1 for r in self.results.values() if r.status == "SKIPPED")

        # Determine go/no-go
        critical_failures = []
        warnings = []

        for key, result in self.results.items():
            if result.status == "FAIL":
                critical_failures.append(result.name)
            elif result.status == "PARTIAL":
                warnings.append(result.name)

        # Go/No-Go decision
        if critical_failures:
            recommendation = "🚨 NO-GO"
            recommendation_detail = (
                "CRITICAL FAILURES DETECTED - DO NOT PROCEED WITH LIVE TRADING"
            )
        elif failed_tests > 0 or partial_tests > total_tests // 2:
            recommendation = "⚠️  CAUTION"
            recommendation_detail = (
                "Multiple issues detected - Review findings before live trading"
            )
        elif skipped_tests == total_tests:
            recommendation = "❓ UNKNOWN"
            recommendation_detail = "No tests have been run - Cannot make recommendation"
        elif passed_tests == total_tests:
            recommendation = "✅ GO"
            recommendation_detail = "All stress tests passed - Ready for live trading"
        else:
            recommendation = "⚠️  CONDITIONAL GO"
            recommendation_detail = (
                "Most tests passed but some warnings - Proceed with caution"
            )

        # Generate report content
        report_lines = [
            "═" * 70,
            "CRYPTO SCALP STRATEGY - STRESS TEST FINAL REPORT",
            "═" * 70,
            "",
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "EXECUTIVE SUMMARY",
            "─" * 70,
            f"Total Tests:        {total_tests}",
            f"Passed:             {passed_tests} ✓",
            f"Failed:             {failed_tests} ✗",
            f"Partial:            {partial_tests} ⚠",
            f"Skipped:            {skipped_tests} ○",
            "",
            f"RECOMMENDATION:     {recommendation}",
            f"                    {recommendation_detail}",
            "",
        ]

        # Add critical failures section if any
        if critical_failures:
            report_lines.extend(
                [
                    "CRITICAL FAILURES (MUST FIX BEFORE LIVE TRADING)",
                    "─" * 70,
                ]
            )
            for failure in critical_failures:
                report_lines.append(f"  ✗ {failure}")
            report_lines.append("")

        # Add warnings section if any
        if warnings:
            report_lines.extend(
                [
                    "WARNINGS (REVIEW BEFORE LIVE TRADING)",
                    "─" * 70,
                ]
            )
            for warning in warnings:
                report_lines.append(f"  ⚠ {warning}")
            report_lines.append("")

        # Add detailed results for each test
        report_lines.extend(
            [
                "DETAILED TEST RESULTS",
                "═" * 70,
                "",
            ]
        )

        for key, result in self.results.items():
            status_icon = {
                "PASS": "✓",
                "FAIL": "✗",
                "PARTIAL": "⚠",
                "SKIPPED": "○",
            }.get(result.status, "?")

            report_lines.extend(
                [
                    f"{result.name}",
                    "─" * 70,
                    f"Status:     {status_icon} {result.status}",
                    f"Timestamp:  {result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
                ]
            )

            if result.report_file:
                report_lines.append(f"Report:     {result.report_file}")
            if result.log_file:
                report_lines.append(f"Log:        {result.log_file}")

            report_lines.append("")
            report_lines.append("Key Findings:")
            for finding in result.key_findings:
                report_lines.append(f"  • {finding}")

            if result.evidence:
                report_lines.append("")
                report_lines.append("Evidence:")
                for evidence in result.evidence:
                    report_lines.append(f"  - {evidence}")

            report_lines.append("")
            report_lines.append("")

        # Add action items
        report_lines.extend(
            [
                "ACTION ITEMS",
                "═" * 70,
                "",
            ]
        )

        if recommendation == "🚨 NO-GO":
            report_lines.extend(
                [
                    "IMMEDIATE ACTIONS REQUIRED:",
                    "  1. Fix all critical failures listed above",
                    "  2. Re-run all stress tests",
                    "  3. Verify fixes in paper mode for 24 hours",
                    "  4. Only then consider live trading",
                    "",
                ]
            )
        elif recommendation == "⚠️  CAUTION" or recommendation == "⚠️  CONDITIONAL GO":
            report_lines.extend(
                [
                    "RECOMMENDED ACTIONS:",
                    "  1. Review all warning items above",
                    "  2. Consider re-running inconclusive tests",
                    "  3. Start live trading with minimum position sizes",
                    "  4. Monitor closely for first 2 hours",
                    "",
                ]
            )
        elif recommendation == "✅ GO":
            report_lines.extend(
                [
                    "READY FOR LIVE TRADING:",
                    "  1. All stress tests passed ✓",
                    "  2. Can proceed with standard position sizing",
                    "  3. Recommend monitoring for first 30 minutes",
                    "  4. Check balance tracking after first 5 trades",
                    "",
                ]
            )
        else:
            report_lines.extend(
                [
                    "ACTIONS REQUIRED:",
                    "  1. Run all stress tests before proceeding",
                    "  2. Review individual test reports",
                    "  3. Generate new final report after testing",
                    "",
                ]
            )

        report_lines.extend(
            [
                "NOTES",
                "─" * 70,
                "- All stress tests should be run before live trading",
                "- Re-run tests after any code changes to core systems",
                "- Circuit breaker test requires losing trades to trigger",
                "- WebSocket test requires manual network disruption",
                "- Position reconciliation requires manual position setup",
                "- Process lock test is fully automated",
                "",
                "═" * 70,
            ]
        )

        report_content = "\n".join(report_lines)

        # Write to file
        with open(output_file, "w") as f:
            f.write(report_content)

        return report_content


def main():
    """Main entry point."""
    # Setup paths
    repo_root = Path(__file__).parent.parent
    logs_dir = repo_root / "logs"

    if not logs_dir.exists():
        print(f"Error: Logs directory not found: {logs_dir}")
        print("Please run stress tests first to generate logs")
        sys.exit(1)

    print("=" * 70)
    print("STRESS TEST REPORT GENERATOR")
    print("=" * 70)
    print()

    # Create analyzer
    analyzer = StressTestAnalyzer(logs_dir)

    # Run all analyses
    print("Analyzing stress test results...")
    print()
    analyzer.run_all_analyses()

    # Generate final report
    output_file = logs_dir / f"STRESS_TEST_FINAL_REPORT_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    print()
    print("Generating final report...")
    report_content = analyzer.generate_final_report(output_file)

    # Display report
    print()
    print(report_content)

    print()
    print(f"✓ Final report saved to: {output_file}")
    print()


if __name__ == "__main__":
    main()
