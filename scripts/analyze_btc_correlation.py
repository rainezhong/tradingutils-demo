#!/usr/bin/env python3
"""Analyze correlation between Bitcoin spot price moves and Kalshi market price moves.

This script investigates:
1. How quickly Kalshi reacts to spot price changes (latency)
2. Correlation strength between spot moves and Kalshi moves
3. Mispricing windows where opportunities exist
4. Leading/lagging relationship

Usage:
    python3 scripts/analyze_btc_correlation.py --db data/btc_probe_merged.db
    python3 scripts/analyze_btc_correlation.py --db data/btc_probe_merged.db --window 60
    python3 scripts/analyze_btc_correlation.py --db data/btc_probe_merged.db --plot
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import numpy as np
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_data(db_path: Path) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """Load aligned Kraken and Kalshi data from database.

    Returns:
        (timestamps, kraken_prices, kalshi_data)
        where kalshi_data is list of (ticker, yes_mid, strike) tuples
    """
    conn = sqlite3.connect(str(db_path))

    # Get Kraken snapshots (60s rolling avg as proxy for BRTI)
    kraken_query = """
        SELECT ts, avg_60s, spot_price
        FROM kraken_snapshots
        ORDER BY ts
    """
    kraken_rows = conn.execute(kraken_query).fetchall()

    # Get Kalshi snapshots
    kalshi_query = """
        SELECT ts, ticker, yes_mid, floor_strike, yes_bid, yes_ask
        FROM kalshi_snapshots
        WHERE yes_mid IS NOT NULL
        ORDER BY ts
    """
    kalshi_rows = conn.execute(kalshi_query).fetchall()

    conn.close()

    print(f"Loaded {len(kraken_rows)} Kraken snapshots")
    print(f"Loaded {len(kalshi_rows)} Kalshi snapshots")

    return kraken_rows, kalshi_rows


def align_timeseries(
    kraken_rows: List[Tuple],
    kalshi_rows: List[Tuple],
    max_time_diff: float = 1.0
) -> List[Dict]:
    """Align Kraken and Kalshi data by timestamp.

    For each Kalshi snapshot, find the nearest Kraken snapshot within max_time_diff.

    Args:
        kraken_rows: (ts, avg_60s, spot_price) tuples
        kalshi_rows: (ts, ticker, yes_mid, strike, yes_bid, yes_ask) tuples
        max_time_diff: Maximum time difference in seconds for alignment

    Returns:
        List of aligned datapoints with both Kraken and Kalshi data
    """
    aligned = []
    kraken_idx = 0

    for kalshi_ts, ticker, kalshi_mid, strike, bid, ask in kalshi_rows:
        # Find nearest Kraken snapshot
        while kraken_idx < len(kraken_rows) - 1:
            if kraken_rows[kraken_idx + 1][0] > kalshi_ts:
                break
            kraken_idx += 1

        kraken_ts, kraken_avg, kraken_spot = kraken_rows[kraken_idx]
        time_diff = abs(kalshi_ts - kraken_ts)

        # Skip if missing critical data
        if strike is None or kalshi_mid is None:
            continue

        if time_diff <= max_time_diff:
            # Calculate implied probability from Kalshi price
            kalshi_prob = kalshi_mid / 100.0  # Convert cents to probability

            # Check if Kraken price is above strike
            kraken_above_strike = kraken_avg > strike

            aligned.append({
                'ts': kalshi_ts,
                'kraken_ts': kraken_ts,
                'time_diff': time_diff,
                'ticker': ticker,
                'strike': strike,
                'kraken_avg': kraken_avg,
                'kraken_spot': kraken_spot,
                'kalshi_mid': kalshi_mid,
                'kalshi_prob': kalshi_prob,
                'kalshi_bid': bid,
                'kalshi_ask': ask,
                'kraken_above_strike': kraken_above_strike,
            })

    print(f"\nAligned {len(aligned)} datapoints (max time diff: {max_time_diff}s)")
    return aligned


def calculate_correlation(aligned: List[Dict], window: int = 60) -> Dict:
    """Calculate correlation statistics between spot and Kalshi moves.

    Args:
        aligned: List of aligned datapoints
        window: Time window in seconds for move calculations

    Returns:
        Dictionary with correlation statistics
    """
    if len(aligned) < 2:
        return {'error': 'Not enough data'}

    # Group by ticker and calculate statistics per market
    tickers = {}
    for point in aligned:
        ticker = point['ticker']
        if ticker not in tickers:
            tickers[ticker] = []
        tickers[ticker].append(point)

    print(f"\nAnalyzing {len(tickers)} unique markets:")

    results = {}
    all_mispricings = []

    for ticker, points in sorted(tickers.items(), key=lambda x: len(x[1]), reverse=True):
        if len(points) < 10:
            continue

        # Calculate price changes over time
        kraken_changes = []
        kalshi_changes = []
        mispricings = []

        for i in range(1, len(points)):
            prev = points[i-1]
            curr = points[i]

            time_delta = curr['ts'] - prev['ts']
            if time_delta > window:
                continue

            # Price changes
            kraken_change = curr['kraken_avg'] - prev['kraken_avg']
            kalshi_change = curr['kalshi_mid'] - prev['kalshi_mid']

            kraken_changes.append(kraken_change)
            kalshi_changes.append(kalshi_change)

            # Mispricing: expected probability vs actual Kalshi price
            # Simple model: if BTC > strike, prob should be high; if BTC < strike, prob should be low
            btc_to_strike_cents = (curr['kraken_avg'] - curr['strike']) * 100
            kalshi_cents_to_50 = curr['kalshi_mid'] - 50

            # Mispricing = how far Kalshi is from "fair" based on spot position
            # Positive = Kalshi too low (buy opportunity), Negative = Kalshi too high
            mispricing = btc_to_strike_cents - kalshi_cents_to_50

            mispricings.append({
                'ts': curr['ts'],
                'ticker': ticker,
                'strike': curr['strike'],
                'kraken_avg': curr['kraken_avg'],
                'kalshi_mid': curr['kalshi_mid'],
                'mispricing_cents': mispricing,
                'btc_to_strike_cents': btc_to_strike_cents,
                'spread_cents': curr['kalshi_ask'] - curr['kalshi_bid'] if curr['kalshi_ask'] and curr['kalshi_bid'] else None,
            })
            all_mispricings.append(mispricings[-1])

        if len(kraken_changes) < 10:
            continue

        # Calculate correlation
        correlation = np.corrcoef(kraken_changes, kalshi_changes)[0, 1] if len(kraken_changes) > 0 else 0

        # Calculate average mispricing
        avg_mispricing = np.mean([m['mispricing_cents'] for m in mispricings])
        std_mispricing = np.std([m['mispricing_cents'] for m in mispricings])

        results[ticker] = {
            'datapoints': len(points),
            'correlation': correlation,
            'avg_mispricing_cents': avg_mispricing,
            'std_mispricing_cents': std_mispricing,
            'strike': points[0]['strike'],
        }

        print(f"  {ticker}: {len(points)} pts, corr={correlation:.3f}, "
              f"mispricing={avg_mispricing:.1f}¢±{std_mispricing:.1f}¢")

    # Overall statistics
    all_correlations = [r['correlation'] for r in results.values() if not np.isnan(r['correlation'])]
    all_mispricing_vals = [m['mispricing_cents'] for m in all_mispricings]

    overall = {
        'markets_analyzed': len(results),
        'total_datapoints': len(aligned),
        'avg_correlation': np.mean(all_correlations) if all_correlations else 0,
        'median_correlation': np.median(all_correlations) if all_correlations else 0,
        'avg_abs_mispricing_cents': np.mean(np.abs(all_mispricing_vals)) if all_mispricing_vals else 0,
        'std_mispricing_cents': np.std(all_mispricing_vals) if all_mispricing_vals else 0,
        'max_mispricing_cents': np.max(np.abs(all_mispricing_vals)) if all_mispricing_vals else 0,
    }

    return {
        'overall': overall,
        'by_market': results,
        'all_mispricings': all_mispricings,
    }


def detect_latency(aligned: List[Dict], threshold_cents: float = 15.0) -> Dict:
    """Detect reaction latency between Kraken moves and Kalshi moves.

    When Kraken has a significant move, how long does it take Kalshi to follow?

    Args:
        aligned: List of aligned datapoints
        threshold_cents: Minimum BTC move to consider (in cents)

    Returns:
        Latency statistics
    """
    print(f"\n=== Latency Analysis (threshold: ${threshold_cents/100:.2f}) ===")

    # Group by ticker
    tickers = {}
    for point in aligned:
        ticker = point['ticker']
        if ticker not in tickers:
            tickers[ticker] = []
        tickers[ticker].append(point)

    latencies = []

    for ticker, points in tickers.items():
        if len(points) < 20:
            continue

        # Look for significant Kraken moves
        for i in range(1, len(points) - 10):
            kraken_move = (points[i]['kraken_avg'] - points[i-1]['kraken_avg']) * 100  # cents

            if abs(kraken_move) < threshold_cents:
                continue

            # Find when Kalshi catches up (moves in same direction)
            kraken_direction = 1 if kraken_move > 0 else -1

            for j in range(i+1, min(i+20, len(points))):
                kalshi_move = points[j]['kalshi_mid'] - points[i]['kalshi_mid']
                kalshi_direction = 1 if kalshi_move > 0 else -1

                if kalshi_direction == kraken_direction and abs(kalshi_move) > 3:  # At least 3¢ move
                    latency = points[j]['ts'] - points[i]['ts']
                    latencies.append({
                        'latency_sec': latency,
                        'kraken_move_cents': kraken_move,
                        'kalshi_move_cents': kalshi_move,
                        'ticker': ticker,
                        'ts': points[i]['ts'],
                    })
                    break

    if not latencies:
        return {'error': 'No significant moves detected'}

    latency_values = [l['latency_sec'] for l in latencies]

    stats = {
        'num_events': len(latencies),
        'avg_latency_sec': np.mean(latency_values),
        'median_latency_sec': np.median(latency_values),
        'p25_latency_sec': np.percentile(latency_values, 25),
        'p75_latency_sec': np.percentile(latency_values, 75),
        'p95_latency_sec': np.percentile(latency_values, 95),
        'min_latency_sec': np.min(latency_values),
        'max_latency_sec': np.max(latency_values),
    }

    print(f"  Detected {stats['num_events']} significant moves")
    print(f"  Median latency: {stats['median_latency_sec']:.2f}s")
    print(f"  P25-P75: {stats['p25_latency_sec']:.2f}s - {stats['p75_latency_sec']:.2f}s")
    print(f"  P95 latency: {stats['p95_latency_sec']:.2f}s")
    print(f"  Range: {stats['min_latency_sec']:.2f}s - {stats['max_latency_sec']:.2f}s")

    return stats


def find_opportunities(aligned: List[Dict], min_edge_cents: float = 10.0) -> List[Dict]:
    """Find mispricing opportunities where Kalshi lags behind spot.

    Args:
        aligned: List of aligned datapoints
        min_edge_cents: Minimum mispricing to consider an opportunity

    Returns:
        List of opportunity datapoints
    """
    print(f"\n=== Opportunity Detection (min edge: {min_edge_cents}¢) ===")

    opportunities = []

    for i in range(1, len(aligned)):
        prev = aligned[i-1]
        curr = aligned[i]

        if curr['ticker'] != prev['ticker']:
            continue

        # Calculate how far BTC is from strike
        btc_to_strike = curr['kraken_avg'] - curr['strike']
        btc_distance_cents = btc_to_strike * 100

        # Simple edge detection: if BTC is well above strike, Kalshi should be high
        # if BTC is well below strike, Kalshi should be low

        if btc_distance_cents > 0:  # BTC above strike
            # Kalshi should be trading near 100¢, any discount is an opportunity
            edge = 100 - curr['kalshi_ask'] if curr['kalshi_ask'] else 100 - curr['kalshi_mid']
            if edge > min_edge_cents:
                opportunities.append({
                    'ts': curr['ts'],
                    'ticker': curr['ticker'],
                    'side': 'BUY_YES',
                    'strike': curr['strike'],
                    'kraken_avg': curr['kraken_avg'],
                    'btc_above_strike_cents': btc_distance_cents,
                    'kalshi_ask': curr['kalshi_ask'],
                    'kalshi_mid': curr['kalshi_mid'],
                    'edge_cents': edge,
                    'spread_cents': curr['kalshi_ask'] - curr['kalshi_bid'] if curr['kalshi_ask'] and curr['kalshi_bid'] else None,
                })
        else:  # BTC below strike
            # Kalshi should be trading near 0¢, any premium is an opportunity to short
            edge = curr['kalshi_bid'] if curr['kalshi_bid'] else curr['kalshi_mid']
            if edge > min_edge_cents:
                opportunities.append({
                    'ts': curr['ts'],
                    'ticker': curr['ticker'],
                    'side': 'BUY_NO',  # or SELL_YES
                    'strike': curr['strike'],
                    'kraken_avg': curr['kraken_avg'],
                    'btc_below_strike_cents': -btc_distance_cents,
                    'kalshi_bid': curr['kalshi_bid'],
                    'kalshi_mid': curr['kalshi_mid'],
                    'edge_cents': edge,
                    'spread_cents': curr['kalshi_ask'] - curr['kalshi_bid'] if curr['kalshi_ask'] and curr['kalshi_bid'] else None,
                })

    print(f"  Found {len(opportunities)} opportunities")

    if opportunities:
        edges = [o['edge_cents'] for o in opportunities]
        print(f"  Average edge: {np.mean(edges):.1f}¢")
        print(f"  Median edge: {np.median(edges):.1f}¢")
        print(f"  Max edge: {np.max(edges):.1f}¢")

        # Sample top opportunities
        top_opps = sorted(opportunities, key=lambda x: x['edge_cents'], reverse=True)[:5]
        print(f"\n  Top 5 opportunities:")
        for opp in top_opps:
            ts_str = datetime.fromtimestamp(opp['ts']).strftime('%H:%M:%S')
            print(f"    {ts_str} {opp['ticker']} {opp['side']}: edge={opp['edge_cents']:.0f}¢, "
                  f"BTC=${opp['kraken_avg']:.2f}, strike=${opp['strike']:.2f}")

    return opportunities


def main():
    parser = argparse.ArgumentParser(description="Analyze BTC/Kalshi correlation")
    parser.add_argument('--db', type=Path, default=Path('data/btc_probe_merged.db'),
                        help='Path to probe database')
    parser.add_argument('--window', type=int, default=60,
                        help='Time window for move calculations (seconds)')
    parser.add_argument('--plot', action='store_true',
                        help='Generate plots (requires matplotlib)')
    parser.add_argument('--min-edge', type=float, default=10.0,
                        help='Minimum edge in cents for opportunity detection')

    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: Database not found: {args.db}")
        print("\nAvailable databases:")
        for db in Path('data').glob('*probe*.db'):
            print(f"  {db}")
        return 1

    print(f"Analyzing correlation from {args.db}")
    print(f"Time window: {args.window}s")
    print(f"Min edge for opportunities: {args.min_edge}¢")
    print("=" * 70)

    # Load and align data
    kraken_rows, kalshi_rows = load_data(args.db)
    aligned = align_timeseries(kraken_rows, kalshi_rows, max_time_diff=1.0)

    if len(aligned) < 10:
        print("Error: Not enough aligned data")
        return 1

    # Time range
    start_ts = aligned[0]['ts']
    end_ts = aligned[-1]['ts']
    duration_hours = (end_ts - start_ts) / 3600
    start_str = datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M:%S')
    end_str = datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M:%S')

    print(f"\nTime range: {start_str} to {end_str}")
    print(f"Duration: {duration_hours:.1f} hours")

    # Correlation analysis
    print("\n" + "=" * 70)
    print("CORRELATION ANALYSIS")
    print("=" * 70)
    corr_results = calculate_correlation(aligned, window=args.window)

    overall = corr_results['overall']
    print(f"\n=== Overall Statistics ===")
    print(f"  Markets analyzed: {overall['markets_analyzed']}")
    print(f"  Total datapoints: {overall['total_datapoints']}")
    print(f"  Average correlation: {overall['avg_correlation']:.3f}")
    print(f"  Median correlation: {overall['median_correlation']:.3f}")
    print(f"  Average abs mispricing: {overall['avg_abs_mispricing_cents']:.1f}¢")
    print(f"  Mispricing std dev: {overall['std_mispricing_cents']:.1f}¢")
    print(f"  Max mispricing: {overall['max_mispricing_cents']:.1f}¢")

    # Latency detection
    print("\n" + "=" * 70)
    print("LATENCY ANALYSIS")
    print("=" * 70)
    latency_stats = detect_latency(aligned, threshold_cents=15.0)

    # Opportunity detection
    print("\n" + "=" * 70)
    print("OPPORTUNITY ANALYSIS")
    print("=" * 70)
    opportunities = find_opportunities(aligned, min_edge_cents=args.min_edge)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"✓ Correlation: {overall['avg_correlation']:.3f} (higher = stronger relationship)")
    print(f"✓ Median latency: {latency_stats.get('median_latency_sec', 'N/A'):.2f}s (how fast Kalshi reacts)")
    print(f"✓ P95 latency: {latency_stats.get('p95_latency_sec', 'N/A'):.2f}s (95th percentile)")
    print(f"✓ Opportunities: {len(opportunities)} found (edge ≥ {args.min_edge}¢)")
    print(f"✓ Avg mispricing: {overall['avg_abs_mispricing_cents']:.1f}¢")

    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)
    print(f"• Correlation {overall['avg_correlation']:.3f} means Kalshi {'follows' if overall['avg_correlation'] > 0.5 else 'loosely tracks'} spot moves")
    print(f"• Median latency {latency_stats.get('median_latency_sec', 'N/A'):.1f}s is your opportunity window")
    print(f"• P95 latency {latency_stats.get('p95_latency_sec', 'N/A'):.1f}s = 95% of moves take this long")
    print(f"• {len(opportunities)} opportunities = ~{len(opportunities)/duration_hours:.1f} per hour")

    if overall['avg_correlation'] > 0.7 and latency_stats.get('median_latency_sec', 999) < 10:
        print("\n✅ STRONG LATENCY ARB OPPORTUNITY")
        print("   High correlation + fast reaction = reliable edge")
    elif overall['avg_correlation'] > 0.5 and latency_stats.get('median_latency_sec', 999) < 30:
        print("\n⚠️  MODERATE LATENCY ARB OPPORTUNITY")
        print("   Decent correlation but need to be fast")
    else:
        print("\n❌ WEAK LATENCY ARB OPPORTUNITY")
        print("   Low correlation or slow reaction time")

    return 0


if __name__ == '__main__':
    sys.exit(main())
