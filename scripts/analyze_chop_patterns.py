#!/usr/bin/env python3
"""
Analyze empirical timing patterns for crypto-scalp-chop strategy.

Detects BTC spot moves from Binance trades, tracks Kalshi price oscillations,
and measures time-to-peak/trough to generate empirical timing patterns.

Usage:
    python3 scripts/analyze_chop_patterns.py \
        --db data/btc_ob_48h.db \
        --output strategies/crypto_scalp_chop/empirical_patterns.json \
        --window 5.0 \
        --min-move 10.0 \
        --tracking-window 30.0
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import List, Dict, Tuple, Optional
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


@dataclass
class SpotMove:
    """Detected BTC spot price move"""
    ts: float
    direction: str  # "up" or "down"
    magnitude_usd: float
    start_price: float
    end_price: float


@dataclass
class KalshiOscillation:
    """Kalshi market oscillation after spot move"""
    ticker: str
    entry_ts: float
    entry_price: int  # cents
    peak_ts: float
    peak_price: int  # cents
    time_to_peak_ms: float
    overshoot_cents: int
    strike: float
    seconds_to_expiry: float


@dataclass
class PatternBucket:
    """Empirical pattern for a move magnitude bucket"""
    move_range: str  # e.g., "10-25"
    sample_count: int
    median_peak_lag_ms: float
    p75_peak_lag_ms: float
    p90_peak_lag_ms: float
    median_overshoot_cents: float
    p75_overshoot_cents: float
    p90_overshoot_cents: float
    mean_peak_lag_ms: float
    std_peak_lag_ms: float


def percentile(data: List[float], p: float) -> float:
    """Calculate percentile of data"""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p
    f = int(k)
    c = int(k) + 1
    if c >= len(sorted_data):
        return sorted_data[-1]
    d0 = sorted_data[f] * (c - k)
    d1 = sorted_data[c] * (k - f)
    return d0 + d1


def detect_spot_moves(
    conn: sqlite3.Connection,
    window_sec: float = 5.0,
    min_move_usd: float = 10.0,
) -> List[SpotMove]:
    """
    Detect BTC spot moves from Binance trades using sliding window.

    Args:
        conn: Database connection
        window_sec: Window size for detecting moves
        min_move_usd: Minimum move magnitude to detect

    Returns:
        List of detected spot moves
    """
    print(f"Detecting spot moves (window={window_sec}s, min_move={min_move_usd} USD)...")

    cursor = conn.cursor()
    cursor.execute("""
        SELECT ts, price
        FROM binance_trades
        ORDER BY ts
    """)

    moves = []
    window_trades = []  # (ts, price)
    last_move_ts = 0
    cooldown_sec = window_sec  # Don't detect overlapping moves

    for ts, price in cursor:
        # Add to window
        window_trades.append((ts, price))

        # Remove trades outside window
        cutoff = ts - window_sec
        window_trades = [(t, p) for t, p in window_trades if t >= cutoff]

        if len(window_trades) < 2:
            continue

        # Check if we're in cooldown
        if ts - last_move_ts < cooldown_sec:
            continue

        # Calculate price change over window
        start_price = window_trades[0][1]
        end_price = price
        delta = end_price - start_price

        if abs(delta) >= min_move_usd:
            direction = "up" if delta > 0 else "down"
            move = SpotMove(
                ts=ts,
                direction=direction,
                magnitude_usd=abs(delta),
                start_price=start_price,
                end_price=end_price,
            )
            moves.append(move)
            last_move_ts = ts
            window_trades = []  # Clear window after detecting move

    cursor.close()
    print(f"Detected {len(moves)} spot moves")
    return moves


def track_kalshi_oscillations(
    conn: sqlite3.Connection,
    spot_moves: List[SpotMove],
    tracking_window_sec: float = 30.0,
) -> Dict[str, List[KalshiOscillation]]:
    """
    Track Kalshi price oscillations after each spot move.

    For each spot move, finds all active markets and tracks their price
    movements for tracking_window_sec to identify peak/trough timing.

    Args:
        conn: Database connection
        spot_moves: Detected spot moves
        tracking_window_sec: How long to track after each move

    Returns:
        Dict mapping move_range bucket to list of oscillations
    """
    print(f"Tracking Kalshi oscillations (window={tracking_window_sec}s)...")

    oscillations_by_bucket = defaultdict(list)
    cursor = conn.cursor()

    for i, move in enumerate(spot_moves):
        if (i + 1) % 100 == 0:
            print(f"  Processing move {i+1}/{len(spot_moves)}...")

        # Query Kalshi snapshots in tracking window
        cursor.execute("""
            SELECT ts, ticker, yes_bid, yes_ask, yes_mid, floor_strike, seconds_to_close
            FROM kalshi_snapshots
            WHERE ts >= ? AND ts <= ?
            ORDER BY ticker, ts
        """, (move.ts, move.ts + tracking_window_sec))

        # Group by ticker
        markets = defaultdict(list)  # ticker -> [(ts, mid, strike, ttx)]
        for row in cursor:
            ts, ticker, yes_bid, yes_ask, yes_mid, strike, ttx = row
            if yes_mid is None or strike is None or ttx is None:
                continue
            markets[ticker].append((ts, yes_mid, strike, ttx))

        # Analyze each market
        for ticker, snapshots in markets.items():
            if len(snapshots) < 3:
                continue

            # Entry price = first snapshot mid
            entry_ts, entry_mid, strike, ttx = snapshots[0]
            entry_price = int(entry_mid)

            # Find peak/trough based on move direction
            if move.direction == "up":
                # For up moves, Kalshi should move down (lower prob -> lower price)
                # So trough is the peak of our opportunity
                prices = [int(mid) for _, mid, _, _ in snapshots]
                peak_idx = prices.index(min(prices))
            else:
                # For down moves, Kalshi should move up (higher prob -> higher price)
                # So peak is the peak of our opportunity
                prices = [int(mid) for _, mid, _, _ in snapshots]
                peak_idx = prices.index(max(prices))

            peak_ts, peak_mid, _, _ = snapshots[peak_idx]
            peak_price = int(peak_mid)

            # Calculate metrics
            time_to_peak_ms = (peak_ts - entry_ts) * 1000
            overshoot_cents = abs(peak_price - entry_price)

            # Bucket by move magnitude
            mag = move.magnitude_usd
            if mag < 25:
                bucket = "10-25"
            elif mag < 50:
                bucket = "25-50"
            elif mag < 100:
                bucket = "50-100"
            else:
                bucket = "100+"

            osc = KalshiOscillation(
                ticker=ticker,
                entry_ts=entry_ts,
                entry_price=entry_price,
                peak_ts=peak_ts,
                peak_price=peak_price,
                time_to_peak_ms=time_to_peak_ms,
                overshoot_cents=overshoot_cents,
                strike=strike,
                seconds_to_expiry=ttx,
            )
            oscillations_by_bucket[bucket].append(osc)

    cursor.close()

    # Print summary
    for bucket in ["10-25", "25-50", "50-100", "100+"]:
        count = len(oscillations_by_bucket[bucket])
        print(f"  {bucket} USD: {count} oscillations")

    return oscillations_by_bucket


def calculate_pattern_buckets(
    oscillations_by_bucket: Dict[str, List[KalshiOscillation]]
) -> Dict[str, PatternBucket]:
    """
    Calculate empirical pattern statistics for each bucket.

    Args:
        oscillations_by_bucket: Oscillations grouped by move magnitude

    Returns:
        Dict mapping bucket name to PatternBucket with statistics
    """
    print("Calculating pattern statistics...")

    patterns = {}

    for bucket_name, oscillations in oscillations_by_bucket.items():
        if not oscillations:
            continue

        # Extract timing and overshoot data
        timings = [osc.time_to_peak_ms for osc in oscillations]
        overshoots = [osc.overshoot_cents for osc in oscillations]

        # Calculate statistics
        pattern = PatternBucket(
            move_range=bucket_name,
            sample_count=len(oscillations),
            median_peak_lag_ms=percentile(timings, 0.5),
            p75_peak_lag_ms=percentile(timings, 0.75),
            p90_peak_lag_ms=percentile(timings, 0.90),
            median_overshoot_cents=percentile(overshoots, 0.5),
            p75_overshoot_cents=percentile(overshoots, 0.75),
            p90_overshoot_cents=percentile(overshoots, 0.90),
            mean_peak_lag_ms=sum(timings) / len(timings) if timings else 0.0,
            std_peak_lag_ms=(
                (sum((x - sum(timings) / len(timings)) ** 2 for x in timings) / len(timings)) ** 0.5
                if len(timings) > 1 else 0.0
            ),
        )
        patterns[bucket_name] = pattern

        # Print summary
        print(f"\n  {bucket_name} USD ({pattern.sample_count} samples):")
        print(f"    Peak timing: median={pattern.median_peak_lag_ms:.0f}ms, "
              f"p75={pattern.p75_peak_lag_ms:.0f}ms, p90={pattern.p90_peak_lag_ms:.0f}ms")
        print(f"    Overshoot: median={pattern.median_overshoot_cents:.1f}¢, "
              f"p75={pattern.p75_overshoot_cents:.1f}¢, p90={pattern.p90_overshoot_cents:.1f}¢")

    return patterns


def main():
    parser = argparse.ArgumentParser(description="Analyze chop patterns from historical data")
    parser.add_argument(
        "--db",
        default="data/btc_ob_48h.db",
        help="Path to database (default: data/btc_ob_48h.db)",
    )
    parser.add_argument(
        "--output",
        default="strategies/crypto_scalp_chop/empirical_patterns.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--window",
        type=float,
        default=5.0,
        help="Window size for spot move detection (seconds, default: 5.0)",
    )
    parser.add_argument(
        "--min-move",
        type=float,
        default=10.0,
        help="Minimum spot move magnitude (USD, default: 10.0)",
    )
    parser.add_argument(
        "--tracking-window",
        type=float,
        default=30.0,
        help="How long to track Kalshi after spot move (seconds, default: 30.0)",
    )

    args = parser.parse_args()

    # Connect to database
    print(f"Opening database: {args.db}")
    conn = sqlite3.connect(args.db)

    # Step 1: Detect spot moves
    spot_moves = detect_spot_moves(
        conn,
        window_sec=args.window,
        min_move_usd=args.min_move,
    )

    if not spot_moves:
        print("ERROR: No spot moves detected!")
        return 1

    # Step 2: Track Kalshi oscillations
    oscillations_by_bucket = track_kalshi_oscillations(
        conn,
        spot_moves,
        tracking_window_sec=args.tracking_window,
    )

    # Step 3: Calculate pattern statistics
    patterns = calculate_pattern_buckets(oscillations_by_bucket)

    if not patterns:
        print("ERROR: No patterns generated!")
        return 1

    # Step 4: Save to JSON
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_data = {
        "metadata": {
            "database": args.db,
            "window_sec": args.window,
            "min_move_usd": args.min_move,
            "tracking_window_sec": args.tracking_window,
            "total_spot_moves": len(spot_moves),
            "total_oscillations": sum(len(oscs) for oscs in oscillations_by_bucket.values()),
        },
        "patterns": {
            bucket_name: asdict(pattern)
            for bucket_name, pattern in patterns.items()
        },
    }

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\n✅ Patterns saved to: {output_path}")
    print(f"   Total spot moves: {len(spot_moves)}")
    print(f"   Total oscillations: {output_data['metadata']['total_oscillations']}")
    print(f"   Buckets: {list(patterns.keys())}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
