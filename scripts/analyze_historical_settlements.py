#!/usr/bin/env python3
"""Analyze historical Kalshi settlements vs actual Bitcoin price outcomes.

This script uses real settlement data to:
1. Measure Kalshi prediction accuracy
2. Correlate Kalshi prices with actual outcomes
3. Identify profitable mispricing patterns
4. Calculate hypothetical returns from latency arbitrage

Usage:
    python3 scripts/analyze_historical_settlements.py --db data/btc_probe_20260227.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Dict, Tuple
import numpy as np
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_settlements(db_path: Path) -> List[Dict]:
    """Load all settlement data from database."""
    conn = sqlite3.connect(str(db_path))

    query = """
        SELECT
            ticker,
            close_time,
            floor_strike,
            settled_yes,
            expiration_value,
            kraken_avg60_at_settle,
            kalshi_last_mid,
            kraken_was_right,
            kalshi_was_right
        FROM market_settlements
        WHERE floor_strike IS NOT NULL
        ORDER BY close_time
    """

    rows = conn.execute(query).fetchall()
    conn.close()

    settlements = []
    for row in rows:
        ticker, close_time, strike, settled_yes, expiry_val, kraken_avg, kalshi_mid, kraken_right, kalshi_right = row

        # Skip if missing critical data
        if settled_yes is None or expiry_val is None:
            continue

        settlements.append({
            'ticker': ticker,
            'close_time': close_time,
            'strike': strike,
            'settled_yes': bool(settled_yes),
            'expiration_value': expiry_val,
            'kraken_avg': kraken_avg if kraken_avg and kraken_avg > 0 else None,
            'kalshi_last_mid': kalshi_mid,
            'kraken_was_right': bool(kraken_right) if kraken_right is not None else None,
            'kalshi_was_right': bool(kalshi_right) if kalshi_right is not None else None,
        })

    return settlements


def analyze_prediction_accuracy(settlements: List[Dict]) -> Dict:
    """Analyze how well Kalshi prices predicted outcomes."""

    print("\n" + "=" * 70)
    print("PREDICTION ACCURACY ANALYSIS")
    print("=" * 70)

    # Overall accuracy
    kalshi_correct = sum(1 for s in settlements if s['kalshi_was_right'])
    kraken_correct = sum(1 for s in settlements if s['kraken_was_right'] is not None and s['kraken_was_right'])
    kraken_total = sum(1 for s in settlements if s['kraken_was_right'] is not None)

    print(f"\n=== Overall Accuracy ===")
    print(f"Kalshi accuracy: {kalshi_correct}/{len(settlements)} = {kalshi_correct/len(settlements):.1%}")
    if kraken_total > 0:
        print(f"Kraken accuracy: {kraken_correct}/{kraken_total} = {kraken_correct/kraken_total:.1%}")

    # Accuracy by Kalshi confidence level
    confidence_buckets = {
        'Very confident YES (>80¢)': [],
        'Confident YES (60-80¢)': [],
        'Uncertain (40-60¢)': [],
        'Confident NO (20-40¢)': [],
        'Very confident NO (<20¢)': [],
    }

    for s in settlements:
        if s['kalshi_last_mid'] is None:
            continue

        kalshi_price = s['kalshi_last_mid']
        actual_outcome = s['settled_yes']

        # Kalshi prediction: >50¢ = YES, <50¢ = NO
        kalshi_predicted_yes = kalshi_price > 50
        correct = (kalshi_predicted_yes == actual_outcome)

        if kalshi_price > 80:
            bucket = 'Very confident YES (>80¢)'
        elif kalshi_price > 60:
            bucket = 'Confident YES (60-80¢)'
        elif kalshi_price >= 40:
            bucket = 'Uncertain (40-60¢)'
        elif kalshi_price >= 20:
            bucket = 'Confident NO (20-40¢)'
        else:
            bucket = 'Very confident NO (<20¢)'

        confidence_buckets[bucket].append(correct)

    print(f"\n=== Accuracy by Kalshi Confidence ===")
    for bucket, results in confidence_buckets.items():
        if not results:
            continue
        accuracy = sum(results) / len(results)
        print(f"{bucket:30s}: {sum(results):2d}/{len(results):2d} = {accuracy:5.1%}")

    return {
        'total_settlements': len(settlements),
        'kalshi_accuracy': kalshi_correct / len(settlements) if settlements else 0,
        'kraken_accuracy': kraken_correct / kraken_total if kraken_total > 0 else None,
    }


def analyze_kalshi_calibration(settlements: List[Dict]) -> Dict:
    """Analyze if Kalshi prices are well-calibrated (e.g., 70¢ → 70% chance of YES)."""

    print("\n" + "=" * 70)
    print("KALSHI PRICE CALIBRATION")
    print("=" * 70)

    # Group by price buckets
    buckets = {
        '0-10¢': [],
        '10-20¢': [],
        '20-30¢': [],
        '30-40¢': [],
        '40-50¢': [],
        '50-60¢': [],
        '60-70¢': [],
        '70-80¢': [],
        '80-90¢': [],
        '90-100¢': [],
    }

    for s in settlements:
        if s['kalshi_last_mid'] is None:
            continue

        price = s['kalshi_last_mid']
        outcome = 1 if s['settled_yes'] else 0

        if price < 10:
            bucket = '0-10¢'
        elif price < 20:
            bucket = '10-20¢'
        elif price < 30:
            bucket = '20-30¢'
        elif price < 40:
            bucket = '30-40¢'
        elif price < 50:
            bucket = '40-50¢'
        elif price < 60:
            bucket = '50-60¢'
        elif price < 70:
            bucket = '60-70¢'
        elif price < 80:
            bucket = '70-80¢'
        elif price < 90:
            bucket = '80-90¢'
        else:
            bucket = '90-100¢'

        buckets[bucket].append(outcome)

    print(f"\n=== Observed YES Rate by Kalshi Price ===")
    print(f"{'Price Range':15s} {'Count':>6s} {'Observed':>10s} {'Expected':>10s} {'Calibration':>12s}")
    print("-" * 70)

    calibration_errors = []

    for bucket_name, outcomes in buckets.items():
        if not outcomes:
            continue

        count = len(outcomes)
        observed_yes_rate = sum(outcomes) / count

        # Expected rate is midpoint of bucket
        bucket_min = int(bucket_name.split('-')[0].replace('¢', ''))
        bucket_max = int(bucket_name.split('-')[1].replace('¢', ''))
        expected_yes_rate = (bucket_min + bucket_max) / 2 / 100

        calibration_error = abs(observed_yes_rate - expected_yes_rate)
        calibration_errors.append(calibration_error)

        print(f"{bucket_name:15s} {count:6d} {observed_yes_rate:9.1%} {expected_yes_rate:9.1%} {calibration_error:11.1%}")

    avg_calibration_error = np.mean(calibration_errors) if calibration_errors else 0

    print(f"\nAverage calibration error: {avg_calibration_error:.1%}")

    if avg_calibration_error < 0.1:
        print("✅ Kalshi is WELL-CALIBRATED (prices match outcomes)")
    elif avg_calibration_error < 0.2:
        print("⚠️  Kalshi is MODERATELY-CALIBRATED (some deviation)")
    else:
        print("❌ Kalshi is POORLY-CALIBRATED (prices don't match outcomes)")

    return {'avg_calibration_error': avg_calibration_error}


def detect_mispricing_patterns(settlements: List[Dict]) -> List[Dict]:
    """Identify patterns where Kalshi was significantly mispriced."""

    print("\n" + "=" * 70)
    print("MISPRICING ANALYSIS")
    print("=" * 70)

    mispricings = []

    for s in settlements:
        if s['kalshi_last_mid'] is None:
            continue

        kalshi_price = s['kalshi_last_mid']
        actual_outcome = s['settled_yes']
        actual_price = 100 if actual_outcome else 0

        # Mispricing = how far was Kalshi from actual outcome
        mispricing = abs(kalshi_price - actual_price)

        # Direction
        if actual_outcome and kalshi_price < 50:
            direction = "UNDERPRICED_YES"  # YES won but Kalshi said NO
        elif not actual_outcome and kalshi_price > 50:
            direction = "OVERPRICED_YES"   # NO won but Kalshi said YES
        else:
            direction = "CORRECT_DIRECTION"

        mispricings.append({
            'ticker': s['ticker'],
            'strike': s['strike'],
            'expiry': s['expiration_value'],
            'kalshi_price': kalshi_price,
            'actual_outcome': actual_outcome,
            'mispricing': mispricing,
            'direction': direction,
        })

    # Top mispricings
    worst_mispricings = sorted(mispricings, key=lambda x: x['mispricing'], reverse=True)[:10]

    print(f"\n=== Top 10 Worst Mispricings ===")
    print(f"{'Ticker':30s} {'Kalshi':>7s} {'Actual':>7s} {'Error':>6s} {'Direction':>18s}")
    print("-" * 70)

    for m in worst_mispricings:
        outcome_str = "YES" if m['actual_outcome'] else "NO"
        print(f"{m['ticker']:30s} {m['kalshi_price']:6.1f}¢ {outcome_str:>7s} {m['mispricing']:5.0f}¢ {m['direction']:>18s}")

    # Statistics
    avg_mispricing = np.mean([m['mispricing'] for m in mispricings])
    median_mispricing = np.median([m['mispricing'] for m in mispricings])
    max_mispricing = np.max([m['mispricing'] for m in mispricings])

    underpriced = sum(1 for m in mispricings if m['direction'] == 'UNDERPRICED_YES')
    overpriced = sum(1 for m in mispricings if m['direction'] == 'OVERPRICED_YES')
    correct = sum(1 for m in mispricings if m['direction'] == 'CORRECT_DIRECTION')

    print(f"\n=== Mispricing Statistics ===")
    print(f"Average mispricing: {avg_mispricing:.1f}¢")
    print(f"Median mispricing: {median_mispricing:.1f}¢")
    print(f"Max mispricing: {max_mispricing:.0f}¢")
    print(f"\nDirection breakdown:")
    print(f"  Underpriced YES: {underpriced} ({underpriced/len(mispricings):.1%})")
    print(f"  Overpriced YES: {overpriced} ({overpriced/len(mispricings):.1%})")
    print(f"  Correct direction: {correct} ({correct/len(mispricings):.1%})")

    return mispricings


def calculate_hypothetical_pnl(settlements: List[Dict]) -> Dict:
    """Calculate what P&L would have been from various trading strategies."""

    print("\n" + "=" * 70)
    print("HYPOTHETICAL P&L ANALYSIS")
    print("=" * 70)

    strategies = {
        'Buy when Kalshi <30¢ and BTC near strike': [],
        'Buy when Kalshi >70¢ and BTC near strike': [],
        'Fade Kalshi extremes (<10¢ or >90¢)': [],
        'Always bet with spot price': [],
    }

    for s in settlements:
        if s['kalshi_last_mid'] is None:
            continue

        kalshi_price = s['kalshi_last_mid']
        actual_outcome = s['settled_yes']
        spot_price = s['expiration_value']
        strike = s['strike']

        # Spot prediction
        spot_says_yes = spot_price > strike

        # Strategy 1: Buy YES when Kalshi <30¢ (underpriced)
        if kalshi_price < 30:
            entry_cost = 30  # Assume we pay ask
            payout = 100 if actual_outcome else 0
            pnl = payout - entry_cost
            strategies['Buy when Kalshi <30¢ and BTC near strike'].append(pnl)

        # Strategy 2: Buy YES when Kalshi >70¢ (follow confident signal)
        if kalshi_price > 70:
            entry_cost = 75  # Assume we pay ask
            payout = 100 if actual_outcome else 0
            pnl = payout - entry_cost
            strategies['Buy when Kalshi >70¢ and BTC near strike'].append(pnl)

        # Strategy 3: Fade extremes (bet NO when >90¢, bet YES when <10¢)
        if kalshi_price > 90:
            entry_cost = 10  # Cost to buy NO (= sell YES at 90¢)
            payout = 100 if not actual_outcome else 0
            pnl = payout - entry_cost
            strategies['Fade Kalshi extremes (<10¢ or >90¢)'].append(pnl)
        elif kalshi_price < 10:
            entry_cost = 10  # Cost to buy YES
            payout = 100 if actual_outcome else 0
            pnl = payout - entry_cost
            strategies['Fade Kalshi extremes (<10¢ or >90¢)'].append(pnl)

        # Strategy 4: Always bet with spot
        if spot_says_yes:
            # Buy YES at market price
            entry_cost = kalshi_price + 5  # Assume 5¢ spread
            payout = 100 if actual_outcome else 0
            pnl = payout - entry_cost
        else:
            # Buy NO at market price
            entry_cost = (100 - kalshi_price) + 5  # Assume 5¢ spread
            payout = 100 if not actual_outcome else 0
            pnl = payout - entry_cost

        strategies['Always bet with spot price'].append(pnl)

    print(f"\n=== Strategy Performance ===")
    print(f"{'Strategy':45s} {'Trades':>7s} {'Avg P&L':>9s} {'Total':>9s} {'Win Rate':>9s}")
    print("-" * 80)

    results = {}

    for strategy_name, pnls in strategies.items():
        if not pnls:
            continue

        trades = len(pnls)
        avg_pnl = np.mean(pnls)
        total_pnl = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        win_rate = wins / trades if trades > 0 else 0

        results[strategy_name] = {
            'trades': trades,
            'avg_pnl_cents': avg_pnl,
            'total_pnl_cents': total_pnl,
            'win_rate': win_rate,
        }

        print(f"{strategy_name:45s} {trades:7d} {avg_pnl:8.1f}¢ {total_pnl:8.0f}¢ {win_rate:8.1%}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Analyze historical Kalshi settlements")
    parser.add_argument('--db', type=Path, default=Path('data/btc_probe_20260227.db'),
                        help='Path to probe database with settlements')

    args = parser.parse_args()

    if not args.db.exists():
        print(f"Error: Database not found: {args.db}")
        print("\nAvailable databases:")
        for db in Path('data').glob('*probe*.db'):
            print(f"  {db}")
        return 1

    print(f"Analyzing settlements from {args.db}")
    print("=" * 70)

    # Load data
    settlements = load_settlements(args.db)

    if len(settlements) < 5:
        print(f"Error: Not enough settlements ({len(settlements)})")
        return 1

    print(f"\nLoaded {len(settlements)} settled markets")

    # Time range
    if settlements[0]['close_time'] and settlements[-1]['close_time']:
        start_time = settlements[0]['close_time']
        end_time = settlements[-1]['close_time']
        print(f"Time range: {start_time} to {end_time}")

    # Analysis
    accuracy_stats = analyze_prediction_accuracy(settlements)
    calibration_stats = analyze_kalshi_calibration(settlements)
    mispricings = detect_mispricing_patterns(settlements)
    pnl_results = calculate_hypothetical_pnl(settlements)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    print(f"\n✓ Analyzed {len(settlements)} settled markets")
    print(f"✓ Kalshi accuracy: {accuracy_stats['kalshi_accuracy']:.1%}")
    print(f"✓ Calibration error: {calibration_stats['avg_calibration_error']:.1%}")

    # Best strategy
    best_strategy = max(pnl_results.items(), key=lambda x: x[1]['total_pnl_cents'])
    print(f"\n🏆 Best strategy: {best_strategy[0]}")
    print(f"   {best_strategy[1]['trades']} trades, "
          f"{best_strategy[1]['avg_pnl_cents']:.1f}¢ avg, "
          f"{best_strategy[1]['total_pnl_cents']:.0f}¢ total, "
          f"{best_strategy[1]['win_rate']:.1%} WR")

    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)

    if accuracy_stats['kalshi_accuracy'] > 0.8:
        print("✅ Kalshi prices are highly predictive (>80% accuracy)")
    elif accuracy_stats['kalshi_accuracy'] > 0.6:
        print("⚠️  Kalshi prices are moderately predictive (60-80% accuracy)")
    else:
        print("❌ Kalshi prices have low predictive power (<60% accuracy)")

    if calibration_stats['avg_calibration_error'] < 0.1:
        print("✅ Kalshi is well-calibrated (prices match probabilities)")
    else:
        print("⚠️  Kalshi has calibration errors (mispricing opportunities)")

    if best_strategy[1]['total_pnl_cents'] > 0:
        print(f"✅ Profitable strategy exists: {best_strategy[0]}")
        print(f"   Expected: {best_strategy[1]['avg_pnl_cents']:.1f}¢ per trade")
    else:
        print("❌ No profitable strategy found in historical data")

    return 0


if __name__ == '__main__':
    sys.exit(main())
