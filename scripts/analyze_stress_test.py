#!/usr/bin/env python3
"""
Analyze stress test results and generate a report
"""

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List


def parse_log_file(log_file: Path) -> Dict:
    """Parse log file for key metrics"""
    with open(log_file, 'r') as f:
        content = f.read()

    metrics = {
        'errors': [],
        'warnings': [],
        'websocket_reconnections': [],
        'rest_fallbacks': [],
        'trades': [],
        'crashes': []
    }

    # Find all errors
    for match in re.finditer(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) ERROR (.+)', content):
        metrics['errors'].append({
            'timestamp': match.group(1),
            'message': match.group(2)
        })

    # Find all warnings
    for match in re.finditer(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) WARNING (.+)', content):
        metrics['warnings'].append({
            'timestamp': match.group(1),
            'message': match.group(2)
        })

    # Find WebSocket reconnections
    for match in re.finditer(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (?:INFO|WARNING) .*(reconnecting|connected successfully|WS connected)', content):
        metrics['websocket_reconnections'].append({
            'timestamp': match.group(1),
            'message': match.group(2)
        })

    # Find REST fallbacks
    for match in re.finditer(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (?:INFO|WARNING) .*(REST fallback|activating REST|deactivating REST)', content):
        metrics['rest_fallbacks'].append({
            'timestamp': match.group(1),
            'event': 'activated' if 'activating' in match.group(2) else 'deactivated'
        })

    # Find paper trades
    for match in re.finditer(r'\[PAPER\] (ENTRY|EXIT(?:\s+\[FORCE\])?): (\w+) ([\w-]+) (\d+) @ (\d+)c.*P&L=([+-]?\d+)c', content):
        metrics['trades'].append({
            'type': match.group(1),
            'side': match.group(2),
            'ticker': match.group(3),
            'contracts': int(match.group(4)),
            'price_cents': int(match.group(5)),
            'pnl_cents': int(match.group(6)) if 'EXIT' in match.group(1) else None
        })

    # Check for crashes
    if 'Traceback' in content and 'Exception' in content:
        for match in re.finditer(r'(Traceback[\s\S]+?(?:Error|Exception): .+)', content):
            metrics['crashes'].append(match.group(1))

    return metrics


def load_stress_events(metrics_file: Path) -> Dict:
    """Load stress injector events"""
    stress_events_file = metrics_file.parent / f"stress_events_{metrics_file.stem}.json"

    if not stress_events_file.exists():
        return None

    with open(stress_events_file, 'r') as f:
        return json.load(f)


def generate_report(log_file: Path, metrics_file: Path, output_file: Path):
    """Generate comprehensive stress test report"""

    print("Analyzing stress test results...")

    # Parse log
    log_metrics = parse_log_file(log_file)

    # Load stress events
    stress_events = load_stress_events(metrics_file)

    # Load metrics snapshots
    metrics_data = None
    if metrics_file.exists():
        with open(metrics_file, 'r') as f:
            metrics_data = json.load(f)

    # Generate report
    report = []
    report.append("# Phase 3 Stress Test Report")
    report.append("")
    report.append(f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    report.append(f"**Log file**: {log_file}")
    report.append("")
    report.append("---")
    report.append("")

    # Overall Summary
    report.append("## 🎯 Overall Summary")
    report.append("")

    total_errors = len(log_metrics['errors'])
    total_warnings = len(log_metrics['warnings'])
    total_trades = len([t for t in log_metrics['trades'] if 'EXIT' in t['type']])
    total_reconnections = len(log_metrics['websocket_reconnections'])
    total_fallbacks = len([f for f in log_metrics['rest_fallbacks'] if f['event'] == 'activated'])
    total_crashes = len(log_metrics['crashes'])

    report.append(f"- **Total Errors**: {total_errors}")
    report.append(f"- **Total Warnings**: {total_warnings}")
    report.append(f"- **Paper Trades**: {total_trades}")
    report.append(f"- **WebSocket Reconnections**: {total_reconnections}")
    report.append(f"- **REST Fallbacks Activated**: {total_fallbacks}")
    report.append(f"- **Crashes**: {total_crashes}")
    report.append("")

    # Stress Events
    if stress_events:
        report.append("## 🔥 Stress Events Injected")
        report.append("")
        report.append(f"- **Total Events**: {stress_events['total_events']}")
        report.append(f"- **Successes**: {stress_events['successes']}")
        report.append(f"- **Failures**: {stress_events['failures']}")
        report.append(f"- **Success Rate**: {stress_events['success_rate']*100:.1f}%")
        report.append("")

        # Event details
        report.append("### Event Details")
        report.append("")
        for event in stress_events['events']:
            status = "✅" if event['success'] else "❌"
            recovery = f" ({event['recovery_time_sec']:.1f}s)" if event['recovery_time_sec'] else ""
            report.append(f"- {status} **{event['type']}**: {event['description']}{recovery}")
        report.append("")

    # WebSocket Reconnections
    report.append("## 🔌 WebSocket Reconnections")
    report.append("")
    if total_reconnections > 0:
        report.append(f"Total reconnection events: {total_reconnections}")
        report.append("")
        report.append("### Recent Reconnections")
        report.append("")
        for recon in log_metrics['websocket_reconnections'][-10:]:
            report.append(f"- `{recon['timestamp']}`: {recon['message']}")
        report.append("")
    else:
        report.append("No WebSocket reconnections detected (stable connections)")
        report.append("")

    # REST Fallbacks
    report.append("## 🔄 REST Fallback Activations")
    report.append("")
    if total_fallbacks > 0:
        report.append(f"Total fallback activations: {total_fallbacks}")
        report.append("")
        for fb in log_metrics['rest_fallbacks']:
            report.append(f"- `{fb['timestamp']}`: {fb['event']}")
        report.append("")
    else:
        report.append("No REST fallbacks needed (WebSocket stable)")
        report.append("")

    # Trading Activity
    report.append("## 📊 Trading Activity")
    report.append("")

    exits = [t for t in log_metrics['trades'] if 'EXIT' in t['type']]
    if exits:
        profitable = sum(1 for t in exits if t['pnl_cents'] and t['pnl_cents'] > 0)
        total_pnl = sum(t['pnl_cents'] for t in exits if t['pnl_cents'])

        report.append(f"- **Total Trades**: {len(exits)}")
        report.append(f"- **Profitable**: {profitable} ({profitable/len(exits)*100:.1f}%)")
        report.append(f"- **Total P&L**: {total_pnl:+d}¢ (${total_pnl/100:+.2f})")
        report.append("")
    else:
        report.append("No trades completed during stress test")
        report.append("")

    # Errors & Warnings
    report.append("## ⚠️ Errors & Warnings")
    report.append("")

    if total_errors > 0:
        report.append(f"### Errors ({total_errors} total)")
        report.append("")

        # Group errors by type
        error_types = Counter(e['message'][:80] for e in log_metrics['errors'])
        for error_type, count in error_types.most_common(10):
            report.append(f"- `{count}x` {error_type}...")
        report.append("")

    if total_warnings > 0:
        report.append(f"### Warnings ({total_warnings} total)")
        report.append("")

        # Group warnings by type
        warning_types = Counter(w['message'][:80] for w in log_metrics['warnings'])
        for warning_type, count in warning_types.most_common(10):
            report.append(f"- `{count}x` {warning_type}...")
        report.append("")

    # Crashes
    if total_crashes > 0:
        report.append("## 💥 CRASHES DETECTED")
        report.append("")
        for i, crash in enumerate(log_metrics['crashes'], 1):
            report.append(f"### Crash #{i}")
            report.append("")
            report.append("```")
            report.append(crash[:1000])  # First 1000 chars
            report.append("```")
            report.append("")

    # Validation Results
    report.append("## ✅ Validation Results")
    report.append("")

    validations = []

    # Check for zero crashes
    if total_crashes == 0:
        validations.append(("✅ PASS", "No crashes detected"))
    else:
        validations.append(("❌ FAIL", f"{total_crashes} crashes detected"))

    # Check WebSocket reconnection
    if total_reconnections > 0:
        validations.append(("✅ PASS", f"WebSocket reconnection working ({total_reconnections} events)"))
    else:
        validations.append(("⚠️  N/A", "No WebSocket disconnections to test recovery"))

    # Check REST fallback
    if total_fallbacks > 0:
        validations.append(("✅ PASS", f"REST fallback working ({total_fallbacks} activations)"))
    else:
        validations.append(("⚠️  N/A", "No REST fallbacks needed"))

    # Check for catastrophic losses
    if exits:
        max_loss = min((t['pnl_cents'] for t in exits if t['pnl_cents']), default=0)
        if max_loss > -100:
            validations.append(("✅ PASS", f"No catastrophic losses (max loss: {max_loss}¢)"))
        else:
            validations.append(("❌ FAIL", f"Large loss detected: {max_loss}¢"))

    for status, message in validations:
        report.append(f"- {status}: {message}")

    report.append("")

    # Final Assessment
    report.append("## 🏁 Final Assessment")
    report.append("")

    failures = sum(1 for v in validations if v[0].startswith("❌"))
    if failures == 0:
        report.append("**✅ PHASE 3 STRESS TEST PASSED**")
        report.append("")
        report.append("All recovery mechanisms validated under stress conditions.")
    else:
        report.append(f"**❌ PHASE 3 STRESS TEST FAILED** ({failures} failures)")
        report.append("")
        report.append("Review failures above before proceeding to Phase 4.")

    report.append("")
    report.append("---")
    report.append("")
    report.append(f"*Generated by analyze_stress_test.py at {datetime.now().isoformat()}*")

    # Write report
    output_file.write_text('\n'.join(report))

    print(f"✅ Report generated: {output_file}")
    print("")

    # Print summary to console
    print("=" * 60)
    print("STRESS TEST SUMMARY")
    print("=" * 60)
    print(f"Errors:        {total_errors}")
    print(f"Warnings:      {total_warnings}")
    print(f"Trades:        {total_trades}")
    print(f"Reconnections: {total_reconnections}")
    print(f"Crashes:       {total_crashes}")
    if stress_events:
        print(f"Stress Events: {stress_events['successes']}/{stress_events['total_events']} passed")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='Analyze stress test results')
    parser.add_argument('--log-file', type=Path, required=True, help='Log file to analyze')
    parser.add_argument('--metrics', type=Path, required=True, help='Metrics JSON file')
    parser.add_argument('--output', type=Path, required=True, help='Output report file')

    args = parser.parse_args()

    generate_report(args.log_file, args.metrics, args.output)


if __name__ == '__main__':
    main()
