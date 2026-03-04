#!/bin/bash
# Start latency probe as a daemon with caffeinate
#
# This script:
# 1. Uses caffeinate to prevent sleep
# 2. Runs the probe continuously in the background
# 3. Logs to files
# 4. Creates a PID file for easy stopping
#
# Usage:
#   ./scripts/latency_probe/start_daemon.sh nba     # Run NBA only
#   ./scripts/latency_probe/start_daemon.sh ncaab   # Run NCAAB only
#   ./scripts/latency_probe/start_daemon.sh both    # Run both (default)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LEAGUE="${1:-both}"
PID_FILE="$PROJECT_ROOT/data/latency_probe_${LEAGUE}.pid"
LOG_DIR="$PROJECT_ROOT/logs/latency_probe"
mkdir -p "$LOG_DIR"
mkdir -p "$(dirname "$PID_FILE")"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "Latency probe ($LEAGUE) is already running (PID: $OLD_PID)"
        echo "To stop it, run: ./scripts/latency_probe/stop_daemon.sh $LEAGUE"
        exit 1
    else
        echo "Stale PID file found, removing..."
        rm -f "$PID_FILE"
    fi
fi

echo "Starting latency probe daemon ($LEAGUE)..."
echo "Logs: $LOG_DIR"
echo "PID file: $PID_FILE"

# Start with caffeinate to prevent sleep
nohup caffeinate -i -w $$ bash -c "
    cd '$PROJECT_ROOT'
    exec '$SCRIPT_DIR/run_continuous.sh' '$LEAGUE'
" > "$LOG_DIR/${LEAGUE}_daemon.log" 2>&1 &

DAEMON_PID=$!
echo "$DAEMON_PID" > "$PID_FILE"

echo "✓ Latency probe daemon started (PID: $DAEMON_PID)"
echo ""
echo "Commands:"
echo "  View logs:  tail -f $LOG_DIR/${LEAGUE}_daemon.log"
echo "  Stop:       ./scripts/latency_probe/stop_daemon.sh $LEAGUE"
echo "  Status:     ./scripts/latency_probe/status_daemon.sh $LEAGUE"
