# Latency Probe Daemon - Continuous Runner

Run the latency probe 24/7 with automatic restarts and system sleep prevention.

## Quick Start

### Start the daemon
```bash
# Run NBA probe continuously
./scripts/latency_probe/start_daemon.sh nba

# Run NCAAB probe continuously
./scripts/latency_probe/start_daemon.sh ncaab

# Run BOTH NBA and NCAAB
./scripts/latency_probe/start_daemon.sh both
```

### Check status
```bash
./scripts/latency_probe/status_daemon.sh
```

### Stop the daemon
```bash
./scripts/latency_probe/stop_daemon.sh nba
./scripts/latency_probe/stop_daemon.sh ncaab
./scripts/latency_probe/stop_daemon.sh both
```

## What It Does

The daemon:
- ✅ **Runs continuously** - 24/7 monitoring of live games
- ✅ **Auto-restarts** - Every 2 hours for database rotation
- ✅ **Prevents sleep** - Uses `caffeinate` to keep macOS awake
- ✅ **Error recovery** - Automatically restarts if it crashes
- ✅ **Logging** - All output saved to `logs/latency_probe/`
- ✅ **Multi-league** - Can run NBA and NCAAB simultaneously

## File Locations

```
tradingutils/
├── data/
│   ├── probe_nba.db              # NBA data (continuously appended)
│   ├── probe_ncaab.db            # NCAAB data (continuously appended)
│   ├── latency_probe_nba.pid     # Process ID for NBA daemon
│   └── latency_probe_ncaab.pid   # Process ID for NCAAB daemon
└── logs/
    └── latency_probe/
        ├── nba_daemon.log        # Main daemon output (NBA)
        ├── nba_main.log          # Session logs (NBA)
        ├── nba_20260222_143022.log  # Individual session (NBA)
        ├── ncaab_daemon.log      # Main daemon output (NCAAB)
        ├── ncaab_main.log        # Session logs (NCAAB)
        └── ncaab_20260222_143022.log  # Individual session (NCAAB)
```

## Monitoring

### View live logs
```bash
# NBA
tail -f logs/latency_probe/nba_daemon.log

# NCAAB
tail -f logs/latency_probe/ncaab_daemon.log
```

### Check database size
```bash
ls -lh data/probe_*.db
```

### View recent activity
```bash
./scripts/latency_probe/status_daemon.sh
```

## How It Works

```
┌─────────────────────────────────────────┐
│  start_daemon.sh                        │
│  (launches with caffeinate)             │
└──────────────┬──────────────────────────┘
               │
               v
┌─────────────────────────────────────────┐
│  run_continuous.sh                      │
│  (infinite loop)                        │
└──────────────┬──────────────────────────┘
               │
               v
┌─────────────────────────────────────────┐
│  run.py nba/ncaab                       │
│  (2-hour sessions)                      │
│  • Poll ESPN every 5s                   │
│  • Poll Kalshi every 0.5s               │
│  • Record all data to SQLite            │
└──────────────┬──────────────────────────┘
               │
               v (auto-restart every 2h)
               │
               └──> Back to run_continuous.sh
```

## Session Rotation

The probe runs in 2-hour sessions, then restarts. This:
- Prevents memory leaks from building up
- Allows log rotation
- Commits all pending database writes
- Refreshes API connections

**You don't need to do anything** - it happens automatically.

## Error Handling

If the probe crashes or exits with an error:
1. Error is logged to the main log file
2. 60-second wait before restart
3. Automatic restart
4. Process continues

This handles:
- Network timeouts
- API rate limits
- Unexpected exceptions
- Database locks

## System Sleep Prevention

The daemon uses `caffeinate -i` which prevents macOS from:
- Going to sleep
- Stopping network activity
- Throttling background processes

**Note:** This keeps your Mac awake! If you close the laptop lid, it will still run.

To allow sleep, stop the daemon first:
```bash
./scripts/latency_probe/stop_daemon.sh both
```

## Resource Usage

**Typical usage (per league):**
- CPU: <5% (mostly idle, spikes during polls)
- Memory: ~50-100 MB
- Network: ~10 KB/s (polling APIs)
- Disk I/O: ~1-2 MB/hour (database writes)

**Running both NBA + NCAAB:**
- CPU: <10%
- Memory: ~100-200 MB
- Safe to run 24/7 on any modern Mac

## When to Run

### NBA Season
- **Regular Season:** October - April
- **Peak times:** 7pm-11pm ET (most games)
- **Off-season:** No live games, probe will run but find nothing

### NCAAB Season
- **Regular Season:** November - March
- **March Madness:** Peak activity!
- **Peak times:** 7pm-11pm ET, plus afternoon games
- **Off-season:** Summer (no games)

**Recommendation:** Run continuously during season, stop during off-season.

## Data Collection

With the probe running 24/7:

**Per game (2 hours):**
- ~14,400 Kalshi snapshots (0.5s interval)
- ~1,440 ESPN polls (5s interval)
- ~500 KB - 2 MB database growth

**Per day (typical 10-15 games):**
- ~5-30 MB database growth

**Per season:**
- NBA: ~2-6 GB total
- NCAAB: ~3-8 GB total (more games)

**Disk space:** Make sure you have 10-20 GB free for a full season.

## Analysis

While the daemon runs, you can analyze data at any time:

```bash
# Analyze current data (doesn't stop daemon)
python3 scripts/latency_probe/run.py analyze --db data/probe_nba.db
```

The probe uses SQLite with WAL mode, so analysis can run concurrently with data collection.

## Auto-Start on Boot (Optional)

If you want the probe to start automatically when your Mac boots:

1. Create a LaunchAgent plist:
```bash
cat > ~/Library/LaunchAgents/com.tradingutils.latencyprobe.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tradingutils.latencyprobe</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/raine/tradingutils/scripts/latency_probe/start_daemon.sh</string>
        <string>both</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/raine/tradingutils/logs/latency_probe/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/raine/tradingutils/logs/latency_probe/launchd_err.log</string>
</dict>
</plist>
EOF
```

2. Load the LaunchAgent:
```bash
launchctl load ~/Library/LaunchAgents/com.tradingutils.latencyprobe.plist
```

3. Unload (to stop auto-start):
```bash
launchctl unload ~/Library/LaunchAgents/com.tradingutils.latencyprobe.plist
```

**Note:** I recommend running manually during season rather than auto-start, so you have control.

## Troubleshooting

### Daemon won't start
```bash
# Check for stale PID files
rm -f data/latency_probe_*.pid

# Try starting again
./scripts/latency_probe/start_daemon.sh nba
```

### No data being collected
```bash
# Check if games are live
curl -s "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard" | grep '"state":"in"'

# View logs
tail -50 logs/latency_probe/nba_daemon.log
```

### High CPU usage
```bash
# Check process stats
./scripts/latency_probe/status_daemon.sh

# If stuck, restart
./scripts/latency_probe/stop_daemon.sh both
./scripts/latency_probe/start_daemon.sh both
```

### Database locked errors
```bash
# Stop all analysis scripts
# The daemon handles locks gracefully, but manual queries can conflict

# Wait a few seconds, try again
```

## Best Practices

1. **Start at season beginning** - Capture full season data
2. **Monitor weekly** - Check status and disk space
3. **Analyze monthly** - Look for patterns and trends
4. **Stop in off-season** - No need to run when no games
5. **Backup databases** - Copy `data/probe_*.db` periodically

## Example: Full Season Setup

```bash
# Start of NBA season (October)
./scripts/latency_probe/start_daemon.sh nba

# Check weekly
./scripts/latency_probe/status_daemon.sh

# Analyze monthly
python3 scripts/latency_probe/run.py analyze --db data/probe_nba.db

# End of season (April)
./scripts/latency_probe/stop_daemon.sh nba

# Backup the data
cp data/probe_nba.db data/probe_nba_2026_season.db
```

You'll end up with a full season of latency data to determine if NBA latency arb is viable! 🏀📊
