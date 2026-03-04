#!/bin/bash
#
# Phase 2 Integration Test Runner for Crypto Scalp Strategy
#
# Runs an 8-hour paper mode validation with conservative configuration
# to validate all critical bug fixes from March 2, 2026.
#
# Usage:
#   ./scripts/run_integration_test.sh [--duration-hours N] [--config PATH]
#
# Options:
#   --duration-hours N    Run for N hours (default: 8)
#   --config PATH         Use custom config YAML (default: auto-generated conservative config)
#   --no-metrics          Skip automatic metrics collection
#   --help                Show this help message
#
# Outputs:
#   - Timestamped log file in logs/integration_test_YYYY-MM-DD_HH-MM-SS.log
#   - Metrics JSON file in logs/integration_test_YYYY-MM-DD_HH-MM-SS_metrics.json
#   - PID file in logs/integration_test.pid
#
# The test will:
#   1. Create a conservative configuration (small size, strict filters)
#   2. Start the crypto-scalp strategy in paper mode
#   3. Start metrics collector in background (unless --no-metrics)
#   4. Monitor system health (WebSocket connections, balance drift)
#   5. Log all activity to timestamped file
#   6. Automatically stop after duration expires
#
# After completion, run:
#   python3 scripts/generate_integration_report.py logs/integration_test_YYYY-MM-DD_HH-MM-SS.log
#

set -euo pipefail

# ── Configuration ──
DURATION_HOURS=8
CUSTOM_CONFIG=""
ENABLE_METRICS=true
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOGS_DIR="$PROJECT_ROOT/logs"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
LOG_FILE="$LOGS_DIR/integration_test_${TIMESTAMP}.log"
METRICS_FILE="$LOGS_DIR/integration_test_${TIMESTAMP}_metrics.json"
PID_FILE="$LOGS_DIR/integration_test.pid"
CONFIG_FILE="$LOGS_DIR/integration_test_${TIMESTAMP}_config.yaml"

# ── Parse Arguments ──
while [[ $# -gt 0 ]]; do
    case $1 in
        --duration-hours)
            DURATION_HOURS="$2"
            shift 2
            ;;
        --config)
            CUSTOM_CONFIG="$2"
            shift 2
            ;;
        --no-metrics)
            ENABLE_METRICS=false
            shift
            ;;
        --help)
            head -n 30 "$0" | grep "^#" | sed 's/^# //'
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Use --help for usage information" >&2
            exit 1
            ;;
    esac
done

# ── Validate Environment ──
echo "=== Phase 2 Integration Test Runner ==="
echo "Duration: ${DURATION_HOURS}h"
echo "Log file: $LOG_FILE"
echo "Metrics file: $METRICS_FILE"
echo ""

# Check if already running
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if ps -p "$OLD_PID" > /dev/null 2>&1; then
        echo "ERROR: Integration test already running (PID $OLD_PID)" >&2
        echo "Stop it first or remove $PID_FILE" >&2
        exit 1
    else
        echo "Cleaning up stale PID file..."
        rm "$PID_FILE"
    fi
fi

# Ensure logs directory exists
mkdir -p "$LOGS_DIR"

# Check for .env file
if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
    echo "ERROR: Missing .env file with Kalshi credentials" >&2
    echo "Create .env with KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH" >&2
    exit 1
fi

# ── Generate Conservative Configuration ──
if [[ -n "$CUSTOM_CONFIG" ]]; then
    echo "Using custom config: $CUSTOM_CONFIG"
    CONFIG_FILE="$CUSTOM_CONFIG"
else
    echo "Generating conservative configuration..."
    cat > "$CONFIG_FILE" << 'EOF'
# Conservative Configuration for Phase 2 Integration Test
# Generated automatically by run_integration_test.sh

# Signal feed
signal_feed: "all"

# Spot move detection (conservative: higher threshold)
spot_lookback_sec: 5.0
min_spot_move_usd: 20.0  # Higher threshold to reduce noise

# Momentum filter
enable_momentum_filter: true
momentum_threshold: 0.8

# Entry filters
min_ttx_sec: 240  # 4 min to close (more conservative)
max_ttx_sec: 900  # 15 min window
min_kalshi_spread_cents: 0
max_entry_price_cents: 70  # Tighter range (was 75)
min_entry_price_cents: 30  # Tighter range (was 25)

# Exit timing
exit_delay_sec: 20.0
max_hold_sec: 35.0

# Position sizing (SMALL for testing)
contracts_per_trade: 2  # Minimal size
max_open_positions: 1
max_total_exposure_usd: 5.0  # Small exposure

# Cooldown
cooldown_sec: 30.0  # Longer cooldown to reduce overtrading

# Execution
slippage_buffer_cents: 1
exit_slippage_cents: 0
fill_timeout_sec: 3.0

# Order TTL (critical fix #1)
max_entry_order_age_seconds: 30.0

# Fill optimization (critical fix #2)
limit_order_timeout_sec: 1.5
market_order_fallback: true
max_fallback_slippage_cents: 5
fallback_min_edge_cents: 8

# Pre-flight market check
max_price_deviation_cents: 10
adaptive_price_threshold_cents: 5

# Volume filters
min_window_volume:
  binance: 0.5
  coinbase: 0.3
  kraken: 0.1
require_multi_exchange_confirm: true

# Regime filter (disabled for test)
regime_window_sec: 60.0
regime_osc_threshold: 0.0

# Risk
max_daily_loss_usd: 20.0  # Conservative daily loss limit
paper_mode: true  # MUST be true for integration test

# Liquidity protection (critical fixes #3, #4)
min_exit_bid_depth: 5  # Require decent liquidity to exit
max_adverse_exit_cents: 35
skip_exit_on_thin_liquidity: true

# Pre-entry liquidity check (critical fix #5)
min_entry_bid_depth: 10  # Strong protection against illiquid markets
enable_entry_liquidity_check: true

# Stop-loss exit (critical fix #6)
stop_loss_cents: 15
stop_loss_delay_sec: 0.0  # Immediate stop-loss
enable_stop_loss: true

# Reversal exit
enable_reversal_exit: true
reversal_exit_delay_sec: 2.0
min_reversal_strength_usd: 10.0

# Position flip (disabled for conservative test)
enable_position_flip: false

# Emergency exit
emergency_exit_ttx_sec: 90
use_market_order_on_emergency: true

# Stuckness filter (disabled for test)
enable_stuckness_filter: false

# Statistical exits (enabled for test)
enable_depth_momentum_exit: true
depth_drain_threshold: 0.4
depth_min_profit_cents: 3
depth_min_hold_sec: 5.0

enable_spread_reversion_exit: true
spread_reversion_multiplier: 2.0
spread_depth_threshold: 0.6

enable_volatility_adjusted_hold: true
high_vol_threshold: 50.0
low_vol_threshold: 15.0
accel_threshold: 100.0

enable_imbalance_reversal_exit: true
imbalance_reversal_threshold: 0.5
imbalance_velocity_threshold: 0.3

enable_divergence_exit: true
divergence_std_threshold: 30.0

# Account
kalshi_user: "env"

# Orderbook fallback (critical fix #7 - WebSocket reliability)
enable_orderbook_rest_fallback: true
orderbook_rest_poll_interval_sec: 1.0
orderbook_rest_poll_depth: 10

# Intervals
scan_interval_sec: 60.0
detector_interval_sec: 0.1

# Symbols
symbols:
  - BTCUSDT
EOF
    echo "Created config: $CONFIG_FILE"
fi

# ── Start Metrics Collector ──
METRICS_PID=""
if [[ "$ENABLE_METRICS" == "true" ]]; then
    echo ""
    echo "Starting metrics collector..."
    python3 "$SCRIPT_DIR/collect_metrics.py" \
        --log-file "$LOG_FILE" \
        --output "$METRICS_FILE" \
        --interval 3600 \
        >> "$LOG_FILE" 2>&1 &
    METRICS_PID=$!
    echo "Metrics collector started (PID $METRICS_PID)"
    echo "Collecting metrics every hour to $METRICS_FILE"
fi

# ── Start Strategy ──
echo ""
echo "Starting crypto-scalp strategy..."
echo "Press Ctrl+C to stop early (or wait ${DURATION_HOURS}h for automatic stop)"
echo ""
echo "Log file: $LOG_FILE"
echo "Config file: $CONFIG_FILE"
if [[ -n "$METRICS_PID" ]]; then
    echo "Metrics collector PID: $METRICS_PID"
fi
echo ""
echo "=== Strategy Starting at $(date) ==="
echo ""

# Start the strategy in background
cd "$PROJECT_ROOT"
python3 main.py run crypto-scalp \
    --config "$CONFIG_FILE" \
    >> "$LOG_FILE" 2>&1 &

STRATEGY_PID=$!
echo "$STRATEGY_PID" > "$PID_FILE"
echo "Strategy started (PID $STRATEGY_PID)"
echo ""

# ── Monitor and Timeout ──
DURATION_SEC=$((DURATION_HOURS * 3600))
ELAPSED=0
CHECK_INTERVAL=10

echo "Monitoring for ${DURATION_HOURS}h (${DURATION_SEC}s)..."
echo "Use 'tail -f $LOG_FILE' to watch progress in another terminal"
echo ""

# Cleanup function
cleanup() {
    echo ""
    echo "=== Stopping Integration Test ==="

    # Kill strategy
    if ps -p "$STRATEGY_PID" > /dev/null 2>&1; then
        echo "Stopping strategy (PID $STRATEGY_PID)..."
        kill "$STRATEGY_PID" 2>/dev/null || true
        sleep 2
        kill -9 "$STRATEGY_PID" 2>/dev/null || true
    fi

    # Kill metrics collector
    if [[ -n "$METRICS_PID" ]] && ps -p "$METRICS_PID" > /dev/null 2>&1; then
        echo "Stopping metrics collector (PID $METRICS_PID)..."
        kill "$METRICS_PID" 2>/dev/null || true
    fi

    # Remove PID file
    rm -f "$PID_FILE"

    echo ""
    echo "=== Integration Test Stopped at $(date) ==="
    echo ""
    echo "Logs saved to: $LOG_FILE"
    if [[ "$ENABLE_METRICS" == "true" ]]; then
        echo "Metrics saved to: $METRICS_FILE"
    fi
    echo ""
    echo "Generate report with:"
    echo "  python3 scripts/generate_integration_report.py $LOG_FILE"
    echo ""
}

# Register cleanup on exit
trap cleanup EXIT INT TERM

# Monitor loop
while [[ $ELAPSED -lt $DURATION_SEC ]]; do
    # Check if strategy is still running
    if ! ps -p "$STRATEGY_PID" > /dev/null 2>&1; then
        echo ""
        echo "ERROR: Strategy process died unexpectedly!" >&2
        echo "Check logs: $LOG_FILE" >&2
        exit 1
    fi

    # Check if metrics collector is still running (if enabled)
    if [[ -n "$METRICS_PID" ]] && ! ps -p "$METRICS_PID" > /dev/null 2>&1; then
        echo ""
        echo "WARNING: Metrics collector died, restarting..." >&2
        python3 "$SCRIPT_DIR/collect_metrics.py" \
            --log-file "$LOG_FILE" \
            --output "$METRICS_FILE" \
            --interval 3600 \
            >> "$LOG_FILE" 2>&1 &
        METRICS_PID=$!
    fi

    sleep $CHECK_INTERVAL
    ELAPSED=$((ELAPSED + CHECK_INTERVAL))

    # Progress update every 10 minutes
    if [[ $((ELAPSED % 600)) -eq 0 ]]; then
        HOURS_ELAPSED=$((ELAPSED / 3600))
        MINS_ELAPSED=$(((ELAPSED % 3600) / 60))
        REMAINING_SEC=$((DURATION_SEC - ELAPSED))
        HOURS_REMAINING=$((REMAINING_SEC / 3600))
        MINS_REMAINING=$(((REMAINING_SEC % 3600) / 60))

        echo "[$(date)] Progress: ${HOURS_ELAPSED}h ${MINS_ELAPSED}m elapsed, ${HOURS_REMAINING}h ${MINS_REMAINING}m remaining"
    fi
done

echo ""
echo "=== Duration Expired - Stopping Test ==="
# cleanup() will be called by trap
