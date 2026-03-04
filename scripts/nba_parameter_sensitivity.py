#!/usr/bin/env python3
"""
NBA Underdog Strategy - Parameter Sensitivity Analysis

Tests different price ranges and stop loss levels to find optimal parameters.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import itertools
from pathlib import Path

# Define parameter ranges
PRICE_RANGES = [
    (10, 15, '10-15¢'),
    (10, 20, '10-20¢'),
    (15, 20, '15-20¢'),
    (15, 25, '15-25¢'),
    (20, 25, '20-25¢'),
    (20, 30, '20-30¢'),
    (25, 30, '25-30¢'),
    (10, 25, '10-25¢'),
    (10, 30, '10-30¢'),
    (15, 30, '15-30¢'),
]

STOP_LOSS_LEVELS = [0, 10, 15, 20, 22, 25, 30, 40, 50]  # 0 = no stop loss


def simulate_strategy(df, min_price, max_price, stop_loss):
    """Simulate strategy with given price range and stop loss."""
    # Filter for price range
    trades = df[(df['open_price'] >= min_price) & (df['open_price'] <= max_price)].copy()

    if len(trades) == 0:
        return {
            'num_trades': 0,
            'win_rate': 0,
            'avg_pnl': 0,
            'total_pnl': 0,
            'roi': 0,
            'max_dd': 0,
            'stopped_out': 0,
            'stopped_out_pct': 0,
        }

    # Calculate P&L for each trade
    results = []
    for _, trade in trades.iterrows():
        entry_price = trade['open_price']

        # Check if stopped out (price dropped below stop loss)
        # Stop loss only applies if it's below entry price
        if stop_loss > 0 and stop_loss < entry_price and trade['low_price'] <= stop_loss:
            exit_price = stop_loss
            stopped = True
        else:
            # Exit at settlement
            exit_price = 100 if trade['underdog_won'] else 0
            stopped = False

        pnl = exit_price - entry_price
        results.append({
            'pnl': pnl,
            'roi': pnl / entry_price if entry_price > 0 else 0,
            'won': pnl > 0,
            'stopped': stopped,
        })

    results_df = pd.DataFrame(results)

    # Calculate metrics
    num_trades = len(results_df)
    wins = results_df['won'].sum()
    win_rate = wins / num_trades if num_trades > 0 else 0
    avg_pnl = results_df['pnl'].mean()
    total_pnl = results_df['pnl'].sum()

    # ROI as a percentage
    avg_entry = trades['open_price'].mean()
    roi = (avg_pnl / avg_entry * 100) if avg_entry > 0 else 0

    # Max drawdown
    cumulative_pnl = results_df['pnl'].cumsum()
    running_max = cumulative_pnl.expanding().max()
    drawdown = running_max - cumulative_pnl
    max_dd = drawdown.max() if len(drawdown) > 0 else 0

    # Stop loss stats
    stopped_out = results_df['stopped'].sum()
    stopped_out_pct = stopped_out / num_trades * 100 if num_trades > 0 else 0

    return {
        'num_trades': num_trades,
        'win_rate': win_rate * 100,
        'avg_pnl': avg_pnl,
        'total_pnl': total_pnl,
        'roi': roi,
        'max_dd': max_dd,
        'stopped_out': stopped_out,
        'stopped_out_pct': stopped_out_pct,
    }


def main():
    print("=" * 80)
    print("NBA UNDERDOG STRATEGY - PARAMETER SENSITIVITY ANALYSIS")
    print("=" * 80)
    print()

    # Load data
    print("Loading data...")
    df = pd.read_csv('data/nba_underdog_parameter_test.csv')
    print(f"✓ Loaded {len(df)} games")
    print(f"  Win rate: {df['underdog_won'].mean():.1%}")
    print(f"  Price range: {df['open_price'].min()}¢ to {df['open_price'].max()}¢")
    print()

    print(f"Testing {len(PRICE_RANGES)} price ranges × {len(STOP_LOSS_LEVELS)} stop loss levels = {len(PRICE_RANGES) * len(STOP_LOSS_LEVELS)} combinations")
    print()

    # Run simulations
    results = []
    for (min_p, max_p, price_label), stop_loss in itertools.product(PRICE_RANGES, STOP_LOSS_LEVELS):
        metrics = simulate_strategy(df, min_p, max_p, stop_loss)
        results.append({
            'price_range': price_label,
            'min_price': min_p,
            'max_price': max_p,
            'stop_loss': stop_loss,
            **metrics
        })

    results_df = pd.DataFrame(results)

    # Top configurations by ROI
    print("=" * 80)
    print("TOP 15 CONFIGURATIONS BY ROI")
    print("=" * 80)
    top_roi = results_df.nlargest(15, 'roi')[[
        'price_range', 'stop_loss', 'num_trades', 'win_rate',
        'avg_pnl', 'roi', 'max_dd', 'stopped_out_pct'
    ]]
    print(top_roi.to_string(index=False))
    print()

    # Current configuration
    print("=" * 80)
    print("CURRENT CONFIGURATION (10-30¢, 22¢ stop)")
    print("=" * 80)
    current = results_df[(results_df['min_price'] == 10) &
                         (results_df['max_price'] == 30) &
                         (results_df['stop_loss'] == 22)].iloc[0]
    print(f"  Trades: {current['num_trades']:.0f}")
    print(f"  Win Rate: {current['win_rate']:.1f}%")
    print(f"  Avg P&L: {current['avg_pnl']:.2f}¢")
    print(f"  Total P&L: {current['total_pnl']:.0f}¢")
    print(f"  ROI: {current['roi']:.2f}%")
    print(f"  Max DD: {current['max_dd']:.0f}¢")
    print(f"  Stopped out: {current['stopped_out_pct']:.1f}%")
    print(f"  Rank: {(results_df['roi'] > current['roi']).sum() + 1} / {len(results_df)}")
    print()

    # Analysis by price range
    print("=" * 80)
    print("PERFORMANCE BY PRICE RANGE (averaged across stop losses)")
    print("=" * 80)
    by_price = results_df.groupby('price_range').agg({
        'num_trades': 'first',
        'win_rate': 'mean',
        'avg_pnl': 'mean',
        'roi': 'mean',
    }).round(2).sort_values('roi', ascending=False)
    print(by_price.to_string())
    print()

    # Analysis by stop loss
    print("=" * 80)
    print("PERFORMANCE BY STOP LOSS (averaged across price ranges)")
    print("=" * 80)
    by_stop = results_df.groupby('stop_loss').agg({
        'num_trades': 'mean',
        'win_rate': 'mean',
        'avg_pnl': 'mean',
        'roi': 'mean',
        'stopped_out_pct': 'mean',
    }).round(2).sort_values('roi', ascending=False)
    print(by_stop.to_string())
    print()

    # Create visualizations
    print("Creating visualizations...")

    # Heatmap
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    pivot_roi = results_df.pivot(index='price_range', columns='stop_loss', values='roi')
    pivot_pnl = results_df.pivot(index='price_range', columns='stop_loss', values='total_pnl')
    pivot_trades = results_df.pivot(index='price_range', columns='stop_loss', values='num_trades')
    pivot_winrate = results_df.pivot(index='price_range', columns='stop_loss', values='win_rate')

    sns.heatmap(pivot_roi, annot=True, fmt='.1f', cmap='RdYlGn', center=0,
                ax=axes[0,0], cbar_kws={'label': 'ROI %'})
    axes[0,0].set_title('ROI % by Price Range and Stop Loss', fontsize=14, fontweight='bold')

    sns.heatmap(pivot_pnl, annot=True, fmt='.0f', cmap='RdYlGn', center=0,
                ax=axes[0,1], cbar_kws={'label': 'Total P&L (¢)'})
    axes[0,1].set_title('Total P&L by Price Range and Stop Loss', fontsize=14, fontweight='bold')

    sns.heatmap(pivot_trades, annot=True, fmt='.0f', cmap='Blues',
                ax=axes[1,0], cbar_kws={'label': 'Trades'})
    axes[1,0].set_title('Number of Trades', fontsize=14, fontweight='bold')

    sns.heatmap(pivot_winrate, annot=True, fmt='.1f', cmap='RdYlGn', center=50,
                ax=axes[1,1], cbar_kws={'label': 'Win Rate %'})
    axes[1,1].set_title('Win Rate %', fontsize=14, fontweight='bold')

    plt.tight_layout()
    plt.savefig('data/nba_param_sensitivity.png', dpi=150, bbox_inches='tight')
    print("✓ Saved heatmaps to data/nba_param_sensitivity.png")

    # Save results
    results_df.to_csv('data/nba_param_sensitivity_results.csv', index=False)
    print("✓ Saved results to data/nba_param_sensitivity_results.csv")
    print()

    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == '__main__':
    main()
