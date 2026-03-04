#!/bin/bash
# Continuous latency probe runner with caffeinate (macOS)
#
# Usage:
#   ./scripts/latency_probe/run_continuous.sh nba
#   ./scripts/latency_probe/run_continuous.sh ncaab
#   ./scripts/latency_probe/run_continuous.sh both

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

LEAGUE="${1:-both}"
LOG_DIR="$PROJECT_ROOT/logs/latency_probe"
mkdir -p "$LOG_DIR"

# Determine which leagues to run
case "$LEAGUE" in
    nba)
        LEAGUES=("nba")
        ;;
    ncaab)
        LEAGUES=("ncaab")
        ;;
    both)
        LEAGUES=("nba" "ncaab")
        ;;
    *)
        echo "Usage: $0 {nba|ncaab|both}"
        exit 1
        ;;
esac

# Function to run a single probe session
run_probe() {
    local league=$1
    local db_path="data/probe_${league}.db"
    local log_file="$LOG_DIR/${league}_$(date +%Y%m%d_%H%M%S).log"

    echo "[$(date)] Starting $league probe (DB: $db_path, Log: $log_file)"

    # Run for 2 hours, then restart (allows graceful rotation)
    python3 scripts/latency_probe/run.py "$league" \
        --duration 7200 \
        --db "$db_path" \
        --espn-poll-interval 5.0 \
        --poll-interval 0.5 \
        2>&1 | tee -a "$log_file"

    echo "[$(date)] $league probe session completed"
}

# Function to run continuous loop for a league
run_continuous() {
    local league=$1

    while true; do
        echo "[$(date)] ========================================" | tee -a "$LOG_DIR/${league}_main.log"
        echo "[$(date)] Starting $league probe session" | tee -a "$LOG_DIR/${league}_main.log"

        # Run probe (will auto-restart every 2 hours)
        if run_probe "$league"; then
            echo "[$(date)] $league probe completed normally" | tee -a "$LOG_DIR/${league}_main.log"
        else
            echo "[$(date)] $league probe exited with error $?" | tee -a "$LOG_DIR/${league}_main.log"
            echo "[$(date)] Waiting 60s before restart..." | tee -a "$LOG_DIR/${league}_main.log"
            sleep 60
        fi

        # Small delay before restarting
        echo "[$(date)] Restarting in 10 seconds..." | tee -a "$LOG_DIR/${league}_main.log"
        sleep 10
    done
}

# Kill handler for graceful shutdown
cleanup() {
    echo ""
    echo "[$(date)] Received interrupt signal, shutting down..."
    kill $(jobs -p) 2>/dev/null || true
    wait
    echo "[$(date)] Shutdown complete"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Start probes in background
echo "=========================================="
echo "Latency Probe Continuous Runner"
echo "=========================================="
echo "Leagues: ${LEAGUES[*]}"
echo "Log directory: $LOG_DIR"
echo "Press Ctrl+C to stop"
echo "=========================================="
echo ""

# Run each league in background
pids=()
for league in "${LEAGUES[@]}"; do
    run_continuous "$league" &
    pids+=($!)
    echo "[$(date)] Started $league probe (PID: ${pids[-1]})"
done

# Wait for all background processes
wait "${pids[@]}"
