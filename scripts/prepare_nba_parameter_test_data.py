#!/usr/bin/env python3
"""Prepare NBA data for parameter sensitivity testing.

Extracts opening price, high, low, and settlement for each game.
"""

import pandas as pd
import sys

def main():
    print("Loading NBA time-series data...")
    df = pd.read_csv('data/nba_ev_raw.csv')

    print(f"Loaded {len(df)} rows")
    print(f"Unique games: {df['event_ticker'].nunique()}")

    # Group by game
    games = []

    for ticker, group in df.groupby('event_ticker'):
        # Sort by minutes until settlement (descending = furthest first)
        group = group.sort_values('minutes_until_settlement', ascending=False)

        # Opening price (furthest from settlement)
        opening_row = group.iloc[0]
        opening_price = opening_row['underdog_price']

        # High/low during the game (convert to cents)
        high_price = group['underdog_price'].max()
        low_price = group['underdog_price'].min()

        # Settlement
        settlement_row = group.iloc[-1]
        winner = settlement_row['winner']
        favorite = settlement_row['favorite']

        # Determine if underdog won
        if favorite == 'home':
            underdog_won = (winner == 'away')
            underdog_team = opening_row['away_team']
        else:
            underdog_won = (winner == 'home')
            underdog_team = opening_row['home_team']

        games.append({
            'ticker': ticker,
            'home_team': opening_row['home_team'],
            'away_team': opening_row['away_team'],
            'underdog_team': underdog_team,
            'open_price': round(opening_price * 100),  # Convert to cents
            'high_price': round(high_price * 100),
            'low_price': round(low_price * 100),
            'underdog_won': underdog_won,
            'volume': settlement_row['volume'],
        })

    result = pd.DataFrame(games)

    # Save to CSV
    output_file = 'data/nba_underdog_parameter_test.csv'
    result.to_csv(output_file, index=False)

    print(f"\n✅ Created {output_file}")
    print(f"   Games: {len(result)}")
    print(f"   Price range: {result['open_price'].min()}¢ to {result['open_price'].max()}¢")
    print(f"   Win rate: {result['underdog_won'].mean():.1%}")
    print(f"\nFirst few rows:")
    print(result.head())

    # Show price distribution
    print("\nPrice distribution:")
    bins = [0, 10, 15, 20, 25, 30, 35, 100]
    labels = ['<10¢', '10-15¢', '15-20¢', '20-25¢', '25-30¢', '30-35¢', '>35¢']
    result['bucket'] = pd.cut(result['open_price'], bins=bins, labels=labels)
    print(result['bucket'].value_counts().sort_index())

if __name__ == '__main__':
    main()
