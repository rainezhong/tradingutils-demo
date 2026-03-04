#!/usr/bin/env python3
"""
Run Data Scout Agent on trading databases.

This script demonstrates how to use the Data Scout Agent to scan
databases for trading patterns and generate actionable hypotheses.

Usage:
    python3 scripts/run_data_scout.py [database_path]
    python3 scripts/run_data_scout.py --ticker KXBTC15M-26FEB180130-30
    python3 scripts/run_data_scout.py --pattern-type spread_anomaly
    python3 scripts/run_data_scout.py --export findings.json
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.data_scout import DataScoutAgent, Hypothesis


def main():
    parser = argparse.ArgumentParser(description="Run Data Scout Agent on trading databases")
    parser.add_argument(
        "database",
        nargs="?",
        default="data/btc_latency_probe.db",
        help="Path to database (default: data/btc_latency_probe.db)"
    )
    parser.add_argument(
        "--ticker",
        help="Analyze only this ticker"
    )
    parser.add_argument(
        "--pattern-type",
        choices=['spread_anomaly', 'price_movement', 'mean_reversion', 'momentum'],
        help="Only scan for this pattern type"
    )
    parser.add_argument(
        "--min-snapshots",
        type=int,
        default=100,
        help="Minimum snapshots required per ticker (default: 100)"
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        help="Minimum confidence threshold (0-1, default: 0.0)"
    )
    parser.add_argument(
        "--export",
        help="Export findings to JSON file"
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Show top N findings per pattern type (default: 10)"
    )

    args = parser.parse_args()

    print(f"\nData Scout Agent - Pattern Detection")
    print("=" * 80)
    print(f"Database: {args.database}")
    if args.ticker:
        print(f"Ticker: {args.ticker}")
    if args.pattern_type:
        print(f"Pattern Type: {args.pattern_type}")
    print(f"Min Confidence: {args.min_confidence:.2%}")
    print(f"Min Snapshots: {args.min_snapshots}")
    print("=" * 80 + "\n")

    # Create agent and scan
    with DataScoutAgent(args.database) as agent:
        if args.ticker and args.pattern_type:
            # Scan specific ticker and pattern type
            hypotheses = scan_specific(agent, args.ticker, args.pattern_type)
        elif args.ticker:
            # Scan all patterns for specific ticker
            hypotheses = scan_ticker(agent, args.ticker)
        elif args.pattern_type:
            # Scan specific pattern across all tickers
            hypotheses = scan_pattern_type(agent, args.pattern_type, args.min_snapshots)
        else:
            # Full scan
            hypotheses = agent.scan_for_patterns(min_snapshots=args.min_snapshots)

        # Filter by confidence
        hypotheses = [h for h in hypotheses if h.confidence >= args.min_confidence]

        # Display results
        display_results(hypotheses, args.top)

        # Export if requested
        if args.export:
            export_findings(hypotheses, args.export)
            print(f"\nExported {len(hypotheses)} findings to {args.export}")


def scan_specific(agent, ticker, pattern_type):
    """Scan specific ticker for specific pattern type."""
    print(f"Scanning {ticker} for {pattern_type} patterns...\n")

    if pattern_type == 'spread_anomaly':
        return agent.find_spread_anomalies(ticker)
    elif pattern_type == 'price_movement':
        return agent.find_price_movements(ticker)
    elif pattern_type == 'mean_reversion':
        return agent.find_mean_reversion(ticker)
    elif pattern_type == 'momentum':
        return agent.find_momentum(ticker)
    else:
        return []


def scan_ticker(agent, ticker):
    """Scan all pattern types for specific ticker."""
    print(f"Scanning {ticker} for all pattern types...\n")

    hypotheses = []
    hypotheses.extend(agent.find_spread_anomalies(ticker))
    hypotheses.extend(agent.find_price_movements(ticker))
    hypotheses.extend(agent.find_mean_reversion(ticker))
    hypotheses.extend(agent.find_momentum(ticker))

    # Sort by confidence
    hypotheses.sort(key=lambda h: h.confidence, reverse=True)

    return hypotheses


def scan_pattern_type(agent, pattern_type, min_snapshots):
    """Scan specific pattern type across all tickers."""
    print(f"Scanning all tickers for {pattern_type} patterns...\n")

    # Get all active tickers
    tickers = agent._get_active_tickers(min_snapshots)
    print(f"Found {len(tickers)} tickers with >={min_snapshots} snapshots")

    hypotheses = []
    for ticker in tickers:
        if pattern_type == 'spread_anomaly':
            hypotheses.extend(agent.find_spread_anomalies(ticker))
        elif pattern_type == 'price_movement':
            hypotheses.extend(agent.find_price_movements(ticker))
        elif pattern_type == 'mean_reversion':
            hypotheses.extend(agent.find_mean_reversion(ticker))
        elif pattern_type == 'momentum':
            hypotheses.extend(agent.find_momentum(ticker))

    # Sort by confidence
    hypotheses.sort(key=lambda h: h.confidence, reverse=True)

    return hypotheses


def display_results(hypotheses, top_n):
    """Display scan results."""
    if not hypotheses:
        print("No patterns detected.\n")
        return

    # Group by pattern type
    by_type = {}
    for h in hypotheses:
        by_type.setdefault(h.pattern_type, []).append(h)

    print(f"Found {len(hypotheses)} total patterns\n")

    for pattern_type, hyps in sorted(by_type.items()):
        print(f"\n{pattern_type.upper().replace('_', ' ')} ({len(hyps)} findings)")
        print("-" * 80)

        # Show top N
        for h in hyps[:top_n]:
            print(f"\n{h}")

        if len(hyps) > top_n:
            print(f"\n... and {len(hyps) - top_n} more")

    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("-" * 80)
    print(f"Total patterns: {len(hypotheses)}")

    for pattern_type, hyps in sorted(by_type.items()):
        avg_conf = sum(h.confidence for h in hyps) / len(hyps)
        avg_sig = sum(h.statistical_significance for h in hyps) / len(hyps)
        print(f"  {pattern_type:20s}: {len(hyps):4d} patterns "
              f"(avg confidence: {avg_conf:6.2%}, avg significance: {avg_sig:6.2f})")


def export_findings(hypotheses, output_path):
    """Export findings to JSON file."""
    findings = []

    for h in hypotheses:
        finding = {
            'pattern_type': h.pattern_type,
            'ticker': h.ticker,
            'description': h.description,
            'confidence': h.confidence,
            'statistical_significance': h.statistical_significance,
            'data_points': h.data_points,
            'timestamp': h.timestamp,
            'metadata': h.metadata
        }
        findings.append(finding)

    with open(output_path, 'w') as f:
        json.dump({
            'total_patterns': len(findings),
            'patterns': findings
        }, f, indent=2, default=str)


if __name__ == "__main__":
    main()
