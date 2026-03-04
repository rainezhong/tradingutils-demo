# Live Trading Session Analysis - March 1, 2026
## All Trades: Signal-Based vs Stale Orders + Exit Analysis

**Session:** 21:33 - 22:11 (38 minutes)
**Strategy:** crypto-scalp (LIVE mode, paper_mode: false)
**Total order attempts:** 5
**Actual fills:** 1
**Completed trades:** 1 (loss of 4¢)

---

## Summary

**ALL ENTRIES WERE SIGNAL-BASED** ✅ - No stale order issues detected
**EXIT PROBLEMS:** Yes ❌ - Only 1 exit executed (loss), 4 entries failed to fill

---

## Trade-by-Trade Analysis

### Trade #1: FAILED TO FILL ❌
**Time:** 21:34:16
**Signal:** `SIGNAL [coinbase]: YES KXBTC15M-26MAR020045-45`
**Entry condition:**
- **Signal-based:** ✅ YES (fresh coinbase signal)
- Spot delta: $28.0 (well above $10 threshold)
- Entry price: 65¢
- Spot price: $66,760

**Order execution:**
- Order submitted: `0fa227e9...` buy 1x @ 65¢
- Polled 6 times over 1.5s (limit timeout)
- **Order canceled** after 1.5s
- Market order fallback attempted
- **Market order skipped:** No orderbook data

**Result:** NO FILL (limit timeout + orderbook unavailable)

**Root cause:**
- **NOT a stale order** - Fresh signal detected
- Orderbook WebSocket not connected for this market
- No fallback possible without orderbook

---

### Trade #2: FAILED TO FILL ❌
**Time:** 21:50:01
**Signal:** `SIGNAL [binance]: YES KXBTC15M-26MAR020100-00`
**Entry condition:**
- **Signal-based:** ✅ YES (fresh binance signal)
- Spot delta: $23.0
- Entry price: 28¢
- Spot price: $66,916

**Order execution:**
- Order submitted: `560d6241...` buy 1x @ 28¢
- Polled 4 times over 1.5s
- **Order canceled** after 1.5s
- Market order fallback skipped (no orderbook)

**Result:** NO FILL

**Root cause:** Same as Trade #1 - orderbook unavailable

---

### Trade #3: FAILED TO FILL ❌
**Time:** 21:50:04 (3 seconds after Trade #2)
**Signal:** `SIGNAL [binance]: YES KXBTC15M-26MAR020100-00`
**Entry condition:**
- **Signal-based:** ✅ YES (fresh binance signal, same market)
- Spot delta: $23.4
- Entry price: 28¢
- Spot price: $66,916

**Order execution:**
- Order submitted: `942c5741...` buy 1x @ 28¢
- Polled 5 times over 1.5s
- **Order canceled** after 1.5s
- Market order fallback skipped (no orderbook)

**Result:** NO FILL

**Root cause:** Same market, same issue - orderbook unavailable

---

### Trade #4: FILLED + EXITED (LOSS) ✅❌
**Time:** 21:52:00
**Signal:** `SIGNAL [binance]: YES KXBTC15M-26MAR020100-00`
**Entry condition:**
- **Signal-based:** ✅ YES (fresh binance signal)
- Spot delta: $10.6 (at threshold)
- Entry price: 28¢
- Spot price: $66,848

**Order execution:**
- Order submitted: `7869e72b...` buy 1x @ 28¢
- Polled 1 time (0.1s)
- **FILLED** @ 29¢ (1¢ worse than signal)
- Position opened: YES @ 29¢

**Exit execution:**
- Time: 21:52:20 (20 seconds after entry)
- Exit price: 25¢
- Exit reason: Normal exit delay (20s configured)
- **Order submitted:** `9dee4d2a...` sell 1x @ 25¢
- **Exit status:** Order submitted but no fill confirmation in logs

**Result:**
- Entry: 29¢
- Exit: 25¢
- **Loss: -4¢** ($-0.04)

**Analysis:**
- **Signal quality:** Marginal ($10.6 move, right at $10 threshold)
- **Entry price:** Worse than signal (28¢ → 29¢ fill)
- **Exit:** Normal timing (20s delay) but bad price (25¢ = -4¢ loss)
- **Market moved against us** immediately after entry

**Exit problem:**
- No fill confirmation logged for exit order
- Position shows as exited in dashboard (line 149)
- Likely filled at 25¢ but no explicit fill log

---

### Trade #5: FAILED TO FILL ❌
**Time:** 22:01:59
**Signal:** `SIGNAL [coinbase]: NO KXBTC15M-26MAR020115-15`
**Entry condition:**
- **Signal-based:** ✅ YES (fresh coinbase signal)
- Spot delta: $-15.8 (negative, NO side)
- Entry price: 66¢
- Spot price: $66,632

**Order execution:**
- Order submitted: `d86fb8b9...` buy 1x @ 66¢
- Polled 5 times over 1.5s
- **Order canceled** after 1.5s
- Market order fallback skipped (no orderbook)

**Result:** NO FILL

**Root cause:** Same as others - orderbook unavailable

---

## Overall Statistics

**Entry Quality:**
- All 5 orders: **Signal-based** ✅
- Zero stale orders ✅
- Signal strength: 3 strong ($23-28), 1 marginal ($10.6), 1 medium ($15.8)

**Execution Quality:**
- Fill rate: **20%** (1/5) ❌
- Failed fills: 4 (all due to orderbook unavailable)
- Successful fills: 1 (but at worse price)

**Exit Quality:**
- Exits attempted: 1
- Exits completed: 1 (likely filled, no confirmation log)
- Exit timing: Normal (20s delay)
- Exit price: Poor (4¢ loss)

---

## Root Causes Identified

### 1. Orderbook WebSocket Not Connected ❌ CRITICAL
**Impact:** 4/5 trades failed to fill

**Evidence:**
```
WARNING Market order skip: No orderbook data for KXBTC15M-26MAR020045-45
WARNING Market order skip: No orderbook data for KXBTC15M-26MAR020100-00
WARNING Market order skip: No orderbook data for KXBTC15M-26MAR020115-15
```

**Problem:**
- Strategy relies on 2-stage fill: limit order (1.5s) → market order fallback
- Market order fallback needs orderbook data to determine current ask price
- Orderbook WebSocket was not connected for these markets
- Without orderbook, no fallback possible → order canceled

**Fix needed:**
- Ensure orderbook WebSocket connects BEFORE allowing trades
- OR: Allow market orders without orderbook (use last known price)
- OR: Extend limit order timeout from 1.5s to 3-5s

### 2. Marginal Signal Quality ⚠️
**Impact:** Trade #4 loss

**Evidence:**
- Trade #4 entered on $10.6 spot delta (exactly at $10 threshold)
- Market immediately reversed
- Lost 4¢ in 20 seconds

**Problem:**
- Min move threshold ($10) is too low
- Entering on marginal moves ($10-15) leads to whipsaw

**Fix needed:**
- Increase min_spot_move_usd from $10 to $15 or $20
- Add momentum filter (move must be accelerating, not decelerating)

### 3. No Exit Fill Confirmation ⚠️
**Impact:** Unclear exit status

**Evidence:**
- Exit order submitted at 21:52:20
- No "LIMIT FILL" or "EXIT FILLED" log message
- Dashboard shows position closed (line 149)

**Problem:**
- Exit might have filled but no confirmation logged
- OR: Exit is still resting on exchange (stranded position risk!)

**Fix needed:**
- Add explicit exit fill logging
- Poll for exit fill confirmation
- Use new OMS WebSocket fill detection

---

## Stale Order Assessment

### Was there a stale order issue? **NO** ✅

**Evidence:**
1. All 5 orders had explicit SIGNAL log lines immediately before submission
2. Time delta between signal and order: <1 second
3. Signal details logged (source, delta, entry price, spot price)
4. No orders submitted without a preceding signal
5. All orders canceled after 1.5s (limit timeout) - no stale orders left on exchange

**Conclusion:**
- **Zero stale orders detected** ✅
- All entries were fresh signal-based entries
- The stale order problem was NOT present in this session
- **The new OMS TTL feature would have prevented stale orders anyway** (30s auto-cancel)

---

## Exit Behavior Assessment

### Did exits work correctly? **PARTIALLY** ⚠️

**Working:**
- Exit timing: 20s delay as configured ✅
- Exit order submitted ✅
- Position closed (per dashboard) ✅

**Broken:**
- No explicit fill confirmation logged ❌
- Can't verify exit price was actually 25¢ ❌
- Can't verify exit filled vs. still resting ❌

**Critical question:**
- Is the exit order still resting on Kalshi?
- Check Kalshi account for open orders on KXBTC15M-26MAR020100-00

---

## Recommendations

### Immediate (Before Next Session)

1. **Fix orderbook WebSocket** ⚠️ CRITICAL
   - Subscribe to orderbook before allowing trades
   - Add connection check: `if not orderbook_connected: skip_trade()`
   - Log orderbook connection status in dashboard

2. **Increase signal threshold**
   - Change `min_spot_move_usd: 10.0` → `15.0`
   - Add momentum filter (require acceleration)

3. **Add exit fill confirmation**
   - Log "EXIT FILLED" when fill detected
   - Use new OMS WebSocket fill detection
   - Poll for exit confirmation like we do for entry

4. **Check for stranded exit order**
   - Verify KXBTC15M-26MAR020100-00 exit filled
   - If still resting, manually cancel

### Medium-Term (This Week)

5. **Extend limit order timeout**
   - Try 3-5s instead of 1.5s
   - May improve fill rate without orderbook fallback

6. **Add pre-flight orderbook check**
   - Before signal → order, verify orderbook connected
   - Skip trades on markets without orderbook

### Validated by New OMS

7. **Stale order protection** ✅ WORKING
   - No stale orders observed (but new 30s TTL adds safety)
   - All orders were signal-based

8. **Position recovery** ✅ NEEDED
   - Restart would have lost position tracking
   - New `initialize()` would recover from fills

---

## Session Summary

**Positives:**
- ✅ All entries were signal-based (not stale)
- ✅ Strategy detected 5 valid signals in 38 minutes
- ✅ Order submission working
- ✅ Exit timing working (20s delay)

**Negatives:**
- ❌ 80% failed fills (4/5) due to orderbook unavailable
- ❌ Only completed trade was a loss (-4¢)
- ❌ Marginal signal quality ($10.6 threshold too low)
- ❌ No exit fill confirmation logging

**Critical insight:**
The **stale order problem was NOT present** - all failures were due to **orderbook WebSocket not connecting**, which caused market order fallback to fail.

**Next session priorities:**
1. Fix orderbook WebSocket connection
2. Increase signal threshold to $15
3. Add exit fill logging
4. Verify no stranded orders on exchange
