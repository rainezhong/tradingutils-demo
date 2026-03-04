#!/usr/bin/env python3
"""
Visualize BTC price movements from Binance and Kalshi with strategy entry/exit points.
"""

import sqlite3
import os
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Configuration
PROJECT_ROOT = Path('/Users/raine/tradingutils')
db_path = str(PROJECT_ROOT / 'data' / 'btc_ob_48h.db')
trades_db_path = str(PROJECT_ROOT / 'data' / 'portfolio_trades.db')
output_dir = PROJECT_ROOT / 'notebooks' / 'results'
output_dir.mkdir(parents=True, exist_ok=True)

# Moving average window (seconds)
ma_window = 60

print("="*60)
print("BTC Price Movement Visualization")
print("="*60)

# Verify database exists
if not os.path.exists(db_path):
    raise FileNotFoundError(f"Database not found: {db_path}")

print(f"\nDatabase: {db_path}")
print(f"Size: {os.path.getsize(db_path) / 1024 / 1024:.1f} MB\n")

# Show available tables
conn = sqlite3.connect(db_path)
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print(f"Tables: {', '.join(tables)}\n")
conn.close()

# Load Binance spot prices
print("Loading Binance trades...")
query = '''
SELECT
    ts,
    price,
    qty
FROM binance_trades
ORDER BY ts
'''

df_binance = pd.read_sql_query(query, sqlite3.connect(db_path))
print(f"Loaded {len(df_binance):,} Binance trades")
print(f"Time range: {pd.to_datetime(df_binance['ts'].min(), unit='s')} to {pd.to_datetime(df_binance['ts'].max(), unit='s')}")
print(f"Price range: ${df_binance['price'].min():.2f} to ${df_binance['price'].max():.2f}\n")

# Aggregate to 1-second intervals
print("Aggregating to 1-second intervals...")
df_binance['timestamp'] = pd.to_datetime(df_binance['ts'], unit='s')
df_binance['total_value'] = df_binance['price'] * df_binance['qty']

binance_1s = df_binance.set_index('timestamp').resample('1s').agg({
    'price': 'last',
    'qty': 'sum',
    'total_value': 'sum'
}).dropna()

binance_1s['vwap'] = binance_1s['total_value'] / binance_1s['qty']
binance_1s['price'] = binance_1s['price'].fillna(binance_1s['vwap'])

print(f"Aggregated to {len(binance_1s):,} 1-second intervals\n")

# Load Kalshi market snapshots
print("Loading Kalshi snapshots...")
query = '''
SELECT
    ts,
    ticker,
    yes_bid,
    yes_ask,
    yes_mid,
    floor_strike,
    seconds_to_close,
    volume,
    open_interest
FROM kalshi_snapshots
ORDER BY ts
'''

df_kalshi = pd.read_sql_query(query, sqlite3.connect(db_path))
df_kalshi['timestamp'] = pd.to_datetime(df_kalshi['ts'], unit='s')
df_kalshi['yes_mid_pct'] = df_kalshi['yes_mid'] / 100

print(f"Loaded {len(df_kalshi):,} Kalshi snapshots")
print(f"Unique tickers: {df_kalshi['ticker'].nunique()}\n")

# Load strategy trades
print("Loading strategy trades...")
try:
    query = '''
    SELECT
        timestamp,
        ticker,
        side,
        price,
        size,
        pnl,
        settled_at
    FROM strategy_trades
    WHERE strategy_name = 'crypto-scalp'
    ORDER BY timestamp
    '''

    df_trades = pd.read_sql_query(query, sqlite3.connect(trades_db_path))
    df_trades['timestamp'] = pd.to_datetime(df_trades['timestamp'], unit='s')
    df_trades['price_cents'] = df_trades['price'] * 100

    print(f"Loaded {len(df_trades)} crypto-scalp trades\n")
except Exception as e:
    print(f"No trade data available: {e}\n")
    df_trades = pd.DataFrame()

# Plot 1: Main chart
print("Creating main visualization...")
fig, axes = plt.subplots(3, 1, figsize=(16, 12), sharex=True)

# Subplot 1: Binance spot price
ax1 = axes[0]
ax1.plot(binance_1s.index, binance_1s['price'], linewidth=0.5, alpha=0.7, label='Last Price')
ax1.plot(binance_1s.index, binance_1s['vwap'], linewidth=1, alpha=0.9, label='VWAP', color='orange')

ma = binance_1s['vwap'].rolling(window=ma_window).mean()
ax1.plot(binance_1s.index, ma, linewidth=1.5, alpha=0.8, label=f'{ma_window}s MA', color='red')

ax1.set_ylabel('BTC Price (USD)', fontsize=12, fontweight='bold')
ax1.set_title('Binance BTC Spot Price', fontsize=14, fontweight='bold')
ax1.legend()
ax1.grid(True, alpha=0.3)

# Subplot 2: Kalshi market prices
ax2 = axes[1]

top_markets = df_kalshi.groupby('ticker')['volume'].max().nlargest(5).index
for ticker in top_markets:
    market_data = df_kalshi[df_kalshi['ticker'] == ticker]
    ax2.plot(market_data['timestamp'], market_data['yes_mid_pct'],
             linewidth=1, alpha=0.7, label=ticker[-5:])

ax2.set_ylabel('YES Probability', fontsize=12, fontweight='bold')
ax2.set_title('Kalshi Market Prices (Top 5 by Volume)', fontsize=14, fontweight='bold')
ax2.legend(bbox_to_anchor=(1.05, 1), loc='upper left')
ax2.grid(True, alpha=0.3)
ax2.set_ylim(0, 1)

# Subplot 3: Price changes
ax3 = axes[2]
price_changes = binance_1s['vwap'].diff()
ax3.plot(binance_1s.index, price_changes, linewidth=0.5, alpha=0.7, color='green')
ax3.axhline(y=10, color='red', linestyle='--', alpha=0.5, label='+$10 threshold')
ax3.axhline(y=-10, color='red', linestyle='--', alpha=0.5, label='-$10 threshold')
ax3.axhline(y=0, color='black', linestyle='-', alpha=0.3)

ax3.set_ylabel('Price Change (USD/s)', fontsize=12, fontweight='bold')
ax3.set_xlabel('Time', fontsize=12, fontweight='bold')
ax3.set_title('Binance BTC 1-Second Price Changes', fontsize=14, fontweight='bold')
ax3.legend()
ax3.grid(True, alpha=0.3)

plt.tight_layout()
output_file = output_dir / 'btc_price_movement.png'
plt.savefig(output_file, dpi=150, bbox_inches='tight')
print(f"Saved: {output_file}")
plt.close()

# Plot 2: Trade overlay (if trades exist)
if len(df_trades) > 0:
    print("Creating trade overlay...")
    fig, ax = plt.subplots(figsize=(16, 8))

    ax.plot(binance_1s.index, binance_1s['vwap'], linewidth=1, alpha=0.7,
            label='Binance VWAP', color='blue')

    entries = df_trades[df_trades['side'] == 'buy']
    for i, trade in entries.iterrows():
        idx = binance_1s.index.get_indexer([trade['timestamp']], method='nearest')[0]
        btc_price = binance_1s.iloc[idx]['vwap']

        ax.scatter(trade['timestamp'], btc_price,
                  marker='^', s=200, color='green',
                  edgecolors='black', linewidths=2, zorder=5,
                  label='Entry' if i == entries.index[0] else '')

        ax.annotate(f"{trade['ticker'][-5:]}\n{trade['price_cents']:.0f}¢",
                   xy=(trade['timestamp'], btc_price),
                   xytext=(0, 20), textcoords='offset points',
                   ha='center', fontsize=8,
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='green', alpha=0.3))

    ax.set_xlabel('Time', fontsize=12, fontweight='bold')
    ax.set_ylabel('BTC Price (USD)', fontsize=12, fontweight='bold')
    ax.set_title('Crypto Scalp Strategy: Entry/Exit Points', fontsize=14, fontweight='bold')
    ax.legend()
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    output_file = output_dir / 'strategy_trades_overlay.png'
    plt.savefig(output_file, dpi=150, bbox_inches='tight')
    print(f"Saved: {output_file}")
    plt.close()
else:
    print("No trades to plot.")

# Summary
print("\n" + "="*60)
print("SUMMARY STATISTICS")
print("="*60)

print(f"\nBinance BTC:")
print(f"  Duration: {(binance_1s.index.max() - binance_1s.index.min()).total_seconds() / 3600:.1f} hours")
print(f"  Price range: ${binance_1s['vwap'].min():.2f} to ${binance_1s['vwap'].max():.2f}")
print(f"  Mean: ${binance_1s['vwap'].mean():.2f} ± ${binance_1s['vwap'].std():.2f}")
print(f"  Total volume: {df_binance['qty'].sum():.2f} BTC")

print(f"\nKalshi Markets:")
print(f"  Snapshots: {len(df_kalshi):,}")
print(f"  Unique markets: {df_kalshi['ticker'].nunique()}")
print(f"  Avg volume: {df_kalshi.groupby('ticker')['volume'].max().mean():.0f}")

if len(df_trades) > 0:
    print(f"\nStrategy Trades:")
    print(f"  Total: {len(df_trades)}")
    print(f"  Win rate: {(df_trades['pnl'] > 0).mean() * 100:.1f}%")
    print(f"  P&L: ${df_trades['pnl'].sum():.2f}")
else:
    print(f"\nStrategy Trades: None")

print("\n" + "="*60)
print(f"\n✅ Visualizations saved to: {output_dir}")
print("="*60)
