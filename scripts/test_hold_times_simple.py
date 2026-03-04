#!/usr/bin/env python3
"""Simple hold time sweep using CLI commands"""

import subprocess
import re
import pandas as pd
import matplotlib.pyplot as plt

hold_times = [2, 3, 4, 5, 7, 10, 15, 20, 25, 30]
db = "data/btc_probe_20260227.db"

results = []

for hold in hold_times:
    print(f"\n{'='*80}")
    print(f"Testing hold_time = {hold}s")
    print('='*80)

    cmd = [
        "python3", "main.py", "backtest", "crypto-scalp",
        "--db", db,
        "--fixed-exit", str(hold),
        "--max-hold", str(hold + 10),
        "--spot-move", "10",
        "--regime-threshold", "0",  # Disable regime filter
    ]

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        print(output)

        # Parse results from output
        # Look for lines like "Total P&L: +123.45"
        pnl_match = re.search(r'Total P&L:\s*([\+\-]?\d+\.?\d*)', output)
        trades_match = re.search(r'Total trades:\s*(\d+)', output)
        win_rate_match = re.search(r'Win rate:\s*(\d+\.?\d*)%', output)

        if pnl_match and trades_match:
            results.append({
                'hold_time': hold,
                'total_pnl': float(pnl_match.group(1)),
                'num_trades': int(trades_match.group(1)),
                'win_rate': float(win_rate_match.group(1)) if win_rate_match else 0
            })

    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e}")
        print(e.output)

# Display results
if results:
    df = pd.DataFrame(results)
    print("\n" + "="*80)
    print("RESULTS")
    print("="*80)
    print(df.to_string(index=False))

    # Find optimal
    best_idx = df['total_pnl'].idxmax()
    best_hold = df.loc[best_idx, 'hold_time']
    best_pnl = df.loc[best_idx, 'total_pnl']

    print(f"\nOPTIMAL: {best_hold}s hold time (P&L: {best_pnl:+.2f}¢)")

    # Plot
    plt.figure(figsize=(10, 6))
    plt.plot(df['hold_time'], df['total_pnl'], marker='o', linewidth=2, markersize=8)
    plt.axvline(best_hold, color='red', linestyle='--', label=f'Optimal: {best_hold}s')
    plt.axhline(0, color='black', linestyle='-', linewidth=0.5)
    plt.axvspan(2, 4, alpha=0.2, color='green', label='Expected (2-4s)')
    plt.xlabel('Hold Time (s)')
    plt.ylabel('Total P&L (¢)')
    plt.title('Hold Time Optimization')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.savefig('hold_time_sweep.png', dpi=100, bbox_inches='tight')
    print(f"\nPlot saved: hold_time_sweep.png")
    plt.show()
