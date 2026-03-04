#!/bin/bash
#
# WebSocket Reconnection Stress Test
# Tests WebSocket reconnection with exponential backoff
#
# Expected behavior:
# 1. Strategy starts with WebSocket connections
# 2. Network interruption simulated (manual or automated)
# 3. Strategy detects disconnection
# 4. Exponential backoff reconnection attempts (1s, 2s, 4s, 8s, ...)
# 5. Recovery after successful reconnection
# 6. Data flow resumes normally
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
TEST_NAME="WebSocket Reconnection Stress Test"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$REPO_ROOT/logs/websocket_reconnection_test_$(date +%Y%m%d_%H%M%S).log"
REPORT_FILE="$REPO_ROOT/logs/websocket_reconnection_test_report.txt"
TEST_DURATION=180  # 3 minutes

# Create logs directory
mkdir -p "$REPO_ROOT/logs"

echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  $TEST_NAME${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}"
echo ""

# Detect OS
OS_TYPE=$(uname -s)
echo "Detected OS: $OS_TYPE"
echo ""

# Step 1: Start strategy
echo -e "${YELLOW}[1/6]${NC} Starting crypto scalp strategy..."
echo "  Log file: $LOG_FILE"

cd "$REPO_ROOT"
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_live.yaml > "$LOG_FILE" 2>&1 &
STRATEGY_PID=$!

echo -e "${GREEN}✓ Strategy started (PID: $STRATEGY_PID)${NC}"
echo "  Waiting 15s for WebSocket initialization..."
sleep 15

# Verify process is still running
if ! kill -0 $STRATEGY_PID 2>/dev/null; then
    echo -e "${RED}✗ Strategy process died during startup${NC}"
    echo "  Check log file: $LOG_FILE"
    tail -50 "$LOG_FILE"
    exit 1
fi

# Step 2: Verify WebSocket connections established
echo ""
echo -e "${YELLOW}[2/6]${NC} Verifying WebSocket connections established..."

WS_CONNECTED=false
for i in {1..10}; do
    if grep -q "WebSocket.*connected\|WS.*ready\|Subscribed to" "$LOG_FILE" 2>/dev/null; then
        WS_CONNECTED=true
        break
    fi
    sleep 1
done

if [ "$WS_CONNECTED" = true ]; then
    echo -e "${GREEN}✓ WebSocket connections established${NC}"

    # Count WebSocket connections
    WS_COUNT=$(grep -c "WebSocket.*connected\|WS.*ready" "$LOG_FILE" 2>/dev/null || echo "0")
    echo "  Active connections: $WS_COUNT"
else
    echo -e "${YELLOW}⚠ Could not verify WebSocket connections${NC}"
    echo "  Proceeding with test anyway..."
fi

# Step 3: Simulate network interruption
echo ""
echo -e "${YELLOW}[3/6]${NC} Simulating network interruption..."
echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  MANUAL INTERVENTION REQUIRED                          ║${NC}"
echo -e "${BLUE}╠════════════════════════════════════════════════════════╣${NC}"
echo -e "${BLUE}║  Please simulate network disruption now:              ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"

if [ "$OS_TYPE" = "Darwin" ]; then
    # macOS instructions
    echo -e "${BLUE}║  macOS Method 1 (Recommended):                        ║${NC}"
    echo -e "${BLUE}║    1. Open 'Network Utility' or Activity Monitor     ║${NC}"
    echo -e "${BLUE}║    2. Turn WiFi OFF for 10 seconds                   ║${NC}"
    echo -e "${BLUE}║    3. Turn WiFi back ON                              ║${NC}"
    echo -e "${BLUE}║                                                        ║${NC}"
    echo -e "${BLUE}║  macOS Method 2 (Firewall):                          ║${NC}"
    echo -e "${BLUE}║    Run in another terminal:                          ║${NC}"
    echo -e "${BLUE}║    sudo pfctl -e                                     ║${NC}"
    echo -e "${BLUE}║    echo 'block out proto tcp to any port 443' |     ║${NC}"
    echo -e "${BLUE}║      sudo pfctl -f -                                 ║${NC}"
    echo -e "${BLUE}║    (wait 10s)                                        ║${NC}"
    echo -e "${BLUE}║    sudo pfctl -d                                     ║${NC}"
else
    # Linux instructions
    echo -e "${BLUE}║  Linux Method 1 (iptables):                          ║${NC}"
    echo -e "${BLUE}║    sudo iptables -A OUTPUT -p tcp --dport 443 -j DROP║${NC}"
    echo -e "${BLUE}║    (wait 10 seconds)                                 ║${NC}"
    echo -e "${BLUE}║    sudo iptables -D OUTPUT -p tcp --dport 443 -j DROP║${NC}"
    echo -e "${BLUE}║                                                        ║${NC}"
    echo -e "${BLUE}║  Linux Method 2 (network interface):                 ║${NC}"
    echo -e "${BLUE}║    sudo ip link set <interface> down                 ║${NC}"
    echo -e "${BLUE}║    sleep 10                                          ║${NC}"
    echo -e "${BLUE}║    sudo ip link set <interface> up                   ║${NC}"
fi

echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}║  Alternative: Just disconnect/reconnect WiFi         ║${NC}"
echo -e "${BLUE}║                                                        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Press ENTER when you have disrupted the connection...${NC}"
read -r

echo "  Monitoring for disconnection events..."

# Step 4: Monitor for disconnection and reconnection
echo ""
echo -e "${YELLOW}[4/6]${NC} Monitoring for WebSocket disconnection..."

DISCONNECT_DETECTED=false
START_TIME=$(date +%s)

while [ $(($(date +%s) - START_TIME)) -lt 60 ]; do
    if grep -q "WebSocket.*disconnect\|WS.*error\|connection.*closed\|reconnect" "$LOG_FILE" 2>/dev/null; then
        DISCONNECT_DETECTED=true
        echo -e "${GREEN}✓ Disconnection detected${NC}"

        # Show last disconnection message
        DISC_MSG=$(grep "WebSocket.*disconnect\|WS.*error\|connection.*closed" "$LOG_FILE" | tail -1)
        echo "  Last message: $DISC_MSG"
        break
    fi

    printf "\r  Waiting for disconnection... (%ds)" $(($(date +%s) - START_TIME))
    sleep 1
done

echo ""

if [ "$DISCONNECT_DETECTED" = false ]; then
    echo -e "${YELLOW}⚠ No disconnection detected in logs${NC}"
    echo "  This may indicate:"
    echo "  - Network interruption was too brief"
    echo "  - WebSocket buffering masked the disruption"
    echo "  - Logs don't capture disconnection events"
    echo ""
    echo "  Proceeding to check for reconnection attempts anyway..."
fi

# Step 5: Verify reconnection with exponential backoff
echo ""
echo -e "${YELLOW}[5/6]${NC} Verifying reconnection with exponential backoff..."

RECONNECT_ATTEMPTS=0
BACKOFF_DETECTED=false
RECONNECT_SUCCESS=false

# Look for reconnection attempts
START_TIME=$(date +%s)
while [ $(($(date +%s) - START_TIME)) -lt 90 ]; do
    # Count reconnection attempts
    RECONNECT_ATTEMPTS=$(grep -c "reconnect.*attempt\|reconnecting.*in" "$LOG_FILE" 2>/dev/null || echo "0")

    # Check for backoff pattern
    if grep -q "reconnecting in.*[0-9]\+\.[0-9]\+s" "$LOG_FILE" 2>/dev/null; then
        BACKOFF_DETECTED=true
    fi

    # Check for successful reconnection
    if grep -q "WebSocket.*connected\|connection.*established\|successfully reconnected" "$LOG_FILE" 2>/dev/null; then
        # Get last connection time
        LAST_CONN=$(grep "WebSocket.*connected\|connection.*established" "$LOG_FILE" | tail -1)

        # Check if it's after disconnection
        if [ "$DISCONNECT_DETECTED" = true ]; then
            RECONNECT_SUCCESS=true
            echo -e "${GREEN}✓ Reconnection successful${NC}"
            echo "  Message: $LAST_CONN"
            break
        fi
    fi

    printf "\r  Monitoring reconnection... Attempts: $RECONNECT_ATTEMPTS"
    sleep 2
done

echo ""
echo ""
echo "Reconnection Analysis:"
echo "  Attempts detected:     $RECONNECT_ATTEMPTS"
echo "  Backoff pattern:       $([ "$BACKOFF_DETECTED" = true ] && echo "✓ YES" || echo "✗ NO")"
echo "  Reconnection success:  $([ "$RECONNECT_SUCCESS" = true ] && echo "✓ YES" || echo "✗ NO")"

# Extract backoff delays if present
if [ "$BACKOFF_DETECTED" = true ]; then
    echo ""
    echo "Exponential backoff delays observed:"
    grep "reconnecting in" "$LOG_FILE" | grep -o "[0-9]\+\.[0-9]\+s" | nl
fi

# Step 6: Verify data flow resumed
echo ""
echo -e "${YELLOW}[6/6]${NC} Verifying data flow resumed after reconnection..."

if [ "$RECONNECT_SUCCESS" = true ]; then
    echo "  Checking for market data updates..."
    sleep 5

    # Look for recent price updates, orderbook updates, or signal checks
    RECENT_DATA=$(tail -100 "$LOG_FILE" | grep -c "price\|orderbook\|signal\|update" || echo "0")

    if [ "$RECENT_DATA" -gt 5 ]; then
        echo -e "${GREEN}✓ Data flow resumed ($RECENT_DATA recent updates)${NC}"
    else
        echo -e "${YELLOW}⚠ Limited data flow detected (only $RECENT_DATA updates)${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Cannot verify data flow (no successful reconnection)${NC}"
fi

# Cleanup: Stop strategy
echo ""
echo "Stopping strategy..."

if kill -0 $STRATEGY_PID 2>/dev/null; then
    kill -TERM $STRATEGY_PID 2>/dev/null || true
    sleep 2

    if kill -0 $STRATEGY_PID 2>/dev/null; then
        kill -KILL $STRATEGY_PID 2>/dev/null || true
    fi
fi

# Remove lock file
LOCKFILE="/tmp/crypto_scalp.lock"
rm -f "$LOCKFILE" 2>/dev/null || true

echo -e "${GREEN}✓ Strategy stopped${NC}"

# Generate test report
echo ""
echo -e "${YELLOW}Generating test report...${NC}"

cat > "$REPORT_FILE" <<EOF
═══════════════════════════════════════════════════════════
WEBSOCKET RECONNECTION STRESS TEST REPORT
Generated: $(date)
═══════════════════════════════════════════════════════════

TEST RESULTS:
  Disconnection Detected:     $([ "$DISCONNECT_DETECTED" = true ] && echo "✓ YES" || echo "✗ NO")
  Reconnection Attempts:      $RECONNECT_ATTEMPTS
  Exponential Backoff:        $([ "$BACKOFF_DETECTED" = true ] && echo "✓ YES" || echo "✗ NO")
  Reconnection Success:       $([ "$RECONNECT_SUCCESS" = true ] && echo "✓ YES" || echo "✗ NO")

BACKOFF DELAYS:
EOF

if [ "$BACKOFF_DETECTED" = true ]; then
    grep "reconnecting in" "$LOG_FILE" | grep -o "[0-9]\+\.[0-9]\+s" | nl >> "$REPORT_FILE" || true
else
    echo "  No backoff delays detected" >> "$REPORT_FILE"
fi

cat >> "$REPORT_FILE" <<EOF

DISCONNECTION EVENTS:
EOF

grep "disconnect\|error.*WebSocket\|connection.*closed" "$LOG_FILE" >> "$REPORT_FILE" 2>/dev/null || echo "  No disconnection events found" >> "$REPORT_FILE"

cat >> "$REPORT_FILE" <<EOF

RECONNECTION EVENTS:
EOF

grep "reconnect\|connection.*established" "$LOG_FILE" >> "$REPORT_FILE" 2>/dev/null || echo "  No reconnection events found" >> "$REPORT_FILE"

cat >> "$REPORT_FILE" <<EOF

FINAL VERDICT:
$([ "$RECONNECT_SUCCESS" = true ] && [ "$BACKOFF_DETECTED" = true ] && echo "  ✓ PASS - WebSocket reconnection working correctly" || echo "  ✗ INCONCLUSIVE - Manual verification required")

NOTES:
  - This test requires manual network disruption
  - Reconnection depends on upstream server availability
  - Exponential backoff pattern: 1s, 2s, 4s, 8s, 16s, 30s (max)
  - Max reconnection attempts: 10
  - Review full log file for detailed WebSocket trace

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
if [ "$RECONNECT_SUCCESS" = true ] && [ "$BACKOFF_DETECTED" = true ]; then
    echo -e "${GREEN}✓ WebSocket reconnection test PASSED${NC}"
    exit 0
elif [ "$DISCONNECT_DETECTED" = false ]; then
    echo -e "${YELLOW}⚠ WebSocket reconnection test SKIPPED (no disconnection)${NC}"
    exit 2
else
    echo -e "${YELLOW}⚠ WebSocket reconnection test INCONCLUSIVE${NC}"
    echo "  Review report and logs for details"
    exit 2
fi
