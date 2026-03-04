#!/usr/bin/env python3
"""Test if crypto latency edge exists using probe data.

Analyzes historical data to validate the hypothesis that Kraken
spot prices lead Kalshi derivative prices by 5-30 seconds.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import numpy as np

def test_edge(db_path: str, verbose: bool = False) -> dict:
    """Test for latency edge in probe data.

    Returns dict with edge metrics.
    """
    print(f"\n📊 Testing Latency Edge: {db_path}")
    print("=" * 70)

    with sqlite3.connect(db_path) as conn:
        # Check what tables exist
        tables = pd.read_sql(
            "SELECT name FROM sqlite_master WHERE type='table'",
            conn
        )['name'].tolist()

        if verbose:
            print(f"Tables: {', '.join(tables)}")

        # Get Kraken spot prices
        if 'kraken_snapshots' in tables:
            kraken = pd.read_sql("""
                SELECT ts, avg_60s as kraken_price
                FROM kraken_snapshots
                ORDER BY ts
            """, conn)
        else:
            print("❌ No kraken_snapshots table found")
            return {}

        # Get Kalshi prices
        if 'kalshi_snapshots' in tables:
            kalshi = pd.read_sql("""
                SELECT
                    ts,
                    yes_mid as kalshi_prob,
                    floor_strike as strike,
                    ticker
                FROM kalshi_snapshots
                WHERE yes_mid IS NOT NULL AND floor_strike IS NOT NULL
                ORDER BY ts
            """, conn)
        else:
            print("❌ No kalshi_snapshots table found")
            return {}

        # Get settlement data if available
        settlements = None
        if 'market_settlements' in tables:
            settlements = pd.read_sql("""
                SELECT
                    ticker,
                    settled_yes,
                    kraken_was_right,
                    kalshi_predicted_yes,
                    kalshi_last_mid,
                    floor_strike
                FROM market_settlements
            """, conn)

    if len(kraken) == 0 or len(kalshi) == 0:
        print("❌ Insufficient data")
        return {}

    print(f"\n📈 Data Summary:")
    print(f"  Kraken snapshots:    {len(kraken):,}")
    print(f"  Kalshi snapshots:    {len(kalshi):,}")
    print(f"  Time range:          {(kraken['ts'].max() - kraken['ts'].min()) / 3600:.1f} hours")

    # Merge on timestamp (1-second tolerance)
    kraken['ts_int'] = (kraken['ts'] // 1).astype(int)
    kalshi['ts_int'] = (kalshi['ts'] // 1).astype(int)

    df = pd.merge(
        kalshi,
        kraken[['ts_int', 'kraken_price']],
        on='ts_int',
        how='inner'
    )

    df = df.dropna()

    if len(df) < 50:
        print(f"❌ Only {len(df)} matched samples - insufficient for analysis")
        return {}

    print(f"  Matched samples:     {len(df):,}")

    # Calculate disagreements
    df['kraken_says_yes'] = df['kraken_price'] > df['strike']
    df['kalshi_says_yes'] = df['kalshi_prob'] > 50
    df['disagree'] = df['kraken_says_yes'] != df['kalshi_says_yes']

    n_disagree = df['disagree'].sum()
    disagree_rate = n_disagree / len(df) * 100

    print(f"\n🔍 Agreement Analysis:")
    print(f"  Agreement rate:      {100 - disagree_rate:.1f}%")
    print(f"  Disagreements:       {n_disagree:,} / {len(df):,} ({disagree_rate:.1f}%)")

    results = {
        'total_samples': len(df),
        'disagreement_rate': disagree_rate,
        'disagreement_count': n_disagree,
    }

    # When they disagree, calculate the edge
    if n_disagree > 0:
        mispriced = df[df['disagree']].copy()

        # Edge = how far from fair value (50¢) when they disagree
        mispriced['edge_cents'] = np.abs(mispriced['kalshi_prob'] - 50)

        # Better edge calc: distance to correct side
        # If Kraken says YES but Kalshi < 50, edge = 50 - kalshi_prob
        # If Kraken says NO but Kalshi > 50, edge = kalshi_prob - 50
        mispriced['exploitable_edge'] = np.where(
            mispriced['kraken_says_yes'],
            100 - mispriced['kalshi_prob'],  # Buy YES
            mispriced['kalshi_prob']          # Buy NO
        )

        avg_edge = mispriced['exploitable_edge'].mean()
        median_edge = mispriced['exploitable_edge'].median()

        print(f"\n💰 Edge When Prices Disagree:")
        print(f"  Average edge:        {avg_edge:.1f}¢")
        print(f"  Median edge:         {median_edge:.1f}¢")
        print(f"  Edge > 5¢:           {(mispriced['exploitable_edge'] > 5).sum():,} ({(mispriced['exploitable_edge'] > 5).mean()*100:.1f}%)")
        print(f"  Edge > 10¢:          {(mispriced['exploitable_edge'] > 10).sum():,} ({(mispriced['exploitable_edge'] > 10).mean()*100:.1f}%)")
        print(f"  Edge > 20¢:          {(mispriced['exploitable_edge'] > 20).sum():,} ({(mispriced['exploitable_edge'] > 20).mean()*100:.1f}%)")

        results['avg_edge_cents'] = avg_edge
        results['median_edge_cents'] = median_edge
        results['edge_gt_5c'] = (mispriced['exploitable_edge'] > 5).sum()
        results['edge_gt_10c'] = (mispriced['exploitable_edge'] > 10).sum()

        # Calculate fee-adjusted edge (Kalshi fee = 7% of profit)
        mispriced['profit_if_win'] = mispriced['exploitable_edge']
        mispriced['fee'] = mispriced['profit_if_win'] * 0.07
        mispriced['net_edge'] = mispriced['profit_if_win'] - mispriced['fee']

        net_avg = mispriced['net_edge'].mean()
        print(f"\n💸 After 7% Kalshi Fee:")
        print(f"  Net average edge:    {net_avg:.1f}¢")
        print(f"  Net edge > 3¢:       {(mispriced['net_edge'] > 3).sum():,} ({(mispriced['net_edge'] > 3).mean()*100:.1f}%)")

        results['net_avg_edge_cents'] = net_avg

    # Settlement analysis
    if settlements is not None and len(settlements) > 0:
        print(f"\n✅ Settlement Scorecard ({len(settlements)} markets):")

        kraken_wins = settlements['kraken_was_right'].sum()
        total_settled = len(settlements)

        # When they disagreed
        disagreed = settlements[
            settlements['kalshi_predicted_yes'] != (settlements['settled_yes'] == 1)
        ]

        if len(disagreed) > 0:
            kraken_wins_when_disagree = disagreed['kraken_was_right'].sum()
            print(f"  Kraken correct:      {kraken_wins}/{total_settled} ({kraken_wins/total_settled*100:.1f}%)")
            print(f"  When they disagreed: {kraken_wins_when_disagree}/{len(disagreed)} Kraken wins")
            print(f"  → Betting with Kraken edge: {kraken_wins_when_disagree/len(disagreed)*100:.1f}% win rate")

            results['settlement_kraken_winrate'] = kraken_wins / total_settled
            results['settlement_edge_winrate'] = kraken_wins_when_disagree / len(disagreed)
        else:
            print(f"  Perfect agreement - no edge to test")

    # Lag analysis (if we have timestamps)
    print(f"\n⏱️  Latency Analysis:")

    # Calculate how long Kalshi stays at same price
    df_sorted = df.sort_values('ts')
    df_sorted['kalshi_changed'] = df_sorted['kalshi_prob'].diff().abs() > 0.5

    # Group consecutive unchanged periods
    df_sorted['change_group'] = df_sorted['kalshi_changed'].cumsum()
    stale_periods = df_sorted.groupby('change_group').agg({
        'ts': ['first', 'last', 'count']
    })
    stale_periods.columns = ['start_ts', 'end_ts', 'count']
    stale_periods['duration'] = stale_periods['end_ts'] - stale_periods['start_ts']

    # Filter to periods > 1 second
    stale_periods = stale_periods[stale_periods['duration'] > 1]

    if len(stale_periods) > 0:
        avg_stale = stale_periods['duration'].mean()
        median_stale = stale_periods['duration'].median()
        max_stale = stale_periods['duration'].max()

        print(f"  Kalshi staleness:")
        print(f"    Avg unchanged:     {avg_stale:.1f}s")
        print(f"    Median unchanged:  {median_stale:.1f}s")
        print(f"    Max unchanged:     {max_stale:.1f}s")
        print(f"    Stale periods:     {len(stale_periods):,}")

        results['avg_stale_seconds'] = avg_stale
        results['median_stale_seconds'] = median_stale

    # Verdict
    print(f"\n{'='*70}")
    print("🎯 VERDICT:")

    if n_disagree == 0:
        print("  ❌ NO EDGE - Perfect agreement between Kraken and Kalshi")
        results['verdict'] = 'no_edge'
    elif disagree_rate < 1:
        print(f"  ⚠️  WEAK EDGE - Only {disagree_rate:.1f}% disagreement (need >5%)")
        results['verdict'] = 'weak'
    elif results.get('net_avg_edge_cents', 0) < 3:
        print(f"  ⚠️  UNPROFITABLE - Net edge {results.get('net_avg_edge_cents', 0):.1f}¢ after fees")
        results['verdict'] = 'unprofitable'
    else:
        print(f"  ✅ EDGE EXISTS!")
        print(f"     - {disagree_rate:.1f}% disagreement rate")
        print(f"     - {results.get('net_avg_edge_cents', 0):.1f}¢ net edge after fees")
        if 'avg_stale_seconds' in results:
            print(f"     - {results['avg_stale_seconds']:.1f}s average Kalshi staleness")
        print(f"     → Exploit window: {results.get('avg_stale_seconds', 10):.0f}s")
        results['verdict'] = 'edge_exists'

    print("=" * 70)

    return results


def main():
    parser = argparse.ArgumentParser(description="Test crypto latency edge")
    parser.add_argument(
        "--db",
        default="data/btc_ob_48h.db",
        help="Probe database path"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"❌ Database not found: {args.db}")
        print("\nAvailable databases:")
        for db in sorted(Path("data").glob("*.db")):
            print(f"  {db}")
        sys.exit(1)

    results = test_edge(args.db, verbose=args.verbose)

    # Exit code: 0 if edge exists, 1 otherwise
    sys.exit(0 if results.get('verdict') == 'edge_exists' else 1)


if __name__ == "__main__":
    main()
