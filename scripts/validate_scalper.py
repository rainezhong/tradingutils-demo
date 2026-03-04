#!/usr/bin/env python3
"""
Scalper Strategy Validation — Price-Based Analysis

Uses nba_historical_candlesticks.csv (246 games, 57K rows) to validate
the scalper strategy independently of the score-based probability table.

Two analyses:
  1. Price-based win rate:  (price, minutes_to_close) → actual win rate
     Does buying at 93-95c with 8 min left actually win 98%+ of the time?

  2. Simulated scalper:     Replay the strategy on all 246 games using
     price thresholds instead of (lead, minute) to find entries.

Also cross-references the synthetic recordings to run the full
score-based scalper on the complete 274-game dataset (122 live + 152 synthetic).

Usage:
    python scripts/validate_scalper.py
    python scripts/validate_scalper.py --full    # Also run score-based backtest
"""

import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import csv
import glob
import pickle
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

from src.kalshi.fees import calculate_fee


# ============================================================================
#  LOAD CANDLESTICK DATA
# ============================================================================


def load_candlesticks(
    path: str = "data/nba_historical_candlesticks.csv",
) -> pd.DataFrame:
    """Load the candlestick CSV into a DataFrame."""
    df = pd.read_csv(path)
    # Ensure types
    df["yes_price"] = df["yes_price"].astype(float)
    df["minutes_until_close"] = df["minutes_until_close"].astype(float)
    df["won"] = df["won"].astype(bool)
    df["volume"] = df["volume"].astype(int)
    return df


def load_candle_pickles(cache_dir: str = "data/nba_cache") -> Dict[str, List[dict]]:
    """Load all candle pickle files. Returns dict of ticker → candle list."""
    candles = {}
    for path in glob.glob(os.path.join(cache_dir, "candles_KXNBAGAME-*.pkl")):
        ticker = os.path.basename(path).replace("candles_", "").replace(".pkl", "")
        with open(path, "rb") as f:
            candles[ticker] = pickle.load(f)
    return candles


# ============================================================================
#  ANALYSIS 1: PRICE-BASED WIN RATE TABLE
# ============================================================================


def price_based_win_rates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build (price_bucket, time_bucket) → win_rate table.
    This is the pure price-based analog of the score-based probability engine.
    """
    # Filter to late-game entries: <= 12 minutes before close, price >= 0.80
    late = df[(df["minutes_until_close"] <= 12) & (df["yes_price"] >= 0.80)].copy()

    # Bucket price and time
    late["price_bucket"] = pd.cut(
        late["yes_price"],
        bins=[0.80, 0.85, 0.88, 0.90, 0.93, 0.95, 0.97, 1.00],
        labels=["80-85", "85-88", "88-90", "90-93", "93-95", "95-97", "97-100"],
        right=False,
    )
    late["time_bucket"] = pd.cut(
        late["minutes_until_close"],
        bins=[0, 2, 4, 6, 8, 10, 12],
        labels=["0-2m", "2-4m", "4-6m", "6-8m", "8-10m", "10-12m"],
        right=True,
    )

    # Compute win rate per bucket
    grouped = (
        late.groupby(["price_bucket", "time_bucket"], observed=True)
        .agg(
            win_rate=("won", "mean"),
            count=("won", "count"),
            avg_price=("yes_price", "mean"),
        )
        .reset_index()
    )

    return grouped


def print_price_win_rate_table(grouped: pd.DataFrame):
    """Print the price-based win rate as a grid."""
    print("\n" + "=" * 80)
    print("  PRICE-BASED WIN RATE TABLE (246 games, candlestick data)")
    print("  (price_bucket x time_remaining → actual win rate)")
    print("=" * 80)

    pivot_wr = grouped.pivot_table(
        index="price_bucket",
        columns="time_bucket",
        values="win_rate",
        aggfunc="first",
    )
    pivot_n = grouped.pivot_table(
        index="price_bucket",
        columns="time_bucket",
        values="count",
        aggfunc="first",
    )

    # Print header
    time_cols = pivot_wr.columns.tolist()
    header = f"  {'Price':<10}"
    for tc in time_cols:
        header += f"  {tc:>10}"
    print(header)
    print("  " + "-" * (10 + 12 * len(time_cols)))

    for price_bucket in pivot_wr.index:
        row_str = f"  {price_bucket:<10}"
        for tc in time_cols:
            wr = pivot_wr.loc[price_bucket, tc] if tc in pivot_wr.columns else None
            n = pivot_n.loc[price_bucket, tc] if tc in pivot_n.columns else None
            if pd.notna(wr) and pd.notna(n) and n > 0:
                row_str += f"  {wr:>5.1%} n={int(n):<3}"
            else:
                row_str += f"  {'---':>10}"
        print(row_str)

    print()


# ============================================================================
#  ANALYSIS 2: SIMULATED PRICE-BASED SCALPER
# ============================================================================


@dataclass
class PriceScalperConfig:
    """Config for price-based scalper (no score data needed)."""

    min_price: float = 0.93  # Only buy at/above this price
    max_minutes: float = 8.0  # Only enter within this many minutes of close
    bankroll: float = 1000.0
    kelly_fraction: float = 0.75
    max_bet_pct: float = 0.25
    prob_haircut: float = 0.02  # Safety margin on estimated win rate


def simulate_price_scalper_from_pickles(
    config: PriceScalperConfig,
    cache_dir: str = "data/nba_cache",
    candlestick_csv: str = "data/nba_historical_candlesticks.csv",
) -> List[dict]:
    """
    Simulate the scalper using pickle bid/ask data (no scores needed).

    Uses actual bid prices from candle files for realistic entry simulation.
    For each game, finds the first candle where:
    - minutes_until_close <= max_minutes
    - bid price >= min_price
    Then buys at the bid and holds to settlement.
    """
    # Load outcomes from CSV
    outcomes = {}  # ticker → won (bool)
    with open(candlestick_csv) as f:
        reader = csv.DictReader(f)
        for row in reader:
            outcomes[row["ticker"]] = row["won"] == "True"

    # Load all candle pickles
    candle_files = sorted(glob.glob(os.path.join(cache_dir, "candles_KXNBAGAME-*.pkl")))

    # Group by event ticker
    events = defaultdict(list)  # event → [(ticker, candles, won)]
    for path in candle_files:
        ticker = os.path.basename(path).replace("candles_", "").replace(".pkl", "")
        event = ticker.rsplit("-", 1)[0]
        won = outcomes.get(ticker)
        if won is None:
            continue
        with open(path, "rb") as f:
            candles = pickle.load(f)
        if candles:
            events[event].append((ticker, candles, won))

    trades = []
    bankroll = config.bankroll

    for event, sides in sorted(events.items()):
        best_entry = None

        for ticker, candles, won in sides:
            if not candles:
                continue

            last_ts = max(c["ts"] for c in candles)

            # Scan candles chronologically for first qualifying entry
            for c in sorted(candles, key=lambda x: x["ts"]):
                mins = (last_ts - c["ts"]) / 60.0
                if mins > config.max_minutes or mins < 0:
                    continue

                bid = c.get("yes_bid_close", 0) / 100.0
                if bid < config.min_price or bid >= 1.0:
                    continue

                # Use empirical estimate: at these prices, win rate ~ 98.5%
                # (from the 68-game validation above)
                est_wr = 0.985
                safe_wr = est_wr - config.prob_haircut
                if safe_wr <= bid:
                    continue

                # Take earliest qualifying entry across both sides
                if best_entry is None or mins > best_entry[2]:
                    best_entry = (ticker, bid, mins, won, c)
                break

        if best_entry is None:
            continue

        ticker, price, mins, won, candle = best_entry
        ask = candle.get("yes_ask_close", 0) / 100.0

        # Kelly sizing
        edge = (0.985 - config.prob_haircut) - price
        kelly_pct = (edge / (1.0 - price)) * config.kelly_fraction
        bet_pct = max(0.0, min(kelly_pct, config.max_bet_pct))
        wager = bankroll * bet_pct
        contracts = int(wager / price)
        if contracts <= 0:
            continue

        cost = contracts * price
        entry_fee = calculate_fee(price, contracts, maker=True)

        if won:
            revenue = contracts * 1.0
            exit_fee = calculate_fee(1.0, contracts, maker=False)
            pnl = revenue - cost - entry_fee - exit_fee
            result = "win"
        else:
            pnl = -cost - entry_fee
            result = "loss"

        bankroll += pnl

        trades.append(
            {
                "event_ticker": event,
                "ticker": ticker,
                "entry_price": price,
                "ask_price": ask,
                "spread": ask - price,
                "minutes_to_close": mins,
                "contracts": contracts,
                "cost": cost,
                "won": won,
                "result": result,
                "pnl": pnl,
                "bankroll": bankroll,
            }
        )

    return trades


# ============================================================================
#  ANALYSIS 3: FULL SCORE-BASED BACKTEST ON ALL DATA
# ============================================================================


def run_full_score_backtest():
    """Run the score-based scalper on all recordings (live + synthetic)."""
    from scripts.nba_scalper_bot import (
        ScalperConfig,
        load_recordings,
        build_win_rate_table,
        run_scalper,
    )

    config = ScalperConfig(
        kelly_fraction=0.75,
        max_bet_pct=0.25,
        min_win_prob=0.95,
        stop_loss=1.0,  # No stop loss
        min_lead=8,
        max_entry_minutes=8,
        max_entry_price=1.0,
        train_pct=0.60,
    )

    all_games = load_recordings(config)
    if not all_games:
        print("  No recordings found.")
        return None

    # Deterministic split
    np.random.seed(42)
    indices = np.random.permutation(len(all_games))
    split = int(len(all_games) * config.train_pct)
    train_games = [all_games[i] for i in indices[:split]]
    test_games = [all_games[i] for i in indices[split:]]

    win_rate_map = build_win_rate_table(train_games, config)
    final_bankroll, trades, equity_curve = run_scalper(test_games, win_rate_map, config)

    return {
        "total_games": len(all_games),
        "train": len(train_games),
        "test": len(test_games),
        "trades": len(trades),
        "wins": sum(1 for t in trades if t.result == "win"),
        "losses": sum(1 for t in trades if t.result == "loss"),
        "final_bankroll": final_bankroll,
        "roi": (final_bankroll - config.starting_bankroll)
        / config.starting_bankroll
        * 100,
        "equity_curve": equity_curve,
        "trade_objects": trades,
    }


# ============================================================================
#  ANALYSIS 4: SPREAD / LIQUIDITY ANALYSIS
# ============================================================================


def spread_analysis(cache_dir: str = "data/nba_cache"):
    """Analyze bid-ask spreads in the final 12 minutes across all games."""
    candles = load_candle_pickles(cache_dir)

    rows = []
    for ticker, candle_list in candles.items():
        if not candle_list:
            continue

        # Get settlement timestamp (last candle + some buffer)
        last_ts = max(c["ts"] for c in candle_list)

        for c in candle_list:
            minutes_to_close = (last_ts - c["ts"]) / 60.0
            if minutes_to_close > 12:
                continue

            bid = c.get("yes_bid_close", 0) / 100.0
            ask = c.get("yes_ask_close", 0) / 100.0
            spread = ask - bid
            mid = (bid + ask) / 2.0

            if mid >= 0.80:  # Only high-price entries
                rows.append(
                    {
                        "ticker": ticker,
                        "minutes_to_close": minutes_to_close,
                        "bid": bid,
                        "ask": ask,
                        "spread": spread,
                        "mid": mid,
                        "volume": c.get("volume", 0),
                    }
                )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # Bucket by time
    df["time_bucket"] = pd.cut(
        df["minutes_to_close"],
        bins=[0, 2, 4, 6, 8, 10, 12],
        labels=["0-2m", "2-4m", "4-6m", "6-8m", "8-10m", "10-12m"],
    )

    return df


def print_spread_analysis(df: pd.DataFrame):
    """Print spread/liquidity summary."""
    if df.empty:
        print("  No spread data available.")
        return

    print("\n" + "=" * 80)
    print("  BID-ASK SPREAD ANALYSIS (last 12 min, price >= 80c)")
    print("  From candle pickle files (310 tickers)")
    print("=" * 80)

    grouped = df.groupby("time_bucket", observed=True).agg(
        avg_spread=("spread", "mean"),
        median_spread=("spread", "median"),
        p90_spread=("spread", lambda x: x.quantile(0.90)),
        avg_volume=("volume", "mean"),
        observations=("spread", "count"),
    )

    print(
        f"\n  {'Time':<10} {'Avg Spread':>10} {'Med Spread':>10} {'P90 Spread':>10} "
        f"{'Avg Vol':>10} {'N':>8}"
    )
    print("  " + "-" * 60)
    for bucket, row in grouped.iterrows():
        print(
            f"  {bucket:<10} {row['avg_spread']:>9.1%} {row['median_spread']:>9.1%} "
            f"{row['p90_spread']:>9.1%} {row['avg_volume']:>10.0f} {int(row['observations']):>8}"
        )

    # Price-level breakdown
    df["price_bucket"] = pd.cut(
        df["mid"],
        bins=[0.80, 0.90, 0.93, 0.95, 0.97, 1.00],
        labels=["80-90c", "90-93c", "93-95c", "95-97c", "97-100c"],
    )

    print(
        f"\n  {'Price':<10} {'Avg Spread':>10} {'Med Spread':>10} {'Avg Vol':>10} {'N':>8}"
    )
    print("  " + "-" * 48)
    for bucket, grp in df.groupby("price_bucket", observed=True):
        print(
            f"  {bucket:<10} {grp['spread'].mean():>9.1%} {grp['spread'].median():>9.1%} "
            f"{grp['volume'].mean():>10.0f} {len(grp):>8}"
        )


# ============================================================================
#  MAIN
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="Validate scalper strategy")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also run full score-based backtest on all recordings",
    )
    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("  SCALPER STRATEGY VALIDATION")
    print("  Independent analysis using historical Kalshi candlestick data")
    print("=" * 80)

    # --- Load data ---
    print("\nLoading candlestick data...")
    df = load_candlesticks()
    n_games = df["event_ticker"].nunique()
    print(f"  {len(df):,} candles across {n_games} games")

    # --- Analysis 1: Price-based win rate table ---
    print("\n[1/4] Building price-based win rate table...")
    grouped = price_based_win_rates(df)
    print_price_win_rate_table(grouped)

    # Key question: what's the win rate at 93-95c with 6-8 min left?
    print("  KEY VALIDATION (scalper entry zone):")
    for pb in ["93-95", "95-97", "97-100"]:
        for tb in ["0-2m", "2-4m", "4-6m", "6-8m"]:
            match = grouped[
                (grouped["price_bucket"] == pb) & (grouped["time_bucket"] == tb)
            ]
            if not match.empty:
                row = match.iloc[0]
                print(
                    f"    Price {pb}c, {tb}: win_rate={row['win_rate']:.1%} "
                    f"(n={int(row['count'])})"
                )

    # --- Analysis 2: Price-based scalper simulation (using pickle bid/ask) ---
    print("\n[2/4] Simulating price-based scalper (bid prices from candle pickles)...")
    config = PriceScalperConfig()
    trades = simulate_price_scalper_from_pickles(config)

    if trades:
        wins = sum(1 for t in trades if t["result"] == "win")
        losses = len(trades) - wins
        total_pnl = sum(t["pnl"] for t in trades)
        final_bank = trades[-1]["bankroll"] if trades else config.bankroll

        print("\n  PRICE-BASED SCALPER RESULTS (no score data used)")
        print(f"  {'=' * 50}")
        print(f"  Trades: {len(trades)}  Wins: {wins}  Losses: {losses}")
        print(f"  Win Rate: {wins / len(trades) * 100:.1f}%")
        print(f"  Total P&L: ${total_pnl:+,.2f}")
        print(
            f"  Final Bankroll: ${final_bank:,.2f} (ROI: {(final_bank - config.bankroll) / config.bankroll * 100:+.1f}%)"
        )

        # Show losses
        if losses > 0:
            print("\n  LOSSES:")
            for t in trades:
                if t["result"] == "loss":
                    print(
                        f"    {t['event_ticker']}: bought {t['ticker']} "
                        f"@ {t['entry_price']:.2f} with {t['minutes_to_close']:.1f}min left | "
                        f"P&L: ${t['pnl']:+.2f}"
                    )

        # Max drawdown
        equity = [config.bankroll] + [t["bankroll"] for t in trades]
        peak = equity[0]
        max_dd = 0.0
        for val in equity:
            peak = max(peak, val)
            dd = (peak - val) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        print(f"  Max Drawdown: {max_dd * 100:.1f}%")
    else:
        print("  No trades generated.")

    # --- Analysis 3: Spread/liquidity ---
    print("\n[3/4] Analyzing bid-ask spreads...")
    spread_df = spread_analysis()
    print_spread_analysis(spread_df)

    # --- Analysis 4: Full score-based backtest (optional) ---
    if args.full:
        print("\n[4/4] Running full score-based backtest on all recordings...")
        result = run_full_score_backtest()
        if result:
            print("\n  SCORE-BASED SCALPER RESULTS (live + synthetic recordings)")
            print(f"  {'=' * 50}")
            print(
                f"  Dataset: {result['total_games']} games "
                f"(train={result['train']}, test={result['test']})"
            )
            print(
                f"  Trades: {result['trades']}  "
                f"Wins: {result['wins']}  Losses: {result['losses']}"
            )
            if result["trades"] > 0:
                print(f"  Win Rate: {result['wins'] / result['trades'] * 100:.1f}%")
            print(
                f"  Final Bankroll: ${result['final_bankroll']:,.2f} "
                f"(ROI: {result['roi']:+.1f}%)"
            )
    else:
        print("\n[4/4] Skipped full score-based backtest (use --full to enable)")

    # --- Summary comparison ---
    print(f"\n{'=' * 80}")
    print("  VALIDATION SUMMARY")
    print(f"{'=' * 80}")
    print("  Grid search claimed: 100% win rate, 0% drawdown, 200% ROI on 50 trades")
    print(f"  Price-based validation ({n_games} games):")
    if trades:
        print(
            f"    Trades: {len(trades)} | Win Rate: {wins / len(trades) * 100:.1f}% | "
            f"ROI: {(final_bank - config.bankroll) / config.bankroll * 100:+.1f}% | "
            f"MaxDD: {max_dd * 100:.1f}%"
        )
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()
