#!/bin/bash
# Test script to compare baseline vs max_momentum filter
# Usage: ./scripts/run_test_comparison.sh [duration_minutes]

set -e

DURATION=${1:-30}  # Default 30 minutes
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_DIR="logs/test_${TIMESTAMP}"

mkdir -p "$LOG_DIR"

echo "=========================================="
echo "Max Momentum Filter Test"
echo "=========================================="
echo "Duration: ${DURATION} minutes"
echo "Timestamp: ${TIMESTAMP}"
echo "Log directory: ${LOG_DIR}"
echo ""

# Helper function to extract metrics from logs
extract_metrics() {
    local logfile=$1
    local label=$2

    echo "=== ${label} ==="

    # Count total signals
    signals=$(grep -c "Signal detected" "$logfile" 2>/dev/null || echo "0")
    echo "  Signals detected: ${signals}"

    # Count momentum spike filters (only in test)
    if grep -q "MOMENTUM SPIKE FILTER" "$logfile" 2>/dev/null; then
        filtered=$(grep -c "MOMENTUM SPIKE FILTER" "$logfile" || echo "0")
        echo "  Ultra-high momentum filtered: ${filtered}"
        echo "  Filter rate: $(awk "BEGIN {printf \"%.1f\", (${filtered}/(${signals}+${filtered}))*100}")%"
    fi

    # Count trades
    trades=$(grep -c "Placing entry order" "$logfile" 2>/dev/null || echo "0")
    echo "  Trades attempted: ${trades}"

    # Count wins/losses (if available)
    wins=$(grep -c "EXIT.*profit" "$logfile" 2>/dev/null || echo "0")
    losses=$(grep -c "EXIT.*loss" "$logfile" 2>/dev/null || echo "0")

    if [ $((wins + losses)) -gt 0 ]; then
        wr=$(awk "BEGIN {printf \"%.1f\", (${wins}/(${wins}+${losses}))*100}")
        echo "  Wins: ${wins}, Losses: ${losses}"
        echo "  Win rate: ${wr}%"
    fi

    echo ""
}

echo "This test will:"
echo "1. Run baseline (no max momentum filter) for ${DURATION} minutes"
echo "2. Run test (max_momentum=7.0) for ${DURATION} minutes"
echo "3. Compare the results"
echo ""
echo "Press Ctrl+C to cancel, or Enter to continue..."
read

# Note: Since we can't run both simultaneously, we'll create instructions instead
cat > "${LOG_DIR}/instructions.txt" << 'EOF'
TEST PROTOCOL
=============

This test compares baseline vs max_momentum_ratio=7.0 filter.

STEP 1: Run Baseline
--------------------
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_paper.yaml

Let it run for 30-60 minutes, then stop with Ctrl+C.

Watch for:
- Total signals detected
- Trades executed
- Win/loss ratio

STEP 2: Edit Config
-------------------
Open strategies/configs/crypto_scalp_paper.yaml and add:

  max_momentum_ratio: 7.0

(Or use the pre-made test config)

STEP 3: Run Test
----------------
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_test.yaml

Let it run for the same duration (30-60 minutes).

Watch for:
- "MOMENTUM SPIKE FILTER" messages in logs
- Total signals detected (should be 10-15% fewer)
- Trades executed
- Win/loss ratio (should improve)

STEP 4: Compare Results
------------------------
Compare the logs:

Baseline:
  - Signals/hour: ?
  - Win rate: ?
  - Avg profit: ?

Test (max_momentum=7.0):
  - Signals/hour: ? (expect -10-15%)
  - Win rate: ? (expect +5-10pp improvement)
  - Avg profit: ? (expect higher)

If test shows improvement:
  ✅ Keep max_momentum_ratio=7.0
  ✅ Consider tightening to 5.0 for even better quality

If test shows no improvement or worse:
  ❌ Revert the change
  ❌ Investigate other factors (volume, concentration)
EOF

cat "${LOG_DIR}/instructions.txt"

echo ""
echo "=========================================="
echo "Quick Start (Recommended)"
echo "=========================================="
echo ""
echo "Run the test config with max_momentum filter:"
echo ""
echo "  python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_test.yaml"
echo ""
echo "Let it run for 30-60 minutes, then check logs for:"
echo ""
echo "  1. MOMENTUM SPIKE FILTER messages (shows it's working)"
echo "  2. Win rate improvement"
echo "  3. Fewer crazy spikes in the trades"
echo ""
echo "Compare to your previous runs to see if accuracy improved!"
echo ""
echo "Instructions saved to: ${LOG_DIR}/instructions.txt"
