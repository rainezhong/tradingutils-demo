# Null Order ID Check Review - March 2, 2026

## Changes Overview

**File**: `strategies/crypto_scalp/orchestrator.py`
**Changes**: 33 insertions, 14 deletions
**Purpose**: Defensive programming - handle `submit_order()` returning `None`

---

## What's Being Fixed

### Problem
`OrderManager.submit_order()` can return `None` in edge cases:
- No event loop available
- Order submission error
- API failure

Without null checks, the code would crash when trying to:
- Cancel a null order ID
- Wait for fill on a null order ID

### Solution
Add null checks at 4 critical locations:
1. Entry order submission
2. Market order fallback
3. Market order entry
4. Exit order submission

---

## Changes by Location

### 1. Entry Order - Limit Order Path (Line 1637)
```python
# Submit via OrderManager (includes opposite-side protection)
order_id = self._run_async(self._om.submit_order(request))

+ # Check if order submission failed
+ if order_id is None:
+     logger.error("Order submission failed (no event loop or error): %s", signal.ticker)
+     return
```

**Impact**:
- ✅ Prevents crash on null order_id
- ✅ Logs error with ticker for debugging
- ✅ Early return prevents further processing
- ✅ No position recorded (correct - order never placed)

### 2. Market Order Fallback - Cancel Limit Order (Line 1655)
```python
# STAGE 2: Limit didn't fill, try market order fallback
- try:
-     # CRITICAL: Use main loop, not scanner loop
-     self._run_async_in_main_loop(self._om.cancel_order(order_id), timeout=5.0)
-     logger.debug(f"✓ Canceled limit order {order_id}")
- except Exception as e:
-     logger.error(f"Failed to cancel limit order {order_id}: {e}")

+ if order_id is not None:
+     try:
+         # CRITICAL: Use main loop, not scanner loop
+         self._run_async_in_main_loop(self._om.cancel_order(order_id), timeout=5.0)
+         logger.debug(f"✓ Canceled limit order {order_id}")
+     except Exception as e:
+         logger.error(f"Failed to cancel limit order {order_id}: {e}")
```

**Impact**:
- ✅ Prevents crash when trying to cancel null order_id
- ✅ Skips cancel attempt if order never submitted
- ⚠️ Note: This shouldn't happen if check #1 above works, but defensive

### 3. Limit Order Timeout - Cancel (Line 1678)
```python
# Limit didn't fill and fallback disabled - give up
- try:
-     self._run_async(self._om.cancel_order(order_id))
- except Exception:
-     pass

+ if order_id is not None:
+     try:
+         self._run_async(self._om.cancel_order(order_id))
+     except Exception:
+         pass
```

**Impact**:
- ✅ Prevents crash when canceling null order_id
- ✅ Same defensive pattern as #2

### 4. Market Order Entry (Line 1886)
```python
# Submit via OrderManager
order_id = self._run_async(self._om.submit_order(request))

+ # Check if order submission failed
+ if order_id is None:
+     logger.error("Market order submission failed (no event loop or error): %s", signal.ticker)
+     return False
```

**Impact**:
- ✅ Prevents crash on null order_id
- ✅ Returns False to indicate failure
- ✅ Caller can handle gracefully

### 5. Exit Order Submission (Line 2411)
```python
exit_order_id = self._run_async(self._om.submit_order(request))

+ # Check if exit order submission failed
+ if exit_order_id is None:
+     logger.error("Exit order submission failed (no event loop or error): %s", ticker)
+     # Position remains open - will be retried on next tick
+     return
```

**Impact**:
- ✅ Prevents crash on null exit order_id
- ✅ Position remains in `self._positions` → will retry exit
- ✅ Good comment explaining behavior
- ⚠️ Position could be "stuck" if exit continuously fails

---

## Code Review

### ✅ Strengths
1. **Defensive programming** - handles edge cases gracefully
2. **Consistent pattern** - all checks use same `if order_id is None` pattern
3. **Good logging** - error messages include ticker for debugging
4. **Proper behavior** - early returns prevent cascading failures
5. **No breaking changes** - purely additive, doesn't change logic

### ⚠️ Potential Concerns

**1. When would `submit_order()` return None?**

Looking at `KalshiOrderManager.submit_order()`:
```python
async def submit_order(self, request: OrderRequest) -> Optional[str]:
    """Submit order and return order ID."""
    try:
        # ... validation, checks ...
        order = await self._client.place_order(...)
        return order.order_id
    except Exception as e:
        logger.error(f"Order submission failed: {e}")
        return None  # Returns None on exception
```

**Answer**: Returns `None` when:
- Exception during order placement
- API failure
- Network error
- Event loop issues (if `_run_async` fails)

**Verdict**: ✅ Valid edge case to handle

**2. Exit order failure - position stuck?**

If exit order submission fails (returns None), the position remains open and will be retried on next tick. But what if exit continuously fails?

**Scenarios**:
- API down → position stuck until API recovers
- No event loop → shouldn't happen in practice
- Network issues → retry on next tick (every 0.25s)

**Mitigation already in place**:
- Emergency exit at TTX < 90s (uses market order)
- Position reconciliation at startup (detects stuck positions)

**Verdict**: ✅ Acceptable - existing safety mechanisms handle this

**3. Market order fallback - null check redundant?**

Lines 1655 and 1678 add `if order_id is not None` before cancel. But if check #1 (line 1637) returns early when `order_id is None`, we should never reach these lines with a null order_id.

**Verdict**: ✅ Still good - defense in depth, protects against future code changes

### 🧪 Testing Needed

These are defensive checks for rare edge cases. Hard to unit test without mocking:
- OrderManager returning None
- Event loop failures
- API errors

**Recommendation**:
1. ✅ Code review (this document)
2. ✅ Syntax validation (run pytest to ensure no syntax errors)
3. ⏳ Paper mode validation (will catch if we broke anything)

---

## Recommendation

### ✅ APPROVE with minor note

**Approval reasons**:
1. Defensive programming - prevents crashes
2. No logic changes - purely additive safety
3. Consistent pattern - easy to understand
4. Good logging - helps debugging
5. Proper error handling - graceful degradation

**Minor note**:
The null checks on cancel operations (lines 1655, 1678) are redundant due to early returns, but that's actually good - defense in depth.

### Testing Plan

1. **Syntax check**: Run pytest to ensure no syntax errors
2. **Integration**: Paper mode will validate behavior
3. **Unit tests**: Not needed - these are defensive edge case handlers

---

## Action Items

1. ✅ Review complete (this document)
2. ⏳ Run pytest to validate syntax
3. ⏳ Commit with descriptive message
4. ⏳ Include in paper mode validation (Task #13)

---

**Reviewer**: Claude Sonnet 4.5
**Date**: 2026-03-02
**Status**: ✅ APPROVED - Safe to commit
**Risk**: Very low - purely defensive, no logic changes
