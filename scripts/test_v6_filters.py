#!/usr/bin/env python3
"""Test v6 filters on probe data to validate signal accuracy improvement.

Compares OLD (v5) vs NEW (v6) filter performance.
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple, Dict
import numpy as np
from collections import deque

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_binance_trades(db_path: Path) -> List[Tuple]:
    """Load Binance trade data."""
    conn = sqlite3.connect(str(db_path))
    for table in ['binance_trades', 'binance_l2']:
        try:
            # Try with price column
            query = f"SELECT ts, price, qty FROM {table} ORDER BY ts"
            try:
                rows = conn.execute(query).fetchall()
                if rows:
                    conn.close()
                    return rows
            except:
                # Try with mid_price
                query = f"SELECT ts, mid_price as price, 0 as qty FROM {table} ORDER BY ts"
                rows = conn.execute(query).fetchall()
                if rows:
                    conn.close()
                    return rows
        except sqlite3.OperationalError:
            continue
    conn.close()
    return []


def load_kalshi_snapshots(db_path: Path) -> List[Tuple]:
    """Load Kalshi snapshots."""
    conn = sqlite3.connect(str(db_path))
    for strike_col in ['strike', 'floor_strike']:
        try:
            query = f"""
                SELECT ts, ticker, yes_mid, yes_bid, yes_ask, {strike_col} as strike
                FROM kalshi_snapshots
                WHERE yes_mid IS NOT NULL
                ORDER BY ts
            """
            rows = conn.execute(query).fetchall()
            if rows:
                conn.close()
                return rows
        except sqlite3.OperationalError:
            continue
    conn.close()
    return []


def detect_moves_with_filters(
    trades: List[Tuple],
    window_sec: float = 5.0,
    min_move_usd: float = 15.0,
    momentum_min: float = 0.8,
    momentum_max: float = 999.0,  # v6: max 5.0
    min_volume: float = 0.0,
    min_concentration: float = 0.0,
) -> List[Dict]:
    """Detect BTC moves with configurable filters."""

    moves = []
    window_trades = deque()

    for trade in trades:
        ts, price, qty = trade
        window_trades.append((ts, price, qty))

        # Trim old trades
        while window_trades and window_trades[0][0] < ts - window_sec:
            window_trades.popleft()

        if len(window_trades) < 4:
            continue

        trades_list = list(window_trades)
        start_price = trades_list[0][1]
        end_price = trades_list[-1][1]
        delta = end_price - start_price

        if abs(delta) < min_move_usd:
            continue

        # Momentum calculation
        mid = len(trades_list) // 2
        older_half = trades_list[:mid]
        recent_half = trades_list[mid:]

        if len(older_half) < 1 or len(recent_half) < 1:
            continue

        older_delta = older_half[-1][1] - older_half[0][1]
        recent_delta = recent_half[-1][1] - recent_half[0][1]

        # Direction check
        same_direction = (
            (delta > 0 and recent_delta > 0) or
            (delta < 0 and recent_delta < 0)
        )

        if not same_direction:
            continue

        # Momentum ratio
        if abs(older_delta) < 0.01:
            momentum_ratio = 0.0
        else:
            momentum_ratio = abs(recent_delta) / abs(older_delta)

        # Apply momentum filters
        if momentum_ratio < momentum_min:
            continue

        if momentum_ratio > momentum_max:  # NEW v6 filter!
            continue

        # Volume filters
        total_volume = sum(t[2] for t in trades_list)
        if total_volume < min_volume:
            continue

        # Concentration filter
        max_trade = max(t[2] for t in trades_list) if trades_list else 0
        concentration = max_trade / total_volume if total_volume > 0 else 0
        if concentration < min_concentration:
            continue

        moves.append({
            'ts': ts,
            'price_delta': delta,
            'momentum_ratio': momentum_ratio,
            'volume': total_volume,
            'concentration': concentration,
        })

    # Deduplicate
    unique = []
    last_ts = 0
    for m in moves:
        if m['ts'] - last_ts > window_sec / 2:
            unique.append(m)
            last_ts = m['ts']

    return unique


def check_kalshi_responses(moves: List[Dict], kalshi_snaps: List[Tuple]) -> Dict:
    """Check how many moves Kalshi followed."""

    true_positives = []
    false_positives = []

    for move in moves:
        move_ts = move['ts']
        move_price = move['price_delta'] + 100000  # Approximate end price

        responded = False

        for snap in kalshi_snaps:
            snap_ts, ticker, yes_mid, yes_bid, yes_ask, strike = snap

            if snap_ts < move_ts or snap_ts > move_ts + 30:
                continue

            # Find baseline
            baseline = None
            for s in kalshi_snaps:
                if s[1] == ticker and s[0] <= move_ts:
                    baseline = s[2]
                elif s[0] > move_ts:
                    break

            if baseline is None:
                continue

            # Check for response
            kalshi_move = yes_mid - baseline

            if abs(kalshi_move) >= 3:  # At least 3¢ move
                responded = True
                break

        if responded:
            true_positives.append(move)
        else:
            false_positives.append(move)

    return {
        'total': len(moves),
        'true_positives': len(true_positives),
        'false_positives': len(false_positives),
        'accuracy': len(true_positives) / len(moves) if moves else 0,
        'tp_data': true_positives,
        'fp_data': false_positives,
    }


def main():
    parser = argparse.ArgumentParser(description="Test v6 filters")
    parser.add_argument('--db', type=Path, default=Path('data/btc_march3_overnight.db'))
    args = parser.parse_args()

    print(f"Testing v6 filters on {args.db}")
    print("="*70)

    # Load data
    print("\nLoading data...")
    binance = load_binance_trades(args.db)
    kalshi = load_kalshi_snapshots(args.db)
    print(f"Loaded {len(binance)} Binance trades, {len(kalshi)} Kalshi snapshots")

    # Test OLD filters (v5)
    print("\n" + "="*70)
    print("OLD FILTERS (v5)")
    print("="*70)
    print("Settings:")
    print("  min_move: $15")
    print("  momentum_min: 0.8")
    print("  momentum_max: NONE (999)")
    print("  min_volume: 0 BTC")
    print("  min_concentration: 0%")

    old_moves = detect_moves_with_filters(
        binance,
        min_move_usd=15.0,
        momentum_min=0.8,
        momentum_max=999.0,  # No max filter
        min_volume=0.0,
        min_concentration=0.0,
    )

    old_results = check_kalshi_responses(old_moves, kalshi)

    print(f"\nResults:")
    print(f"  Moves detected: {old_results['total']}")
    print(f"  True positives: {old_results['true_positives']}")
    print(f"  False positives: {old_results['false_positives']}")
    print(f"  Accuracy: {old_results['accuracy']*100:.1f}%")

    if old_results['tp_data']:
        tp_momentum = np.mean([m['momentum_ratio'] for m in old_results['tp_data']])
        print(f"  Avg TP momentum: {tp_momentum:.2f}x")
    if old_results['fp_data']:
        fp_momentum = np.mean([m['momentum_ratio'] for m in old_results['fp_data']])
        print(f"  Avg FP momentum: {fp_momentum:.2f}x")

    # Test NEW filters (v6)
    print("\n" + "="*70)
    print("NEW FILTERS (v6)")
    print("="*70)
    print("Settings:")
    print("  min_move: $22 (increased from $15)")
    print("  momentum_min: 0.8")
    print("  momentum_max: 5.0 (NEW - reject whipsaw)")
    print("  min_volume: 2.0 BTC (increased from 0)")
    print("  min_concentration: 15% (NEW - institutional sweep)")

    new_moves = detect_moves_with_filters(
        binance,
        min_move_usd=22.0,
        momentum_min=0.8,
        momentum_max=5.0,  # NEW!
        min_volume=2.0,
        min_concentration=0.15,
    )

    new_results = check_kalshi_responses(new_moves, kalshi)

    print(f"\nResults:")
    print(f"  Moves detected: {new_results['total']}")
    print(f"  True positives: {new_results['true_positives']}")
    print(f"  False positives: {new_results['false_positives']}")
    print(f"  Accuracy: {new_results['accuracy']*100:.1f}%")

    if new_results['tp_data']:
        tp_momentum = np.mean([m['momentum_ratio'] for m in new_results['tp_data']])
        print(f"  Avg TP momentum: {tp_momentum:.2f}x")
    if new_results['fp_data']:
        fp_momentum = np.mean([m['momentum_ratio'] for m in new_results['fp_data']])
        print(f"  Avg FP momentum: {fp_momentum:.2f}x")

    # Comparison
    print("\n" + "="*70)
    print("COMPARISON: v5 → v6")
    print("="*70)

    signal_reduction = (1 - new_results['total'] / old_results['total']) * 100 if old_results['total'] > 0 else 0
    accuracy_improvement = (new_results['accuracy'] - old_results['accuracy']) * 100
    fp_reduction = (1 - new_results['false_positives'] / old_results['false_positives']) * 100 if old_results['false_positives'] > 0 else 0

    print(f"  Total signals: {old_results['total']} → {new_results['total']} ({signal_reduction:+.1f}%)")
    print(f"  Accuracy: {old_results['accuracy']*100:.1f}% → {new_results['accuracy']*100:.1f}% ({accuracy_improvement:+.1f}pp)")
    print(f"  False positives: {old_results['false_positives']} → {new_results['false_positives']} ({fp_reduction:+.1f}%)")
    print(f"  True positives: {old_results['true_positives']} → {new_results['true_positives']}")

    print("\n" + "="*70)
    print("VERDICT")
    print("="*70)

    if new_results['accuracy'] > old_results['accuracy'] + 0.1:
        print("✅ MAJOR IMPROVEMENT - v6 filters significantly better!")
    elif new_results['accuracy'] > old_results['accuracy']:
        print("✓ IMPROVEMENT - v6 filters better")
    else:
        print("⚠️  NO IMPROVEMENT - v6 filters not helping")

    return 0


if __name__ == '__main__':
    sys.exit(main())
