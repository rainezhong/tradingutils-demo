#!/usr/bin/env python3
"""Calibrate fill rates from historical spread capture data.

This script analyzes historical trading data to extract:
1. Entry fill rates by spread bucket
2. Exit fill rates by spread bucket
3. Time-to-fill distributions
4. Recommended simulation parameters

Usage:
    python3 scripts/calibrate_fill_rates.py [--data-dir DATA_DIR]

Output:
    - Summary statistics to stdout
    - Recommended parameters for depth_base.py
"""

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FillEvent:
    """A single fill event from historical data."""

    timestamp: str
    ticker: str
    order_type: str  # "entry" or "exit"
    side: str  # "buy" or "sell"
    price: int
    size: int
    spread_at_entry: int
    mid_at_entry: float
    time_to_fill: float  # seconds from order placement to fill
    filled: bool
    trade_id: str


@dataclass
class TradeRecord:
    """Complete trade record tracking entry and exit."""

    trade_id: str
    ticker: str

    # Entry
    entry_price: Optional[int] = None
    entry_fill_price: Optional[int] = None
    entry_fill_size: int = 0
    entry_time: Optional[float] = None
    entry_fill_time: Optional[float] = None
    entry_filled: bool = False

    # Exit
    exit_price: Optional[int] = None
    exit_fill_price: Optional[int] = None
    exit_fill_size: int = 0
    exit_time: Optional[float] = None
    exit_fill_time: Optional[float] = None
    exit_filled: bool = False

    # Context
    spread_at_entry: int = 0
    mid_at_entry: float = 0.0
    hold_time: float = 0.0

    # Outcome
    net_pnl: float = 0.0
    state: str = "unknown"


def parse_timestamp(ts_str: str) -> float:
    """Parse ISO timestamp to epoch seconds."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.timestamp()
    except:
        return 0.0


def load_trades_from_jsonl(data_dir: Path) -> List[TradeRecord]:
    """Load and parse all trade records from JSONL files."""
    trades: Dict[str, TradeRecord] = {}
    events_by_trade: Dict[str, List[dict]] = defaultdict(list)

    # Find all spread capture data files
    files = list(data_dir.glob("depth_*.jsonl"))
    if not files:
        print(f"No data files found in {data_dir}", file=sys.stderr)
        return []

    print(f"Loading data from {len(files)} files...", file=sys.stderr)

    for filepath in sorted(files):
        with open(filepath, "r") as f:
            for line in f:
                try:
                    event = json.loads(line.strip())
                    trade_id = event.get("trade_id")
                    if trade_id:
                        events_by_trade[trade_id].append(event)
                except json.JSONDecodeError:
                    continue

    # Build trade records from events
    for trade_id, events in events_by_trade.items():
        trade = TradeRecord(trade_id=trade_id, ticker="")

        for event in events:
            event_type = event.get("type", "")

            if event_type == "spread_entry_attempt":
                trade.ticker = event.get("ticker", "")
                trade.entry_price = event.get("entry_price")
                trade.entry_time = parse_timestamp(event.get("timestamp", ""))
                trade.spread_at_entry = event.get("spread_at_entry", 0)

            elif event_type == "fill":
                # Determine if this is entry or exit fill
                side = event.get("side", "")
                ts = parse_timestamp(event.get("timestamp", ""))

                if side == "buy" and not trade.entry_filled:
                    trade.entry_fill_price = event.get("price")
                    trade.entry_fill_size = event.get("size", 0)
                    trade.entry_fill_time = ts
                    trade.entry_filled = True
                elif side == "sell":
                    trade.exit_fill_price = event.get("price")
                    trade.exit_fill_size = event.get("size", 0)
                    trade.exit_fill_time = ts
                    trade.exit_filled = True

            elif event_type == "spread_complete":
                trade.state = event.get("state", "closed")
                trade.net_pnl = event.get("net_pnl", 0.0)
                trade.hold_time = event.get("hold_time", 0.0)
                trade.entry_fill_price = event.get("entry_fill_price")
                trade.entry_fill_size = event.get("entry_fill_size", 0)
                trade.exit_fill_price = event.get("exit_fill_price")
                trade.exit_fill_size = event.get("exit_fill_size", 0)
                trade.spread_at_entry = event.get("spread_at_entry", 0)
                if trade.entry_fill_price:
                    trade.entry_filled = True
                if trade.exit_fill_price:
                    trade.exit_filled = True

            elif event_type == "spread_entry_timeout":
                trade.state = "entry_timeout"
                trade.entry_filled = False

            elif event_type in ("order_placed", "reprice"):
                # Track order times for time-to-fill calculation
                pass

        if trade.ticker:  # Only add trades with valid tickers
            trades[trade_id] = trade

    return list(trades.values())


def bucket_spread(spread: int) -> str:
    """Bucket spread into ranges for analysis."""
    if spread <= 5:
        return "0-5c"
    elif spread <= 10:
        return "6-10c"
    elif spread <= 15:
        return "11-15c"
    elif spread <= 20:
        return "16-20c"
    elif spread <= 25:
        return "21-25c"
    else:
        return "26c+"


def analyze_fill_rates(trades: List[TradeRecord]) -> Dict:
    """Analyze fill rates by order type and spread bucket."""

    results = {
        "entry": defaultdict(lambda: {"filled": 0, "total": 0, "times": []}),
        "exit": defaultdict(lambda: {"filled": 0, "total": 0, "times": []}),
    }

    for trade in trades:
        bucket = bucket_spread(trade.spread_at_entry)

        # Entry analysis
        if trade.entry_time:
            results["entry"][bucket]["total"] += 1
            if trade.entry_filled:
                results["entry"][bucket]["filled"] += 1
                if trade.entry_fill_time and trade.entry_time:
                    time_to_fill = trade.entry_fill_time - trade.entry_time
                    if time_to_fill > 0:
                        results["entry"][bucket]["times"].append(time_to_fill)

        # Exit analysis (only if entry filled)
        if trade.entry_filled:
            results["exit"][bucket]["total"] += 1
            if trade.exit_filled:
                results["exit"][bucket]["filled"] += 1
                if trade.exit_fill_time and trade.entry_fill_time:
                    time_to_exit = trade.exit_fill_time - trade.entry_fill_time
                    if time_to_exit > 0:
                        results["exit"][bucket]["times"].append(time_to_exit)

    return results


def calculate_hazard_rate(times: List[float], target_prob: float = 0.5) -> float:
    """Calculate hazard rate (λ) from time-to-fill data.

    Using exponential model: P(fill by t) = 1 - exp(-λt)
    Given median time t50: λ = -ln(0.5) / t50 ≈ 0.693 / t50
    """
    if not times:
        return 0.0

    times_sorted = sorted(times)
    median_idx = len(times_sorted) // 2
    median_time = times_sorted[median_idx]

    if median_time <= 0:
        return 0.0

    # λ = -ln(1 - target_prob) / median_time
    hazard_rate = -math.log(1 - target_prob) / median_time
    return hazard_rate


def estimate_parameters(results: Dict) -> Dict:
    """Estimate simulation parameters from fill rate data."""

    # Calculate overall rates
    entry_total_filled = sum(b["filled"] for b in results["entry"].values())
    entry_total_attempts = sum(b["total"] for b in results["entry"].values())
    exit_total_filled = sum(b["filled"] for b in results["exit"].values())
    exit_total_attempts = sum(b["total"] for b in results["exit"].values())

    entry_rate = (
        entry_total_filled / entry_total_attempts if entry_total_attempts > 0 else 0
    )
    exit_rate = (
        exit_total_filled / exit_total_attempts if exit_total_attempts > 0 else 0
    )

    # Estimate hazard rates from time-to-fill data
    entry_times = []
    exit_times = []
    for bucket_data in results["entry"].values():
        entry_times.extend(bucket_data["times"])
    for bucket_data in results["exit"].values():
        exit_times.extend(bucket_data["times"])

    entry_hazard = calculate_hazard_rate(entry_times)
    exit_hazard = calculate_hazard_rate(exit_times)

    # Calculate spread penalty by comparing exit rates across buckets
    spread_penalties = []
    baseline_bucket = "6-10c"  # Use 6-10c as baseline
    baseline_rate = results["exit"].get(baseline_bucket, {}).get("filled", 0)
    baseline_total = results["exit"].get(baseline_bucket, {}).get("total", 1)
    baseline_exit_rate = baseline_rate / baseline_total if baseline_total > 0 else 0

    for bucket, data in results["exit"].items():
        if data["total"] > 0 and bucket != baseline_bucket:
            bucket_rate = data["filled"] / data["total"]
            # Extract midpoint of bucket for regression
            if bucket == "0-5c":
                spread_mid = 2.5
            elif bucket == "6-10c":
                spread_mid = 8
            elif bucket == "11-15c":
                spread_mid = 13
            elif bucket == "16-20c":
                spread_mid = 18
            elif bucket == "21-25c":
                spread_mid = 23
            else:
                spread_mid = 28

            if baseline_exit_rate > 0 and bucket_rate > 0:
                # penalty per cent = (baseline_rate - bucket_rate) / (spread_mid - baseline_mid) / baseline_rate
                rate_diff = baseline_exit_rate - bucket_rate
                spread_diff = spread_mid - 8  # baseline_mid = 8
                if spread_diff != 0:
                    penalty_per_cent = rate_diff / abs(spread_diff) / baseline_exit_rate
                    spread_penalties.append(penalty_per_cent)

    avg_spread_penalty = (
        sum(spread_penalties) / len(spread_penalties) if spread_penalties else 0.04
    )

    return {
        "entry_fill_rate": entry_rate,
        "exit_fill_rate": exit_rate,
        "entry_hazard_rate": entry_hazard,
        "exit_hazard_rate": exit_hazard,
        "exit_spread_penalty_per_cent": avg_spread_penalty,
        "entry_total": entry_total_attempts,
        "exit_total": exit_total_attempts,
    }


def print_report(results: Dict, params: Dict):
    """Print analysis report and recommendations."""

    print("\n" + "=" * 70)
    print("SPREAD CAPTURE FILL RATE ANALYSIS")
    print("=" * 70)

    print("\n## Entry Fill Rates by Spread Bucket\n")
    print(
        f"{'Bucket':<12} {'Filled':<10} {'Total':<10} {'Rate':<10} {'Median Time':<12}"
    )
    print("-" * 54)

    for bucket in ["0-5c", "6-10c", "11-15c", "16-20c", "21-25c", "26c+"]:
        data = results["entry"].get(bucket, {"filled": 0, "total": 0, "times": []})
        if data["total"] > 0:
            rate = data["filled"] / data["total"]
            times = sorted(data["times"]) if data["times"] else []
            median_time = times[len(times) // 2] if times else 0
            print(
                f"{bucket:<12} {data['filled']:<10} {data['total']:<10} {rate:.1%}       {median_time:.1f}s"
            )

    print("\n## Exit Fill Rates by Spread Bucket\n")
    print(
        f"{'Bucket':<12} {'Filled':<10} {'Total':<10} {'Rate':<10} {'Median Time':<12}"
    )
    print("-" * 54)

    for bucket in ["0-5c", "6-10c", "11-15c", "16-20c", "21-25c", "26c+"]:
        data = results["exit"].get(bucket, {"filled": 0, "total": 0, "times": []})
        if data["total"] > 0:
            rate = data["filled"] / data["total"]
            times = sorted(data["times"]) if data["times"] else []
            median_time = times[len(times) // 2] if times else 0
            print(
                f"{bucket:<12} {data['filled']:<10} {data['total']:<10} {rate:.1%}       {median_time:.1f}s"
            )

    print("\n## Overall Statistics\n")
    print(f"Entry attempts: {params['entry_total']}")
    print(f"Entry fill rate: {params['entry_fill_rate']:.1%}")
    print(f"Exit attempts: {params['exit_total']}")
    print(f"Exit fill rate: {params['exit_fill_rate']:.1%}")
    print(
        f"Edge retention: {params['exit_fill_rate'] / params['entry_fill_rate']:.1%}"
        if params["entry_fill_rate"] > 0
        else "N/A"
    )

    print("\n## Recommended Parameters for depth_base.py\n")
    print("```python")
    print(
        f"passive_fill_rate = {params['entry_hazard_rate']:.4f}  # Entry hazard rate (per second)"
    )
    print(
        f"exit_passive_fill_rate = {params['exit_hazard_rate']:.4f}  # Exit hazard rate (per second)"
    )
    print(
        f"exit_spread_penalty_per_cent = {params['exit_spread_penalty_per_cent']:.4f}  # Rate reduction per cent of spread"
    )
    print(
        "exit_distance_penalty_per_cent = 0.02  # Rate reduction per cent from mid (default)"
    )
    print("```")

    print("\n## Expected Fill Rates with Recommended Parameters (60s timeout)\n")
    print(f"{'Spread':<12} {'Entry Fill':<15} {'Exit Fill':<15} {'Edge Retention':<15}")
    print("-" * 57)

    entry_rate = params["entry_hazard_rate"]
    exit_rate = params["exit_hazard_rate"]
    penalty = params["exit_spread_penalty_per_cent"]

    for spread in [10, 15, 20, 25]:
        # Entry: P = 1 - exp(-λ * t)
        entry_prob = 1 - math.exp(-entry_rate * 60)

        # Exit: apply spread penalty
        adj_exit_rate = exit_rate * max(0.05, 1.0 - spread * penalty)
        exit_prob = 1 - math.exp(-adj_exit_rate * 60)

        retention = exit_prob / entry_prob if entry_prob > 0 else 0
        print(
            f"{spread}c         {entry_prob:.1%}           {exit_prob:.1%}           {retention:.1%}"
        )

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Calibrate fill rates from historical data"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/spread_capture"),
        help="Directory containing spread capture JSONL files",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional: output parameters as JSON file",
    )

    args = parser.parse_args()

    # Resolve path relative to script location if needed
    if not args.data_dir.is_absolute():
        script_dir = Path(__file__).parent.parent
        args.data_dir = script_dir / args.data_dir

    if not args.data_dir.exists():
        print(f"Error: Data directory not found: {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    # Load and analyze data
    trades = load_trades_from_jsonl(args.data_dir)

    if not trades:
        print("No trades found in data files", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(trades)} trades", file=sys.stderr)

    results = analyze_fill_rates(trades)
    params = estimate_parameters(results)

    # Print report
    print_report(results, params)

    # Optionally output JSON
    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(params, f, indent=2)
        print(f"\nParameters written to {args.output_json}", file=sys.stderr)


if __name__ == "__main__":
    main()
