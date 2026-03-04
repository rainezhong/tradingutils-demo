#!/bin/bash
# Check status of all running services

echo "================================================================================"
echo "TRADING SYSTEM STATUS"
echo "================================================================================"
echo ""

# NBA Underdog Strategy
echo "NBA UNDERDOG STRATEGY:"
echo "--------------------------------------------------------------------------------"
if pgrep -f "run_nba_underdog.py" > /dev/null; then
    PID=$(pgrep -f "run_nba_underdog.py")
    UPTIME=$(ps -p $PID -o etime= | tr -d ' ')
    MEM=$(ps -p $PID -o rss= | awk '{printf "%.1f MB", $1/1024}')
    echo "✅ RUNNING (PID: $PID, Uptime: $UPTIME, Memory: $MEM)"
    echo "   Command: $(ps -p $PID -o command= | head -c 100)..."
else
    echo "❌ NOT RUNNING"
fi
echo ""

# NBA Probe
echo "NBA PROBE:"
echo "--------------------------------------------------------------------------------"
if pgrep -f "latency_probe/run.py nba" > /dev/null; then
    PID=$(pgrep -f "latency_probe/run.py nba")
    UPTIME=$(ps -p $PID -o etime= | tr -d ' ')
    MEM=$(ps -p $PID -o rss= | awk '{printf "%.1f MB", $1/1024}')
    DB_SIZE=$(ls -lh data/probe_nba.db 2>/dev/null | awk '{print $5}')
    SNAPSHOTS=$(sqlite3 data/probe_nba.db "SELECT COUNT(*) FROM kalshi_snapshots" 2>/dev/null)
    echo "✅ RUNNING (PID: $PID, Uptime: $UPTIME, Memory: $MEM)"
    echo "   DB: probe_nba.db ($DB_SIZE, $SNAPSHOTS snapshots)"
else
    echo "❌ NOT RUNNING"
fi
echo ""

# NCAAB Probe
echo "NCAAB PROBE:"
echo "--------------------------------------------------------------------------------"
if pgrep -f "latency_probe/run.py ncaab" > /dev/null; then
    PID=$(pgrep -f "latency_probe/run.py ncaab")
    UPTIME=$(ps -p $PID -o etime= | tr -d ' ')
    MEM=$(ps -p $PID -o rss= | awk '{printf "%.1f MB", $1/1024}')
    DB_SIZE=$(ls -lh data/probe_ncaab.db 2>/dev/null | awk '{print $5}')
    SNAPSHOTS=$(sqlite3 data/probe_ncaab.db "SELECT COUNT(*) FROM kalshi_snapshots" 2>/dev/null)
    echo "✅ RUNNING (PID: $PID, Uptime: $UPTIME, Memory: $MEM)"
    echo "   DB: probe_ncaab.db ($DB_SIZE, $SNAPSHOTS snapshots)"
else
    echo "❌ NOT RUNNING"
fi
echo ""

# Caffeinate Status
echo "CAFFEINATE STATUS:"
echo "--------------------------------------------------------------------------------"
if pgrep -f "caffeinate" > /dev/null; then
    CAFFEINE_PIDS=$(pgrep -f "caffeinate")
    echo "✅ Caffeinate processes running:"
    ps -p $CAFFEINE_PIDS -o pid,etime,command | tail -n +2
else
    echo "⚠️  NO caffeinate processes found"
    echo "   Processes may sleep if system goes idle"
fi
echo ""

# Recent Activity
echo "RECENT ACTIVITY (Last 10 log lines):"
echo "--------------------------------------------------------------------------------"
echo "NBA Probe:"
tail -3 logs/latency_probe/nba_*.log 2>/dev/null | tail -1
echo ""
echo "NCAAB Probe:"
tail -3 logs/latency_probe/ncaab_*.log 2>/dev/null | tail -1
echo ""

echo "================================================================================"
echo "To restart with caffeinate: bash scripts/start_all_with_caffeinate.sh"
echo "To stop all: pkill -f run_nba_underdog.py; pkill -f 'latency_probe/run.py'"
echo "================================================================================"
