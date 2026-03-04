# HMM → GBM Data Collection Status

**Last Updated:** 2026-02-27 4:50 PM

---

## ✅ Phase 1: HMM Training (COMPLETE)

### Training Data
```
Database: btc_ob_48h.db (2.1 GB)
Date range: Feb 22 17:10 - Feb 24 18:33 (49.4 hours)
Windows: 34,307 (5-second intervals)
Episodes: 6 (continuous trading sessions)
```

### Features Extracted
```
✅ net_move: Price change in 5s window ($)
✅ oscillation_ratio: total_path / net_move (clipped to 100)
✅ volume: BTC traded in window
❌ spread_bps: Not available (old DB)
❌ orderflow_imbalance: Not available (old DB)
```

### HMM Model
```
Model: models/crypto_regime_hmm.pkl
States: 3 (trending_low_vol, choppy, trending_high_vol)
Features: 3 (normalized to mean=0, std=1)
Training method: Gaussian HMM, diagonal covariance, 5 inits
```

### Learned States

**State 0: Trending (Low Volatility) - 42% of time**
- Oscillation ratio: -0.633 (below average = trending)
- Volume: -0.141 (below average)
- Use case: Slow, steady trends

**State 1: Choppy - 34% of time**
- Oscillation ratio: 1.126 (above average = choppy)
- Volume: -0.243 (below average)
- Use case: Avoid trading (whipsaw risk)

**State 2: Trending (High Volume) - 24% of time**
- Oscillation ratio: -0.485 (below average = trending)
- Volume: 0.585 (above average = strong conviction)
- Use case: Best trades (strong directional moves)

---

## ⏳ Phase 2: Enhanced Data Collection (IN PROGRESS)

### Current Probe
```
Database: btc_probe_20260227.db (47 MB → growing)
Started: 4:02 PM (PID 67511)
Duration: 24 hours (until tomorrow 4:02 PM)
Current progress: ~0.8 hours collected
```

### NEW Features Available
```
✅ binance_l2.spread_bps (every 200ms)
✅ binance_l2.imbalance (orderflow)
✅ coinbase_l2.spread_bps
✅ coinbase_l2.imbalance
✅ kalshi_orderbook (full L2 depth)
```

### Timeline
```
Now (4:50 PM):        0.8 hours collected
Tomorrow 4:02 PM:    24 hours collected → RETRAIN HMM with 5 features
Feb 28 4:02 PM:      48 hours collected → Train GBM on HMM states
```

---

## 📊 Next Steps

### Tomorrow (Feb 28, ~4 PM) - Retrain HMM
Once we have 24+ hours of data with L2 features:

```bash
# Extract features from new probe (5 features now!)
python3 strategies/crypto_scalp/feature_extraction.py data/btc_probe_20260227.db

# Retrain HMM with spread + orderflow
python3 scripts/train_crypto_regime_hmm.py \
  --db data/btc_probe_20260227.db \
  --output models/crypto_regime_hmm_v2.pkl

# Expected: Better regime detection with spread/orderflow signals
```

### Day 3 (Mar 1) - Train GBM
After HMM v2 is trained:

1. **Label windows by profitability:**
   - For each 5s window, check if a trade would have made money
   - Criteria: BUY when HMM says trending, SELL 20s later
   - Label: 1 = profitable, 0 = loss

2. **Extract GBM features:**
   ```
   For each window:
     - Raw features (net_move, osc_ratio, volume, spread, orderflow)
     - HMM state posteriors [P(state_0), P(state_1), P(state_2)]
     - Concatenate: [5 raw features, 3 state posteriors] = 8 features total
   ```

3. **Train GBM:**
   ```python
   from src.models.gbm_trainer import GBMTrainer

   gbm = GBMTrainer(n_splits=5)
   gbm.hyperparameter_search(X, y, groups=episode_ids, n_configs=50)
   gbm.train_final(X, y)
   gbm.save('models/crypto_regime_gbm.txt', 'models/crypto_regime_cal.pkl')
   ```

4. **Expected improvement:**
   ```
   Simple threshold (osc < 3.0):  54% WR, +$77.50 over 48h
   HMM filter only:               58-60% WR (estimate)
   HMM → GBM hybrid:              60-62% WR (target)
   ```

---

## 🔍 Data Quality Checks

### Current Probe Health
```bash
# Check probe is running
ps aux | grep btc_latency_probe
# PID 67511 - ✅ Running

# Check data collection rate
sqlite3 data/btc_probe_20260227.db "
  SELECT
    'Binance trades: ' || count(*) || ' (' || round((max(ts)-min(ts))/3600, 1) || 'h)',
    'Expected rate: ~1.8M trades / 24h'
  FROM binance_trades"
# 54,698 trades in 0.8h = ~68k/hour = ~1.6M/24h ✅ On track
```

### Data Gaps (if any)
```bash
# Check for gaps > 60s (indicates probe crash)
sqlite3 data/btc_probe_20260227.db "
  SELECT
    datetime(ts, 'unixepoch') as gap_start,
    gap_sec
  FROM (
    SELECT ts, ts - LAG(ts) OVER (ORDER BY ts) as gap_sec
    FROM binance_trades
  )
  WHERE gap_sec > 60"
# If any results: probe had downtime, may need to restart
```

---

## 📈 Expected Training Schedule

| Date | Time | Action | Data Available | Model |
|------|------|--------|----------------|-------|
| **Feb 27** | **4:50 PM** | ✅ Train HMM v1 | 49h (3 features) | `crypto_regime_hmm.pkl` |
| Feb 28 | 4:00 PM | Train HMM v2 | 24h (5 features) | `crypto_regime_hmm_v2.pkl` |
| Mar 1 | 10:00 AM | Label windows | 48h (5 features) | - |
| Mar 1 | 2:00 PM | Train GBM | 48h labeled | `crypto_regime_gbm.txt` |
| Mar 1 | 4:00 PM | Backtest hybrid | 48h | HMM v2 + GBM |
| Mar 1 | 5:00 PM | Paper trade | Live | HMM v2 + GBM |

---

## 🎯 Success Criteria

### HMM v1 (Current - 3 features)
- ✅ Trains without errors
- ✅ Learns 3 distinct states
- ✅ State distribution reasonable (not 99% in one state)
- ✅ Can classify new windows in <5ms

### HMM v2 (Tomorrow - 5 features)
- [ ] Trains on 24+ hours of data
- [ ] Incorporates spread + orderflow signals
- [ ] States show different spread/imbalance characteristics
- [ ] Improves regime detection vs v1

### GBM (Day 3)
- [ ] Trains on 5,000+ labeled windows
- [ ] Cross-validation log-loss < 0.65
- [ ] Calibrated probabilities (isotonic regression)
- [ ] Feature importance shows HMM states + spread matter

### Backtest (Day 3)
- [ ] Win rate > 60% (vs 54% baseline)
- [ ] Avg P&L > $0.15/trade (vs $0.103 baseline)
- [ ] Fewer trades (higher selectivity)
- [ ] Sharpe ratio improvement

---

## 🚨 Monitoring

While probe collects data, check every 4-6 hours:

```bash
# Quick status
./scripts/crypto_scalp_status.sh

# Detailed edge monitor (runs continuously)
python3 scripts/monitor_scalp_edge.py \
  --probe-db data/btc_probe_20260227.db \
  --interval 300  # Check every 5 min
```

---

## 📝 Notes

- **Feature normalization:** HMM requires standardized features (mean=0, std=1)
- **Episode segmentation:** 5-minute gap = new episode (handles market closures)
- **Oscillation clipping:** Max ratio = 100 (prevents infinity when net_move ≈ 0)
- **BIC selection:** Can run `--bic` flag to auto-select optimal # of states
- **GBM labels:** Will need to simulate trades on historical data to generate profit labels

---

**Bottom Line:**
- ✅ HMM v1 trained successfully on 49h historical data
- ⏳ Collecting enhanced data (24h target by tomorrow)
- 📅 GBM training scheduled for Mar 1
- 🎯 Expected final performance: 60-62% WR (+7-8% improvement)
