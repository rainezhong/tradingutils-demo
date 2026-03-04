#!/usr/bin/env python3
"""Train GBM profit predictor on HMM regime states + features.

Takes HMM-classified regime states and trains a LightGBM model to predict
trade profitability. This is the final layer in the HMM → GBM pipeline.

Flow:
  1. Extract features from probe DB
  2. Get HMM state posteriors for each window
  3. Simulate trades to generate profit labels
  4. Train GBM: (raw features + HMM states) → P(profitable)
  5. Save trained model + calibration

Usage:
    python3 scripts/train_crypto_regime_gbm.py --db data/btc_ob_48h.db
    python3 scripts/train_crypto_regime_gbm.py --db data/btc_probe_20260227.db --hmm models/crypto_regime_hmm_v2.pkl
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models.gbm_trainer import GBMTrainer
from src.models.hmm_feature_extractor import HMMFeatureExtractor
from strategies.crypto_scalp.feature_extraction import (
    FeatureExtractor,
    WindowFeatures,
)


class TradeLabelGenerator:
    """Generate profitability labels by simulating trades on historical data."""

    def __init__(
        self,
        db_path: str,
        exit_delay_sec: float = 20.0,
        fee_pct: float = 0.07,
        min_profit_cents: int = 5,
    ):
        self.db_path = db_path
        self.exit_delay_sec = exit_delay_sec
        self.fee_pct = fee_pct
        self.min_profit_cents = min_profit_cents

    def _get_kalshi_price_at_time(self, ts: float) -> Optional[Tuple[float, int, float]]:
        """Get Kalshi market state at timestamp.

        Returns:
            (btc_price, yes_mid_cents, floor_strike) or None
        """
        with sqlite3.connect(self.db_path) as conn:
            # Get nearest Kalshi snapshot
            row = conn.execute(
                """
                SELECT yes_mid, floor_strike
                FROM kalshi_snapshots
                WHERE ts >= ? AND ts < ?
                  AND yes_mid IS NOT NULL
                  AND floor_strike IS NOT NULL
                ORDER BY ts
                LIMIT 1
                """,
                (ts - 1.0, ts + 3.0)
            ).fetchone()

            if not row or row[0] is None:
                return None

            yes_mid_cents = row[0]
            floor_strike = row[1]

            # Get BTC price at this time
            btc_row = conn.execute(
                """
                SELECT price
                FROM binance_trades
                WHERE ts >= ? AND ts < ?
                ORDER BY ts
                LIMIT 1
                """,
                (ts - 0.5, ts + 0.5)
            ).fetchone()

            if not btc_row:
                return None

            btc_price = btc_row[0]
            return (btc_price, yes_mid_cents, floor_strike)

    def simulate_trade(self, window: WindowFeatures) -> Optional[bool]:
        """Simulate a trade starting at this window.

        Returns:
            True if trade would be profitable, False if loss, None if can't simulate
        """
        entry_ts = window.ts_start

        # Get entry price
        entry_data = self._get_kalshi_price_at_time(entry_ts)
        if entry_data is None:
            return None

        btc_price_entry, yes_mid_entry, floor_strike = entry_data

        # Determine side (YES if BTC > strike, NO otherwise)
        if btc_price_entry > floor_strike:
            side = "YES"
            entry_price_cents = yes_mid_entry
        else:
            side = "NO"
            entry_price_cents = 100 - yes_mid_entry

        # Get exit price after delay
        exit_ts = entry_ts + self.exit_delay_sec
        exit_data = self._get_kalshi_price_at_time(exit_ts)

        if exit_data is None:
            return None

        btc_price_exit, yes_mid_exit, _ = exit_data

        if side == "YES":
            exit_price_cents = yes_mid_exit
        else:
            exit_price_cents = 100 - yes_mid_exit

        # Calculate P&L
        gross_pnl_cents = exit_price_cents - entry_price_cents

        # Apply fees (only on profit)
        if gross_pnl_cents > 0:
            fee_cents = gross_pnl_cents * self.fee_pct
            net_pnl_cents = gross_pnl_cents - fee_cents
        else:
            net_pnl_cents = gross_pnl_cents

        # Label: profitable if net P&L > min threshold
        return net_pnl_cents >= self.min_profit_cents

    def generate_labels(
        self,
        windows: List[WindowFeatures],
        cooldown_sec: float = 15.0,
    ) -> Tuple[List[WindowFeatures], List[bool]]:
        """Generate profit labels for windows.

        Args:
            windows: All windows from database
            cooldown_sec: Minimum time between trades

        Returns:
            (filtered_windows, labels) - windows that could be simulated + their labels
        """
        labeled_windows = []
        labels = []
        last_trade_ts = 0.0

        for window in windows:
            # Cooldown filter
            if window.ts_start < last_trade_ts + cooldown_sec:
                continue

            # Simulate trade
            is_profitable = self.simulate_trade(window)

            if is_profitable is not None:
                labeled_windows.append(window)
                labels.append(is_profitable)
                last_trade_ts = window.ts_start

        return labeled_windows, labels


def train_gbm(
    db_path: str,
    hmm_path: str,
    output_gbm_path: str = "models/crypto_regime_gbm.txt",
    output_cal_path: str = "models/crypto_regime_cal.pkl",
    n_configs: int = 30,
    use_optuna: bool = True,
) -> Tuple[GBMTrainer, HMMFeatureExtractor]:
    """Train GBM profit predictor.

    Args:
        db_path: Path to probe database
        hmm_path: Path to trained HMM model
        output_gbm_path: Where to save GBM model
        output_cal_path: Where to save calibration
        n_configs: Number of hyperparameter configs to try
        use_optuna: Use Optuna for hyperparameter search (else random)

    Returns:
        (trained_gbm, hmm_model)
    """
    print("="*70)
    print("CRYPTO REGIME GBM TRAINING")
    print("="*70)
    print(f"Database: {db_path}")
    print(f"HMM model: {hmm_path}")
    print()

    # Load HMM
    print("Loading HMM model...")
    hmm = HMMFeatureExtractor.load(hmm_path)
    print(f"  Loaded {hmm.n_states}-state HMM")
    print()

    # Extract features
    print("Extracting features from probe database...")
    extractor = FeatureExtractor(db_path, window_sec=5.0)
    windows = extractor.extract_windows(min_trades_per_window=5)
    print(f"  Extracted {len(windows)} windows")

    # Get HMM state posteriors
    print("\nComputing HMM state posteriors...")
    sequences = extractor.extract_sequences(normalize=True)

    # Flatten sequences to match windows
    all_posteriors = []
    for seq in sequences:
        posteriors = hmm.predict_proba(seq)
        all_posteriors.append(posteriors)

    all_posteriors = np.concatenate(all_posteriors, axis=0)
    print(f"  Computed {len(all_posteriors)} state posteriors")

    # Handle mismatch between windows and posteriors (due to episode filtering)
    if len(all_posteriors) != len(windows):
        print(f"  ⚠️  Mismatch: {len(all_posteriors)} posteriors vs {len(windows)} windows")
        print(f"      Using first {len(all_posteriors)} windows")
        windows = windows[:len(all_posteriors)]

    # Generate profit labels
    print("\nGenerating profit labels (simulating trades)...")
    labeler = TradeLabelGenerator(
        db_path=db_path,
        exit_delay_sec=20.0,
        fee_pct=0.07,
        min_profit_cents=5,
    )

    labeled_windows, labels = labeler.generate_labels(windows, cooldown_sec=15.0)
    print(f"  Labeled {len(labeled_windows)} windows")
    print(f"  Profitable: {sum(labels)} ({100*sum(labels)/len(labels):.1f}%)")
    print(f"  Losses: {len(labels) - sum(labels)} ({100*(len(labels)-sum(labels))/len(labels):.1f}%)")

    if len(labeled_windows) < 100:
        raise ValueError(f"Too few labeled windows ({len(labeled_windows)}). Need at least 100.")

    # Assemble features: [raw features + HMM state posteriors]
    print("\nAssembling GBM features...")

    # Map labeled windows back to their indices in all_posteriors
    window_to_idx = {id(w): i for i, w in enumerate(windows)}

    X = []
    y = []
    episode_ids = []  # For GroupKFold CV

    current_episode = 0
    last_ts = 0.0
    episode_gap_sec = 300.0  # 5-minute gap = new episode

    for i, window in enumerate(labeled_windows):
        # Get raw features
        has_spread = window.spread_bps is not None
        raw_features = window.to_array(include_spread=has_spread)

        # Get HMM state posteriors
        window_idx = window_to_idx.get(id(window))
        if window_idx is None or window_idx >= len(all_posteriors):
            continue

        state_probs = all_posteriors[window_idx]

        # Concatenate: [raw features, state posteriors]
        combined = np.concatenate([raw_features, state_probs])
        X.append(combined)
        y.append(labels[i])

        # Assign episode ID (for GroupKFold)
        if window.ts_start > last_ts + episode_gap_sec:
            current_episode += 1
        episode_ids.append(current_episode)
        last_ts = window.ts_start

    X = np.array(X)
    y = np.array(y, dtype=int)
    groups = np.array(episode_ids)

    n_features_raw = len(raw_features)
    n_features_hmm = hmm.n_states

    print(f"  Feature shape: {X.shape}")
    print(f"    Raw features: {n_features_raw}")
    print(f"    HMM states: {n_features_hmm}")
    print(f"    Total: {X.shape[1]}")
    print(f"  Episodes: {len(set(episode_ids))}")
    print()

    # Train GBM with hyperparameter search
    print("Training GBM with hyperparameter search...")
    print(f"  Method: {'Optuna (Bayesian)' if use_optuna else 'Random search'}")
    print(f"  Configs: {n_configs}")
    print(f"  CV folds: 5 (GroupKFold by episode)")
    print()

    gbm = GBMTrainer(n_splits=5, random_state=42)

    best_params = gbm.hyperparameter_search(
        X, y, groups,
        n_configs=n_configs,
        use_optuna=use_optuna,
    )

    print(f"\nBest hyperparameters found:")
    for key, val in best_params.items():
        if key not in ['objective', 'metric', 'verbose']:
            print(f"  {key}: {val}")

    # Train final model on all data
    print("\nTraining final model on all data...")
    gbm.train_final(X, y, params=best_params)

    # Fit calibration on OOF predictions
    print("Fitting isotonic calibration...")
    gbm.fit_calibration(gbm.oof_predictions, y)

    # Feature importance
    print("\n" + "="*70)
    print("FEATURE IMPORTANCE (Top 10)")
    print("="*70)

    feature_names = []
    if has_spread:
        feature_names = ["net_move", "osc_ratio", "volume", "spread_bps", "orderflow"]
    else:
        feature_names = ["net_move", "osc_ratio", "volume"]

    for i in range(hmm.n_states):
        feature_names.append(f"hmm_state_{i}")

    importance = gbm.feature_importance(feature_names, importance_type="gain")

    for i, (name, gain) in enumerate(importance[:10]):
        print(f"  {i+1:2d}. {name:20s}  {gain:>10.1f}")

    # Save models
    print("\n" + "="*70)
    print("SAVING MODELS")
    print("="*70)

    Path(output_gbm_path).parent.mkdir(parents=True, exist_ok=True)
    gbm.save(output_gbm_path, output_cal_path)
    print(f"  GBM saved to {output_gbm_path}")
    print(f"  Calibration saved to {output_cal_path}")

    # Print CV metrics
    print("\n" + "="*70)
    print("CROSS-VALIDATION METRICS")
    print("="*70)

    from src.models.gbm_trainer import compute_metrics

    oof_preds = gbm.oof_predictions
    cal_preds = gbm.calibrator.predict(oof_preds) if gbm.calibrator else oof_preds

    raw_metrics = compute_metrics(y, oof_preds)
    cal_metrics = compute_metrics(y, cal_preds)

    print("\nRaw predictions:")
    for metric, value in raw_metrics.items():
        if metric != 'n_samples':
            print(f"  {metric:20s}: {value:.4f}")

    print("\nCalibrated predictions:")
    for metric, value in cal_metrics.items():
        if metric != 'n_samples':
            print(f"  {metric:20s}: {value:.4f}")

    print()

    return gbm, hmm


def main():
    parser = argparse.ArgumentParser(
        description="Train GBM profit predictor on HMM states"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to probe database"
    )
    parser.add_argument(
        "--hmm",
        default="models/crypto_regime_hmm.pkl",
        help="Path to trained HMM model"
    )
    parser.add_argument(
        "--output-gbm",
        default="models/crypto_regime_gbm.txt",
        help="Output path for GBM model"
    )
    parser.add_argument(
        "--output-cal",
        default="models/crypto_regime_cal.pkl",
        help="Output path for calibration"
    )
    parser.add_argument(
        "--n-configs",
        type=int,
        default=30,
        help="Number of hyperparameter configs to try"
    )
    parser.add_argument(
        "--random-search",
        action="store_true",
        help="Use random search instead of Optuna"
    )

    args = parser.parse_args()

    try:
        gbm, hmm = train_gbm(
            db_path=args.db,
            hmm_path=args.hmm,
            output_gbm_path=args.output_gbm,
            output_cal_path=args.output_cal,
            n_configs=args.n_configs,
            use_optuna=not args.random_search,
        )

        print("="*70)
        print("✅ GBM TRAINING COMPLETE")
        print("="*70)
        print()
        print("Next steps:")
        print("  1. Backtest the HMM → GBM pipeline:")
        print("     python3 scripts/backtest_hmm_gbm.py --db", args.db)
        print()
        print("  2. Compare to baseline:")
        print("     python3 scripts/backtest_hmm_vs_threshold.py --db", args.db)
        print()

    except Exception as e:
        print(f"❌ Training failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
