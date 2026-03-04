#!/usr/bin/env python3
"""
Scalper Grid Search — exhaustive parameter optimization (vectorized).

Tests every combination of scalper parameters and ranks them by
ROI, risk-adjusted return (ROI/MaxDD), win rate, and drawdown.

All 157K+ configs are evaluated simultaneously per game using numpy
array operations. Optional GPU acceleration via CuPy.

Outputs:
  - Console: top configs by each metric + Pareto frontier
  - CSV: full ranked results for every combo tested

Usage:
    python3 scripts/scalper_grid_search.py              # full grid, numpy
    python3 scripts/scalper_grid_search.py --quick       # reduced grid
    python3 scripts/scalper_grid_search.py --gpu         # use CuPy on GPU
    python3 scripts/scalper_grid_search.py --no-split    # look-ahead bias mode
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import time
import os
from copy import copy
from itertools import product
from typing import Dict, List

import numpy as np
import pandas as pd

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.nba_scalper_bot import (
    ScalperConfig,
    load_recordings,
    build_win_rate_table,
    parse_time,
)


# ============================================================================
#  PARAMETER GRID
# ============================================================================

FULL_GRID = {
    "kelly_fraction": [0.10, 0.25, 0.50, 0.75, 1.0],
    "max_bet_pct": [0.05, 0.10, 0.15, 0.20, 0.25],
    "min_win_prob": [0.80, 0.85, 0.88, 0.90, 0.93, 0.95],
    "stop_loss": [0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 1.00],
    "min_lead": [8, 10, 12, 15, 18],
    "max_entry_minutes": [12, 10, 8, 6, 5, 3],
    "max_entry_price": [1.00, 0.95, 0.92, 0.90, 0.85],
}

QUICK_GRID = {
    "kelly_fraction": [0.25, 0.50, 1.0],
    "max_bet_pct": [0.10, 0.15, 0.25],
    "min_win_prob": [0.85, 0.90, 0.95],
    "stop_loss": [0.08, 0.10, 0.20, 1.00],
    "min_lead": [8, 12, 16],
    "max_entry_minutes": [12, 8, 5],
    "max_entry_price": [1.00, 0.92, 0.85],
}


def count_combos(grid: dict) -> int:
    n = 1
    for vals in grid.values():
        n *= len(vals)
    return n


# ============================================================================
#  PRECOMPUTE GAME DATA
# ============================================================================


def precompute_game(
    game: dict,
    wrm_cache: Dict[int, dict],
    lead_values: List[int],
    stop_values: List[float],
) -> dict:
    """
    Precompute all possible entry candidates and exit outcomes for one game.

    Returns dict with:
      - candidates: list of entry point dicts
      - exit_cache: {(cand_idx, stop_val): (exit_price, is_loss)}
    """
    frames = game["frames"]
    winner = game["winner"]

    # Extract all Q4+ candidate entry frames
    candidates = []
    for i, frame in enumerate(frames):
        if frame.get("period", 0) < 4:
            continue
        if frame.get("game_status") == "final":
            continue

        hs = frame.get("home_score", 0)
        as_ = frame.get("away_score", 0)
        lead = abs(hs - as_)
        if lead == 0:
            continue

        minute, _ = parse_time(frame.get("time_remaining", "12:00"))
        side = "home" if hs > as_ else "away"
        price = frame.get("home_bid") if side == "home" else frame.get("away_bid")
        if not price or price <= 0:
            continue

        # Look up prob in each min_lead's win_rate_map
        probs = {}
        for ml in lead_values:
            if lead >= ml:
                p = wrm_cache[ml].get((lead, minute))
                if p is not None:
                    probs[ml] = p

        if not probs:
            continue

        candidates.append(
            {
                "frame_idx": i,
                "lead": lead,
                "minute": minute,
                "side": side,
                "price": price,
                "probs": probs,
            }
        )

    if not candidates:
        return None

    # Precompute exit outcomes for each (candidate, stop_loss) pair
    exit_cache = {}
    for ci, cand in enumerate(candidates):
        winner_matches = cand["side"] == winner
        entry_price = cand["price"]

        for sv in stop_values:
            stop_price = entry_price - sv
            stopped = False
            exit_p = 0.0

            for frame in frames[cand["frame_idx"] + 1 :]:
                if frame.get("game_status") == "final":
                    break

                cur_bid = (
                    frame.get("home_bid")
                    if cand["side"] == "home"
                    else frame.get("away_bid")
                )
                if cur_bid is None:
                    continue

                # Lead flip detection
                hs = frame.get("home_score", 0)
                as_ = frame.get("away_score", 0)
                cur_leading = "home" if hs > as_ else "away"
                if cur_leading != cand["side"]:
                    other_bid = (
                        frame.get("away_bid")
                        if cand["side"] == "home"
                        else frame.get("home_bid")
                    )
                    if other_bid and other_bid > 0.80:
                        cur_bid = 0.01

                if cur_bid <= stop_price:
                    exit_p = cur_bid
                    stopped = True
                    break

            if stopped:
                # Stopped out: revenue = contracts * exit_p
                # pnl = contracts * exit_p - contracts * entry_price
                #      = contracts * (exit_p - entry_price)
                # Per-dollar-wagered: (exit_p - entry_price) / entry_price ... but we need per-contract
                # Store (exit_price, is_loss=True)
                exit_cache[(ci, sv)] = (exit_p, True)
            elif winner_matches:
                # Resolution win: revenue = contracts * 1.0, fee = revenue * 0.01
                # pnl = contracts * 1.0 - cost - contracts * 0.01
                exit_cache[(ci, sv)] = (1.0, False)
            else:
                # Resolution loss: pnl = -cost
                exit_cache[(ci, sv)] = (0.0, True)

    return {
        "candidates": candidates,
        "exit_cache": exit_cache,
    }


# ============================================================================
#  VECTORIZED GRID SEARCH ENGINE
# ============================================================================


def run_grid_search(
    games: List[dict],
    base_config: ScalperConfig,
    grid: dict,
    use_gpu: bool = False,
) -> pd.DataFrame:
    """
    Vectorized grid search: all configs evaluated simultaneously per game.

    Instead of running 157K sequential backtests, processes all configs as
    numpy arrays. Each game is one sequential step; within each game, all
    configs are handled via vectorized array operations.
    """
    xp = np  # numpy by default
    if use_gpu:
        try:
            import cupy

            xp = cupy
            print("  Using CuPy (GPU acceleration)")
        except ImportError:
            print("  CuPy not found, falling back to numpy (CPU)")

    # 1. Build all config combinations as arrays
    lead_values = grid["min_lead"]
    stop_values = grid["stop_loss"]

    combo_keys = [
        "kelly_fraction",
        "max_bet_pct",
        "min_win_prob",
        "stop_loss",
        "min_lead",
        "max_entry_minutes",
        "max_entry_price",
    ]
    combo_vals = [grid[k] for k in combo_keys]
    all_combos = list(product(*combo_vals))
    N = len(all_combos)

    combo_arr = np.array(all_combos)
    kelly = xp.asarray(combo_arr[:, 0])
    max_bet = xp.asarray(combo_arr[:, 1])
    min_prob = xp.asarray(combo_arr[:, 2])
    stop = xp.asarray(combo_arr[:, 3])
    cfg_lead = xp.asarray(combo_arr[:, 4].astype(int))
    cfg_maxmin = xp.asarray(combo_arr[:, 5].astype(int))
    cfg_maxprc = xp.asarray(combo_arr[:, 6])

    # State arrays
    bankroll = xp.full(N, base_config.starting_bankroll)
    peak = xp.full(N, base_config.starting_bankroll)
    max_dd = xp.zeros(N)
    wins = xp.zeros(N, dtype=int)
    losses = xp.zeros(N, dtype=int)
    total_pnl = xp.zeros(N)

    # 2. Pre-build win_rate_maps
    wrm_cache = {}
    print("  Building probability tables...")
    for ml in lead_values:
        cfg = copy(base_config)
        cfg.min_lead = ml
        wrm_cache[ml] = build_win_rate_table(games, cfg)
        print(f"    min_lead={ml}: {len(wrm_cache[ml])} lookup cells")

    # 3. Precompute all game data
    print(f"  Precomputing entry/exit data for {len(games)} games...")
    t0 = time.time()
    game_data = []
    for game in games:
        gd = precompute_game(game, wrm_cache, lead_values, stop_values)
        game_data.append(gd)
    precompute_time = time.time() - t0
    valid_games = sum(1 for g in game_data if g is not None)
    print(
        f"    Done in {precompute_time:.1f}s — {valid_games} games have trade candidates"
    )

    # 4. Vectorized simulation
    print(f"\n  Running {N:,} configs across {len(games)} games (vectorized)...")
    t0 = time.time()

    for gi, gd in enumerate(game_data):
        if gd is None:
            continue

        candidates = gd["candidates"]
        exit_cache = gd["exit_cache"]

        # Track which configs have entered this game
        entered = xp.zeros(N, dtype=bool)
        game_pnl = xp.zeros(N)
        game_won = xp.zeros(N, dtype=bool)
        game_traded = xp.zeros(N, dtype=bool)

        for ci, cand in enumerate(candidates):
            # Skip if all configs already entered
            remaining = ~entered
            if not xp.any(remaining):
                break

            lead = cand["lead"]
            minute = cand["minute"]
            price = cand["price"]

            # Filter: lead >= config's min_lead
            lead_ok = remaining & (lead >= cfg_lead)
            if not xp.any(lead_ok):
                continue

            # Filter: minute <= config's max_entry_minutes
            time_ok = lead_ok & (minute <= cfg_maxmin)
            if not xp.any(time_ok):
                continue

            # Filter: price <= config's max_entry_price
            price_ok = time_ok & (price <= cfg_maxprc)
            if not xp.any(price_ok):
                continue

            # Filter: prob >= min_win_prob AND price < prob
            # prob depends on config's min_lead value
            prob_ok = xp.zeros(N, dtype=bool)
            config_prob = xp.zeros(N)

            for ml in lead_values:
                if ml not in cand["probs"]:
                    continue
                p = cand["probs"][ml]
                ml_mask = price_ok & (cfg_lead == ml) & (p >= min_prob) & (price < p)
                prob_ok |= ml_mask
                config_prob = xp.where(ml_mask, p, config_prob)

            if not xp.any(prob_ok):
                continue

            match = prob_ok

            # Compute Kelly bet size (vectorized)
            edge = config_prob[match] - price
            kelly_pct = (edge / (1.0 - price)) * kelly[match]
            bet_pct = xp.clip(kelly_pct, 0.0, max_bet[match])
            wager = bankroll[match] * bet_pct
            contracts = xp.floor(wager / price).astype(int)

            # Filter: contracts > 0
            valid = contracts > 0
            if not xp.any(valid):
                entered |= match
                continue

            # Expand valid mask back to full N-size
            match_indices = xp.where(match)[0]
            valid_indices = match_indices[valid]
            valid_contracts = contracts[valid]
            cost = valid_contracts * price

            # Look up exit outcomes for each stop_loss value
            for sv in stop_values:
                sv_mask = stop[valid_indices] == sv
                if not xp.any(sv_mask):
                    continue

                exit_p, is_loss = exit_cache[(ci, sv)]
                sv_indices = valid_indices[sv_mask]
                sv_contracts = valid_contracts[sv_mask]
                sv_cost = cost[sv_mask]

                if is_loss:
                    if exit_p > 0:
                        revenue = sv_contracts * exit_p
                        game_pnl[sv_indices] = revenue - sv_cost
                    else:
                        game_pnl[sv_indices] = -sv_cost
                    game_won[sv_indices] = False
                else:
                    revenue = sv_contracts * 1.0
                    game_pnl[sv_indices] = revenue - sv_cost - (revenue * 0.01)
                    game_won[sv_indices] = True

                game_traded[sv_indices] = True

            entered |= match

        # Update bankroll for all configs that traded this game
        bankroll += game_pnl
        total_pnl += game_pnl
        wins += (game_traded & game_won).astype(int)
        losses += (game_traded & ~game_won).astype(int)

        # Update drawdown
        peak = xp.maximum(peak, bankroll)
        dd = xp.where(peak > 0, (peak - bankroll) / peak, 0.0)
        max_dd = xp.maximum(max_dd, dd)

    sim_time = time.time() - t0
    print(f"  Simulation complete in {sim_time:.1f}s")

    # 5. Build results dataframe
    if use_gpu and xp != np:
        bankroll = xp.asnumpy(bankroll)
        total_pnl = xp.asnumpy(total_pnl)
        wins = xp.asnumpy(wins)
        losses = xp.asnumpy(losses)
        max_dd = xp.asnumpy(max_dd)

    n_trades = wins + losses
    has_trades = n_trades > 0
    roi = np.where(
        has_trades,
        (bankroll - base_config.starting_bankroll)
        / base_config.starting_bankroll
        * 100,
        0.0,
    )
    win_rate = np.where(has_trades, wins / n_trades * 100, 0.0)
    risk_adj = np.where(has_trades, roi / np.maximum(max_dd * 100, 0.1), 0.0)
    avg_pnl = np.where(has_trades, total_pnl / n_trades, 0.0)

    records = []
    for i in range(N):
        if not has_trades[i]:
            continue
        records.append(
            {
                "kelly_fraction": all_combos[i][0],
                "max_bet_pct": all_combos[i][1],
                "min_win_prob": all_combos[i][2],
                "stop_loss": all_combos[i][3],
                "min_lead": int(all_combos[i][4]),
                "max_entry_minutes": int(all_combos[i][5]),
                "max_entry_price": all_combos[i][6],
                "roi": round(float(roi[i]), 2),
                "final_bankroll": round(float(bankroll[i]), 2),
                "trades": int(n_trades[i]),
                "wins": int(wins[i]),
                "losses": int(losses[i]),
                "win_rate": round(float(win_rate[i]), 2),
                "max_drawdown": round(float(max_dd[i] * 100), 2),
                "risk_adj_return": round(float(risk_adj[i]), 3),
                "total_pnl": round(float(total_pnl[i]), 2),
                "avg_pnl": round(float(avg_pnl[i]), 2),
            }
        )

    print(f"  {len(records):,} configs produced trades")
    return pd.DataFrame(records)


# ============================================================================
#  ANALYSIS & REPORTING
# ============================================================================


def print_results(df: pd.DataFrame):
    """Print ranked results across multiple metrics."""

    print()
    print("=" * 90)
    print("  GRID SEARCH RESULTS")
    print("=" * 90)
    print(f"  {len(df):,} valid configurations tested")

    if df.empty:
        print("  No valid results.")
        return

    def show_top(title, sort_col, ascending=False, n=15, filter_fn=None):
        subset = df if filter_fn is None else df[filter_fn(df)]
        if subset.empty:
            return
        print(f"\n  --- {title} (Top {n}) ---")
        top = (
            subset.nlargest(n, sort_col)
            if not ascending
            else subset.nsmallest(n, sort_col)
        )
        print(
            f"  {'#':>3} {'Kelly':>5} {'MaxBet':>6} {'MinP':>5} {'Stop':>5} "
            f"{'Lead':>5} {'MaxMin':>6} {'MaxPrc':>6} | "
            f"{'ROI':>7} {'Win%':>5} {'MaxDD':>6} {'R/DD':>6} {'Trades':>6}"
        )
        print("  " + "-" * 87)
        for rank, (_, row) in enumerate(top.iterrows(), 1):
            stop_str = "None" if row["stop_loss"] >= 1.0 else f"{row['stop_loss']:.2f}"
            price_str = (
                "Any"
                if row["max_entry_price"] >= 1.0
                else f"{row['max_entry_price']:.0%}"
            )
            print(
                f"  {rank:>3} {row['kelly_fraction']:>5.2f} {row['max_bet_pct']:>5.0%} "
                f"{row['min_win_prob']:>4.0%} {stop_str:>5} "
                f"{int(row['min_lead']):>4}+ {int(row['max_entry_minutes']):>4}m {price_str:>6} | "
                f"{row['roi']:>+6.1f}% {row['win_rate']:>4.0f}% {row['max_drawdown']:>5.1f}% "
                f"{row['risk_adj_return']:>5.1f} {int(row['trades']):>6}"
            )

    show_top("HIGHEST ROI", "roi")
    show_top("BEST RISK-ADJUSTED (ROI / MaxDrawdown)", "risk_adj_return")
    show_top(
        "LOWEST DRAWDOWN (with ROI > 0)",
        "max_drawdown",
        ascending=True,
        filter_fn=lambda d: d["roi"] > 0,
    )
    show_top(
        "HIGHEST WIN RATE (min 10 trades)",
        "win_rate",
        filter_fn=lambda d: d["trades"] >= 10,
    )

    # Pareto frontier
    print("\n  --- PARETO FRONTIER (best ROI per drawdown level) ---")
    dd_buckets = [3, 5, 8, 10, 15, 20, 25, 30, 40, 50]
    print(
        f"  {'MaxDD':>7} {'BestROI':>8} {'Kelly':>5} {'MaxBet':>6} {'MinP':>5} "
        f"{'Stop':>5} {'Lead':>5} {'MaxMin':>6} {'MaxPrc':>6} {'Trades':>6}"
    )
    print("  " + "-" * 72)

    for dd_limit in dd_buckets:
        eligible = df[df["max_drawdown"] <= dd_limit]
        if eligible.empty:
            print(f"  {dd_limit:>6}%  {'--':>8}")
            continue
        best = eligible.loc[eligible["roi"].idxmax()]
        stop_str = "None" if best["stop_loss"] >= 1.0 else f"{best['stop_loss']:.2f}"
        price_str = (
            "Any"
            if best["max_entry_price"] >= 1.0
            else f"{best['max_entry_price']:.0%}"
        )
        print(
            f"  <={dd_limit:>3}% {best['roi']:>+7.1f}% {best['kelly_fraction']:>5.2f} "
            f"{best['max_bet_pct']:>5.0%} {best['min_win_prob']:>4.0%} {stop_str:>5} "
            f"{int(best['min_lead']):>4}+ {int(best['max_entry_minutes']):>4}m "
            f"{price_str:>6} {int(best['trades']):>6}"
        )

    # Parameter importance
    print("\n  --- PARAMETER IMPORTANCE (ROI spread by parameter) ---")
    param_cols = [
        "kelly_fraction",
        "max_bet_pct",
        "min_win_prob",
        "stop_loss",
        "min_lead",
        "max_entry_minutes",
        "max_entry_price",
    ]
    importance = []
    for col in param_cols:
        means = df.groupby(col)["roi"].mean()
        spread = means.max() - means.min()
        importance.append((col, spread, means.idxmax(), means.max()))

    importance.sort(key=lambda x: x[1], reverse=True)
    print(f"  {'Parameter':<20} {'ROI Spread':>10} {'Best Value':>12} {'Avg ROI':>10}")
    print("  " + "-" * 55)
    for col, spread, best_val, best_roi in importance:
        if col == "stop_loss" and best_val >= 1.0:
            val_str = "None"
        elif col == "max_entry_price" and best_val >= 1.0:
            val_str = "Any"
        elif isinstance(best_val, float) and best_val < 1:
            val_str = f"{best_val:.2f}"
        else:
            val_str = str(best_val)
        print(f"  {col:<20} {spread:>9.1f}pp {val_str:>12} {best_roi:>+9.1f}%")

    # Summary
    best_overall = df.loc[df["roi"].idxmax()]
    best_safe = df.loc[df["risk_adj_return"].idxmax()]
    print("\n  SUMMARY:")
    for label, best in [
        ("Max ROI config: ", best_overall),
        ("Safest high-ROI:", best_safe),
    ]:
        stop_s = "None" if best["stop_loss"] >= 1.0 else f"${best['stop_loss']:.2f}"
        print(
            f"    {label} K={best['kelly_fraction']}, "
            f"Bet={best['max_bet_pct']:.0%}, "
            f"Prob>={best['min_win_prob']:.0%}, "
            f"Stop={stop_s}, "
            f"Lead>={int(best['min_lead'])}, "
            f"Time<={int(best['max_entry_minutes'])}m, "
            f"Price<={best['max_entry_price']:.0%}"
            f"  ->  ROI={best['roi']:+.1f}%, DD={best['max_drawdown']:.1f}%"
        )

    print()
    print("=" * 90)


# ============================================================================
#  MAIN
# ============================================================================


def main():
    parser = argparse.ArgumentParser(description="Scalper Grid Search (vectorized)")
    parser.add_argument(
        "--quick", action="store_true", help="Use reduced grid for fast iteration"
    )
    parser.add_argument(
        "--gpu", action="store_true", help="Use CuPy for GPU acceleration"
    )
    parser.add_argument(
        "--no-split", action="store_true", help="Use all data (look-ahead bias)"
    )
    parser.add_argument("--train-pct", type=float, default=0.60)
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument("--csv", type=str, default="scalper_grid_results.csv")
    args = parser.parse_args()

    grid = QUICK_GRID if args.quick else FULL_GRID
    total = count_combos(grid)

    print()
    print("=" * 90)
    print(
        f"  SCALPER GRID SEARCH — {'QUICK' if args.quick else 'FULL'} MODE"
        f" {'(GPU)' if args.gpu else '(CPU)'}"
    )
    print(f"  {total:,} parameter combinations to test")
    for k, v in grid.items():
        print(f"    {k}: {v}")
    print("=" * 90)

    base_config = ScalperConfig(
        starting_bankroll=args.bankroll, train_pct=args.train_pct
    )

    # Load recordings
    all_games = load_recordings(base_config)
    if not all_games:
        print("  No recordings found.")
        return

    # Train/test split
    if args.no_split:
        test_games = all_games
        print("\n  WARNING: No train/test split — results have look-ahead bias")
        print(f"  Games: {len(all_games)}")
    else:
        np.random.seed(42)
        indices = np.random.permutation(len(all_games))
        split = int(len(all_games) * args.train_pct)
        test_games = [all_games[i] for i in indices[split:]]
        print(f"\n  Train: {split} games | Test: {len(test_games)} games")

    print()
    t_total = time.time()
    df_results = run_grid_search(test_games, base_config, grid, use_gpu=args.gpu)
    total_time = time.time() - t_total
    print(
        f"  Total time: {total_time:.1f}s ({total / max(total_time, 0.01):.0f} configs/sec)"
    )

    if df_results.empty:
        print("  No valid configurations produced trades.")
        return

    print_results(df_results)

    df_results.sort_values("risk_adj_return", ascending=False).to_csv(
        args.csv, index=False
    )
    print(f"  Full results saved to {args.csv}")
    print(f"  ({len(df_results):,} rows — open in a spreadsheet to sort/filter)")


if __name__ == "__main__":
    main()
