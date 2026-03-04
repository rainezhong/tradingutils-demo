#!/usr/bin/env python3
"""Train HMM regime detector for crypto scalp strategy.

Extracts 5-second window features from probe database and trains
a Gaussian HMM to classify market regimes (trending vs choppy).

Usage:
    python3 scripts/train_crypto_regime_hmm.py --db data/btc_ob_48h.db
    python3 scripts/train_crypto_regime_hmm.py --db data/btc_ob_48h.db --states 3 --bic
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models.hmm_feature_extractor import HMMFeatureExtractor
from strategies.crypto_scalp.feature_extraction import (
    FeatureExtractor,
    print_feature_stats,
)


def train_hmm(
    db_path: str,
    n_states: int = 3,
    use_bic: bool = False,
    output_path: str = "models/crypto_regime_hmm.pkl",
    max_windows: Optional[int] = None,
) -> HMMFeatureExtractor:
    """Train HMM on probe database.

    Args:
        db_path: Path to probe database
        n_states: Number of hidden states (default: 3)
        use_bic: If True, use BIC to select optimal n_states
        output_path: Where to save trained model
        max_windows: If set, randomly sample this many windows (for very large DBs)

    Returns:
        Trained HMMFeatureExtractor
    """
    print("="*70)
    print("CRYPTO REGIME HMM TRAINING")
    print("="*70)
    print(f"Database: {db_path}")
    print(f"States: {n_states} (BIC selection: {use_bic})")
    if max_windows:
        print(f"Max windows: {max_windows:,} (will sample if DB larger)")
    print()

    # Extract features
    print("Extracting features...")
    extractor = FeatureExtractor(db_path, window_sec=5.0)
    windows = extractor.extract_windows(show_progress=True)

    # Sample if dataset is too large
    if max_windows and len(windows) > max_windows:
        print(f"⚠️  Dataset has {len(windows):,} windows, sampling {max_windows:,}...")
        indices = np.random.choice(len(windows), max_windows, replace=False)
        indices = np.sort(indices)  # Keep temporal order
        windows = [windows[i] for i in indices]

    print_feature_stats(windows)
    print()

    # Segment into episodes
    print("Segmenting into episodes...")
    sequences = extractor.extract_sequences(
        episode_gap_sec=300.0,  # 5 min gap = new episode
        min_windows_per_episode=12,  # at least 60 seconds
    )

    print(f"Extracted {len(sequences)} episodes:")
    total_windows = sum(len(seq) for seq in sequences)
    print(f"  Total windows: {total_windows}")
    print(f"  Features per window: {sequences[0].shape[1]}")
    print(f"  Avg windows/episode: {total_windows / len(sequences):.0f}")
    print()

    # Train HMM
    print("Training HMM...")
    hmm = HMMFeatureExtractor(
        n_states=n_states,
        covariance_type="diag",
        n_iter=100,
        n_init=5,
        random_state=42,
    )

    if use_bic:
        print("Running BIC model selection...")
        state_range = [2, 3, 4, 5, 6]
        best_n = hmm.bic_select(sequences, state_range)
        print(f"BIC selected {best_n} states")
        hmm.n_states = best_n

    hmm.fit(sequences)
    print()

    # Describe learned states (manual, since describe_states() is for NBA)
    print("="*70)
    print("LEARNED STATES")
    print("="*70)

    feature_names = ["net_move", "osc_ratio", "volume"]
    if sequences[0].shape[1] == 5:
        feature_names.extend(["spread_bps", "orderflow"])

    means = hmm.model.means_
    for i in range(hmm.n_states):
        print(f"\n  State {i}:")
        for j, name in enumerate(feature_names):
            print(f"    {name}: mean={means[i, j]:.3f}")
    print()

    # Save model
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    hmm.save(output_path)
    print(f"Model saved to {output_path}")
    print()

    # Compute state distribution on training data
    print("="*70)
    print("STATE DISTRIBUTION (Training Data)")
    print("="*70)

    all_posteriors = []
    for seq in sequences:
        posteriors = hmm.predict_proba(seq)
        all_posteriors.append(posteriors)

    # Concatenate all posteriors
    all_posteriors = np.concatenate(all_posteriors, axis=0)
    state_counts = np.argmax(all_posteriors, axis=1)

    for i in range(hmm.n_states):
        count = np.sum(state_counts == i)
        pct = 100 * count / len(state_counts)
        print(f"  State {i}: {count:,} windows ({pct:.1f}%)")

    print()

    # Analyze regime characteristics
    print("="*70)
    print("REGIME CHARACTERISTICS")
    print("="*70)

    means = hmm.model.means_
    feature_names = ["net_move", "osc_ratio", "volume"]
    if sequences[0].shape[1] == 5:
        feature_names.extend(["spread_bps", "orderflow"])

    # Sort states by oscillation ratio (trending = low osc)
    osc_idx = 1  # oscillation_ratio is 2nd feature
    sorted_states = np.argsort(means[:, osc_idx])

    for i, state_idx in enumerate(sorted_states):
        print(f"\nState {state_idx} ({'Trending' if i < len(sorted_states)//2 else 'Choppy'}):")
        for j, name in enumerate(feature_names):
            mean_val = means[state_idx, j]
            print(f"  {name}: {mean_val:.3f}")

    return hmm


def main():
    parser = argparse.ArgumentParser(
        description="Train HMM regime detector for crypto scalp"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to probe database"
    )
    parser.add_argument(
        "--states",
        type=int,
        default=3,
        help="Number of hidden states (default: 3)"
    )
    parser.add_argument(
        "--bic",
        action="store_true",
        help="Use BIC to select optimal number of states"
    )
    parser.add_argument(
        "--output",
        default="models/crypto_regime_hmm.pkl",
        help="Output path for trained model"
    )
    parser.add_argument(
        "--max-windows",
        type=int,
        help="Max windows to use (samples randomly if DB larger). Useful for huge datasets."
    )

    args = parser.parse_args()

    try:
        train_hmm(
            db_path=args.db,
            n_states=args.states,
            use_bic=args.bic,
            output_path=args.output,
            max_windows=args.max_windows,
        )
        print("✅ Training complete!")
    except Exception as e:
        print(f"❌ Training failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
