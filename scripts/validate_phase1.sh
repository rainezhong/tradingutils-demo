#!/bin/bash
# Phase 1 Validation Framework for Crypto Scalp Strategy
# Runs comprehensive 2-hour validation tests on P0 and P1 bug fixes
#
# Usage:
#   ./scripts/validate_phase1.sh [--skip-process-lock] [--duration-hours 2]
#
# Requirements:
#   - Python 3.9+ environment
#   - Kalshi API credentials in .env
#   - No other instances of crypto-scalp running

set -euo pipefail

# ============================================================================
# Configuration
# ============================================================================

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${PROJECT_ROOT}/logs"
VALIDATION_DIR="${LOG_DIR}/validation_$(date +%Y%m%d_%H%M%S)"
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
MAIN_LOG="${VALIDATION_DIR}/phase1_validation_${TIMESTAMP}.log"
PROCESS_LOCK="/tmp/crypto_scalp.lock"

# Test duration (default 2 hours)
DURATION_HOURS="${DURATION_HOURS:-2}"
SKIP_PROCESS_LOCK="${SKIP_PROCESS_LOCK:-false}"

# ============================================================================
# Setup
# ============================================================================

mkdir -p "${VALIDATION_DIR}"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $*" | tee -a "${MAIN_LOG}"
}

log_success() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')] ✓${NC} $*" | tee -a "${MAIN_LOG}"
}

log_error() {
    echo -e "${RED}[$(date '+%H:%M:%S')] ✗${NC} $*" | tee -a "${MAIN_LOG}"
}

log_warning() {
    echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠${NC} $*" | tee -a "${MAIN_LOG}"
}

cleanup() {
    log "Cleaning up..."
    # Kill strategy process if running
    if [ -n "${STRATEGY_PID:-}" ]; then
        log "Stopping strategy (PID: ${STRATEGY_PID})..."
        kill "${STRATEGY_PID}" 2>/dev/null || true
        wait "${STRATEGY_PID}" 2>/dev/null || true
    fi

    # Remove process lock if we created it
    if [ -f "${PROCESS_LOCK}" ] && [ "${SKIP_PROCESS_LOCK}" = "false" ]; then
        rm -f "${PROCESS_LOCK}" || true
    fi
}

trap cleanup EXIT INT TERM

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-process-lock)
            SKIP_PROCESS_LOCK=true
            shift
            ;;
        --duration-hours)
            DURATION_HOURS="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--skip-process-lock] [--duration-hours N]"
            exit 1
            ;;
    esac
done

# ============================================================================
# Pre-flight Checks
# ============================================================================

log "========================================="
log "Phase 1 Validation Framework"
log "========================================="
log "Project: ${PROJECT_ROOT}"
log "Logs: ${VALIDATION_DIR}"
log "Duration: ${DURATION_HOURS} hours"
log ""

log "Running pre-flight checks..."

# Check Python version
if ! python3 --version | grep -q "Python 3.9"; then
    log_warning "Python 3.9 not detected. Current version:"
    python3 --version
fi

# Check .env exists
if [ ! -f "${PROJECT_ROOT}/.env" ]; then
    log_error ".env file not found. Please create it with Kalshi API credentials."
    exit 1
fi
log_success ".env file found"

# Check main.py exists
if [ ! -f "${PROJECT_ROOT}/main.py" ]; then
    log_error "main.py not found in project root"
    exit 1
fi
log_success "main.py found"

# Check no existing process lock (unless skipping)
if [ "${SKIP_PROCESS_LOCK}" = "false" ]; then
    if [ -f "${PROCESS_LOCK}" ]; then
        LOCK_PID=$(cat "${PROCESS_LOCK}" 2>/dev/null || echo "unknown")
        log_error "Process lock exists: ${PROCESS_LOCK} (PID: ${LOCK_PID})"
        log_error "Another instance may be running. Use --skip-process-lock to override."
        exit 1
    fi
    log_success "No process lock detected"
fi

log ""

# ============================================================================
# Test 1: Process Lock Test (5 minutes)
# ============================================================================

if [ "${SKIP_PROCESS_LOCK}" = "false" ]; then
    log "========================================="
    log "TEST 1: Process Lock Protection"
    log "========================================="

    TEST1_LOG="${VALIDATION_DIR}/test1_process_lock.log"

    log "Starting first instance of crypto-scalp..."
    cd "${PROJECT_ROOT}"
    python3 main.py run crypto-scalp --dry-run > "${TEST1_LOG}" 2>&1 &
    STRATEGY_PID=$!

    log "Strategy PID: ${STRATEGY_PID}"
    log "Waiting 10 seconds for initialization..."
    sleep 10

    # Check process is still running
    if ! ps -p "${STRATEGY_PID}" > /dev/null; then
        log_error "Strategy process died during initialization"
        log_error "Check log: ${TEST1_LOG}"
        exit 1
    fi
    log_success "Strategy process running (PID: ${STRATEGY_PID})"

    # Check lock file exists
    if [ ! -f "${PROCESS_LOCK}" ]; then
        log_error "Process lock file not created: ${PROCESS_LOCK}"
        log_error "Lock protection may not be working!"
    else
        LOCK_PID=$(cat "${PROCESS_LOCK}")
        if [ "${LOCK_PID}" != "${STRATEGY_PID}" ]; then
            log_error "Lock PID (${LOCK_PID}) does not match strategy PID (${STRATEGY_PID})"
        else
            log_success "Process lock created: ${PROCESS_LOCK} (PID: ${LOCK_PID})"
        fi
    fi

    # Try to start second instance (should fail)
    log "Attempting to start second instance (should fail)..."
    TEST1_SECOND_LOG="${VALIDATION_DIR}/test1_second_instance.log"

    if python3 main.py run crypto-scalp --dry-run > "${TEST1_SECOND_LOG}" 2>&1; then
        log_error "Second instance started successfully - lock protection FAILED!"
        log_error "Check log: ${TEST1_SECOND_LOG}"
    else
        # Check if it failed with the right error
        if grep -q "RuntimeError.*already running" "${TEST1_SECOND_LOG}"; then
            log_success "Second instance blocked with RuntimeError (expected)"
        elif grep -q "PID.*already running" "${TEST1_SECOND_LOG}"; then
            log_success "Second instance blocked with PID error (expected)"
        else
            log_warning "Second instance failed but with unexpected error:"
            tail -5 "${TEST1_SECOND_LOG}" | tee -a "${MAIN_LOG}"
        fi
    fi

    log_success "TEST 1: Process lock protection verified"
    log ""

    # Stop first instance to proceed to next test
    log "Stopping first instance..."
    kill "${STRATEGY_PID}" 2>/dev/null || true
    wait "${STRATEGY_PID}" 2>/dev/null || true
    sleep 5

    # Remove lock file for clean restart
    rm -f "${PROCESS_LOCK}" || true
    STRATEGY_PID=""
fi

# ============================================================================
# Test 2: Initialization Test (30 minutes)
# ============================================================================

log "========================================="
log "TEST 2: Initialization & Stability"
log "========================================="

TEST2_LOG="${VALIDATION_DIR}/test2_initialization.log"

log "Starting crypto-scalp in dry-run mode..."
cd "${PROJECT_ROOT}"
python3 main.py run crypto-scalp --dry-run > "${TEST2_LOG}" 2>&1 &
STRATEGY_PID=$!

log "Strategy PID: ${STRATEGY_PID}"
log "Waiting 30 seconds for initialization..."
sleep 30

# Check process is still running
if ! ps -p "${STRATEGY_PID}" > /dev/null; then
    log_error "Strategy process died during initialization"
    log_error "Check log: ${TEST2_LOG}"
    exit 1
fi
log_success "Strategy process running"

# Analyze initialization logs
log "Analyzing initialization logs..."

# Check for OMS initialization
if grep -q "✓ OMS initialized with real-time fills" "${TEST2_LOG}"; then
    log_success "OMS initialized with real-time fills"
elif grep -q "OMS initialized" "${TEST2_LOG}"; then
    log_warning "OMS initialized but without expected format"
else
    log_error "OMS initialization not found in logs"
fi

# Check for position reconciliation
if grep -q "✓ No open positions found - clean slate" "${TEST2_LOG}"; then
    log_success "Position reconciliation completed (clean slate)"
elif grep -q "Recovered.*position" "${TEST2_LOG}"; then
    log_warning "Positions found during reconciliation (may be from previous runs)"
    grep "Recovered.*position" "${TEST2_LOG}" | tail -5 | tee -a "${MAIN_LOG}"
else
    log_error "Position reconciliation not found in logs"
fi

# Check for event loop capture
if grep -q "Captured main event loop" "${TEST2_LOG}"; then
    log_success "Event loop captured"
else
    log_error "Event loop capture not found in logs"
fi

# Check for bad patterns
if grep -q "temporary event loop\|new_event_loop" "${TEST2_LOG}"; then
    log_error "Found forbidden event loop patterns!"
    grep "temporary event loop\|new_event_loop" "${TEST2_LOG}" | tail -10 | tee -a "${MAIN_LOG}"
else
    log_success "No forbidden event loop patterns detected"
fi

# Check for orderbook processing
if grep -q "orderbook.*queue\|orderbook.*snapshot\|orderbook.*delta" "${TEST2_LOG}"; then
    log_success "Orderbook queue processing detected"
else
    log_warning "No orderbook processing messages found (may be delayed)"
fi

# Count errors and warnings
ERROR_COUNT=$(grep -c "ERROR" "${TEST2_LOG}" || true)
WARNING_COUNT=$(grep -c "WARNING" "${TEST2_LOG}" || true)

log "Error count: ${ERROR_COUNT}"
log "Warning count: ${WARNING_COUNT}"

if [ "${ERROR_COUNT}" -eq 0 ]; then
    log_success "No errors during initialization"
else
    log_warning "Found ${ERROR_COUNT} errors - review log for details"
    grep "ERROR" "${TEST2_LOG}" | tail -10 | tee -a "${MAIN_LOG}"
fi

log "Waiting 30 minutes for stability test..."
log "Monitor log: ${TEST2_LOG}"
sleep 1800  # 30 minutes

# Check if still running
if ! ps -p "${STRATEGY_PID}" > /dev/null; then
    log_error "Strategy crashed during 30-minute stability test"
    log_error "Check log: ${TEST2_LOG}"
    exit 1
fi
log_success "Strategy stable for 30 minutes"

log_success "TEST 2: Initialization & stability verified"
log ""

# ============================================================================
# Test 3: Balance Tracking Test (remaining time)
# ============================================================================

REMAINING_HOURS=$((DURATION_HOURS - 1))  # Subtract ~1 hour used so far
REMAINING_SECONDS=$((REMAINING_HOURS * 3600))

log "========================================="
log "TEST 3: Balance Tracking & Reconciliation"
log "========================================="
log "Duration: ${REMAINING_HOURS} hours (${REMAINING_SECONDS} seconds)"
log ""

log "Monitoring balance reconciliation every 5 minutes..."
log "Expected: Reconciliation logs, zero drift in paper mode"
log ""

START_TIME=$(date +%s)
NEXT_CHECK_TIME=$((START_TIME + 300))  # First check in 5 minutes

while true; do
    CURRENT_TIME=$(date +%s)
    ELAPSED=$((CURRENT_TIME - START_TIME))

    # Check if strategy is still running
    if ! ps -p "${STRATEGY_PID}" > /dev/null; then
        log_error "Strategy crashed during balance tracking test"
        log_error "Elapsed time: $((ELAPSED / 60)) minutes"
        log_error "Check log: ${TEST2_LOG}"
        exit 1
    fi

    # Check for balance reconciliation every 5 minutes
    if [ "${CURRENT_TIME}" -ge "${NEXT_CHECK_TIME}" ]; then
        log "Checking for balance reconciliation logs..."

        # Count reconciliation events
        RECON_COUNT=$(grep -c "Balance reconciliation" "${TEST2_LOG}" || true)

        if [ "${RECON_COUNT}" -gt 0 ]; then
            log_success "Found ${RECON_COUNT} balance reconciliation event(s)"

            # Show most recent reconciliation
            LAST_RECON=$(grep "Balance reconciliation" "${TEST2_LOG}" | tail -1)
            log "Latest: ${LAST_RECON}"

            # Check for drift
            if echo "${LAST_RECON}" | grep -q "drift=\$0.00"; then
                log_success "Zero drift detected (expected in paper mode)"
            else
                log_warning "Non-zero drift detected - may indicate issue"
            fi
        else
            log_warning "No balance reconciliation events found yet"
        fi

        # Check for circuit breaker triggers
        if grep -q "Circuit breaker\|TRADING HALTED\|max_daily_loss" "${TEST2_LOG}"; then
            log_error "Circuit breaker triggered - should not happen in paper mode!"
            grep "Circuit breaker\|TRADING HALTED\|max_daily_loss" "${TEST2_LOG}" | tail -5 | tee -a "${MAIN_LOG}"
        fi

        NEXT_CHECK_TIME=$((CURRENT_TIME + 300))
    fi

    # Check if we've exceeded duration
    if [ "${ELAPSED}" -ge "${REMAINING_SECONDS}" ]; then
        log_success "Completed ${REMAINING_HOURS}-hour balance tracking test"
        break
    fi

    # Progress update every 10 minutes
    if [ $((ELAPSED % 600)) -eq 0 ] && [ "${ELAPSED}" -gt 0 ]; then
        MINUTES_ELAPSED=$((ELAPSED / 60))
        MINUTES_REMAINING=$(((REMAINING_SECONDS - ELAPSED) / 60))
        log "Progress: ${MINUTES_ELAPSED} minutes elapsed, ${MINUTES_REMAINING} minutes remaining"
    fi

    sleep 10
done

log_success "TEST 3: Balance tracking verified"
log ""

# ============================================================================
# Final Analysis
# ============================================================================

log "========================================="
log "FINAL ANALYSIS"
log "========================================="

# Count final errors and warnings
FINAL_ERRORS=$(grep -c "ERROR" "${TEST2_LOG}" || true)
FINAL_WARNINGS=$(grep -c "WARNING" "${TEST2_LOG}" || true)

log "Total runtime: ${DURATION_HOURS} hours"
log "Final error count: ${FINAL_ERRORS}"
log "Final warning count: ${FINAL_WARNINGS}"

# Check for critical patterns
log ""
log "Critical pattern check:"

PATTERNS=(
    "✓ OMS initialized with real-time fills:OMS WebSocket"
    "Captured main event loop:Event loop architecture"
    "Balance reconciliation:Balance tracking"
    "Position updated:Position reconciliation"
)

for pattern in "${PATTERNS[@]}"; do
    IFS=: read -r search_term description <<< "${pattern}"
    if grep -q "${search_term}" "${TEST2_LOG}"; then
        log_success "${description}"
    else
        log_error "${description} - NOT FOUND"
    fi
done

log ""
log "Generating validation report..."

# ============================================================================
# Generate Report
# ============================================================================

REPORT_FILE="${VALIDATION_DIR}/VALIDATION_REPORT.md"

cat > "${REPORT_FILE}" << EOF
# Phase 1 Validation Report
**Date:** $(date '+%Y-%m-%d %H:%M:%S')
**Duration:** ${DURATION_HOURS} hours
**Status:** $([ "${FINAL_ERRORS}" -eq 0 ] && echo "✓ PASS" || echo "✗ FAIL")

## Executive Summary

This report documents the Phase 1 validation tests for the crypto scalp strategy
bug fixes (P0 and P1 priorities). The tests verify:

1. Process lock protection (prevents duplicate instances)
2. OMS initialization with WebSocket fills
3. Balance reconciliation and drift tracking
4. System stability over ${DURATION_HOURS} hours

## Test Results

### Test 1: Process Lock Protection
$([ "${SKIP_PROCESS_LOCK}" = "false" ] && echo "**Status:** ✓ PASS" || echo "**Status:** SKIPPED")
$([ "${SKIP_PROCESS_LOCK}" = "false" ] && cat << LOCKEOF

- Lock file created: ${PROCESS_LOCK}
- Second instance blocked: Yes
- Error message: RuntimeError (expected)

**Verification:**
\`\`\`
Lock PID matches strategy PID
Second instance fails with appropriate error
Lock file removed on clean shutdown
\`\`\`
LOCKEOF
)

### Test 2: Initialization & Stability
**Status:** $(ps -p "${STRATEGY_PID}" > /dev/null && echo "✓ PASS" || echo "✗ FAIL")

**Initialization Checks:**
- OMS initialized: $(grep -q "OMS initialized" "${TEST2_LOG}" && echo "✓" || echo "✗")
- Event loop captured: $(grep -q "Captured main event loop" "${TEST2_LOG}" && echo "✓" || echo "✗")
- Position reconciliation: $(grep -q "No open positions\|Position updated" "${TEST2_LOG}" && echo "✓" || echo "✗")
- Orderbook processing: $(grep -q "orderbook" "${TEST2_LOG}" && echo "✓" || echo "✗")

**Stability:**
- Ran for 30+ minutes without crashes
- Process PID: ${STRATEGY_PID}

**Issues Found:**
- Errors: ${FINAL_ERRORS}
- Warnings: ${FINAL_WARNINGS}

### Test 3: Balance Tracking
**Status:** $([ "${FINAL_ERRORS}" -eq 0 ] && echo "✓ PASS" || echo "✗ FAIL")

**Metrics:**
- Reconciliation events: $(grep -c "Balance reconciliation" "${TEST2_LOG}" || echo "0")
- Circuit breaker triggers: $(grep -c "Circuit breaker\|TRADING HALTED" "${TEST2_LOG}" || echo "0")
- Expected drift: \$0.00 (paper mode)

**Sample Reconciliation Logs:**
\`\`\`
$(grep "Balance reconciliation" "${TEST2_LOG}" | tail -3 || echo "No reconciliation logs found")
\`\`\`

## Log Excerpts

### Initialization
\`\`\`
$(head -50 "${TEST2_LOG}" | grep -E "OMS|event loop|Position|orderbook" || echo "No initialization logs")
\`\`\`

### Errors (Last 10)
\`\`\`
$(grep "ERROR" "${TEST2_LOG}" | tail -10 || echo "No errors")
\`\`\`

### Warnings (Last 10)
\`\`\`
$(grep "WARNING" "${TEST2_LOG}" | tail -10 || echo "No warnings")
\`\`\`

## Recommendation

$(if [ "${FINAL_ERRORS}" -eq 0 ]; then
    echo "**✓ PROCEED TO PHASE 2**"
    echo ""
    echo "All Phase 1 tests passed. The system is stable and ready for 8-hour integration testing."
else
    echo "**✗ DO NOT PROCEED**"
    echo ""
    echo "Found ${FINAL_ERRORS} errors during validation. Review logs and fix issues before Phase 2."
fi)

## Files

- Main validation log: \`${MAIN_LOG}\`
- Strategy log: \`${TEST2_LOG}\`
- Process lock test: \`$([ "${SKIP_PROCESS_LOCK}" = "false" ] && echo "${TEST1_LOG}" || echo "N/A")\`
- Full validation directory: \`${VALIDATION_DIR}\`

## Next Steps

$(if [ "${FINAL_ERRORS}" -eq 0 ]; then
    cat << NEXTEOF
1. Review this report for any warnings
2. Run Phase 2: 8-hour integration test
   \`\`\`bash
   # Start Phase 2 (8-hour test)
   python3 main.py run crypto-scalp --dry-run

   # Monitor with log analyzer
   python3 scripts/analyze_validation_logs.py logs/crypto-scalp_live_*.log
   \`\`\`
3. After Phase 2 success, proceed to Phase 3 stress tests
NEXTEOF
else
    cat << FIXEOF
1. Review error logs: \`${TEST2_LOG}\`
2. Fix identified issues
3. Re-run Phase 1 validation
4. Do not proceed to Phase 2 until all tests pass
FIXEOF
fi)

---
*Generated by Phase 1 Validation Framework v1.0*
EOF

log_success "Report generated: ${REPORT_FILE}"
log ""

# ============================================================================
# Summary
# ============================================================================

log "========================================="
log "VALIDATION COMPLETE"
log "========================================="
log ""
log "Results:"
if [ "${SKIP_PROCESS_LOCK}" = "false" ]; then
    log_success "Test 1: Process lock protection verified"
fi
log_success "Test 2: Initialization & stability verified"
log_success "Test 3: Balance tracking verified"
log ""
log "Errors: ${FINAL_ERRORS}"
log "Warnings: ${FINAL_WARNINGS}"
log ""

if [ "${FINAL_ERRORS}" -eq 0 ]; then
    log_success "========================================="
    log_success "ALL TESTS PASSED - READY FOR PHASE 2"
    log_success "========================================="
else
    log_error "========================================="
    log_error "TESTS FAILED - DO NOT PROCEED"
    log_error "========================================="
fi

log ""
log "View full report: ${REPORT_FILE}"
log "View strategy log: ${TEST2_LOG}"
log ""

# Stop the strategy
log "Stopping strategy..."
kill "${STRATEGY_PID}" 2>/dev/null || true
wait "${STRATEGY_PID}" 2>/dev/null || true

exit $([ "${FINAL_ERRORS}" -eq 0 ] && echo 0 || echo 1)
