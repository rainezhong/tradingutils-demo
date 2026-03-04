"""
Standalone Kalshi price tracking visualization
Run this as a single script or copy into a notebook cell
"""

import sqlite3
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
import bisect

# ==============================================================================
# CONFIGURATION
# ==============================================================================

# Output directory
OUTPUT_DIR = Path('notebooks/results/plots')
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

# Find data directory
current = Path.cwd()
data_dir = current / 'data' if (current / 'data').exists() else current.parent / 'data'

# Find database with kalshi_snapshots
db_path = None
for candidate in ['btc_probe_20260227.db', 'btc_ob_48h.db', 'btc_probe_merged.db']:
    test_path = data_dir / candidate
    if test_path.exists():
        conn_test = sqlite3.connect(str(test_path))
        cursor = conn_test.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='kalshi_snapshots'")
        if cursor.fetchone():
            db_path = test_path
            conn_test.close()
            break
        conn_test.close()

if not db_path:
    raise FileNotFoundError(f"No database found in {data_dir}")

print(f"Using database: {db_path.name}")

# ==============================================================================
# LOAD DATA
# ==============================================================================

conn = sqlite3.connect(str(db_path))

# Load Kalshi data
kalshi_df = pd.read_sql_query("""
    SELECT ts, ticker, yes_bid, yes_ask, yes_mid,
           floor_strike as strike, seconds_to_close, volume, open_interest
    FROM kalshi_snapshots ORDER BY ts
""", conn)
kalshi_df['datetime'] = pd.to_datetime(kalshi_df['ts'], unit='s')

# Load Kraken data (truth signal)
kraken_df = pd.read_sql_query("""
    SELECT ts, spot_price FROM kraken_snapshots ORDER BY ts
""", conn)

conn.close()

print(f"Loaded {len(kalshi_df):,} Kalshi snapshots")
print(f"Loaded {len(kraken_df):,} Kraken snapshots")

# ==============================================================================
# SELECT MARKET
# ==============================================================================

# Pick most active market
market_counts = kalshi_df.groupby('ticker').size().sort_values(ascending=False)
ticker = market_counts.index[0]
market_df = kalshi_df[kalshi_df['ticker'] == ticker].copy()

# Merge with Kraken prices
kraken_ts = kraken_df['ts'].values
kraken_prices = kraken_df['spot_price'].values

def find_nearest_price(ts):
    idx = bisect.bisect_right(kraken_ts, ts) - 1
    return kraken_prices[idx] if idx >= 0 else np.nan

market_df['kraken_spot'] = market_df['ts'].apply(find_nearest_price)

print(f"\nAnalyzing: {ticker}")
print(f"Strike: ${market_df['strike'].iloc[0]:,.2f}")
print(f"Snapshots: {len(market_df):,}")

# ==============================================================================
# CALCULATE FAIR VALUE
# ==============================================================================

def calc_fair_value(spot, strike, ttx):
    if pd.isna(spot) or pd.isna(strike) or ttx <= 0:
        return np.nan
    if spot >= strike:
        return 95.0 if ttx < 60 else 50 + min(45, (spot - strike) / 10 * 10)
    else:
        return 5.0 if ttx < 60 else 50 - min(45, (strike - spot) / 10 * 10)

market_df['fair_value'] = market_df.apply(
    lambda r: calc_fair_value(r['kraken_spot'], r['strike'], r['seconds_to_close']),
    axis=1
)
market_df['edge'] = market_df['fair_value'] - market_df['yes_mid']
market_df['spread'] = market_df['yes_ask'] - market_df['yes_bid']

corr = market_df[['yes_mid', 'fair_value']].corr().iloc[0, 1]

print(f"\nCorrelation: {corr:.3f}")
print(f"Average edge: {market_df['edge'].mean():.2f}¢")
print(f"Average spread: {market_df['spread'].mean():.2f}¢")

# ==============================================================================
# PLOT 1: TIME SERIES
# ==============================================================================

fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

# Prices
ax1 = axes[0]
ax1.plot(market_df['datetime'], market_df['yes_mid'], label='Kalshi', color='blue', lw=1.5)
ax1.plot(market_df['datetime'], market_df['fair_value'], label='Fair Value', color='green', lw=1.5)
ax1.fill_between(market_df['datetime'], market_df['yes_bid'], market_df['yes_ask'],
                  alpha=0.2, color='blue', label='Spread')
ax1.set_ylabel('Probability (¢)')
ax1.set_title(f'{ticker} - Price Tracking (corr={corr:.3f})', fontweight='bold')
ax1.legend()
ax1.grid(True, alpha=0.3)
ax1.set_ylim(0, 100)

# BTC price
ax2 = axes[1]
ax2.plot(market_df['datetime'], market_df['kraken_spot'], color='orange', lw=2)
ax2.axhline(market_df['strike'].iloc[0], color='red', ls='--', lw=2,
            label=f'Strike: ${market_df["strike"].iloc[0]:,.0f}')
ax2.set_ylabel('BTC Price ($)')
ax2.legend()
ax2.grid(True, alpha=0.3)

# Edge
ax3 = axes[2]
ax3.plot(market_df['datetime'], market_df['edge'], color='purple', lw=1.5)
ax3.axhline(0, color='black', lw=0.5)
ax3.fill_between(market_df['datetime'], 0, market_df['edge'],
                  where=market_df['edge']>0, alpha=0.3, color='green', label='Under')
ax3.fill_between(market_df['datetime'], 0, market_df['edge'],
                  where=market_df['edge']<0, alpha=0.3, color='red', label='Over')
ax3.set_ylabel('Edge (¢)')
ax3.set_xlabel('Time')
ax3.legend()
ax3.grid(True, alpha=0.3)

for ax in axes:
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.tick_params(axis='x', rotation=45)

plt.tight_layout()
plt.savefig(OUTPUT_DIR / 'tracking.png', dpi=100, bbox_inches='tight')
print(f"\nSaved: {OUTPUT_DIR / 'tracking.png'}")
plt.show()

# ==============================================================================
# ARBITRAGE STATS
# ==============================================================================

arb_long = ((market_df['edge'] > 5) & (market_df['spread'] <= 3)).sum()
arb_short = ((market_df['edge'] < -5) & (market_df['spread'] <= 3)).sum()
total = len(market_df)

print(f"\n{'='*60}")
print(f"ARBITRAGE: {arb_long+arb_short:,}/{total:,} ({(arb_long+arb_short)/total*100:.1f}%)")
print(f"  Long: {arb_long:,} ({arb_long/total*100:.1f}%)")
print(f"  Short: {arb_short:,} ({arb_short/total*100:.1f}%)")
print(f"{'='*60}")
