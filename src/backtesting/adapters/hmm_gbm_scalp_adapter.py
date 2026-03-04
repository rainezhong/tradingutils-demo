"""HMM → GBM crypto scalp adapter for unified backtest framework.

Extends the standard crypto scalp adapter with:
1. HMM regime classification (3 states: trending low-vol, choppy, trending high-vol)
2. GBM profit prediction (uses HMM states + raw features)
3. Trade filtering based on GBM P(profit) threshold

Usage:
    from src.backtesting.adapters.hmm_gbm_scalp_adapter import HMMGBMScalpAdapter
    from src.backtesting.adapters.scalp_adapter import CryptoScalpDataFeed
    from src.backtesting.engine import BacktestEngine

    feed = CryptoScalpDataFeed("data/btc_ob_48h.db")
    adapter = HMMGBMScalpAdapter(
        hmm_path="models/crypto_regime_hmm.pkl",
        gbm_path="models/crypto_regime_gbm.txt",
        gbm_threshold=0.20,
    )
    engine = BacktestEngine()
    result = engine.run(feed, adapter)
"""

from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from src.models.gbm_trainer import GBMTrainer
from src.models.hmm_feature_extractor import HMMFeatureExtractor
from strategies.base import Signal

from ..data_feed import BacktestFrame
from .scalp_adapter import CryptoScalpAdapter


class HMMGBMScalpAdapter(CryptoScalpAdapter):
    """Crypto scalp adapter with HMM → GBM profit prediction.

    Adds HMM regime classification and GBM profit prediction on top of
    the standard scalp adapter filters.
    """

    def __init__(
        self,
        hmm_path: str = "models/crypto_regime_hmm.pkl",
        gbm_path: str = "models/crypto_regime_gbm.txt",
        cal_path: str = "models/crypto_regime_cal.pkl",
        gbm_threshold: float = 0.20,
        feature_window_sec: float = 60.0,
        **scalp_kwargs,
    ):
        """Initialize HMM → GBM adapter.

        Args:
            hmm_path: Path to trained HMM model
            gbm_path: Path to trained GBM model
            cal_path: Path to calibration model
            gbm_threshold: Minimum P(profit) to trade (default: 0.20)
            feature_window_sec: Window for feature extraction (default: 60s)
            **scalp_kwargs: Additional args for CryptoScalpAdapter
        """
        super().__init__(**scalp_kwargs)

        self.gbm_threshold = gbm_threshold
        self.feature_window_sec = feature_window_sec

        # Load models
        self.hmm: Optional[HMMFeatureExtractor] = None
        self.gbm: Optional[GBMTrainer] = None

        if Path(hmm_path).exists():
            self.hmm = HMMFeatureExtractor.load(hmm_path)
            print(f"[HMM→GBM] Loaded HMM from {hmm_path} ({self.hmm.n_states} states)")
        else:
            print(f"[HMM→GBM] ⚠️  HMM not found at {hmm_path}, will skip")

        if Path(gbm_path).exists():
            cal = cal_path if Path(cal_path).exists() else None
            self.gbm = GBMTrainer.load(gbm_path, cal)
            print(f"[HMM→GBM] Loaded GBM from {gbm_path}")
        else:
            print(f"[HMM→GBM] ⚠️  GBM not found at {gbm_path}, will skip")

        # Feature buffers for HMM (need sequences for state inference)
        # Key: source (e.g., "binance")
        # Value: deque of (ts, features) tuples
        self._feature_buffers: Dict[str, deque] = {}
        self._normalization_stats: Optional[Dict[str, Tuple[float, float]]] = None

        # Stats
        self.signals_filtered_gbm = 0
        self.gbm_predictions: List[float] = []

    def on_start(self) -> None:
        super().on_start()
        self._feature_buffers.clear()
        self._normalization_stats = None
        self.signals_filtered_gbm = 0
        self.gbm_predictions = []

    def _extract_features(
        self,
        ctx: dict,
        source: str,
    ) -> Optional[np.ndarray]:
        """Extract features from context for a given source.

        Returns:
            Array of [net_move, osc_ratio, volume] (always 3 features to match HMM)

        Note:
            L2 features (spread_bps, orderflow) are available in some datasets but
            ignored here because the HMM was trained on 3 features only.
            To use 5 features, retrain HMM and GBM on L2 data.
        """
        spot = ctx.get("spot", {}).get(source, {})
        regime = ctx.get("regime", {}).get(source, {})

        # Net move (from spot delta)
        net_move = spot.get("delta")
        if net_move is None:
            return None

        # Oscillation ratio (from regime)
        osc_ratio = regime.get("oscillation_ratio") if regime else None
        if osc_ratio is None:
            return None

        # Clip osc_ratio to max 100 (same as training)
        osc_ratio = min(100.0, osc_ratio)

        # Volume
        volume = spot.get("volume", 0.0)

        # Always return 3 features to match trained HMM model
        return np.array([net_move, osc_ratio, volume])

    def _normalize_features(self, features: np.ndarray) -> np.ndarray:
        """Normalize features to mean=0, std=1.

        Uses running mean/std statistics updated during backtest.
        """
        if self._normalization_stats is None:
            # Initialize with feature values (bootstrap)
            self._normalization_stats = {
                i: (features[i], 1.0) for i in range(len(features))
            }
            return features  # No normalization on first call

        # Normalize using current stats (handle dynamic feature count)
        normalized = np.zeros_like(features)
        for i in range(len(features)):
            # Initialize missing features on first encounter
            if i not in self._normalization_stats:
                self._normalization_stats[i] = (features[i], 1.0)
            mean, std = self._normalization_stats[i]
            normalized[i] = (features[i] - mean) / max(std, 1e-6)

        # Update running stats (exponential moving average)
        alpha = 0.01  # Smoothing factor
        for i in range(len(features)):
            if i in self._normalization_stats:
                old_mean, old_std = self._normalization_stats[i]
                new_mean = alpha * features[i] + (1 - alpha) * old_mean
                new_var = alpha * (features[i] - new_mean) ** 2 + (1 - alpha) * old_std ** 2
                new_std = np.sqrt(new_var)
                self._normalization_stats[i] = (new_mean, new_std)

        return normalized

    def _get_hmm_state_posteriors(
        self,
        features: np.ndarray,
        source: str,
        ts: float,
    ) -> Optional[np.ndarray]:
        """Get HMM state posteriors for current window.

        Args:
            features: Normalized feature array (not used - features come from buffer)
            source: Exchange source
            ts: Current timestamp

        Returns:
            Array of state posteriors (shape: n_states,) or None

        Note:
            The buffer is populated in evaluate() on every frame, so we just
            read from it here rather than appending again.
        """
        if self.hmm is None:
            return None

        # Buffer should already be populated by evaluate()
        if source not in self._feature_buffers:
            return None

        # Need at least 12 windows (60s / 5s = 12 for 60s window)
        if len(self._feature_buffers[source]) < 12:
            return None

        # Build sequence (last 60 seconds)
        cutoff = ts - self.feature_window_sec
        sequence = [
            feat for t, feat in self._feature_buffers[source]
            if t >= cutoff
        ]

        if len(sequence) < 5:  # Need at least 5 windows
            return None

        sequence_array = np.array(sequence)

        # Match feature count to HMM's expected dimensions
        # HMM was trained on 3 features, but dataset may have 5 (with L2 data)
        hmm_n_features = getattr(self.hmm.model, 'n_features', 3)
        original_feature_count = sequence_array.shape[1]
        if original_feature_count > hmm_n_features:
            # Use only first N features to match HMM
            # Order: net_move, osc_ratio, volume, [spread_bps, orderflow_imbalance]
            sequence_array = sequence_array[:, :hmm_n_features]
            # Only print once
            if not hasattr(self, '_feature_trim_logged'):
                print(f"[HMM→GBM] Trimmed features from {original_feature_count} to {hmm_n_features} to match HMM")
                self._feature_trim_logged = True

        # Get HMM state posteriors for last window
        posteriors = self.hmm.predict_proba(sequence_array)
        return posteriors[-1]  # Return posteriors for most recent window

    def _get_gbm_profit_prediction(
        self,
        raw_features: np.ndarray,
        hmm_states: np.ndarray,
    ) -> Optional[float]:
        """Get GBM profit probability prediction.

        Args:
            raw_features: Raw (normalized) features
            hmm_states: HMM state posteriors

        Returns:
            Profit probability [0, 1] or None
        """
        if self.gbm is None or hmm_states is None:
            return None

        # Trim raw features to match HMM feature count
        # GBM was trained on [raw_features, hmm_states] where raw_features matched HMM
        hmm_n_features = getattr(self.hmm.model, 'n_features', 3)
        if len(raw_features) > hmm_n_features:
            raw_features = raw_features[:hmm_n_features]

        # Concatenate features: [raw features, hmm state posteriors]
        combined = np.concatenate([raw_features, hmm_states])

        # GBM prediction
        profit_prob = self.gbm.predict(
            combined.reshape(1, -1),
            calibrate=True
        )[0]

        return float(profit_prob)

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        """Evaluate frame and generate signals with HMM → GBM filtering.

        Extends parent evaluate() by adding GBM profit prediction filter.
        """
        # CRITICAL FIX: Update feature buffer on EVERY frame, not just signals
        # This ensures we have 60s of history when a signal occurs
        ctx = frame.context
        ts = ctx["ts"]
        source = self._signal_feed if self._signal_feed != "all" else "binance"

        # Extract and buffer features on every frame
        raw_features = self._extract_features(ctx, source)
        if raw_features is not None:
            normalized_features = self._normalize_features(raw_features)
            # Add to buffer (this populates the buffer even when no signal)
            if source not in self._feature_buffers:
                self._feature_buffers[source] = deque(maxlen=100)
            self._feature_buffers[source].append((ts, normalized_features))

        # Get signals from parent (includes all standard filters)
        signals = super().evaluate(frame)

        # If parent filtered it out, we're done
        if not signals:
            return signals

        # Check if this is an entry signal (BID = entry)
        entry_signals = [s for s in signals if s.side == "BID"]
        if not entry_signals:
            return signals  # Exit signal, pass through

        # For entry signals, apply HMM → GBM filter
        # Features already extracted and normalized above
        if raw_features is None:
            return []  # Can't compute features, filter out

        # Get HMM state posteriors (uses pre-populated buffer)
        hmm_states = self._get_hmm_state_posteriors(normalized_features, source, ts)

        # Get GBM profit prediction
        profit_prob = self._get_gbm_profit_prediction(normalized_features, hmm_states)

        if profit_prob is not None:
            self.gbm_predictions.append(profit_prob)

            # Filter by GBM threshold
            if profit_prob < self.gbm_threshold:
                self.signals_filtered_gbm += 1

                # CRITICAL: Remove position from parent's tracking
                # Parent already recorded position in super().evaluate(), undo it
                for signal in entry_signals:
                    ticker = signal.ticker
                    if ticker in self._positions:
                        del self._positions[ticker]
                        self.entries -= 1  # Undo parent's counter increment

                # DEBUG
                if not hasattr(self, '_filter_logged'):
                    print(f"[HMM→GBM] Filtering signal: P(profit)={profit_prob:.3f} < threshold={self.gbm_threshold}")
                    self._filter_logged = True
                return []  # Filter out low profit probability

            # Add GBM metadata to signal
            for signal in entry_signals:
                if signal.metadata is None:
                    signal.metadata = {}
                signal.metadata["gbm_profit_prob"] = profit_prob
                if hmm_states is not None:
                    signal.metadata["hmm_states"] = hmm_states.tolist()
                # DEBUG
                if not hasattr(self, '_pass_logged'):
                    print(f"[HMM→GBM] Passing signal: P(profit)={profit_prob:.3f} >= threshold={self.gbm_threshold}")
                    self._pass_logged = True

        return signals

    def summary_stats(self) -> dict:
        """Get summary statistics including GBM metrics."""
        stats = {
            "signals_detected": self.signals_detected,
            "signals_filtered_regime": self.signals_filtered_regime,
            "signals_filtered_gbm": self.signals_filtered_gbm,
            "signals_filtered_other": self.signals_filtered_other,
            "entries": self.entries,
            "exits": self.exits,
        }

        if self.gbm_predictions:
            stats["gbm_mean_profit_prob"] = float(np.mean(self.gbm_predictions))
            stats["gbm_median_profit_prob"] = float(np.median(self.gbm_predictions))
            stats["gbm_predictions_count"] = len(self.gbm_predictions)

        return stats
