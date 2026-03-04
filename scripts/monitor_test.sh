#!/bin/bash
# Real-time monitoring script for crypto scalp test
# Shows live stats as the test runs

echo "=========================================="
echo "Crypto Scalp Test Monitor"
echo "=========================================="
echo ""
echo "This will show real-time stats from the running test."
echo "Press Ctrl+C to stop monitoring (strategy keeps running)."
echo ""
echo "Looking for latest log file..."

# Find the most recent log file
LOGFILE=$(ls -t logs/*.log 2>/dev/null | head -1)

if [ -z "$LOGFILE" ]; then
    echo "No log file found. Start the strategy first:"
    echo "  python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_test.yaml"
    exit 1
fi

echo "Monitoring: $LOGFILE"
echo ""

# Function to calculate stats
show_stats() {
    echo "=========================================="
    echo "Live Stats ($(date +%H:%M:%S))"
    echo "=========================================="

    # Signals detected
    signals=$(grep -c "Signal detected" "$LOGFILE" 2>/dev/null || echo "0")
    echo "📊 Signals detected: ${signals}"

    # Momentum spike filters
    filtered=$(grep -c "MOMENTUM SPIKE FILTER" "$LOGFILE" 2>/dev/null || echo "0")
    if [ "$filtered" -gt 0 ]; then
        filter_pct=$(awk "BEGIN {printf \"%.1f\", (${filtered}/(${signals}+${filtered}))*100}")
        echo "🚫 Ultra-high momentum filtered: ${filtered} (${filter_pct}%)"
        echo ""
        echo "Recent filtered signals:"
        grep "MOMENTUM SPIKE FILTER" "$LOGFILE" | tail -3 | sed 's/^/  /'
    fi

    echo ""

    # Trades
    entries=$(grep -c "Placing entry order" "$LOGFILE" 2>/dev/null || echo "0")
    exits=$(grep -c "EXIT" "$LOGFILE" 2>/dev/null || echo "0")
    echo "📈 Trades: ${entries} entries, ${exits} exits"

    # Win/loss
    wins=$(grep -c "profit" "$LOGFILE" 2>/dev/null || echo "0")
    losses=$(grep -c "loss" "$LOGFILE" 2>/dev/null || echo "0")

    if [ $((wins + losses)) -gt 0 ]; then
        wr=$(awk "BEGIN {printf \"%.1f\", (${wins}/(${wins}+${losses}))*100}")
        echo "✅ Wins: ${wins}"
        echo "❌ Losses: ${losses}"
        echo "📊 Win rate: ${wr}%"
    fi

    echo ""
    echo "Recent trades:"
    grep -E "(entry|EXIT)" "$LOGFILE" | tail -5 | sed 's/^/  /'

    echo ""
    echo "=========================================="
}

# Initial stats
show_stats

echo ""
echo "Watching for updates... (refresh every 10s)"
echo ""

# Monitor loop
while true; do
    sleep 10
    clear
    show_stats
done
