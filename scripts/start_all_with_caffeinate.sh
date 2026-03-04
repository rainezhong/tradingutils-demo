#!/bin/bash
# Start NBA strategy and probes with caffeinate to prevent sleep

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "Starting all services with caffeinate..."

# Check if already running
if pgrep -f "run_nba_underdog.py" > /dev/null; then
    echo "NBA underdog strategy already running"
else
    echo "Starting NBA underdog strategy (15-20¢ optimal range, 3-6h timing)..."
    nohup caffeinate -i python3 scripts/run_nba_underdog.py \
        --position-size 5 \
        --max-positions 10 \
        --min-price 15 \
        --max-price 20 \
        > logs/nba_underdog.log 2>&1 &
    echo "NBA strategy started (PID: $!)"
fi

# Check if NBA probe running
if pgrep -f "latency_probe/run.py nba" > /dev/null; then
    echo "NBA probe already running"
else
    echo "Starting NBA probe..."
    nohup caffeinate -i bash scripts/latency_probe/run_continuous.sh nba \
        > logs/nba_probe_runner.log 2>&1 &
    echo "NBA probe started (PID: $!)"
fi

# Check if NCAAB probe running
if pgrep -f "latency_probe/run.py ncaab" > /dev/null; then
    echo "NCAAB probe already running"
else
    echo "Starting NCAAB probe..."
    nohup caffeinate -i bash scripts/latency_probe/run_continuous.sh ncaab \
        > logs/ncaab_probe_runner.log 2>&1 &
    echo "NCAAB probe started (PID: $!)"
fi

echo ""
echo "All services started!"
echo ""
echo "To check status:"
echo "  ps aux | grep -E '(run_nba_underdog|latency_probe)' | grep -v grep"
echo ""
echo "To stop all:"
echo "  pkill -f run_nba_underdog.py"
echo "  pkill -f 'latency_probe/run.py'"
echo ""
