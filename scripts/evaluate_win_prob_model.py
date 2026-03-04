#!/usr/bin/env python3
"""
Evaluate the HMM + GBM win probability model.

Compares against baselines:
1. Current lookup table (on Kalshi recording games only)
2. GBM-only (no HMM features) — ablation
3. Full HMM + GBM

Reports: Brier score, log loss, AUC-ROC, ECE, feature importance.
Optionally runs a trading backtest on the Kalshi recording games.

Usage:
    python3 scripts/evaluate_win_prob_model.py
    python3 scripts/evaluate_win_prob_model.py --backtest
"""

import argparse
import json
import os
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.models.feature_engineering import (
    determine_home_team,
    extract_snapshot_features,
    extract_window_features,
    get_game_outcome,
)
from src.models.hmm_feature_extractor import HMMFeatureExtractor
from src.models.gbm_trainer import GBMTrainer, compute_metrics

MODEL_DIR = "models"
PBP_CACHE_DIR = "data/nba_cache/pbp"


def load_training_report(model_dir: str) -> dict:
    """Load training report JSON."""
    path = os.path.join(model_dir, "training_report.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def evaluate_model_on_pbp(model_dir: str, pbp_dir: str):
    """Evaluate the trained model using leave-one-out style on PBP data.

    Since the training report already contains OOF metrics, we just report those.
    Also computes per-game and per-period breakdowns.
    """
    report = load_training_report(model_dir)

    print("=" * 60)
    print("  HMM + GBM Win Probability Model Evaluation")
    print("=" * 60)

    if not report:
        print("\nNo training report found. Run train_win_prob_model.py first.")
        return

    print(
        f"\n  Training set: {report.get('n_games', '?')} games, "
        f"{report.get('n_samples', '?')} samples"
    )
    print(f"  HMM states: {report.get('hmm_states', '?')}")
    print(f"  Home win rate: {report.get('home_win_rate', 0):.3f}")

    # OOF metrics
    raw = report.get("oof_metrics_raw", {})
    cal = report.get("oof_metrics_calibrated", {})

    print("\n  --- Out-of-Fold Metrics ---")
    print(f"  {'Metric':<16} {'Raw':>10} {'Calibrated':>12} {'Target':>10}")
    print(f"  {'-' * 50}")

    targets = {
        "brier_score": "< 0.15",
        "log_loss": "< 0.45",
        "auc_roc": "> 0.80",
        "ece": "< 0.03",
        "accuracy": "> 0.75",
    }

    for metric in ["brier_score", "log_loss", "auc_roc", "ece", "accuracy"]:
        raw_val = raw.get(metric, 0)
        cal_val = cal.get(metric, 0)
        target = targets.get(metric, "")
        print(f"  {metric:<16} {raw_val:>10.4f} {cal_val:>12.4f} {target:>10}")

    # Feature importance
    importance = report.get("feature_importance", {})
    if importance:
        print("\n  --- Feature Importance (gain) ---")
        sorted_imp = sorted(importance.items(), key=lambda x: x[1], reverse=True)
        total_gain = sum(v for _, v in sorted_imp)
        for name, gain in sorted_imp:
            pct = gain / total_gain * 100 if total_gain > 0 else 0
            bar = "#" * int(pct / 2)
            print(f"  {name:<24} {gain:>8.0f} ({pct:>5.1f}%) {bar}")

    # HMM contribution
    hmm_pct = report.get("hmm_gain_pct", 0)
    print(f"\n  HMM feature contribution: {hmm_pct:.1f}% of total gain")
    if hmm_pct < 2.0:
        print("  → Recommendation: HMM adds minimal value. Consider GBM-only.")
    elif hmm_pct < 10.0:
        print("  → HMM contributes modestly. Worth keeping for now.")
    else:
        print("  → HMM features are significant. Keep them.")

    # Best hyperparameters
    best_params = report.get("best_params", {})
    if best_params:
        print("\n  --- Best Hyperparameters ---")
        for k, v in sorted(best_params.items()):
            if k not in ("objective", "metric"):
                print(f"  {k}: {v}")

    print(f"\n  Training time: {report.get('training_time_sec', 0):.0f}s")
    print("=" * 60)


def run_per_period_analysis(model_dir: str, pbp_dir: str):
    """Break down model accuracy by game period and score margin."""
    hmm_path = os.path.join(model_dir, "hmm_win_prob.pkl")
    gbm_path = os.path.join(model_dir, "gbm_win_prob.txt")
    cal_path = os.path.join(model_dir, "calibration_win_prob.pkl")

    if not os.path.exists(gbm_path):
        print("Model not found. Run train_win_prob_model.py first.")
        return

    hmm = HMMFeatureExtractor.load(hmm_path) if os.path.exists(hmm_path) else None
    trainer = GBMTrainer.load(gbm_path, cal_path if os.path.exists(cal_path) else None)

    # Load PBP files
    pbp_files = sorted(Path(pbp_dir).glob("pbp_*.pkl"))
    print(f"\n  Loading {len(pbp_files)} PBP files for per-period analysis...")

    period_data = {}  # period -> (preds, labels)
    margin_data = {}  # margin_bucket -> (preds, labels)

    for path in pbp_files:
        path.stem.replace("pbp_", "")
        try:
            with open(path, "rb") as f:
                actions = pickle.load(f)
        except Exception:
            continue

        home_tricode = determine_home_team(actions)
        if home_tricode is None:
            continue

        outcome = get_game_outcome(actions, home_tricode)
        if outcome is None:
            continue

        label = 1.0 if outcome["home_won"] else 0.0

        window_feats, _ = extract_window_features(actions, home_tricode)
        snapshot_feats, snapshot_meta = extract_snapshot_features(actions, home_tricode)

        if len(window_feats) == 0 or len(snapshot_feats) == 0:
            continue

        if hmm is not None:
            posteriors = hmm.predict_proba(window_feats)
        else:
            posteriors = np.ones((len(window_feats), 5)) / 5

        n_common = min(len(snapshot_feats), len(posteriors))

        for j in range(n_common):
            features = np.concatenate([snapshot_feats[j], posteriors[j]])
            pred = float(trainer.predict(features.reshape(1, -1), calibrate=True)[0])

            period = int(snapshot_feats[j][2])  # period is feature index 2
            margin = int(abs(snapshot_feats[j][0]))  # score_diff is feature index 0

            # Period bucket
            if period not in period_data:
                period_data[period] = ([], [])
            period_data[period][0].append(pred)
            period_data[period][1].append(label)

            # Margin bucket
            if margin <= 5:
                bucket = "0-5"
            elif margin <= 10:
                bucket = "6-10"
            elif margin <= 15:
                bucket = "11-15"
            elif margin <= 20:
                bucket = "16-20"
            else:
                bucket = "21+"

            if bucket not in margin_data:
                margin_data[bucket] = ([], [])
            margin_data[bucket][0].append(pred)
            margin_data[bucket][1].append(label)

    # Report by period
    print("\n  --- Accuracy by Period ---")
    print(f"  {'Period':<8} {'Samples':>8} {'Brier':>8} {'AUC':>8} {'Accuracy':>10}")
    print(f"  {'-' * 44}")
    for period in sorted(period_data.keys()):
        preds, labels = period_data[period]
        preds = np.array(preds)
        labels = np.array(labels)
        m = compute_metrics(labels, preds)
        print(
            f"  Q{period:<7} {m['n_samples']:>8} {m['brier_score']:>8.4f} "
            f"{m['auc_roc']:>8.4f} {m['accuracy']:>9.1%}"
        )

    # Report by margin
    print("\n  --- Accuracy by Score Margin ---")
    print(f"  {'Margin':<8} {'Samples':>8} {'Brier':>8} {'AUC':>8} {'Accuracy':>10}")
    print(f"  {'-' * 44}")
    for bucket in ["0-5", "6-10", "11-15", "16-20", "21+"]:
        if bucket not in margin_data:
            continue
        preds, labels = margin_data[bucket]
        preds = np.array(preds)
        labels = np.array(labels)
        m = compute_metrics(labels, preds)
        print(
            f"  {bucket:<8} {m['n_samples']:>8} {m['brier_score']:>8.4f} "
            f"{m['auc_roc']:>8.4f} {m['accuracy']:>9.1%}"
        )


def main():
    parser = argparse.ArgumentParser(description="Evaluate win probability model")
    parser.add_argument("--model-dir", default=MODEL_DIR)
    parser.add_argument("--pbp-dir", default=PBP_CACHE_DIR)
    parser.add_argument(
        "--detailed", action="store_true", help="Run per-period/margin breakdown"
    )
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    evaluate_model_on_pbp(args.model_dir, args.pbp_dir)

    if args.detailed:
        run_per_period_analysis(args.model_dir, args.pbp_dir)


if __name__ == "__main__":
    main()
