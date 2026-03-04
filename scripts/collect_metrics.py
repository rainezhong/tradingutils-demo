#!/usr/bin/env python3
"""
Phase 2 Integration Test Metrics Collector

Runs alongside the integration test to collect periodic metrics:
- Entry/exit attempt counts and success rates
- Balance drift over time
- WebSocket connection health
- Error/warning counts
- Position reconciliation status

Saves metrics to JSON file with timestamps for later analysis.

Usage:
    python3 scripts/collect_metrics.py --log-file LOGFILE --output METRICS.json [--interval SECONDS]

Options:
    --log-file PATH       Path to the strategy log file to monitor
    --output PATH         Path to save metrics JSON file
    --interval SECONDS    Collection interval in seconds (default: 3600 = 1 hour)
    --once                Collect once and exit (don't run continuously)
    --help                Show this help message
"""

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class MetricsSnapshot:
    """Single point-in-time metrics snapshot."""

    timestamp: str
    elapsed_hours: float

    # Entry metrics
    entry_attempts: int
    entry_successes: int
    entry_failures: int
    entry_success_rate: float

    # Exit metrics
    exit_attempts: int
    exit_successes: int
    exit_failures: int
    exit_success_rate: float

    # Balance tracking
    balance_checks: int
    balance_drift_total_cents: int
    balance_drift_max_cents: int
    balance_drift_avg_cents: float

    # WebSocket health
    orderbook_ws_connected: bool
    oms_ws_connected: bool
    orderbook_ws_reconnections: int
    oms_ws_reconnections: int

    # REST fallback usage
    orderbook_rest_fallbacks: int

    # Position reconciliation
    position_reconciliations: int
    stranded_positions_found: int

    # Error tracking
    error_count: int
    warning_count: int
    critical_count: int

    # Trade outcomes
    trades_completed: int
    trades_profitable: int
    trades_unprofitable: int
    win_rate: float


class MetricsCollector:
    """Collects metrics from strategy log file."""

    def __init__(self, log_file: Path, output_file: Path):
        self.log_file = log_file
        self.output_file = output_file
        self.start_time = datetime.now()

        # Cumulative counters
        self.entry_attempts = 0
        self.entry_successes = 0
        self.entry_failures = 0

        self.exit_attempts = 0
        self.exit_successes = 0
        self.exit_failures = 0

        self.balance_checks = 0
        self.balance_drift_values: List[int] = []

        self.orderbook_ws_reconnections = 0
        self.oms_ws_reconnections = 0
        self.orderbook_rest_fallbacks = 0

        self.position_reconciliations = 0
        self.stranded_positions_found = 0

        self.error_count = 0
        self.warning_count = 0
        self.critical_count = 0

        self.trades_completed = 0
        self.trades_profitable = 0
        self.trades_unprofitable = 0

        # Track last read position
        self.last_position = 0

        # Current WebSocket status (read from most recent status line)
        self.orderbook_ws_connected = False
        self.oms_ws_connected = False

    def parse_log_incremental(self) -> None:
        """Parse log file from last position, updating counters."""
        if not self.log_file.exists():
            return

        with open(self.log_file, "r") as f:
            # Seek to last read position
            f.seek(self.last_position)

            for line in f:
                self._parse_line(line)

            # Save current position
            self.last_position = f.tell()

    def _parse_line(self, line: str) -> None:
        """Parse a single log line and update counters."""
        # Entry attempts
        if "Submitting BUY order" in line or "Attempting entry" in line:
            self.entry_attempts += 1

        # Entry successes
        if "Entry order filled" in line or "Successfully entered position" in line:
            self.entry_successes += 1

        # Entry failures
        if (
            "Entry failed" in line
            or "Entry order cancelled" in line
            or "Entry order rejected" in line
        ):
            self.entry_failures += 1

        # Exit attempts
        if "Submitting SELL order" in line or "Attempting exit" in line:
            self.exit_attempts += 1

        # Exit successes
        if "Exit order filled" in line or "Successfully exited position" in line:
            self.exit_successes += 1

        # Exit failures
        if (
            "Exit failed" in line
            or "Exit order cancelled" in line
            or "Exit order rejected" in line
        ):
            self.exit_failures += 1

        # Balance drift (look for reconciliation messages)
        if "Balance drift detected" in line:
            self.balance_checks += 1
            # Extract drift amount if present: "drift=+5¢" or "drift=-12¢"
            match = re.search(r"drift=([+-]?\d+)¢", line)
            if match:
                drift = int(match.group(1))
                self.balance_drift_values.append(drift)

        if "Balance reconciliation" in line:
            self.balance_checks += 1

        # WebSocket reconnections
        if "Orderbook WebSocket reconnecting" in line or "Reconnecting to orderbook" in line:
            self.orderbook_ws_reconnections += 1

        if "OMS WebSocket reconnecting" in line or "Reconnecting to OMS" in line:
            self.oms_ws_reconnections += 1

        # REST fallback
        if "Using REST API fallback for orderbook" in line or "Orderbook WebSocket failed, using REST" in line:
            self.orderbook_rest_fallbacks += 1

        # WebSocket status (track most recent)
        if "Orderbook WebSocket: connected" in line:
            self.orderbook_ws_connected = True
        elif "Orderbook WebSocket: disconnected" in line or "Orderbook WebSocket failed" in line:
            self.orderbook_ws_connected = False

        if "OMS WebSocket: connected" in line:
            self.oms_ws_connected = True
        elif "OMS WebSocket: disconnected" in line or "OMS WebSocket failed" in line:
            self.oms_ws_connected = False

        # Position reconciliation
        if "Reconciling positions" in line or "Position reconciliation" in line:
            self.position_reconciliations += 1

        if "Stranded position found" in line or "Untracked position detected" in line:
            self.stranded_positions_found += 1

        # Error tracking
        if " ERROR " in line or line.strip().startswith("ERROR"):
            self.error_count += 1

        if " WARNING " in line or line.strip().startswith("WARNING"):
            self.warning_count += 1

        if " CRITICAL " in line or line.strip().startswith("CRITICAL"):
            self.critical_count += 1

        # Trade outcomes (look for P&L logging)
        if "Trade closed" in line or "Position closed" in line:
            self.trades_completed += 1

            # Check if profitable
            if "profit=" in line or "pnl=" in line:
                # Extract P&L: "profit=+5.0" or "pnl=-3.2"
                match = re.search(r"(?:profit|pnl)=([+-]?\d+\.?\d*)", line)
                if match:
                    pnl = float(match.group(1))
                    if pnl > 0:
                        self.trades_profitable += 1
                    elif pnl < 0:
                        self.trades_unprofitable += 1

    def collect_snapshot(self) -> MetricsSnapshot:
        """Collect current metrics snapshot."""
        # Parse new log lines
        self.parse_log_incremental()

        # Calculate elapsed time
        elapsed = (datetime.now() - self.start_time).total_seconds()
        elapsed_hours = elapsed / 3600.0

        # Calculate success rates
        entry_success_rate = (
            self.entry_successes / self.entry_attempts if self.entry_attempts > 0 else 0.0
        )
        exit_success_rate = (
            self.exit_successes / self.exit_attempts if self.exit_attempts > 0 else 0.0
        )

        # Calculate balance drift stats
        balance_drift_total = sum(abs(d) for d in self.balance_drift_values)
        balance_drift_max = max(abs(d) for d in self.balance_drift_values) if self.balance_drift_values else 0
        balance_drift_avg = (
            balance_drift_total / len(self.balance_drift_values)
            if self.balance_drift_values
            else 0.0
        )

        # Calculate win rate
        win_rate = (
            self.trades_profitable / self.trades_completed
            if self.trades_completed > 0
            else 0.0
        )

        return MetricsSnapshot(
            timestamp=datetime.now().isoformat(),
            elapsed_hours=elapsed_hours,
            entry_attempts=self.entry_attempts,
            entry_successes=self.entry_successes,
            entry_failures=self.entry_failures,
            entry_success_rate=entry_success_rate,
            exit_attempts=self.exit_attempts,
            exit_successes=self.exit_successes,
            exit_failures=self.exit_failures,
            exit_success_rate=exit_success_rate,
            balance_checks=self.balance_checks,
            balance_drift_total_cents=balance_drift_total,
            balance_drift_max_cents=balance_drift_max,
            balance_drift_avg_cents=balance_drift_avg,
            orderbook_ws_connected=self.orderbook_ws_connected,
            oms_ws_connected=self.oms_ws_connected,
            orderbook_ws_reconnections=self.orderbook_ws_reconnections,
            oms_ws_reconnections=self.oms_ws_reconnections,
            orderbook_rest_fallbacks=self.orderbook_rest_fallbacks,
            position_reconciliations=self.position_reconciliations,
            stranded_positions_found=self.stranded_positions_found,
            error_count=self.error_count,
            warning_count=self.warning_count,
            critical_count=self.critical_count,
            trades_completed=self.trades_completed,
            trades_profitable=self.trades_profitable,
            trades_unprofitable=self.trades_unprofitable,
            win_rate=win_rate,
        )

    def save_snapshot(self, snapshot: MetricsSnapshot) -> None:
        """Save snapshot to JSON file (append to list)."""
        # Load existing snapshots
        snapshots = []
        if self.output_file.exists():
            with open(self.output_file, "r") as f:
                data = json.load(f)
                snapshots = data.get("snapshots", [])

        # Append new snapshot
        snapshots.append(asdict(snapshot))

        # Save back
        with open(self.output_file, "w") as f:
            json.dump(
                {
                    "test_start": self.start_time.isoformat(),
                    "log_file": str(self.log_file),
                    "snapshots": snapshots,
                },
                f,
                indent=2,
            )

        print(f"[{snapshot.timestamp}] Metrics snapshot saved (elapsed={snapshot.elapsed_hours:.2f}h)")
        print(f"  Entry: {snapshot.entry_successes}/{snapshot.entry_attempts} ({snapshot.entry_success_rate:.1%})")
        print(f"  Exit: {snapshot.exit_successes}/{snapshot.exit_attempts} ({snapshot.exit_success_rate:.1%})")
        print(f"  Balance drift: max={snapshot.balance_drift_max_cents}¢, avg={snapshot.balance_drift_avg_cents:.1f}¢")
        print(f"  Errors: {snapshot.error_count}, Warnings: {snapshot.warning_count}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Collect metrics from integration test log file"
    )
    parser.add_argument(
        "--log-file", type=Path, required=True, help="Path to strategy log file"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="Path to save metrics JSON file"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Collection interval in seconds (default: 3600)",
    )
    parser.add_argument(
        "--once", action="store_true", help="Collect once and exit"
    )

    args = parser.parse_args()

    # Validate log file
    if not args.log_file.exists():
        print(f"Waiting for log file to be created: {args.log_file}")
        # Wait up to 60 seconds for log file to appear
        for _ in range(60):
            if args.log_file.exists():
                break
            time.sleep(1)
        else:
            print(f"ERROR: Log file not found: {args.log_file}", file=sys.stderr)
            return 1

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Create collector
    collector = MetricsCollector(args.log_file, args.output)

    print(f"Metrics collector started")
    print(f"  Log file: {args.log_file}")
    print(f"  Output: {args.output}")
    print(f"  Interval: {args.interval}s")
    print()

    if args.once:
        # Collect once and exit
        snapshot = collector.collect_snapshot()
        collector.save_snapshot(snapshot)
        return 0

    # Collect periodically
    try:
        while True:
            snapshot = collector.collect_snapshot()
            collector.save_snapshot(snapshot)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nMetrics collector stopped")
        # Save final snapshot
        snapshot = collector.collect_snapshot()
        collector.save_snapshot(snapshot)
        return 0


if __name__ == "__main__":
    sys.exit(main())
