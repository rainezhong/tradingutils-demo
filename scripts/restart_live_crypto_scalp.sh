#!/bin/bash
# Restart crypto-scalp live trading with proper logging
# Created: 2026-02-28

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "========================================"
echo "CRYPTO SCALP LIVE TRADING RESTART"
echo "========================================"
echo ""

# Check for existing process
EXISTING_PID=$(pgrep -f "crypto-scalp.*--live" || echo "")

if [ -n "$EXISTING_PID" ]; then
    echo "⚠️  Found existing live process: PID $EXISTING_PID"
    echo ""
    ps -p "$EXISTING_PID" -o pid,etime,command=
    echo ""
    read -p "Kill this process and restart? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Stopping PID $EXISTING_PID..."
        kill "$EXISTING_PID"
        sleep 2
        echo "✓ Stopped"
    else
        echo "Cancelled. Not starting new process."
        exit 1
    fi
fi

echo ""
echo "Starting crypto-scalp in LIVE mode with logging..."
echo ""
echo "Config: strategies/configs/crypto_scalp_live.yaml"
echo "  - paper_mode: false (REAL MONEY)"
echo "  - Position size: 1 contract (~$0.50/trade)"
echo "  - Max daily loss: $20"
echo ""

# Show config preview
echo "Current config:"
grep -E "^(paper_mode|contracts_per_trade|max_daily_loss|signal_feed|min_spot_move):" \
    strategies/configs/crypto_scalp_live.yaml | sed 's/^/  /'
echo ""

read -p "Start live trading? [y/N] " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 1
fi

echo ""
echo "🚀 Starting live trading..."
echo ""

# Start with proper logging (logs will auto-save to file now)
python3 main.py run crypto-scalp --live \
    --config strategies/configs/crypto_scalp_live.yaml

echo ""
echo "Session ended."
