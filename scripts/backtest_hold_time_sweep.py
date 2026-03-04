#!/usr/bin/env python3
"""
Backtest crypto scalp strategy with different hold times to find optimal exit delay.

Based on lag analysis showing Kalshi catches up in 3 seconds, we expect:
- Short hold (2-4s): Best P&L (captures lag, exits before noise)
- Medium hold (10s): Worse (holding through noise)
- Long hold (20s+): Worst (current config, validated to lose money)
"""

import sys
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.backtesting.engine import BacktestEngine
from src.backtesting.adapters.scalp_adapter import CryptoScalpAdapter

def run_backtest_with_hold_time(hold_time_sec: float, db_path: str):
    """Run backtest with specific hold time"""

    # Create adapter with parameters (not config object)
    adapter = CryptoScalpAdapter(
        db_path=db_path,

        # Entry filters
        min_ttx_sec=120,
        max_ttx_sec=900,
        min_entry_price_cents=25,
        max_entry_price_cents=75,

        # EXIT TIMING - THE VARIABLE WE'RE TESTING
        exit_delay_sec=hold_time_sec,
        max_hold_sec=hold_time_sec + 10.0,

        # Position sizing
        contracts_per_trade=1,

        # Signal detection
        signal_feed="all",
        min_spot_move_usd=10.0,

        # Volume filters
        min_window_volume={
            "binance": 0.5,
            "coinbase": 0.3,
            "kraken": 0.1
        },
        require_multi_exchange_confirm=True,

        # Execution
        slippage_cents=1,
        cooldown_sec=15.0,

        # Pre-entry liquidity check
        min_entry_bid_depth=5,
        enable_entry_liquidity_check=True,

        # Stop-loss (keep enabled)
        stop_loss_cents=15,
        stop_loss_delay_sec=0.0,
        enable_stop_loss=True,

        # Regime filter (disable for clean test)
        regime_osc_threshold=0.0,
    )

    # Run backtest
    engine = BacktestEngine(adapter=adapter)
    results = engine.run()

    return results

def main():
    # Database path
    db_path = "data/btc_probe_20260227.db"

    # Hold times to test (seconds)
    hold_times = [1, 2, 3, 4, 5, 7, 10, 15, 20, 25, 30]

    print("="*80)
    print("HOLD TIME SWEEP BACKTEST")
    print("="*80)
    print(f"Database: {db_path}")
    print(f"Testing hold times: {hold_times}")
    print(f"Hypothesis: Optimal hold time = 2-4s (based on 3s lag)")
    print("="*80)
    print()

    results_list = []

    for hold_time in hold_times:
        print(f"\n{'='*80}")
        print(f"Testing hold_time = {hold_time}s")
        print('='*80)

        try:
            results = run_backtest_with_hold_time(hold_time, db_path)

            # Extract metrics
            metrics = {
                'hold_time': hold_time,
                'total_pnl': results.total_pnl,
                'num_trades': results.num_trades,
                'win_rate': results.win_rate,
                'avg_pnl': results.avg_pnl,
                'avg_winner': results.avg_winner,
                'avg_loser': results.avg_loser,
                'max_win': results.max_win,
                'max_loss': results.max_loss,
                'sharpe': results.sharpe if hasattr(results, 'sharpe') else 0,
            }

            results_list.append(metrics)

            # Print summary
            print(f"\nResults for hold_time={hold_time}s:")
            print(f"  Total P&L: {results.total_pnl:+.2f}¢")
            print(f"  Trades: {results.num_trades}")
            print(f"  Win rate: {results.win_rate:.1%}")
            print(f"  Avg P&L: {results.avg_pnl:+.2f}¢")
            print(f"  Avg winner: {results.avg_winner:+.2f}¢")
            print(f"  Avg loser: {results.avg_loser:+.2f}¢")
            print(f"  Max win: {results.max_win:+.2f}¢")
            print(f"  Max loss: {results.max_loss:+.2f}¢")

        except Exception as e:
            print(f"ERROR: {e}")
            import traceback
            traceback.print_exc()
            continue

    # Create DataFrame
    df = pd.DataFrame(results_list)

    print("\n" + "="*80)
    print("SUMMARY TABLE")
    print("="*80)
    print(df.to_string(index=False))
    print()

    # Find optimal
    if len(df) > 0:
        best_idx = df['total_pnl'].idxmax()
        best_hold = df.loc[best_idx, 'hold_time']
        best_pnl = df.loc[best_idx, 'total_pnl']

        print("="*80)
        print(f"OPTIMAL HOLD TIME: {best_hold}s (P&L: {best_pnl:+.2f}¢)")
        print("="*80)

        # Save results
        df.to_csv('hold_time_sweep_results.csv', index=False)
        print(f"\nResults saved to: hold_time_sweep_results.csv")

        # Plot results
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))

        # Plot 1: Total P&L vs Hold Time
        ax1 = axes[0, 0]
        ax1.plot(df['hold_time'], df['total_pnl'], marker='o', linewidth=2, markersize=8)
        ax1.axvline(best_hold, color='red', linestyle='--', linewidth=2,
                    label=f'Optimal: {best_hold}s')
        ax1.axhline(0, color='black', linestyle='-', linewidth=0.5)
        ax1.axvspan(2, 4, alpha=0.2, color='green', label='Expected optimal (2-4s)')
        ax1.set_xlabel('Hold Time (seconds)', fontsize=12)
        ax1.set_ylabel('Total P&L (¢)', fontsize=12)
        ax1.set_title('Total P&L vs Hold Time', fontsize=14, fontweight='bold')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        # Plot 2: Win Rate vs Hold Time
        ax2 = axes[0, 1]
        ax2.plot(df['hold_time'], df['win_rate'] * 100, marker='o',
                 linewidth=2, markersize=8, color='green')
        ax2.axvline(best_hold, color='red', linestyle='--', linewidth=2)
        ax2.axhline(50, color='gray', linestyle='--', linewidth=1, alpha=0.5, label='50%')
        ax2.set_xlabel('Hold Time (seconds)', fontsize=12)
        ax2.set_ylabel('Win Rate (%)', fontsize=12)
        ax2.set_title('Win Rate vs Hold Time', fontsize=14, fontweight='bold')
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        # Plot 3: Average P&L vs Hold Time
        ax3 = axes[1, 0]
        ax3.plot(df['hold_time'], df['avg_pnl'], marker='o', linewidth=2, markersize=8)
        ax3.axvline(best_hold, color='red', linestyle='--', linewidth=2)
        ax3.axhline(0, color='black', linestyle='-', linewidth=0.5)
        ax3.set_xlabel('Hold Time (seconds)', fontsize=12)
        ax3.set_ylabel('Average P&L per Trade (¢)', fontsize=12)
        ax3.set_title('Average P&L vs Hold Time', fontsize=14, fontweight='bold')
        ax3.grid(True, alpha=0.3)

        # Plot 4: Risk-Reward (Avg Winner vs Avg Loser)
        ax4 = axes[1, 1]
        width = 0.35
        x = np.arange(len(df))
        ax4.bar(x - width/2, df['avg_winner'], width, label='Avg Winner', color='green', alpha=0.7)
        ax4.bar(x + width/2, df['avg_loser'], width, label='Avg Loser', color='red', alpha=0.7)
        ax4.set_xlabel('Hold Time (seconds)', fontsize=12)
        ax4.set_ylabel('P&L (¢)', fontsize=12)
        ax4.set_title('Risk-Reward by Hold Time', fontsize=14, fontweight='bold')
        ax4.set_xticks(x)
        ax4.set_xticklabels(df['hold_time'])
        ax4.legend()
        ax4.grid(True, alpha=0.3, axis='y')
        ax4.axhline(0, color='black', linewidth=0.5)

        plt.tight_layout()
        plt.savefig('hold_time_sweep.png', dpi=100, bbox_inches='tight')
        print(f"Plot saved to: hold_time_sweep.png")
        plt.show()

        # Analysis
        print("\n" + "="*80)
        print("ANALYSIS")
        print("="*80)

        # Check if optimal is in expected range
        if 2 <= best_hold <= 4:
            print(f"✓ HYPOTHESIS CONFIRMED: Optimal hold time ({best_hold}s) is in expected range (2-4s)")
            print(f"  This validates the lag analysis showing Kalshi catches up in 3 seconds.")
        else:
            print(f"✗ HYPOTHESIS REJECTED: Optimal hold time ({best_hold}s) is outside expected range (2-4s)")
            print(f"  This suggests:")
            if best_hold < 2:
                print(f"    - Kalshi catches up faster than 3s, OR")
                print(f"    - There's immediate mean reversion after entry")
            else:
                print(f"    - Kalshi takes longer to catch up, OR")
                print(f"    - There's momentum overshoot we should capture")

        # Compare current (20s) to optimal
        if 20 in df['hold_time'].values:
            current_pnl = df[df['hold_time'] == 20]['total_pnl'].values[0]
            improvement = best_pnl - current_pnl
            improvement_pct = (improvement / abs(current_pnl) * 100) if current_pnl != 0 else float('inf')

            print(f"\nCurrent config (20s hold): {current_pnl:+.2f}¢")
            print(f"Optimal config ({best_hold}s hold): {best_pnl:+.2f}¢")
            print(f"Improvement: {improvement:+.2f}¢ ({improvement_pct:+.1f}%)")

        print("="*80)

if __name__ == "__main__":
    main()
