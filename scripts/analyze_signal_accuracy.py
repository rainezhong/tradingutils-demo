#!/usr/bin/env python3
"""Analyze signal accuracy: what % of detected BTC moves result in Kalshi repricing?

This is the CRITICAL question for trend detection:
- How many $15+ BTC moves do we detect?
- What % does Kalshi follow within 30s?
- What characteristics predict if Kalshi will follow?

Usage:
    python3 scripts/analyze_signal_accuracy.py --db data/btc_march3_overnight.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import numpy as np
from datetime import datetime
from collections import defaultdict, deque

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_binance_trades(db_path: Path) -> List[Tuple]:
    """Load Binance trade data (ts, price, qty)."""
    conn = sqlite3.connect(str(db_path))

    # Try different table names
    for table in ['binance_trades', 'binance_l2']:
        try:
            query = f"SELECT ts, price, qty FROM {table} ORDER BY ts"
            # Try with price column
            try:
                rows = conn.execute(query).fetchall()
                if rows:
                    conn.close()
                    return rows
            except:
                # Try with mid_price column
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
    """Load Kalshi snapshots (ts, ticker, yes_mid, yes_bid, yes_ask, strike)."""
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


def detect_btc_moves(
    trades: List[Tuple],
    window_sec: float = 5.0,
    min_move_usd: float = 15.0,
    momentum_threshold: float = 0.8
) -> List[Dict]:
    """Detect significant BTC moves using strategy's actual logic.

    Returns list of detected moves with:
    - timestamp
    - price_delta
    - direction
    - momentum_ratio (recent_half / older_half)
    - volume
    - num_trades
    """
    moves = []

    # Sliding window detector
    window_trades = deque()

    for trade in trades:
        ts, price, qty = trade

        # Add to window
        window_trades.append((ts, price, qty))

        # Trim old trades
        while window_trades and window_trades[0][0] < ts - window_sec:
            window_trades.popleft()

        if len(window_trades) < 4:
            continue

        # Calculate metrics
        trades_list = list(window_trades)
        start_price = trades_list[0][1]
        end_price = trades_list[-1][1]
        delta = end_price - start_price

        if abs(delta) < min_move_usd:
            continue

        # Momentum filter (split in half)
        mid = len(trades_list) // 2
        older_half = trades_list[:mid]
        recent_half = trades_list[mid:]

        older_delta = older_half[-1][1] - older_half[0][1]
        recent_delta = recent_half[-1][1] - recent_half[0][1]

        # Check momentum ratio
        if abs(older_delta) < 0.01:
            momentum_ratio = 0
        else:
            momentum_ratio = abs(recent_delta) / abs(older_delta)

        # Check direction consistency
        same_direction = (
            (delta > 0 and recent_delta > 0) or
            (delta < 0 and recent_delta < 0)
        )

        # Apply momentum threshold
        passes_momentum = (
            same_direction and
            momentum_ratio >= momentum_threshold
        )

        # Volume
        total_volume = sum(t[2] for t in trades_list)

        # Record the move
        moves.append({
            'ts': ts,
            'price_delta': delta,
            'direction': 1 if delta > 0 else -1,
            'start_price': start_price,
            'end_price': end_price,
            'momentum_ratio': momentum_ratio,
            'passes_momentum': passes_momentum,
            'volume': total_volume,
            'num_trades': len(trades_list),
        })

    # Deduplicate (moves detected multiple times in sliding window)
    unique_moves = []
    last_ts = 0
    for move in moves:
        if move['ts'] - last_ts > window_sec / 2:  # At least half-window apart
            unique_moves.append(move)
            last_ts = move['ts']

    return unique_moves


def check_kalshi_response(
    move: Dict,
    kalshi_snaps: List[Tuple],
    response_window_sec: float = 30.0,
    min_reprice_cents: int = 3
) -> Optional[Dict]:
    """Check if Kalshi repriced in response to a BTC move.

    Args:
        move: Detected BTC move
        kalshi_snaps: All Kalshi snapshots
        response_window_sec: How long to wait for Kalshi response
        min_reprice_cents: Minimum Kalshi move to count as response

    Returns:
        Dict with response info, or None if no response
    """
    move_ts = move['ts']
    move_direction = move['direction']
    move_price = move['end_price']

    # Find Kalshi snapshots in the response window
    responses = {}

    for snap in kalshi_snaps:
        snap_ts, ticker, yes_mid, yes_bid, yes_ask, strike = snap

        # Skip if before move
        if snap_ts < move_ts:
            continue

        # Stop if beyond window
        if snap_ts > move_ts + response_window_sec:
            break

        # Track first snapshot per ticker (baseline)
        if ticker not in responses:
            # Find baseline (last snapshot before move)
            baseline_mid = None
            for s in kalshi_snaps:
                if s[1] == ticker and s[0] <= move_ts:
                    baseline_mid = s[2]  # yes_mid
                elif s[0] > move_ts:
                    break

            if baseline_mid is None:
                continue

            responses[ticker] = {
                'baseline_mid': baseline_mid,
                'baseline_ts': move_ts,
                'strike': strike,
                'responded': False,
                'response_lag': None,
                'response_magnitude': None,
            }

        # Check if this snapshot shows a response
        resp = responses[ticker]
        kalshi_move = yes_mid - resp['baseline_mid']

        # Determine expected direction based on BTC vs strike
        btc_above_strike = move_price > strike
        expected_kalshi_dir = 1 if btc_above_strike else -1

        # Check if Kalshi moved in expected direction
        if expected_kalshi_dir == 1 and kalshi_move >= min_reprice_cents:
            if not resp['responded']:
                resp['responded'] = True
                resp['response_lag'] = snap_ts - move_ts
                resp['response_magnitude'] = kalshi_move
        elif expected_kalshi_dir == -1 and kalshi_move <= -min_reprice_cents:
            if not resp['responded']:
                resp['responded'] = True
                resp['response_lag'] = snap_ts - move_ts
                resp['response_magnitude'] = abs(kalshi_move)

    # Return aggregate response
    responded_tickers = [t for t, r in responses.items() if r['responded']]

    if not responded_tickers:
        return None

    # Average response across all markets
    avg_lag = np.mean([responses[t]['response_lag'] for t in responded_tickers])
    avg_magnitude = np.mean([responses[t]['response_magnitude'] for t in responded_tickers])

    return {
        'num_markets_responded': len(responded_tickers),
        'total_markets': len(responses),
        'avg_lag': avg_lag,
        'avg_magnitude': avg_magnitude,
        'response_rate': len(responded_tickers) / len(responses) if responses else 0,
    }


def analyze_signal_accuracy(
    btc_moves: List[Dict],
    kalshi_snaps: List[Tuple]
) -> Dict:
    """Analyze what % of detected BTC moves result in Kalshi repricing."""

    print(f"\nAnalyzing signal accuracy for {len(btc_moves)} detected BTC moves...")

    results = {
        'total_moves': len(btc_moves),
        'moves_with_response': 0,
        'moves_without_response': 0,
        'moves_with_momentum_filter': 0,
        'moves_without_momentum_filter': 0,
        'filtered_moves_with_response': 0,
        'unfiltered_moves_with_response': 0,
    }

    true_positives = []  # Kalshi followed
    false_positives = []  # Kalshi ignored

    for move in btc_moves:
        response = check_kalshi_response(move, kalshi_snaps)

        if response and response['num_markets_responded'] > 0:
            results['moves_with_response'] += 1
            true_positives.append({**move, **response})
        else:
            results['moves_without_response'] += 1
            false_positives.append(move)

        # Track momentum filter effectiveness
        if move['passes_momentum']:
            results['moves_with_momentum_filter'] += 1
            if response and response['num_markets_responded'] > 0:
                results['filtered_moves_with_response'] += 1
        else:
            results['moves_without_momentum_filter'] += 1
            if response and response['num_markets_responded'] > 0:
                results['unfiltered_moves_with_response'] += 1

    # Calculate accuracy metrics
    if results['total_moves'] > 0:
        results['overall_accuracy'] = results['moves_with_response'] / results['total_moves']

    if results['moves_with_momentum_filter'] > 0:
        results['filtered_accuracy'] = results['filtered_moves_with_response'] / results['moves_with_momentum_filter']
    else:
        results['filtered_accuracy'] = 0

    if results['moves_without_momentum_filter'] > 0:
        results['unfiltered_accuracy'] = results['unfiltered_moves_with_response'] / results['moves_without_momentum_filter']
    else:
        results['unfiltered_accuracy'] = 0

    results['true_positives'] = true_positives
    results['false_positives'] = false_positives

    return results


def print_results(results: Dict):
    """Print analysis results."""
    print(f"\n{'='*70}")
    print(f"SIGNAL ACCURACY ANALYSIS")
    print(f"{'='*70}")

    print(f"\n=== Overall ===")
    print(f"  Total BTC moves detected: {results['total_moves']}")
    print(f"  Kalshi responded: {results['moves_with_response']} ({results['overall_accuracy']*100:.1f}%)")
    print(f"  Kalshi ignored: {results['moves_without_response']} ({(1-results['overall_accuracy'])*100:.1f}%)")

    print(f"\n=== Momentum Filter Effectiveness ===")
    print(f"  Moves passing momentum filter: {results['moves_with_momentum_filter']}")
    print(f"    → Kalshi followed: {results['filtered_moves_with_response']} ({results['filtered_accuracy']*100:.1f}%)")
    print(f"  ")
    print(f"  Moves failing momentum filter: {results['moves_without_momentum_filter']}")
    print(f"    → Kalshi followed: {results['unfiltered_moves_with_response']} ({results['unfiltered_accuracy']*100:.1f}%)")

    # Filter improvement
    if results['filtered_accuracy'] > results['unfiltered_accuracy']:
        improvement = results['filtered_accuracy'] - results['unfiltered_accuracy']
        print(f"\n  ✅ Momentum filter IMPROVES accuracy by {improvement*100:.1f}%")
    else:
        degradation = results['unfiltered_accuracy'] - results['filtered_accuracy']
        print(f"\n  ⚠️  Momentum filter REDUCES accuracy by {degradation*100:.1f}%")

    # Analyze characteristics of true positives vs false positives
    if results['true_positives'] and results['false_positives']:
        tp = results['true_positives']
        fp = results['false_positives']

        print(f"\n=== True Positives (Kalshi Followed) ===")
        print(f"  Count: {len(tp)}")
        print(f"  Avg momentum ratio: {np.mean([m['momentum_ratio'] for m in tp]):.2f}")
        print(f"  Avg volume: {np.mean([m['volume'] for m in tp]):.2f} BTC")
        print(f"  Avg price delta: ${np.mean([abs(m['price_delta']) for m in tp]):.2f}")
        print(f"  Avg Kalshi lag: {np.mean([m['avg_lag'] for m in tp]):.2f}s")

        print(f"\n=== False Positives (Kalshi Ignored) ===")
        print(f"  Count: {len(fp)}")
        print(f"  Avg momentum ratio: {np.mean([m['momentum_ratio'] for m in fp]):.2f}")
        print(f"  Avg volume: {np.mean([m['volume'] for m in fp]):.2f} BTC")
        print(f"  Avg price delta: ${np.mean([abs(m['price_delta']) for m in fp]):.2f}")

        # Statistical comparison
        print(f"\n=== Discriminating Features ===")

        tp_momentum = np.mean([m['momentum_ratio'] for m in tp])
        fp_momentum = np.mean([m['momentum_ratio'] for m in fp])
        print(f"  Momentum ratio: TP={tp_momentum:.2f} vs FP={fp_momentum:.2f} "
              f"({'HIGHER' if tp_momentum > fp_momentum else 'LOWER'} is better)")

        tp_volume = np.mean([m['volume'] for m in tp])
        fp_volume = np.mean([m['volume'] for m in fp])
        print(f"  Volume: TP={tp_volume:.2f} vs FP={fp_volume:.2f} BTC "
              f"({'HIGHER' if tp_volume > fp_volume else 'LOWER'} is better)")

        tp_delta = np.mean([abs(m['price_delta']) for m in tp])
        fp_delta = np.mean([abs(m['price_delta']) for m in fp])
        print(f"  Price delta: TP=${tp_delta:.2f} vs FP=${fp_delta:.2f} "
              f"({'LARGER' if tp_delta > fp_delta else 'SMALLER'} is better)")

    print(f"\n{'='*70}")
    print(f"INTERPRETATION")
    print(f"{'='*70}")

    if results['overall_accuracy'] > 0.7:
        print(f"✅ HIGH ACCURACY ({results['overall_accuracy']*100:.1f}%)")
        print(f"   Most detected moves result in Kalshi repricing")
    elif results['overall_accuracy'] > 0.5:
        print(f"⚠️  MODERATE ACCURACY ({results['overall_accuracy']*100:.1f}%)")
        print(f"   Many moves followed, but significant false positives")
    else:
        print(f"❌ LOW ACCURACY ({results['overall_accuracy']*100:.1f}%)")
        print(f"   Too many false signals - need better filters")

    if results['filtered_accuracy'] > results['overall_accuracy'] + 0.1:
        print(f"\n✅ Momentum filter is CRITICAL (accuracy {results['overall_accuracy']*100:.1f}% → {results['filtered_accuracy']*100:.1f}%)")
    elif results['filtered_accuracy'] > results['overall_accuracy']:
        print(f"\n✓ Momentum filter helps slightly (accuracy {results['overall_accuracy']*100:.1f}% → {results['filtered_accuracy']*100:.1f}%)")
    else:
        print(f"\n⚠️  Momentum filter may not be helping")


def main():
    parser = argparse.ArgumentParser(description="Analyze BTC signal accuracy")
    parser.add_argument('--db', type=Path, default=Path('data/btc_march3_overnight.db'))
    parser.add_argument('--window', type=float, default=5.0,
                        help='BTC move detection window (seconds)')
    parser.add_argument('--min-move', type=float, default=15.0,
                        help='Minimum BTC move in USD')
    parser.add_argument('--momentum', type=float, default=0.8,
                        help='Momentum threshold (recent/older ratio)')
    parser.add_argument('--response-window', type=float, default=30.0,
                        help='How long to wait for Kalshi response (seconds)')

    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: Database not found: {args.db}")
        return 1

    print(f"Analyzing signal accuracy from {args.db}")
    print(f"BTC detection window: {args.window}s")
    print(f"Min BTC move: ${args.min_move}")
    print(f"Momentum threshold: {args.momentum}")
    print(f"Kalshi response window: {args.response_window}s")
    print("="*70)

    # Load data
    print("\nLoading data...")
    binance_trades = load_binance_trades(args.db)
    kalshi_snaps = load_kalshi_snapshots(args.db)

    if not binance_trades:
        print("Error: No Binance trade data")
        return 1
    if not kalshi_snaps:
        print("Error: No Kalshi snapshots")
        return 1

    print(f"Loaded {len(binance_trades)} Binance trades")
    print(f"Loaded {len(kalshi_snaps)} Kalshi snapshots")

    # Detect BTC moves
    print(f"\nDetecting BTC moves (≥${args.min_move}, {args.window}s window)...")
    btc_moves = detect_btc_moves(
        binance_trades,
        window_sec=args.window,
        min_move_usd=args.min_move,
        momentum_threshold=args.momentum
    )

    print(f"Detected {len(btc_moves)} BTC moves")

    # Analyze accuracy
    results = analyze_signal_accuracy(btc_moves, kalshi_snaps)

    # Print results
    print_results(results)

    return 0


if __name__ == '__main__':
    sys.exit(main())
