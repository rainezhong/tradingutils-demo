#!/bin/bash
# Paper Trading Validation Script
# Purpose: Run crypto-scalp strategy in paper mode while recording probe data
# for HMM validation on fresh out-of-sample data (March 3+)

set -e

# Configuration
DURATION_SEC=${1:-14400}  # Default: 4 hours (14400 seconds)
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PROBE_DB="data/btc_paper_${TIMESTAMP}.db"
STRATEGY_LOG="logs/paper_trading_${TIMESTAMP}.log"
PROBE_LOG="logs/probe_${TIMESTAMP}.log"

echo "========================================================================"
echo "  PAPER TRADING VALIDATION - HMM Out-of-Sample Test"
echo "========================================================================"
echo "Duration: $((DURATION_SEC / 3600)) hours ($DURATION_SEC seconds)"
echo "Probe database: $PROBE_DB"
echo "Strategy log: $STRATEGY_LOG"
echo "Probe log: $PROBE_LOG"
echo ""
echo "This will:"
echo "  1. Run crypto-scalp strategy in paper mode"
echo "  2. Record BTC probe data (trades + orderbook) to database"
echo "  3. After completion, backtest with trained HMM model"
echo ""
echo "Press Ctrl+C to stop early (data will still be saved)"
echo "========================================================================"
echo ""

# Create logs directory
mkdir -p logs

# Start probe recording in background
echo "[$(date +'%H:%M:%S')] Starting probe recording..."
python3 scripts/btc_latency_probe.py \
    --duration $DURATION_SEC \
    --db "$PROBE_DB" \
    > "$PROBE_LOG" 2>&1 &
PROBE_PID=$!

# Wait 5 seconds for probe to initialize
sleep 5

# Check if probe is still running
if ! kill -0 $PROBE_PID 2>/dev/null; then
    echo "❌ Probe recording failed to start. Check $PROBE_LOG"
    exit 1
fi

echo "[$(date +'%H:%M:%S')] Probe recording started (PID: $PROBE_PID)"
echo ""

# Start paper trading strategy
echo "[$(date +'%H:%M:%S')] Starting paper trading strategy..."
python3 main.py run crypto-scalp \
    --dry-run \
    --config strategies/configs/crypto_scalp_paper.yaml \
    > "$STRATEGY_LOG" 2>&1 &
STRATEGY_PID=$!

# Wait 5 seconds for strategy to initialize
sleep 5

# Check if strategy is still running
if ! kill -0 $STRATEGY_PID 2>/dev/null; then
    echo "❌ Strategy failed to start. Check $STRATEGY_LOG"
    kill $PROBE_PID 2>/dev/null || true
    exit 1
fi

echo "[$(date +'%H:%M:%S')] Strategy started (PID: $STRATEGY_PID)"
echo ""

echo "========================================================================"
echo "  MONITORING (will run for $((DURATION_SEC / 3600)) hours)"
echo "========================================================================"
echo ""
echo "Live monitoring commands:"
echo "  Strategy: tail -f $STRATEGY_LOG"
echo "  Probe:    tail -f $PROBE_LOG"
echo ""
echo "To stop early: Ctrl+C (or kill $STRATEGY_PID $PROBE_PID)"
echo ""

# Trap Ctrl+C to clean up both processes
trap "echo ''; echo 'Stopping...'; kill $STRATEGY_PID $PROBE_PID 2>/dev/null || true; exit" INT TERM

# Monitor both processes
START_TIME=$(date +%s)
END_TIME=$((START_TIME + DURATION_SEC))

while true; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))
    REMAINING=$((END_TIME - CURRENT_TIME))

    # Check if both processes are still running
    if ! kill -0 $STRATEGY_PID 2>/dev/null; then
        echo ""
        echo "[$(date +'%H:%M:%S')] ⚠️  Strategy stopped unexpectedly"
        echo "Check logs: $STRATEGY_LOG"
        kill $PROBE_PID 2>/dev/null || true
        break
    fi

    if ! kill -0 $PROBE_PID 2>/dev/null; then
        echo ""
        echo "[$(date +'%H:%M:%S')] ⚠️  Probe recording stopped unexpectedly"
        echo "Check logs: $PROBE_LOG"
        kill $STRATEGY_PID 2>/dev/null || true
        break
    fi

    # Check if duration elapsed
    if [ $REMAINING -le 0 ]; then
        echo ""
        echo "[$(date +'%H:%M:%S')] ✅ Duration complete!"
        kill $STRATEGY_PID $PROBE_PID 2>/dev/null || true
        break
    fi

    # Print status every 5 minutes
    if [ $((ELAPSED % 300)) -eq 0 ] && [ $ELAPSED -gt 0 ]; then
        echo "[$(date +'%H:%M:%S')] Running... ($((ELAPSED / 60)) min elapsed, $((REMAINING / 60)) min remaining)"

        # Show recent strategy activity
        echo "  Latest signals: $(tail -5 $STRATEGY_LOG | grep -c 'SIGNAL' || echo 0)"

        # Show probe data size
        if [ -f "$PROBE_DB" ]; then
            DB_SIZE=$(du -h "$PROBE_DB" | cut -f1)
            echo "  Probe DB size: $DB_SIZE"
        fi
    fi

    sleep 10
done

echo ""
echo "========================================================================"
echo "  PAPER TRADING COMPLETE"
echo "========================================================================"
echo ""

# Show final stats
if [ -f "$PROBE_DB" ]; then
    echo "Probe database: $PROBE_DB"
    echo "Database size: $(du -h "$PROBE_DB" | cut -f1)"

    # Count trades
    TRADE_COUNT=$(sqlite3 "$PROBE_DB" "SELECT COUNT(*) FROM binance_trades" 2>/dev/null || echo "N/A")
    echo "Binance trades recorded: $TRADE_COUNT"

    # Time range
    TIME_RANGE=$(sqlite3 "$PROBE_DB" "SELECT datetime(min(ts), 'unixepoch') || ' to ' || datetime(max(ts), 'unixepoch') FROM binance_trades" 2>/dev/null || echo "N/A")
    echo "Time range: $TIME_RANGE"
fi

echo ""
echo "Strategy log: $STRATEGY_LOG"
echo ""

# Count signals and trades from strategy log
if [ -f "$STRATEGY_LOG" ]; then
    SIGNAL_COUNT=$(grep -c "SIGNAL" "$STRATEGY_LOG" 2>/dev/null || echo 0)
    ENTRY_COUNT=$(grep -c "ENTRY" "$STRATEGY_LOG" 2>/dev/null || echo 0)
    EXIT_COUNT=$(grep -c "EXIT" "$STRATEGY_LOG" 2>/dev/null || echo 0)

    echo "Strategy activity:"
    echo "  Signals detected: $SIGNAL_COUNT"
    echo "  Entries: $ENTRY_COUNT"
    echo "  Exits: $EXIT_COUNT"
fi

echo ""
echo "========================================================================"
echo "  NEXT STEP: Backtest with HMM"
echo "========================================================================"
echo ""
echo "Run this command to validate the HMM on fresh data:"
echo ""
echo "  python3 main.py backtest crypto-scalp-hmm-gbm \\"
echo "      --db $PROBE_DB \\"
echo "      --hmm models/crypto_regime_hmm_feb.pkl \\"
echo "      --gbm-threshold 0.20"
echo ""
echo "This will show if the HMM-trained regimes generalize to March 3 data."
echo ""
