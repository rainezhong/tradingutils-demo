#!/bin/bash
#
# Process Lock Stress Test
# Tests that only one instance of the strategy can run at a time
#
# Expected behavior:
# 1. First instance starts successfully
# 2. Lock file created at /tmp/crypto_scalp.lock
# 3. Second instance attempt fails with RuntimeError
# 4. Lock file persists while first instance runs
# 5. Lock file cleaned up on shutdown
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
TEST_NAME="Process Lock Stress Test"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE_1="$REPO_ROOT/logs/process_lock_test_instance1_$(date +%Y%m%d_%H%M%S).log"
LOG_FILE_2="$REPO_ROOT/logs/process_lock_test_instance2_$(date +%Y%m%d_%H%M%S).log"
REPORT_FILE="$REPO_ROOT/logs/process_lock_test_report.txt"
LOCKFILE="/tmp/crypto_scalp.lock"

# Create logs directory
mkdir -p "$REPO_ROOT/logs"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  $TEST_NAME${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Cleanup any existing lock file
if [ -f "$LOCKFILE" ]; then
    echo -e "${YELLOW}⚠ Cleaning up existing lock file${NC}"
    rm -f "$LOCKFILE"
fi

# Step 1: Start first instance
echo -e "${YELLOW}[1/5]${NC} Starting first instance of crypto scalp strategy..."
echo "  Log file: $LOG_FILE_1"

cd "$REPO_ROOT"
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_live.yaml > "$LOG_FILE_1" 2>&1 &
INSTANCE1_PID=$!

echo -e "${GREEN}✓ First instance started (PID: $INSTANCE1_PID)${NC}"
echo "  Waiting 10s for initialization..."
sleep 10

# Verify first instance is still running
if ! kill -0 $INSTANCE1_PID 2>/dev/null; then
    echo -e "${RED}✗ First instance died during startup${NC}"
    echo "  Check log file: $LOG_FILE_1"
    tail -50 "$LOG_FILE_1"
    exit 1
fi

echo -e "${GREEN}✓ First instance running normally${NC}"

# Step 2: Verify lock file exists
echo ""
echo -e "${YELLOW}[2/5]${NC} Verifying lock file creation..."

if [ -f "$LOCKFILE" ]; then
    echo -e "${GREEN}✓ Lock file exists: $LOCKFILE${NC}"

    # Read PID from lock file
    LOCK_PID=$(cat "$LOCKFILE")
    echo "  Lock file PID: $LOCK_PID"

    # Verify PID matches first instance
    if [ "$LOCK_PID" = "$INSTANCE1_PID" ]; then
        echo -e "${GREEN}✓ Lock file PID matches first instance${NC}"
    else
        echo -e "${YELLOW}⚠ Lock file PID mismatch${NC}"
        echo "  Expected: $INSTANCE1_PID"
        echo "  Found:    $LOCK_PID"
    fi
else
    echo -e "${RED}✗ Lock file NOT created${NC}"
    echo "  This is a critical failure - process lock not working!"
fi

# Step 3: Attempt to start second instance
echo ""
echo -e "${YELLOW}[3/5]${NC} Attempting to start second instance (should fail)..."
echo "  Log file: $LOG_FILE_2"

cd "$REPO_ROOT"
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_live.yaml > "$LOG_FILE_2" 2>&1 &
INSTANCE2_PID=$!

echo "  Second instance process started (PID: $INSTANCE2_PID)"
echo "  Waiting 5s to see if it fails..."
sleep 5

# Check if second instance is still running (it shouldn't be)
INSTANCE2_RUNNING=false
if kill -0 $INSTANCE2_PID 2>/dev/null; then
    INSTANCE2_RUNNING=true
    echo -e "${RED}✗ Second instance is still running (FAILURE!)${NC}"
else
    echo -e "${GREEN}✓ Second instance exited as expected${NC}"
fi

# Step 4: Verify second instance error message
echo ""
echo -e "${YELLOW}[4/5]${NC} Verifying second instance error message..."

# Check for RuntimeError in second instance log
RUNTIME_ERROR_FOUND=false
if grep -q "RuntimeError\|Another instance is running\|Lock file exists" "$LOG_FILE_2" 2>/dev/null; then
    RUNTIME_ERROR_FOUND=true
    echo -e "${GREEN}✓ RuntimeError found in second instance log${NC}"

    # Show error message
    echo ""
    echo "Error message:"
    grep -A 3 "RuntimeError\|Another instance is running" "$LOG_FILE_2" | head -10
    echo ""
else
    echo -e "${RED}✗ RuntimeError NOT found in second instance log${NC}"
    echo "  Second instance log excerpt:"
    tail -20 "$LOG_FILE_2"
fi

# Step 5: Stop first instance and verify cleanup
echo ""
echo -e "${YELLOW}[5/5]${NC} Stopping first instance and verifying cleanup..."

# Stop first instance
if kill -0 $INSTANCE1_PID 2>/dev/null; then
    kill -TERM $INSTANCE1_PID 2>/dev/null || true
    sleep 3

    # Force kill if still running
    if kill -0 $INSTANCE1_PID 2>/dev/null; then
        kill -KILL $INSTANCE1_PID 2>/dev/null || true
        sleep 1
    fi
fi

echo -e "${GREEN}✓ First instance stopped${NC}"

# Stop second instance if somehow still running
if [ "$INSTANCE2_RUNNING" = true ]; then
    kill -TERM $INSTANCE2_PID 2>/dev/null || true
    sleep 1
    kill -KILL $INSTANCE2_PID 2>/dev/null || true
fi

# Wait a moment for cleanup
sleep 2

# Verify lock file was cleaned up
LOCK_CLEANED_UP=false
if [ ! -f "$LOCKFILE" ]; then
    LOCK_CLEANED_UP=true
    echo -e "${GREEN}✓ Lock file cleaned up successfully${NC}"
else
    echo -e "${YELLOW}⚠ Lock file still exists after shutdown${NC}"
    echo "  This may indicate cleanup handler didn't run"
    echo "  Manually removing lock file..."
    rm -f "$LOCKFILE"
fi

# Generate test report
echo ""
echo -e "${YELLOW}Generating test report...${NC}"

cat > "$REPORT_FILE" <<EOF
═══════════════════════════════════════════════════════════
PROCESS LOCK STRESS TEST REPORT
Generated: $(date)
═══════════════════════════════════════════════════════════

TEST CONFIGURATION:
  Lock file path:    $LOCKFILE
  First instance:    $LOG_FILE_1
  Second instance:   $LOG_FILE_2

TEST RESULTS:
  First Instance Started:     ✓ YES
  Lock File Created:          $([ -f "$LOCKFILE" ] && echo "✓ YES (at test start)" || echo "✗ NO")
  Second Instance Blocked:    $([ "$INSTANCE2_RUNNING" = false ] && echo "✓ YES" || echo "✗ NO (FAILURE)")
  RuntimeError Raised:        $([ "$RUNTIME_ERROR_FOUND" = true ] && echo "✓ YES" || echo "✗ NO")
  Lock File Cleaned Up:       $([ "$LOCK_CLEANED_UP" = true ] && echo "✓ YES" || echo "⚠ NO (manual cleanup)")

LOCK FILE DETAILS:
EOF

if [ -f "$LOCKFILE" ]; then
    echo "  PID in lock file: $(cat $LOCKFILE 2>/dev/null || echo 'ERROR')" >> "$REPORT_FILE"
else
    echo "  Lock file removed (as expected after shutdown)" >> "$REPORT_FILE"
fi

cat >> "$REPORT_FILE" <<EOF

SECOND INSTANCE ERROR:
EOF

grep -A 5 "RuntimeError\|Another instance\|Lock file" "$LOG_FILE_2" >> "$REPORT_FILE" 2>/dev/null || echo "  No error message found" >> "$REPORT_FILE"

cat >> "$REPORT_FILE" <<EOF

FINAL VERDICT:
EOF

# Determine pass/fail
if [ "$INSTANCE2_RUNNING" = false ] && [ "$RUNTIME_ERROR_FOUND" = true ] && [ "$LOCK_CLEANED_UP" = true ]; then
    echo "  ✓ PASS - Process lock working correctly" >> "$REPORT_FILE"
    VERDICT="PASS"
elif [ "$INSTANCE2_RUNNING" = false ] && [ "$RUNTIME_ERROR_FOUND" = true ]; then
    echo "  ⚠ PARTIAL - Process lock works but cleanup may have issues" >> "$REPORT_FILE"
    VERDICT="PARTIAL"
else
    echo "  ✗ FAIL - Process lock NOT working correctly" >> "$REPORT_FILE"
    VERDICT="FAIL"
fi

cat >> "$REPORT_FILE" <<EOF

NOTES:
  - Lock file location: /tmp/crypto_scalp.lock
  - Lock file contains PID of running instance
  - Cleanup handled by atexit.register() in Python
  - Second instance should fail immediately with RuntimeError
  - Lock file should be automatically removed on graceful shutdown

DETAILED LOGS:
  Instance 1: $LOG_FILE_1
  Instance 2: $LOG_FILE_2

═══════════════════════════════════════════════════════════
EOF

cat "$REPORT_FILE"

echo ""
echo -e "${GREEN}Test complete!${NC}"
echo "  Instance 1 log: $LOG_FILE_1"
echo "  Instance 2 log: $LOG_FILE_2"
echo "  Report:         $REPORT_FILE"
echo ""

# Final cleanup - ensure no lock file remains
rm -f "$LOCKFILE" 2>/dev/null || true

# Exit with appropriate code
if [ "$VERDICT" = "PASS" ]; then
    echo -e "${GREEN}✓ Process lock test PASSED${NC}"
    exit 0
elif [ "$VERDICT" = "PARTIAL" ]; then
    echo -e "${YELLOW}⚠ Process lock test PARTIAL${NC}"
    exit 2
else
    echo -e "${RED}✗ Process lock test FAILED${NC}"
    exit 1
fi
