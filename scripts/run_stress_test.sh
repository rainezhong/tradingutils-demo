#!/bin/bash
# Phase 3 Stress Test Runner
# Tests recovery mechanisms under aggressive failure scenarios

set -e

# Configuration
DURATION_HOURS=3
DURATION_SEC=$((DURATION_HOURS * 3600))
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
LOG_DIR="/Users/raine/tradingutils/logs"
LOG_FILE="$LOG_DIR/stress_test_${TIMESTAMP}.log"
METRICS_FILE="$LOG_DIR/stress_test_${TIMESTAMP}_metrics.json"
CONFIG_FILE="$LOG_DIR/stress_test_${TIMESTAMP}_config.yaml"
REPORT_FILE="$LOG_DIR/stress_test_${TIMESTAMP}_report.md"

echo "=== Phase 3 Stress Test Runner ==="
echo "Duration: ${DURATION_HOURS}h"
echo "Log file: $LOG_FILE"
echo "Metrics file: $METRICS_FILE"
echo ""

# Generate stress test configuration (more aggressive)
cat > "$CONFIG_FILE" <<EOF
# Phase 3 Stress Test Configuration
# Generated automatically - AGGRESSIVE SETTINGS

# Signal feed
signal_feed: "all"

# Spot move detection (lower threshold for more signals)
spot_lookback_sec: 3.0
min_spot_move_usd: 15.0

# Momentum filter (disabled for more trades)
enable_momentum_filter: false

# Entry filters (wider range for more opportunities)
min_ttx_sec: 180  # 3 min
max_ttx_sec: 900  # 15 min
min_kalshi_spread_cents: 0
max_entry_price_cents: 75
min_entry_price_cents: 25

# Exit timing (faster exits for more turnover)
exit_delay_sec: 15.0
max_hold_sec: 30.0

# Position sizing (SMALL for testing)
contracts_per_trade: 2
max_open_positions: 2  # Allow 2 simultaneous positions
max_total_exposure_usd: 10.0

# Cooldown (shorter for more trades)
cooldown_sec: 15.0

# Execution
slippage_buffer_cents: 1
exit_slippage_cents: 0
fill_timeout_sec: 3.0

# Order TTL
max_entry_order_age_seconds: 30.0

# Fill optimization
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

# Regime filter (disabled)
regime_window_sec: 60.0
regime_osc_threshold: 0.0

# Risk
max_daily_loss_usd: 50.0  # Higher for stress test
paper_mode: true  # MUST be true

# Liquidity protection
min_exit_bid_depth: 5
max_adverse_exit_cents: 35
skip_exit_on_thin_liquidity: true

# Pre-entry liquidity check
min_entry_bid_depth: 5  # Lower threshold for more trades
enable_entry_liquidity_check: true

# Stop-loss exit
stop_loss_cents: 15
stop_loss_delay_sec: 2.0  # Slightly delayed to avoid whipsaw
enable_stop_loss: true

# Reversal exit
enable_reversal_exit: true
reversal_exit_delay_sec: 2.0
min_reversal_strength_usd: 10.0

# Position flip (ENABLED for stress test)
enable_position_flip: true

# Emergency exit
emergency_exit_ttx_sec: 90
use_market_order_on_emergency: true

# Stuckness filter (disabled)
enable_stuckness_filter: false

# Statistical exits (all enabled)
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

# Orderbook fallback
enable_orderbook_rest_fallback: true
orderbook_rest_poll_interval_sec: 1.0
orderbook_rest_poll_depth: 10

# Intervals
scan_interval_sec: 30.0  # Faster scanning
detector_interval_sec: 0.1

# Symbols
symbols:
  - BTCUSDT
EOF

echo "Created config: $CONFIG_FILE"
echo ""

# Start metrics collector
echo "Starting metrics collector..."
python3 /Users/raine/tradingutils/scripts/collect_metrics.py \
    --log-file "$LOG_FILE" \
    --output "$METRICS_FILE" \
    --interval 600 &  # Every 10 minutes
METRICS_PID=$!
echo "Metrics collector started (PID $METRICS_PID)"
echo "Collecting metrics every 10 minutes to $METRICS_FILE"
echo ""

# Start stress injector
echo "Starting stress injector..."
python3 /Users/raine/tradingutils/scripts/stress_injector.py \
    --log-file "$LOG_FILE" \
    --metrics "$METRICS_FILE" \
    --duration $DURATION_SEC &
INJECTOR_PID=$!
echo "Stress injector started (PID $INJECTOR_PID)"
echo ""

# Start crypto-scalp strategy
echo "Starting crypto-scalp strategy..."
echo "Press Ctrl+C to stop early (or wait ${DURATION_HOURS}h for automatic stop)"
echo ""
echo "Log file: $LOG_FILE"
echo "Config file: $CONFIG_FILE"
echo "Metrics collector PID: $METRICS_PID"
echo "Stress injector PID: $INJECTOR_PID"
echo ""
echo "=== Strategy Starting at $(date) ==="
echo ""

# Run strategy (no timeout command on macOS - monitor will kill it)
cd /Users/raine/tradingutils
python3 main.py run crypto-scalp \
    --config "$CONFIG_FILE" \
    --dry-run \
    > "$LOG_FILE" 2>&1 &
STRATEGY_PID=$!
echo "Strategy started (PID $STRATEGY_PID)"

# Monitor function
monitor_stress_test() {
    local start_time=$(date +%s)
    local end_time=$((start_time + DURATION_SEC))

    while [ $(date +%s) -lt $end_time ]; do
        sleep 60

        # Check if strategy is still running
        if ! kill -0 $STRATEGY_PID 2>/dev/null; then
            echo ""
            echo "⚠️  Strategy process died unexpectedly!"
            break
        fi

        # Show progress
        local now=$(date +%s)
        local elapsed=$((now - start_time))
        local remaining=$((DURATION_SEC - elapsed))
        local pct=$((elapsed * 100 / DURATION_SEC))
        echo "Progress: ${pct}% complete, ${remaining}s remaining..."
    done
}

echo ""
echo "Monitoring for ${DURATION_HOURS}h (${DURATION_SEC}s)..."
echo "Use 'tail -f $LOG_FILE' to watch progress in another terminal"
echo ""

# Monitor in foreground
monitor_stress_test

# Cleanup
echo ""
echo "=== Stress test duration complete ==="
echo "Stopping processes..."

kill $STRATEGY_PID 2>/dev/null || true
kill $METRICS_PID 2>/dev/null || true
kill $INJECTOR_PID 2>/dev/null || true

sleep 2

echo ""
echo "Generating stress test report..."
python3 /Users/raine/tradingutils/scripts/analyze_stress_test.py \
    --log-file "$LOG_FILE" \
    --metrics "$METRICS_FILE" \
    --output "$REPORT_FILE"

echo ""
echo "=== Phase 3 Stress Test Complete ==="
echo "Report: $REPORT_FILE"
echo ""
