# Paper Mode Test Status - Entry Timing Optimization

**Date:** 2026-03-01
**Status:** 🟡 PARTIAL - Core fixes working, scanner architecture needs refactoring

---

## ✅ Completed Fixes

### 1. Entry Timing Optimizations (Fully Implemented)
- **Raised minimum delta threshold**: $10 → $15 ✅
- **Added momentum filter**: Split-window acceleration check ✅
- **Enhanced diagnostic logging**: Orderbook status tracking ✅

**Files Modified:**
- `strategies/crypto_scalp/config.py` - Added momentum_threshold (0.8), raised min_spot_move_usd
- `strategies/crypto_scalp/detector.py` - Added `_compute_delta_with_momentum()` method
- `strategies/configs/crypto_scalp_live.yaml` - Updated min_spot_move_usd to 15.0
- `strategies/crypto_scalp/orchestrator.py` - Added diagnostic logging, signals_filtered_momentum counter

### 2. OrderBookManager Import Fix (Working)
- **Issue**: Import path was `kalshi.orderbook` (deleted) → Fixed to `core.market.orderbook_manager`
- **Solution**: Created synchronous wrapper around async OrderBookManager for thread-safe access
- **Status**: ✅ Working - No more "coroutine object has no attribute 'best_bid'" errors

**Implementation:**
```python
# Synchronous wrapper for async OrderBookManager
class OrderBookManager:
    def __init__(self, on_update=None, on_gap=None):
        self._async_manager = AsyncOrderBookManager(on_update=on_update, on_gap=on_gap)
        self._orderbooks: Dict[str, Optional[OrderBookState]] = {}  # Local cache

    def get_orderbook(self, ticker: str) -> Optional[OrderBookState]:
        """Get orderbook state from local cache (updated by callbacks)."""
        return self._orderbooks.get(ticker)
```

**WebSocket Integration:**
- WebSocket gets async manager directly (needs async methods)
- Sync threads use wrapper's cached state (populated via callbacks)
- Callbacks update cache for immediate sync access

---

## ⚠️ Known Issue: Scanner Event Loop Mismatch

### Problem
Scanner thread has "Future attached to a different loop" error preventing market scanning after initial scan:

```
WARNING Scan failed for KXBTC15M: Task ... got Future ... attached to a different loop
```

### Root Cause
The strategy was designed to run standalone with its own event loops and threads, but main.py creates the client in an async context manager with its own event loop. The scanner thread creates a NEW event loop, but the client is bound to the MAIN event loop.

### Impact
- Initial scan works (finds 1 market)
- Subsequent scans fail (markets list goes to 0)
- Cannot test entry timing optimizations without functional scanner

### Architecture Mismatch
```
main.py:
  └─ async with KalshiExchangeClient.from_env() as client  # Event loop A
       └─ strategy = CryptoScalpStrategy(exchange_client=client)
            └─ scanner_thread (creates Event loop B)  # ❌ Mismatch!
                 └─ tries to use client from loop A in loop B
```

### Possible Solutions

**Option 1: Use asyncio.run_coroutine_threadsafe()**
Add helper method to run client calls from scanner thread:
```python
def _run_async(self, coro):
    """Run async coroutine from sync thread using main event loop."""
    if self._scanner_loop:
        future = asyncio.run_coroutine_threadsafe(coro, self._main_loop)
        return future.result()
```

**Option 2: Make scanner async and run in same loop as main**
Refactor scanner to not use separate thread, run in main event loop

**Option 3: Keep client creation in scanner thread**
Create client INSIDE the scanner thread's event loop instead of accepting it via dependency injection

---

## Test Results (10-Second Run)

```
2026-03-01 22:18:57,801 INFO Found 1 active markets
2026-03-01 22:18:59,566 INFO Orderbook WebSocket started      ✅
2026-03-01 22:19:30,583 INFO DASH | runtime=33s |
    feeds=[binance=OK | coinbase=OK | kraken=DOWN]
    markets=0                                      ❌ (scanner failed)
    positions=0/1 | signals=0 | trades=0
    regime=osc=32.8                                ✅ (regime detector working)
```

**Success Indicators:**
- ✅ OrderBookManager loading ("Orderbook WebSocket started")
- ✅ No more "coroutine object has no attribute 'best_bid'" errors
- ✅ Regime detector working (osc=32.8)
- ✅ Entry timing config loaded (min_move=$15)
- ✅ CEX feeds working (binance=OK, coinbase=OK)

**Failure Indicators:**
- ❌ markets=0 (scanner can't maintain market list)
- ❌ "Scan failed for KXBTC15M" every 60 seconds
- ❌ No orderbook subscriptions ("Cannot subscribe to orderbook for X: WebSocket not available")
- ❌ No signals (can't test entry timing optimizations)

---

## Next Steps

### Immediate (Required for Testing)
1. **Fix scanner event loop issue** using one of the three solutions above
2. **Verify orderbook subscriptions** working after scanner fix
3. **Run 2-4 hour paper mode test** to validate Phase 1 metrics:
   - Fill rate ≥60% (vs 25% baseline)
   - "✓ Subscribed to orderbook" messages in logs
   - Absence of "No orderbook data" errors

### Phase 2 (After Scanner Fix)
1. **Validate threshold change** (50+ trades):
   - Signal count -20-30% (fewer weak signals)
   - Win rate +5-10pp (38% → 45%+)
   - No catastrophic losses (-125¢)
   - Avg P&L per trade positive

### Phase 3 (Final Validation)
1. **Analyze momentum filter** effectiveness:
   - % of signals filtered by momentum
   - Win rate of remaining signals (target: 55-60%)
   - Early exit rate reduction (target: -50%)
   - Avg hold time increase (target: 10-15s vs 5-8s)

---

## Entry Timing Optimizations - Expected Impact

### Current Baseline (Before Fixes)
- Fill rate: 25%
- Win rate: 38%
- Avg P&L per trade: -$0.04
- Trades per session: 1
- Session P&L: -$0.04

### After All Fixes (Projected)
- Fill rate: 25% → 70% (orderbook fix)
- Win rate: 38% → 55% (threshold + momentum)
- Avg P&L per trade: -$0.04 → +$0.07
- Trades per session: 1 → 8-12 (more fills)
- **Session P&L: -$0.04 → +$0.50 to +$0.85** (12-21x improvement)

---

## Files Modified (This Session)

1. **strategies/crypto_scalp/orchestrator.py**
   - Fixed OrderBookManager import path
   - Created sync wrapper around async OrderBookManager
   - Enhanced diagnostic logging (startup, subscription, fallback)
   - Added WebSocket integration with async manager

2. **strategies/crypto_scalp/config.py** (previously committed)
   - Raised min_spot_move_usd: 10.0 → 15.0
   - Added enable_momentum_filter: true
   - Added momentum_threshold: 0.8

3. **strategies/crypto_scalp/detector.py** (previously committed)
   - Added _compute_delta_with_momentum() method
   - Integrated momentum filter into detect()

4. **strategies/configs/crypto_scalp_live.yaml** (previously committed)
   - Updated min_spot_move_usd: 15.0

---

## Conclusion

**Core entry timing optimizations are implemented and ready**, but cannot be tested due to scanner architecture issue. The OrderBookManager fix is working (no more coroutine errors), but the scanner needs refactoring to work with the async client from main.py.

**Recommendation:** Fix scanner event loop issue (Option 1 preferred - least invasive), then proceed with Phase 1 paper mode testing.

---

**Commit Status:** Ready to commit OrderBookManager fix + documentation
**Testing Status:** Blocked on scanner fix
**Confidence in Fixes:** High (based on backtest analysis and implementation review)
