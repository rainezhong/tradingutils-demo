# HMM Training Guide for Crypto Scalp Strategy

## Table of Contents
1. [Why Historical Training Matters](#why-historical-training-matters)
2. [Collecting Historical Data](#collecting-historical-data)
3. [Training the HMM](#training-the-hmm)
4. [Validating Generalization](#validating-generalization)
5. [Using in Backtests](#using-in-backtests)
6. [Example Workflow](#example-workflow)

---

## Why Historical Training Matters

### The Overfitting Problem

**Initial Approach (WRONG)**:
```
Training data: Feb 22-24, 2026
Test data:     Feb 27-28, 2026
Result:        MASSIVE OVERFITTING
  - In-sample:  osc<3.0 = +$12.46 (48.9% WR)
  - Out-sample: osc<3.0 = -$14.65 (31% WR)
```

**Why it failed:**
- HMM learned regime patterns specific to those 3 days in February
- Regimes (choppy, trending, volatile) should be **universal**, not date-specific
- Model memorized February's market conditions rather than learning general patterns

### What the HMM Actually Learns

The HMM learns **market microstructure regimes** from these features:

1. **net_move** - Price change in 5-second window ($)
2. **oscillation_ratio** - Choppiness (total_path / abs(net_move))
3. **volume** - BTC volume traded
4. **spread_bps** - Average bid-ask spread (if available)
5. **orderflow_imbalance** - Buy vs sell pressure (if available)

These patterns are **timeless** — they occur across all time periods:
- **State 0 (Trending)**: Low oscillation, high volume, directional orderflow
- **State 1 (Choppy)**: High oscillation, moderate volume, balanced orderflow
- **State 2 (Quiet)**: Low volume, wide spreads, minimal movement

### Proper Approach

**Train on MONTHS/YEARS of historical data** → Test on future unseen data

```
Training data: Oct 2025 - Jan 2026 (3+ months)
Test data:     Feb 2026 (unseen)
Expected:      Better generalization
```

---

## Collecting Historical Data

### Option 1: Merge Existing Probe Databases

If you have multiple probe databases from different time periods, merge them:

```bash
# Merge all available probe databases
python3 scripts/merge_probe_dbs.py \
    --inputs data/btc_ob_*.db data/btc_probe_*.db \
    --output data/btc_historical_training.db \
    --skip-kalshi  # Skip Kalshi-specific tables (not needed for HMM)
```

**Output:**
```
✅ Created output database with 9 tables: data/btc_historical_training.db
📊 Merged 20,008,791 binance trades
📊 Merged 285,166 L2 orderbook snapshots
```

### Option 2: Download Historical Data from Binance

For TRUE historical training (months/years), download from Binance API:

```python
# scripts/download_historical_btc.py
# TODO: Implement Binance historical data downloader
# Fetches: BTC/USDT trades + L2 orderbook snapshots
# Stores: Same schema as probe databases
# Duration: 3-6 months minimum, 1+ years ideal
```

**What you need:**
- **Trades**: timestamp, price, quantity, buyer_is_maker
- **L2 Orderbook**: timestamp, bid_levels (top 20), ask_levels (top 20)
- **Frequency**: Trades=every trade, L2=100-200ms snapshots

**Data volume estimates:**
- 1 week: ~2 GB
- 1 month: ~8 GB
- 3 months: ~25 GB
- 1 year: ~100 GB

### Option 3: Use Public Datasets

Download pre-collected Bitcoin data:
- **Kaggle**: Search for "Bitcoin orderbook" or "BTC trades"
- **CryptoDataDownload**: Historical OHLCV + trades
- **Academic datasets**: Research labs often share cleaned data

**Convert to probe database schema:**
```bash
python3 scripts/convert_csv_to_probe_db.py \
    --input historical_btc_trades.csv \
    --output data/btc_historical.db
```

---

## Training the HMM

### Step 1: Extract Features

The training script automatically extracts 5-second window features:

```bash
python3 scripts/train_crypto_regime_hmm.py \
    --db data/btc_historical_training.db \
    --states 3 \
    --bic \
    --output models/crypto_regime_hmm_historical.pkl
```

**What it does:**
1. Splits data into 5-second windows
2. Computes features (net_move, oscillation, volume, spread, orderflow)
3. Normalizes features (z-score)
4. Segments into episodes (gaps >5 min = new episode)
5. Trains Gaussian HMM with `--bic` to select optimal # of states

**Progress output:**
```
Extracting features...
  Processed 100,000/500,000 windows (20.0%)
  Processed 200,000/500,000 windows (40.0%)
  ...

Extracted 500,000 windows:
  Total windows: 500,000
  Features per window: 5
  Episodes: 120
  Avg windows/episode: 4,167
```

### Step 2: Inspect Learned States

The script prints learned regime characteristics:

```
LEARNED STATES
======================================================================

State 0 (Trending):
  net_move: mean=1.245
  osc_ratio: mean=1.523
  volume: mean=8.432
  spread_bps: mean=2.1
  orderflow: mean=0.342

State 1 (Choppy):
  net_move: mean=0.112
  osc_ratio: mean=12.456
  volume: mean=5.234
  spread_bps: mean=3.5
  orderflow: mean=-0.023

State 2 (Quiet):
  net_move: mean=0.034
  osc_ratio: mean=2.145
  volume: mean=1.234
  spread_bps: mean=8.2
  orderflow: mean=0.001
```

**Interpretation:**
- **Trending**: Large net move, low oscillation, high volume, tight spread, directional flow
- **Choppy**: Small net move, high oscillation, moderate volume, moderate spread, neutral flow
- **Quiet**: Tiny net move, low oscillation, low volume, wide spread, no flow

### Step 3: Save Model

Model is automatically saved to specified path:
```
Model saved to models/crypto_regime_hmm_historical.pkl
```

**Model contents:**
- Trained HMM parameters (transition matrix, emission distributions)
- Normalization statistics (mean, std for each feature)
- Number of states
- Feature dimensions

### Advanced: Sampling for Very Large Datasets

If you have >10 GB of data, use sampling to avoid memory issues:

```bash
python3 scripts/train_crypto_regime_hmm.py \
    --db data/btc_1year.db \
    --states 3 \
    --bic \
    --max-windows 1000000 \  # Sample 1M windows (keeps temporal order)
    --output models/crypto_regime_hmm_1year.pkl
```

**When to sample:**
- Dataset >500K windows (>40 hours)
- Limited RAM (<16 GB)
- Want faster training iterations

**Impact:**
- Loses some temporal detail
- Still captures major regime patterns
- Much faster training (hours → minutes)

---

## Validating Generalization

**CRITICAL**: Always validate on completely unseen data.

### Step 1: Run Regime Distribution Analysis

```bash
python3 scripts/analyze_hmm_regimes.py \
    --db data/btc_probe_20260227.db \  # Unseen test data
    --hmm models/crypto_regime_hmm_historical.pkl
```

**Output:**
```
STATE DISTRIBUTION (Feb 27-28, 2026)
======================================================================
  State 0 (Trending): 12,345 windows (25.3%)
  State 1 (Choppy):   28,567 windows (58.6%)
  State 2 (Quiet):     7,891 windows (16.1%)
```

**What to check:**
1. **State distribution** - Should be similar to training distribution (±10%)
2. **State transitions** - Should see realistic regime shifts (not random)
3. **Feature means** - Should match training data characteristics

### Step 2: Run Out-of-Sample Backtest

```bash
python3 main.py backtest crypto-scalp-hmm-gbm \
    --db data/btc_probe_20260227.db \
    --hmm models/crypto_regime_hmm_historical.pkl \
    --gbm-threshold 0.20
```

**Compare to in-sample:**
```
Training period (Oct-Jan): +$XX.XX, YY% WR
Test period (Feb 27-28):   +$ZZ.ZZ, WW% WR
```

**What to check:**
1. **Win rate** - Should be within 5-10% of training WR
2. **P&L** - Should be same order of magnitude (not 10x different)
3. **Sharpe ratio** - Should be similar (±0.5)

**Red flags (overfitting)**:
- Training WR 60%, test WR 30% → OVERFIT
- Training P&L +$50, test P&L -$50 → OVERFIT
- Training Sharpe 2.5, test Sharpe 0.5 → OVERFIT

### Step 3: Walk-Forward Validation

For maximum confidence, test on MULTIPLE future periods:

```bash
# Test on 4 different weeks
for week in week1 week2 week3 week4; do
    python3 main.py backtest crypto-scalp-hmm-gbm \
        --db data/btc_${week}.db \
        --hmm models/crypto_regime_hmm_historical.pkl \
        --gbm-threshold 0.20
done
```

**Expected results:**
- All 4 weeks have similar WR (±10%)
- All 4 weeks have positive P&L (if strategy is robust)
- No week is catastrophically bad (>50% drawdown)

---

## Using in Backtests

### Standard Backtest (Uses Pre-Trained HMM)

```bash
python3 main.py backtest crypto-scalp-hmm-gbm \
    --db data/btc_probe_20260227.db \
    --hmm models/crypto_regime_hmm_historical.pkl \  # YOUR TRAINED MODEL
    --gbm models/crypto_regime_gbm.txt \
    --gbm-threshold 0.20
```

**Key points:**
- HMM is **never retrained** on backtest data
- HMM is loaded once at start
- All regime classification uses pre-trained model

### Threshold Sweep (Find Optimal GBM Threshold)

```bash
python3 main.py backtest crypto-scalp-hmm-gbm \
    --db data/btc_probe_20260227.db \
    --hmm models/crypto_regime_hmm_historical.pkl \
    --gbm models/crypto_regime_gbm.txt \
    # No --gbm-threshold → sweeps [0.10, 0.15, 0.20, 0.25, 0.30]
```

**Output:**
```
Config               Signals  Fills   Wins  WinRate  Net PnL
------------------------------------------------------------------
gbm >= 0.10            452     98      32      33%   -$8.50
gbm >= 0.15            312     67      24      36%   +$3.20
gbm >= 0.20            187     42      18      43%   +$12.45  ← BEST
gbm >= 0.25             89     21      10      48%   +$8.10
gbm >= 0.30             34      9       5      56%   +$2.30
```

**Interpretation:**
- Lower threshold = more trades, lower selectivity
- Higher threshold = fewer trades, higher selectivity
- Optimal threshold balances volume and quality

---

## Example Workflow

### Full Pipeline: From Data Collection to Validated Strategy

```bash
# STEP 1: Merge existing probe databases
python3 scripts/merge_probe_dbs.py \
    --inputs data/btc_ob_48h.db data/btc_probe_20260227.db \
    --output data/btc_merged_feb.db \
    --skip-kalshi

# STEP 2: Train HMM on merged data
python3 scripts/train_crypto_regime_hmm.py \
    --db data/btc_merged_feb.db \
    --states 3 \
    --bic \
    --output models/crypto_regime_hmm_feb.pkl

# STEP 3: Validate on out-of-sample data (e.g., March 2026)
python3 scripts/analyze_hmm_regimes.py \
    --db data/btc_probe_20260301.db \
    --hmm models/crypto_regime_hmm_feb.pkl

# STEP 4: Run backtest on unseen data
python3 main.py backtest crypto-scalp-hmm-gbm \
    --db data/btc_probe_20260301.db \
    --hmm models/crypto_regime_hmm_feb.pkl \
    --gbm models/crypto_regime_gbm.txt

# STEP 5: If performance holds, deploy to live trading
python3 main.py run crypto-scalp \
    --hmm-model models/crypto_regime_hmm_feb.pkl
```

### Quick Test: Does HMM Help?

Compare baseline vs HMM-filtered strategy:

```bash
# Baseline (no HMM)
python3 main.py backtest crypto-scalp \
    --db data/btc_probe_20260301.db \
    --realism realistic

# HMM-filtered
python3 main.py backtest crypto-scalp-hmm-gbm \
    --db data/btc_probe_20260301.db \
    --hmm models/crypto_regime_hmm_feb.pkl \
    --gbm-threshold 0.20

# Compare results
# Expected: HMM reduces losing trades, improves Sharpe ratio
```

---

## Troubleshooting

### Error: "HMM dimension mismatch"

**Cause:** Database has different features than HMM was trained on (3 vs 5 features).

**Solution:**
- Train new HMM on database with same L2 data availability
- OR retrain HMM with `--no-l2` flag to use only price+volume features

### Error: "Memory error during feature extraction"

**Cause:** Database too large (>10 GB, >1M windows).

**Solution:**
```bash
python3 scripts/train_crypto_regime_hmm.py \
    --db data/huge_db.db \
    --max-windows 500000 \  # Sample 500K windows
    --output models/hmm.pkl
```

### Warning: "Very few episodes (<10)"

**Cause:** Database has large gaps (>5 min) between data points.

**Solution:**
- Use `--episode-gap 600` (10 min) to allow longer gaps
- OR collect more continuous data

### Poor Out-of-Sample Performance

**Symptoms:**
- Training WR 50%, test WR 30%
- Training P&L +$20, test P&L -$15

**Likely causes:**
1. **Too little training data** - Need 3+ months minimum
2. **Training data not representative** - Need diverse market conditions
3. **Overfitting GBM threshold** - Threshold tuned on test data (data leakage!)

**Solutions:**
1. Collect more historical data (6+ months)
2. Ensure training data includes bull/bear/sideways markets
3. Use separate validation set for threshold tuning

---

## Best Practices

1. **Never train on test data** - Strict temporal separation
2. **Use 3+ months of training data** - Capture all regime types
3. **Validate on multiple periods** - Walk-forward testing
4. **Document training process** - Save training logs and configs
5. **Version your models** - crypto_regime_hmm_v1.pkl, _v2.pkl, etc.
6. **Monitor drift** - Retrain quarterly on latest data
7. **A/B test in production** - Run old vs new model side-by-side

---

## See Also

- [Backtest Realism Models](BACKTEST_REALISM_MODELS.md) - Realistic fill simulation
- [HMM Feature Extraction](../strategies/crypto_scalp/feature_extraction.py) - Feature engineering
- [GBM Training](../scripts/train_crypto_regime_gbm.py) - Profit prediction model
