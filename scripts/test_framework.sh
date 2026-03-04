#!/bin/bash
#
# Integration Test Framework Validation
#
# Tests that all scripts are working correctly before running the actual 8-hour test.
#
# Usage:
#   ./scripts/test_framework.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=== Integration Test Framework Validation ==="
echo ""

# Test 1: Check all scripts exist and are executable
echo "Test 1: Checking script files..."
MISSING=0

for script in run_integration_test.sh monitor_live.sh collect_metrics.py generate_integration_report.py; do
    if [[ ! -f "$SCRIPT_DIR/$script" ]]; then
        echo "  ✗ Missing: $script"
        MISSING=1
    elif [[ ! -x "$SCRIPT_DIR/$script" ]]; then
        echo "  ✗ Not executable: $script"
        MISSING=1
    else
        echo "  ✓ $script"
    fi
done

if [[ $MISSING -eq 1 ]]; then
    echo ""
    echo "ERROR: Some scripts are missing or not executable"
    exit 1
fi

echo ""
echo "Test 2: Checking help messages..."

# Test help flags work
"$SCRIPT_DIR/run_integration_test.sh" --help > /dev/null && echo "  ✓ run_integration_test.sh --help"
"$SCRIPT_DIR/monitor_live.sh" --help > /dev/null && echo "  ✓ monitor_live.sh --help"
python3 "$SCRIPT_DIR/collect_metrics.py" --help > /dev/null && echo "  ✓ collect_metrics.py --help"
python3 "$SCRIPT_DIR/generate_integration_report.py" --help > /dev/null && echo "  ✓ generate_integration_report.py --help"

echo ""
echo "Test 3: Checking Python imports..."

# Test Python scripts can import their dependencies
python3 -c "import json, re, sys, argparse, time, dataclasses, datetime, pathlib, typing" && echo "  ✓ Standard library imports"

echo ""
echo "Test 4: Checking directory structure..."

# Check logs directory exists or can be created
if [[ ! -d "$PROJECT_ROOT/logs" ]]; then
    mkdir -p "$PROJECT_ROOT/logs" && echo "  ✓ Created logs/ directory"
else
    echo "  ✓ logs/ directory exists"
fi

echo ""
echo "Test 5: Creating test log file..."

# Create a minimal test log
TEST_LOG="$PROJECT_ROOT/logs/test_framework_validation.log"
cat > "$TEST_LOG" << 'EOF'
2026-03-02 14:00:00 Starting crypto-scalp strategy
2026-03-02 14:01:00 Submitting BUY order: KXBTC-26MAR02T1401-B95500-B95600 @ 65¢
2026-03-02 14:01:01 Entry order filled: 2 contracts @ 65¢
2026-03-02 14:01:01 Entry fee: 1.5¢
2026-03-02 14:01:21 Submitting SELL order: 2 contracts @ 70¢
2026-03-02 14:01:22 Exit order filled: 2 contracts @ 70¢
2026-03-02 14:01:22 Exit fill confirmed via WebSocket
2026-03-02 14:01:22 Trade closed: profit=+8.5¢ (entry=65¢, exit=70¢, fee=1.5¢)
2026-03-02 14:01:23 Balance drift detected: drift=+1¢
2026-03-02 14:02:00 Submitting BUY order: KXBTC-26MAR02T1402-B95600-B95700 @ 60¢
2026-03-02 14:02:01 Entry failed: order cancelled
2026-03-02 14:03:00 Orderbook WebSocket reconnecting
2026-03-02 14:03:01 Using REST API fallback for orderbook
2026-03-02 15:00:00 Strategy stopped
EOF

echo "  ✓ Created test log: $TEST_LOG"

echo ""
echo "Test 6: Testing metrics collector..."

# Test metrics collector on test log
TEST_METRICS="$PROJECT_ROOT/logs/test_framework_validation_metrics.json"
python3 "$SCRIPT_DIR/collect_metrics.py" \
    --log-file "$TEST_LOG" \
    --output "$TEST_METRICS" \
    --once > /dev/null && echo "  ✓ Metrics collector works"

# Verify metrics file is valid JSON
python3 -c "import json; json.load(open('$TEST_METRICS'))" && echo "  ✓ Metrics JSON is valid"

echo ""
echo "Test 7: Testing report generator..."

# Test report generator on test log
TEST_REPORT_HTML="$PROJECT_ROOT/logs/test_framework_validation_report.html"
TEST_REPORT_MD="$PROJECT_ROOT/logs/test_framework_validation_report.md"

python3 "$SCRIPT_DIR/generate_integration_report.py" \
    "$TEST_LOG" \
    --output "$TEST_REPORT_HTML" \
    --format html > /dev/null && echo "  ✓ HTML report generated"

python3 "$SCRIPT_DIR/generate_integration_report.py" \
    "$TEST_LOG" \
    --output "$TEST_REPORT_MD" \
    --format md > /dev/null && echo "  ✓ Markdown report generated"

# Verify reports exist and are not empty
[[ -s "$TEST_REPORT_HTML" ]] && echo "  ✓ HTML report is not empty"
[[ -s "$TEST_REPORT_MD" ]] && echo "  ✓ Markdown report is not empty"

echo ""
echo "Test 8: Checking report content..."

# Check HTML report contains expected sections
grep -q "Test Summary" "$TEST_REPORT_HTML" && echo "  ✓ HTML report has Test Summary"
grep -q "Bug Fix Validation" "$TEST_REPORT_HTML" && echo "  ✓ HTML report has Bug Fix Validation"
grep -q "Performance Metrics" "$TEST_REPORT_HTML" && echo "  ✓ HTML report has Performance Metrics"

# Check Markdown report contains expected sections
grep -q "Test Summary" "$TEST_REPORT_MD" && echo "  ✓ Markdown report has Test Summary"
grep -q "Bug Fix Validation" "$TEST_REPORT_MD" && echo "  ✓ Markdown report has Bug Fix Validation"
grep -q "Performance Metrics" "$TEST_REPORT_MD" && echo "  ✓ Markdown report has Performance Metrics"

echo ""
echo "Test 9: Checking documentation..."

# Check documentation files exist
[[ -f "$SCRIPT_DIR/INTEGRATION_TEST_FRAMEWORK.md" ]] && echo "  ✓ INTEGRATION_TEST_FRAMEWORK.md exists"
[[ -f "$SCRIPT_DIR/QUICK_START.md" ]] && echo "  ✓ QUICK_START.md exists"
[[ -f "$SCRIPT_DIR/README.md" ]] && echo "  ✓ README.md exists"

echo ""
echo "Test 10: Cleanup test files..."

# Clean up test files
rm -f "$TEST_LOG" "$TEST_METRICS" "$TEST_REPORT_HTML" "$TEST_REPORT_MD"
echo "  ✓ Test files removed"

echo ""
echo "=== All Tests Passed! ==="
echo ""
echo "The integration test framework is ready to use."
echo ""
echo "Next step: Run the actual integration test"
echo "  ./scripts/run_integration_test.sh"
echo ""
echo "Or see quick start guide:"
echo "  cat scripts/QUICK_START.md"
echo ""
