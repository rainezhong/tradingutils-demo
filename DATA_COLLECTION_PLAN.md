# NCAAB Data Collection Plan

## Current Status

### ✅ What We Have

#### NBA Data (EXCELLENT)
- **558 settled games** with full price history
- Source: `data/nba_historical_candlesticks_full_corrected.csv`
- Date range: Dec 30, 2025 - Feb 1, 2026
- Quality: Minute-by-minute prices with settlements
- **Status: READY FOR DEPLOYMENT**

#### NCAAB Data (LIMITED)
- **90 settled games** from recent collection
- Source: `data/ncaab_underdog_analysis.csv`
- Date range: Feb 20-23, 2026 (3 days only!)
- By bucket:
  - 15-20¢: 6 games (83% win rate!)
  - 25-30¢: 17 games (65% win rate!)
  - 30-35¢: 21 games (71% win rate)
  - 35+¢: 41 games (39% win rate)
- **Status: PROMISING BUT INSUFFICIENT**

### ❌ What We Need

- **100+ more NCAAB settled games** for statistical confidence
- Especially need more in 10-20¢ bucket (only 6 games currently)
- Historical data going back 2-4 weeks minimum
- Ongoing collection to validate edge persistence

---

## Data Collection Strategy

### Phase 1: Historical Backfill (Immediate)

#### Goal
Collect 100+ settled NCAAB games from past 30 days

#### Method
Use new settlement collector script:

```bash
# Collect last 30 days of settled NCAAB games
python3 scripts/collect_ncaab_settlements.py --days 30 --csv data/ncaab_settlements_full.csv

# Check what we got
python3 scripts/collect_ncaab_settlements.py --days 30 --dry-run
```

#### Expected Outcome
- NCAAB season is active (Feb 2026)
- Expect 10-15 games per day
- 30 days = ~300-450 games
- Should give us 20-30 games in key 10-20¢ bucket

#### Timeline
- **Duration:** 10 minutes to run
- **Completion:** Today

---

### Phase 2: Ongoing Collection (2-4 Weeks)

#### Goal
Continuously collect new settled games as they happen

#### Method A: Cron Job (Recommended)
Set up hourly cron job to collect last 24 hours:

```bash
# Add to crontab (runs every hour)
0 * * * * cd /Users/raine/tradingutils && python3 scripts/collect_ncaab_settlements.py --days 1 --csv data/ncaab_settlements.csv
```

#### Method B: Continuous Mode
Run collector in background:

```bash
# Run continuously, check every hour
python3 scripts/collect_ncaab_settlements.py --continuous --interval 3600 --days 1 > logs/ncaab_collector.log 2>&1 &
```

#### Expected Outcome
- Collect 10-15 new games daily
- After 2 weeks: 140-210 more games
- After 4 weeks: 280-420 more games
- Combined with backfill: 400-600 total games!

#### Timeline
- **Start:** After Phase 1 completes
- **Duration:** 2-4 weeks
- **Review:** Weekly to check data quality

---

### Phase 3: Validation & Analysis (Weekly)

#### Goal
Re-run edge analysis weekly to validate findings

#### Method
Use existing analysis notebooks:

```bash
# Re-run NCAAB edge analysis
python3 -m mcp.research.run_notebook ncaab_actual_edges.ipynb

# Compare to NBA
python3 -m mcp.research.run_notebook nba_vs_ncaab_edge_analysis.ipynb
```

#### Success Criteria
After collecting 200+ total NCAAB games:
- **If 365% ROI holds:** Deploy cautiously
- **If regresses to 100-200% ROI:** Still excellent, deploy
- **If drops to 30-50% ROI:** Comparable to NBA, deploy conservatively
- **If drops below 20% ROI:** Wide spreads make it unprofitable, NBA only

#### Timeline
- **Week 1:** Analyze after backfill (~400 games)
- **Week 2:** Re-analyze (450+ games)
- **Week 3:** Re-analyze (500+ games)
- **Week 4:** Final decision on deployment

---

## Alternative Data Sources

### 1. Kalshi Historical API (Explored)
- **Status:** Limited - only 3 NCAAB games with snapshots
- **Verdict:** Not sufficient

### 2. Markets.db (Explored)
- **Status:** 1,292 NCAAB markets but poor settlement tracking
- **Verdict:** Unreliable for historical analysis

### 3. Probe Databases (Active)
- **probe_nba.db:** 678K snapshots of current NBA markets
- **probe_ncaab.db:** 691K snapshots of current NCAAB markets
- **Status:** Excellent for current opportunities, will have settlements in 1-2 weeks
- **Verdict:** Continue probes, will supplement historical data

### 4. Scrape Past Prices (Future Enhancement)
Could potentially:
- Use Wayback Machine for Kalshi historical prices
- Scrape other prediction markets (PredictIt, etc.) for validation
- **Verdict:** Not needed if API backfill works

### 5. Other Sports Data
Same methodology can be applied to:
- **NCAA Women's Basketball (NCAAWB):** Similar dynamics to men's
- **NBA G-League:** Lower liquidity, potentially higher edges
- **International Basketball:** FIBA, EuroLeague, etc.
- **Other College Sports:** Football, hockey, baseball

---

## Data Storage & Organization

### File Structure

```
data/
├── ncaab_settlements_full.csv       # Historical backfill (30 days)
├── ncaab_settlements.csv            # Ongoing collection (appended daily)
├── ncaab_settlements.db             # SQLite database (optional)
├── nba_historical_candlesticks_full_corrected.csv  # NBA data (existing)
└── probe_ncaab.db                   # Real-time probe data

notebooks/
├── ncaab_actual_edges.ipynb         # Edge analysis (updated weekly)
└── nba_vs_ncaab_edge_analysis.ipynb # Comparison (updated weekly)
```

### Database Schema (Optional)

If using SQLite instead of CSV:

```sql
CREATE TABLE ncaab_settlements (
    ticker TEXT PRIMARY KEY,
    event_ticker TEXT NOT NULL,
    close_time TEXT NOT NULL,
    result TEXT NOT NULL,
    yes_open_price REAL,
    no_open_price REAL,
    underdog_side TEXT NOT NULL,
    underdog_price REAL NOT NULL,
    underdog_won INTEGER NOT NULL,
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_ncaab_close_time ON ncaab_settlements(close_time);
CREATE INDEX idx_ncaab_underdog_price ON ncaab_settlements(underdog_price);
```

Benefits:
- Easier queries
- No duplicates (PRIMARY KEY)
- Fast lookups (indexes)
- Can track collection timestamp

---

## Monitoring & Quality Checks

### Daily Checks
1. **Collection Status**
   ```bash
   tail -20 logs/ncaab_collector.log  # Check for errors
   wc -l data/ncaab_settlements.csv   # Count total games
   ```

2. **Data Quality**
   ```bash
   # Check for duplicates
   python3 scripts/check_data_quality.py --csv data/ncaab_settlements.csv
   ```

3. **Sample Size by Bucket**
   ```python
   import pandas as pd
   df = pd.read_csv('data/ncaab_settlements.csv')
   print(df.groupby(pd.cut(df['underdog_price'], [0,10,15,20,25,30,35,100])).size())
   ```

### Weekly Analysis
1. Run edge analysis notebooks
2. Compare to previous week
3. Check for edge decay or improvement
4. Update strategy parameters if needed

---

## Deployment Decision Tree

```
After 2 weeks of collection (~200-250 total games):
│
├─ 10-20¢ sample size ≥ 20 games?
│  ├─ YES → Proceed to edge check
│  └─ NO → Collect 2 more weeks
│
├─ Edge check: ROI in 10-20¢ bucket?
│  ├─ >200% → Deploy with caution (smaller positions)
│  ├─ 100-200% → Deploy conservatively
│  ├─ 50-100% → Deploy very conservatively
│  └─ <50% → NBA only (spreads too wide)
│
└─ Liquidity check: Avg OI in target bucket?
   ├─ >500 → Can scale normally
   ├─ 200-500 → Small positions only
   └─ <200 → Skip, too illiquid
```

---

## Expected Timeline

### Week 1 (Today)
- ✅ Create collection script
- ✅ Run historical backfill (30 days)
- ✅ Initial analysis on ~400 games
- 📊 First look at actual edges with larger sample

### Week 2
- 🔄 Ongoing collection (10-15 games/day)
- 📊 Re-run analysis (~450 games total)
- 🎯 Make preliminary deployment decision

### Week 3
- 🔄 Ongoing collection
- 📊 Re-run analysis (~500 games total)
- 🚀 Deploy if edges hold (small positions)

### Week 4
- 🔄 Ongoing collection
- 📊 Final analysis (~550 games total)
- 📈 Scale up if performing well

---

## Risk Management During Collection

### While Collecting Data
1. **Do NOT deploy NCAAB strategy yet**
   - Current 90-game sample too small
   - 365% ROI likely to regress

2. **DO deploy NBA strategy now**
   - 558 games is excellent sample
   - Edges well-validated
   - High liquidity, tight spreads

3. **Monitor NCAAB opportunities**
   - Track current markets in probe database
   - Paper trade to understand execution
   - But no real money until validated

### After Sufficient Data
1. **Start small (50% of NBA position sizes)**
2. **Only trade high-liquidity markets (OI > 500)**
3. **Use limit orders to avoid wide spreads**
4. **Track actual vs expected performance**
5. **Scale up gradually if edges persist**

---

## Success Metrics

### Data Collection
- ✅ Collect 300+ settled NCAAB games
- ✅ At least 25+ games in 10-20¢ bucket
- ✅ At least 40+ games in 25-30¢ bucket
- ✅ Even distribution across last 30 days (no selection bias)

### Edge Validation
- ✅ ROI > 50% in 10-20¢ bucket (even after regression)
- ✅ ROI > 30% in 25-30¢ bucket
- ✅ Edges persist across different time periods
- ✅ Win rate significantly > implied probability

### Execution
- ✅ Can fill orders in target buckets
- ✅ Actual spreads ≤ 40¢ (manageable)
- ✅ Sufficient liquidity for 5-10 contract positions
- ✅ Market impact < 5¢ per trade

---

## Next Steps (Immediate Actions)

### 1. Run Historical Backfill
```bash
cd /Users/raine/tradingutils
python3 scripts/collect_ncaab_settlements.py --days 30 --csv data/ncaab_settlements_full.csv
```

### 2. Analyze Results
```bash
python3 -m mcp.research.run_notebook ncaab_actual_edges.ipynb
```

### 3. Set Up Ongoing Collection
```bash
# Option A: Cron job
crontab -e
# Add: 0 * * * * cd /Users/raine/tradingutils && python3 scripts/collect_ncaab_settlements.py --days 1

# Option B: tmux/screen session
tmux new -s ncaab-collector
python3 scripts/collect_ncaab_settlements.py --continuous --interval 3600
# Ctrl+B, D to detach
```

### 4. Deploy NBA Strategy
```bash
# NBA is validated and ready
python3 main.py run nba-underdog --config conservative
```

### 5. Monitor Progress
```bash
# Check daily
tail -f logs/ncaab_collector.log

# Analyze weekly
python3 -m mcp.research.run_notebook ncaab_actual_edges.ipynb
```

---

## Questions to Answer Through Collection

1. **Does 365% ROI persist with larger sample?**
   - Current: 83% win rate on 6 games
   - Likely to regress, but to what level?
   - Even 50% regressed ROI (182%) would be incredible

2. **Are certain times more profitable?**
   - Early season vs late season
   - Conference play vs non-conference
   - Tournament games

3. **Does edge vary by team/conference?**
   - Power 5 conferences vs mid-majors
   - Could focus on highest-edge subsets

4. **How do spreads behave in practice?**
   - Theoretical: 34¢ avg spread
   - Reality: May be better for liquid markets
   - Can we work orders to reduce impact?

5. **What about other college sports?**
   - Women's basketball
   - College football (next season)
   - College hockey

---

## Conclusion

**IMMEDIATE ACTION:** Run 30-day backfill to get ~300-400 NCAAB games

**TIMELINE:** 2-4 weeks of ongoing collection for full validation

**EXPECTED OUTCOME:**
- Best case: 200%+ ROI persists → Deploy aggressively
- Likely case: 100-150% ROI → Deploy conservatively
- Worst case: 30-50% ROI → NBA only (spreads too wide)

**RISK:** Low - we're only collecting data, not trading yet

**OPPORTUNITY:** Could discover a market inefficiency 10x larger than NBA
