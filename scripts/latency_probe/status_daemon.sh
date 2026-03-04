#!/bin/bash
# Check status of latency probe daemon
#
# Usage:
#   ./scripts/latency_probe/status_daemon.sh [nba|ncaab|both]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LEAGUES=("${1:-}")
if [ -z "${LEAGUES[0]}" ] || [ "${LEAGUES[0]}" = "all" ]; then
    LEAGUES=("nba" "ncaab" "both")
fi

echo "Latency Probe Daemon Status"
echo "============================"
echo ""

for league in "${LEAGUES[@]}"; do
    PID_FILE="$PROJECT_ROOT/data/latency_probe_${league}.pid"

    if [ ! -f "$PID_FILE" ]; then
        echo "[$league] Not running (no PID file)"
        continue
    fi

    PID=$(cat "$PID_FILE")

    if ps -p "$PID" > /dev/null 2>&1; then
        UPTIME=$(ps -o etime= -p "$PID" | tr -d ' ')
        MEM=$(ps -o rss= -p "$PID" | awk '{print int($1/1024)" MB"}')
        echo "[$league] ✓ Running (PID: $PID, Uptime: $UPTIME, Memory: $MEM)"

        # Check database size
        DB_FILE="$PROJECT_ROOT/data/probe_${league}.db"
        if [ -f "$DB_FILE" ]; then
            DB_SIZE=$(du -h "$DB_FILE" | cut -f1)
            echo "        Database: $DB_SIZE"
        fi
    else
        echo "[$league] ✗ Not running (stale PID: $PID)"
    fi
done

echo ""
echo "Recent logs:"
echo "------------"
LOG_DIR="$PROJECT_ROOT/logs/latency_probe"
if [ -d "$LOG_DIR" ]; then
    for league in "${LEAGUES[@]}"; do
        MAIN_LOG="$LOG_DIR/${league}_main.log"
        if [ -f "$MAIN_LOG" ]; then
            echo ""
            echo "[$league] Last 5 lines:"
            tail -5 "$MAIN_LOG" | sed 's/^/  /'
        fi
    done
else
    echo "  (No logs found)"
fi
