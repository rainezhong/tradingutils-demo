#!/bin/bash
#
# Real-Time Integration Test Monitor
#
# Monitors the integration test log in real-time with syntax highlighting
# for key events, errors, and metrics.
#
# Usage:
#   ./scripts/monitor_live.sh [LOG_FILE]
#
# If no log file is provided, auto-detects the most recent integration test log.
#
# Features:
#   - Color-coded output (entries, exits, errors, warnings)
#   - Highlights balance reconciliations
#   - Shows WebSocket reconnections
#   - Filters noise (configurable)
#   - Real-time tail with automatic restart if log rotates
#

set -euo pipefail

# ── Configuration ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$PROJECT_ROOT/logs"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ── Parse Arguments ──
LOG_FILE=""
SHOW_ALL=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --all)
            SHOW_ALL=true
            shift
            ;;
        --help)
            echo "Usage: $0 [LOG_FILE] [--all] [--help]"
            echo ""
            echo "Monitor integration test log in real-time with color highlighting."
            echo ""
            echo "Options:"
            echo "  LOG_FILE    Path to log file (auto-detects if not provided)"
            echo "  --all       Show all lines (default: filter to important events)"
            echo "  --help      Show this help message"
            exit 0
            ;;
        *)
            LOG_FILE="$1"
            shift
            ;;
    esac
done

# ── Auto-detect Log File ──
if [[ -z "$LOG_FILE" ]]; then
    # Find most recent integration_test_*.log
    if [[ -f "$LOGS_DIR/integration_test.pid" ]]; then
        # Use the log from current run
        PID=$(cat "$LOGS_DIR/integration_test.pid")
        LOG_FILE=$(ls -t "$LOGS_DIR"/integration_test_*.log 2>/dev/null | head -n 1)
    else
        # Find most recent
        LOG_FILE=$(ls -t "$LOGS_DIR"/integration_test_*.log 2>/dev/null | head -n 1)
    fi

    if [[ -z "$LOG_FILE" ]]; then
        echo "ERROR: No integration test log found in $LOGS_DIR" >&2
        echo "Start a test with: ./scripts/run_integration_test.sh" >&2
        exit 1
    fi

    echo "Auto-detected log file: $LOG_FILE"
    echo ""
fi

# Validate log file
if [[ ! -f "$LOG_FILE" ]]; then
    echo "ERROR: Log file not found: $LOG_FILE" >&2
    exit 1
fi

# ── Colorize Function ──
colorize() {
    local line="$1"

    # Critical errors
    if [[ "$line" =~ CRITICAL|FATAL ]]; then
        echo -e "${RED}${BOLD}$line${NC}"
        return
    fi

    # Errors
    if [[ "$line" =~ ERROR ]]; then
        echo -e "${RED}$line${NC}"
        return
    fi

    # Warnings
    if [[ "$line" =~ WARNING|WARN ]]; then
        echo -e "${YELLOW}$line${NC}"
        return
    fi

    # Entry events
    if [[ "$line" =~ "Submitting BUY"|"Entry order filled"|"Successfully entered" ]]; then
        echo -e "${GREEN}$line${NC}"
        return
    fi

    # Exit events
    if [[ "$line" =~ "Submitting SELL"|"Exit order filled"|"Successfully exited" ]]; then
        echo -e "${CYAN}$line${NC}"
        return
    fi

    # Balance reconciliation
    if [[ "$line" =~ "Balance"|"drift" ]]; then
        echo -e "${MAGENTA}$line${NC}"
        return
    fi

    # WebSocket events
    if [[ "$line" =~ "WebSocket"|"Reconnecting"|"REST fallback" ]]; then
        echo -e "${BLUE}$line${NC}"
        return
    fi

    # Trade outcomes
    if [[ "$line" =~ "Trade closed"|"Position closed"|"profit"|"pnl" ]]; then
        # Color based on profit/loss
        if [[ "$line" =~ "profit=+"|"pnl=+" ]] || [[ "$line" =~ "profit=[0-9]"|"pnl=[0-9]" ]]; then
            echo -e "${GREEN}${BOLD}$line${NC}"
        elif [[ "$line" =~ "profit=-"|"pnl=-" ]]; then
            echo -e "${RED}$line${NC}"
        else
            echo -e "${BOLD}$line${NC}"
        fi
        return
    fi

    # Important events
    if [[ "$line" =~ "Signal detected"|"Opportunity"|"Stopped"|"Started" ]]; then
        echo -e "${BOLD}$line${NC}"
        return
    fi

    # Default (only show if --all)
    if [[ "$SHOW_ALL" == "true" ]]; then
        echo "$line"
    fi
}

# ── Display Header ──
clear
echo -e "${BOLD}=== Integration Test Live Monitor ===${NC}"
echo -e "Log file: ${CYAN}$LOG_FILE${NC}"
echo -e "Time: $(date)"
echo ""
echo -e "${GREEN}Legend:${NC}"
echo -e "  ${GREEN}Green${NC}    - Entry events, wins"
echo -e "  ${CYAN}Cyan${NC}     - Exit events"
echo -e "  ${MAGENTA}Magenta${NC}  - Balance reconciliation"
echo -e "  ${BLUE}Blue${NC}     - WebSocket events"
echo -e "  ${YELLOW}Yellow${NC}   - Warnings"
echo -e "  ${RED}Red${NC}      - Errors, losses"
echo ""
echo -e "${BOLD}--- Live Feed ---${NC}"
echo ""

# ── Monitor Loop ──
# Use tail -f to follow log file, colorize output
tail -f "$LOG_FILE" | while IFS= read -r line; do
    colorize "$line"
done

# If we get here, tail exited (log rotated or deleted)
echo ""
echo -e "${YELLOW}Log file monitoring stopped${NC}"
