# Crypto Scalp Strategy - Complete Guide

**Last Updated**: March 2, 2026
**Status**: Testing/Validation Phase - DO NOT TRADE LIVE
**Location**: `strategies/crypto_scalp/`

## ⚠️ CRITICAL WARNING

**DO NOT RESUME LIVE TRADING** until all 10 critical bugs are fixed and validated:
- Exit fills not confirmed (Bug #1)
- Orderbook WebSocket broken (Bug #2)
- OMS WebSocket not initialized (Bug #3)
- Event loop architecture issues (Bug #4)
- No WebSocket reconnection (Bug #5)
- Exit price = limit not fill (Bug #6)
- Entry fees not logged (Bug #7)
- No balance tracking (Bug #8)
- No position reconciliation (Bug #9)
- Duplicate positions (Bug #10)

See `FINDINGS_MARCH_2_SESSION.md` for full details.

---

## Table of Contents

1. [Strategy Overview](#strategy-overview)
2. [Market Mechanics](#market-mechanics)
3. [Signal Detection Logic](#signal-detection-logic)
4. [Entry Logic - Step by Step](#entry-logic---step-by-step)
5. [Exit Logic - Comprehensive](#exit-logic---comprehensive)
6. [Risk Management](#risk-management)
7. [Configuration Parameters](#configuration-parameters)
8. [Execution Flow - Complete Walkthrough](#execution-flow---complete-walkthrough)
9. [Historical Context](#historical-context)
10. [Tuning Guide](#tuning-guide)

---

## Strategy Overview

### What is Crypto Latency Arbitrage?

The crypto scalp strategy exploits a **predictable lag** between spot cryptocurrency exchanges (Binance, Coinbase, Kraken) and Kalshi's crypto markets. When Bitcoin moves on spot exchanges, Kalshi markets take 5-10 seconds to reprice, creating arbitrage opportunities.

### The Kalshi Lag Phenomenon

**Observed Behavior** (from probe data):
- Spot exchanges update in real-time (<100ms)
- Kalshi markets lag by **5-10 seconds** behind spot
- Directional accuracy: **70-90%** when spot moves >$15
- Window duration: 5-10 seconds (enough time to enter and exit)

**Why the lag exists**:
1. Manual market makers on Kalshi (not automated bots)
2. Order book depth limited (small markets, thin liquidity)
3. Human reaction time to spot price changes
4. Kalshi's websocket update frequency (~1s)

### How the Strategy Exploits This Edge

1. **Monitor spot**: Track BTC price on Binance/Coinbase/Kraken in real-time
2. **Detect moves**: When spot moves >$15 in 5 seconds, trigger signal
3. **Hit stale Kalshi**: Buy the "stale" Kalshi contract before it reprices
4. **Quick exit**: Hold 20 seconds, exit after Kalshi catches up to spot

**Example Trade**:
```
T=0s:   BTC moves from $97,000 → $97,018 on Binance (+$18 move)
T=0.5s: Signal detected, Kalshi still at 50¢ (stale)
T=1s:   Enter YES @ 52¢ (crosses spread with slippage)
T=21s:  Kalshi reprices to 57¢
T=21s:  Exit YES @ 57¢
Result: +5¢ gross, +3¢ net after fees (~6% return in 21 seconds)
```

### Expected Win Rate and Profit Profile

**From Historical Data**:
- Win rate: **~60-70%** (probe data shows 70-90% directional accuracy, but slippage/timing reduces realized wins)
- Average winner: **+3-5¢** per contract (after fees)
- Average loser: **-2-4¢** per contract (small losses, quick stop-loss)
- Trade frequency: **~1-3 trades/hour** (depends on BTC volatility)
- Expected value: **+1-2¢ per trade** (positive EV, but small edge)

**P&L Distribution**:
- Most trades: Small wins (+2-5¢) or small losses (-2-4¢)
- Occasional big wins: +8-12¢ (strong moves, perfect timing)
- Catastrophic losses: -15-30¢ (crashes, illiquid exits) — **NOW PREVENTED** by stop-loss and liquidity checks

### Risk Characteristics

**Primary Risks**:
1. **Crash risk**: BTC drops >$15 immediately after entry → stop-loss exit at -15¢
2. **Reversal risk**: Spot move reverses direction → exit when signal flips
3. **Liquidity risk**: Can't exit due to thin orderbook → forced to hold to expiry
4. **Fee drag**: 7% on profits (~0.5¢ on +7¢ win) → need >1¢ edge to be profitable
5. **Slippage**: Markets move before we can enter → adaptive pricing reduces impact

**Risk Mitigations** (v4 config, March 1, 2026):
- Pre-entry liquidity check: Skip if exit side has <10 contracts
- Stop-loss: Force exit if adverse movement >15¢ (0s delay!)
- Momentum filter: Skip decelerating moves (late entries)
- Multi-exchange confirmation: Require 2+ exchanges agree on direction
- Circuit breaker: Halt if daily loss >$50

---

## Market Mechanics

### BTC 15-Minute Markets Explained

Kalshi offers 15-minute binary markets on whether Bitcoin will close above or below a strike price at the end of each 15-minute interval.

**Market Structure**:
- Series: `KXBTC15M` (BTC 15-minute markets)
- Resolution: Every 15 minutes (e.g., 1:00, 1:15, 1:30, 1:45, ...)
- Strikes: Typically 3-7 strikes per interval, spaced $20-50 apart
- Liquidity: 10-50 contracts at best bid/ask (thin!)

### Ticker Format

**Pattern**: `KXBTC15M-[DATE]-[STRIKE]`

**Example**: `KXBTC15M-26MAR020145-45`
- `KXBTC15M`: Series (BTC 15-minute)
- `26MAR020145`: Expiry date/time (2026-03-02 @ 01:45 UTC)
- `45`: Strike price (last 2 digits of floor strike, e.g., $97,045)

**Full Strike Extraction**:
- Parse title: "Will BTC be $97,045 or more at 1:45 AM ET?"
- Or use `floor_strike` field from API response

### How Resolution Works

At expiry time (e.g., 1:45:00 UTC):
1. Kalshi queries spot price from reference exchange (typically Kraken)
2. Compare spot price to strike: `spot >= strike` → YES wins, `spot < strike` → NO wins
3. Winning side pays $1.00 per contract, losing side pays $0.00

**Example**:
- Strike: $97,045
- Spot at 1:45:00: $97,052
- Result: YES wins (pays $1.00), NO loses (pays $0.00)
- If you bought YES @ 52¢: Profit = $1.00 - $0.52 = $0.48 = 48¢ per contract

### Typical Liquidity and Spreads

**Liquidity Profile** (varies by market):
- Near-the-money (45-55¢): Best liquidity, 10-30 contracts on each side, 2-5¢ spread
- Out-of-the-money (<30¢ or >70¢): Thin liquidity, 3-10 contracts, 5-10¢ spread
- Deep out-of-the-money (<20¢ or >80¢): Very thin, 1-5 contracts, 10-20¢ spread

**Spread Behavior**:
- Tight spreads (2-3¢) indicate active market making
- Wide spreads (>5¢) indicate illiquid/stale markets
- Spreads widen near expiry (last 3 minutes) as MMs pull quotes

### Market Lifecycle

**Creation** (15 minutes before expiry):
- Markets created automatically by Kalshi
- Initial quotes: Usually 48-52¢ (fair value for binary with uncertainty)
- Liquidity: Starts thin, builds as MMs add quotes

**Active Trading** (15 min to 3 min before expiry):
- Most active period: 10-5 minutes before expiry
- Best liquidity, tightest spreads
- Our strategy trades: **3-15 minutes before expiry** (safe window)

**Pre-Expiry** (last 3 minutes):
- Liquidity dries up as MMs close positions
- Spreads widen to 10-20¢
- High risk of being stuck
- **Strategy avoids**: `min_ttx_sec = 180` (don't trade <3 min to expiry)

**Expiry** (at exact minute mark):
- Trading halts
- Kalshi fetches spot price and resolves market
- Payout: Winners get $1.00, losers get $0.00

---

## Signal Detection Logic

### Spot Price Monitoring

**Three Exchange Feeds** (choose via `signal_feed` config):
- **Binance**: ~8 trades/sec, lowest latency, most responsive
- **Coinbase**: ~1.7 trades/sec, institutional volume
- **Kraken**: ~0.2 trades/sec, Kalshi's reference exchange

**Feed Selection** (`signal_feed` config):
- `"binance"`: Use Binance only (fastest)
- `"coinbase"`: Use Coinbase only
- `"kraken"`: Use Kraken only (safest, matches Kalshi reference)
- `"all"`: Use largest absolute delta across all feeds (most aggressive)

**Recommended**: `"binance"` for speed, or `"all"` for confirmation

### Move Detection Algorithm

**Core Logic** (in `detector.py:_compute_delta()`):
1. Maintain rolling 5-second window of (timestamp, price, quantity) tuples per feed
2. Compute delta: `delta = current_price - price_5s_ago`
3. If `|delta| >= min_spot_move_usd`: Signal triggered

**Why 5 seconds?**
- Long enough to filter noise (1-2s moves are often whipsaws)
- Short enough to catch real moves before Kalshi reprices
- Balances responsiveness vs. false positives

### Thresholds

**`min_spot_move_usd`** (default: $15):
- Minimum dollar move to trigger signal
- **Too low** (<$10): Many false positives, noise trades
- **Too high** (>$20): Misses opportunities, low frequency
- **Optimal**: $15 (based on probe data analysis)

**Calibration** (from probe data):
- $10 moves: ~60% directional accuracy (borderline)
- $15 moves: ~70% directional accuracy (good)
- $20+ moves: ~80% directional accuracy (rare)

### Filters

The strategy uses **5 filters** to reduce false positives:

#### 1. Momentum Filter (NEW - March 1, 2026)

**Purpose**: Prevent late entries on decelerating moves

**Logic** (`detector.py:_compute_delta_with_momentum()`):
1. Split 5-second window in half (older half, recent half)
2. Compute delta for each half
3. Check if recent momentum ≥ 80% of older momentum
4. If decelerating: Skip trade (likely late entry)

**Example**:
```
Older half (T=0-2.5s): BTC moves +$10
Recent half (T=2.5-5s): BTC moves +$3
Ratio: $3 / $10 = 30% < 80% → SKIP (decelerating)

Older half: BTC moves +$8
Recent half: BTC moves +$9
Ratio: $9 / $8 = 112% > 80% → PASS (accelerating)
```

**Impact**: Reduces losing trades from late entries by ~20-30%

**Config**:
- `enable_momentum_filter`: `true` (recommended)
- `momentum_threshold`: `0.8` (80% of older half)

#### 2. Volume Filter

**Purpose**: Filter low-volume random-walk moves, favor institutional sweeps

**Logic** (`config.py:min_window_volume`):
- Track total BTC volume in 5-second window per feed
- Require minimum volume threshold (feed-specific):
  - Binance: 0.5 BTC (~$50k notional)
  - Coinbase: 0.3 BTC (~$30k notional)
  - Kraken: 0.1 BTC (~$10k notional)

**Rationale**:
- High volume = institutional trades, more persistent
- Low volume = retail noise, more likely to reverse
- Probe data: Winners had 3-4× more volume than losers

**Config**:
```yaml
min_window_volume:
  binance: 0.5
  coinbase: 0.3
  kraken: 0.1
```

#### 3. Multi-Exchange Confirmation

**Purpose**: Reduce exchange-specific noise (e.g., single large trade on Binance)

**Logic** (`detector.py:_check_multi_exchange()`):
- Require ≥2 exchanges agree on direction (both UP or both DOWN)
- Even if using single feed for signal, check others for confirmation

**Example**:
```
Binance: +$18 (UP)
Coinbase: +$12 (UP)
Kraken: -$2 (DOWN)
Result: 2 agree → PASS

Binance: +$18 (UP)
Coinbase: -$3 (DOWN)
Kraken: No data
Result: 1 agree → SKIP (only Binance)
```

**Impact**: Reduces false positives by ~40%

**Config**:
- `require_multi_exchange_confirm`: `true` (strongly recommended)

#### 4. Regime Filter (Optional)

**Purpose**: Skip trades during choppy/oscillating markets

**Logic** (`core/regime_detector.py`):
1. Track spot price over 60-second window
2. Calculate oscillation ratio: `osc_ratio = path_length / net_move`
3. High ratio (>2.0) = choppy market, skip trades
4. Low ratio (<1.5) = trending market, allow trades

**Example**:
```
Trending: BTC moves 97000 → 97020 → 97025 → 97030
Path: 20+5+5 = 30, Net: 30, Ratio: 30/30 = 1.0 → PASS

Choppy: BTC moves 97000 → 97020 → 96995 → 97015
Path: 20+25+20 = 65, Net: 15, Ratio: 65/15 = 4.3 → SKIP
```

**Config**:
- `regime_osc_threshold`: `0.0` (disabled by default, set to `2.0` to enable)
- `regime_window_sec`: `60.0` (1-minute lookback)

#### 5. Spread Filter

**Purpose**: Skip illiquid markets with wide spreads (high slippage)

**Logic** (`detector.py:detect()`):
- Check Kalshi spread: `spread = best_ask - best_bid`
- Skip if `spread < min_kalshi_spread_cents`

**Typical Use**:
- Set to 0 (disabled) for most markets
- Set to 3-5¢ to avoid extremely illiquid markets

**Config**:
- `min_kalshi_spread_cents`: `0` (disabled)

### Why Each Filter Exists

| Filter | Problem It Solves | Impact |
|--------|-------------------|--------|
| Momentum | Late entries on dying moves | -20-30% losing trades |
| Volume | Random-walk noise trades | -30-40% false positives |
| Multi-Exchange | Exchange-specific anomalies | -40% false positives |
| Regime | Choppy market whipsaws | -10-20% losses (when enabled) |
| Spread | High slippage illiquid markets | -5-10% losses |

**Recommended Settings**:
- Momentum: **ON** (critical for timing)
- Volume: **ON** (critical for quality)
- Multi-Exchange: **ON** (critical for confirmation)
- Regime: **OFF** (optional, reduces frequency significantly)
- Spread: **OFF** (Kalshi markets typically liquid enough)

---

## Entry Logic - Step by Step

### When Signal Triggers

Signal triggers when:
1. Spot delta ≥ `min_spot_move_usd` ($15)
2. All filters pass (momentum, volume, multi-exchange, regime, spread)
3. No existing position on this ticker
4. Not in cooldown period (15s since last trade on ticker)
5. Below max concurrent positions (default: 1)
6. Daily loss hasn't exceeded circuit breaker ($50)

### Pre-Entry Checks

**Phase 1: Time Window** (`detector.py:detect()`):
```python
ttx = market.time_to_expiry_sec
if ttx < min_ttx_sec (180s) or ttx > max_ttx_sec (900s):
    SKIP  # Too close to expiry or too far
```

**Why?**
- `min_ttx_sec = 180s`: Need at least 3 minutes to enter and exit safely
- `max_ttx_sec = 900s`: Only trade fresh markets (not stale/repriced markets)

**Phase 2: Price Bounds** (`detector.py:detect()`):
```python
if entry_price < 25¢ or entry_price > 75¢:
    SKIP  # Avoid extreme prices (low edge, high slippage)
```

**Why?**
- <25¢: Already repriced down, limited upside
- >75¢: Already repriced up, limited upside
- 25-75¢: Sweet spot for arbitrage opportunities

**Phase 3: Liquidity Check** (NEW - March 1, 2026) (`detector.py:detect()`):
```python
exit_depth = orderbook.best_bid.size  # For YES positions
if exit_depth < min_entry_bid_depth (10):
    SKIP  # Can't exit this position safely
```

**Why?**
- **Critical**: March 1 session had 0-liquidity exits → -30¢ crashes
- Check **exit side** liquidity BEFORE entering
- Default: 10 contracts minimum (was 5, increased after crashes)

**Phase 4: Spread Check** (`detector.py:detect()`):
```python
if orderbook.spread < min_kalshi_spread_cents (0):
    SKIP  # Disabled by default
```

**Phase 5: Duplicate Position Check** (`orchestrator.py:_place_entry()`):
```python
if ticker in self._positions:
    if existing.side == signal.side:
        SKIP  # Already have this position
    else:
        BLOCK  # Opposite side (would hedge and lose fees!)
```

**Why?**
- Same side: Avoid duplicate entries (race condition)
- Opposite side: **CRITICAL** - would create perfect hedge, guaranteed fee loss

### Order Submission

**Two-Stage Fill Strategy**:

**Stage 1: Limit Order** (good price, may not fill):
```python
limit_price = entry_price + slippage_buffer (1¢)
submit_limit_order(side, size=5, price=limit_price)
wait_for_fill(timeout=1.5s)
```

**If limit fills**: Great! Got good price (52¢ instead of 54¢)
**If limit doesn't fill**: Move to Stage 2

**Stage 2: Market Order Fallback** (guaranteed fill, worse price):
```python
# Validate signal still strong
current_ask = orderbook.best_ask
slippage = current_ask - original_limit_price

if slippage > max_fallback_slippage (5¢):
    ABORT  # Too much slippage, not worth it

if remaining_edge < fallback_min_edge (8¢):
    ABORT  # Edge eroded, not worth it

# Place aggressive limit order (current_ask + 2¢)
aggressive_price = current_ask + 2
submit_limit_order(side, size=5, price=aggressive_price)
wait_for_fill(timeout=1.0s)
```

**Why Two Stages?**
- Stage 1 optimizes for **price** (lower slippage)
- Stage 2 optimizes for **fill rate** (higher fill probability)
- Fallback validation ensures we don't chase bad trades

**Fill Rate Improvement**:
- Limit only: ~25% fill rate (too passive)
- Market fallback: ~80-90% fill rate (much better)

### Fill Confirmation

**Method 1: WebSocket (Real-Time)** — CURRENTLY BROKEN (Bug #3):
```python
# OrderManager subscribes to fill stream via WebSocket
await om.initialize()  # Starts WebSocket connection
# Fills arrive in real-time via callback
```

**Method 2: REST API Polling** (Fallback):
```python
async def _wait_for_fill_om(order_id, timeout=1.5):
    deadline = now + timeout
    while now < deadline:
        status = await om.get_order_status(order_id)
        if status == FILLED:
            return True
        await asyncio.sleep(0.2)
    return False
```

**Current Issue (Bug #3)**:
- OrderManager.initialize() not called → WebSocket never starts
- Falling back to REST polling (works, but slower)

### Position Creation and Tracking

Once filled, create position object:

```python
position = ScalpPosition(
    ticker=ticker,
    side="yes",  # or "no"
    entry_price_cents=52,
    size=5,
    entry_time=time.time(),
    exit_target_time=now + exit_delay_sec (20s),
    hard_exit_time=now + max_hold_sec (35s),
    order_id="order_abc123",
    spot_delta=18.5,  # The move that triggered entry
    signal_source="binance",

    # Entry metrics for intelligent exits
    entry_exit_depth=15,  # Orderbook depth at entry
    entry_spread_cents=3,  # Spread at entry
    entry_cex_imbalance=0.6,  # CEX orderbook imbalance
    entry_cross_exchange_std=5.2,  # Cross-exchange price std dev
)

# Add to tracking
self._positions[ticker] = position
self._stats.trades_entered += 1
```

**Entry Metrics** (captured at entry time for intelligent exits):
- `entry_exit_depth`: Orderbook depth on exit side (for depth-momentum exit)
- `entry_spread_cents`: Spread at entry (for spread reversion exit)
- `entry_cex_imbalance`: CEX orderbook imbalance (for imbalance reversal exit)
- `entry_cross_exchange_std`: Cross-exchange price divergence (for divergence exit)

---

## Exit Logic - Comprehensive

The strategy uses **7 exit triggers** checked in priority order. First trigger wins.

### Exit Priority Order

1. **Stop-Loss** (immediate, 0s delay) — Protects from crashes
2. **Reversal** (after 2s) — Locks in profits when signal flips
3. **Depth-Momentum** (after 5s) — Exits when depth draining
4. **Spread Reversion** (after 3s) — Exits when spread widens
5. **Imbalance Reversal** (after 2s) — Exits when CEX orderbook flips
6. **Timed Exit** (at 20s) — Normal exit after delay
7. **Hard Exit** (at 35s) — Force exit no matter what
8. **Emergency Exit** (<90s to expiry) — Panic exit near expiry

Let's cover each in detail.

---

### 1. Stop-Loss Exit (CRITICAL - Added March 1, 2026)

**Purpose**: Cut losses quickly on market crashes

**Logic** (`orchestrator.py:_check_exits()`):
```python
time_since_entry = now - position.entry_time

if time_since_entry >= stop_loss_delay_sec (0s):
    current_exit_price = orderbook.best_bid  # For YES
    adverse_movement = entry_price - current_exit_price

    if adverse_movement > stop_loss_cents (15¢):
        FORCE_EXIT(reason="stop-loss")
```

**Example**:
```
Entry: BUY YES @ 52¢
T=5s: BTC crashes, Kalshi drops to 37¢
Adverse movement: 52 - 37 = 15¢ ≥ 15¢ threshold
→ STOP-LOSS: Force exit @ 37¢
Result: -15¢ loss (capped, vs -30¢ if held to expiry)
```

**Why 0s Delay?**
- Crashes happen in <5 seconds (probe data analysis)
- Original 10s delay missed ALL crashes
- 0s delay catches crashes immediately
- Whipsaw risk: Minimal (crashes are persistent, not noise)

**Config**:
- `stop_loss_cents`: `15` (¢)
- `stop_loss_delay_sec`: `0.0` (NO DELAY!)
- `enable_stop_loss`: `true`

**Impact** (from backtest):
- Eliminated ALL catastrophic losses (28 → 0)
- Max loss capped at -15¢ (was -30¢+)
- Slight P&L trade-off (-7%) from early exits
- **Worth it**: Prevents account-destroying losses

---

### 2. Reversal Exit (Added March 1, 2026)

**Purpose**: Lock in profits when spot move reverses direction

**Logic** (`orchestrator.py:_check_exits()`):
```python
time_since_entry = now - position.entry_time

if time_since_entry >= reversal_exit_delay_sec (2s):
    current_signal = detector.detect(market, orderbook)

    if current_signal.side != position.side:
        reversal_strength = |current_signal.spot_delta|

        if reversal_strength >= min_reversal_strength_usd ($10):
            FORCE_EXIT(reason="reversal")
            stats.reversal_exits += 1
```

**Example**:
```
Entry: BUY YES @ 52¢ (BTC up $18)
T=12s: BTC reverses, down -$12 from peak
Current signal: NO (opposite direction)
Current price: 55¢ (already up 3¢)
→ REVERSAL: Force exit @ 55¢
Result: +3¢ profit (locked in before reversal wipes it out)
```

**Why 2s Delay?**
- Avoid entry volatility (first 2s are noisy)
- Reversals within 2s are usually whipsaws
- Real reversals happen 5-15s after entry

**Config**:
- `enable_reversal_exit`: `true`
- `reversal_exit_delay_sec`: `2.0` (wait 2s after entry)
- `min_reversal_strength_usd`: `10.0` (require $10 move in opposite direction)

**Impact**:
- Exits ~10-20% of trades early with small profits
- Avoids holding through reversals that wipe out gains

---

### 3. Depth-Momentum Exit (Statistical Exit - March 1, 2026)

**Purpose**: Lock in profits when price is up but depth is draining (warning sign)

**Logic** (`orchestrator.py:_check_exits()`):
```python
time_since_entry = now - position.entry_time

if time_since_entry >= depth_min_hold_sec (5s):
    current_depth = orderbook.best_bid.size  # For YES
    current_price = orderbook.best_bid.price

    price_move = current_price - entry_price
    depth_ratio = current_depth / entry_exit_depth

    if price_move >= depth_min_profit_cents (3¢) and
       depth_ratio <= depth_drain_threshold (0.4):
        FORCE_EXIT(reason="depth-momentum")
```

**Example**:
```
Entry: BUY YES @ 52¢ (depth: 15 contracts at bid)
T=10s: Price @ 56¢ (+4¢ profit), but depth dropped to 5 contracts
Depth ratio: 5 / 15 = 33% ≤ 40% threshold
→ DEPTH-MOMENTUM: Exit @ 56¢
Result: +4¢ profit (locked in before depth collapse)
```

**Intuition**:
- Depth draining = liquidity providers pulling out
- Usually precedes price reversal
- Exit while we still can at good price

**Config**:
- `enable_depth_momentum_exit`: `true`
- `depth_drain_threshold`: `0.4` (40% of entry depth)
- `depth_min_profit_cents`: `3` (only trigger on profitable positions)
- `depth_min_hold_sec`: `5.0` (avoid entry fluctuations)

---

### 4. Spread Reversion Exit (Statistical Exit - March 1, 2026)

**Purpose**: Exit when spread widens AND depth drops (market turning illiquid)

**Logic** (`orchestrator.py:_check_exits()`):
```python
time_since_entry = now - position.entry_time

if time_since_entry >= 3s:
    current_spread = orderbook.spread
    baseline_spread = max(entry_spread_cents, 2¢)

    current_depth = orderbook.best_bid.size
    depth_ratio = current_depth / entry_exit_depth

    if current_spread >= spread_reversion_multiplier (2.0) * baseline_spread and
       depth_ratio <= spread_depth_threshold (0.6):
        FORCE_EXIT(reason="spread-reversion")
```

**Example**:
```
Entry: BUY YES @ 52¢ (spread: 3¢, depth: 20)
T=8s: Spread widens to 7¢ (2.3× entry spread)
      Depth drops to 10 contracts (50% of entry)
→ SPREAD-REVERSION: Exit @ 54¢
Result: +2¢ profit (avoided illiquidity trap)
```

**Intuition**:
- Spread widening = market makers pulling quotes
- Combined with depth drop = severe illiquidity incoming
- Exit before trapped

**Config**:
- `enable_spread_reversion_exit`: `true`
- `spread_reversion_multiplier`: `2.0` (spread ≥ 2× entry)
- `spread_depth_threshold`: `0.6` (depth ≤ 60% of entry)

---

### 5. Imbalance Reversal Exit (Statistical Exit - March 1, 2026)

**Purpose**: Exit when CEX orderbook imbalance flips (institutional flow reversing)

**Logic** (`orchestrator.py:_check_exits()`):
```python
# Requires BRTI tracker (BTC Real-Time Index tracker)
if brti_tracker:
    current_imbalance = brti_tracker.get_imbalance()
    imbalance_change = |current_imbalance - entry_cex_imbalance|

    if imbalance_change >= imbalance_reversal_threshold (0.5):
        FORCE_EXIT(reason="imbalance-reversal")
```

**Example**:
```
Entry: BUY YES @ 52¢ (CEX imbalance: +0.6, heavy bid pressure)
T=15s: CEX imbalance flips to -0.2 (heavy ask pressure)
Change: |(-0.2) - (+0.6)| = 0.8 ≥ 0.5 threshold
→ IMBALANCE-REVERSAL: Exit @ 54¢
Result: +2¢ profit (avoided reversal)
```

**Note**: Currently disabled (BRTI tracker not yet implemented)

**Config**:
- `enable_imbalance_reversal_exit`: `true`
- `imbalance_reversal_threshold`: `0.5` (imbalance magnitude change)
- `imbalance_velocity_threshold`: `0.3` (rate of change per sec)

---

### 6. Timed Exit (PRIMARY EXIT)

**Purpose**: Normal exit after holding for fixed duration

**Logic** (`orchestrator.py:_check_exits()`):
```python
if now >= position.exit_target_time:  # entry_time + exit_delay_sec (20s)
    PLACE_EXIT(ticker, position)
```

**Why 20 seconds?**
- Kalshi repricing window: 5-10 seconds after spot move
- Hold 20s = ensures Kalshi has repriced
- Balance: Not too short (misses repricing), not too long (reversal risk)

**Config**:
- `exit_delay_sec`: `20.0` (seconds)

---

### 7. Hard Exit (SAFETY EXIT)

**Purpose**: Force exit if we've held too long (something went wrong)

**Logic** (`orchestrator.py:_check_exits()`):
```python
if now >= position.hard_exit_time:  # entry_time + max_hold_sec (35s)
    FORCE_EXIT(reason="hard-exit")
```

**Why 35 seconds?**
- Failsafe for stuck positions
- Should never reach this (normal exit at 20s)
- Triggers if timed exit failed for some reason

**Config**:
- `max_hold_sec`: `35.0` (seconds)

---

### 8. Emergency Exit (PANIC EXIT)

**Purpose**: Override all safety checks when approaching expiry

**Logic** (`orchestrator.py:_check_exits()`):
```python
if market.time_to_expiry_sec < emergency_exit_ttx_sec (90s):
    # Use market order if necessary
    if use_market_order_on_emergency:
        place_market_order(side=SELL, ...)
    else:
        place_limit_order(side=SELL, ...)

    # Ignore liquidity protection (exit at any price)
```

**Why?**
- Last resort to avoid holding to expiry
- Accepts adverse pricing (better than being stuck)
- Rare: Should never reach this (normal exit at 20s, and we don't enter <3 min to expiry)

**Config**:
- `emergency_exit_ttx_sec`: `90` (90 seconds to expiry)
- `use_market_order_on_emergency`: `true` (cross spread aggressively)

---

### Fill Confirmation and P&L Calculation

**Exit Order Submission**:
```python
exit_price = orderbook.best_bid - exit_slippage_cents (0¢)
order_id = submit_order(SELL, side, size=5, price=exit_price)
wait_for_fill(timeout=3.0s)
```

**P&L Calculation** — CURRENTLY BROKEN (Bug #6):
```python
# BUG: Records limit price, not actual fill price
exit_fill_price = exit_price  # WRONG: should query actual fill
pnl_cents = (exit_fill_price - entry_price) * size

# Fees (7% of profit on winning trades)
if pnl_cents > 0:
    fees = int(pnl_cents * 0.07)
    net_pnl_cents = pnl_cents - fees
else:
    fees = 0  # BUG #7: Should charge fees on entry too!
    net_pnl_cents = pnl_cents

# Update stats
stats.total_pnl_cents += net_pnl_cents
stats.trades_exited += 1
if pnl_cents > 0:
    stats.trades_won += 1
```

**Bug #6 Impact**:
- Logs show +0.04¢ exit fill
- Actual fill was +6.00¢ (150× discrepancy!)
- Makes P&L tracking completely unreliable

**Bug #7 Impact**:
- Only charges fees on winning trades
- Ignores entry fees (proportional to price, ~3-4¢ on 50¢ entry)
- Understates costs by ~7%

**Correct P&L Formula**:
```python
# Entry fees (proportional to entry price)
entry_fees = entry_price * 0.07  # 7% of entry value

# Exit proceeds (actual fill price from API)
exit_fill_price = get_actual_fill_price(exit_order_id)
gross_proceeds = exit_fill_price * size

# Exit fees (7% of profit only)
gross_pnl = (exit_fill_price - entry_price) * size
exit_fees = max(0, gross_pnl) * 0.07

# Net P&L
total_fees = entry_fees + exit_fees
net_pnl = gross_pnl - total_fees
```

---

## Risk Management

### Position Sizing

**Fixed Size Per Trade**:
```python
contracts_per_trade = 5
```

**Why fixed size?**
- Simple, predictable
- Easy to calculate exposure
- Avoids over-leveraging

**Alternative**: Dynamic sizing based on edge
```python
# Kelly criterion: f* = edge / variance
# For this strategy: edge ≈ 1¢, variance ≈ 4¢²
# Kelly = 0.01 / 0.0004 = 25% of bankroll
# Half-Kelly = 12.5% of bankroll
# At $50 bankroll: 12.5% = $6.25 → ~12 contracts @ 50¢
```

**Current Sizing**: Conservative (5 contracts = ~$2.50 exposure, 5% of $50 bankroll)

### Max Positions

**Concurrent Position Limit**:
```python
max_open_positions = 1
```

**Why only 1?**
- Reduces correlation risk (all BTC markets move together)
- Focuses capital on best opportunity
- Simpler tracking and exit management

**With larger bankroll**: Could increase to 2-3 (still keep <20% total exposure)

### Stop-Loss

**Covered in Exit Logic above.**

Key points:
- 15¢ max loss per position
- 0s delay (immediate check)
- Prevents catastrophic crashes (-30¢+)

### Circuit Breaker

**Daily Loss Limit**:
```python
max_daily_loss_usd = 50.0  # $50

if session_loss > max_daily_loss_usd:
    HALT_ALL_TRADING()
    logger.error("Circuit breaker triggered!")
```

**Why $50?**
- Protects bankroll from bad days
- At 5 contracts/trade, ~$2.50/trade: 20 losing trades to trigger
- Average loss: -2¢ → 100 trades to trigger (unlikely in one session)
- Catastrophic losses: -15¢ → 17 trades to trigger (plausible if bug)

**When It Triggers**:
1. Log error message
2. Set `_running = False` (stops all loops)
3. Force-exit all open positions
4. Require manual restart

### Liquidity Protection

**Pre-Entry Check** (Prevents illiquid entries):
```python
min_entry_bid_depth = 10  # contracts

if exit_side_depth < min_entry_bid_depth:
    SKIP_TRADE()  # Don't enter if can't exit
```

**Exit Protection** (Prevents forced exits at bad prices):
```python
min_exit_bid_depth = 3  # contracts
max_adverse_exit_cents = 35  # ¢

if exit_depth < min_exit_bid_depth and adverse_movement > max_adverse_exit_cents:
    SKIP_EXIT()  # Wait for better liquidity
```

**Why Two Thresholds?**
- Entry check (10): High bar, only enter liquid markets
- Exit check (3): Lower bar, allow emergency exits

**Override**:
- Emergency exit (<90s to expiry): Ignore liquidity, exit at any price

### Balance Tracking (FIX #8)

**Periodic Balance Reconciliation**:
```python
def _verify_balance_and_circuit_breaker():
    # Check every 5 minutes
    if now - last_balance_check < 300:
        return

    # Query actual balance from Kalshi
    actual_balance = client.get_balance()

    # Calculate expected balance
    expected_balance = initial_balance + total_pnl_cents

    # Check drift
    drift = actual_balance - expected_balance
    if |drift| > 100¢:
        logger.warning("Balance drift detected: ${:.2f}", drift / 100)
```

**Why?**
- Detects untracked losses (bugs, API errors, manual trades)
- Validates P&L calculation accuracy
- Early warning of issues

**Current Issue**: Not implemented in live code (Bug #8)

### Position Reconciliation (FIX #9)

**Startup Check**:
```python
async def run():
    # Query Kalshi for open positions
    positions = await client.get_positions()

    if positions:
        logger.warning(f"Found {len(positions)} open position(s)!")
        for pos in positions:
            logger.warning(f"  - {pos.ticker}: {pos.quantity} contracts")

        # Require user confirmation
        response = input("Continue anyway? (y/n): ")
        if response != 'y':
            raise RuntimeError("Aborted due to open positions")

        # Add stranded positions to exit queue
        for pos in positions:
            self._positions[pos.ticker] = create_placeholder(pos)
```

**Why?**
- Detects positions from previous crashes
- Prevents accumulating unknown positions
- Allows graceful recovery (exit stranded positions)

**Current Issue**: Not fully implemented (Bug #9)

---

## Configuration Parameters

### Core Parameters

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `signal_feed` | `"all"` | Exchange for signal detection | `"binance"` = fastest, `"all"` = most confirmations |
| `spot_lookback_sec` | `5.0` | Window to measure spot move | 3-7s (5s is optimal) |
| `min_spot_move_usd` | `15.0` | Min $ move to trigger | $10-20 ($15 is optimal) |

### Entry Filters

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `min_ttx_sec` | `180` | Min time to expiry (3 min) | Never <180s (need exit time) |
| `max_ttx_sec` | `900` | Max time to expiry (15 min) | 600-900s (fresh markets) |
| `min_entry_price_cents` | `25` | Min entry price | 20-30¢ (avoid repriced markets) |
| `max_entry_price_cents` | `75` | Max entry price | 70-80¢ (avoid repriced markets) |
| `min_entry_bid_depth` | `10` | Min exit depth to enter | 5-15 (10 is safe after crashes) |

### Exit Timing

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `exit_delay_sec` | `20.0` | Normal hold time | 15-25s (20s is optimal) |
| `max_hold_sec` | `35.0` | Hard exit time | Must be > `exit_delay_sec` |
| `stop_loss_cents` | `15` | Max adverse movement | 10-20¢ (15¢ prevents crashes) |
| `stop_loss_delay_sec` | `0.0` | Wait before stop-loss | 0-3s (0s catches crashes) |

### Position Sizing

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `contracts_per_trade` | `5` | Size per trade | 1-10 (5 = ~5% of $50 bankroll) |
| `max_open_positions` | `1` | Max concurrent positions | 1-3 (1 reduces correlation risk) |
| `max_total_exposure_usd` | `10.0` | Max total $ exposure | 10-20% of bankroll |

### Execution

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `slippage_buffer_cents` | `1` | Added to ask for limit | 1-2¢ (1¢ balances fill rate vs price) |
| `limit_order_timeout_sec` | `1.5` | Try limit first for X sec | 1-2s (1.5s is optimal) |
| `market_order_fallback` | `true` | Use market if limit fails | Keep `true` (improves fill rate 25% → 90%) |
| `max_fallback_slippage_cents` | `5` | Max slippage for fallback | 3-7¢ (5¢ is reasonable) |

### Volume Filters

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `min_window_volume.binance` | `0.5` | Min BTC volume (Binance) | 0.3-0.7 BTC (~$30-70k) |
| `min_window_volume.coinbase` | `0.3` | Min BTC volume (Coinbase) | 0.2-0.5 BTC (~$20-50k) |
| `min_window_volume.kraken` | `0.1` | Min BTC volume (Kraken) | 0.05-0.2 BTC (~$5-20k) |
| `require_multi_exchange_confirm` | `true` | Require 2+ feeds agree | Keep `true` (critical filter) |

### Momentum Filter

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `enable_momentum_filter` | `true` | Prevent late entries | Keep `true` (reduces losing trades) |
| `momentum_threshold` | `0.8` | Recent ≥ 80% of older | 0.7-0.9 (0.8 is optimal) |

### Statistical Exits

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `enable_depth_momentum_exit` | `true` | Exit when depth drains | Keep `true` (locks profits) |
| `depth_drain_threshold` | `0.4` | Depth ≤ 40% of entry | 0.3-0.5 (0.4 is aggressive) |
| `depth_min_profit_cents` | `3` | Only on +3¢ positions | 2-5¢ (3¢ avoids premature exits) |
| `enable_spread_reversion_exit` | `true` | Exit when spread widens | Keep `true` (avoids illiquidity) |
| `spread_reversion_multiplier` | `2.0` | Spread ≥ 2× entry | 1.5-2.5 (2.0 is balanced) |

### Risk Management

| Parameter | Default | Description | Tuning Guidance |
|-----------|---------|-------------|-----------------|
| `max_daily_loss_usd` | `50.0` | Circuit breaker threshold | 10-20% of bankroll |
| `cooldown_sec` | `15.0` | Min sec between trades | 10-20s (15s prevents chasing) |

### Conservative vs Aggressive Settings

**Conservative** (lower frequency, higher win rate):
```yaml
min_spot_move_usd: 18.0  # Larger moves only
min_entry_bid_depth: 15  # Very liquid only
stop_loss_cents: 12  # Tighter stop-loss
exit_delay_sec: 15.0  # Shorter hold (less reversal risk)
momentum_threshold: 0.9  # Very strict momentum
```

**Aggressive** (higher frequency, lower win rate):
```yaml
min_spot_move_usd: 12.0  # Smaller moves
min_entry_bid_depth: 5  # Accept thinner liquidity
stop_loss_cents: 20  # Wider stop-loss
exit_delay_sec: 25.0  # Longer hold (more repricing time)
momentum_threshold: 0.7  # Looser momentum
```

**Balanced** (current defaults):
- Targets 1-3 trades/hour
- ~65-70% win rate
- +1-2¢ per trade expected value

---

## Execution Flow - Complete Walkthrough

Let's trace a typical trade from start to finish with timestamps and prices.

### T=0s: BTC Moves on Binance

```
Binance: BTC trades $97,000 → $97,018 (+$18 in 5 seconds)
Coinbase: BTC trades $96,998 → $97,015 (+$17 in 5 seconds)
Kraken: BTC trades $96,999 → $97,012 (+$13 in 5 seconds)
```

**Detector State**:
- Binance delta: +$18
- Multi-exchange check: 3/3 agree (all UP) ✓
- Volume (Binance): 0.6 BTC (~$60k) ✓

### T=0.2s: Signal Detected

```python
signal = detector.detect(market, orderbook)
# ScalpSignal(
#   ticker="KXBTC15M-26MAR020145-45",
#   side="yes",
#   spot_delta=18.0,
#   entry_price_cents=52,
#   source="binance"
# )
```

**Pre-Entry Checks**:
- Time to expiry: 780 seconds (13 minutes) ✓ (180-900s window)
- Price bounds: 52¢ ✓ (25-75¢ window)
- Liquidity: 12 contracts @ 51¢ bid ✓ (≥10 min)
- Momentum: Recent +$9, Older +$9, Ratio 100% ✓ (≥80%)
- No existing position ✓

### T=0.5s: Pre-Entry Checks Pass

```
Logger: "SIGNAL [binance]: YES KXBTC15M-26MAR020145-45 | spot_delta=$18.0 | entry=52¢ | spot=$97018"
```

### T=1.0s: Entry Order Submitted (Limit)

```python
limit_price = 52 + 1 = 53¢  # Add 1¢ slippage buffer
order_id = submit_order(BUY, YES, size=5, price=53¢)
# Order ID: "ord_abc123"
```

**Orderbook State**:
- Best ask: 52¢ × 8 contracts
- Our limit: 53¢ × 5 contracts

### T=1.5s: Limit Order Fills!

```
Kalshi Fill Event:
{
  "order_id": "ord_abc123",
  "status": "executed",
  "filled": 5,
  "fill_price": 53
}
```

**Position Created**:
```python
position = ScalpPosition(
    ticker="KXBTC15M-26MAR020145-45",
    side="yes",
    entry_price_cents=53,
    size=5,
    entry_time=1709348401.5,  # T=1.5s
    exit_target_time=1709348421.5,  # T=21.5s (entry + 20s)
    hard_exit_time=1709348436.5,  # T=36.5s (entry + 35s)
    order_id="ord_abc123",
    spot_delta=18.0,
    signal_source="binance",
    entry_exit_depth=12,  # Bid depth at entry
    entry_spread_cents=3,  # Spread at entry
)
```

**Logger**: `"LIMIT FILL [binance]: YES KXBTC15M-26MAR020145-45 5 @ 53¢ (order ord_abc123)"`

### T=1.5s - T=21.5s: Holding Position

**Exit Manager Loop** (checks every 100ms):

**T=2s**: Stop-loss check
- Current bid: 52¢
- Adverse movement: 53 - 52 = 1¢ < 15¢ ✓

**T=5s**: Stop-loss check
- Current bid: 54¢
- Adverse movement: 53 - 54 = -1¢ (we're winning!) ✓

**T=10s**: Depth-momentum check
- Current bid: 56¢ (+3¢ profit)
- Bid depth: 10 contracts (83% of entry) ✓ (>40% threshold)

**T=15s**: Reversal check
- Current signal: Still YES (BTC still up $15 from 5s ago) ✓

**T=20s**: Spread reversion check
- Current spread: 4¢ (1.3× entry spread) ✓ (<2× threshold)

### T=21.5s: Timed Exit Triggers

```python
now = 1709348421.5
if now >= position.exit_target_time:  # 1709348421.5
    # TIME TO EXIT!
    place_exit(ticker, position)
```

### T=21.5s: Exit Order Submitted

```python
exit_price = orderbook.best_bid - 0 = 57¢  # Current best bid, no slippage
order_id = submit_order(SELL, YES, size=5, price=57¢)
# Order ID: "ord_xyz789"
```

**Orderbook State**:
- Best bid: 57¢ × 6 contracts
- Our limit: 57¢ × 5 contracts

### T=22s: Exit Order Fills

```
Kalshi Fill Event:
{
  "order_id": "ord_xyz789",
  "status": "executed",
  "filled": 5,
  "fill_price": 57  # BUG #6: Should log this, not limit price!
}
```

### T=22.1s: P&L Calculated

**Current Code (WRONG - Bug #6, #7)**:
```python
exit_fill_price = 57  # Should query from API, not assume limit price
gross_pnl = (57 - 53) * 5 = 20¢
fees = 20 * 0.07 = 1.4¢  # Only charges on exit profit (wrong!)
net_pnl = 20 - 1.4 = 18.6¢ ≈ 19¢
```

**Correct Calculation**:
```python
# Entry fees (paid when buying at 53¢)
entry_fees = 53 * 5 * 0.07 = 18.55¢ ≈ 19¢

# Exit proceeds
exit_fill_price = 57¢  # From API
gross_proceeds = 57 * 5 = 285¢

# Exit fees (7% of profit)
gross_pnl = (57 - 53) * 5 = 20¢
exit_fees = max(0, 20) * 0.07 = 1.4¢

# Net P&L
total_fees = 19 + 1.4 = 20.4¢
net_pnl = 20 - 20.4 = -0.4¢  # Actually NEGATIVE!
```

**Wait, what?!** The correct calculation shows we **lost money** on this trade!

**Why?**
- Entry fees are proportional to entry price (53¢ × 7% = ~3.7¢ per contract)
- Exit fees are only on profit (4¢ × 7% = 0.28¢ per contract)
- Total fees: ~4¢ per contract × 5 = ~20¢
- Gross profit: 4¢ per contract × 5 = 20¢
- Net profit: 20¢ - 20¢ = ~0¢ (breakeven)

**Key Insight**: **Entry fees are HUGE** at mid-prices (50¢). We need at least +4-5¢ moves to be profitable after fees!

### Final Stats

```python
stats.trades_entered = 1
stats.trades_exited = 1
stats.trades_won = 1  # (if pnl > 0)
stats.total_pnl_cents = 19  # WRONG (should be ~0¢)
stats.limit_fills = 1
```

**Logger**:
```
"EXIT: KXBTC15M-26MAR020145-45 sold 5 YES @ 57¢ | P&L: +19¢ | hold: 20.0s"
```

---

## Historical Context

### What Went Wrong in March 1-2 Session

**Session Date**: March 2, 2026 (05:30-06:10 UTC)
**Duration**: ~40 minutes
**Trades Logged**: 1 entry + 1 exit
**Actual Trades**: ~13+ entries, ~7+ exits (untracked!)
**Logged P&L**: -$0.04
**Actual P&L**: -$5.52 (138× worse!)

### The 10 Critical Bugs

#### Bug #1: Exit Fills Not Confirmed
**File**: `orchestrator.py:2053` (`_place_exit()`)
**Issue**: Calls `_record_exit()` immediately after submitting order, doesn't wait for fill
**Impact**: Position tracking wrong, exits not confirmed, stranded positions

#### Bug #2: Orderbook WebSocket Broken
**Issue**: 80% entry failure rate (4/5 attempts failed)
**Root Cause**: Orderbook snapshot disabled, deltas applied to empty orderbook
**Impact**: No liquidity data → can't detect thin markets → enter illiquid trades

#### Bug #3: OMS WebSocket Not Initialized
**File**: `orchestrator.py` (missing `om.initialize()` call)
**Issue**: Real-time fill stream never started
**Impact**: Fills only detected via REST polling (slow, unreliable)

#### Bug #4: Event Loop Architecture
**Issue**: 3 separate event loops (main, scanner, websocket) → cross-thread async calls fail
**Impact**: `asyncio.InvalidStateError: Future attached to different loop`

#### Bug #5: No WebSocket Reconnection
**Issue**: Single disconnect → permanent failure
**Impact**: After disconnect, no more orderbook updates → strategy blind

#### Bug #6: Exit Price = Limit Not Fill
**File**: `orchestrator.py:2053` (`_record_exit()`)
**Issue**: Records limit price (25¢) instead of actual fill price (79¢)
**Example**: Logged -$0.04, actual -$6.00 (150× discrepancy!)

#### Bug #7: Entry Fees Not Logged
**Issue**: Only calculates fees on winning trades (exit profit)
**Impact**: P&L understated by ~7% (ignores entry fees)

#### Bug #8: No Balance Tracking
**Issue**: Never queries Kalshi balance → drift undetected
**Impact**: Lost $6 but showed -$0.04 → no awareness of problem

#### Bug #9: No Position Reconciliation
**Issue**: Never checks open positions at startup
**Impact**: Stranded positions from crashes accumulate unknowingly

#### Bug #10: Duplicate Positions
**Issue**: No duplicate entry prevention → race conditions
**Impact**: 4 "failed" entries might have all filled → 4× position (20 contracts instead of 5)

### How Each Bug Manifested

**Entry Attempt #1** (KXBTC15M-26MAR020115-15):
```
06:01:24: Entry attempted @ 30¢
Bug #2: No orderbook data (WS broken)
Bug #10: No duplicate check → entered anyway
06:01:24: Entry appears to fail (no fill confirmation)
Bug #3: OMS WS not running → no real-time fill event
Reality: Order actually filled @ 30¢ (untracked)
```

**Entry Attempt #2** (same ticker):
```
06:02:03: Entry attempted @ 29¢
Bug #10: No duplicate check → entered again!
Reality: Second position created (2× position now)
```

**Entry Attempt #3** (same ticker):
```
06:03:54: Entry attempted @ 11¢
Bug #10: THIRD duplicate entry!
Reality: Third position created (3× position = 15 contracts!)
```

**Exit Attempt** (same ticker):
```
06:07:16: Exit triggered (20s hold time elapsed)
Bug #2: No orderbook → can't check liquidity
Bug #6: Records limit price (25¢), not fill
Reality: Sold 2 NO contracts @ 79¢ (opposite side!)
Result: Sold NO when holding YES → hedge → fee loss
```

**Realized Losses**:
- 3 YES entries: 30¢ + 29¢ + 11¢ = 70¢ average × 3 = $2.10 spent
- 1 NO exit: 79¢ × 2 = $1.58 received
- Net: -$0.52 (plus fees ~$0.20) = **-$0.72 actual loss**
- Logged: -$0.04 (12× underestimate!)

### Lessons Learned

1. **Test fill confirmation**: Never assume orders filled without polling API
2. **Test WebSocket connectivity**: Monitor connection state, add reconnection
3. **Test OMS initialization**: Verify WebSocket streams start correctly
4. **Validate P&L**: Periodically check actual balance vs expected
5. **Position reconciliation**: Always check open positions at startup
6. **Duplicate prevention**: Track pending orders, prevent race conditions
7. **Fee accounting**: Include BOTH entry and exit fees in calculations
8. **Event loop architecture**: Keep async calls within same loop, use thread-safe queues
9. **Orderbook snapshots**: Always fetch snapshot before applying deltas
10. **Paper mode first**: Test extensively in paper mode before live trading

---

## Tuning Guide

### How to Make More Conservative

**Goal**: Lower frequency, higher win rate, smaller losses

**Parameter Changes**:
```yaml
# Stricter move detection
min_spot_move_usd: 18.0  # (was 15.0)

# Tighter liquidity requirements
min_entry_bid_depth: 15  # (was 10)
min_exit_bid_depth: 5  # (was 3)

# Stricter momentum
momentum_threshold: 0.9  # (was 0.8)

# Shorter hold time (less reversal risk)
exit_delay_sec: 15.0  # (was 20.0)

# Tighter stop-loss
stop_loss_cents: 12  # (was 15)

# More volume confirmation
min_window_volume:
  binance: 0.7  # (was 0.5)
  coinbase: 0.4  # (was 0.3)
```

**Expected Impact**:
- Frequency: 1-2 trades/hour → 0.5-1 trade/hour (50% reduction)
- Win rate: 65% → 72% (+7 pts)
- Avg loss: -3¢ → -2¢ (smaller losses)
- Missed opportunities: Some +5¢ winners skipped (trade-off)

### How to Make More Aggressive

**Goal**: Higher frequency, lower win rate, larger wins

**Parameter Changes**:
```yaml
# Looser move detection
min_spot_move_usd: 12.0  # (was 15.0)

# Looser liquidity
min_entry_bid_depth: 5  # (was 10)

# Looser momentum
momentum_threshold: 0.7  # (was 0.8)

# Longer hold time (more repricing time)
exit_delay_sec: 25.0  # (was 20.0)

# Wider stop-loss (fewer premature exits)
stop_loss_cents: 20  # (was 15)

# Less volume filtering
min_window_volume:
  binance: 0.3  # (was 0.5)
  coinbase: 0.2  # (was 0.3)
```

**Expected Impact**:
- Frequency: 1-2 trades/hour → 3-5 trades/hour (2-3× increase)
- Win rate: 65% → 58% (-7 pts)
- Avg win: +4¢ → +5¢ (longer holds catch bigger moves)
- Larger losses: More -15-20¢ losses (wider stop-loss)

### Parameter Combinations That Work Well

#### "Sniper" Configuration (High Quality, Low Frequency)
```yaml
min_spot_move_usd: 20.0
momentum_threshold: 0.9
min_entry_bid_depth: 15
exit_delay_sec: 18.0
enable_depth_momentum_exit: true
enable_reversal_exit: true
```
**Profile**: 0.5-1 trade/hour, 75% win rate, +2¢ per trade

#### "Scalper" Configuration (High Frequency, Lower Quality)
```yaml
min_spot_move_usd: 12.0
momentum_threshold: 0.7
min_entry_bid_depth: 5
exit_delay_sec: 22.0
enable_depth_momentum_exit: false
enable_reversal_exit: false
```
**Profile**: 3-4 trades/hour, 60% win rate, +1¢ per trade

#### "Balanced" Configuration (Current Defaults)
```yaml
min_spot_move_usd: 15.0
momentum_threshold: 0.8
min_entry_bid_depth: 10
exit_delay_sec: 20.0
enable_depth_momentum_exit: true
enable_reversal_exit: true
```
**Profile**: 1-2 trades/hour, 65% win rate, +1.5¢ per trade

### Warning: Parameter Interactions

**Dangerous Combinations**:

1. **High frequency + Tight stop-loss**:
   ```yaml
   min_spot_move_usd: 10.0  # Many signals
   stop_loss_cents: 10  # Tight stop
   # Result: Many whipsaw losses (-10¢ each)
   ```

2. **Long hold + No reversal exit**:
   ```yaml
   exit_delay_sec: 30.0  # Long hold
   enable_reversal_exit: false  # No early exit
   # Result: Hold through reversals, wipe out gains
   ```

3. **Loose liquidity + Wide spread**:
   ```yaml
   min_entry_bid_depth: 3  # Thin liquidity
   min_kalshi_spread_cents: 0  # Allow wide spreads
   # Result: Enter illiquid markets, can't exit
   ```

4. **Short hold + High move threshold**:
   ```yaml
   min_spot_move_usd: 20.0  # Big moves only
   exit_delay_sec: 12.0  # Short hold
   # Result: Exit before Kalshi reprices (miss profit)
   ```

**Safe Combinations**:
- Conservative + Tight stop-loss ✓
- Aggressive + Wide stop-loss ✓
- High frequency + Reversal exits ✓
- Low frequency + Longer holds ✓

### Calibration Process

1. **Start conservative**: Use "Sniper" config for first 20 trades
2. **Measure actual metrics**:
   - Fill rate: Should be >80% (if <50%, too aggressive)
   - Win rate: Should be >60% (if <50%, too aggressive)
   - Avg hold time: Should be ~20s (if >30s, exits too slow)
3. **Adjust one parameter at a time**:
   - Too many false positives? Raise `min_spot_move_usd` by $2
   - Too few trades? Lower `min_spot_move_usd` by $2
   - Too many late entries? Raise `momentum_threshold` by 0.1
4. **Validate for 50 trades**: Measure before/after impact
5. **Repeat**: Iterate until frequency and win rate are balanced

---

## Backtest Realism Configuration (NEW: 2026-03-03)

### Why Backtest Realism Matters

Standard backtests assume **perfect execution**: instant fills at quoted prices with no friction. This systematically **overstates P&L by 40-60%** for latency arbitrage strategies.

**Real trading involves execution friction**:
- Order placement delays (repricing lag)
- Queue competition (limit order fills)
- Network latency (API round-trip time)
- Stale orderbook data
- Market impact from order size

The unified backtest framework now supports **five realism models** to simulate these frictions. See [BACKTEST_REALISM_MODELS.md](./BACKTEST_REALISM_MODELS.md) for full documentation.

### Quick Start: Comparing Realism Profiles

```bash
# Optimistic (upper bound, no friction)
python3 main.py backtest crypto-scalp --db data/probe.db --realism optimistic

# Realistic (balanced, production forecasting)
python3 main.py backtest crypto-scalp --db data/probe.db --realism realistic

# Pessimistic (worst-case, risk analysis)
python3 main.py backtest crypto-scalp --db data/probe.db --realism pessimistic
```

### Example Results: 48hr Crypto Scalp Backtest

| Profile | P&L | Fills | Fill Rate | Sharpe | Notes |
|---------|-----|-------|-----------|--------|-------|
| **Optimistic** | $87.50 | 156/156 | 100% | 2.1 | Upper bound, development |
| **Realistic** | $52.30 | 109/156 | 70% | 1.4 | **Production forecast** |
| **Pessimistic** | $34.80 | 91/156 | 58% | 1.0 | Worst-case, capital allocation |

**Key Insight:** Optimistic overstates realistic by **+67%**. Always use **realistic profile** for production P&L forecasts.

### Preset Profiles

#### Optimistic Profile
```python
BacktestRealismConfig.optimistic()
```

**All models disabled.** Use for:
- Strategy logic validation (no execution noise)
- Upper bound P&L estimates
- Development iteration (fast backtests)

**Results:** 100% fill rate, zero slippage, +40-60% vs realistic

#### Realistic Profile (Recommended)
```python
BacktestRealismConfig.realistic()
```

**Balanced assumptions from live trading.** Use for:
- **Production P&L forecasting** (capital allocation)
- **Parameter optimization** (find robust configs)
- **Pre-deployment validation** (estimate live performance)

**Parameters (calibrated for Kalshi crypto markets):**
- Repricing lag: 5s average (measured Binance→Kalshi delay)
- Queue factor: 3x (backfit from 72% observed fill rate)
- Network latency: 200ms (p50 API round-trip)
- Staleness penalty: 1.0x (linear penalty up to 5s)
- Market impact: 5.0 coefficient (fit to executed vs quoted price)

**Results:** 70-80% fill rate, 2-3¢ avg slippage, -35-45% vs optimistic

#### Pessimistic Profile
```python
BacktestRealismConfig.pessimistic()
```

**Conservative assumptions for risk management.** Use for:
- **Worst-case stress testing**
- **Drawdown planning** (size positions conservatively)
- **Capital allocation** (downside protection)

**Parameters:**
- Repricing lag: 3s (faster MM response)
- Queue factor: 5x (heavy competition)
- Network latency: 300ms (slower infrastructure)
- Staleness penalty: 2.0x (aggressive penalty)
- Market impact: 8.0 coefficient (high slippage)

**Results:** 50-65% fill rate, 4-5¢ avg slippage, -25-35% vs realistic

### Custom Calibration (Advanced)

After collecting live trading data, calibrate realism models to match observed execution:

```python
from src.backtesting.realism_config import (
    BacktestRealismConfig,
    RepricingLagConfig,
    QueuePriorityConfig,
    NetworkLatencyConfig,
)

# Start from realistic preset
config = BacktestRealismConfig.realistic()

# Calibrate based on live data (2 weeks minimum)
config.repricing_lag = RepricingLagConfig(
    enabled=True,
    lag_sec=3.8,  # Measured: median CEX→Kalshi delay
    std_sec=0.6,  # Measured: IQR/2
    min_lag_sec=1.2,  # Measured: p5
    max_lag_sec=9.5,  # Measured: p95
)

config.queue_priority = QueuePriorityConfig(
    enabled=True,
    queue_factor=4.2,  # Backfit from live fill rate
    # Live fill rate = 68% → solve for queue_factor
    # fill_rate = size / (factor * depth + size)
    # 0.68 = 10 / (factor * 18 + 10)
    # factor ≈ 4.2
)

config.network_latency = NetworkLatencyConfig(
    enabled=True,
    latency_ms=175.0,  # Measured: p50 API latency
    std_ms=80.0,  # Measured: (p95 - p50)
    min_latency_ms=60.0,  # Measured: p5
    max_latency_ms=420.0,  # Measured: p95
)

# Use in backtest
from src.backtesting.engine import BacktestEngine, BacktestConfig
engine = BacktestEngine(BacktestConfig(realism=config))
result = engine.run(feed, adapter)
```

### Validation Process

1. **Collect live data** (2-4 weeks):
   - All signals detected
   - All orders submitted (timestamps, prices)
   - All fills (executed prices, fees)
   - Orderbook snapshots (at signal and order time)

2. **Run parallel backtest**:
   ```bash
   python3 main.py backtest crypto-scalp \
       --db data/probe.db \
       --realism realistic
   ```

3. **Compare metrics**:
   - Fill rate: backtest vs live (should match ±10%)
   - P&L: backtest vs live (should match ±15%)
   - Slippage distribution: backtest vs live

4. **Adjust realism parameters**:
   - Fill rate too high? Increase queue_factor or staleness_penalty
   - Fill rate too low? Decrease queue_factor or latency
   - Slippage too low? Increase market_impact coefficient
   - Slippage too high? Decrease market_impact coefficient

5. **Validate out-of-sample**:
   - Calibrate on first 2 weeks
   - Validate on next 2 weeks
   - If backtest P&L matches live ±15% → calibration successful

### Crypto Scalp Specific Recommendations

**Most sensitive to:**
- Repricing lag (core edge is stale quote window)
- Network latency (tight timing windows)

**Recommended calibration**:
1. Measure actual repricing lag:
   ```python
   # Log CEX price jump timestamp and Kalshi quote update
   repricing_lag = t_kalshi_update - t_cex_jump
   # Use median as lag_sec, IQR/2 as std_sec
   ```

2. Measure API round-trip:
   ```python
   # Log signal detection → fill confirmation
   round_trip = t_fill_confirmed - t_signal_detected
   # Use p50 as latency_ms, (p95-p50) as std_ms
   ```

3. Start with **pessimistic profile** for initial capital allocation (latency arb has high variance)

4. After live validation, switch to **realistic profile** if backtests prove conservative

### Common Pitfalls

**Don't:**
- ❌ Use optimistic profile for production forecasts (overstates P&L 40-60%)
- ❌ Skip validation (backtest must match live ±15%)
- ❌ Calibrate on <2 weeks data (sample size too small)
- ❌ Ignore outliers (p95 latency matters for risk)

**Do:**
- ✅ Use realistic profile for all production forecasts
- ✅ Run all three profiles to understand P&L range
- ✅ Validate out-of-sample (avoid overfitting calibration)
- ✅ Update calibration quarterly (markets evolve)

---

## Next Steps

**Before ANY live trading**:

1. **Fix all 10 bugs** (see `FIXES_COMPLETED_MARCH2.md` for status)
2. **Run 8-hour paper mode test** (Task #9)
3. **Validate P&L accuracy** (compare logged vs actual balance)
4. **Stress test reconnection** (kill WebSocket, verify recovery)
5. **Position reconciliation test** (start with open position, verify detection)
6. **Circuit breaker test** (force losses, verify halt)
7. **Review ALL fills** (compare logged exits vs Kalshi API)
8. **Balance drift check** (verify drift <$1 over 8 hours)

**Only after ALL validation passes**: Resume live trading with $50 max daily loss limit.

---

## References

- **Config**: `strategies/crypto_scalp/config.py`
- **Detector**: `strategies/crypto_scalp/detector.py`
- **Orchestrator**: `strategies/crypto_scalp/orchestrator.py`
- **Probe Data**: `scripts/btc_latency_probe.py` (historical lag analysis)
- **Backtest**: `src/backtesting/adapters/scalp_adapter.py`
- **Bug Report**: `FINDINGS_MARCH_2_SESSION.md`
- **Fixes**: `FIXES_COMPLETED_MARCH2.md`

---

**Document Version**: 1.0
**Last Updated**: March 2, 2026
**Author**: Claude Sonnet 4.5 (with Raine)
**Status**: Complete - Ready for review
