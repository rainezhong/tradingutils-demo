#!/usr/bin/env python3
"""Backtest Mean Reversion and Fade Momentum strategies on historical NBA data."""

import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))


class BacktestEngine:
    """Simple backtest engine for pattern-based strategies."""

    def __init__(self, db_path: str = "data/markets.db"):
        """Initialize backtest engine."""
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)

    def load_nba_data(self) -> pd.DataFrame:
        """Load NBA market snapshots."""
        query = """
        SELECT
            s.ticker,
            s.timestamp,
            s.yes_bid,
            s.yes_ask,
            s.mid_price,
            s.spread_cents,
            s.volume_24h,
            m.close_time,
            m.status
        FROM snapshots s
        JOIN markets m ON s.ticker = m.ticker
        WHERE m.ticker LIKE 'KXNBAGAME%'
            AND s.mid_price IS NOT NULL
            AND s.mid_price > 0
        ORDER BY s.ticker, s.timestamp
        """

        df = pd.read_sql_query(query, self.conn)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['close_time'] = pd.to_datetime(df['close_time'])

        print(f"Loaded {len(df)} snapshots for {df['ticker'].nunique()} markets")
        return df

    def backtest_mean_reversion(
        self,
        df: pd.DataFrame,
        min_move: float = 5.0,
        max_spread: float = 4.0,
        volume_filter: bool = True,
    ) -> Tuple[List[Dict], pd.DataFrame]:
        """Backtest mean reversion strategy.

        Args:
            df: Market data
            min_move: Minimum price move to trigger signal (cents)
            max_spread: Maximum acceptable spread (cents)
            volume_filter: If True, only trade high-volume markets

        Returns:
            (trades, results_df)
        """
        print("\n" + "=" * 60)
        print("BACKTESTING MEAN REVERSION STRATEGY")
        print("=" * 60)
        print(f"Min move: {min_move}¢")
        print(f"Max spread: {max_spread}¢")
        print(f"Volume filter: {volume_filter}")
        print()

        # Calculate volume median
        volume_median = df['volume_24h'].median()

        # Calculate price changes
        df = df.sort_values(['ticker', 'timestamp'])
        df['price_change'] = df.groupby('ticker')['mid_price'].diff()
        df['next_price'] = df.groupby('ticker')['mid_price'].shift(-1)
        df['next_price_change'] = df['next_price'] - df['mid_price']

        trades = []

        for idx, row in df.iterrows():
            # Check filters
            if pd.isna(row['price_change']) or pd.isna(row['next_price_change']):
                continue

            # Signal: sharp price move
            if abs(row['price_change']) < min_move:
                continue

            # Spread filter
            if row['spread_cents'] > max_spread:
                continue

            # Volume filter
            if volume_filter and row['volume_24h'] < volume_median:
                continue

            # Price range filter (10-90¢)
            if row['mid_price'] < 10 or row['mid_price'] > 90:
                continue

            # SIGNAL: Bet against the move
            if row['price_change'] > 0:
                # Price went up → bet NO (bet it comes down)
                side = "NO"
                entry_price = 100 - row['yes_bid']  # NO ask approximation
            else:
                # Price went down → bet YES (bet it comes up)
                side = "YES"
                entry_price = row['yes_ask']

            # Simulate outcome
            # Did price reverse? (move back toward previous price)
            price_reversed = (
                (row['price_change'] > 0 and row['next_price_change'] < 0) or
                (row['price_change'] < 0 and row['next_price_change'] > 0)
            )

            # Calculate P&L (simplified: assume we exit at next snapshot)
            if side == "YES":
                exit_price = row['next_price']
                pnl = exit_price - entry_price
            else:
                exit_price = 100 - row['next_price']
                pnl = exit_price - entry_price

            trades.append({
                'ticker': row['ticker'],
                'timestamp': row['timestamp'],
                'signal_type': 'mean_reversion',
                'side': side,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'price_change': row['price_change'],
                'next_price_change': row['next_price_change'],
                'spread': row['spread_cents'],
                'volume': row['volume_24h'],
                'reversed': price_reversed,
                'pnl': pnl,
                'win': pnl > 0,
            })

        results_df = pd.DataFrame(trades)

        if len(results_df) > 0:
            print(f"Total trades: {len(results_df)}")
            print(f"Wins: {results_df['win'].sum()} ({results_df['win'].mean()*100:.1f}%)")
            print(f"Price reversed: {results_df['reversed'].sum()} ({results_df['reversed'].mean()*100:.1f}%)")
            print(f"Avg P&L: {results_df['pnl'].mean():.2f}¢")
            print(f"Total P&L: {results_df['pnl'].sum():.2f}¢")
            print(f"Sharpe: {results_df['pnl'].mean() / results_df['pnl'].std():.2f}" if results_df['pnl'].std() > 0 else "N/A")
        else:
            print("No trades generated!")

        return trades, results_df

    def backtest_fade_momentum(
        self,
        df: pd.DataFrame,
        min_consecutive: int = 2,
        min_move_size: float = 0.5,
        max_spread: float = 4.0,
        volume_filter: bool = True,
    ) -> Tuple[List[Dict], pd.DataFrame]:
        """Backtest fade momentum strategy.

        Args:
            df: Market data
            min_consecutive: Minimum consecutive moves
            min_move_size: Minimum move size per step (cents)
            max_spread: Maximum acceptable spread
            volume_filter: If True, only trade high-volume markets

        Returns:
            (trades, results_df)
        """
        print("\n" + "=" * 60)
        print("BACKTESTING FADE MOMENTUM STRATEGY")
        print("=" * 60)
        print(f"Min consecutive moves: {min_consecutive}")
        print(f"Min move size: {min_move_size}¢")
        print(f"Max spread: {max_spread}¢")
        print(f"Volume filter: {volume_filter}")
        print()

        volume_median = df['volume_24h'].median()

        # Calculate price changes
        df = df.sort_values(['ticker', 'timestamp'])
        df['price_change'] = df.groupby('ticker')['mid_price'].diff()
        df['prev_change'] = df.groupby('ticker')['price_change'].shift(1)
        df['prev_change_2'] = df.groupby('ticker')['price_change'].shift(2)
        df['next_price'] = df.groupby('ticker')['mid_price'].shift(-1)
        df['next_price_change'] = df['next_price'] - df['mid_price']

        trades = []

        for idx, row in df.iterrows():
            # Need at least 2 previous changes
            if pd.isna(row['prev_change']) or pd.isna(row['next_price_change']):
                continue

            # Check for consecutive moves in same direction
            if min_consecutive == 2:
                # 2 consecutive moves
                same_direction = (
                    (row['price_change'] > min_move_size and row['prev_change'] > min_move_size) or
                    (row['price_change'] < -min_move_size and row['prev_change'] < -min_move_size)
                )
            else:
                # 3 consecutive moves
                if pd.isna(row['prev_change_2']):
                    continue
                same_direction = (
                    (row['price_change'] > min_move_size and
                     row['prev_change'] > min_move_size and
                     row['prev_change_2'] > min_move_size) or
                    (row['price_change'] < -min_move_size and
                     row['prev_change'] < -min_move_size and
                     row['prev_change_2'] < -min_move_size)
                )

            if not same_direction:
                continue

            # Filters
            if row['spread_cents'] > max_spread:
                continue

            if volume_filter and row['volume_24h'] < volume_median:
                continue

            if row['mid_price'] < 10 or row['mid_price'] > 90:
                continue

            # SIGNAL: Bet against momentum
            if row['price_change'] > 0:
                # Upward momentum → bet NO
                side = "NO"
                entry_price = 100 - row['yes_bid']
            else:
                # Downward momentum → bet YES
                side = "YES"
                entry_price = row['yes_ask']

            # Check if momentum reversed
            momentum_reversed = (
                (row['price_change'] > 0 and row['next_price_change'] < 0) or
                (row['price_change'] < 0 and row['next_price_change'] > 0)
            )

            # Calculate P&L
            if side == "YES":
                exit_price = row['next_price']
                pnl = exit_price - entry_price
            else:
                exit_price = 100 - row['next_price']
                pnl = exit_price - entry_price

            # Enhanced signal in 60-80¢ range
            is_premium = 60 <= row['mid_price'] <= 80

            trades.append({
                'ticker': row['ticker'],
                'timestamp': row['timestamp'],
                'signal_type': 'fade_momentum_premium' if is_premium else 'fade_momentum',
                'side': side,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'price_change': row['price_change'],
                'prev_change': row['prev_change'],
                'next_price_change': row['next_price_change'],
                'spread': row['spread_cents'],
                'volume': row['volume_24h'],
                'reversed': momentum_reversed,
                'pnl': pnl,
                'win': pnl > 0,
                'is_premium': is_premium,
            })

        results_df = pd.DataFrame(trades)

        if len(results_df) > 0:
            print(f"Total trades: {len(results_df)}")
            print(f"Wins: {results_df['win'].sum()} ({results_df['win'].mean()*100:.1f}%)")
            print(f"Momentum reversed: {results_df['reversed'].sum()} ({results_df['reversed'].mean()*100:.1f}%)")
            print(f"Avg P&L: {results_df['pnl'].mean():.2f}¢")
            print(f"Total P&L: {results_df['pnl'].sum():.2f}¢")
            print(f"Sharpe: {results_df['pnl'].mean() / results_df['pnl'].std():.2f}" if results_df['pnl'].std() > 0 else "N/A")

            # Premium range performance
            premium_trades = results_df[results_df['is_premium']]
            if len(premium_trades) > 0:
                print(f"\nPremium range (60-80¢) performance:")
                print(f"  Trades: {len(premium_trades)}")
                print(f"  Win rate: {premium_trades['win'].mean()*100:.1f}%")
                print(f"  Reversal rate: {premium_trades['reversed'].mean()*100:.1f}%")
                print(f"  Avg P&L: {premium_trades['pnl'].mean():.2f}¢")
        else:
            print("No trades generated!")

        return trades, results_df

    def compare_strategies(self, mr_df: pd.DataFrame, fm_df: pd.DataFrame):
        """Compare performance of both strategies."""
        print("\n" + "=" * 60)
        print("STRATEGY COMPARISON")
        print("=" * 60)

        if len(mr_df) == 0 and len(fm_df) == 0:
            print("No trades for either strategy!")
            return

        strategies = []

        if len(mr_df) > 0:
            strategies.append(("Mean Reversion", mr_df))

        if len(fm_df) > 0:
            strategies.append(("Fade Momentum", fm_df))

        print(f"\n{'Strategy':<20} {'Trades':<10} {'Win %':<10} {'Avg P&L':<12} {'Total P&L':<12} {'Sharpe':<10}")
        print("-" * 80)

        for name, df in strategies:
            sharpe = df['pnl'].mean() / df['pnl'].std() if df['pnl'].std() > 0 else 0
            print(
                f"{name:<20} {len(df):<10} {df['win'].mean()*100:<10.1f} "
                f"{df['pnl'].mean():<12.2f} {df['pnl'].sum():<12.2f} {sharpe:<10.2f}"
            )

        print()


def main():
    """Run backtest."""
    import logging
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    engine = BacktestEngine()

    # Load data
    df = engine.load_nba_data()

    # Backtest Mean Reversion
    mr_trades, mr_df = engine.backtest_mean_reversion(
        df,
        min_move=5.0,
        max_spread=4.0,
        volume_filter=True,
    )

    # Backtest Fade Momentum
    fm_trades, fm_df = engine.backtest_fade_momentum(
        df,
        min_consecutive=2,
        min_move_size=0.5,
        max_spread=4.0,
        volume_filter=True,
    )

    # Compare
    engine.compare_strategies(mr_df, fm_df)

    # Save results
    if len(mr_df) > 0:
        mr_df.to_csv('data/mean_reversion_backtest.csv', index=False)
        print(f"\n✅ Saved Mean Reversion results to data/mean_reversion_backtest.csv")

    if len(fm_df) > 0:
        fm_df.to_csv('data/fade_momentum_backtest.csv', index=False)
        print(f"✅ Saved Fade Momentum results to data/fade_momentum_backtest.csv")


if __name__ == "__main__":
    main()
