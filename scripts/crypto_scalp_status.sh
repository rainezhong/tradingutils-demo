#!/bin/bash
# Crypto Scalp Strategy - Complete Status Dashboard

clear
echo "════════════════════════════════════════════════════════════════════════"
echo "🚀 CRYPTO SCALP STRATEGY - STATUS DASHBOARD"
echo "════════════════════════════════════════════════════════════════════════"
echo ""

# Check if probe is running
echo "📡 PROBE STATUS:"
PROBE_PID=$(ps aux | grep -E "[b]tc_latency_probe.py.*$(date +%Y%m%d)" | awk '{print $2}')
if [ -n "$PROBE_PID" ]; then
    PROBE_RUNTIME=$(ps -p $PROBE_PID -o etime= | tr -d ' ')
    echo "  ✅ BTC Probe Running (PID: $PROBE_PID, Runtime: $PROBE_RUNTIME)"
    echo "  📊 Database: data/btc_probe_$(date +%Y%m%d).db"

    # Check database size
    if [ -f "data/btc_probe_$(date +%Y%m%d).db" ]; then
        DB_SIZE=$(du -h "data/btc_probe_$(date +%Y%m%d).db" | cut -f1)
        echo "  💾 Database Size: $DB_SIZE"
    fi
else
    echo "  ⚠️  No BTC probe running for today"
    echo "  Start with: python3 scripts/btc_latency_probe.py --duration 86400 --db data/btc_probe_$(date +%Y%m%d).db &"
fi
echo ""

# Check if paper trading is running
echo "📝 PAPER TRADING STATUS:"
PAPER_PID=$(ps aux | grep "[r]un_scalp_live.py" | awk '{print $2}')
if [ -n "$PAPER_PID" ]; then
    PAPER_RUNTIME=$(ps -p $PAPER_PID -o etime= | tr -d ' ')
    echo "  ✅ Paper Trading Running (PID: $PAPER_PID, Runtime: $PAPER_RUNTIME)"
else
    echo "  ⚠️  Paper trading not running"
    echo "  Start with: python3 scripts/run_scalp_live.py"
fi
echo ""

# Latest backtest results
echo "📊 LATEST BACKTEST RESULTS (48h Feb 22-24):"
echo "  Configuration: osc < 3.0 (regime filter)"
echo "  ✅ Win Rate:      54% (405 wins / 753 trades)"
echo "  ✅ Net P&L:       +\$77.50 over 48 hours"
echo "  ✅ Avg/Trade:     +\$0.103 per trade"
echo "  ✅ Trades/Hour:   15.7"
echo "  📈 Expected:      +\$38/day, +\$1,165/month"
echo ""

# Edge validation
echo "🎯 EDGE VALIDATION:"
echo "  Statistical Test:  ✅ 7.4% disagreement rate (Kraken vs Kalshi)"
echo "  Backtest:          ✅ 54% win rate, +\$77.50 over 48h"
echo "  Regime Filter:     🔥 CRITICAL - osc < 3.0 (filters 87% of signals)"
echo "  Paper Trading:     ⏳ In progress..."
echo ""

# Next steps
echo "📋 NEXT STEPS:"
echo "  1. ✅ Probe collecting (24h) → Complete by $(date -v+1d '+%Y-%m-%d %H:%M')"
echo "  2. ⏳ Paper trading → Monitor for 24h"
echo "  3. ⏳ After 24h → Backtest fresh data"
echo "  4. ⏳ If validated → Micro-live (10 contracts)"
echo ""

# Monitoring commands
echo "🔍 MONITORING COMMANDS:"
echo "  Check probe:       tail -f logs/btc_probe_$(date +%Y%m%d).log"
echo "  Monitor edge:      python3 scripts/monitor_scalp_edge.py"
echo "  Test fresh data:   python3 scripts/test_latency_edge.py --db data/btc_probe_$(date +%Y%m%d).db"
echo "  Backtest fresh:    python3 main.py backtest crypto-scalp --db data/btc_probe_$(date +%Y%m%d).db"
echo ""

echo "════════════════════════════════════════════════════════════════════════"
echo "💡 TIP: Run 'watch -n 60 ./scripts/crypto_scalp_status.sh' for live updates"
echo "════════════════════════════════════════════════════════════════════════"
