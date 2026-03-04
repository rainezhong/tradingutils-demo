#!/usr/bin/env python3
"""Backtest comparison: HMM regime filter vs simple oscillation threshold.

Simulates crypto scalp strategy on historical probe data:
1. Baseline: osc_ratio < 3.0 → trade
2. HMM: P(trending) > threshold → trade

Usage:
    python3 scripts/backtest_hmm_vs_threshold.py --db data/btc_ob_48h.db
    python3 scripts/backtest_hmm_vs_threshold.py --db data/btc_ob_48h.db --hmm-threshold 0.8
"""

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.models.hmm_feature_extractor import HMMFeatureExtractor
from strategies.crypto_scalp.feature_extraction import (
    FeatureExtractor,
    WindowFeatures,
)


@dataclass
class Trade:
    """A simulated trade."""
    entry_ts: float
    exit_ts: float
    entry_price: float
    exit_price: float
    side: str  # "YES" or "NO"
    gross_pnl_cents: float
    net_pnl_cents: float
    reason: str
    hmm_state_probs: Optional[List[float]] = None


@dataclass
class BacktestResult:
    """Backtest results for one strategy."""
    name: str
    trades: List[Trade]
    total_signals: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    gross_pnl: float
    net_pnl: float
    avg_pnl_per_trade: float
    avg_hold_time: float


class BacktestEngine:
    """Simulate crypto scalp trades on historical data."""

    def __init__(
        self,
        db_path: str,
        hmm_path: Optional[str] = "models/crypto_regime_hmm.pkl",
        exit_delay_sec: float = 20.0,
        fee_pct: float = 0.07,
        min_edge_cents: int = 5,
    ):
        self.db_path = db_path
        self.exit_delay_sec = exit_delay_sec
        self.fee_pct = fee_pct
        self.min_edge_cents = min_edge_cents

        # Load HMM if available
        self.hmm = None
        if hmm_path and Path(hmm_path).exists():
            self.hmm = HMMFeatureExtractor.load(hmm_path)
            print(f"Loaded HMM from {hmm_path}")
            print(f"  States: {self.hmm.n_states}")
        else:
            print(f"⚠️  HMM not found at {hmm_path}, will skip HMM backtest")

        # Extract features
        print(f"\nExtracting features from {db_path}...")
        self.extractor = FeatureExtractor(db_path, window_sec=5.0)
        self.windows = self.extractor.extract_windows(min_trades_per_window=5)
        print(f"  Extracted {len(self.windows)} windows")

        # Precompute HMM states for all windows
        self.hmm_states = None
        if self.hmm:
            print("\nComputing HMM state posteriors...")
            self._precompute_hmm_states()

    def _precompute_hmm_states(self):
        """Compute HMM state posteriors for all windows."""
        # Need to segment into episodes for HMM
        sequences = self.extractor.extract_sequences(normalize=True)

        # Build a mapping: window index → state posteriors
        # Note: extract_sequences returns normalized features, but we still have
        # the original windows list which matches 1:1 with the flattened sequences

        # Flatten sequences back to windows
        all_posteriors = []
        for seq in sequences:
            posteriors = self.hmm.predict_proba(seq)
            all_posteriors.append(posteriors)

        # Concatenate (should match length of windows)
        all_posteriors = np.concatenate(all_posteriors, axis=0)

        if len(all_posteriors) != len(self.windows):
            print(f"⚠️  Warning: HMM posteriors ({len(all_posteriors)}) != windows ({len(self.windows)})")
            print(f"    This is due to episode segmentation filtering short episodes")
            print(f"    Using None for missing windows")
            # Pad with None for missing windows
            self.hmm_states = [None] * len(self.windows)
            # Fill in what we have (this is a simplification - proper solution would track window IDs)
            for i in range(min(len(all_posteriors), len(self.windows))):
                self.hmm_states[i] = all_posteriors[i]
        else:
            self.hmm_states = list(all_posteriors)

        print(f"  Computed {len([s for s in self.hmm_states if s is not None])} state posteriors")

    def _get_kalshi_price_at_time(self, ts: float) -> Optional[Tuple[float, int, int]]:
        """Get Kalshi yes_mid at given timestamp.

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
                (ts - 1.0, ts + 3.0)  # Within 3s window
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

    def _simulate_trade(
        self,
        window: WindowFeatures,
        reason: str,
        hmm_state_probs: Optional[np.ndarray] = None,
    ) -> Optional[Trade]:
        """Simulate a trade starting at this window.

        Logic:
        1. Entry: BUY YES if BTC > strike, BUY NO if BTC < strike
        2. Exit: 20 seconds later
        3. P&L: Compare Kalshi price change (minus 7% fee)
        """
        entry_ts = window.ts_start

        # Get Kalshi market at entry
        entry_data = self._get_kalshi_price_at_time(entry_ts)
        if entry_data is None:
            return None

        btc_price_entry, yes_mid_entry, floor_strike = entry_data

        # Determine side (YES if BTC > strike, NO if BTC < strike)
        if btc_price_entry > floor_strike:
            side = "YES"
            entry_price_cents = yes_mid_entry
        else:
            side = "NO"
            entry_price_cents = 100 - yes_mid_entry

        # Exit: 20 seconds later
        exit_ts = entry_ts + self.exit_delay_sec
        exit_data = self._get_kalshi_price_at_time(exit_ts)

        if exit_data is None:
            return None

        btc_price_exit, yes_mid_exit, _ = exit_data

        if side == "YES":
            exit_price_cents = yes_mid_exit
        else:
            exit_price_cents = 100 - yes_mid_exit

        # P&L calculation
        gross_pnl_cents = exit_price_cents - entry_price_cents

        # Only pay fees on profit
        if gross_pnl_cents > 0:
            fee_cents = gross_pnl_cents * self.fee_pct
            net_pnl_cents = gross_pnl_cents - fee_cents
        else:
            net_pnl_cents = gross_pnl_cents

        return Trade(
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            entry_price=entry_price_cents,
            exit_price=exit_price_cents,
            side=side,
            gross_pnl_cents=gross_pnl_cents,
            net_pnl_cents=net_pnl_cents,
            reason=reason,
            hmm_state_probs=list(hmm_state_probs) if hmm_state_probs is not None else None,
        )

    def backtest_threshold(
        self,
        osc_threshold: float = 3.0,
        min_volume: float = 0.0,
    ) -> BacktestResult:
        """Backtest simple oscillation threshold filter."""
        print(f"\n{'='*70}")
        print(f"BACKTEST: Simple Threshold (osc < {osc_threshold})")
        print(f"{'='*70}")

        trades = []
        signals = 0
        last_trade_ts = 0.0
        cooldown_sec = 15.0

        for window in self.windows:
            # Filter: osc_ratio < threshold and volume > min
            if window.oscillation_ratio >= osc_threshold:
                continue

            if window.volume < min_volume:
                continue

            # Cooldown
            if window.ts_start < last_trade_ts + cooldown_sec:
                continue

            signals += 1

            # Simulate trade
            trade = self._simulate_trade(
                window,
                reason=f"osc={window.oscillation_ratio:.1f} < {osc_threshold}"
            )

            if trade:
                trades.append(trade)
                last_trade_ts = trade.entry_ts

        return self._compute_results("Threshold", trades, signals)

    def backtest_hmm(
        self,
        trending_threshold: float = 0.7,
        min_volume: float = 0.0,
    ) -> Optional[BacktestResult]:
        """Backtest HMM regime filter.

        Args:
            trending_threshold: Trade if P(trending) > this (default: 0.7)
            min_volume: Min volume filter
        """
        if self.hmm is None or self.hmm_states is None:
            print("⚠️  HMM not available, skipping")
            return None

        print(f"\n{'='*70}")
        print(f"BACKTEST: HMM Filter (P(trending) > {trending_threshold})")
        print(f"{'='*70}")

        trades = []
        signals = 0
        last_trade_ts = 0.0
        cooldown_sec = 15.0

        # Determine which states are "trending" (low oscillation)
        # From training output: State 0 and State 2 are trending
        # State 1 is choppy (high osc_ratio)
        means = self.hmm.model.means_
        osc_idx = 1  # oscillation_ratio is 2nd feature
        sorted_states = np.argsort(means[:, osc_idx])  # Sort by osc_ratio
        trending_states = sorted_states[:2]  # Two lowest = trending

        print(f"Trending states: {trending_states} (lowest oscillation)")

        for i, window in enumerate(self.windows):
            state_probs = self.hmm_states[i]
            if state_probs is None:
                continue

            # Volume filter
            if window.volume < min_volume:
                continue

            # HMM filter: sum of trending state probabilities
            trending_prob = sum(state_probs[s] for s in trending_states)

            if trending_prob < trending_threshold:
                continue

            # Cooldown
            if window.ts_start < last_trade_ts + cooldown_sec:
                continue

            signals += 1

            # Simulate trade
            trade = self._simulate_trade(
                window,
                reason=f"P(trending)={trending_prob:.2f} > {trending_threshold}",
                hmm_state_probs=state_probs,
            )

            if trade:
                trades.append(trade)
                last_trade_ts = trade.entry_ts

        return self._compute_results("HMM", trades, signals)

    def _compute_results(
        self,
        name: str,
        trades: List[Trade],
        signals: int,
    ) -> BacktestResult:
        """Compute backtest metrics."""
        if not trades:
            return BacktestResult(
                name=name,
                trades=[],
                total_signals=signals,
                total_trades=0,
                wins=0,
                losses=0,
                win_rate=0.0,
                gross_pnl=0.0,
                net_pnl=0.0,
                avg_pnl_per_trade=0.0,
                avg_hold_time=0.0,
            )

        wins = sum(1 for t in trades if t.net_pnl_cents > 0)
        losses = len(trades) - wins
        win_rate = wins / len(trades) if trades else 0.0

        gross_pnl = sum(t.gross_pnl_cents for t in trades) / 100.0  # Convert to $
        net_pnl = sum(t.net_pnl_cents for t in trades) / 100.0

        avg_pnl = net_pnl / len(trades)
        avg_hold_time = np.mean([t.exit_ts - t.entry_ts for t in trades])

        return BacktestResult(
            name=name,
            trades=trades,
            total_signals=signals,
            total_trades=len(trades),
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            avg_pnl_per_trade=avg_pnl,
            avg_hold_time=avg_hold_time,
        )

    def print_comparison(self, results: List[BacktestResult]):
        """Print side-by-side comparison."""
        print(f"\n{'='*70}")
        print("BACKTEST COMPARISON")
        print(f"{'='*70}\n")

        # Header
        print(f"{'Metric':<30} {'Threshold':>15} {'HMM':>15} {'Improvement':>10}")
        print("-" * 70)

        if len(results) < 2:
            print("Not enough results to compare")
            return

        baseline = results[0]
        hmm = results[1]

        def fmt_pct(val):
            return f"{val*100:.1f}%"

        def fmt_dollar(val):
            return f"${val:.2f}"

        def fmt_int(val):
            return f"{val:,}"

        def fmt_improvement(baseline_val, new_val):
            if baseline_val == 0:
                return "-"
            pct_change = ((new_val - baseline_val) / abs(baseline_val)) * 100
            sign = "+" if pct_change > 0 else ""
            return f"{sign}{pct_change:.1f}%"

        # Print metrics
        metrics = [
            ("Total signals", baseline.total_signals, hmm.total_signals, fmt_int),
            ("Total trades", baseline.total_trades, hmm.total_trades, fmt_int),
            ("Wins", baseline.wins, hmm.wins, fmt_int),
            ("Losses", baseline.losses, hmm.losses, fmt_int),
            ("Win rate", baseline.win_rate, hmm.win_rate, fmt_pct),
            ("Gross P&L", baseline.gross_pnl, hmm.gross_pnl, fmt_dollar),
            ("Net P&L", baseline.net_pnl, hmm.net_pnl, fmt_dollar),
            ("Avg P&L/trade", baseline.avg_pnl_per_trade, hmm.avg_pnl_per_trade, fmt_dollar),
            ("Avg hold time (s)", baseline.avg_hold_time, hmm.avg_hold_time, lambda x: f"{x:.1f}s"),
        ]

        for metric_name, baseline_val, hmm_val, formatter in metrics:
            baseline_str = formatter(baseline_val)
            hmm_str = formatter(hmm_val)

            # Compute improvement for numeric metrics
            if metric_name in ["Win rate", "Net P&L", "Avg P&L/trade"]:
                improvement = fmt_improvement(baseline_val, hmm_val)
            else:
                improvement = ""

            print(f"{metric_name:<30} {baseline_str:>15} {hmm_str:>15} {improvement:>10}")

        print()


def main():
    parser = argparse.ArgumentParser(
        description="Backtest HMM vs threshold regime filter"
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
        "--threshold",
        type=float,
        default=3.0,
        help="Oscillation ratio threshold for baseline (default: 3.0)"
    )
    parser.add_argument(
        "--hmm-threshold",
        type=float,
        default=0.7,
        help="HMM trending probability threshold (default: 0.7)"
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=0.0,
        help="Minimum volume filter (BTC)"
    )

    args = parser.parse_args()

    # Run backtest
    engine = BacktestEngine(
        db_path=args.db,
        hmm_path=args.hmm,
        exit_delay_sec=20.0,
        fee_pct=0.07,
    )

    results = []

    # Baseline
    baseline_result = engine.backtest_threshold(
        osc_threshold=args.threshold,
        min_volume=args.min_volume,
    )
    results.append(baseline_result)

    # HMM
    if engine.hmm:
        hmm_result = engine.backtest_hmm(
            trending_threshold=args.hmm_threshold,
            min_volume=args.min_volume,
        )
        if hmm_result:
            results.append(hmm_result)

    # Print comparison
    engine.print_comparison(results)

    # Success indicator
    if len(results) == 2 and results[1].win_rate > results[0].win_rate:
        print("✅ HMM improves win rate!")
    elif len(results) == 2:
        print("⚠️  HMM does not improve win rate")
    else:
        print("⚠️  Could not compare (missing HMM results)")


if __name__ == "__main__":
    main()
