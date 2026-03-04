#!/bin/bash
#
# Position Reconciliation Stress Test
# Tests detection and handling of stranded positions at startup
#
# Expected behavior:
# 1. User manually creates a test position on Kalshi
# 2. Strategy starts and queries open positions
# 3. Stranded position detected (position from previous run)
# 4. User prompted: abort or continue
# 5. If continue: position added to exit queue
# 6. Strategy attempts to close stranded position
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
TEST_NAME="Position Reconciliation Stress Test"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/logs/position_reconciliation_test_$(date +%Y%m%d_%H%M%S).log"
REPORT_FILE="$REPO_ROOT/logs/position_reconciliation_test_report.txt"

# Create logs directory
mkdir -p "$REPO_ROOT/logs"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  $TEST_NAME${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Step 1: Instructions for creating test position
echo -e "${YELLOW}[1/5]${NC} Setting up test position..."
echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  MANUAL SETUP REQUIRED                                 ║${NC}"
echo -e "${BLUE}╠════════════════════════════════════════════════════════╣${NC}"
echo -e "${BLUE}║  To test position reconciliation, you need to:         ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}║  1. Log into your Kalshi account (web or API)         ║${NC}"
echo -e "${BLUE}║  2. Manually place a small order on ANY active BTC     ║${NC}"
echo -e "${BLUE}║     market (e.g., 1 contract @ any price)             ║${NC}"
echo -e "${BLUE}║  3. Wait for the order to fill                        ║${NC}"
echo -e "${BLUE}║  4. Note the ticker and position size                 ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}║  Recommended:                                          ║${NC}"
echo -e "${BLUE}║  - Use PAPER TRADING account if available             ║${NC}"
echo -e "${BLUE}║  - Use 1-2 contracts only (minimize risk)             ║${NC}"
echo -e "${BLUE}║  - Choose a YES or NO position (doesn't matter)       ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}║  Example tickers:                                      ║${NC}"
echo -e "${BLUE}║  - BTCUSD-26MAR02T1200-B100000                        ║${NC}"
echo -e "${BLUE}║  - BTCUSD-26MAR02T1215-B99000                         ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Have you created a test position on Kalshi? (yes/no)${NC}"
read -r POSITION_CREATED

if [[ ! "$POSITION_CREATED" =~ ^[Yy] ]]; then
    echo -e "${RED}✗ Test cancelled - no position created${NC}"
    echo "  Please create a test position and run this script again"
    exit 1
fi

echo ""
echo "Great! Please provide position details:"
echo -n "Ticker (e.g., BTCUSD-26MAR02T1200-B100000): "
read -r TICKER
echo -n "Position size (number of contracts): "
read -r POSITION_SIZE
echo -n "Side (YES/NO): "
read -r SIDE

echo ""
echo "Position details:"
echo "  Ticker: $TICKER"
echo "  Size:   $POSITION_SIZE contracts"
echo "  Side:   $SIDE"
echo ""
echo -e "${GREEN}✓ Test position configured${NC}"

# Step 2: Start strategy
echo ""
echo -e "${YELLOW}[2/5]${NC} Starting crypto scalp strategy..."
echo "  Log file: $LOG_FILE"
echo ""
echo -e "${YELLOW}The strategy will detect your stranded position.${NC}"
echo -e "${YELLOW}When prompted, you can choose to:${NC}"
echo "  - ABORT: Stop the strategy (test abort scenario)"
echo "  - CONTINUE: Add position to exit queue (test continue scenario)"
echo ""
echo -e "${BLUE}Press ENTER to start the strategy...${NC}"
read -r

cd "$REPO_ROOT"

# Start strategy, piping output to both terminal and log file
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_live.yaml 2>&1 | tee "$LOG_FILE" &
STRATEGY_PID=$!

echo ""
echo -e "${GREEN}✓ Strategy started (PID: $STRATEGY_PID)${NC}"
echo "  Watch for position reconciliation prompt above..."
echo ""

# Wait for strategy to finish startup or exit
wait $STRATEGY_PID
EXIT_CODE=$?

# Step 3: Analyze logs for reconciliation behavior
echo ""
echo -e "${YELLOW}[3/5]${NC} Analyzing position reconciliation behavior..."

# Check if position was detected
POSITION_DETECTED=false
if grep -q "Stranded position\|open position" "$LOG_FILE" 2>/dev/null; then
    POSITION_DETECTED=true
    echo -e "${GREEN}✓ Stranded position detected${NC}"

    # Show detection message
    grep "Stranded position\|open position" "$LOG_FILE" | head -5
else
    echo -e "${RED}✗ Stranded position NOT detected${NC}"
    echo "  This may indicate:"
    echo "  - Position reconciliation code did not run"
    echo "  - API query failed"
    echo "  - Position was not visible to the API"
fi

echo ""

# Check if user was prompted
USER_PROMPTED=false
if grep -q "Stranded positions detected\|Continue\|Abort" "$LOG_FILE" 2>/dev/null; then
    USER_PROMPTED=true
    echo -e "${GREEN}✓ User prompted for action${NC}"
else
    echo -e "${YELLOW}⚠ User prompt not found in logs${NC}"
fi

# Check user's choice
USER_CHOICE="unknown"
if grep -q "User confirmed\|adding.*to exit queue" "$LOG_FILE" 2>/dev/null; then
    USER_CHOICE="continue"
    echo -e "${GREEN}✓ User chose to CONTINUE${NC}"
elif grep -q "User aborted\|Exiting" "$LOG_FILE" 2>/dev/null; then
    USER_CHOICE="abort"
    echo -e "${GREEN}✓ User chose to ABORT${NC}"
fi

# Step 4: Verify exit attempt (if user continued)
echo ""
echo -e "${YELLOW}[4/5]${NC} Verifying exit behavior..."

if [ "$USER_CHOICE" = "continue" ]; then
    echo "  User chose to continue - checking for exit attempt..."

    # Look for exit order placement
    EXIT_ATTEMPTED=false
    if grep -q "Closing stranded position\|EXIT.*$TICKER\|Placing exit order" "$LOG_FILE" 2>/dev/null; then
        EXIT_ATTEMPTED=true
        echo -e "${GREEN}✓ Exit attempt detected${NC}"

        # Show exit messages
        grep "EXIT\|Closing stranded" "$LOG_FILE" | head -5
    else
        echo -e "${YELLOW}⚠ No exit attempt detected${NC}"
        echo "  This may indicate:"
        echo "  - Market was closed"
        echo "  - Exit logic didn't trigger"
        echo "  - Insufficient time before test ended"
    fi
elif [ "$USER_CHOICE" = "abort" ]; then
    echo -e "${GREEN}✓ Strategy aborted as expected (exit code: $EXIT_CODE)${NC}"
else
    echo -e "${YELLOW}⚠ User choice unclear from logs${NC}"
fi

# Step 5: Manual verification reminder
echo ""
echo -e "${YELLOW}[5/5]${NC} Manual verification required..."
echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  PLEASE VERIFY MANUALLY                                ║${NC}"
echo -e "${BLUE}╠════════════════════════════════════════════════════════╣${NC}"
echo -e "${BLUE}║  1. Check your Kalshi account positions               ║${NC}"
echo -e "${BLUE}║  2. Verify if position was closed (if you continued)  ║${NC}"
echo -e "${BLUE}║  3. If position remains, close it manually            ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}║  Position details:                                     ║${NC}"
echo -e "${BLUE}║    Ticker: $TICKER${NC}"
echo -e "${BLUE}║    Size:   $POSITION_SIZE contracts${NC}"
echo -e "${BLUE}║    Side:   $SIDE${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Was the position successfully closed? (yes/no/still-open)${NC}"
read -r POSITION_CLOSED

# Cleanup
LOCKFILE="/tmp/crypto_scalp.lock"
rm -f "$LOCKFILE" 2>/dev/null || true

# Generate test report
echo ""
echo -e "${YELLOW}Generating test report...${NC}"

cat > "$REPORT_FILE" <<EOF
═══════════════════════════════════════════════════════════
POSITION RECONCILIATION STRESS TEST REPORT
Generated: $(date)
═══════════════════════════════════════════════════════════

TEST CONFIGURATION:
  Test Ticker:       $TICKER
  Position Size:     $POSITION_SIZE
  Side:              $SIDE

TEST RESULTS:
  Position Detected:     $([ "$POSITION_DETECTED" = true ] && echo "✓ YES" || echo "✗ NO")
  User Prompted:         $([ "$USER_PROMPTED" = true ] && echo "✓ YES" || echo "✗ NO")
  User Choice:           $USER_CHOICE
  Exit Attempted:        $([ "$EXIT_ATTEMPTED" = true ] && echo "✓ YES" || echo "N/A (user aborted)")
  Position Closed:       $POSITION_CLOSED (manual verification)

POSITION DETECTION EVENTS:
EOF

grep "Stranded position\|open position" "$LOG_FILE" >> "$REPORT_FILE" 2>/dev/null || echo "  No detection events found" >> "$REPORT_FILE"

cat >> "$REPORT_FILE" <<EOF

USER INTERACTION:
EOF

grep "Stranded positions detected\|Continue\|Abort\|User confirmed\|User aborted" "$LOG_FILE" >> "$REPORT_FILE" 2>/dev/null || echo "  No user interaction found" >> "$REPORT_FILE"

cat >> "$REPORT_FILE" <<EOF

EXIT ATTEMPTS:
EOF

if [ "$USER_CHOICE" = "continue" ]; then
    grep "EXIT\|Closing stranded\|exit order" "$LOG_FILE" >> "$REPORT_FILE" 2>/dev/null || echo "  No exit attempts found" >> "$REPORT_FILE"
else
    echo "  N/A (user aborted)" >> "$REPORT_FILE"
fi

cat >> "$REPORT_FILE" <<EOF

FINAL VERDICT:
EOF

# Determine pass/fail
if [ "$POSITION_DETECTED" = true ] && [ "$USER_PROMPTED" = true ]; then
    if [ "$USER_CHOICE" = "abort" ]; then
        echo "  ✓ PASS - Position detected, user prompted, abort handled correctly" >> "$REPORT_FILE"
        VERDICT="PASS"
    elif [ "$USER_CHOICE" = "continue" ] && [ "$EXIT_ATTEMPTED" = true ]; then
        echo "  ✓ PASS - Position detected, user prompted, exit attempted" >> "$REPORT_FILE"
        VERDICT="PASS"
    else
        echo "  ⚠ PARTIAL - Position detected but exit not confirmed" >> "$REPORT_FILE"
        VERDICT="PARTIAL"
    fi
else
    echo "  ✗ FAIL - Position reconciliation did not work as expected" >> "$REPORT_FILE"
    VERDICT="FAIL"
fi

cat >> "$REPORT_FILE" <<EOF

NOTES:
  - This test requires manual position creation on Kalshi
  - Position detection depends on API access and permissions
  - Exit success depends on market liquidity and status
  - Manual verification is required to confirm position closure
  - If position remains open, close it manually to avoid fees

LOG FILE: $LOG_FILE

═══════════════════════════════════════════════════════════
EOF

cat "$REPORT_FILE"

echo ""
echo -e "${GREEN}Test complete!${NC}"
echo "  Full log: $LOG_FILE"
echo "  Report:   $REPORT_FILE"
echo ""

# Exit with appropriate code
if [ "$VERDICT" = "PASS" ]; then
    echo -e "${GREEN}✓ Position reconciliation test PASSED${NC}"
    exit 0
elif [ "$VERDICT" = "PARTIAL" ]; then
    echo -e "${YELLOW}⚠ Position reconciliation test PARTIAL${NC}"
    exit 2
else
    echo -e "${RED}✗ Position reconciliation test FAILED${NC}"
    exit 1
fi
