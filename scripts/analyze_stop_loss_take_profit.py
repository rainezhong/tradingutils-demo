#!/usr/bin/env python3
"""
Analyze NBA underdog bets with stop loss and take profit strategies.

Tests whether dynamic exit rules improve returns compared to buy-and-hold.

Usage:
    python3 scripts/analyze_stop_loss_take_profit.py --data data/nba_historical_candlesticks.csv
"""

import pandas as pd
import numpy as np
import argparse
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from collections import defaultdict


@dataclass
class TradeResult:
    """Result of a single trade."""
    ticker: str
    team: str
    entry_price: float
    exit_price: float
    profit: float
    exit_reason: str  # 'hold', 'stop_loss', 'take_profit', 'settlement'
    won: bool
    max_price: float  # Peak price reached
    min_price: float  # Lowest price reached


@dataclass
class StrategyStats:
    """Statistics for a trading strategy."""
    strategy_name: str
    trades: int
    wins: int
    win_rate: float
    total_profit: float
    avg_profit: float
    roi: float
    max_drawdown: float
    sharpe_ratio: float


def identify_underdogs(df: pd.DataFrame, min_price: float = 0.10, max_price: float = 0.40) -> pd.DataFrame:
    """Identify underdog markets (opening price in specified range)."""
    # Group by market and get first price (opening)
    opening_prices = df.groupby('ticker').first().reset_index()

    # Filter for underdogs
    underdogs = opening_prices[
        (opening_prices['yes_price'] >= min_price) &
        (opening_prices['yes_price'] <= max_price)
    ][['ticker', 'yes_price', 'won', 'event_ticker', 'team']]

    return underdogs


def simulate_strategy(
    df: pd.DataFrame,
    ticker: str,
    entry_price: float,
    stop_loss_cents: Optional[float] = None,
    take_profit_cents: Optional[float] = None
) -> TradeResult:
    """Simulate a single trade with optional stop loss and take profit."""
    # Get price history for this market
    market_data = df[df['ticker'] == ticker].sort_values('timestamp')

    if len(market_data) == 0:
        return None

    # Entry price (from first snapshot)
    entry = entry_price
    won = market_data.iloc[0]['won']
    team = market_data.iloc[0]['team']

    # Track price movements
    max_price = entry
    min_price = entry
    exit_price = None
    exit_reason = 'settlement'

    for idx, row in market_data.iterrows():
        price = row['yes_price']

        # Track extremes
        max_price = max(max_price, price)
        min_price = min(min_price, price)

        # Check stop loss (price dropped X cents from entry)
        if stop_loss_cents is not None:
            if price <= (entry - stop_loss_cents):
                exit_price = price
                exit_reason = 'stop_loss'
                break

        # Check take profit (price rose X cents from entry)
        if take_profit_cents is not None:
            if price >= (entry + take_profit_cents):
                exit_price = price
                exit_reason = 'take_profit'
                break

    # If no exit triggered, hold to settlement
    if exit_price is None:
        exit_price = 1.0 if won else 0.0
        exit_reason = 'settlement'

    # Calculate profit
    profit = exit_price - entry

    return TradeResult(
        ticker=ticker,
        team=team,
        entry_price=entry,
        exit_price=exit_price,
        profit=profit,
        exit_reason=exit_reason,
        won=won,
        max_price=max_price,
        min_price=min_price
    )


def calculate_stats(results: List[TradeResult], strategy_name: str) -> StrategyStats:
    """Calculate statistics for a list of trade results."""
    if not results:
        return None

    trades = len(results)
    wins = sum(1 for r in results if r.profit > 0)
    win_rate = wins / trades

    total_profit = sum(r.profit for r in results)
    avg_profit = total_profit / trades

    # ROI = total profit / total invested (entry prices)
    total_invested = sum(r.entry_price for r in results)
    roi = (total_profit / total_invested) * 100

    # Max drawdown (largest cumulative loss)
    cumulative = 0
    peak = 0
    max_dd = 0
    for r in results:
        cumulative += r.profit
        peak = max(peak, cumulative)
        drawdown = peak - cumulative
        max_dd = max(max_dd, drawdown)

    # Sharpe ratio (mean profit / std dev of profits)
    profits = [r.profit for r in results]
    sharpe = (np.mean(profits) / np.std(profits)) if np.std(profits) > 0 else 0

    return StrategyStats(
        strategy_name=strategy_name,
        trades=trades,
        wins=wins,
        win_rate=win_rate,
        total_profit=total_profit,
        avg_profit=avg_profit,
        roi=roi,
        max_drawdown=max_dd,
        sharpe_ratio=sharpe
    )


def print_comparison(strategies: List[StrategyStats]):
    """Print comparison table of strategies."""
    print("=" * 120)
    print("STOP LOSS / TAKE PROFIT STRATEGY COMPARISON")
    print("=" * 120)
    print()

    print(f"{'Strategy':<25} {'Trades':>8} {'Wins':>8} {'Win%':>8} {'Total $':>10} {'Avg $':>10} {'ROI':>10} {'Sharpe':>8}")
    print("-" * 120)

    for s in strategies:
        print(f"{s.strategy_name:<25} {s.trades:>8} {s.wins:>8} {s.win_rate:>7.1%} "
              f"${s.total_profit:>9.2f} ${s.avg_profit:>9.4f} {s.roi:>9.1f}% {s.sharpe_ratio:>7.2f}")

    print()

    # Find best strategies
    best_roi = max(strategies, key=lambda x: x.roi)
    best_sharpe = max(strategies, key=lambda x: x.sharpe_ratio)
    best_profit = max(strategies, key=lambda x: x.total_profit)

    print(f"🏆 Best ROI: {best_roi.strategy_name} ({best_roi.roi:.1f}%)")
    print(f"📈 Best Sharpe: {best_sharpe.strategy_name} ({best_sharpe.sharpe_ratio:.2f})")
    print(f"💰 Best Total Profit: {best_profit.strategy_name} (${best_profit.total_profit:.2f})")


def print_exit_breakdown(results: List[TradeResult], strategy_name: str):
    """Print breakdown of exit reasons."""
    exit_counts = defaultdict(int)
    exit_profits = defaultdict(list)

    for r in results:
        exit_counts[r.exit_reason] += 1
        exit_profits[r.exit_reason].append(r.profit)

    print(f"\n{strategy_name} - Exit Reason Breakdown:")
    print(f"{'Reason':<15} {'Count':>8} {'Avg Profit':>12} {'Total Profit':>12}")
    print("-" * 50)

    for reason in ['settlement', 'take_profit', 'stop_loss']:
        if reason in exit_counts:
            count = exit_counts[reason]
            profits = exit_profits[reason]
            avg = np.mean(profits)
            total = sum(profits)
            print(f"{reason:<15} {count:>8} ${avg:>11.4f} ${total:>11.2f}")


def main():
    parser = argparse.ArgumentParser(description="Analyze stop loss and take profit strategies")
    parser.add_argument("--data", default="data/nba_historical_candlesticks.csv", help="Path to candlestick data")
    parser.add_argument("--min-price", type=float, default=0.10, help="Min underdog price (default: 0.10)")
    parser.add_argument("--max-price", type=float, default=0.40, help="Max underdog price (default: 0.40)")

    args = parser.parse_args()

    # Load data
    print(f"Loading data from {args.data}...")
    df = pd.read_csv(args.data)

    # Identify underdogs
    underdogs = identify_underdogs(df, args.min_price, args.max_price)
    print(f"Found {len(underdogs)} underdog markets (${args.min_price:.2f} - ${args.max_price:.2f})")
    print()

    # Test different strategies
    strategies_to_test = [
        ("Buy & Hold", None, None),
        ("Stop Loss 5¢", 0.05, None),
        ("Stop Loss 10¢", 0.10, None),
        ("Stop Loss 15¢", 0.15, None),
        ("Take Profit 10¢", None, 0.10),
        ("Take Profit 20¢", None, 0.20),
        ("Take Profit 30¢", None, 0.30),
        ("SL 5¢ / TP 20¢", 0.05, 0.20),
        ("SL 10¢ / TP 20¢", 0.10, 0.20),
        ("SL 5¢ / TP 30¢", 0.05, 0.30),
    ]

    all_stats = []
    all_results = {}

    for strategy_name, stop_loss, take_profit in strategies_to_test:
        results = []

        for _, underdog in underdogs.iterrows():
            result = simulate_strategy(
                df,
                underdog['ticker'],
                underdog['yes_price'],
                stop_loss,
                take_profit
            )
            if result:
                results.append(result)

        stats = calculate_stats(results, strategy_name)
        if stats:
            all_stats.append(stats)
            all_results[strategy_name] = results

    # Print comparison
    print_comparison(all_stats)

    # Print detailed breakdown for top 3 strategies
    print("\n" + "=" * 120)
    print("DETAILED BREAKDOWN - TOP STRATEGIES")
    print("=" * 120)

    top_3 = sorted(all_stats, key=lambda x: x.roi, reverse=True)[:3]
    for stats in top_3:
        print_exit_breakdown(all_results[stats.strategy_name], stats.strategy_name)


if __name__ == "__main__":
    main()
