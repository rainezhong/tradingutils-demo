#!/bin/bash
# Backtest crypto scalp with different hold times using the CLI

DB="data/btc_probe_20260227.db"
HOLD_TIMES="1 2 3 4 5 7 10 15 20 25 30"

echo "================================================================================"
echo "HOLD TIME SWEEP BACKTEST (using CLI)"
echo "================================================================================"
echo "Database: $DB"
echo "Testing hold times: $HOLD_TIMES"
echo "Hypothesis: Optimal hold time = 2-4s (based on 3s lag)"
echo "================================================================================"
echo ""

# Create results file
RESULTS_FILE="hold_time_sweep_results.csv"
echo "hold_time,total_pnl,num_trades,win_rate,avg_pnl,max_win,max_loss" > $RESULTS_FILE

for HOLD in $HOLD_TIMES; do
    echo ""
    echo "================================================================================"
    echo "Testing hold_time = ${HOLD}s"
    echo "================================================================================"

    # Run backtest with this hold time
    # We need to temporarily modify the config or pass parameters
    # For now, let's just document what we need to do

    python3 main.py backtest crypto-scalp \
        --db "$DB" \
        --exit-delay "$HOLD" \
        --max-hold "$((HOLD + 10))" \
        2>&1 | tee "hold_${HOLD}s.log"

    # Extract results from output
    # (This would need parsing logic)
done

echo ""
echo "================================================================================"
echo "Results saved to: $RESULTS_FILE"
echo "================================================================================"
