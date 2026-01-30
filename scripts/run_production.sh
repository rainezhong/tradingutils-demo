#!/bin/bash
# Production runner for Kalshi Market Data Collector
# Usage: ./scripts/run_production.sh [--daemon]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
PID_FILE="$PROJECT_ROOT/logs/scheduler.pid"
LOG_FILE="$PROJECT_ROOT/logs/scheduler.log"

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Function to start the scheduler
start_scheduler() {
    local daemon_mode=$1

    # Check if already running
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Scheduler is already running (PID: $pid)"
            exit 1
        fi
        rm -f "$PID_FILE"
    fi

    if [ "$daemon_mode" = "true" ]; then
        echo "Starting scheduler as daemon..."
        nohup python main.py schedule > "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
        echo "Scheduler started (PID: $(cat $PID_FILE))"
        echo "Log file: $LOG_FILE"
    else
        echo "Starting scheduler in foreground (Ctrl+C to stop)..."
        python main.py schedule
    fi
}

# Function to stop the scheduler
stop_scheduler() {
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping scheduler (PID: $pid)..."
            kill "$pid"
            rm -f "$PID_FILE"
            echo "Scheduler stopped"
        else
            echo "Scheduler not running (stale PID file)"
            rm -f "$PID_FILE"
        fi
    else
        echo "Scheduler is not running (no PID file)"
    fi
}

# Function to check status
check_status() {
    if [ -f "$PID_FILE" ]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Scheduler is running (PID: $pid)"
            echo
            python main.py monitor
        else
            echo "Scheduler is not running (stale PID file)"
        fi
    else
        echo "Scheduler is not running"
    fi
}

# Function to run a full pipeline
run_pipeline() {
    echo "Running full data pipeline..."
    python main.py pipeline --skip-errors
}

# Parse arguments
case "${1:-start}" in
    start)
        start_scheduler "false"
        ;;
    --daemon|-d)
        start_scheduler "true"
        ;;
    stop)
        stop_scheduler
        ;;
    restart)
        stop_scheduler
        sleep 2
        start_scheduler "true"
        ;;
    status)
        check_status
        ;;
    pipeline)
        run_pipeline
        ;;
    *)
        echo "Usage: $0 {start|--daemon|stop|restart|status|pipeline}"
        echo
        echo "Commands:"
        echo "  start      Start scheduler in foreground"
        echo "  --daemon   Start scheduler as background daemon"
        echo "  stop       Stop the scheduler daemon"
        echo "  restart    Restart the scheduler daemon"
        echo "  status     Check scheduler status"
        echo "  pipeline   Run a full data pipeline once"
        exit 1
        ;;
esac
