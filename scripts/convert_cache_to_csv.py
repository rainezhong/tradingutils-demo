#!/usr/bin/env python3
"""
Convert cached candle pickle files to CSV format for analysis.

Merges with existing nba_historical_candlesticks.csv to create
comprehensive dataset for stop loss testing.
"""

import pickle
from pathlib import Path
import pandas as pd
from datetime import datetime
from typing import List, Dict


def load_candles(pickle_path: Path) -> List[Dict]:
    """Load candle data from pickle file."""
    with open(pickle_path, 'rb') as f:
        return pickle.load(f)


def convert_candles_to_df(ticker: str, candles: List[Dict], won_override=None) -> pd.DataFrame:
    """Convert candle data to DataFrame format.

    Args:
        won_override: If provided, use this as the settlement result instead of
                      inferring from the last candle price.
    """
    rows = []

    for candle in candles:
        # Extract event ticker and team from market ticker
        # Format: KXNBAGAME-26JAN26PORBOS-POR
        parts = ticker.split('-')
        if len(parts) >= 3:
            event_ticker = '-'.join(parts[:3])
            team = parts[-1] if len(parts) == 4 else None
        else:
            continue

        # Mid price from bid/ask
        yes_bid = candle.get('yes_bid_close', 0)
        yes_ask = candle.get('yes_ask_close', 100)
        yes_price = (yes_bid + yes_ask) / 200.0  # Convert to decimal (0-1)

        rows.append({
            'event_ticker': event_ticker,
            'ticker': ticker,
            'team': team,
            'timestamp': candle.get('ts', 0),
            'yes_price': yes_price,
            'volume': candle.get('volume', 0),
            'won': won_override,
        })

    df = pd.DataFrame(rows)

    # If no override, fall back to inferring from last candle price
    if won_override is None and len(df) > 0:
        last_candle = candles[-1]
        if 'yes_bid_close' in last_candle:
            bid = last_candle['yes_bid_close']
            ask = last_candle.get('yes_ask_close', 100)
            if bid >= 99 or ask >= 99:
                df['won'] = True
            elif bid <= 1 or ask <= 1:
                df['won'] = False

    return df


def main():
    cache_dir = Path("data/nba_cache")
    output_file = Path("data/nba_historical_candlesticks.csv")

    print("Converting cached candle data to CSV...")

    # Load settled market metadata for authoritative settlement results
    market_results = {}
    settled_path = cache_dir / "settled_markets_raw.pkl"
    if settled_path.exists():
        with open(settled_path, 'rb') as f:
            settled_markets = pickle.load(f)
        for m in settled_markets:
            ticker = m.get('ticker', '')
            result = m.get('result', '')
            if ticker and result in ('yes', 'no'):
                market_results[ticker] = result == 'yes'
        print(f"Loaded settlement results for {len(market_results)} markets")

    # Load all cached candles
    all_files = sorted(cache_dir.glob("candles_*.pkl"))
    print(f"Found {len(all_files)} cached market files")

    all_dfs = []
    processed = 0
    skipped = 0

    for pkl_file in all_files:
        try:
            ticker = pkl_file.stem.replace('candles_', '')
            candles = load_candles(pkl_file)

            if not candles:
                skipped += 1
                continue

            # Use authoritative result if available, otherwise fall back to candle inference
            if ticker in market_results:
                df = convert_candles_to_df(ticker, candles, won_override=market_results[ticker])
            else:
                df = convert_candles_to_df(ticker, candles)

            if len(df) > 0:
                all_dfs.append(df)
                processed += 1
        except Exception as e:
            print(f"Error processing {pkl_file.name}: {e}")
            skipped += 1

    print(f"Processed: {processed}, Skipped: {skipped}")

    # Combine all dataframes
    if all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)

        # Add derived columns
        combined['close_ts'] = combined.groupby('ticker')['timestamp'].transform('max')
        combined['minutes_until_close'] = (combined['close_ts'] - combined['timestamp']) / 60.0

        # Sort by ticker and timestamp
        combined = combined.sort_values(['ticker', 'timestamp'])

        print(f"\nCombined dataset:")
        print(f"  Total rows: {len(combined):,}")
        print(f"  Unique markets: {combined['ticker'].nunique()}")
        print(f"  Unique games: {combined['event_ticker'].nunique()}")
        print(f"  Date range: {pd.to_datetime(combined['timestamp'], unit='s').min()} to {pd.to_datetime(combined['timestamp'], unit='s').max()}")

        # Save to CSV
        combined.to_csv(output_file, index=False)
        print(f"\n✅ Saved to {output_file}")

        return output_file
    else:
        print("❌ No data to process")
        return None


if __name__ == "__main__":
    output = main()
    if output:
        print(f"\n🎯 Ready to test stop loss on extended dataset: {output}")
