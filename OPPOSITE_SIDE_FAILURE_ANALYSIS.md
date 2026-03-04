# Opposite-Side Protection Failure Analysis - March 2, 2026

## Task #15: Investigation Complete

---

## Summary

**Finding**: Opposite-side protection in `KalshiOrderManager` SHOULD have worked, but the crypto scalp strategy has **two independent position tracking systems** that were not synchronized.

**Root Cause**: Architectural issue - orchestrator and OrderManager track positions separately.

---

## Evidence from March 2 Session

From `MARCH2_PNL_ANALYSIS.md`, ALL 13 markets showed opposite-side trading:

**Example: KXBTC15M-26MAR020100-00**
- 3× BUY YES (11¢, 29¢, 30¢)
- 1× SELL NO (2x @ 79¢)

**Problem**: You can't SELL NO contracts unless you first BUY NO contracts. These fills indicate:
1. Strategy bought YES contracts (opened YES position)
2. Strategy bought NO contracts (opened NO position on same ticker!)
3. Strategy sold NO contracts (closed NO position)

---

## How Opposite-Side Protection Works

`KalshiOrderManager` has opposite-side protection (see `docs/OPPOSITE_SIDE_PROTECTION.md`):

```python
# In submit_order():
if request.action == Action.BUY:
    if self.has_opposite_position(request.ticker, request.side):
        raise ValueError(
            f"Cannot BUY {request.side} on {request.ticker}: "
            f"already have position on opposite side"
        )
```

**Key Points**:
- Tracks positions by `(ticker, side)` tuple
- Only blocks **BUY** orders on opposite side
- Allows **SELL** orders (closing positions)
- Position tracking updated via `update_position_from_fill()`

---

## Why Protection Failed

### Issue 1: Two Position Tracking Systems

**Orchestrator** (`strategies/crypto_scalp/orchestrator.py`):
```python
self._positions: Dict[str, ScalpPosition] = {}  # Keyed by ticker only!
```

**OrderManager** (`core/order_manager/kalshi_order_manager.py`):
```python
self._positions: Dict[Tuple[str, Side], int] = {}  # Keyed by (ticker, side)
```

### Issue 2: Duplicate Position Check (Fix #10)

Line 1238 in orchestrator.py (added in Fix #2):
```python
# DUPLICATE POSITION CHECK
with self._lock:
    if signal.ticker in self._positions:
        logger.warning("Already have position on %s, skipping duplicate entry", signal.ticker)
        return
```

**This check only prevents same-ticker entries at orchestrator level**, but doesn't prevent opposite-side entries because it checks by ticker alone, not `(ticker, side)`.

### Issue 3: Position Sync May Be Broken

The OrderManager's position tracking is updated when:
1. Order fills (via `get_fills()` which calls `update_position_from_fill()`)
2. WebSocket fill stream (via `initialize()`)

**BUT**: The orchestrator's `self._positions` is only updated when:
1. Entry order fills → `_record_entry()` creates position
2. Exit order fills → `_record_exit()` removes position

**Problem**: If fills aren't properly synced to OrderManager (e.g., WebSocket not initialized, or fills missed), the OrderManager's position tracking could be stale/wrong, allowing opposite-side orders through.

---

## Likely Scenario (March 2 Session)

Given that OMS WebSocket wasn't initialized (Issue #3, now fixed), here's what probably happened:

1. **T=0**: Strategy buys YES @ 30¢
   - Orchestrator: creates `positions["TICKER"] = ScalpPosition(side="yes", ...)`
   - OrderManager: WebSocket NOT initialized, misses fill → position tracking empty!

2. **T=20**: Strategy detects reversal, tries to buy NO
   - Orchestrator duplicate check: `"TICKER" in positions` → TRUE, blocked
   - BUT if signal strength was high, might have force-exited and re-entered

3. **OR**: Multiple entry attempts
   - First BUY YES fills → creates position
   - Second BUY YES submitted before first fill confirmed → slips through duplicate check
   - Then tries to exit via SELL → but somehow SELL NO instead of SELL YES

---

## Configuration Check

Checked `crypto_scalp_live.yaml`:
```yaml
enable_position_flip: false  # DISABLED
```

**Position flip feature was DISABLED** during March 2 session, so that's NOT the cause.

---

## Conclusions

### Primary Issue (Architectural)

**Two position tracking systems that don't sync**:
- Orchestrator tracks by `ticker` only
- OrderManager tracks by `(ticker, side)`
- No automatic synchronization between them

### Secondary Issues (Now Fixed)

1. ✅ **Issue #3 (Fixed)**: OMS WebSocket not initialized → OrderManager position tracking empty
2. ✅ **Issue #10 (Fixed)**: Duplicate entry prevention at orchestrator level
3. ⏳ **Issue #16 (Pending)**: Need BOTH checks - ticker-level AND (ticker, side)-level

### Why Opposite-Side Trading Happened

**Most likely**:
1. OMS WebSocket wasn't initialized → OrderManager had no position tracking
2. All BUY orders went through because OrderManager thought positions were empty
3. Duplicate entry check at orchestrator level checked by ticker, not (ticker, side)
4. Multiple signals for same ticker with different sides all passed through

---

## Recommended Fixes

### Fix #16: Enhanced Position Tracking (CRITICAL)

**Option A**: Make orchestrator position tracking match OrderManager format

```python
# Change from:
self._positions: Dict[str, ScalpPosition] = {}

# To:
self._positions: Dict[Tuple[str, str], ScalpPosition] = {}  # (ticker, side)
```

**Option B**: Add opposite-side check at orchestrator level

```python
# In _place_entry(), after duplicate check:
with self._lock:
    # Check if we have position on SAME ticker
    if signal.ticker in self._positions:
        existing = self._positions[signal.ticker]
        if existing.side == signal.side:
            logger.warning("Already have same-side position on %s", signal.ticker)
            return
        else:
            logger.error("🚨 OPPOSITE SIDE DETECTED: Have %s position, trying to enter %s",
                         existing.side, signal.side)
            return
```

**Option C** (BEST): Keep both systems in sync

```python
# After every fill, sync OrderManager position to orchestrator:
def _sync_position_tracking(self, ticker: str, side: str) -> None:
    """Ensure orchestrator and OrderManager have same position view."""
    side_enum = Side.YES if side == "yes" else Side.NO
    om_position = self._om.get_position(ticker, side_enum)

    with self._lock:
        if om_position > 0:
            # OrderManager says we have position - ensure orchestrator has it
            if ticker not in self._positions:
                logger.warning("Position sync: Adding %s to orchestrator tracking", ticker)
                # Create placeholder...
        else:
            # OrderManager says no position - remove from orchestrator
            if ticker in self._positions:
                logger.warning("Position sync: Removing %s from orchestrator tracking", ticker)
                self._positions.pop(ticker)
```

---

## Status

**Task #15**: ✅ INVESTIGATION COMPLETE

**Root cause identified**: Two independent position tracking systems + OMS WebSocket not initialized

**Required fixes**:
- ✅ Issue #3: OMS WebSocket initialization (DONE)
- ✅ Issue #10: Duplicate entry prevention (DONE)
- ⏳ Issue #16: Enhanced position tracking with opposite-side check (PENDING)

---

## Next Steps

1. Implement **Fix #16** (Option B - simplest, most defensive)
2. Add position sync verification to dashboard
3. Paper mode test for 8 hours
4. Verify NO opposite-side trading occurs

---

**Date**: 2026-03-02
**Investigator**: Claude Sonnet 4.5
**Status**: Analysis complete, fix recommended
