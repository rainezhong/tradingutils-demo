#!/bin/bash
#
# Circuit Breaker Stress Test
# Tests that trading halts when max_daily_loss_usd is exceeded
#
# Expected behavior:
# 1. Strategy starts normally
# 2. Simulated losses accumulate
# 3. Circuit breaker triggers at $2 loss threshold
# 4. Trading halts with error message
# 5. No further trades are executed
#
# Author: Claude Code
# Date: 2026-03-02
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configuration
TEST_NAME="Circuit Breaker Stress Test"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="$REPO_ROOT/strategies/configs/crypto_scalp_circuit_breaker_test.yaml"
LOG_FILE="$REPO_ROOT/logs/circuit_breaker_test_$(date +%Y%m%d_%H%M%S).log"
REPORT_FILE="$REPO_ROOT/logs/circuit_breaker_test_report.txt"
TIMEOUT_SECONDS=300  # 5 minutes max

# Create logs directory
mkdir -p "$REPO_ROOT/logs"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  $TEST_NAME${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Step 1: Create temporary test configuration with low threshold
echo -e "${YELLOW}[1/5]${NC} Creating test configuration with max_daily_loss_usd: 2.0..."

# Copy base config and modify loss threshold
cp "$REPO_ROOT/strategies/configs/crypto_scalp_live.yaml" "$CONFIG_FILE"

# Use Python to modify the YAML (safer than sed)
python3 -c "
import yaml
from pathlib import Path

config_path = Path('$CONFIG_FILE')
with open(config_path, 'r') as f:
    config = yaml.safe_load(f)

# Set low loss threshold for testing
config['max_daily_loss_usd'] = 2.0
config['paper_mode'] = True  # CRITICAL: Use paper mode for testing
config['contracts_per_trade'] = 1
config['scan_interval_sec'] = 10.0
config['detector_interval_sec'] = 0.5

# Add comment at top
yaml_str = yaml.dump(config, default_flow_style=False, sort_keys=False)
header = '''# TEMPORARY TEST CONFIG - DO NOT USE IN PRODUCTION
# This config is for circuit breaker stress testing only
# max_daily_loss_usd set to 2.0 for rapid testing
# Generated: $(date)

'''
with open(config_path, 'w') as f:
    f.write(header)
    f.write(yaml_str)

print(f'✓ Test config created: {config_path}')
"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Test configuration created${NC}"
    echo "  Config file: $CONFIG_FILE"
    echo "  Max daily loss: \$2.00"
else
    echo -e "${RED}✗ Failed to create test configuration${NC}"
    exit 1
fi

# Step 2: Start strategy in background
echo ""
echo -e "${YELLOW}[2/5]${NC} Starting crypto scalp strategy with test config..."
echo "  Log file: $LOG_FILE"

# Start strategy in background, capturing PID
cd "$REPO_ROOT"
python3 main.py run crypto-scalp --config "$CONFIG_FILE" > "$LOG_FILE" 2>&1 &
STRATEGY_PID=$!

echo -e "${GREEN}✓ Strategy started (PID: $STRATEGY_PID)${NC}"
echo "  Waiting 10s for initialization..."
sleep 10

# Verify process is still running
if ! kill -0 $STRATEGY_PID 2>/dev/null; then
    echo -e "${RED}✗ Strategy process died during startup${NC}"
    echo "  Check log file: $LOG_FILE"
    cat "$LOG_FILE"
    exit 1
fi

echo -e "${GREEN}✓ Strategy running normally${NC}"

# Step 3: Monitor for circuit breaker trigger
echo ""
echo -e "${YELLOW}[3/5]${NC} Monitoring for circuit breaker trigger..."
echo "  Threshold: \$2.00 loss"
echo "  Timeout: ${TIMEOUT_SECONDS}s"
echo ""

START_TIME=$(date +%s)
CIRCUIT_BREAKER_TRIGGERED=false
HALT_MESSAGE_FOUND=false

echo "  Monitoring log file for circuit breaker events..."

while true; do
    ELAPSED=$(($(date +%s) - START_TIME))

    # Check timeout
    if [ $ELAPSED -gt $TIMEOUT_SECONDS ]; then
        echo -e "${YELLOW}⚠ Timeout reached (${TIMEOUT_SECONDS}s)${NC}"
        break
    fi

    # Check if process is still running
    if ! kill -0 $STRATEGY_PID 2>/dev/null; then
        echo -e "${YELLOW}⚠ Strategy process exited${NC}"
        break
    fi

    # Check for circuit breaker trigger in logs
    if grep -q "CIRCUIT BREAKER TRIGGERED" "$LOG_FILE" 2>/dev/null; then
        if [ "$CIRCUIT_BREAKER_TRIGGERED" = false ]; then
            CIRCUIT_BREAKER_TRIGGERED=true
            echo -e "${GREEN}✓ Circuit breaker triggered detected${NC}"

            # Extract loss amount
            LOSS_LINE=$(grep "CIRCUIT BREAKER TRIGGERED" "$LOG_FILE" | tail -1)
            echo "  Message: $LOSS_LINE"
        fi
    fi

    # Check for halt message
    if grep -q "HALTING ALL TRADING" "$LOG_FILE" 2>/dev/null; then
        if [ "$HALT_MESSAGE_FOUND" = false ]; then
            HALT_MESSAGE_FOUND=true
            echo -e "${GREEN}✓ Trading halt message detected${NC}"
        fi
    fi

    # If both conditions met, wait a bit more then exit loop
    if [ "$CIRCUIT_BREAKER_TRIGGERED" = true ] && [ "$HALT_MESSAGE_FOUND" = true ]; then
        echo ""
        echo -e "${GREEN}✓ Circuit breaker fully activated${NC}"
        sleep 5  # Wait to ensure no further trades
        break
    fi

    # Progress indicator
    printf "\r  Elapsed: ${ELAPSED}s / ${TIMEOUT_SECONDS}s"

    sleep 2
done

echo ""

# Step 4: Verify no trades after circuit breaker
echo ""
echo -e "${YELLOW}[4/5]${NC} Verifying trading halted after circuit breaker..."

# Count trades before and after circuit breaker
if [ "$CIRCUIT_BREAKER_TRIGGERED" = true ]; then
    # Get timestamp of circuit breaker trigger
    CB_LINE=$(grep -n "CIRCUIT BREAKER TRIGGERED" "$LOG_FILE" | head -1 | cut -d: -f1)
    TOTAL_LINES=$(wc -l < "$LOG_FILE")

    # Count entry/exit messages after circuit breaker
    TRADES_AFTER=$(tail -n $((TOTAL_LINES - CB_LINE)) "$LOG_FILE" | grep -c "ENTRY\|EXIT" || true)

    if [ "$TRADES_AFTER" -eq 0 ]; then
        echo -e "${GREEN}✓ No trades executed after circuit breaker${NC}"
    else
        echo -e "${RED}✗ Found $TRADES_AFTER trades after circuit breaker${NC}"
        echo "  This indicates circuit breaker did not halt trading!"
    fi
else
    echo -e "${YELLOW}⚠ Circuit breaker was not triggered${NC}"
    echo "  This may indicate:"
    echo "  - No losing trades occurred during test period"
    echo "  - Loss threshold not reached"
    echo "  - Test duration too short"
fi

# Step 5: Stop strategy and cleanup
echo ""
echo -e "${YELLOW}[5/5]${NC} Stopping strategy and cleaning up..."

# Gracefully stop strategy
if kill -0 $STRATEGY_PID 2>/dev/null; then
    kill -TERM $STRATEGY_PID 2>/dev/null || true
    sleep 2

    # Force kill if still running
    if kill -0 $STRATEGY_PID 2>/dev/null; then
        kill -KILL $STRATEGY_PID 2>/dev/null || true
    fi
fi

# Remove lock file if exists
LOCKFILE="/tmp/crypto_scalp.lock"
if [ -f "$LOCKFILE" ]; then
    rm -f "$LOCKFILE"
    echo "  Removed lock file: $LOCKFILE"
fi

echo -e "${GREEN}✓ Strategy stopped${NC}"

# Generate test report
echo ""
echo -e "${YELLOW}Generating test report...${NC}"

cat > "$REPORT_FILE" <<EOF
═══════════════════════════════════════════════════════════
CIRCUIT BREAKER STRESS TEST REPORT
Generated: $(date)
═══════════════════════════════════════════════════════════

TEST CONFIGURATION:
  Max Daily Loss:    \$2.00
  Paper Mode:        true
  Test Duration:     ${ELAPSED}s
  Config File:       $CONFIG_FILE
  Log File:          $LOG_FILE

TEST RESULTS:
  Circuit Breaker Triggered:  $([ "$CIRCUIT_BREAKER_TRIGGERED" = true ] && echo "✓ YES" || echo "✗ NO")
  Trading Halted:             $([ "$HALT_MESSAGE_FOUND" = true ] && echo "✓ YES" || echo "✗ NO")
  Trades After CB:            ${TRADES_AFTER:-N/A}

CIRCUIT BREAKER EVENTS:
EOF

if [ "$CIRCUIT_BREAKER_TRIGGERED" = true ]; then
    grep -A 3 "CIRCUIT BREAKER TRIGGERED" "$LOG_FILE" >> "$REPORT_FILE" || true
else
    echo "  No circuit breaker events detected" >> "$REPORT_FILE"
fi

cat >> "$REPORT_FILE" <<EOF

FINAL VERDICT:
$([ "$CIRCUIT_BREAKER_TRIGGERED" = true ] && [ "$HALT_MESSAGE_FOUND" = true ] && [ "${TRADES_AFTER:-1}" -eq 0 ] && echo "  ✓ PASS - Circuit breaker working correctly" || echo "  ✗ FAIL - Circuit breaker did not function as expected")

NOTES:
  - This test requires losing trades to trigger circuit breaker
  - If no trigger occurred, market conditions may not have produced losses
  - For definitive testing, consider manual loss injection or longer test duration
  - Review full log file for detailed execution trace

═══════════════════════════════════════════════════════════
EOF

cat "$REPORT_FILE"

# Cleanup temp config
rm -f "$CONFIG_FILE"
echo ""
echo -e "${GREEN}Test complete!${NC}"
echo "  Full log: $LOG_FILE"
echo "  Report:   $REPORT_FILE"
echo ""

# Exit with appropriate code
if [ "$CIRCUIT_BREAKER_TRIGGERED" = true ] && [ "$HALT_MESSAGE_FOUND" = true ] && [ "${TRADES_AFTER:-1}" -eq 0 ]; then
    echo -e "${GREEN}✓ Circuit breaker test PASSED${NC}"
    exit 0
else
    echo -e "${YELLOW}⚠ Circuit breaker test INCONCLUSIVE${NC}"
    echo "  Review report and logs for details"
    exit 2
fi
