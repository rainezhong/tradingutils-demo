#!/usr/bin/env python3
"""
Backtest Using Collected Spread Data

Run backtests on spread data collected by collect_spreads.py.

Usage:
    python scripts/backtest_collected.py                          # Backtest all pairs
    python scripts/backtest_collected.py --pair TICKER1:TICKER2   # Specific pair
    python scripts/backtest_collected.py --list                   # List available pairs
    python scripts/backtest_collected.py --export spreads.csv     # Export to CSV
"""

import sys
import os
import argparse
import math
from datetime import datetime
from typing import List, Dict, Any

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def print_header(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")


def analyze_pair_history(history: List[Dict[str, Any]], pair_id: str) -> Dict[str, Any]:
    """Analyze spread history for a single pair."""
    if not history:
        return {"pair_id": pair_id, "error": "No data"}

    # Extract metrics
    edges = [h["dutch_edge"] for h in history if h["dutch_edge"] is not None]
    combined = [h["combined_yes_ask"] for h in history if h["combined_yes_ask"] is not None]

    if not edges:
        return {"pair_id": pair_id, "error": "No valid quotes"}

    # Find opportunities (positive edge)
    opportunities = [e for e in edges if e > 0]
    near_opportunities = [e for e in edges if e > -0.01]  # Within 1%

    # Calculate stats
    return {
        "pair_id": pair_id,
        "snapshots": len(history),
        "time_range": f"{history[0]['timestamp']} to {history[-1]['timestamp']}",
        "avg_edge": sum(edges) / len(edges),
        "max_edge": max(edges),
        "min_edge": min(edges),
        "avg_combined": sum(combined) / len(combined) if combined else None,
        "opportunities": len(opportunities),
        "opportunity_pct": 100 * len(opportunities) / len(edges),
        "near_opportunities": len(near_opportunities),
        "near_opportunity_pct": 100 * len(near_opportunities) / len(edges),
    }


def backtest_pair(history: List[Dict[str, Any]], threshold: float = 0.0) -> Dict[str, Any]:
    """
    Simulate trading strategy on collected data.

    Strategy: Enter when dutch_edge > threshold, exit at settlement ($1).
    """
    if not history:
        return {"error": "No data"}

    trades = []
    total_profit = 0

    for h in history:
        edge = h.get("dutch_edge")
        if edge is not None and edge > threshold:
            # Would enter this trade
            combined = h.get("combined_yes_ask", 1.0)
            profit = 1.0 - combined  # Profit per contract at settlement
            trades.append({
                "timestamp": h["timestamp"],
                "combined_ask": combined,
                "profit_per_contract": profit,
            })
            total_profit += profit

    return {
        "threshold": threshold,
        "num_trades": len(trades),
        "total_profit_per_contract": total_profit,
        "avg_profit_per_trade": total_profit / len(trades) if trades else 0,
        "trades": trades[:10],  # First 10 for display
    }


def main():
    parser = argparse.ArgumentParser(description="Backtest Collected Spread Data")
    parser.add_argument("--db", type=str, default="data/spreads.db", help="Database path")
    parser.add_argument("--pair", type=str, help="Specific pair ID (TICKER_A:TICKER_B)")
    parser.add_argument("--list", action="store_true", help="List available pairs")
    parser.add_argument("--export", type=str, metavar="FILE", help="Export to CSV")
    parser.add_argument("--threshold", type=float, default=0.0, help="Entry threshold for backtest")
    parser.add_argument("--start", type=str, help="Start time (ISO format)")
    parser.add_argument("--end", type=str, help="End time (ISO format)")
    parser.add_argument("--no-plot", action="store_true", help="Skip plots")

    args = parser.parse_args()

    from arb.spread_collector import (
        load_spread_history,
        list_collected_pairs,
        get_collection_stats,
    )

    # List pairs
    if args.list:
        print_header("AVAILABLE PAIRS")
        pairs = list_collected_pairs(args.db)
        if not pairs:
            print("  No pairs in database. Run collect_spreads.py first.")
            return

        stats = get_collection_stats(args.db)
        print(f"Database: {args.db}")
        print(f"Total snapshots: {stats['num_snapshots']}")
        print(f"Time range: {stats['first_snapshot']} to {stats['last_snapshot']}")
        print(f"\nPairs ({len(pairs)}):\n")

        for p in pairs:
            print(f"  {p['pair_id']}")
            print(f"    {p['event_title']} ({p['match_type']})")
        return

    # Export to CSV
    if args.export:
        print(f"Exporting to {args.export}...")
        history = load_spread_history(
            args.db,
            pair_id=args.pair,
            start_time=args.start,
            end_time=args.end,
        )

        if not history:
            print("No data to export")
            return

        import csv
        with open(args.export, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=history[0].keys())
            writer.writeheader()
            writer.writerows(history)

        print(f"Exported {len(history)} rows to {args.export}")
        return

    # Backtest
    print_header("SPREAD BACKTEST")

    stats = get_collection_stats(args.db)
    print(f"Database: {args.db}")
    print(f"Total snapshots: {stats['num_snapshots']}")

    if stats['num_snapshots'] == 0:
        print("\nNo data collected yet. Run:")
        print("  python scripts/collect_spreads.py --once")
        return

    print(f"Time range: {stats['first_snapshot']} to {stats['last_snapshot']}")

    # Get pairs to analyze
    if args.pair:
        pairs_to_analyze = [{"pair_id": args.pair}]
    else:
        pairs_to_analyze = list_collected_pairs(args.db)

    print(f"\nAnalyzing {len(pairs_to_analyze)} pairs...")
    print(f"Entry threshold: ${args.threshold:.4f}\n")

    results = []

    for p in pairs_to_analyze:
        pair_id = p["pair_id"]
        history = load_spread_history(
            args.db,
            pair_id=pair_id,
            start_time=args.start,
            end_time=args.end,
        )

        if not history:
            continue

        # Analyze
        analysis = analyze_pair_history(history, pair_id)
        backtest = backtest_pair(history, threshold=args.threshold)

        results.append({
            "pair_id": pair_id,
            "event_title": p.get("event_title", ""),
            **analysis,
            **backtest,
        })

    # Print results
    print("-" * 70)
    print(f"{'Pair':<40} {'Snaps':>6} {'Avg Edge':>10} {'Max Edge':>10} {'Opps':>6}")
    print("-" * 70)

    for r in sorted(results, key=lambda x: x.get("max_edge", -999), reverse=True):
        if "error" in r:
            continue
        pair_short = r["pair_id"][:38]
        print(f"{pair_short:<40} {r['snapshots']:>6} ${r['avg_edge']:>+8.4f} ${r['max_edge']:>+8.4f} {r['opportunities']:>6}")

    # Summary
    print("-" * 70)

    total_opps = sum(r.get("opportunities", 0) for r in results)
    total_snaps = sum(r.get("snapshots", 0) for r in results)
    best_edge = max((r.get("max_edge", -999) for r in results), default=0)

    print(f"\nSummary:")
    print(f"  Total snapshots analyzed: {total_snaps}")
    print(f"  Total opportunities (edge > 0): {total_opps}")
    print(f"  Best edge seen: ${best_edge:+.4f}/contract")

    if total_opps > 0:
        print(f"\n  ** Found {total_opps} arbitrage opportunities in historical data! **")

    # Plot if requested
    if not args.no_plot and args.pair and results:
        try:
            import matplotlib.pyplot as plt

            history = load_spread_history(args.db, pair_id=args.pair)
            if history:
                timestamps = [h["timestamp"] for h in history]
                edges = [h["dutch_edge"] or 0 for h in history]
                combined = [h["combined_yes_ask"] or 1 for h in history]

                fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)

                # Edge over time
                axes[0].plot(range(len(edges)), edges, label="Dutch Edge")
                axes[0].axhline(0, color="gray", linestyle="-", alpha=0.5)
                axes[0].axhline(args.threshold, color="green", linestyle="--", label=f"Threshold ({args.threshold})")
                axes[0].fill_between(range(len(edges)), 0, edges, where=[e > 0 for e in edges], alpha=0.3, color="green")
                axes[0].set_ylabel("Edge ($/contract)")
                axes[0].set_title(f"Spread History: {args.pair}")
                axes[0].legend()

                # Combined ask over time
                axes[1].plot(range(len(combined)), combined, label="Combined YES Ask", color="orange")
                axes[1].axhline(1.0, color="gray", linestyle="-", alpha=0.5)
                axes[1].set_ylabel("Combined Ask ($)")
                axes[1].set_xlabel("Snapshot #")
                axes[1].legend()

                plt.tight_layout()
                plt.show()
        except ImportError:
            print("\nMatplotlib not available for plotting")
        except Exception as e:
            print(f"\nCould not plot: {e}")


if __name__ == "__main__":
    main()
