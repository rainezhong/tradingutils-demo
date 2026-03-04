#!/usr/bin/env python3
"""Backtest full HMM → GBM pipeline.

Tests the complete hybrid model:
  1. HMM classifies market regime
  2. GBM predicts trade profitability
  3. Trade only if GBM P(profit) > threshold

Usage:
    python3 scripts/backtest_hmm_gbm.py --db data/btc_ob_48h.db
    python3 scripts/backtest_hmm_gbm.py --db data/btc_ob_48h.db --gbm-threshold 0.6
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


class HMMGBMBacktest:
    """Backtest HMM → GBM hybrid model."""

    def __init__(
        self,
        db_path: str,
        hmm_path: str,
        gbm_path: str,
        cal_path: Optional[str] = None,
    ):
        self.db_path = db_path

        # Load models
        print("Loading models...")
        self.hmm = HMMFeatureExtractor.load(hmm_path)
        self.gbm = GBMTrainer.load(gbm_path, cal_path)
        print(f"  HMM: {self.hmm.n_states} states")
        print(f"  GBM: loaded")
        print()

        # Extract features
        print("Extracting features...")
        self.extractor = FeatureExtractor(db_path, window_sec=5.0)
        self.windows = self.extractor.extract_windows(min_trades_per_window=5)
        print(f"  Extracted {len(self.windows)} windows")

        # Precompute HMM states and GBM predictions
        print("\nPrecomputing HMM states and GBM predictions...")
        self._precompute_predictions()

    def _precompute_predictions(self):
        """Compute HMM states and GBM predictions for all windows."""
        # Get HMM states
        sequences = self.extractor.extract_sequences(normalize=True)

        all_posteriors = []
        for seq in sequences:
            posteriors = self.hmm.predict_proba(seq)
            all_posteriors.append(posteriors)

        all_posteriors = np.concatenate(all_posteriors, axis=0)

        # Handle mismatch
        if len(all_posteriors) != len(self.windows):
            print(f"  ⚠️  Mismatch: {len(all_posteriors)} posteriors vs {len(self.windows)} windows")
            print(f"      Using first {len(all_posteriors)} windows")
            self.windows = self.windows[:len(all_posteriors)]

        # Compute GBM features and predictions
        self.hmm_states = []
        self.gbm_predictions = []

        for i, window in enumerate(self.windows):
            # Raw features
            has_spread = window.spread_bps is not None
            raw_features = window.to_array(include_spread=has_spread)

            # HMM states
            state_probs = all_posteriors[i]
            self.hmm_states.append(state_probs)

            # Combined features for GBM
            combined = np.concatenate([raw_features, state_probs])

            # GBM prediction
            profit_prob = self.gbm.predict(
                combined.reshape(1, -1),
                calibrate=True
            )[0]

            self.gbm_predictions.append(profit_prob)

        print(f"  Computed {len(self.gbm_predictions)} GBM predictions")
        print(f"  Mean profit prob: {np.mean(self.gbm_predictions):.3f}")
        print(f"  Median profit prob: {np.median(self.gbm_predictions):.3f}")

    def _get_kalshi_price_at_time(self, ts: float) -> Optional[Tuple[float, int, float]]:
        """Get Kalshi market state at timestamp."""
        with sqlite3.connect(self.db_path) as conn:
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

    def _simulate_trade(self, window: WindowFeatures) -> Optional[float]:
        """Simulate trade and return net P&L in cents."""
        entry_ts = window.ts_start
        entry_data = self._get_kalshi_price_at_time(entry_ts)

        if entry_data is None:
            return None

        btc_price_entry, yes_mid_entry, floor_strike = entry_data

        if btc_price_entry > floor_strike:
            side = "YES"
            entry_price_cents = yes_mid_entry
        else:
            side = "NO"
            entry_price_cents = 100 - yes_mid_entry

        exit_ts = entry_ts + 20.0  # 20s exit
        exit_data = self._get_kalshi_price_at_time(exit_ts)

        if exit_data is None:
            return None

        btc_price_exit, yes_mid_exit, _ = exit_data

        if side == "YES":
            exit_price_cents = yes_mid_exit
        else:
            exit_price_cents = 100 - yes_mid_exit

        gross_pnl_cents = exit_price_cents - entry_price_cents

        if gross_pnl_cents > 0:
            fee_cents = gross_pnl_cents * 0.07
            net_pnl_cents = gross_pnl_cents - fee_cents
        else:
            net_pnl_cents = gross_pnl_cents

        return net_pnl_cents

    def backtest(
        self,
        gbm_threshold: float = 0.55,
        cooldown_sec: float = 15.0,
    ):
        """Run backtest with GBM profit threshold."""
        print("="*70)
        print(f"BACKTEST: HMM → GBM (profit_prob > {gbm_threshold})")
        print("="*70)
        print()

        trades = []
        signals = 0
        last_trade_ts = 0.0

        for i, window in enumerate(self.windows):
            profit_prob = self.gbm_predictions[i]

            # Filter: GBM profit probability
            if profit_prob < gbm_threshold:
                continue

            signals += 1

            # Cooldown
            if window.ts_start < last_trade_ts + cooldown_sec:
                continue

            # Simulate trade
            net_pnl_cents = self._simulate_trade(window)

            if net_pnl_cents is not None:
                trades.append({
                    'ts': window.ts_start,
                    'profit_prob': profit_prob,
                    'hmm_states': self.hmm_states[i],
                    'net_pnl_cents': net_pnl_cents,
                    'profitable': net_pnl_cents > 0,
                })
                last_trade_ts = window.ts_start

        # Compute metrics
        if not trades:
            print("❌ No trades executed")
            return

        total_trades = len(trades)
        wins = sum(1 for t in trades if t['profitable'])
        losses = total_trades - wins
        win_rate = wins / total_trades

        net_pnl_dollars = sum(t['net_pnl_cents'] for t in trades) / 100.0
        avg_pnl = net_pnl_dollars / total_trades

        print(f"Total signals:    {signals:,}")
        print(f"Total trades:     {total_trades:,}")
        print(f"Wins:             {wins:,} ({win_rate*100:.1f}%)")
        print(f"Losses:           {losses:,}")
        print(f"Net P&L:          ${net_pnl_dollars:.2f}")
        print(f"Avg P&L/trade:    ${avg_pnl:.4f}")
        print()

        # Analyze by profit probability buckets
        print("="*70)
        print("PERFORMANCE BY PROFIT PROBABILITY")
        print("="*70)
        print()

        buckets = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.00)]

        print(f"{'Prob Range':>15} {'Trades':>8} {'Win Rate':>10} {'Avg P&L':>12}")
        print("-"*70)

        for low, high in buckets:
            bucket_trades = [
                t for t in trades
                if low <= t['profit_prob'] < high
            ]

            if not bucket_trades:
                continue

            bucket_wins = sum(1 for t in bucket_trades if t['profitable'])
            bucket_wr = bucket_wins / len(bucket_trades)
            bucket_pnl = sum(t['net_pnl_cents'] for t in bucket_trades) / 100.0
            bucket_avg = bucket_pnl / len(bucket_trades)

            print(
                f"{low:.2f} - {high:.2f}  {len(bucket_trades):>8,} "
                f"{bucket_wr*100:>9.1f}% "
                f"${bucket_avg:>11.4f}"
            )

        return trades


def main():
    parser = argparse.ArgumentParser(
        description="Backtest HMM → GBM hybrid model"
    )
    parser.add_argument(
        "--db",
        required=True,
        help="Path to probe database"
    )
    parser.add_argument(
        "--hmm",
        default="models/crypto_regime_hmm.pkl",
        help="Path to HMM model"
    )
    parser.add_argument(
        "--gbm",
        default="models/crypto_regime_gbm.txt",
        help="Path to GBM model"
    )
    parser.add_argument(
        "--calibration",
        default="models/crypto_regime_cal.pkl",
        help="Path to calibration model"
    )
    parser.add_argument(
        "--gbm-threshold",
        type=float,
        default=0.55,
        help="Minimum profit probability to trade (default: 0.55)"
    )

    args = parser.parse_args()

    # Check files exist
    for path, name in [
        (args.hmm, "HMM"),
        (args.gbm, "GBM"),
    ]:
        if not Path(path).exists():
            print(f"❌ {name} model not found: {path}")
            print("\nTrain the models first:")
            print("  python3 scripts/train_crypto_regime_hmm.py --db", args.db)
            print("  python3 scripts/train_crypto_regime_gbm.py --db", args.db)
            sys.exit(1)

    # Run backtest
    engine = HMMGBMBacktest(
        db_path=args.db,
        hmm_path=args.hmm,
        gbm_path=args.gbm,
        cal_path=args.calibration if Path(args.calibration).exists() else None,
    )

    trades = engine.backtest(gbm_threshold=args.gbm_threshold)

    if trades:
        print("\n✅ Backtest complete!")
    else:
        print("\n⚠️  No trades executed (try lowering --gbm-threshold)")


if __name__ == "__main__":
    main()
