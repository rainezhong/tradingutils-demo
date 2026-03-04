#!/usr/bin/env python3
"""Comprehensive blowout strategy backtest across all NBA recordings."""

import json
import glob
from pathlib import Path
from src.backtesting.engine import BacktestEngine, BacktestConfig
from src.backtesting.adapters.nba_adapter import NBADataFeed, BlowoutAdapter

def main():
    # Find all NBA game recordings
    recordings = glob.glob("data/recordings/*.json")
    recordings = [r for r in recordings if "crypto" not in r and "synthetic" not in r]

    print(f"Found {len(recordings)} NBA game recordings")
    print("=" * 80)

    # Run backtest on each game
    config = BacktestConfig(
        initial_bankroll=100.0,
        fill_probability=1.0,
        slippage=0.03,
    )

    all_fills = []
    all_settlements = {}
    total_pnl = 0.0
    total_fees = 0.0
    games_with_signals = 0
    total_signals = 0
    bankroll_curves = []
    winners = 0
    losers = 0

    for i, recording in enumerate(sorted(recordings), 1):
        try:
            feed = NBADataFeed(recording)
            adapter = BlowoutAdapter()
            engine = BacktestEngine(config)
            result = engine.run(feed, adapter, verbose=False)

            if result.fills:
                games_with_signals += 1
                total_signals += len(result.fills)
                game_pnl = result.metrics.net_pnl
                game_fees = result.metrics.total_fees
                total_pnl += game_pnl
                total_fees += game_fees
                all_fills.extend(result.fills)
                all_settlements.update(result.settlements)
                bankroll_curves.append(result.bankroll_curve)
                winners += result.metrics.winning_fills
                losers += result.metrics.losing_fills

                print(f"[{i:3d}/{len(recordings)}] {Path(recording).name:50s} - "
                      f"{len(result.fills):2d} trades, "
                      f"PnL: ${game_pnl:+7.2f}, "
                      f"Fees: ${game_fees:6.2f}")
        except Exception as e:
            print(f"[{i:3d}/{len(recordings)}] {Path(recording).name:50s} - ERROR: {e}")

    print("=" * 80)
    print("\nAGGREGATED RESULTS:")
    print(f"  Total games:           {len(recordings)}")
    print(f"  Games with signals:    {games_with_signals}")
    print(f"  Total signals:         {total_signals}")
    print(f"  Total fills:           {len(all_fills)}")
    print(f"  Total PnL:             ${total_pnl:+.2f}")
    print(f"  Total fees:            ${total_fees:.2f}")
    print(f"  Net PnL:               ${total_pnl - total_fees:+.2f}")

    if all_fills:
        print(f"  Winners:               {winners}")
        print(f"  Losers:                {losers}")
        if winners + losers > 0:
            print(f"  Win rate:              {winners/(winners+losers)*100:.1f}%")
        print(f"  Avg PnL per trade:     ${total_pnl/len(all_fills):+.4f}")

        if len(all_fills) >= 10:
            print("\n" + "=" * 80)
            print("RUNNING VALIDATION SUITE")
            print("=" * 80)

            from src.backtesting.validation import (
                ExtendedMetrics,
                MonteCarloConfig,
                MonteCarloMode,
                BootstrapConfig,
                PermutationConfig,
                run_validation_suite,
            )

            # Create a mock result object with aggregated data
            # Need to reconstruct bankroll curve from fills and settlements
            from src.backtesting.portfolio import PositionTracker
            from src.backtesting.metrics import BacktestMetrics

            tracker = PositionTracker(initial_bankroll=100.0)
            for fill in all_fills:
                tracker.process_fill(fill)
            tracker.settle(all_settlements)

            # Create metrics from tracker
            metrics = BacktestMetrics(
                total_frames=sum(1 for _ in recordings),
                total_signals=total_signals,
                total_fills=len(all_fills),
                initial_bankroll=100.0,
                final_bankroll=tracker.bankroll,
                net_pnl=total_pnl,
                return_pct=total_pnl / 100.0 * 100,
                total_fees=total_fees,
                max_drawdown_pct=tracker._max_drawdown * 100,
                peak_bankroll=tracker._peak_bankroll,
                winning_fills=winners,
                losing_fills=losers,
                win_rate_pct=winners/(winners+losers)*100 if (winners+losers) > 0 else 0.0,
            )

            class AggregatedResult:
                def __init__(self, fills, settlements, tracker, metrics):
                    self.fills = fills
                    self.settlements = settlements
                    self.bankroll_curve = tracker.bankroll_curve
                    self.metrics = metrics

            agg_result = AggregatedResult(all_fills, all_settlements, tracker, metrics)

            # Extended metrics
            ext = ExtendedMetrics.compute(
                agg_result.fills,
                agg_result.settlements,
                agg_result.bankroll_curve
            )
            print("\n" + ext.report())

            # Full validation suite
            mc_config = MonteCarloConfig(
                n_simulations=10000,
                mode=MonteCarloMode.SEQUENCE,
                seed=42
            )
            bs_config = BootstrapConfig(
                n_samples=10000,
                seed=42
            )
            perm_config = PermutationConfig(
                n_permutations=10000,
                seed=42
            )

            suite_result = run_validation_suite(
                agg_result,
                run_extended=False,  # Already printed above
                run_monte_carlo=True,
                run_bootstrap=True,
                run_permutation=True,
                mc_config=mc_config,
                bs_config=bs_config,
                perm_config=perm_config,
            )

            print("\n" + suite_result.report())

if __name__ == "__main__":
    main()
