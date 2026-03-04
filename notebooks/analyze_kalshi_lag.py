#!/usr/bin/env python3
"""
Analyze lag between Bitcoin spot price and Kalshi market prices.
Identify arbitrage opportunities from latency differences.
"""

import sqlite3
import os
from pathlib import Path
from datetime import timedelta

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

# Configuration
PROJECT_ROOT = Path('/Users/raine/tradingutils')
db_path = str(PROJECT_ROOT / 'data' / 'btc_ob_48h.db')
output_dir = PROJECT_ROOT / 'notebooks' / 'results'
output_dir.mkdir(parents=True, exist_ok=True)

print("="*80)
print("KALSHI LAG ANALYSIS - Finding Arbitrage Opportunities")
print("="*80)

# Load Binance spot prices
print("\n1. Loading Binance spot prices...")
query_binance = '''
SELECT ts, price, qty
FROM binance_trades
ORDER BY ts
'''

df_binance = pd.read_sql_query(query_binance, sqlite3.connect(db_path))
df_binance['timestamp'] = pd.to_datetime(df_binance['ts'], unit='s')

# Aggregate to 100ms intervals for high-resolution analysis
df_binance['total_value'] = df_binance['price'] * df_binance['qty']
binance_100ms = df_binance.set_index('timestamp').resample('100ms').agg({
    'price': 'last',
    'qty': 'sum',
    'total_value': 'sum'
}).dropna()
binance_100ms['vwap'] = binance_100ms['total_value'] / binance_100ms['qty']
binance_100ms['price'] = binance_100ms['price'].fillna(binance_100ms['vwap'])

print(f"   Loaded {len(df_binance):,} trades → {len(binance_100ms):,} 100ms intervals")
print(f"   Price range: ${binance_100ms['price'].min():.2f} - ${binance_100ms['price'].max():.2f}")

# Load Kalshi snapshots
print("\n2. Loading Kalshi market snapshots...")
query_kalshi = '''
SELECT ts, ticker, yes_bid, yes_ask, yes_mid, floor_strike, volume
FROM kalshi_snapshots
WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
ORDER BY ts
'''

df_kalshi = pd.read_sql_query(query_kalshi, sqlite3.connect(db_path))
df_kalshi['timestamp'] = pd.to_datetime(df_kalshi['ts'], unit='s')
df_kalshi['spread_cents'] = df_kalshi['yes_ask'] - df_kalshi['yes_bid']

print(f"   Loaded {len(df_kalshi):,} snapshots")
print(f"   Unique markets: {df_kalshi['ticker'].nunique()}")
print(f"   Avg spread: {df_kalshi['spread_cents'].mean():.1f}¢")

# Focus on a few active markets for detailed analysis
top_markets = df_kalshi.groupby('ticker')['volume'].max().nlargest(10).index.tolist()
print(f"\n3. Analyzing top {len(top_markets)} markets by volume...")

# Analysis per market
results = []

for ticker in top_markets:
    market_data = df_kalshi[df_kalshi['ticker'] == ticker].copy()
    strike = market_data['floor_strike'].iloc[0]

    # Calculate fair value based on spot price
    # For a binary "BTC > strike" market, fair value ≈ how far above strike
    # Simple model: if spot > strike, YES should be high, else low

    # Merge with binance data (forward fill to get spot price at each Kalshi update)
    market_data = market_data.set_index('timestamp')
    binance_resampled = binance_100ms[['price']].reindex(
        market_data.index, method='ffill'
    )
    market_data['spot_price'] = binance_resampled['price'].values

    # Calculate "fair value" for YES (0-100 cents)
    # Simple heuristic: distance from strike normalized
    market_data['distance_from_strike'] = market_data['spot_price'] - strike

    # Fair value: sigmoid function centered at strike
    # If spot = strike, fair = 50¢. As spot moves away, asymptote to 0 or 100
    # Using a soft threshold of $100 to normalize
    market_data['fair_value_cents'] = 100 / (1 + np.exp(-market_data['distance_from_strike'] / 100))

    # Calculate mispricing
    market_data['yes_mid_mispricing'] = market_data['yes_mid'] - market_data['fair_value_cents']
    market_data['yes_bid_mispricing'] = market_data['yes_bid'] - market_data['fair_value_cents']
    market_data['yes_ask_mispricing'] = market_data['yes_ask'] - market_data['fair_value_cents']

    # Calculate spot price changes
    market_data['spot_change'] = market_data['spot_price'].diff()
    market_data['spot_change_pct'] = market_data['spot_price'].pct_change() * 100

    # Calculate Kalshi price changes
    market_data['kalshi_mid_change'] = market_data['yes_mid'].diff()

    # Calculate lag by looking at correlation at different time shifts
    # We'll shift Kalshi prices backward in time to see if they correlate with earlier spot moves
    spot_changes = market_data['spot_change'].dropna()
    kalshi_changes = market_data['kalshi_mid_change'].dropna()

    # Find common timestamps
    common_idx = spot_changes.index.intersection(kalshi_changes.index)
    if len(common_idx) < 100:
        continue

    spot_aligned = spot_changes.loc[common_idx]
    kalshi_aligned = kalshi_changes.loc[common_idx]

    # Test correlation at different lags (0s to 5s)
    lag_range = range(0, 51)  # 0 to 5 seconds in 100ms increments
    correlations = []

    for lag in lag_range:
        if lag == 0:
            corr = spot_aligned.corr(kalshi_aligned)
        else:
            # Shift Kalshi backward by lag periods
            kalshi_lagged = kalshi_aligned.shift(-lag)
            corr = spot_aligned.corr(kalshi_lagged)
        correlations.append(corr)

    # Find lag with maximum correlation
    max_corr_idx = np.nanargmax(correlations)
    max_corr = correlations[max_corr_idx]
    optimal_lag_ms = max_corr_idx * 100

    # Identify arbitrage opportunities
    # Buy YES when: spot moved up but Kalshi hasn't adjusted (underprice)
    # Buy NO when: spot moved down but Kalshi hasn't adjusted (overprice)

    # Define opportunity: mispricing > 5¢ AND spread < 5¢
    market_data['buy_yes_opp'] = (
        (market_data['yes_ask_mispricing'] < -5) &  # Kalshi too low
        (market_data['spread_cents'] <= 5)
    )
    market_data['buy_no_opp'] = (
        (market_data['yes_bid_mispricing'] > 5) &  # Kalshi too high
        (market_data['spread_cents'] <= 5)
    )

    num_yes_opps = market_data['buy_yes_opp'].sum()
    num_no_opps = market_data['buy_no_opp'].sum()

    # Calculate average edge when opportunities occur
    avg_yes_edge = abs(market_data.loc[market_data['buy_yes_opp'], 'yes_ask_mispricing'].mean()) if num_yes_opps > 0 else 0
    avg_no_edge = abs(market_data.loc[market_data['buy_no_opp'], 'yes_bid_mispricing'].mean()) if num_no_opps > 0 else 0

    results.append({
        'ticker': ticker[-10:],  # Last 10 chars
        'strike': strike,
        'snapshots': len(market_data),
        'optimal_lag_ms': optimal_lag_ms,
        'max_correlation': max_corr,
        'avg_spread': market_data['spread_cents'].mean(),
        'avg_mispricing': market_data['yes_mid_mispricing'].abs().mean(),
        'yes_opportunities': num_yes_opps,
        'no_opportunities': num_no_opps,
        'avg_yes_edge': avg_yes_edge,
        'avg_no_edge': avg_no_edge,
        'total_opportunities': num_yes_opps + num_no_opps,
    })

    print(f"   {ticker[-15:]:20s} | Lag: {optimal_lag_ms:4.0f}ms | Corr: {max_corr:5.3f} | "
          f"Opps: {num_yes_opps + num_no_opps:4d} | Edge: {(avg_yes_edge + avg_no_edge)/2:4.1f}¢")

# Results DataFrame
df_results = pd.DataFrame(results)

print("\n" + "="*80)
print("SUMMARY STATISTICS")
print("="*80)

print(f"\nAverage optimal lag: {df_results['optimal_lag_ms'].mean():.0f}ms "
      f"(median: {df_results['optimal_lag_ms'].median():.0f}ms)")
print(f"Average correlation at optimal lag: {df_results['max_correlation'].mean():.3f}")
print(f"Average spread: {df_results['avg_spread'].mean():.2f}¢")
print(f"Average mispricing magnitude: {df_results['avg_mispricing'].mean():.2f}¢")

print(f"\nARBITRAGE OPPORTUNITIES:")
print(f"  Total opportunities: {df_results['total_opportunities'].sum():,}")
print(f"  YES opportunities: {df_results['yes_opportunities'].sum():,}")
print(f"  NO opportunities: {df_results['no_opportunities'].sum():,}")
print(f"  Avg edge per YES opp: {df_results['avg_yes_edge'].mean():.2f}¢")
print(f"  Avg edge per NO opp: {df_results['avg_no_edge'].mean():.2f}¢")

# Calculate opportunity rate (opps per hour)
total_duration_hours = (df_kalshi['timestamp'].max() - df_kalshi['timestamp'].min()).total_seconds() / 3600
opp_rate = df_results['total_opportunities'].sum() / total_duration_hours

print(f"\nOPPORTUNITY RATE: {opp_rate:.1f} opportunities/hour")
print(f"  ({opp_rate * 24:.0f} per day)")

# Save detailed results
results_file = output_dir / 'kalshi_lag_analysis.csv'
df_results.to_csv(results_file, index=False)
print(f"\nDetailed results saved to: {results_file}")

# Visualization 1: Lag distribution
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# Plot 1: Lag distribution
ax1 = axes[0, 0]
ax1.hist(df_results['optimal_lag_ms'], bins=20, edgecolor='black', alpha=0.7)
ax1.axvline(df_results['optimal_lag_ms'].mean(), color='red', linestyle='--',
            linewidth=2, label=f'Mean: {df_results["optimal_lag_ms"].mean():.0f}ms')
ax1.axvline(df_results['optimal_lag_ms'].median(), color='orange', linestyle='--',
            linewidth=2, label=f'Median: {df_results["optimal_lag_ms"].median():.0f}ms')
ax1.set_xlabel('Optimal Lag (ms)', fontsize=12, fontweight='bold')
ax1.set_ylabel('Number of Markets', fontsize=12, fontweight='bold')
ax1.set_title('Distribution of Kalshi Response Lag', fontsize=14, fontweight='bold')
ax1.legend()
ax1.grid(True, alpha=0.3)

# Plot 2: Correlation vs Lag
ax2 = axes[0, 1]
ax2.scatter(df_results['optimal_lag_ms'], df_results['max_correlation'],
           s=100, alpha=0.6, edgecolors='black')
ax2.set_xlabel('Optimal Lag (ms)', fontsize=12, fontweight='bold')
ax2.set_ylabel('Max Correlation', fontsize=12, fontweight='bold')
ax2.set_title('Correlation Strength vs Lag Time', fontsize=14, fontweight='bold')
ax2.grid(True, alpha=0.3)

# Plot 3: Opportunities by market
ax3 = axes[1, 0]
markets_sorted = df_results.sort_values('total_opportunities', ascending=True)
y_pos = np.arange(len(markets_sorted))
ax3.barh(y_pos, markets_sorted['total_opportunities'], alpha=0.7, edgecolor='black')
ax3.set_yticks(y_pos)
ax3.set_yticklabels(markets_sorted['ticker'], fontsize=8)
ax3.set_xlabel('Number of Opportunities', fontsize=12, fontweight='bold')
ax3.set_title('Arbitrage Opportunities by Market', fontsize=14, fontweight='bold')
ax3.grid(True, alpha=0.3, axis='x')

# Plot 4: Edge size distribution
ax4 = axes[1, 1]
all_edges = []
for _, row in df_results.iterrows():
    if row['yes_opportunities'] > 0:
        all_edges.extend([row['avg_yes_edge']] * int(row['yes_opportunities']))
    if row['no_opportunities'] > 0:
        all_edges.extend([row['avg_no_edge']] * int(row['no_opportunities']))

if all_edges:
    ax4.hist(all_edges, bins=30, edgecolor='black', alpha=0.7, color='green')
    ax4.axvline(np.mean(all_edges), color='red', linestyle='--', linewidth=2,
               label=f'Mean: {np.mean(all_edges):.2f}¢')
    ax4.set_xlabel('Edge Size (cents)', fontsize=12, fontweight='bold')
    ax4.set_ylabel('Frequency', fontsize=12, fontweight='bold')
    ax4.set_title('Distribution of Edge Sizes', fontsize=14, fontweight='bold')
    ax4.legend()
    ax4.grid(True, alpha=0.3)

plt.tight_layout()
chart_file = output_dir / 'kalshi_lag_analysis.png'
plt.savefig(chart_file, dpi=150, bbox_inches='tight')
print(f"Charts saved to: {chart_file}")
plt.close()

# Detailed example: Show one market's behavior
print("\n" + "="*80)
print("EXAMPLE: Detailed view of most active market")
print("="*80)

most_active = df_results.sort_values('total_opportunities', ascending=False).iloc[0]
ticker = most_active['ticker']

# Get the full ticker from original data
full_ticker = [t for t in top_markets if t.endswith(ticker)][0]
market_data = df_kalshi[df_kalshi['ticker'] == full_ticker].copy()
market_data = market_data.set_index('timestamp')

# Merge with spot
binance_resampled = binance_100ms[['price']].reindex(market_data.index, method='ffill')
market_data['spot_price'] = binance_resampled['price'].values
market_data['distance_from_strike'] = market_data['spot_price'] - market_data['floor_strike'].iloc[0]
market_data['fair_value_cents'] = 100 / (1 + np.exp(-market_data['distance_from_strike'] / 100))
market_data['yes_mid_mispricing'] = market_data['yes_mid'] - market_data['fair_value_cents']

# Plot example market
fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True)

ax1 = axes[0]
ax1.plot(market_data.index, market_data['spot_price'], label='Binance Spot', linewidth=1)
ax1.axhline(y=market_data['floor_strike'].iloc[0], color='red', linestyle='--',
           label=f'Strike: ${market_data["floor_strike"].iloc[0]:.0f}')
ax1.set_ylabel('BTC Price (USD)', fontsize=12, fontweight='bold')
ax1.set_title(f'Market: {full_ticker}', fontsize=14, fontweight='bold')
ax1.legend()
ax1.grid(True, alpha=0.3)

ax2 = axes[1]
ax2.plot(market_data.index, market_data['yes_mid'], label='Kalshi YES Mid', linewidth=1, color='blue')
ax2.plot(market_data.index, market_data['fair_value_cents'], label='Fair Value',
        linewidth=1, color='orange', linestyle='--', alpha=0.7)
ax2.fill_between(market_data.index, market_data['yes_bid'], market_data['yes_ask'],
                alpha=0.3, color='gray', label='Bid-Ask Spread')
ax2.set_ylabel('Price (cents)', fontsize=12, fontweight='bold')
ax2.set_title('Kalshi Price vs Fair Value', fontsize=14, fontweight='bold')
ax2.legend()
ax2.grid(True, alpha=0.3)

ax3 = axes[2]
ax3.plot(market_data.index, market_data['yes_mid_mispricing'], linewidth=1, color='green')
ax3.axhline(y=0, color='black', linestyle='-', linewidth=1)
ax3.axhline(y=5, color='red', linestyle='--', alpha=0.5, label='Overbought threshold')
ax3.axhline(y=-5, color='red', linestyle='--', alpha=0.5, label='Oversold threshold')
ax3.fill_between(market_data.index, 0, market_data['yes_mid_mispricing'],
                where=(market_data['yes_mid_mispricing'] > 5), alpha=0.3, color='red')
ax3.fill_between(market_data.index, 0, market_data['yes_mid_mispricing'],
                where=(market_data['yes_mid_mispricing'] < -5), alpha=0.3, color='green')
ax3.set_ylabel('Mispricing (cents)', fontsize=12, fontweight='bold')
ax3.set_xlabel('Time', fontsize=12, fontweight='bold')
ax3.set_title('Mispricing (Positive = Kalshi too high, Negative = Kalshi too low)',
             fontsize=14, fontweight='bold')
ax3.legend()
ax3.grid(True, alpha=0.3)

plt.tight_layout()
example_file = output_dir / 'kalshi_lag_example.png'
plt.savefig(example_file, dpi=150, bbox_inches='tight')
print(f"Example chart saved to: {example_file}")

print("\n" + "="*80)
print("ANALYSIS COMPLETE")
print("="*80)
print(f"\nKey Findings:")
print(f"  1. Kalshi lags Binance by {df_results['optimal_lag_ms'].median():.0f}ms on average")
print(f"  2. {df_results['total_opportunities'].sum():,} arbitrage opportunities identified")
print(f"  3. Average edge: {df_results[['avg_yes_edge', 'avg_no_edge']].mean().mean():.2f}¢ per trade")
print(f"  4. Opportunity rate: {opp_rate:.1f} per hour")

# Estimate P&L potential
avg_edge = df_results[['avg_yes_edge', 'avg_no_edge']].mean().mean()
profit_per_opp = avg_edge * 0.01  # Convert cents to dollars per contract
potential_daily_profit = profit_per_opp * (opp_rate * 24)

print(f"\nPOTENTIAL DAILY P&L (1 contract per trade):")
print(f"  Theoretical max: ${potential_daily_profit:.2f}/day")
print(f"  After 2¢ fees: ${potential_daily_profit - (opp_rate * 24 * 0.02):.2f}/day")
print(f"  Assuming 50% capture rate: ${(potential_daily_profit - (opp_rate * 24 * 0.02)) * 0.5:.2f}/day")

print("\n" + "="*80)
