# Feature Rollout Plan: Empirical Kelly, VPIN Kill Switch, Sequence Gap Detection, A-S Reservation Price

## Executive Summary

This document outlines the recommended phased rollout strategy for the 4 new quantitative features. The approach prioritizes validation, incremental risk exposure, and measurable impact assessment.

---

## Phase 1: Backtesting Validation (Week 1)

**Goal:** Validate features in historical data before risking live capital.

### 1.1 Empirical Kelly Validation

**Test Cases:**
```bash
# Run backtest with standard Kelly (baseline)
python3 main.py backtest prediction-mm \
    --db data/historical_mm.db \
    --config config/portfolio_config_baseline.yaml

# Run backtest with empirical Kelly
python3 main.py backtest prediction-mm \
    --db data/historical_mm.db \
    --config config/portfolio_config_empirical.yaml
```

**Compare metrics:**
- Max drawdown (expect: 10-30% reduction with empirical Kelly)
- Sharpe ratio (expect: marginal improvement)
- Capital utilization (expect: 60-80% of full Kelly)
- Position size stability over time

**Success Criteria:**
- ✅ Max DD reduced by at least 15%
- ✅ No catastrophic single-trade losses
- ✅ Allocations stay within [0, 100%] always
- ✅ CV adjustments reasonable (haircut < 50% for stable strategies)

**Config for testing:**
```yaml
# config/portfolio_config_empirical.yaml
kelly_fraction: 0.5
use_empirical_kelly: true
empirical_kelly_simulations: 1000
empirical_kelly_seed: 42  # Reproducibility
```

---

### 1.2 VPIN Kill Switch Validation

**Test Cases:**
```bash
# Backtest with kill switch disabled (baseline)
python3 main.py backtest prediction-mm \
    --db data/historical_fills.db \
    --config strategies/configs/prediction_mm_baseline.yaml

# Backtest with kill switch enabled
python3 main.py backtest prediction-mm \
    --db data/historical_fills.db \
    --config strategies/configs/prediction_mm_vpin.yaml
```

**Analyze:**
1. Identify toxic flow periods in historical data
2. Check if kill switch would have triggered
3. Measure PnL impact of avoided fills vs. lost spread capture

**Success Criteria:**
- ✅ Kill switch triggers 2-5 times per week (not too sensitive)
- ✅ Avoided fills during toxic periods have negative expected value
- ✅ Spread capture opportunity cost < 20% of adverse selection savings
- ✅ No false positives during normal flow

**Config for testing:**
```yaml
# strategies/configs/prediction_mm_vpin.yaml
vpin_kill_switch:
  enabled: true
  toxic_threshold: 0.70
  warning_threshold: 0.50
  check_interval_sec: 5
  toxic_cooldown_sec: 60
  warning_spread_multiplier: 2.5
```

**Analysis script:**
```python
# scripts/analyze_vpin_backtest.py
import sqlite3
import pandas as pd

db = sqlite3.connect('data/historical_fills.db')

# Get all fills
fills = pd.read_sql('SELECT * FROM fills ORDER BY timestamp', db)

# Simulate VPIN
from core.indicators.vpin import VPINCalculator
vpin = VPINCalculator()

toxic_periods = []
for _, fill in fills.iterrows():
    vpin.on_trade(fill.price, fill.size, fill.bid, fill.ask)
    reading = vpin.get_reading()
    if reading and reading.is_toxic:
        toxic_periods.append({
            'time': fill.timestamp,
            'vpin': reading.vpin,
            'price': fill.price
        })

# Analyze fills that would have been blocked
print(f"Toxic periods detected: {len(toxic_periods)}")
```

---

### 1.3 Sequence Gap Detection Validation

**Test Cases:**
```bash
# Test with historical orderbook recording
python3 scripts/btc_latency_probe.py \
    --mode analyze \
    --db data/btc_probe_2024_12.db \
    --check-gaps
```

**Analyze:**
1. Count gaps in historical WebSocket data
2. Measure orderbook divergence after gaps
3. Quantify fill price slippage from stale orderbooks

**Success Criteria:**
- ✅ Gap detection identifies all sequence breaks
- ✅ Reconnection completes within 2 seconds
- ✅ No orderbook state corruption after reconnect
- ✅ Gap metrics logged for monitoring

**Config for testing:**
```yaml
# core/exchange_client/kalshi/config.yaml (create this)
websocket:
  enable_sequence_validation: true
  gap_tolerance: 0  # Strict mode for testing
  reconnect_delay_ms: 500
```

---

### 1.4 A-S Reservation Price Validation

**Test Cases:**
```bash
# Backtest with simple inventory skew (baseline)
python3 main.py backtest prediction-mm \
    --db data/historical_mm.db \
    --config strategies/configs/prediction_mm_baseline.yaml

# Backtest with A-S reservation price
python3 main.py backtest prediction-mm \
    --db data/historical_mm.db \
    --config strategies/configs/prediction_mm_avellaneda.yaml
```

**Compare:**
- Inventory mean reversion time
- Extreme position frequency (|position| > 80% of max)
- PnL from inventory exits vs. adverse selection
- Quote competitiveness (inside spread %)

**Success Criteria:**
- ✅ Inventory mean reversion 20-40% faster
- ✅ Extreme positions reduced by 30%+
- ✅ Spread capture rate maintained (within 10% of baseline)
- ✅ No quote lockout from over-aggressive skewing

**Config for testing:**
```yaml
# strategies/configs/prediction_mm_avellaneda.yaml
use_reservation_price: true
risk_aversion: 0.05  # Start conservative
reservation_use_log_odds: false  # Test direct mode first
```

**Analysis notebook:**
```python
# notebooks/analyze_reservation_price.ipynb
import pandas as pd
import matplotlib.pyplot as plt

# Load backtest results
baseline = pd.read_csv('results/baseline_positions.csv')
avellaneda = pd.read_csv('results/avellaneda_positions.csv')

# Plot inventory over time
fig, ax = plt.subplots(2, 1, figsize=(12, 8))

ax[0].plot(baseline.timestamp, baseline.net_position, label='Simple Skew')
ax[0].axhline(50, color='r', linestyle='--', alpha=0.3)
ax[0].axhline(-50, color='r', linestyle='--', alpha=0.3)
ax[0].set_title('Inventory: Simple Skew')

ax[1].plot(avellaneda.timestamp, avellaneda.net_position, label='A-S Reservation')
ax[1].axhline(50, color='r', linestyle='--', alpha=0.3)
ax[1].axhline(-50, color='r', linestyle='--', alpha=0.3)
ax[1].set_title('Inventory: A-S Reservation Price')

plt.tight_layout()
plt.savefig('results/inventory_comparison.png')

# Calculate mean reversion time
def mean_reversion_time(positions, threshold=25):
    crossings = 0
    total_time = 0
    start = None
    for i, pos in enumerate(positions):
        if abs(pos) > threshold and start is None:
            start = i
        elif abs(pos) < threshold and start is not None:
            total_time += (i - start)
            crossings += 1
            start = None
    return total_time / crossings if crossings > 0 else float('inf')

baseline_mr = mean_reversion_time(baseline.net_position.values)
avellaneda_mr = mean_reversion_time(avellaneda.net_position.values)

print(f"Mean reversion time (ticks):")
print(f"  Baseline: {baseline_mr:.1f}")
print(f"  A-S: {avellaneda_mr:.1f}")
print(f"  Improvement: {(1 - avellaneda_mr/baseline_mr)*100:.1f}%")
```

---

## Phase 2: Paper Trading (Week 2-3)

**Goal:** Validate in live market conditions with zero capital risk.

### 2.1 Setup Paper Trading Environment

```bash
# Create paper trading configs
mkdir -p config/paper_trading/

# Copy production configs but set dry_run=true
cp strategies/configs/prediction_mm_strategy.yaml \
   config/paper_trading/prediction_mm_paper.yaml
```

**Enable all features in paper trading:**
```yaml
# config/paper_trading/prediction_mm_paper.yaml
dry_run: true  # CRITICAL: No real orders

# Enable all new features
use_reservation_price: true
risk_aversion: 0.05

vpin_kill_switch:
  enabled: true
  toxic_threshold: 0.70

# In websocket config
enable_sequence_validation: true
gap_tolerance: 0
```

**Portfolio config:**
```yaml
# config/paper_trading/portfolio_paper.yaml
use_empirical_kelly: true
empirical_kelly_simulations: 1000
kelly_fraction: 0.5
```

### 2.2 Monitoring Setup

**Create monitoring dashboard:**
```python
# scripts/paper_trading_monitor.py
import time
from datetime import datetime
from strategies.prediction_mm.orchestrator import PredictionMMOrchestrator
from core.portfolio.portfolio_manager import PortfolioManager

class PaperTradingMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.metrics = {
            'vpin_triggers': [],
            'sequence_gaps': [],
            'quotes_generated': 0,
            'quotes_cancelled': 0,
            'empirical_kelly_adjustments': [],
        }

    def log_vpin_trigger(self, state, vpin):
        self.metrics['vpin_triggers'].append({
            'time': datetime.now(),
            'state': state,
            'vpin': vpin
        })
        print(f"[VPIN] State: {state}, VPIN: {vpin:.3f}")

    def log_sequence_gap(self, ticker, expected, actual):
        self.metrics['sequence_gaps'].append({
            'time': datetime.now(),
            'ticker': ticker,
            'gap_size': actual - expected
        })
        print(f"[GAP] {ticker}: expected {expected}, got {actual}")

    def log_kelly_adjustment(self, strategy, cv, haircut):
        self.metrics['empirical_kelly_adjustments'].append({
            'time': datetime.now(),
            'strategy': strategy,
            'cv': cv,
            'haircut': haircut
        })
        print(f"[KELLY] {strategy}: CV={cv:.3f}, haircut={haircut:.1%}")

    def print_summary(self):
        runtime = time.time() - self.start_time
        print(f"\n=== Paper Trading Summary ({runtime/3600:.1f}h) ===")
        print(f"VPIN triggers: {len(self.metrics['vpin_triggers'])}")
        print(f"Sequence gaps: {len(self.metrics['sequence_gaps'])}")
        print(f"Quotes generated: {self.metrics['quotes_generated']}")
        print(f"Quotes cancelled: {self.metrics['quotes_cancelled']}")

# Run for 48 hours
monitor = PaperTradingMonitor()
# ... wire into strategy callbacks
```

### 2.3 Paper Trading Success Criteria

After 48-72 hours of paper trading:

**VPIN Kill Switch:**
- ✅ 0-3 false positives per day
- ✅ Triggers during known volatile events (news, macro)
- ✅ Cooldown completes without quote spam
- ✅ Logs are clear and actionable

**Sequence Gap Detection:**
- ✅ No orderbook corruption events
- ✅ Reconnects complete in < 2 seconds
- ✅ Gap metrics logged correctly
- ✅ No impact on fill quality

**Empirical Kelly:**
- ✅ Allocations update correctly on rebalance
- ✅ CV values reasonable (< 0.5 for stable strategies)
- ✅ Position sizes within expected ranges
- ✅ No allocation exceeds 25% per strategy

**A-S Reservation Price:**
- ✅ Quotes remain competitive (inside spread 80%+ of time)
- ✅ Inventory trends toward zero
- ✅ No quote lockout from extreme positions
- ✅ Spread widening proportional to inventory

---

## Phase 3: Live Trading - Single Feature (Week 4)

**Goal:** Deploy ONE feature at a time with minimal capital.

### 3.1 Choose Starting Feature

**Recommended order:**
1. **Sequence Gap Detection** (lowest risk, pure infrastructure)
2. **Empirical Kelly** (affects position sizing, measurable)
3. **A-S Reservation Price** (affects quoting, moderate impact)
4. **VPIN Kill Switch** (most aggressive, save for last)

### 3.2 Deploy Sequence Gap Detection First

**Why first?**
- Pure infrastructure (no strategy logic change)
- Defensive (prevents orderbook corruption)
- Minimal performance impact
- Easy to measure (gap count)

**Config:**
```yaml
# config/production/kalshi_websocket.yaml
enable_sequence_validation: true
gap_tolerance: 1  # Allow small gaps in production
reconnect_delay_ms: 1000
```

**Run for 1 week with monitoring:**
```bash
python3 main.py run prediction-mm \
    --config config/production/prediction_mm.yaml \
    --capital 1000  # Start small
```

**Monitor:**
- Gap frequency (expect: 0-2 per day)
- Reconnect latency (expect: < 2s)
- Fill price slippage vs. mid (expect: no change)
- Orderbook state validity

**Success = No gap-related issues for 7 days**

---

### 3.3 Deploy Empirical Kelly (Week 5)

**After sequence gap detection is stable:**

**Config:**
```yaml
# config/production/portfolio_config.yaml
use_empirical_kelly: true
empirical_kelly_simulations: 500  # Lower in production for speed
kelly_fraction: 0.5
```

**Run with $5K capital for 1 week:**
```bash
python3 main.py portfolio rebalance --config config/production/portfolio_config.yaml
```

**Monitor:**
- Position size vs. baseline (expect: 60-80% of full Kelly)
- Max drawdown (expect: 15-30% improvement)
- Rebalance frequency (expect: daily or on ±20% bankroll change)
- CV values per strategy

**Success Criteria:**
- ✅ No position exceeds allocation limits
- ✅ Drawdowns reduced vs. historical baseline
- ✅ No Kelly calculation errors in logs

---

### 3.4 Deploy A-S Reservation Price (Week 6)

**After empirical Kelly is stable:**

**Config:**
```yaml
# config/production/prediction_mm.yaml
use_reservation_price: true
risk_aversion: 0.03  # Conservative start
reservation_use_log_odds: false
```

**Run with $10K capital for 1 week:**

**Monitor:**
- Average inventory (expect: closer to 0)
- Extreme position events (expect: -30% frequency)
- Spread capture rate (expect: within 10% of baseline)
- Quote competitiveness (expect: inside 80%+ of time)

**Success Criteria:**
- ✅ Inventory mean reversion faster than baseline
- ✅ PnL maintained or improved
- ✅ No quote lockout periods

**Tuning after 3 days:**
If inventory still extreme, increase `risk_aversion` to 0.05-0.07.

---

### 3.5 Deploy VPIN Kill Switch (Week 7)

**After all other features are stable:**

**Config:**
```yaml
# config/production/prediction_mm.yaml
vpin_kill_switch:
  enabled: true
  toxic_threshold: 0.75  # More conservative in production
  warning_threshold: 0.55
  check_interval_sec: 5
  toxic_cooldown_sec: 120  # Longer cooldown
  warning_spread_multiplier: 2.0  # Less aggressive widening
```

**Why last?**
- Most aggressive (cancels quotes)
- Affects liquidity provision directly
- Needs tuning to avoid false positives

**Run with full capital allocation:**

**Monitor:**
- Kill switch activation frequency (expect: 1-3 per week)
- PnL during toxic vs. normal periods
- False positive rate (manual review)
- Spread capture opportunity cost

**Success Criteria:**
- ✅ < 1 false positive per week
- ✅ Avoided fills have negative expected value
- ✅ Opportunity cost < 15% of adverse selection savings

**Tuning:**
If too many false positives, increase `toxic_threshold` to 0.80.
If missing toxic events, decrease to 0.70.

---

## Phase 4: Full Production (Week 8+)

**Goal:** All features enabled, full capital, continuous monitoring.

### 4.1 Final Production Config

```yaml
# config/production/portfolio_config.yaml
kelly_fraction: 0.5
use_empirical_kelly: true
empirical_kelly_simulations: 500
rebalance_frequency: daily
min_allocation_threshold: 0.05
max_allocation_per_strategy: 0.25
max_total_allocation: 0.80

# config/production/prediction_mm.yaml
use_reservation_price: true
risk_aversion: 0.05

vpin_kill_switch:
  enabled: true
  toxic_threshold: 0.75
  warning_threshold: 0.55
  check_interval_sec: 5
  toxic_cooldown_sec: 120
  warning_spread_multiplier: 2.0

# config/production/kalshi_websocket.yaml
enable_sequence_validation: true
gap_tolerance: 1
reconnect_delay_ms: 1000
```

### 4.2 Continuous Monitoring

**Daily checks:**
```bash
# Check VPIN activations
grep "VPIN KILL SWITCH" logs/prediction_mm.log | tail -20

# Check sequence gaps
grep "sequence gap detected" logs/kalshi_ws.log | tail -20

# Check Kelly adjustments
grep "empirical Kelly" logs/portfolio.log | tail -20

# Check inventory levels
python3 scripts/analyze_portfolio.py --db data/portfolio_trades.db --last 7d
```

**Weekly review:**
- VPIN false positive rate
- Sequence gap frequency
- Empirical Kelly haircut distribution
- Inventory mean reversion time
- Overall PnL vs. baseline

### 4.3 Alert Thresholds

**Set up alerts for:**
```python
# monitoring/alerts.py
ALERTS = {
    'vpin_activations_per_day': {
        'warning': 5,
        'critical': 10
    },
    'sequence_gaps_per_day': {
        'warning': 3,
        'critical': 10
    },
    'kelly_cv_threshold': {
        'warning': 0.5,  # High uncertainty
        'critical': 0.8
    },
    'inventory_extreme_duration_hours': {
        'warning': 4,
        'critical': 12
    }
}
```

---

## Rollback Plan

**If any feature causes issues:**

### Quick Disable (< 1 minute)
```bash
# Edit config and restart
vim config/production/prediction_mm.yaml
# Set enabled: false for problematic feature
systemctl restart prediction-mm
```

### Emergency Kill Switch
```python
# scripts/emergency_disable.py
import yaml

features = {
    'use_empirical_kelly': False,
    'use_reservation_price': False,
    'vpin_kill_switch.enabled': False,
    'enable_sequence_validation': False
}

for config_file in ['config/production/*.yaml']:
    # Set all to False
    # Restart service
```

### Rollback Triggers

**Disable feature if:**
- ✅ PnL drops > 20% vs. 7-day average
- ✅ False positive rate > 10% for VPIN
- ✅ Sequence gaps > 10 per day
- ✅ Kelly allocations exceed limits
- ✅ Inventory lockout > 6 hours

---

## Success Metrics (30 days after full deployment)

**Overall:**
- ✅ Sharpe ratio improved by 10-20%
- ✅ Max drawdown reduced by 15-30%
- ✅ No catastrophic single-day losses
- ✅ Capital efficiency improved (allocation utilization 70-80%)

**Per Feature:**
- **Empirical Kelly:** Position sizes stable, drawdowns reduced
- **VPIN Kill Switch:** < 2 false positives per week, adverse selection avoided
- **Sequence Gap Detection:** 0 orderbook corruption events
- **A-S Reservation:** Inventory mean reversion 30%+ faster

---

## Tuning Guidelines

### Empirical Kelly
- **If CV too high (> 0.5):** Increase lookback window for trade PnLs
- **If allocations too conservative:** Decrease kelly_fraction to 0.6-0.7
- **If allocations too aggressive:** Increase empirical_kelly_simulations to 2000

### VPIN Kill Switch
- **Too many triggers:** Increase toxic_threshold to 0.80
- **Missing toxic events:** Decrease toxic_threshold to 0.70
- **Slow recovery:** Reduce toxic_cooldown_sec to 60

### Sequence Gap Detection
- **Too many reconnects:** Increase gap_tolerance to 2-3
- **Missing gaps:** Set gap_tolerance to 0 (strict)

### A-S Reservation
- **Inventory still extreme:** Increase risk_aversion to 0.10
- **Quotes not competitive:** Decrease risk_aversion to 0.03
- **Near expiry issues:** Enable reservation_use_log_odds

---

## Timeline Summary

| Week | Phase | Feature | Capital | Goal |
|------|-------|---------|---------|------|
| 1 | Backtest | All | $0 | Validate in historical data |
| 2-3 | Paper Trade | All | $0 | Live market validation |
| 4 | Live | Sequence Gap | $1K | Infrastructure stability |
| 5 | Live | Empirical Kelly | $5K | Position sizing validation |
| 6 | Live | A-S Reservation | $10K | Quoting optimization |
| 7 | Live | VPIN Kill Switch | $20K | Adverse selection protection |
| 8+ | Production | All | Full | Continuous monitoring |

**Total time to full deployment: 8 weeks**
**Risk-adjusted, incremental, measurable**
