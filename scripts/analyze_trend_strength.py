#!/usr/bin/env python3
"""Analyze Bitcoin trend strength vs Kalshi repricing lag.

This script helps optimize trend detection parameters by analyzing:
1. How trend strength (velocity, acceleration) correlates with Kalshi lag
2. Optimal lookback windows for trend detection
3. Volume-weighted vs simple price momentum
4. Multi-timeframe confirmation effectiveness

Usage:
    python3 scripts/analyze_trend_strength.py --db data/btc_march3_overnight.db
    python3 scripts/analyze_trend_strength.py --db data/btc_march3_overnight.db --plot
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Tuple, Dict, Optional
import numpy as np
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_binance_trades(db_path: Path, start_ts: float, end_ts: float) -> List[Tuple]:
    """Load Binance trade data (price, qty, timestamp)."""
    conn = sqlite3.connect(str(db_path))

    # Try both table names (binance_trades and binance_l2)
    for table in ['binance_trades', 'binance_l2']:
        try:
            query = f"""
                SELECT ts, price, qty
                FROM {table}
                WHERE ts BETWEEN ? AND ?
                ORDER BY ts
            """
            rows = conn.execute(query, (start_ts, end_ts)).fetchall()
            if rows:
                conn.close()
                return rows
        except sqlite3.OperationalError:
            continue

    conn.close()
    return []


def load_kalshi_snapshots(db_path: Path) -> List[Tuple]:
    """Load Kalshi market snapshots."""
    conn = sqlite3.connect(str(db_path))

    # Try both column name formats
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


def calculate_trend_metrics(
    trades: List[Tuple],
    window_sec: float = 5.0
) -> Dict:
    """Calculate trend strength metrics from trades.

    Args:
        trades: List of (ts, price, qty) tuples
        window_sec: Lookback window in seconds

    Returns:
        Dictionary with trend metrics:
        - velocity: $ per second (price change rate)
        - acceleration: $ per second² (velocity change rate)
        - volume: Total BTC traded in window
        - vwap_delta: Volume-weighted price change
        - momentum_ratio: Recent half vs older half momentum
    """
    if len(trades) < 4:
        return None

    # Calculate basic metrics
    start_price = trades[0][1]
    end_price = trades[-1][1]
    duration = trades[-1][0] - trades[0][0]

    if duration < 1.0:
        return None

    # Velocity ($/s)
    velocity = (end_price - start_price) / duration

    # Acceleration (split window in half)
    mid = len(trades) // 2
    first_half = trades[:mid]
    second_half = trades[mid:]

    if len(first_half) < 2 or len(second_half) < 2:
        return None

    first_velocity = (first_half[-1][1] - first_half[0][1]) / (first_half[-1][0] - first_half[0][0])
    second_velocity = (second_half[-1][1] - second_half[0][1]) / (second_half[-1][0] - second_half[0][0])

    acceleration = (second_velocity - first_velocity) / duration

    # Volume metrics
    total_volume = sum(t[2] for t in trades)

    # VWAP delta
    vwap_start = sum(t[1] * t[2] for t in trades[:mid]) / sum(t[2] for t in trades[:mid]) if sum(t[2] for t in trades[:mid]) > 0 else start_price
    vwap_end = sum(t[1] * t[2] for t in trades[mid:]) / sum(t[2] for t in trades[mid:]) if sum(t[2] for t in trades[mid:]) > 0 else end_price
    vwap_delta = vwap_end - vwap_start

    # Momentum ratio (current strategy metric)
    older_delta = first_half[-1][1] - first_half[0][1]
    recent_delta = second_half[-1][1] - second_half[0][1]

    momentum_ratio = abs(recent_delta) / abs(older_delta) if abs(older_delta) > 0.01 else 0

    # Direction consistency (what % of time is price moving in trend direction?)
    trend_direction = 1 if (end_price - start_price) > 0 else -1
    consistent_moves = 0
    for i in range(1, len(trades)):
        move_dir = 1 if (trades[i][1] - trades[i-1][1]) > 0 else -1
        if move_dir == trend_direction:
            consistent_moves += 1

    consistency = consistent_moves / (len(trades) - 1) if len(trades) > 1 else 0

    return {
        'price_delta': end_price - start_price,
        'velocity': velocity,
        'acceleration': acceleration,
        'volume': total_volume,
        'vwap_delta': vwap_delta,
        'momentum_ratio': momentum_ratio,
        'consistency': consistency,
        'num_trades': len(trades),
    }


def analyze_kalshi_lag(
    binance_trades: List[Tuple],
    kalshi_snapshots: List[Tuple],
    window_sec: float = 5.0
) -> List[Dict]:
    """Analyze relationship between BTC trend strength and Kalshi repricing lag.

    For each significant BTC move:
    1. Calculate trend metrics (velocity, acceleration, etc.)
    2. Measure how long Kalshi takes to reprice
    3. Find correlation between trend strength and lag

    Returns:
        List of events with trend metrics and Kalshi lag
    """
    events = []

    # Group Kalshi snapshots by ticker
    kalshi_by_ticker = defaultdict(list)
    for snap in kalshi_snapshots:
        ticker = snap[1]
        kalshi_by_ticker[ticker].append(snap)

    # Scan through Binance trades looking for significant moves
    i = 0
    while i < len(binance_trades) - 10:
        start_ts = binance_trades[i][0]
        end_ts = start_ts + window_sec

        # Get trades in this window
        window_trades = []
        j = i
        while j < len(binance_trades) and binance_trades[j][0] <= end_ts:
            window_trades.append(binance_trades[j])
            j += 1

        if len(window_trades) < 4:
            i += 1
            continue

        # Calculate trend metrics
        metrics = calculate_trend_metrics(window_trades, window_sec)
        if not metrics:
            i += 1
            continue

        # Only consider significant moves (≥$15)
        if abs(metrics['price_delta']) < 15:
            i += 1
            continue

        # Find Kalshi repricing lag for each active market
        btc_price_end = window_trades[-1][1]

        for ticker, snaps in kalshi_by_ticker.items():
            # Find strike price
            strike = None
            for snap in snaps:
                if snap[5] is not None:  # strike column
                    strike = snap[5]
                    break

            if strike is None:
                continue

            # Find Kalshi snapshot right before BTC move
            pre_snap = None
            for snap in snaps:
                if snap[0] <= start_ts:
                    pre_snap = snap

            if not pre_snap:
                continue

            pre_price = pre_snap[2]  # yes_mid

            # Determine expected Kalshi direction
            btc_above_strike = btc_price_end > strike
            expected_kalshi_direction = 1 if btc_above_strike else -1

            # Find when Kalshi catches up (moves ≥3¢ in expected direction)
            lag = None
            for snap in snaps:
                if snap[0] <= end_ts:
                    continue

                current_price = snap[2]  # yes_mid
                kalshi_move = current_price - pre_price

                if expected_kalshi_direction == 1 and kalshi_move >= 3:
                    lag = snap[0] - end_ts
                    break
                elif expected_kalshi_direction == -1 and kalshi_move <= -3:
                    lag = snap[0] - end_ts
                    break

                # Give up after 2 minutes
                if snap[0] - end_ts > 120:
                    break

            if lag is not None and lag > 0:
                events.append({
                    'ts': start_ts,
                    'ticker': ticker,
                    'btc_delta': metrics['price_delta'],
                    'velocity': metrics['velocity'],
                    'acceleration': metrics['acceleration'],
                    'volume': metrics['volume'],
                    'vwap_delta': metrics['vwap_delta'],
                    'momentum_ratio': metrics['momentum_ratio'],
                    'consistency': metrics['consistency'],
                    'num_trades': metrics['num_trades'],
                    'kalshi_lag_sec': lag,
                    'strike': strike,
                    'btc_price': btc_price_end,
                })

        # Move to next non-overlapping window
        i = j

    return events


def print_summary(events: List[Dict]):
    """Print summary statistics."""
    if not events:
        print("No events found")
        return

    print(f"\n{'='*70}")
    print(f"TREND STRENGTH vs KALSHI LAG ANALYSIS")
    print(f"{'='*70}")
    print(f"\nAnalyzed {len(events)} significant BTC moves (≥$15)")

    # Overall lag statistics
    lags = [e['kalshi_lag_sec'] for e in events]
    print(f"\n=== Kalshi Repricing Lag ===")
    print(f"  Median: {np.median(lags):.2f}s")
    print(f"  P25-P75: {np.percentile(lags, 25):.2f}s - {np.percentile(lags, 75):.2f}s")
    print(f"  Min-Max: {np.min(lags):.2f}s - {np.max(lags):.2f}s")

    # Correlation analysis
    velocities = [e['velocity'] for e in events]
    accelerations = [e['acceleration'] for e in events]
    volumes = [e['volume'] for e in events]
    momentum_ratios = [e['momentum_ratio'] for e in events]
    consistencies = [e['consistency'] for e in events]

    print(f"\n=== Trend Metrics vs Lag Correlation ===")
    print(f"  Velocity corr:       {np.corrcoef(velocities, lags)[0,1]:.3f}")
    print(f"  Acceleration corr:   {np.corrcoef(accelerations, lags)[0,1]:.3f}")
    print(f"  Volume corr:         {np.corrcoef(volumes, lags)[0,1]:.3f}")
    print(f"  Momentum ratio corr: {np.corrcoef(momentum_ratios, lags)[0,1]:.3f}")
    print(f"  Consistency corr:    {np.corrcoef(consistencies, lags)[0,1]:.3f}")

    # Bucket analysis: high momentum vs low momentum
    median_momentum = np.median(momentum_ratios)
    high_momentum = [e for e in events if e['momentum_ratio'] >= median_momentum]
    low_momentum = [e for e in events if e['momentum_ratio'] < median_momentum]

    print(f"\n=== High vs Low Momentum Moves ===")
    print(f"  High momentum (≥{median_momentum:.2f}):")
    print(f"    Count: {len(high_momentum)}")
    print(f"    Avg lag: {np.mean([e['kalshi_lag_sec'] for e in high_momentum]):.2f}s")
    print(f"    Avg volume: {np.mean([e['volume'] for e in high_momentum]):.2f} BTC")

    print(f"\n  Low momentum (<{median_momentum:.2f}):")
    print(f"    Count: {len(low_momentum)}")
    print(f"    Avg lag: {np.mean([e['kalshi_lag_sec'] for e in low_momentum]):.2f}s")
    print(f"    Avg volume: {np.mean([e['volume'] for e in low_momentum]):.2f} BTC")

    # High volume vs low volume
    median_volume = np.median(volumes)
    high_volume = [e for e in events if e['volume'] >= median_volume]
    low_volume = [e for e in events if e['volume'] < median_volume]

    print(f"\n=== High vs Low Volume Moves ===")
    print(f"  High volume (≥{median_volume:.2f} BTC):")
    print(f"    Count: {len(high_volume)}")
    print(f"    Avg lag: {np.mean([e['kalshi_lag_sec'] for e in high_volume]):.2f}s")
    print(f"    Avg momentum: {np.mean([e['momentum_ratio'] for e in high_volume]):.2f}")

    print(f"\n  Low volume (<{median_volume:.2f} BTC):")
    print(f"    Count: {len(low_volume)}")
    print(f"    Avg lag: {np.mean([e['kalshi_lag_sec'] for e in low_volume]):.2f}s")
    print(f"    Avg momentum: {np.mean([e['momentum_ratio'] for e in low_volume]):.2f}")

    # Best signals (high momentum + high volume)
    best_signals = [e for e in events if e['momentum_ratio'] >= median_momentum and e['volume'] >= median_volume]
    print(f"\n=== Best Signals (High Momentum + High Volume) ===")
    print(f"  Count: {len(best_signals)}")
    print(f"  Avg lag: {np.mean([e['kalshi_lag_sec'] for e in best_signals]):.2f}s")
    print(f"  This is your highest-conviction entry signal!")

    # Top 10 longest lags (best opportunities)
    print(f"\n=== Top 10 Longest Lags (Best Opportunities) ===")
    sorted_events = sorted(events, key=lambda x: x['kalshi_lag_sec'], reverse=True)[:10]
    for i, e in enumerate(sorted_events, 1):
        ts_str = datetime.fromtimestamp(e['ts']).strftime('%H:%M:%S')
        print(f"  {i:2d}. {ts_str} {e['ticker']}: lag={e['kalshi_lag_sec']:.1f}s, "
              f"vel={e['velocity']:.1f}$/s, vol={e['volume']:.2f}BTC, mom={e['momentum_ratio']:.2f}")

    # Recommendations
    print(f"\n{'='*70}")
    print(f"RECOMMENDATIONS")
    print(f"{'='*70}")

    avg_lag = np.mean(lags)
    if avg_lag > 15:
        print(f"✅ LARGE EDGE WINDOW ({avg_lag:.1f}s avg lag)")
        print(f"   Your 2-5s execution leaves {avg_lag-5:.1f}s+ cushion")
    elif avg_lag > 8:
        print(f"⚠️  MODERATE EDGE WINDOW ({avg_lag:.1f}s avg lag)")
        print(f"   Need fast execution (<3s) to reliably capture edge")
    else:
        print(f"❌ SMALL EDGE WINDOW ({avg_lag:.1f}s avg lag)")
        print(f"   May struggle to capture edge consistently")

    mom_corr = np.corrcoef(momentum_ratios, lags)[0,1]
    if abs(mom_corr) > 0.3:
        print(f"\n{'✅' if mom_corr < 0 else '⚠️'} Momentum ratio {'inversely' if mom_corr < 0 else 'positively'} "
              f"correlates with lag (r={mom_corr:.3f})")
        if mom_corr < 0:
            print(f"   → High momentum = shorter lag (harder edge)")
        else:
            print(f"   → High momentum = longer lag (easier edge)")

    vol_corr = np.corrcoef(volumes, lags)[0,1]
    if abs(vol_corr) > 0.3:
        print(f"\n{'✅' if vol_corr > 0 else '⚠️'} Volume {'positively' if vol_corr > 0 else 'inversely'} "
              f"correlates with lag (r={vol_corr:.3f})")
        if vol_corr > 0:
            print(f"   → High volume = longer lag (easier edge)")
        else:
            print(f"   → High volume = shorter lag (harder edge)")


def main():
    parser = argparse.ArgumentParser(description="Analyze BTC trend strength vs Kalshi lag")
    parser.add_argument('--db', type=Path, default=Path('data/btc_march3_overnight.db'),
                        help='Path to probe database')
    parser.add_argument('--window', type=float, default=5.0,
                        help='Lookback window for trend detection (seconds)')
    parser.add_argument('--plot', action='store_true',
                        help='Generate plots (requires matplotlib)')

    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: Database not found: {args.db}")
        return 1

    print(f"Analyzing trend strength from {args.db}")
    print(f"Lookback window: {args.window}s")
    print("="*70)

    # Load data
    print("\nLoading data...")
    kalshi_snaps = load_kalshi_snapshots(args.db)

    if not kalshi_snaps:
        print("Error: No Kalshi snapshots found")
        return 1

    start_ts = kalshi_snaps[0][0]
    end_ts = kalshi_snaps[-1][0]

    binance_trades = load_binance_trades(args.db, start_ts - 3600, end_ts)

    if not binance_trades:
        print("Error: No Binance trade data found")
        return 1

    print(f"Loaded {len(binance_trades)} Binance trades")
    print(f"Loaded {len(kalshi_snaps)} Kalshi snapshots")

    # Analyze
    print("\nAnalyzing trend strength vs Kalshi lag...")
    events = analyze_kalshi_lag(binance_trades, kalshi_snaps, window_sec=args.window)

    # Print summary
    print_summary(events)

    # Plot if requested
    if args.plot:
        try:
            import matplotlib.pyplot as plt

            fig, axes = plt.subplots(2, 2, figsize=(12, 10))

            # Plot 1: Velocity vs Lag
            axes[0, 0].scatter([e['velocity'] for e in events],
                              [e['kalshi_lag_sec'] for e in events], alpha=0.5)
            axes[0, 0].set_xlabel('Velocity ($/s)')
            axes[0, 0].set_ylabel('Kalshi Lag (s)')
            axes[0, 0].set_title('Velocity vs Kalshi Lag')

            # Plot 2: Volume vs Lag
            axes[0, 1].scatter([e['volume'] for e in events],
                              [e['kalshi_lag_sec'] for e in events], alpha=0.5)
            axes[0, 1].set_xlabel('Volume (BTC)')
            axes[0, 1].set_ylabel('Kalshi Lag (s)')
            axes[0, 1].set_title('Volume vs Kalshi Lag')

            # Plot 3: Momentum Ratio vs Lag
            axes[1, 0].scatter([e['momentum_ratio'] for e in events],
                              [e['kalshi_lag_sec'] for e in events], alpha=0.5)
            axes[1, 0].set_xlabel('Momentum Ratio')
            axes[1, 0].set_ylabel('Kalshi Lag (s)')
            axes[1, 0].set_title('Momentum Ratio vs Kalshi Lag')
            axes[1, 0].axvline(x=0.8, color='r', linestyle='--', label='Current threshold (0.8)')
            axes[1, 0].legend()

            # Plot 4: Lag distribution
            axes[1, 1].hist([e['kalshi_lag_sec'] for e in events], bins=30, alpha=0.7)
            axes[1, 1].axvline(x=np.median([e['kalshi_lag_sec'] for e in events]),
                              color='r', linestyle='--', label=f'Median ({np.median([e["kalshi_lag_sec"] for e in events]):.1f}s)')
            axes[1, 1].set_xlabel('Kalshi Lag (s)')
            axes[1, 1].set_ylabel('Frequency')
            axes[1, 1].set_title('Lag Distribution')
            axes[1, 1].legend()

            plt.tight_layout()

            output_path = args.db.parent / f"trend_analysis_{args.db.stem}.png"
            plt.savefig(output_path, dpi=150)
            print(f"\n✓ Plot saved to {output_path}")

        except ImportError:
            print("\nWarning: matplotlib not available, skipping plots")

    return 0


if __name__ == '__main__':
    sys.exit(main())
