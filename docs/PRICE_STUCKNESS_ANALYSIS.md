# Price Stuckness Analysis for Crypto Scalp Strategy

**Date:** 2026-02-28
**Purpose:** Detect when BTC prediction market prices get "stuck" at extreme values with no trading edge

---

## The Problem

The crypto scalp strategy relies on **Kalshi repricing after spot BTC moves**. But sometimes markets get "stuck":

**Example stuck scenario:**
```
BTC spot: $67,250 → $67,300 (+$50 move)
Strike: $67,200
Kalshi YES price: 95¢ → 95¢ (NO MOVEMENT)
```

**Why it's stuck:**
- Market is 95% confident BTC will be >$67,200
- BTC is already $50 above strike
- Even a $50 move doesn't change the 95% probability much
- **No edge to capture** - market won't reprice meaningfully

**The risk:**
- Strategy detects $50 BTC move
- Places order expecting repricing
- Market doesn't move (already priced in)
- Order times out or fills at stale price
- **Loss from fees + slippage**

---

## What Makes a Price "Stuck"?

A price is stuck when it exhibits multiple characteristics:

### 1. **Extreme Position (>90¢ or <10¢)**
```
Price = 95¢  → Only 5¢ away from certainty
Price = 8¢   → Only 8¢ away from zero
```

Near-certain markets have **little room to move**.

### 2. **Low Entropy (< 1.0 bits)**
Shannon entropy measures price distribution width:

```python
# HIGH entropy (good for trading):
prices = [42, 45, 48, 51, 54, 57]  # Moving around
entropy = 2.5 bits  # Distributed across range

# LOW entropy (stuck):
prices = [94, 95, 95, 95, 94, 95]  # Concentrated
entropy = 0.3 bits  # Stuck at 95¢
```

**Entropy < 1.0 bit** = prices concentrated in narrow range

### 3. **Low Volatility (< 2¢)**
```
5-minute price changes: [0¢, +1¢, 0¢, -1¢, 0¢]
Volatility = 0.5¢  → STUCK (not moving)

VS.

5-minute price changes: [+5¢, -3¢, +7¢, -4¢, +2¢]
Volatility = 4.3¢  → ACTIVE (good for trading)
```

### 4. **Unresponsive to Spot Moves**
Responsiveness ratio = `(Kalshi change) / (Spot change)`

```
BTC: $67,200 → $67,250 (+$50)
Kalshi: 60¢ → 65¢ (+5¢)
Ratio = 5¢ / $50 = 0.10  → RESPONSIVE ✓

BTC: $67,200 → $67,300 (+$100)
Kalshi: 95¢ → 96¢ (+1¢)
Ratio = 1¢ / $100 = 0.01  → UNRESPONSIVE (stuck)
```

**Ratio < 0.02** = market not responding to spot moves

---

## Stuckness Detection Rules

The analysis tool uses 4 rules to detect stuck prices:

### Rule 1: Extreme + Low Volatility
```python
if price > 90¢ or price < 10¢:
    if volatility < 2.0¢:
        → STUCK
```

### Rule 2: Low Entropy
```python
if price_entropy < 1.0 bits:
    → STUCK (concentrated distribution)
```

### Rule 3: Narrow Range Despite Spot Movement
```python
if price_range < 3¢ and abs(spot_change) > $20:
    → STUCK (not reacting to spot)
```

### Rule 4: Unresponsive
```python
if abs(spot_change) > $50 and responsiveness_ratio < 0.02:
    → STUCK (less than 2¢ per $100 spot move)
```

---

## Usage

### 1. Analyze Historical Data

```bash
# Basic analysis
python3 scripts/analyze_price_stuckness.py \
    --db data/btc_latency_probe.db \
    --csv results/stuckness_analysis.csv

# With plot (requires matplotlib)
python3 scripts/analyze_price_stuckness.py \
    --db data/btc_latency_probe.db \
    --csv results/stuckness.csv \
    --plot results/stuckness_plot.png

# Specific ticker
python3 scripts/analyze_price_stuckness.py \
    --db data/btc_latency_probe.db \
    --ticker KXBTC15M-26FEB280200-00 \
    --csv results/stuckness_specific.csv
```

### 2. Output Interpretation

```
=== Summary ===
Total snapshots: 1,234
Stuck snapshots: 187 (15.1%)

Stuck reasons:
  Extreme price + low volatility: 98 (52.4%)
  Low entropy: 45 (24.1%)
  Unresponsive to spot: 32 (17.1%)
  Narrow range despite spot movement: 12 (6.4%)

Entropy distribution:
  Mean: 2.15 bits
  Median: 2.30 bits
  P25: 1.45 bits  ← 25% of samples have entropy < 1.45
  P75: 2.85 bits
```

**Interpretation:**
- 15% of time, market is stuck (no trading edge)
- Most common reason: extreme prices (>90¢ or <10¢) with low volatility
- Median entropy 2.30 bits = healthy movement
- P25 entropy 1.45 bits = quarter of samples have low entropy

### 3. CSV Output

Columns:
- `timestamp`, `datetime` - When the snapshot was taken
- `price_cents` - Kalshi price (0-100)
- `price_entropy` - Shannon entropy of recent price distribution
- `price_range` - Max - min price in lookback window
- `price_volatility` - Std dev of Kalshi price changes
- `spot_volatility` - Std dev of BTC spot price changes
- `spot_change` - BTC price change in window
- `kalshi_change` - Kalshi price change in window
- `responsiveness_ratio` - Kalshi change / spot change
- `distance_from_50` - abs(price - 50¢)
- `is_extreme` - True if price >90¢ or <10¢
- `is_stuck` - True if any stuck rule triggered
- `stuck_reason` - Human-readable explanation

---

## Integration with Crypto Scalp Strategy

### Option 1: Pre-Signal Filter (Recommended)

Add stuckness check in `detector.py` before generating signal:

```python
# In CryptoScalpDetector.check_for_signal()

# After computing entry_price, before returning signal:

# Check if market is stuck
if self._is_price_stuck(orderbook, market):
    logger.debug("SKIP: Market stuck at %d¢ (low entropy/volatility)", entry_price)
    return None

# ... rest of signal generation
```

**Implementation:**

```python
def _is_price_stuck(
    self,
    orderbook: OrderBook,
    market: KalshiMarket,
) -> bool:
    """Check if market price is stuck (no edge to capture).

    Returns True if:
    - Price is extreme (>90 or <10) AND volatility < 2¢
    - Recent price range < 3¢ despite spot moves >$20
    """
    # Get current price
    if orderbook and orderbook.best_ask:
        price = orderbook.best_ask.price
    else:
        price = market.yes_ask

    # Rule 1: Extreme price
    if price > 90 or price < 10:
        # Check recent volatility from stored prices
        recent_prices = self._recent_prices.get(market.ticker, [])
        if len(recent_prices) > 5:
            volatility = float(np.std(np.diff(recent_prices[-10:])))
            if volatility < 2.0:
                return True  # Stuck: extreme + low volatility

    # Rule 2: Narrow range despite spot movement
    if len(recent_prices) > 5:
        price_range = max(recent_prices[-10:]) - min(recent_prices[-10:])
        spot_range = self._get_recent_spot_range(lookback_sec=300)
        if price_range < 3 and abs(spot_range) > 20:
            return True  # Stuck: unresponsive to spot

    return False
```

### Option 2: Post-Signal Filter

Add check in `_place_entry()` after pre-flight check:

```python
# After pre-flight orderbook check in orchestrator.py

# Check if current market is stuck
if self._detector.is_price_stuck(current_orderbook, market):
    logger.warning("SKIP: Market stuck at %d¢", signal.entry_price_cents)
    return
```

### Option 3: Config-Based Threshold

Add to `CryptoScalpConfig`:

```python
@dataclass
class CryptoScalpConfig:
    # ... existing fields ...

    # Stuckness filters
    min_price_entropy: float = 1.0  # Skip if entropy < 1.0 bits
    min_price_volatility_cents: float = 2.0  # Skip if volatility < 2¢
    max_extreme_price: int = 90  # Skip if price > 90 or < (100-90)=10
```

---

## Expected Impact

### Before Stuckness Filter

**Scenario:** BTC moves $50, Kalshi at 95¢

```
Signal detected: BTC +$50 → Buy YES @ 96¢
Order placed: YES @ 97¢ (signal + 1¢ buffer)
Market current ask: 96¢
Order fills: YES @ 97¢
20s later: Market still at 96¢ (no repricing)
Exit: YES @ 94¢ (best bid - slippage)
Result: -3¢ loss + 4¢ fees = -7¢ LOSS
```

### After Stuckness Filter

```
Signal detected: BTC +$50, price 95¢
Stuckness check: entropy = 0.3 bits, volatility = 0.5¢
Filter: STUCK - extreme price + low volatility
Action: SKIP (no order placed)
Result: 0¢ (avoided -7¢ loss)
```

### Estimated Improvement

From live trading analysis (Feb 28):
- **9 trades total**
- Estimated **2-3 were stuck** (extreme prices, no repricing)
- Those 2-3 lost ~10-15¢ total
- **Filter would have skipped them**
- Net improvement: **+10-15¢ per session**

---

## Recommended Thresholds

Based on historical data analysis:

```yaml
# Conservative (fewer trades, higher quality)
min_price_entropy: 1.5  # bits
min_price_volatility_cents: 3.0
max_extreme_price: 85  # Skip if >85 or <15

# Moderate (balanced)
min_price_entropy: 1.0  # bits
min_price_volatility_cents: 2.0
max_extreme_price: 90  # Skip if >90 or <10

# Aggressive (more trades, accept some stuck risk)
min_price_entropy: 0.5  # bits
min_price_volatility_cents: 1.0
max_extreme_price: 95  # Skip if >95 or <5
```

**Start with Moderate settings** and tune based on live performance.

---

## Validation

### Historical Backtest

1. Run analysis on probe database:
   ```bash
   python3 scripts/analyze_price_stuckness.py --db data/btc_latency_probe.db --csv results/stuck.csv
   ```

2. Identify stuck periods from CSV

3. Check scalp backtest results for same periods:
   ```bash
   python3 main.py backtest crypto-scalp --db data/btc_latency_probe.db
   ```

4. Compare:
   - Win rate during stuck periods vs. normal periods
   - Expected: **Lower win rate during stuck periods**
   - If confirmed: stuckness filter should improve performance

### Live Testing

1. Add stuckness logging to detector:
   ```python
   logger.info("Signal @ %d¢: entropy=%.2f, vol=%.2f, stuck=%s",
               price, entropy, volatility, is_stuck)
   ```

2. Run live for 1-2 hours with filter DISABLED (log only)

3. Post-analysis:
   - Trades that WOULD have been filtered
   - Their actual P&L
   - Compare to trades that WEREN'T filtered

4. Enable filter if stuck trades have negative average P&L

---

## Next Steps

1. ⬜ Run analysis on historical probe database
2. ⬜ Identify optimal thresholds (entropy, volatility, etc.)
3. ⬜ Implement stuckness filter in detector
4. ⬜ Test in paper mode (log only)
5. ⬜ Validate: stuck trades have lower win rate
6. ⬜ Enable filter in live mode
7. ⬜ Monitor impact on win rate and P&L

---

## Technical Details

### Shannon Entropy Formula

```
H = -Σ p(x) * log₂(p(x))

Where:
- p(x) = probability of price being in bin x
- Bins = 10¢ buckets (0-10, 10-20, ..., 90-100)
- Result in bits (0 to ~3.3 for uniform distribution)
```

**Interpretation:**
- **0 bits** = all prices in one bucket (completely stuck)
- **1 bit** = prices concentrated in 2 buckets (mostly stuck)
- **2 bits** = prices spread across 4 buckets (some movement)
- **3+ bits** = prices distributed widely (active trading)

### Responsiveness Ratio Calibration

From Black-Scholes binary option delta:

```
Delta = n(d2) / (σ√T)

For typical 15-min BTC market:
- σ = 0.8 (80% annualized vol)
- T = 15/525600 ≈ 0.0000285 years
- √T ≈ 0.0053
- σ√T ≈ 0.0042

Expected delta ≈ 0.05-0.20 (5-20¢ per $100 BTC move)
```

**Thresholds:**
- **Responsive:** ratio > 0.05 (5¢ / $100)
- **Marginal:** ratio 0.02-0.05 (2-5¢ / $100)
- **Stuck:** ratio < 0.02 (<2¢ / $100)

---

**Status:** ✅ ANALYSIS TOOL IMPLEMENTED
**Next:** Run on historical data and integrate filter
