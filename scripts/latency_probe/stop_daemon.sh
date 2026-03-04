#!/bin/bash
# Stop the latency probe daemon
#
# Usage:
#   ./scripts/latency_probe/stop_daemon.sh nba
#   ./scripts/latency_probe/stop_daemon.sh ncaab
#   ./scripts/latency_probe/stop_daemon.sh both

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

LEAGUE="${1:-both}"
PID_FILE="$PROJECT_ROOT/data/latency_probe_${LEAGUE}.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "No PID file found for $LEAGUE probe"
    echo "It may not be running."
    exit 1
fi

PID=$(cat "$PID_FILE")

if ! ps -p "$PID" > /dev/null 2>&1; then
    echo "Process $PID is not running (stale PID file)"
    rm -f "$PID_FILE"
    exit 1
fi

echo "Stopping latency probe daemon ($LEAGUE, PID: $PID)..."

# Send SIGTERM for graceful shutdown
kill -TERM "$PID" 2>/dev/null || true

# Wait up to 30 seconds for graceful shutdown
for i in {1..30}; do
    if ! ps -p "$PID" > /dev/null 2>&1; then
        echo "✓ Probe stopped gracefully"
        rm -f "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# Force kill if still running
echo "Process did not stop gracefully, forcing..."
kill -KILL "$PID" 2>/dev/null || true
rm -f "$PID_FILE"
echo "✓ Probe stopped (forced)"
