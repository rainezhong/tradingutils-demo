#!/usr/bin/env python3
"""
End-to-end training pipeline for the HMM + GBM win probability model.

Steps:
1. Load all PBP files → extract window features per game
2. Train HMM on ALL windows (once, frozen)
3. Run HMM forward on each game → compute state posteriors at each window
4. Build snapshot feature matrix: raw game-state + HMM posteriors, label = home_won
5. Hyperparameter search (50 configs × 5-fold GroupKFold)
6. Fit isotonic calibration on OOF predictions
7. Train final GBM on ALL data with best config
8. Save all artifacts

Usage:
    python3 scripts/train_win_prob_model.py
    python3 scripts/train_win_prob_model.py --skip-bic --n-configs 20
"""

import argparse
import json
import logging
import os
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.models.feature_engineering import (
    assemble_gbm_features,
    determine_home_team,
    extract_snapshot_features,
    extract_window_features,
    get_game_outcome,
    get_gbm_feature_names,
)
from src.models.hmm_feature_extractor import HMMFeatureExtractor
from src.models.gbm_trainer import GBMTrainer, compute_metrics
from src.models.team_strength import TeamStrength, game_id_to_season, get_away_team

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PBP_CACHE_DIR = "data/nba_cache/pbp"
MODEL_DIR = "models"


def load_all_pbp(pbp_dir: str):
    """Load all cached PBP files.

    Returns list of (game_id, actions) tuples.
    """
    games = []
    pbp_files = sorted(Path(pbp_dir).glob("pbp_*.pkl"))

    for path in pbp_files:
        game_id = path.stem.replace("pbp_", "")
        try:
            with open(path, "rb") as f:
                actions = pickle.load(f)
            if actions and len(actions) > 10:
                games.append((game_id, actions))
        except Exception as e:
            logger.warning(f"Failed to load {path}: {e}")

    return games


def process_game(game_id: str, actions: list, team_strength: TeamStrength = None):
    """Process a single game's PBP data.

    Returns:
        dict with window_features, snapshot_features, snapshot_meta,
        home_tricode, outcome, or None if game is invalid.
    """
    home_tricode = determine_home_team(actions)
    if home_tricode is None:
        return None

    outcome = get_game_outcome(actions, home_tricode)
    if outcome is None:
        return None

    # Get team strength features
    team_stats = None
    if team_strength:
        away_tricode = get_away_team(actions, home_tricode)
        if away_tricode:
            season = game_id_to_season(game_id)
            team_stats = team_strength.get_game_features(
                home_tricode, away_tricode, season
            )

    # Extract features
    window_feats, window_meta = extract_window_features(actions, home_tricode)
    snapshot_feats, snapshot_meta = extract_snapshot_features(
        actions, home_tricode, team_stats=team_stats
    )

    if len(window_feats) == 0 or len(snapshot_feats) == 0:
        return None

    return {
        "game_id": game_id,
        "home_tricode": home_tricode,
        "outcome": outcome,
        "window_features": window_feats,
        "snapshot_features": snapshot_feats,
        "snapshot_meta": snapshot_meta,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Train HMM + GBM win probability model"
    )
    parser.add_argument("--pbp-dir", default=PBP_CACHE_DIR, help="PBP cache directory")
    parser.add_argument("--model-dir", default=MODEL_DIR, help="Output model directory")
    parser.add_argument(
        "--n-configs", type=int, default=50, help="Hyperparameter search configs"
    )
    parser.add_argument("--n-splits", type=int, default=5, help="CV folds")
    parser.add_argument(
        "--skip-bic", action="store_true", help="Skip BIC model selection, use 5 states"
    )
    parser.add_argument(
        "--n-states", type=int, default=5, help="HMM states (if --skip-bic)"
    )
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.makedirs(args.model_dir, exist_ok=True)

    t_start = time.time()
    print("=" * 60)
    print("  HMM + GBM Win Probability Model Training")
    print("=" * 60)

    # ── Step 1: Load PBP data ──────────────────────────────────
    print("\n[1/8] Loading PBP data...")
    raw_games = load_all_pbp(args.pbp_dir)
    print(f"  Loaded {len(raw_games)} PBP files")

    if len(raw_games) < 50:
        print(
            "  WARNING: Very few games loaded. Consider running fetch_historical_pbp.py first."
        )

    # ── Step 1b: Load team strength data ──────────────────────
    print("  Loading team strength data...")
    seasons = sorted(set(game_id_to_season(gid) for gid, _ in raw_games))
    team_strength = TeamStrength(preload_seasons=seasons)
    print(f"  Loaded team stats for {len(seasons)} seasons: {', '.join(seasons)}")

    # ── Step 2: Process games (extract features + outcomes) ────
    print("\n[2/8] Processing games...")
    processed = []
    skipped = 0
    for i, (game_id, actions) in enumerate(raw_games):
        result = process_game(game_id, actions, team_strength=team_strength)
        if result:
            processed.append(result)
        else:
            skipped += 1
        if (i + 1) % 500 == 0:
            print(
                f"  [{i + 1}/{len(raw_games)}] processed ({len(processed)} valid, {skipped} skipped)"
            )

    print(f"  Valid games: {len(processed)}, Skipped: {skipped}")

    if len(processed) < 20:
        print("ERROR: Too few valid games for training. Aborting.")
        sys.exit(1)

    # ── Step 3: Train HMM ─────────────────────────────────────
    print("\n[3/8] Training HMM...")
    window_sequences = [g["window_features"] for g in processed]

    # BIC model selection (optional)
    n_states = args.n_states
    if not args.skip_bic and len(processed) >= 100:
        print("  Running BIC model selection...")
        hmm_extractor = HMMFeatureExtractor(n_states=5)
        n_states = hmm_extractor.bic_select(window_sequences)
        print(f"  BIC selected {n_states} states")

    hmm_extractor = HMMFeatureExtractor(n_states=n_states)
    hmm_extractor.fit(window_sequences)
    print(
        f"  HMM trained: {n_states} states, {sum(len(s) for s in window_sequences)} total windows"
    )
    print(hmm_extractor.describe_states())

    # ── Step 4: Build GBM feature matrix ──────────────────────
    print("\n[4/8] Building feature matrix...")
    all_X = []
    all_y = []
    all_groups = []

    for g in processed:
        # Get HMM posteriors for each window
        posteriors = hmm_extractor.predict_proba(g["window_features"])

        # Align snapshots with window posteriors
        # Both are sampled at 2-minute intervals, so they should align
        n_snapshots = len(g["snapshot_features"])
        n_windows = len(posteriors)
        n_common = min(n_snapshots, n_windows)

        for j in range(n_common):
            features = assemble_gbm_features(
                g["snapshot_features"][j],  # 20 snapshot features
                posteriors[j],  # N HMM state posteriors
            )  # Returns: 20 + N + 1 (entropy)
            all_X.append(features)
            all_y.append(1.0 if g["outcome"]["home_won"] else 0.0)
            all_groups.append(g["game_id"])

    X = np.array(all_X)
    y = np.array(all_y)
    groups = np.array(all_groups)

    print(f"  Feature matrix: {X.shape[0]} samples × {X.shape[1]} features")
    print(f"  Home win rate: {y.mean():.3f}")
    print(f"  Unique games: {len(set(all_groups))}")

    feature_names = get_gbm_feature_names(n_states)

    # ── Step 5: Hyperparameter search ─────────────────────────
    print(
        f"\n[5/8] Hyperparameter search ({args.n_configs} configs × {args.n_splits} folds)..."
    )
    trainer = GBMTrainer(n_splits=args.n_splits)
    best_params = trainer.hyperparameter_search(X, y, groups, n_configs=args.n_configs)
    display_params = {
        k: int(v)
        if isinstance(v, (np.integer,))
        else float(v)
        if isinstance(v, (np.floating,))
        else v
        for k, v in best_params.items()
        if k not in ("objective", "metric", "verbose")
    }
    print(f"  Best params: {json.dumps(display_params, indent=4)}")

    # ── Step 6: Calibration ───────────────────────────────────
    print("\n[6/8] Fitting isotonic calibration on OOF predictions...")
    trainer.fit_calibration(trainer.oof_predictions, y)

    # Compute OOF metrics
    oof_metrics_raw = compute_metrics(y, trainer.oof_predictions)
    cal_preds = trainer.calibrator.predict(trainer.oof_predictions)
    oof_metrics_cal = compute_metrics(y, cal_preds)

    print(
        f"  OOF metrics (raw):        Brier={oof_metrics_raw['brier_score']:.4f}, "
        f"LogLoss={oof_metrics_raw['log_loss']:.4f}, AUC={oof_metrics_raw['auc_roc']:.4f}, "
        f"ECE={oof_metrics_raw['ece']:.4f}"
    )
    print(
        f"  OOF metrics (calibrated): Brier={oof_metrics_cal['brier_score']:.4f}, "
        f"LogLoss={oof_metrics_cal['log_loss']:.4f}, AUC={oof_metrics_cal['auc_roc']:.4f}, "
        f"ECE={oof_metrics_cal['ece']:.4f}"
    )

    # ── Step 7: Train final model ─────────────────────────────
    print("\n[7/8] Training final model on all data...")
    trainer.train_final(X, y, best_params)

    # Feature importance
    importance = trainer.feature_importance(feature_names)
    print("  Feature importance (top 10):")
    for name, gain in importance[:10]:
        print(f"    {name}: {gain:.1f}")

    # Check if HMM features contribute
    hmm_gains = [g for n, g in importance if n.startswith("hmm_state_")]
    total_gain = sum(g for _, g in importance)
    hmm_pct = sum(hmm_gains) / total_gain * 100 if total_gain > 0 else 0
    print(f"\n  HMM state features: {hmm_pct:.1f}% of total gain")
    if hmm_pct < 1.0:
        print(
            "  WARNING: HMM features contribute very little. Consider GBM-only model."
        )

    # ── Step 8: Save artifacts ────────────────────────────────
    print("\n[8/8] Saving artifacts...")
    hmm_path = os.path.join(args.model_dir, "hmm_win_prob.pkl")
    gbm_path = os.path.join(args.model_dir, "gbm_win_prob.txt")
    cal_path = os.path.join(args.model_dir, "calibration_win_prob.pkl")
    report_path = os.path.join(args.model_dir, "training_report.json")

    hmm_extractor.save(hmm_path)
    trainer.save(gbm_path, cal_path)

    # Training report
    elapsed = time.time() - t_start

    def _jsonable(v):
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        return v

    report = {
        "training_time_sec": elapsed,
        "n_games": len(processed),
        "n_samples": len(X),
        "n_features": X.shape[1],
        "hmm_states": n_states,
        "best_params": {
            k: _jsonable(v) for k, v in best_params.items() if k not in ("verbose",)
        },
        "oof_metrics_raw": oof_metrics_raw,
        "oof_metrics_calibrated": oof_metrics_cal,
        "feature_importance": {n: float(g) for n, g in importance},
        "hmm_gain_pct": hmm_pct,
        "home_win_rate": float(y.mean()),
    }

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    print("\n  Saved:")
    print(f"    HMM:         {hmm_path}")
    print(f"    GBM:         {gbm_path}")
    print(f"    Calibration: {cal_path}")
    print(f"    Report:      {report_path}")

    print(f"\n{'=' * 60}")
    print(f"  Training complete in {elapsed:.0f}s")
    print(
        f"  Brier: {oof_metrics_cal['brier_score']:.4f}  |  "
        f"LogLoss: {oof_metrics_cal['log_loss']:.4f}  |  "
        f"AUC: {oof_metrics_cal['auc_roc']:.4f}  |  "
        f"ECE: {oof_metrics_cal['ece']:.4f}"
    )
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
