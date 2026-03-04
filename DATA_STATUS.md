# Trading System - Data Status

**Last Updated:** February 24, 2026 - 6:30 PM PST

---

## 🎯 Summary

**Total Data Storage:** ~4.8 GB across 14 databases + CSV files

**Active Collection:**
- ✅ NBA Markets: 1.2M snapshots, 53 hours coverage
- ✅ NCAAB Markets: 1.2M snapshots, 53 hours coverage
- ✅ BTC Orderbook: 1.1M snapshots, 49 hours coverage
- ✅ NBA Strategy: Running with optimized 15-20¢ config

---

## 📊 NBA Basketball Data

### Live Collection (probe_nba.db)

**Status:** ✅ COLLECTING (PID 85391)
- **Size:** 192 MB
- **Snapshots:** 1,201,336
- **Markets:** 34 current NBA games
- **Coverage:** 53.4 hours (continuously tracking)
- **Top tracked games:**
  - BOS @ DEN (53,415 snapshots, 25h tracking)
  - CLE @ MIL (53,415 snapshots, 25h tracking)
  - SAC @ HOU (53,414 snapshots, 25h tracking)
- **Settlements:** 0 (all games are future, will settle over next 2 weeks)

### Historical Data (CSV files)

**nba_ev_raw.csv** (960 KB)
- **Games:** 155 settled games
- **Rows:** 5,547 time-series snapshots
- **Time range:** January 6-26, 2026
- **Contains:** Opening prices, settlement results, volume data
- **Use case:** Historical analysis, not used by current strategy

**nba_underdog_parameter_test.csv** (11 KB)
- **Games:** 155 settled games
- **Format:** One row per game with open/high/low/settlement
- **Use case:** Parameter sensitivity testing (used for analysis)
- **Quality:** ⭐⭐⭐⭐⭐ Excellent

**nba_historical_candlesticks.csv** (6 MB)
- **Records:** 57,716 candlestick snapshots
- **Contains:** Full price history with timestamps
- **Use case:** Backtesting, price movement analysis

### NBA Strategy Status

**Running Strategy:** 15-20¢ Optimal Config
- **PID:** 62290 (since 1:59 AM)
- **Price range:** 15-20¢ (changed from 10-30¢)
- **Stop loss:** 22¢
- **Entry window:** 3-6 hours before game
- **Position size:** 5 contracts
- **Current opportunities:** 0 (all games >6h away)

---

## 🏀 NCAAB Basketball Data

### Live Collection (probe_ncaab.db)

**Status:** ✅ COLLECTING (PID 85422)
- **Size:** 208 MB
- **Snapshots:** 1,223,952
- **Markets:** 78 current NCAAB games
- **Coverage:** 53.4 hours
- **Settlements:** 0 (will settle over next 2 weeks)

### Historical Data

**ncaab_underdog_analysis.csv** (8.1 KB)
- **Games:** 90 settled NCAAB games
- **Date range:** Feb 20-23, 2026 (3 days)
- **Quality:** ⭐⭐⭐⭐⭐ Excellent
- **Key finding:** 15-20¢ range shows 83% win rate (small sample)

**ncaab_settlements_markets.csv** (5.3 KB)
- **Games:** 62 settled games
- **Source:** markets.db snapshots
- **Quality:** ⭐⭐ Poor (suspicious win rates)
- **Status:** Not used for analysis

### NCAAB Strategy Status

**NOT DEPLOYED** - Waiting for more data
- Need: 100+ more settled games
- ETA: 2-4 weeks of collection
- Plan: Deploy if edges hold at 200+ game sample

---

## ₿ Bitcoin / Crypto Data

### Live BTC Orderbook Collection (btc_ob_48h.db)

**Status:** ✅ COLLECTING (PID 48494, since Sunday 9 AM)
- **Size:** 2.1 GB (largest database!)
- **Duration:** 49.4 hours continuous
- **Data collected:**
  - Kalshi orderbook: 1,094,656 snapshots (197 markets)
  - Binance trades: 15,205,532 trades
  - Coinbase trades: 1,380,968 trades
  - Kraken snapshots: 44,924 snapshots
  - Kraken trades: 141,049 trades
- **Tables:**
  - `kalshi_orderbook` - Full L2 orderbook (top 20 levels)
  - `binance_l2` - Binance L2 snapshots
  - `coinbase_l2` - Coinbase L2 snapshots
  - `binance_trades` - Trade-by-trade data
  - `coinbase_trades` - Trade-by-trade data
- **Use case:** Crypto scalp strategy, orderflow analysis

### Historical Crypto Data

**btc_probe_l2_1h.db** (56 MB)
- Kalshi orderbook: 22,366 snapshots
- CEX L2 data: ~29k snapshots
- 1 hour recording session

**btc_probe_merged.db** (13 MB)
- Historical probe data
- 38,269 Kalshi snapshots
- 17 settlements

---

## 📈 Other Data Sources

### Historical Markets Database (markets.db)

**Size:** 2.2 GB (second largest!)
- **Markets:** 4,842,634 market records
- **Snapshots:** 810,057 price snapshots
- **Quality:** Mixed (some settlement data unreliable)
- **Use case:** Historical research, not primary data source

### Analysis Results

**nba_param_sensitivity_results.csv** (8.1 KB)
- 90 parameter combinations tested
- Shows 15-20¢ optimal (+38.4% ROI)

**nba_underdog_grid_search.csv** (9.5 KB)
- Backtest suite grid search results
- Limited by probe data (only 6-8 trades)

---

## 🔄 Active Data Collection Processes

| Process | PID | Status | Runtime | Data Target |
|---------|-----|--------|---------|-------------|
| NBA Probe | 85391 | ✅ Running | 6:06 PM start | probe_nba.db |
| NCAAB Probe | 85422 | ✅ Running | 6:06 PM start | probe_ncaab.db |
| BTC Orderbook | 48494 | ✅ Running | 2.5 days | btc_ob_48h.db |
| NBA Strategy | 62290 | ✅ Running | Since 1:59 AM | Live trading (15-20¢) |

### Auto-Restart Configuration

**NBA/NCAAB Probes:**
- Duration: 2 hours per session
- Auto-restart: Yes (via run_continuous.sh)
- Caffeinate: Yes (prevents sleep)
- Logs: `logs/latency_probe/`

**BTC Probe:**
- Duration: 48 hours
- Manual management
- Caffeinate: Yes

---

## 📊 Data Quality Assessment

### Excellent Quality (⭐⭐⭐⭐⭐)
- ✅ `nba_underdog_parameter_test.csv` - 155 games, clean settlements
- ✅ `ncaab_underdog_analysis.csv` - 90 games, verified settlements
- ✅ `probe_nba.db` - Live data, comprehensive
- ✅ `btc_ob_48h.db` - Rich orderbook + trade data

### Good Quality (⭐⭐⭐⭐)
- ✅ `probe_ncaab.db` - Live data, comprehensive
- ✅ `nba_ev_raw.csv` - Time-series data
- ✅ Historical probe databases

### Poor Quality (⭐⭐)
- ⚠️ `ncaab_settlements_markets.csv` - Suspicious win rates
- ⚠️ `markets.db` - Mixed quality, some bad settlement data

---

## 🎯 Data Gaps & Collection Priorities

### High Priority
1. **NBA Settlement Data** - Need 50+ more settled games for validation
   - Current: 155 historical games
   - Target: 200+ games
   - ETA: Ongoing (probe collecting)

2. **NCAAB Settlement Data** - Need 110+ more games
   - Current: 90 games
   - Target: 200+ games
   - ETA: 2-4 weeks

### Medium Priority
3. **Long-term NBA tracking** - Monitor price movements 15+ days before games
   - Current: 53 hours max
   - Need: Multi-week tracking
   - Purpose: Validate 3-6h entry timing

4. **BTC Historical Settlement Data** - More settled crypto markets
   - Current: 3 settlements in btc_ob_48h.db
   - Need: 50+ settlements

### Low Priority
5. **Additional sports** - NFL, NHL, etc.
6. **International basketball** - Euroleague, etc.

---

## 💾 Storage Management

### Current Usage by Category

**Live Probes:** ~2.5 GB
- btc_ob_48h.db: 2.1 GB
- probe_nba.db: 192 MB
- probe_ncaab.db: 208 MB

**Historical Markets:** 2.2 GB
- markets.db: 2.2 GB (oldest, not actively used)

**Analysis Data:** ~100 MB
- CSV files: ~50 MB
- Small probe DBs: ~50 MB

### Cleanup Recommendations

**Can Archive:**
- Old probe sessions (btc_probe_l2.db, etc.) - 100+ MB
- Historical candlesticks (duplicates) - 50+ MB
- markets.db (if not needed) - 2.2 GB!

**Keep Active:**
- probe_nba.db, probe_ncaab.db (current collection)
- btc_ob_48h.db (rich orderbook data)
- CSV files (analysis inputs)

---

## 🔮 Data Roadmap

### This Week
- ✅ Continue NBA/NCAAB probe collection
- ✅ Monitor NBA strategy with optimized 15-20¢ config
- 📊 Collect settlement data as games complete

### Next 2 Weeks
- 📊 Accumulate 50+ NBA settlements from probe
- 📊 Accumulate 100+ NCAAB settlements
- 📈 Validate parameter sensitivity findings

### Month 2+
- 🎯 Deploy NCAAB strategy if edges hold
- 📊 Long-term price movement validation
- 🔬 Expand to other sports if profitable

---

## 📝 Data Access Commands

### Query Live Data

```bash
# NBA current opportunities
python3 -m mcp_servers.research query_db \
    --sql "SELECT ticker, yes_bid, yes_ask, close_time FROM kalshi_snapshots WHERE ticker LIKE 'KXNBAGAME-%' GROUP BY ticker" \
    --database probe_nba.db

# NCAAB current opportunities
python3 -m mcp_servers.research query_db \
    --sql "SELECT ticker, yes_bid, yes_ask FROM kalshi_snapshots WHERE ticker LIKE 'KXNCAAMBGAME-%' GROUP BY ticker" \
    --database probe_ncaab.db

# BTC orderbook depth
python3 -m mcp_servers.research query_db \
    --sql "SELECT ticker, best_bid, best_ask, bid_depth, ask_depth FROM kalshi_orderbook ORDER BY ts DESC LIMIT 10" \
    --database btc_ob_48h.db
```

### Check Collection Status

```bash
# View status
bash scripts/status_check.sh

# Check database sizes
du -h data/*.db | sort -h

# Check recent probe logs
tail -f logs/latency_probe/nba_*.log
```

---

## 📊 Summary Statistics

| Data Type | Count | Size | Quality | Status |
|-----------|-------|------|---------|--------|
| NBA Snapshots | 1.2M | 192 MB | ⭐⭐⭐⭐⭐ | Active |
| NCAAB Snapshots | 1.2M | 208 MB | ⭐⭐⭐⭐⭐ | Active |
| BTC Orderbook | 1.1M | 2.1 GB | ⭐⭐⭐⭐⭐ | Active |
| NBA Settled Games | 155 | 11 KB | ⭐⭐⭐⭐⭐ | Static |
| NCAAB Settled Games | 90 | 8 KB | ⭐⭐⭐⭐⭐ | Static |
| Historical Markets | 4.8M | 2.2 GB | ⭐⭐⭐ | Archive |

**Total:** ~4.8 GB, 3 active collection processes, excellent data quality for live systems

---

**Data infrastructure is healthy and collecting high-quality market data continuously. NBA strategy running on optimized 15-20¢ configuration with 3-6h entry timing.**
