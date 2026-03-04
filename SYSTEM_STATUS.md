# Trading System Status

**Last Updated:** February 24, 2026 - 2:00 AM PST

---

## ✅ All Systems Running

### NBA Underdog Strategy
- **Status:** ✅ RUNNING (OPTIMIZED!)
- **PID:** 62290
- **Started:** Feb 24, 2:00 AM PST
- **Memory:** ~36 MB
- **Configuration:**
  - **Price range: 15-20¢** (OPTIMAL - changed from 10-30¢!)
  - **Entry window: 3-6 hours before game**
  - Position size: 5 contracts
  - Max positions: 10
  - Stop loss: 22¢ (validated as optimal)
- **Expected ROI: +38.4%** (vs -6.55% with old config)
- **Caffeinate:** ✅ Wrapped

### NBA Probe
- **Status:** ✅ RUNNING
- **PID:** 73799
- **Uptime:** 1 hour, 17 minutes
- **Memory:** 29.4 MB
- **Database:** probe_nba.db (111 MB, 710,845 snapshots)
- **Restarts:** Every 2 hours (automatic)
- **Caffeinate:** ✅ Wrapped (PID 18245)

### NCAAB Probe
- **Status:** ✅ RUNNING
- **PID:** 74572
- **Uptime:** 1 hour, 16 minutes
- **Memory:** 26.5 MB
- **Database:** probe_ncaab.db (116 MB, 723,487 snapshots)
- **Restarts:** Every 2 hours (automatic)
- **Caffeinate:** ✅ Wrapped (PID 18246)

---

## 📊 Data Collection Progress

### NBA
- **Snapshots Collected:** 710,845
- **Markets Tracked:** 20 current markets
- **Settlement Data:** 558 historical games (from candlesticks)
- **Status:** Ready for trading

### NCAAB
- **Snapshots Collected:** 723,487
- **Markets Tracked:** 44 current markets
- **Settlement Data:** 90 historical games (limited)
- **Status:** Collecting data, not yet validated

---

## 🔧 System Management

### Check Status
```bash
bash scripts/status_check.sh
```

### View Logs
```bash
# NBA Strategy (no dedicated log yet)
ps -p 43473 -o command

# NBA Probe
tail -f logs/latency_probe/nba_*.log

# NCAAB Probe
tail -f logs/latency_probe/ncaab_*.log
```

### Stop All Services
```bash
pkill -f run_nba_underdog.py
pkill -f 'latency_probe/run.py'
pkill -f 'caffeinate -w'
```

### Restart All Services
```bash
bash scripts/start_all_with_caffeinate.sh
```

---

## 📈 Current Opportunities

### NBA Markets in Profitable Buckets
Based on latest probe data (7 markets):

| Game | Side | Price | Bucket | Expected EV | Liquidity |
|------|------|-------|--------|-------------|-----------|
| HOU @ SAC | HOU (NO) | 16¢ | 15-20¢ | +8.57¢ | 4,737 OI |
| HOU @ SAC | SAC (YES) | 17¢ | 15-20¢ | +8.57¢ | 3,432 OI |
| CLE @ MIL | CLE (NO) | 27¢ | 25-30¢ | +4.98¢ | 794 OI |
| CLE @ MIL | MIL (YES) | 28¢ | 25-30¢ | +4.98¢ | 398 OI |
| BOS @ PHX | BOS (NO) | 29¢ | 25-30¢ | +4.98¢ | 34,726 OI |
| BOS @ PHX | PHX (YES) | 29¢ | 25-30¢ | +4.98¢ | 5,276 OI |
| CHI @ CHA | CHI (YES) | 30¢ | 25-30¢ | +4.98¢ | 5,461 OI |

**Strategy is actively trading these with 1 contract positions.**

---

## 🎯 Strategy Performance

### NBA Underdog (Updated Feb 23, 9:32 PM)

**Current Settings (OPTIMIZED):**
- **Entry timing: 3-6 hours before game** (capital efficiency!)
- Position size: 5 contracts (up from 1)
- Max positions: 10 concurrent (up from 5)
- Price range: 10-30¢ (covering all profitable buckets)
- Stop loss: 22¢ (optimal)

**To check positions:**
```bash
# Strategy should maintain internal state
# Check logs or use API to query open positions
```

---

## ⚙️ Automatic Features

### Probe Auto-Restart
Both probes run for 2 hours then automatically restart:
- Prevents memory leaks
- Fresh connections
- Log rotation

### Caffeinate Protection
All processes wrapped with `caffeinate -w`:
- System won't sleep while processes run
- Prevents interrupted data collection
- Ensures continuous trading

---

## 📁 Key Files

### Scripts
- `scripts/start_all_with_caffeinate.sh` - Start all services
- `scripts/status_check.sh` - Check system status
- `scripts/run_nba_underdog.py` - NBA strategy runner
- `scripts/latency_probe/run_continuous.sh` - Probe runner
- `scripts/collect_ncaab_from_probe.py` - Settlement collector

### Logs
- `logs/latency_probe/nba_*.log` - NBA probe logs
- `logs/latency_probe/ncaab_*.log` - NCAAB probe logs

### Data
- `data/probe_nba.db` - NBA market snapshots
- `data/probe_ncaab.db` - NCAAB market snapshots
- `data/ncaab_underdog_analysis.csv` - NCAAB settlements (90 games)

---

## 🔔 Monitoring

### Daily Checks

```bash
# 1. Check all systems running
bash scripts/status_check.sh

# 2. Collect NCAAB settlements (if any new)
python3 scripts/collect_ncaab_from_probe.py --db data/probe_ncaab.db --output data/ncaab_ongoing.csv

# 3. Check database sizes
ls -lh data/probe_*.db

# 4. View recent probe activity
tail -20 logs/latency_probe/nba_*.log
tail -20 logs/latency_probe/ncaab_*.log
```

### Weekly Analysis

```bash
# Re-analyze NCAAB edges as more games settle
python3 -m jupyter notebook notebooks/ncaab_actual_edges.ipynb

# Compare to NBA
python3 -m jupyter notebook notebooks/nba_vs_ncaab_edge_analysis.ipynb
```

---

## 🚨 Troubleshooting

### Process Died
```bash
# Check what's running
bash scripts/status_check.sh

# Restart all
bash scripts/start_all_with_caffeinate.sh
```

### System Going to Sleep
```bash
# Check caffeinate wrappers
ps aux | grep caffeinate | grep -v grep

# Re-wrap if needed
caffeinate -w <PID> &
```

### Database Too Large
```bash
# Check sizes
du -h data/*.db

# If probe DB > 1GB, consider archiving old snapshots
# (Keep last 7 days worth of data)
```

---

## 📊 Next Steps

### This Week
- ✅ NBA strategy running with OPTIMAL 3-6h timing window
- ✅ Both probes collecting data continuously
- ✅ Updated to 5 contract positions (capital efficiency)
- 📊 Monitor first entries when games reach 3-6h window
- 📊 Collect NCAAB settlements as they happen

### Week 2-4
- 📈 Verify 3-6h timing improves capital efficiency (30x target)
- 📈 Scale positions if performing well (5 → 10 contracts)
- 📊 Re-analyze NCAAB with 150+ total games
- 🎯 Deploy NCAAB if edges hold

---

*System is healthy and running normally. All caffeinate protections in place.*
