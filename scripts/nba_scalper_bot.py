#!/usr/bin/env python3
"""
NBA Scalper Bot — Kelly-sized Q4 blowout scalper with empirical win-rate engine.

Builds a probability lookup table from historical data (lead × minute → win rate),
then trades games where the leading team's empirical win rate exceeds a confidence
threshold. Position sizes via fractional Kelly with a safety cap.

Usage:
    python scripts/nba_scalper_bot.py
    python scripts/nba_scalper_bot.py --kelly 0.5 --max-bet 0.10 --stop-loss 0.08
    python scripts/nba_scalper_bot.py --train-pct 0.6 --verbose
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import json
import glob
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from src.kalshi.fees import calculate_fee

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================================
#  ML MODEL PROBABILITY ENGINE
# ============================================================================


def build_ml_win_rate_map(
    games: List[dict],
    config: "ScalperConfig",
) -> Dict[str, object]:
    """Build an ML-based probability engine with proper holdout.

    Trains a GBM that has NEVER seen any of the Kalshi recording games,
    using the pre-trained HMM (which learns generic dynamics, not outcomes).
    This ensures honest backtest results with no look-ahead bias.
    """
    import pickle
    from pathlib import Path
    from src.models.hmm_gbm_model import HMMGBMModel
    from src.models.hmm_feature_extractor import HMMFeatureExtractor
    from src.models.gbm_trainer import GBMTrainer
    from src.models.feature_engineering import (
        assemble_gbm_features,
        determine_home_team,
        extract_window_features,
        extract_snapshot_features,
        get_game_outcome,
    )
    from src.models.team_strength import TeamStrength, game_id_to_season, get_away_team

    # Load pre-trained HMM (trained on all data — learns generic dynamics, not outcomes)
    hmm_path = "models/hmm_win_prob.pkl"
    if not os.path.exists(hmm_path):
        print("  [ERROR] HMM not found. Run train_win_prob_model.py first.")
        return None

    hmm = HMMFeatureExtractor.load(hmm_path)

    # Collect game_ids from ALL recordings (both train and test splits)
    holdout_ids = set()
    for game in games:
        gid = game.get("game_id")
        if gid:
            holdout_ids.add(gid)

    # Also scan recording files directly for any we might have missed
    for pattern in ["data/recordings/*.json", "data/recordings/synthetic/*.json"]:
        import json as _json

        for path in glob.glob(pattern):
            try:
                with open(path) as f:
                    meta = _json.load(f)["metadata"]
                gid = meta.get("game_id")
                if gid:
                    holdout_ids.add(gid)
            except Exception:
                continue

    print(
        f"  Holdout: {len(holdout_ids)} Kalshi recording games excluded from training"
    )

    # Load all PBP data
    pbp_dir = Path("data/nba_cache/pbp")
    train_data = []  # (game_id, window_feats, snapshot_feats, label)
    skipped = 0

    # Pre-load team strength data
    all_game_ids = [p.stem.replace("pbp_", "") for p in pbp_dir.glob("pbp_*.pkl")]
    seasons = sorted(set(game_id_to_season(gid) for gid in all_game_ids))
    team_strength = TeamStrength(preload_seasons=seasons)

    for path in sorted(pbp_dir.glob("pbp_*.pkl")):
        game_id = path.stem.replace("pbp_", "")
        if game_id in holdout_ids:
            skipped += 1
            continue  # EXCLUDE from training

        try:
            with open(path, "rb") as f:
                actions = pickle.load(f)
        except Exception:
            continue

        home = determine_home_team(actions)
        if not home:
            continue
        outcome = get_game_outcome(actions, home)
        if not outcome:
            continue

        # Get team strength features
        away = get_away_team(actions, home)
        team_stats = None
        if away:
            season = game_id_to_season(game_id)
            team_stats = team_strength.get_game_features(home, away, season)

        wf, _ = extract_window_features(actions, home)
        sf, _ = extract_snapshot_features(actions, home, team_stats=team_stats)
        if len(wf) == 0 or len(sf) == 0:
            continue

        train_data.append((game_id, wf, sf, 1.0 if outcome["home_won"] else 0.0))

    print(f"  Training GBM on {len(train_data)} non-Kalshi games (excluded {skipped})")

    # Build feature matrix
    all_X, all_y, all_groups = [], [], []
    for game_id, wf, sf, label in train_data:
        posteriors = hmm.predict_proba(wf)
        n = min(len(sf), len(posteriors))
        for j in range(n):
            features = assemble_gbm_features(sf[j], posteriors[j])
            all_X.append(features)
            all_y.append(label)
            all_groups.append(game_id)

    X = np.array(all_X)
    y = np.array(all_y)
    groups = np.array(all_groups)

    # Train GBM with CV to get calibration, then final model
    trainer = GBMTrainer(n_splits=5)

    # Load best params from the full training run (architecture decisions are fine to reuse)
    report_path = "models/training_report.json"
    if os.path.exists(report_path):
        import json as _json

        with open(report_path) as f:
            report = _json.load(f)
        best_params = report.get("best_params", {})
        best_params["verbose"] = -1
    else:
        best_params = None

    # CV for calibration
    _, oof_preds = trainer.cross_validate(X, y, groups, best_params)
    trainer.fit_calibration(oof_preds, y)

    # Train final holdout model on all non-Kalshi data
    trainer.train_final(X, y, best_params)

    # Wrap in HMMGBMModel interface
    model = HMMGBMModel.__new__(HMMGBMModel)
    model.name = "HMMGBMModel_holdout"
    model.hmm = hmm
    model.trainer = trainer
    model._is_fitted = True

    from src.backtesting.models.base import WalkForwardState

    model.state = WalkForwardState()

    # Pre-load PBP data for recording games (for prediction with PBP features)
    game_pbp = {}
    game_home_tricode = {}
    game_team_stats = {}
    for game in games:
        gid = game.get("game_id")
        if gid:
            pbp_path = pbp_dir / f"pbp_{gid}.pkl"
            if pbp_path.exists():
                try:
                    with open(pbp_path, "rb") as f:
                        actions = pickle.load(f)
                    game_pbp[gid] = actions
                    home = determine_home_team(actions)
                    if home:
                        game_home_tricode[gid] = home
                        away = get_away_team(actions, home)
                        if away:
                            season = game_id_to_season(gid)
                            game_team_stats[gid] = team_strength.get_game_features(
                                home, away, season
                            )
                except Exception:
                    pass
    print(f"  Loaded PBP for {len(game_pbp)}/{len(holdout_ids)} recording games")

    print("  Holdout GBM trained. Ready for honest backtest.")
    return {
        "__ml_model__": model,
        "__game_pbp__": game_pbp,
        "__game_home_tricode__": game_home_tricode,
        "__game_team_stats__": game_team_stats,
    }


def ml_predict_prob(
    ml_model_dict: dict,
    frame: dict,
    config: "ScalperConfig",
    game_id: str = "",
) -> Optional[float]:
    """Get win probability for the LEADING side using ML model."""
    from src.backtesting.models.base import GameState
    from src.models.feature_engineering import compute_pbp_derived_stats

    model = ml_model_dict["__ml_model__"]
    game_pbp = ml_model_dict.get("__game_pbp__", {})
    game_home_tricode = ml_model_dict.get("__game_home_tricode__", {})
    game_team_stats = ml_model_dict.get("__game_team_stats__", {})

    period = frame.get("period", 0)
    hs = frame.get("home_score", 0)
    as_ = frame.get("away_score", 0)
    abs(hs - as_)

    # Parse time remaining to seconds
    time_str = frame.get("time_remaining", "12:00")
    minute, sec = parse_time(time_str)
    time_remaining_seconds = minute * 60 + sec

    game_state = GameState(
        game_id=game_id or "backtest",
        home_team="HOME",
        away_team="AWAY",
        home_score=hs,
        away_score=as_,
        period=period,
        time_remaining_seconds=time_remaining_seconds,
    )

    # Compute PBP-derived stats if PBP data is available
    pbp_stats = None
    if game_id and game_id in game_pbp:
        actions = game_pbp[game_id]
        home_tricode = game_home_tricode.get(game_id)
        if home_tricode:
            # Compute elapsed seconds for this frame
            reg_total = 2880.0
            if period <= 4:
                elapsed = reg_total - (time_remaining_seconds + (4 - period) * 720.0)
            else:
                elapsed = (
                    reg_total + (period - 5) * 300.0 + (300.0 - time_remaining_seconds)
                )
            elapsed = max(elapsed, 1.0)
            pbp_stats = compute_pbp_derived_stats(actions, home_tricode, elapsed)
            # Add team strength to pbp_stats
            ts = game_team_stats.get(game_id)
            if ts and pbp_stats:
                pbp_stats.update(ts)
            elif ts:
                pbp_stats = dict(ts)

    prediction = model.predict(game_state, pbp_stats=pbp_stats)
    # Model predicts P(home_win). If away is leading, flip.
    prob = prediction.home_win_prob
    if as_ > hs:
        prob = 1.0 - prob

    # Apply haircut and cap
    prob = np.clip(prob - config.prob_haircut, 0.0, config.prob_cap)
    return prob


# ============================================================================
#  CONFIGURATION
# ============================================================================


@dataclass
class ScalperConfig:
    starting_bankroll: float = 1000.00
    kelly_fraction: float = 1.0  # 1.0 = full Kelly, 0.5 = half Kelly
    max_bet_pct: float = 0.15  # max % of bankroll per trade
    min_win_prob: float = 0.85  # minimum empirical win rate to enter
    stop_loss: float = 0.10  # exit if bid drops this much below entry
    min_lead: int = 8  # minimum point lead to consider
    max_lead: int = 25  # maximum lead (beyond this, no market depth)
    max_entry_price: float = 1.0  # skip entries above this price
    max_entry_minutes: int = 12  # skip entries with more minutes remaining
    min_period: int = 4  # Q4+
    min_sample_count: int = 3  # require N observations in lookup cell
    prob_haircut: float = 0.02  # subtract from raw win rate (safety margin)
    prob_cap: float = 0.98  # cap win rate (never assume certainty)
    train_pct: float = 0.60  # fraction of games for building probability table
    use_real: bool = True
    use_synthetic: bool = True


# ============================================================================
#  DATA LOADING
# ============================================================================


def load_recordings(config: ScalperConfig) -> List[dict]:
    """Load completed game recordings."""
    paths = []
    if config.use_real:
        paths += glob.glob("data/recordings/*.json")
    if config.use_synthetic:
        paths += glob.glob("data/recordings/synthetic/*.json")

    games = []
    for path in sorted(paths):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            continue

        frames = data.get("frames", [])
        metadata = data.get("metadata", {})
        if not frames:
            continue

        final = frames[-1]
        if final.get("period", 0) < 4:
            continue
        if "final" not in str(final.get("game_status", "")).lower():
            continue

        final_home = final.get("home_score", 0)
        final_away = final.get("away_score", 0)
        if final_home == final_away:
            continue

        games.append(
            {
                "home": metadata.get("home_team", "???"),
                "away": metadata.get("away_team", "???"),
                "game_id": metadata.get("game_id"),
                "winner": "home" if final_home > final_away else "away",
                "final_home": final_home,
                "final_away": final_away,
                "frames": frames,
                "path": path,
                "synthetic": metadata.get("synthetic", False),
            }
        )

    return games


def parse_time(time_str: str) -> Tuple[int, int]:
    """Parse time_remaining string → (minutes, seconds)."""
    try:
        time_str = time_str.split()[-1]
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]), int(float(parts[1]))
        return int(float(parts[0])), 0
    except Exception:
        return 12, 0


# ============================================================================
#  PROBABILITY ENGINE
# ============================================================================


def build_win_rate_table(
    games: List[dict],
    config: ScalperConfig,
) -> Dict[Tuple[int, int], float]:
    """
    Build empirical win-rate lookup: (lead, minute) → safe_win_rate.

    Only uses Q4+ frames where the lead is within [min_lead, max_lead].
    Applies a haircut and cap to avoid overconfidence.
    """
    rows = []
    for game in games:
        winner = game["winner"]
        for frame in game["frames"]:
            period = frame.get("period", 0)
            if period < config.min_period:
                continue
            if frame.get("game_status") == "final":
                continue

            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            lead = abs(hs - as_)
            if lead < config.min_lead or lead > config.max_lead:
                continue

            minute, _ = parse_time(frame.get("time_remaining", "12:00"))
            leading = "home" if hs > as_ else "away"
            rows.append(
                {
                    "lead": lead,
                    "minute": minute,
                    "is_win": int(leading == winner),
                }
            )

    if not rows:
        return {}

    df = pd.DataFrame(rows)
    lookup = (
        df.groupby(["lead", "minute"])["is_win"].agg(["mean", "count"]).reset_index()
    )

    # Filter out low-sample cells
    lookup = lookup[lookup["count"] >= config.min_sample_count]

    # Apply haircut and cap
    lookup["safe_win_rate"] = np.clip(
        lookup["mean"] - config.prob_haircut,
        0.0,
        config.prob_cap,
    )

    return {
        (int(r["lead"]), int(r["minute"])): r["safe_win_rate"]
        for _, r in lookup.iterrows()
    }


# ============================================================================
#  TRADING ENGINE
# ============================================================================


@dataclass
class ScalperTrade:
    game: str
    side: str
    lead_at_entry: int
    minute_at_entry: int
    win_prob: float
    entry_price: float
    contracts: int
    cost: float
    entry_fee: float
    exit_price: Optional[float] = None
    exit_fee: float = 0.0
    exit_reason: str = ""
    result: str = "open"
    pnl: float = 0.0
    synthetic: bool = False


def run_scalper(
    games: List[dict],
    win_rate_map,
    config: ScalperConfig,
) -> Tuple[float, List[ScalperTrade], List[float]]:
    """Execute the scalper strategy across a set of games."""
    bankroll = config.starting_bankroll
    trades: List[ScalperTrade] = []
    equity_curve = [config.starting_bankroll]
    use_ml = isinstance(win_rate_map, dict) and "__ml_model__" in win_rate_map

    for game in games:
        trade = None
        entry_idx = 0
        entered = False
        frames = game["frames"]

        # --- ENTRY SCAN ---
        for i, frame in enumerate(frames):
            if entered:
                break
            if frame.get("game_status") == "final":
                continue
            period = frame.get("period", 0)
            if period < config.min_period:
                continue

            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            lead = abs(hs - as_)
            minute, _ = parse_time(frame.get("time_remaining", "12:00"))

            if minute > config.max_entry_minutes:
                continue

            if use_ml:
                prob = ml_predict_prob(
                    win_rate_map, frame, config, game_id=game.get("game_id", "")
                )
            else:
                prob = win_rate_map.get((lead, minute))
            if prob is None or prob < config.min_win_prob:
                continue

            side = "home" if hs > as_ else "away"
            price = frame.get("home_bid") if side == "home" else frame.get("away_bid")
            if not price or price <= 0 or price >= prob:
                continue
            if price > config.max_entry_price:
                continue

            # Kelly sizing: edge / odds
            edge = prob - price
            kelly_pct = (edge / (1.0 - price)) * config.kelly_fraction
            bet_pct = max(0.0, min(kelly_pct, config.max_bet_pct))
            wager = bankroll * bet_pct

            contracts = int(wager / price)
            if contracts <= 0:
                continue

            cost = contracts * price
            entry_fee = calculate_fee(price, contracts, maker=True)

            entered = True
            entry_idx = i
            trade = ScalperTrade(
                game=f"{game['away']}@{game['home']}",
                side=side,
                lead_at_entry=lead,
                minute_at_entry=minute,
                win_prob=prob,
                entry_price=price,
                contracts=contracts,
                cost=cost,
                entry_fee=entry_fee,
                synthetic=game.get("synthetic", False),
            )

        if not entered or trade is None:
            continue

        # --- EXIT LOGIC ---
        stop_price = trade.entry_price - config.stop_loss

        for frame in frames[entry_idx + 1 :]:
            if frame.get("game_status") == "final":
                break

            cur_bid = (
                frame.get("home_bid") if trade.side == "home" else frame.get("away_bid")
            )
            if cur_bid is None:
                continue

            # Detect lead flip — if the OTHER side is now heavily favored, mark down
            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            cur_leading = "home" if hs > as_ else "away"
            if cur_leading != trade.side:
                # Our side lost the lead, use 1c as conservative mark
                other_bid = (
                    frame.get("away_bid")
                    if trade.side == "home"
                    else frame.get("home_bid")
                )
                if other_bid and other_bid > 0.80:
                    cur_bid = 0.01

            if cur_bid <= stop_price:
                exit_fee = calculate_fee(cur_bid, trade.contracts, maker=True)
                revenue = trade.contracts * cur_bid
                trade.exit_price = cur_bid
                trade.exit_fee = exit_fee
                trade.exit_reason = "stop_loss"
                trade.result = "loss"
                trade.pnl = revenue - trade.cost - trade.entry_fee - exit_fee
                break

        # If not stopped, resolve at game end
        if trade.result == "open":
            if trade.side == game["winner"]:
                revenue = trade.contracts * 1.0
                exit_fee = calculate_fee(1.0, trade.contracts, maker=False)
                trade.exit_price = 1.0
                trade.exit_fee = exit_fee
                trade.exit_reason = "resolution_win"
                trade.result = "win"
                trade.pnl = revenue - trade.cost - trade.entry_fee - exit_fee
            else:
                trade.exit_price = 0.0
                trade.exit_fee = 0.0
                trade.exit_reason = "resolution_loss"
                trade.result = "loss"
                trade.pnl = -trade.cost - trade.entry_fee

        bankroll += trade.pnl
        equity_curve.append(bankroll)
        trades.append(trade)

    return bankroll, trades, equity_curve


# ============================================================================
#  FAST SWEEP RUNNER (returns stats only, no trade objects)
# ============================================================================


def _run_sweep(
    games: List[dict],
    win_rate_map: Dict[Tuple[int, int], float],
    config: ScalperConfig,
) -> dict:
    """Lightweight backtest for parameter sweeps. Returns summary stats only."""
    bankroll = config.starting_bankroll
    peak = bankroll
    max_dd = 0.0
    wins = 0
    losses = 0
    total_pnl = 0.0
    curve = [bankroll]

    for game in games:
        entered = False
        entry_idx = 0
        t_side = None
        t_price = 0.0
        t_contracts = 0
        t_cost = 0.0
        frames = game["frames"]

        for i, frame in enumerate(frames):
            if entered:
                break
            if frame.get("game_status") == "final":
                continue
            if frame.get("period", 0) < config.min_period:
                continue

            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            lead = abs(hs - as_)
            minute, _ = parse_time(frame.get("time_remaining", "12:00"))

            if minute > config.max_entry_minutes:
                continue

            prob = win_rate_map.get((lead, minute))
            if prob is None or prob < config.min_win_prob:
                continue

            side = "home" if hs > as_ else "away"
            price = frame.get("home_bid") if side == "home" else frame.get("away_bid")
            if not price or price <= 0 or price >= prob:
                continue
            if price > config.max_entry_price:
                continue

            kelly_pct = ((prob - price) / (1.0 - price)) * config.kelly_fraction
            bet_pct = max(0.0, min(kelly_pct, config.max_bet_pct))
            wager = bankroll * bet_pct
            contracts = int(wager / price)
            if contracts <= 0:
                continue

            entered = True
            entry_idx = i
            t_side = side
            t_price = price
            t_contracts = contracts
            t_cost = contracts * price

        if not entered:
            continue

        # Exit logic
        stop_price = t_price - config.stop_loss
        result = "open"
        pnl = 0.0

        for frame in frames[entry_idx + 1 :]:
            if frame.get("game_status") == "final":
                break
            cur_bid = (
                frame.get("home_bid") if t_side == "home" else frame.get("away_bid")
            )
            if cur_bid is None:
                continue

            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            cur_leading = "home" if hs > as_ else "away"
            if cur_leading != t_side:
                other_bid = (
                    frame.get("away_bid") if t_side == "home" else frame.get("home_bid")
                )
                if other_bid and other_bid > 0.80:
                    cur_bid = 0.01

            if cur_bid <= stop_price:
                pnl = (t_contracts * cur_bid) - t_cost
                result = "loss"
                break

        if result == "open":
            if t_side == game["winner"]:
                revenue = t_contracts * 1.0
                pnl = revenue - t_cost - (revenue * 0.01)
                result = "win"
            else:
                pnl = -t_cost
                result = "loss"

        bankroll += pnl
        total_pnl += pnl
        curve.append(bankroll)
        if result == "win":
            wins += 1
        else:
            losses += 1

        if bankroll > peak:
            peak = bankroll
        dd = (peak - bankroll) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    n = wins + losses
    return {
        "final": bankroll,
        "roi": (bankroll - config.starting_bankroll) / config.starting_bankroll * 100,
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n * 100) if n > 0 else 0,
        "total_pnl": total_pnl,
        "max_dd": max_dd * 100,
        "curve": curve,
    }


# ============================================================================
#  PARAMETER SWEEP
# ============================================================================


def run_sweep(
    games: List[dict],
    win_rate_map: Dict[Tuple[int, int], float],
    base_config: ScalperConfig,
):
    """Run full parameter sensitivity analysis."""
    from copy import copy
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    print()
    print("=" * 72)
    print("  PARAMETER SWEEP")
    print("=" * 72)

    # --- 1. STOP LOSS SENSITIVITY ---
    stop_values = [0.05, 0.08, 0.10, 0.15, 0.20, 0.30, 0.50, 1.00]
    print("\n  [1/5] Stop Loss Sensitivity")
    print(
        f"  {'Stop':>8} {'ROI':>8} {'Trades':>7} {'Win%':>6} {'MaxDD':>7} {'Final':>10}"
    )
    print("  " + "-" * 50)
    best_stop_roi = -999
    for sv in stop_values:
        cfg = copy(base_config)
        cfg.stop_loss = sv
        r = _run_sweep(games, win_rate_map, cfg)
        label = "None" if sv >= 1.0 else f"${sv:.2f}"
        print(
            f"  {label:>8} {r['roi']:>+7.1f}% {r['trades']:>7} {r['win_rate']:>5.1f}% "
            f"{r['max_dd']:>6.1f}% ${r['final']:>9,.2f}"
        )
        if r["roi"] > best_stop_roi:
            best_stop_roi = r["roi"]

    # --- 2. MIN LEAD SENSITIVITY ---
    lead_values = [8, 10, 12, 14, 16, 18, 20]
    print("\n  [2/5] Minimum Lead Sensitivity")
    print(
        f"  {'MinLead':>8} {'ROI':>8} {'Trades':>7} {'Win%':>6} {'MaxDD':>7} {'Final':>10}"
    )
    print("  " + "-" * 50)
    for lv in lead_values:
        # Rebuild win_rate_map with this min_lead
        cfg = copy(base_config)
        cfg.min_lead = lv
        wrm = build_win_rate_table(games, cfg)
        r = _run_sweep(games, wrm, cfg)
        print(
            f"  {lv:>6}+ {r['roi']:>+7.1f}% {r['trades']:>7} {r['win_rate']:>5.1f}% "
            f"{r['max_dd']:>6.1f}% ${r['final']:>9,.2f}"
        )

    # --- 3. MIN WIN PROB SENSITIVITY ---
    prob_values = [0.80, 0.85, 0.88, 0.90, 0.92, 0.95]
    print("\n  [3/5] Minimum Win Probability Sensitivity")
    print(
        f"  {'MinProb':>8} {'ROI':>8} {'Trades':>7} {'Win%':>6} {'MaxDD':>7} {'Final':>10}"
    )
    print("  " + "-" * 50)
    for pv in prob_values:
        cfg = copy(base_config)
        cfg.min_win_prob = pv
        r = _run_sweep(games, win_rate_map, cfg)
        print(
            f"  {pv:>7.0%} {r['roi']:>+7.1f}% {r['trades']:>7} {r['win_rate']:>5.1f}% "
            f"{r['max_dd']:>6.1f}% ${r['final']:>9,.2f}"
        )

    # --- 4. KELLY x MAX BET GRID ---
    kelly_vals = [0.10, 0.25, 0.50, 0.75, 1.0]
    bet_vals = [0.05, 0.10, 0.15, 0.25]
    print("\n  [4/5] Kelly Fraction x Max Bet Grid (ROI %)")
    header = f"  {'':>10}"
    for k in kelly_vals:
        header += f" K={k:<5}"
    print(header)
    print("  " + "-" * (10 + 7 * len(kelly_vals)))

    best_score = -999
    best_combo = (0, 0)
    roi_grid = []
    dd_grid = []

    for mb in bet_vals:
        row_str = f"  Bet={mb:<4.0%}"
        roi_row = []
        dd_row = []
        for k in kelly_vals:
            cfg = copy(base_config)
            cfg.kelly_fraction = k
            cfg.max_bet_pct = mb
            r = _run_sweep(games, win_rate_map, cfg)
            row_str += f" {r['roi']:>+5.0f}% "
            roi_row.append(r["roi"])
            dd_row.append(r["max_dd"])
            # Score = ROI / max(DD, 1) — reward return, penalize drawdown
            score = r["roi"] / max(r["max_dd"], 1.0)
            if score > best_score:
                best_score = score
                best_combo = (k, mb)
        print(row_str)
        roi_grid.append(roi_row)
        dd_grid.append(dd_row)

    print(f"\n  Best risk-adjusted: Kelly={best_combo[0]}, MaxBet={best_combo[1]:.0%}")

    # --- 4b. DRAWDOWN GRID ---
    print("\n  Max Drawdown Grid (%)")
    header = f"  {'':>10}"
    for k in kelly_vals:
        header += f" K={k:<5}"
    print(header)
    print("  " + "-" * (10 + 7 * len(kelly_vals)))
    for i, mb in enumerate(bet_vals):
        row_str = f"  Bet={mb:<4.0%}"
        for j, k in enumerate(kelly_vals):
            row_str += f" {dd_grid[i][j]:>5.1f}%"
        print(row_str)

    # --- 5. MAX ENTRY TIME SENSITIVITY ---
    time_values = [12, 10, 8, 6, 5, 4, 3]
    print("\n  [5/5] Max Entry Time (minutes remaining)")
    print(
        f"  {'MaxMin':>8} {'ROI':>8} {'Trades':>7} {'Win%':>6} {'MaxDD':>7} {'Final':>10}"
    )
    print("  " + "-" * 50)
    for tv in time_values:
        # Filter win_rate_map to only include entries at or below this minute
        wrm_filtered = {(l, m): p for (l, m), p in win_rate_map.items() if m <= tv}
        cfg = copy(base_config)
        r = _run_sweep(games, wrm_filtered, cfg)
        print(
            f"  {tv:>6}m {r['roi']:>+7.1f}% {r['trades']:>7} {r['win_rate']:>5.1f}% "
            f"{r['max_dd']:>6.1f}% ${r['final']:>9,.2f}"
        )

    # --- SAVE PLOTS ---
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))

    # Plot 1: Stop loss equity curves
    ax = axes[0][0]
    for sv in [0.05, 0.10, 0.20, 0.50, 1.00]:
        cfg = copy(base_config)
        cfg.stop_loss = sv
        r = _run_sweep(games, win_rate_map, cfg)
        label = "None" if sv >= 1.0 else f"Stop ${sv:.2f}"
        ax.plot(r["curve"], label=f"{label} ({r['roi']:+.0f}%)", linewidth=1.5)
    ax.axhline(base_config.starting_bankroll, color="black", linestyle="--", alpha=0.3)
    ax.set_title("Stop Loss Impact")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Bankroll ($)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # Plot 2: Min lead equity curves
    ax = axes[0][1]
    for lv in [8, 12, 16, 20]:
        cfg = copy(base_config)
        cfg.min_lead = lv
        wrm = build_win_rate_table(games, cfg)
        r = _run_sweep(games, wrm, cfg)
        ax.plot(r["curve"], label=f"Lead>={lv} ({r['roi']:+.0f}%)", linewidth=1.5)
    ax.axhline(base_config.starting_bankroll, color="black", linestyle="--", alpha=0.3)
    ax.set_title("Min Lead Impact")
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Bankroll ($)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    # Plot 3: ROI heatmap
    ax = axes[1][0]
    roi_arr = np.array(roi_grid)
    im = ax.imshow(roi_arr, cmap="RdYlGn", aspect="auto")
    ax.set_xticks(range(len(kelly_vals)))
    ax.set_xticklabels([f"{k}" for k in kelly_vals])
    ax.set_yticks(range(len(bet_vals)))
    ax.set_yticklabels([f"{b:.0%}" for b in bet_vals])
    ax.set_xlabel("Kelly Fraction")
    ax.set_ylabel("Max Bet %")
    ax.set_title("ROI % (Kelly x MaxBet)")
    for i in range(len(bet_vals)):
        for j in range(len(kelly_vals)):
            ax.text(
                j,
                i,
                f"{roi_arr[i, j]:+.0f}%",
                ha="center",
                va="center",
                fontsize=9,
                fontweight="bold",
            )
    plt.colorbar(im, ax=ax, shrink=0.8)

    # Plot 4: Drawdown heatmap
    ax = axes[1][1]
    dd_arr = np.array(dd_grid)
    im = ax.imshow(dd_arr, cmap="RdYlGn_r", aspect="auto")
    ax.set_xticks(range(len(kelly_vals)))
    ax.set_xticklabels([f"{k}" for k in kelly_vals])
    ax.set_yticks(range(len(bet_vals)))
    ax.set_yticklabels([f"{b:.0%}" for b in bet_vals])
    ax.set_xlabel("Kelly Fraction")
    ax.set_ylabel("Max Bet %")
    ax.set_title("Max Drawdown % (Kelly x MaxBet)")
    for i in range(len(bet_vals)):
        for j in range(len(kelly_vals)):
            ax.text(j, i, f"{dd_arr[i, j]:.1f}%", ha="center", va="center", fontsize=9)
    plt.colorbar(im, ax=ax, shrink=0.8)

    plt.suptitle(
        "NBA Scalper Bot — Parameter Sensitivity", fontsize=14, fontweight="bold"
    )
    plt.tight_layout()
    plt.savefig("plots/scalper_sweep.png", dpi=150, bbox_inches="tight")
    print("\n  Plots saved to plots/scalper_sweep.png")
    print("=" * 72)


# ============================================================================
#  TOXIC TRADE ANALYSIS
# ============================================================================


def analyze_toxic_trades(
    trades: List[ScalperTrade],
    equity_curve: List[float],
    config: ScalperConfig,
):
    """Identify and analyze the worst trades to find patterns."""
    print()
    print("=" * 72)
    print("  TOXIC TRADE ANALYSIS")
    print("=" * 72)

    if not trades:
        print("  No trades to analyze.")
        return

    losses = [t for t in trades if t.result == "loss"]
    if not losses:
        print("  No losses found.")
        return

    # Sort by worst P&L
    losses.sort(key=lambda t: t.pnl)

    print(f"\n  Total losses: {len(losses)} trades, ${sum(t.pnl for t in losses):,.2f}")
    print(f"  Worst 5 trades account for ${sum(t.pnl for t in losses[:5]):,.2f}")
    print()

    # Show worst trades
    print(
        f"  {'#':>3} {'Game':<16} {'Lead':>5} {'Min':>4} {'Entry':>6} {'Ct':>4} "
        f"{'Exit':>6} {'P&L':>9} {'Reason':>14}"
    )
    print("  " + "-" * 72)
    for i, t in enumerate(losses[:10]):
        exit_p = f"{t.exit_price:.0%}" if t.exit_price is not None else "N/A"
        print(
            f"  {i + 1:>3} {t.game:<16} +{t.lead_at_entry:<4} {t.minute_at_entry:>4} "
            f"{t.entry_price:>5.0%} {t.contracts:>4} {exit_p:>6} "
            f"${t.pnl:>+8.2f} {t.exit_reason:>14}"
        )

    # Pattern analysis
    print("\n  --- Pattern Analysis ---")

    # Entry price buckets
    price_buckets = [
        (0, 0.80, "<80c"),
        (0.80, 0.90, "80-89c"),
        (0.90, 0.95, "90-94c"),
        (0.95, 1.0, "95c+"),
    ]
    print("\n  By Entry Price:")
    print(
        f"  {'Bucket':>10} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'Win%':>6} {'AvgPnL':>8} {'TotalPnL':>10}"
    )
    print("  " + "-" * 58)
    for lo, hi, label in price_buckets:
        bucket = [t for t in trades if lo <= t.entry_price < hi]
        if not bucket:
            continue
        bw = sum(1 for t in bucket if t.result == "win")
        bl = sum(1 for t in bucket if t.result == "loss")
        bp = sum(t.pnl for t in bucket)
        print(
            f"  {label:>10} {len(bucket):>7} {bw:>5} {bl:>7} "
            f"{bw / len(bucket) * 100:>5.1f}% ${bp / len(bucket):>+7.2f} ${bp:>+9.2f}"
        )

    # Entry minute buckets
    min_buckets = [(10, 12, "10-12m"), (7, 9, "7-9m"), (4, 6, "4-6m"), (0, 3, "0-3m")]
    print("\n  By Entry Time:")
    print(
        f"  {'Bucket':>10} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'Win%':>6} {'AvgPnL':>8} {'TotalPnL':>10}"
    )
    print("  " + "-" * 58)
    for lo, hi, label in min_buckets:
        bucket = [t for t in trades if lo <= t.minute_at_entry <= hi]
        if not bucket:
            continue
        bw = sum(1 for t in bucket if t.result == "win")
        bl = sum(1 for t in bucket if t.result == "loss")
        bp = sum(t.pnl for t in bucket)
        print(
            f"  {label:>10} {len(bucket):>7} {bw:>5} {bl:>7} "
            f"{bw / len(bucket) * 100:>5.1f}% ${bp / len(bucket):>+7.2f} ${bp:>+9.2f}"
        )

    # Entry lead buckets
    lead_buckets = [
        (8, 10, "8-10"),
        (11, 14, "11-14"),
        (15, 19, "15-19"),
        (20, 25, "20-25"),
    ]
    print("\n  By Entry Lead:")
    print(
        f"  {'Bucket':>10} {'Trades':>7} {'Wins':>5} {'Losses':>7} {'Win%':>6} {'AvgPnL':>8} {'TotalPnL':>10}"
    )
    print("  " + "-" * 58)
    for lo, hi, label in lead_buckets:
        bucket = [t for t in trades if lo <= t.lead_at_entry <= hi]
        if not bucket:
            continue
        bw = sum(1 for t in bucket if t.result == "win")
        bl = sum(1 for t in bucket if t.result == "loss")
        bp = sum(t.pnl for t in bucket)
        print(
            f"  {'+' + label:>10} {len(bucket):>7} {bw:>5} {bl:>7} "
            f"{bw / len(bucket) * 100:>5.1f}% ${bp / len(bucket):>+7.2f} ${bp:>+9.2f}"
        )

    # Gap risk: trades where exit_price was 1c (lead flip)
    gap_trades = [
        t for t in losses if t.exit_price is not None and t.exit_price <= 0.02
    ]
    if gap_trades:
        print("\n  --- Gap Risk (lead flip → 1c exit) ---")
        print(
            f"  {len(gap_trades)} trades hit by lead flip. Total damage: ${sum(t.pnl for t in gap_trades):,.2f}"
        )
        print(
            f"  These entered at: {', '.join(f'{t.entry_price:.0%}' for t in gap_trades)}"
        )
        print(f"  Entry leads: {', '.join(f'+{t.lead_at_entry}' for t in gap_trades)}")
        print(
            f"  Entry minutes: {', '.join(f'{t.minute_at_entry}m' for t in gap_trades)}"
        )
        without_gaps = sum(t.pnl for t in trades if t not in gap_trades)
        print(f"  P&L WITHOUT these trades: ${without_gaps:+,.2f}")

    # Recommendation
    print("\n  --- Recommendations ---")
    high_price_losses = [t for t in losses if t.entry_price >= 0.90]
    early_losses = [t for t in losses if t.minute_at_entry >= 10]
    if high_price_losses:
        print(
            f"  - {len(high_price_losses)} losses entered at >=90c (${sum(t.pnl for t in high_price_losses):+,.2f})"
        )
        print(
            "    Consider: --max-entry-price 0.90 or lower MAX_BET_PCT for high prices"
        )
    if early_losses:
        print(
            f"  - {len(early_losses)} losses entered with >=10 min left (${sum(t.pnl for t in early_losses):+,.2f})"
        )
        print("    Consider: filtering win_rate_map to max 8-9 minutes")
    if gap_trades:
        print(f"  - {len(gap_trades)} losses from lead-flip gap risk")
        print(
            "    Consider: wider stop, or skip entries where lead < 12 with > 8 min left"
        )

    print()
    print("=" * 72)


# ============================================================================
#  REPORTING
# ============================================================================


def print_report(
    trades: List[ScalperTrade],
    equity_curve: List[float],
    config: ScalperConfig,
    n_train: int,
    n_test: int,
    table_size: int,
):
    print()
    print("=" * 72)
    print("  NBA SCALPER BOT RESULTS")
    print("=" * 72)
    print()
    print("  Config:")
    print(
        f"    Kelly: {config.kelly_fraction:.1f} | Max bet: {config.max_bet_pct:.0%} | "
        f"Min prob: {config.min_win_prob:.0%} | Stop: {config.stop_loss:.0%}"
    )
    print(
        f"    Lead range: {config.min_lead}-{config.max_lead}pts | "
        f"Period >= Q{config.min_period} | Haircut: {config.prob_haircut:.0%}"
    )
    print(
        f"    Train: {n_train} games | Test: {n_test} games | "
        f"Lookup cells: {table_size}"
    )
    print()

    if not trades:
        print("  No trades generated.")
        print("=" * 72)
        return

    n = len(trades)
    wins = [t for t in trades if t.result == "win"]
    losses = [t for t in trades if t.result == "loss"]
    total_pnl = sum(t.pnl for t in trades)
    total_fees = sum(t.entry_fee + t.exit_fee for t in trades)
    final_bankroll = equity_curve[-1]

    print(
        f"  Trades: {n}   Wins: {len(wins)}   Losses: {len(losses)}   "
        f"Win Rate: {len(wins) / n * 100:.1f}%"
    )
    print(
        f"  Starting: ${config.starting_bankroll:,.2f}   "
        f"Final: ${final_bankroll:,.2f}   "
        f"Return: {((final_bankroll - config.starting_bankroll) / config.starting_bankroll) * 100:.1f}%"
    )
    print(f"  Total P&L: ${total_pnl:+,.2f}   Fees paid: ${total_fees:.2f}")

    if wins:
        print(f"  Avg Win: ${sum(t.pnl for t in wins) / len(wins):.2f}   ", end="")
    if losses:
        print(f"Avg Loss: ${sum(t.pnl for t in losses) / len(losses):.2f}")
    else:
        print()

    # Exit breakdown
    from collections import Counter

    exits = Counter(t.exit_reason for t in trades)
    print(f"  Exits: {dict(exits)}")

    # Max drawdown
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        peak = max(peak, val)
        dd = (peak - val) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    print(f"  Max Drawdown: {max_dd * 100:.1f}%")

    # Trade table
    print()
    print(
        f"  {'Game':<16} {'Lead':>5} {'Min':>4} {'Prob':>5} {'Entry':>6} "
        f"{'Ct':>3} {'Exit':>14} {'P&L':>8}"
    )
    print("  " + "-" * 66)
    for t in trades:
        marker = "S" if t.synthetic else " "
        print(
            f" {marker}{t.game:<15} +{t.lead_at_entry:<4} {t.minute_at_entry:>4} "
            f"{t.win_prob:>4.0%} {t.entry_price:>5.0%} {t.contracts:>3} "
            f"{t.exit_reason:>14} ${t.pnl:>+7.2f}"
        )

    print()
    print("=" * 72)


def save_results_csv(
    trades: List[ScalperTrade], filename: str = "nba_scalper_results.csv"
):
    if not trades:
        print("  No trades to save.")
        return
    records = []
    for t in trades:
        records.append(
            {
                "game": t.game,
                "side": t.side,
                "lead_at_entry": t.lead_at_entry,
                "minute_at_entry": t.minute_at_entry,
                "win_prob": round(t.win_prob, 4),
                "entry_price": t.entry_price,
                "contracts": t.contracts,
                "cost": round(t.cost, 2),
                "entry_fee": round(t.entry_fee, 2),
                "exit_price": t.exit_price,
                "exit_fee": round(t.exit_fee, 2),
                "exit_reason": t.exit_reason,
                "result": t.result,
                "pnl": round(t.pnl, 2),
                "synthetic": t.synthetic,
            }
        )
    df = pd.DataFrame(records)
    df.to_csv(filename, index=False)
    print(f"  Trade log saved to {filename}")


# ============================================================================
#  MAIN
# ============================================================================


def run_edge_backtest(
    all_games: List[dict],
    config: ScalperConfig,
    min_edge: float = 0.06,
    exit_edge: float = 0.01,
):
    """Run edge capture backtest: model prob vs market price at every snapshot."""
    from strategies.edge_capture import NBAEdgeCaptureStrategy, EdgeCaptureConfig

    print("\n  Building ML model for edge capture backtest...")
    ml_dict = build_ml_win_rate_map(all_games, config)
    if ml_dict is None:
        print("  [ERROR] Failed to build ML model.")
        return

    model = ml_dict["__ml_model__"]
    game_pbp = ml_dict.get("__game_pbp__", {})
    game_home_tricode = ml_dict.get("__game_home_tricode__", {})
    game_team_stats = ml_dict.get("__game_team_stats__", {})

    # Train/test split (same as main backtest)
    np.random.seed(42)
    indices = np.random.permutation(len(all_games))
    split = int(len(all_games) * config.train_pct)
    test_games = [all_games[i] for i in indices[split:]]

    edge_config = EdgeCaptureConfig(
        min_edge=min_edge,
        exit_edge=exit_edge,
        bankroll=config.starting_bankroll,
        prob_haircut=config.prob_haircut,
        dry_run=True,
    )
    strategy = NBAEdgeCaptureStrategy(edge_config)

    print(f"  Edge capture: min_edge={min_edge:.0%} exit_edge={exit_edge:.0%}")
    print(f"  Test games: {len(test_games)}")

    for game in test_games:
        frames = game["frames"]
        game_id = game.get("game_id", "")
        winner = game["winner"]

        for frame in frames:
            if frame.get("game_status") == "final":
                continue

            period = frame.get("period", 0)
            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            time_str = frame.get("time_remaining", "12:00")
            minute, sec = parse_time(time_str)
            time_remaining_seconds = minute * 60 + sec

            # Compute elapsed minutes
            reg_total = 2880.0
            if period <= 4:
                elapsed_sec = reg_total - (
                    time_remaining_seconds + (4 - period) * 720.0
                )
            else:
                elapsed_sec = (
                    reg_total + (period - 5) * 300.0 + (300.0 - time_remaining_seconds)
                )
            elapsed_minutes = max(elapsed_sec / 60.0, 0.5)

            # Get model probability
            from src.backtesting.models.base import GameState
            from src.models.feature_engineering import compute_pbp_derived_stats

            game_state = GameState(
                game_id=game_id or "backtest",
                home_team="HOME",
                away_team="AWAY",
                home_score=hs,
                away_score=as_,
                period=period,
                time_remaining_seconds=time_remaining_seconds,
            )

            pbp_stats = None
            if game_id and game_id in game_pbp:
                actions = game_pbp[game_id]
                home_tricode = game_home_tricode.get(game_id)
                if home_tricode:
                    pbp_stats = compute_pbp_derived_stats(
                        actions, home_tricode, max(elapsed_sec, 1.0)
                    )
                    ts = game_team_stats.get(game_id)
                    if ts and pbp_stats:
                        pbp_stats.update(ts)
                    elif ts:
                        pbp_stats = dict(ts)

            prediction = model.predict(game_state, pbp_stats=pbp_stats)
            model_prob_home = prediction.home_win_prob

            # Get market probability from recorded Kalshi prices
            home_bid = frame.get("home_bid")
            away_bid = frame.get("away_bid")
            # Use bid price as market implied probability for the leading side
            # Market prob for home = home_bid (or 1 - away_bid)
            if home_bid is not None and home_bid > 0:
                market_prob_home = home_bid
            elif away_bid is not None and away_bid > 0:
                market_prob_home = 1.0 - away_bid
            else:
                continue  # No market data for this frame

            # Evaluate strategy
            signal = strategy.evaluate(
                game_id=game_id,
                model_prob_home=model_prob_home,
                market_prob_home=market_prob_home,
                period=period,
                elapsed_minutes=elapsed_minutes,
                ticker=f"{game.get('away', '???')}@{game.get('home', '???')}",
            )

            if signal is None:
                continue

            if signal["action"] == "enter":
                strategy.enter_position(signal)
            elif signal["action"] == "exit":
                # Exit at current market price
                strategy.exit_position(
                    game_id,
                    reason=signal["reason"],
                    exit_price=market_prob_home
                    if strategy.positions.get(game_id, None)
                    and strategy.positions[game_id].side == "home"
                    else (1.0 - market_prob_home),
                )

        # Settle any remaining position for this game
        if game_id in strategy.positions:
            home_won = winner == "home"
            strategy.settle_position(game_id, home_won)

    # Report
    strategy.print_report()

    # Sensitivity sweep
    print()
    print("=" * 60)
    print("  EDGE THRESHOLD SENSITIVITY")
    print("=" * 60)
    print(
        f"  {'MinEdge':>8} {'Trades':>7} {'Win%':>6} {'ROI':>8} {'MaxDD':>7} {'Final':>10}"
    )
    print("  " + "-" * 52)

    for me in [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]:
        sweep_config = EdgeCaptureConfig(
            min_edge=me,
            exit_edge=exit_edge,
            bankroll=config.starting_bankroll,
            prob_haircut=config.prob_haircut,
            dry_run=True,
        )
        sweep_strat = NBAEdgeCaptureStrategy(sweep_config)

        for game in test_games:
            frames = game["frames"]
            gid = game.get("game_id", "")

            for frame in frames:
                if frame.get("game_status") == "final":
                    continue
                period = frame.get("period", 0)
                hs = frame.get("home_score", 0)
                as_ = frame.get("away_score", 0)
                time_str = frame.get("time_remaining", "12:00")
                m, s = parse_time(time_str)
                trs = m * 60 + s
                rt = 2880.0
                if period <= 4:
                    el = rt - (trs + (4 - period) * 720.0)
                else:
                    el = rt + (period - 5) * 300.0 + (300.0 - trs)
                em = max(el / 60.0, 0.5)

                gs = GameState(
                    game_id=gid or "backtest",
                    home_team="HOME",
                    away_team="AWAY",
                    home_score=hs,
                    away_score=as_,
                    period=period,
                    time_remaining_seconds=trs,
                )
                ps = None
                if gid and gid in game_pbp:
                    ht = game_home_tricode.get(gid)
                    if ht:
                        ps = compute_pbp_derived_stats(game_pbp[gid], ht, max(el, 1.0))
                        ts = game_team_stats.get(gid)
                        if ts and ps:
                            ps.update(ts)
                        elif ts:
                            ps = dict(ts)

                pred = model.predict(gs, pbp_stats=ps)
                hb = frame.get("home_bid")
                ab = frame.get("away_bid")
                if hb and hb > 0:
                    mph = hb
                elif ab and ab > 0:
                    mph = 1.0 - ab
                else:
                    continue

                sig = sweep_strat.evaluate(
                    game_id=gid,
                    model_prob_home=pred.home_win_prob,
                    market_prob_home=mph,
                    period=period,
                    elapsed_minutes=em,
                    ticker=f"{game.get('away', '???')}@{game.get('home', '???')}",
                )
                if sig and sig["action"] == "enter":
                    sweep_strat.enter_position(sig)
                elif sig and sig["action"] == "exit":
                    p = sweep_strat.positions.get(gid)
                    if p:
                        ep = mph if p.side == "home" else (1.0 - mph)
                        sweep_strat.exit_position(gid, sig["reason"], ep)

            if gid in sweep_strat.positions:
                sweep_strat.settle_position(gid, game["winner"] == "home")

        st = sweep_strat.get_stats()
        print(
            f"  {me:>7.0%} {st['trades']:>7} {st['win_rate']:>5.1f}% "
            f"{st['roi']:>+7.1f}% {st['max_drawdown']:>6.1f}% ${st['final_bankroll']:>9,.2f}"
        )

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="NBA Scalper Bot")
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument(
        "--kelly", type=float, default=1.0, help="Kelly fraction (0.5=half, 1.0=full)"
    )
    parser.add_argument(
        "--max-bet", type=float, default=0.15, help="Max bet as pct of bankroll"
    )
    parser.add_argument(
        "--min-prob", type=float, default=0.85, help="Min win probability to enter"
    )
    parser.add_argument(
        "--stop-loss", type=float, default=0.10, help="Stop loss in dollars"
    )
    parser.add_argument("--min-lead", type=int, default=8)
    parser.add_argument("--max-lead", type=int, default=25)
    parser.add_argument(
        "--max-entry-min", type=int, default=12, help="Max entry minutes remaining"
    )
    parser.add_argument(
        "--train-pct",
        type=float,
        default=0.60,
        help="Fraction of data for probability table",
    )
    parser.add_argument(
        "--no-split",
        action="store_true",
        help="Use all data for both train and test (look-ahead bias!)",
    )
    parser.add_argument("--csv", type=str, default="nba_scalper_results.csv")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--sweep", action="store_true", help="Run parameter sensitivity analysis"
    )
    parser.add_argument(
        "--toxic", action="store_true", help="Analyze worst trades for patterns"
    )
    parser.add_argument(
        "--model",
        choices=["lookup", "ml", "edge"],
        default="lookup",
        help="Probability engine: 'lookup' (empirical table), 'ml' (HMM+GBM), or 'edge' (edge capture)",
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.06,
        help="Min edge for edge capture mode (default: 0.06)",
    )
    parser.add_argument(
        "--exit-edge",
        type=float,
        default=0.01,
        help="Exit edge threshold for edge capture mode (default: 0.01)",
    )
    args = parser.parse_args()

    config = ScalperConfig(
        starting_bankroll=args.bankroll,
        kelly_fraction=args.kelly,
        max_bet_pct=args.max_bet,
        min_win_prob=args.min_prob,
        stop_loss=args.stop_loss,
        min_lead=args.min_lead,
        max_lead=args.max_lead,
        max_entry_minutes=args.max_entry_min,
        train_pct=args.train_pct,
    )

    # Load all recordings
    all_games = load_recordings(config)
    if not all_games:
        print("No complete game recordings found.")
        return

    # Edge capture mode
    if args.model == "edge":
        run_edge_backtest(
            all_games, config, min_edge=args.min_edge, exit_edge=args.exit_edge
        )
        return

    # Split into train (build probability table) and test (execute trades)
    if args.no_split:
        train_games = all_games
        test_games = all_games
        print(
            f"  WARNING: Using all {len(all_games)} games for both train and test (look-ahead bias)"
        )
    else:
        np.random.seed(42)
        indices = np.random.permutation(len(all_games))
        split = int(len(all_games) * config.train_pct)
        train_idx = indices[:split]
        test_idx = indices[split:]
        train_games = [all_games[i] for i in train_idx]
        test_games = [all_games[i] for i in test_idx]

    # Build probability engine
    if args.model == "ml":
        win_rate_map = build_ml_win_rate_map(all_games, config)
        if win_rate_map is None:
            # Fallback to lookup
            win_rate_map = build_win_rate_table(train_games, config)
        else:
            print("  Using ML model (HMM+GBM) for probability predictions")
            # ML model uses all data — no train/test split needed for the model itself
            # But we still test on the test split of Kalshi games
    else:
        win_rate_map = build_win_rate_table(train_games, config)

    if not win_rate_map:
        print("No probability data generated. Check lead/period filters.")
        return

    if args.verbose and not (
        isinstance(win_rate_map, dict) and "__ml_model__" in win_rate_map
    ):
        print(f"\n  Probability table ({len(win_rate_map)} cells):")
        for (lead, minute), prob in sorted(win_rate_map.items()):
            print(f"    Lead +{lead}, {minute} min remaining → {prob:.1%}")

    # Parameter sweep mode (lookup only)
    if args.sweep:
        if isinstance(win_rate_map, dict) and "__ml_model__" in win_rate_map:
            print("  Parameter sweep not supported with ML model. Use --model lookup.")
            return
        run_sweep(test_games, win_rate_map, config)
        return

    # Execute trades on test set
    final_bankroll, trades, equity_curve = run_scalper(test_games, win_rate_map, config)

    # Report
    print_report(
        trades,
        equity_curve,
        config,
        len(train_games),
        len(test_games),
        len(win_rate_map),
    )
    save_results_csv(trades, args.csv)

    # Toxic trade analysis
    if args.toxic:
        analyze_toxic_trades(trades, equity_curve, config)


if __name__ == "__main__":
    main()
