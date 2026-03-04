# Quick Test: Max Momentum Filter

## What We're Testing

**Hypothesis:** Adding `max_momentum_ratio: 7.0` will filter ultra-high momentum spikes and improve win rate.

**Expected:**
- 10-15% fewer signals (filters the craziest spikes)
- 5-10% better win rate (fewer false positives)
- Same or better profitability (quality over quantity)

---

## Quick Start (30-60 minutes)

### Step 1: Start the Test

```bash
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_test.yaml
```

This config is **identical to your baseline** except for ONE change:
```yaml
max_momentum_ratio: 7.0  # NEW - filters ultra-high momentum spikes
```

### Step 2: Monitor in Another Terminal

Open a second terminal and run:

```bash
./scripts/monitor_test.sh
```

This shows live stats:
- 📊 Signals detected
- 🚫 Ultra-high momentum filtered
- ✅ Wins / ❌ Losses
- 📊 Win rate

### Step 3: Watch for These Patterns

**You should see:**

```
MOMENTUM SPIKE FILTER: binance - ratio 12.34 > max 7.0 (likely mean reversion)
```

These are the whipsaw spikes being filtered!

**Count them:**
- If you see ~10-20 filtered per hour → Working as expected!
- If you see 0 filtered → Markets are calm (test in volatile period)
- If you see 100+ filtered → Too aggressive (raise to 10.0)

### Step 4: Let It Run

**Minimum:** 30 minutes (get some data)
**Ideal:** 60-90 minutes (statistical significance)

Stop with `Ctrl+C` when done.

---

## Interpreting Results

### Success Criteria

**✅ Filter is working IF:**
1. You see "MOMENTUM SPIKE FILTER" messages in logs
2. Win rate improved by 5-10% vs your baseline
3. P&L per trade is similar or better

**Example:**
```
Baseline (old):     Test (new):
- Signals: 100      - Signals: 85 (-15%)
- Win rate: 35%     - Win rate: 45% (+10pp) ✅
- Avg profit: $0.10 - Avg profit: $0.12 (+20%) ✅
```

### Next Steps If Successful

If the test shows improvement:

**Option A: Keep it (conservative)**
```yaml
max_momentum_ratio: 7.0  # Current test setting
```

**Option B: Tighten it (more aggressive)**
```yaml
max_momentum_ratio: 5.0  # Filter more aggressively
```

Test Option B for another hour to see if even better!

### Next Steps If No Improvement

If win rate didn't improve:

**Possible reasons:**
1. Markets were too calm (not enough spikes to filter)
2. The threshold is wrong (try 5.0 instead of 7.0)
3. Volume/concentration filters matter more

**What to try:**
- Test during volatile hours (market open/close)
- Try max_momentum_ratio: 5.0 instead
- Add volume filter: min_window_volume.binance: 1.0

---

## Comparison Checklist

After running the test, compare to your baseline:

| Metric | Baseline (Old) | Test (New) | Change |
|--------|---------------|------------|--------|
| **Signals/hour** | ___ | ___ | ___% |
| **Momentum filters/hour** | 0 | ___ | NEW |
| **Win rate** | ___% | ___% | ___pp |
| **Avg profit/trade** | $__ | $__ | ___% |
| **Max loss** | $__ | $__ | Better? |

**Decision:**
- [ ] Keep max_momentum_ratio: 7.0
- [ ] Tighten to 5.0 and test again
- [ ] Revert (no improvement)
- [ ] Try different threshold

---

## Quick Commands Reference

**Start test:**
```bash
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_test.yaml
```

**Monitor live (separate terminal):**
```bash
./scripts/monitor_test.sh
```

**Check recent logs:**
```bash
tail -f logs/*.log | grep -E "(MOMENTUM|Signal|EXIT)"
```

**Count momentum filters:**
```bash
grep -c "MOMENTUM SPIKE FILTER" logs/*.log
```

**Calculate win rate:**
```bash
wins=$(grep -c "profit" logs/*.log)
losses=$(grep -c "loss" logs/*.log)
echo "Win rate: $(awk "BEGIN {printf \"%.1f\", ($wins/($wins+$losses))*100}")%"
```

---

## What You'll Learn

After this test, you'll know:

1. ✅ **Does the filter work?** (Do you see MOMENTUM SPIKE FILTER messages?)
2. ✅ **Does it help?** (Did win rate improve?)
3. ✅ **Is 7.0 the right threshold?** (Too many/few filtered?)
4. ✅ **Should you deploy it?** (Better risk-adjusted returns?)

---

## Files Created for This Test

- ✅ `strategies/configs/crypto_scalp_test.yaml` - Test configuration
- ✅ `scripts/run_test_comparison.sh` - Test runner script
- ✅ `scripts/monitor_test.sh` - Live monitoring script
- ✅ `TEST_INSTRUCTIONS.md` - This file

---

## Ready to Start?

Just run:

```bash
python3 main.py run crypto-scalp --config strategies/configs/crypto_scalp_test.yaml
```

And in another terminal:

```bash
./scripts/monitor_test.sh
```

**Good luck!** 🚀

Report back with your results and we'll analyze whether to:
- ✅ Keep it (7.0)
- 🔧 Tighten it (5.0)
- 📊 Add more filters (volume, concentration)
- ❌ Revert (no improvement)
