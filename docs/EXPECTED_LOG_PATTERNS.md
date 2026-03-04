# Expected Log Patterns for Crypto Scalp Strategy

This document shows what successful initialization and operation looks like in the logs.
Use this as a reference when analyzing validation test results.

---

## Table of Contents

1. [Successful Initialization](#successful-initialization)
2. [Position Reconciliation](#position-reconciliation)
3. [Balance Reconciliation](#balance-reconciliation)
4. [Orderbook Processing](#orderbook-processing)
5. [Process Lock Protection](#process-lock-protection)
6. [Error Patterns to Watch For](#error-patterns-to-watch-for)
7. [Trading Activity (Paper Mode)](#trading-activity-paper-mode)

---

## Successful Initialization

### Complete Initialization Sequence

This is what you should see in the first 30 seconds of startup:

```
2026-03-02 19:31:04,202 INFO HTTP Request: GET https://api.elections.kalshi.com/trade-api/v2/exchange/status "HTTP/2 200 OK"
2026-03-02 19:31:04,203 INFO Connected to Kalshi API
2026-03-02 19:31:04,203 INFO Initialized Crypto Scalp Strategy: feed=all, lookback=5.0s, min_move=$15.0 [DRY RUN]

# Event loop architecture (CRITICAL - must appear)
2026-03-02 19:31:04,220 INFO Captured main event loop for cross-thread async calls

# OMS initialization
2026-03-02 19:31:04,220 INFO Initializing OMS (WebSocket fill stream)...
2026-03-02 19:31:04,220 INFO Initializing OMS...

# Cleanup stale orders
2026-03-02 19:31:04,220 INFO Canceling all resting orders from previous runs...
2026-03-02 19:31:04,307 INFO ✓ Canceled 0 stale order(s)

# Position reconciliation
2026-03-02 19:31:04,307 INFO Recovering positions from recent fills...
# ... (fill pagination logs) ...
2026-03-02 19:31:04,838 INFO ✓ No open positions found - clean slate

# CEX feed connections
2026-03-02 19:31:05,123 INFO Starting Binance trade feed for BTCUSDT
2026-03-02 19:31:05,234 INFO Starting Coinbase trade feed for BTC-USD
2026-03-02 19:31:05,345 INFO Starting Kraken trade feed for XBT/USD

# Kalshi WebSocket
2026-03-02 19:31:05,456 INFO Starting Kalshi orderbook WebSocket...
2026-03-02 19:31:05,567 INFO Kalshi WebSocket connected

# Market scanning
2026-03-02 19:31:05,678 INFO Scanning for active BTC markets...
2026-03-02 19:31:05,789 INFO Found 8 active BTC markets

# Ready to trade
2026-03-02 19:31:05,890 INFO ✓ All feeds initialized - ready to trade
```

### Key Indicators of Success

**1. Event Loop Capture (REQUIRED)**
```
INFO Captured main event loop for cross-thread async calls
```

**What it means:** The event loop architecture is working correctly. All async operations will run on the main thread's event loop.

**If missing:** BUG #4 (event loop architecture) is not fixed. Do not proceed.

---

**2. OMS Initialization (REQUIRED)**
```
INFO Initializing OMS (WebSocket fill stream)...
INFO Initializing OMS...
```

**What it means:** OMS is initializing with WebSocket support for real-time fills.

**If missing:** OMS not initialized. Check for connection errors.

---

**3. Position Reconciliation (REQUIRED)**
```
INFO ✓ No open positions found - clean slate
```

**OR (if positions from previous runs):**
```
INFO Position updated: KXBTC15M-26MAR022230-30 yes 0 → 5 (delta=5)
```

**What it means:** Position reconciliation is working. Strategy knows its current positions.

**If missing:** BUG #9 (position reconciliation) is not fixed.

---

**4. Stale Order Cleanup (REQUIRED)**
```
INFO ✓ Canceled 0 stale order(s)
```

**OR:**
```
INFO ✓ Canceled 3 stale order(s)
```

**What it means:** Strategy cleaned up any resting orders from previous crashes/runs.

**If missing:** Orders from previous runs might execute unexpectedly.

---

## Position Reconciliation

### Clean Slate (No Positions)

```
2026-03-02 19:31:04,307 INFO Recovering positions from recent fills...
2026-03-02 19:31:04,403 INFO HTTP Request: GET https://api.elections.kalshi.com/trade-api/v2/portfolio/fills?limit=100 "HTTP/2 200 OK"
2026-03-02 19:31:04,838 INFO Paginated fills: fetched 100 total
2026-03-02 19:31:04,838 INFO ✓ No open positions found - clean slate
```

**What it means:** No positions from previous runs. Starting fresh.

**Expected in:** First run after clearing all positions, or after all positions settled.

---

### Existing Positions Recovered

```
2026-03-02 19:31:04,307 INFO Recovering positions from recent fills...
2026-03-02 19:31:04,403 INFO HTTP Request: GET https://api.elections.kalshi.com/trade-api/v2/portfolio/fills?limit=100 "HTTP/2 200 OK"
2026-03-02 19:31:04,518 INFO HTTP Request: GET https://api.elections.kalshi.com/trade-api/v2/portfolio/fills?limit=100 "HTTP/2 200 OK"
2026-03-02 19:31:04,635 INFO HTTP Request: GET https://api.elections.kalshi.com/trade-api/v2/portfolio/fills?limit=100 "HTTP/2 200 OK"
2026-03-02 19:31:04,838 INFO Paginated fills: fetched 500 total (>100 limit)

# Position updates (one per fill)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022230-30 yes 0 → 1 (delta=1)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022230-30 yes 1 → 2 (delta=1)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022230-30 yes 2 → 3 (delta=1)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022230-30 yes 3 → 4 (delta=1)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022230-30 yes 4 → 5 (delta=5)

# Sells (negative deltas)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR020115-15 no 0 → -2 (delta=-2)
2026-03-02 19:31:04,839 INFO Position updated: KXBTC15M-26MAR020045-45 yes 0 → -1 (delta=-1)

# Final summary
2026-03-02 19:31:04,840 INFO Recovered 12 open positions across 8 markets
```

**What it means:** Strategy recovered positions from previous run. This prevents duplicate entries and "stranded position" bugs.

**Expected in:** Runs after previous crashes or unclean shutdowns.

---

### Pagination (Many Fills)

```
2026-03-02 19:31:04,838 WARNING Fill pagination stopped at 500 fills (safety limit)
2026-03-02 19:31:04,838 INFO Paginated fills: fetched 500 total (>100 limit)
```

**What it means:** Strategy fetched 500 fills (5 pages × 100). Safety limit prevents infinite loops.

**Normal in:** Accounts with extensive trading history.

**Warning sign:** If you see this on first run, it means the account has many historical fills. Position recovery may be incomplete if relevant fills are beyond the 500-fill window.

---

## Balance Reconciliation

### Paper Mode (Zero Drift Expected)

```
2026-03-02 19:36:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
2026-03-02 19:41:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
2026-03-02 19:46:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
2026-03-02 19:51:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
```

**Frequency:** Every 5 minutes (configurable)

**What it means:**
- `initial`: Starting balance at strategy start
- `pnl`: Cumulative profit/loss from tracked positions
- `expected`: initial + pnl (what balance should be)
- `actual`: Current balance from Kalshi API
- `drift`: expected - actual (discrepancy)

**In paper mode:** All drift should be $0.00 because no real trades execute.

**If drift != $0.00 in paper mode:** CRITICAL BUG - position tracking or P&L calculation is wrong.

---

### Live Mode (Small Drift Acceptable)

```
2026-03-02 19:36:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$2.50 = expected=$102.50 | actual=$102.50 | drift=$0.00
2026-03-02 19:41:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$3.75 = expected=$103.75 | actual=$103.75 | drift=$0.00
2026-03-02 19:46:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$5.00 = expected=$105.00 | actual=$104.93 | drift=$0.07
```

**In live mode:**
- PnL should reflect actual trading activity
- Small drift (<$0.50) acceptable due to rounding or timing
- Large drift (>$1.00) indicates missing fills or position tracking bug

---

### Circuit Breaker Trigger (Should NOT happen in paper mode)

```
2026-03-02 20:15:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=-$52.00 = expected=$48.00 | actual=$48.00 | drift=$0.00
2026-03-02 20:15:04,501 ERROR Circuit breaker triggered: daily loss exceeded $50.00 (current: -$52.00)
2026-03-02 20:15:04,501 ERROR TRADING HALTED - manual intervention required
```

**What it means:** Daily loss limit exceeded. Trading stops automatically.

**In paper mode:** Should NEVER happen. If it does, phantom losses are being tracked.

**In live mode:** Expected behavior when losses exceed configured limit (`max_daily_loss_usd`).

---

## Orderbook Processing

### WebSocket Snapshots

```
2026-03-02 19:31:15,123 INFO Processing orderbook snapshot for KXBTC15M-26MAR022230-30
2026-03-02 19:31:15,124 INFO ✓ Cached orderbook for KXBTC15M-26MAR022230-30: bid=25, ask=26
```

**What it means:** Initial orderbook snapshot received via WebSocket.

**Frequency:** Once per market when WebSocket connects, or after reconnection.

---

### WebSocket Deltas

```
2026-03-02 19:31:16,234 DEBUG Orderbook delta: KXBTC15M-26MAR022230-30 yes price=26 delta=+5
2026-03-02 19:31:16,235 INFO ✓ Cached orderbook for KXBTC15M-26MAR022230-30: bid=25, ask=26
```

**What it means:** Orderbook update received via WebSocket (real-time).

**Frequency:** Varies based on market activity. Active markets may have 10+ updates/second.

---

### REST API Fallback (WebSocket Failure)

```
2026-03-02 19:35:15,123 WARNING Kalshi WebSocket disconnected
2026-03-02 19:35:15,124 INFO Falling back to REST API polling for orderbook
2026-03-02 19:35:16,125 INFO HTTP Request: GET https://api.elections.kalshi.com/trade-api/v2/markets/KXBTC15M-26MAR022230-30/orderbook?depth=10 "HTTP/2 200 OK"
2026-03-02 19:35:16,126 INFO ✓ Cached orderbook for KXBTC15M-26MAR022230-30: bid=25, ask=26 (from REST)
```

**What it means:** WebSocket failed, falling back to REST API polling.

**Frequency:** 1-second polling interval (configurable: `orderbook_rest_poll_interval_sec`).

**Performance impact:** Higher latency than WebSocket, but prevents total failure.

**Expected in:** Network issues, WebSocket connection drops.

---

### Orderbook Queue Processing

```
2026-03-02 19:31:05,500 INFO Starting orderbook queue processor...
2026-03-02 19:31:05,501 INFO Orderbook queue processor started
2026-03-02 19:31:15,678 DEBUG Orderbook queue: 3 items pending
2026-03-02 19:31:15,679 DEBUG Processing orderbook queue item 1/3
```

**What it means:** Background thread processing orderbook updates from WebSocket.

**Normal behavior:** Queue usually empty or <10 items. High queue depth (>50) indicates processing backlog.

---

## Process Lock Protection

### First Instance (Success)

```
2026-03-02 19:31:04,100 INFO Checking for existing instances...
2026-03-02 19:31:04,101 INFO No lock file found - proceeding
2026-03-02 19:31:04,102 INFO Created process lock: /tmp/crypto_scalp.lock (PID: 12345)
```

**What it means:** First instance acquired the lock successfully.

---

### Second Instance (Blocked)

**Terminal output:**
```
RuntimeError: Crypto scalp strategy already running (PID: 12345)
```

**OR:**

```
ERROR Another instance is already running (PID: 12345)
Detected lock file: /tmp/crypto_scalp.lock
Lock PID: 12345 (running)
Aborting to prevent duplicate instances
```

**What it means:** Second instance detected lock file and aborted. This is CORRECT behavior.

**Expected when:** Testing process lock protection (Test 1).

**NOT expected when:** Trying to start first instance. If this happens, stale lock file exists - remove it.

---

### Lock Cleanup (Shutdown)

```
2026-03-02 21:45:12,345 INFO Shutting down gracefully...
2026-03-02 21:45:12,456 INFO Stopping all feeds...
2026-03-02 21:45:12,567 INFO Removing process lock: /tmp/crypto_scalp.lock
2026-03-02 21:45:12,568 INFO ✓ Clean shutdown complete
```

**What it means:** Process exited cleanly and removed lock file.

**If lock not removed:** Unclean shutdown (crash, kill -9). Next start will need manual lock removal.

---

## Error Patterns to Watch For

### ❌ Event Loop Architecture Issues (BUG #4)

**Bad patterns - should NOT appear:**

```
ERROR Main event loop not available - did strategy.run() get called?
```

```
WARNING Creating temporary event loop for async operation
```

```
DEBUG new_event_loop() called from <location>
```

**What they mean:** Event loop architecture broken. Cross-thread async calls will fail.

**Action:** DO NOT PROCEED. Fix BUG #4 first.

---

### ❌ OMS WebSocket Not Initialized (BUG #3)

**Bad patterns:**

```
ERROR Failed to initialize OMS WebSocket
```

```
WARNING OMS initialized without WebSocket support - fills will be polled
```

```
ERROR om.initialize() not called - WebSocket fills unavailable
```

**What they mean:** OMS WebSocket initialization failed. Fill detection will be delayed (polling) or broken.

**Action:** DO NOT PROCEED. Fix BUG #3 first.

---

### ❌ Exit Fill Not Confirmed (BUG #1)

**Bad patterns:**

```
INFO Exit order placed - assuming filled  # WRONG - should wait for fill
INFO Position closed (estimated)           # WRONG - should confirm
```

**Good patterns:**

```
INFO Exit order placed - waiting for fill confirmation...
INFO Exit fill confirmed: KXBTC15M-... @ 26¢
INFO Position closed: KXBTC15M-... | profit=$0.25
```

**What they mean:** Strategy should ALWAYS wait for fill confirmation before updating position state.

**Action:** If you see "assuming filled" or "estimated", BUG #1 is NOT fixed.

---

### ❌ Wrong Exit Price Logged (BUG #6)

**Bad pattern:**

```
INFO Exit order placed @ 25¢ (limit price)
INFO Position closed | exit_price=25¢ | profit=$0.05
# But actual fill was @ 24¢!
```

**Good pattern:**

```
INFO Exit order placed @ 25¢ (limit price)
INFO Exit fill confirmed @ 24¢ (actual fill price)
INFO Position closed | entry=50¢ exit=24¢ | profit=-$1.30
```

**What they mean:** Exit price should be ACTUAL FILL PRICE, not limit order price.

**Action:** Compare logged exit price with actual fills from Kalshi API. If mismatch, BUG #6 not fixed.

---

### ❌ Entry Fees Not Logged (BUG #7)

**Bad pattern:**

```
INFO Position entered: KXBTC15M-... @ 50¢ | quantity=5
# No fee calculation on entry
```

**Good pattern:**

```
INFO Position entered: KXBTC15M-... @ 50¢ | quantity=5 | fee=$0.18 (7%)
```

**What they mean:** Entry fees should be calculated and logged, not just exit fees.

**Action:** If no entry fee logs, BUG #7 not fixed. P&L will be overstated by ~7%.

---

### ❌ No Balance Tracking (BUG #8)

**Bad pattern:**

```
# No balance reconciliation logs after 30+ minutes
```

**Good pattern:**

```
# Every 5 minutes:
INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
```

**What they mean:** Balance tracking thread should run every 5 minutes.

**Action:** If no reconciliation logs, BUG #8 not fixed. Balance drift will go undetected.

---

## Trading Activity (Paper Mode)

### Entry Signal Detection

```
2026-03-02 19:45:23,123 INFO [SIGNAL] Spot move detected: $16.23 in 5.0s (binance) | direction=UP
2026-03-02 19:45:23,234 INFO [ENTRY] Checking entry conditions for KXBTC15M-26MAR022230-30
2026-03-02 19:45:23,345 INFO [ENTRY] ✓ All filters passed | edge=8¢ | ttx=547s
```

**What it means:** Signal detected and entry conditions satisfied.

**In paper mode:** Signal detection should work, but order won't execute.

---

### Entry Blocked (Paper Mode)

```
2026-03-02 19:45:23,456 INFO [ENTRY] Would place order (paper mode) | ticker=KXBTC15M-26MAR022230-30 | side=yes | price=51¢ | qty=5
2026-03-02 19:45:23,457 INFO [ENTRY] Skipping order placement - paper mode enabled
```

**What it means:** Entry would have happened in live mode, but blocked because `paper_mode=true`.

**Expected in:** Paper mode testing. You should see signal detection but no actual orders.

---

### Live Trading (When paper_mode=false)

```
2026-03-02 19:45:23,456 INFO [ENTRY] Placing limit order | ticker=KXBTC15M-26MAR022230-30 | side=yes | price=51¢ | qty=5
2026-03-02 19:45:23,567 INFO HTTP Request: POST https://api.elections.kalshi.com/trade-api/v2/portfolio/orders
2026-03-02 19:45:23,678 INFO [ENTRY] Order submitted | order_id=abc-123-def
2026-03-02 19:45:24,123 INFO [ENTRY] Waiting for fill confirmation...
2026-03-02 19:45:24,456 INFO [ENTRY] Fill confirmed via WebSocket | fill_price=51¢ | qty=5
2026-03-02 19:45:24,457 INFO Position entered: KXBTC15M-26MAR022230-30 yes | entry=51¢ | qty=5 | fee=$0.18
```

**What it means:** Live trading flow from signal → order → fill → position tracking.

**Only expected when:** `paper_mode=false` and live trading enabled.

---

## Summary: What Success Looks Like

### First 30 Seconds (Initialization)

✓ "Connected to Kalshi API"
✓ "Captured main event loop"
✓ "Initializing OMS (WebSocket fill stream)"
✓ "✓ Canceled X stale order(s)"
✓ "✓ No open positions found - clean slate" (or position updates)
✓ "Starting Binance/Coinbase/Kraken trade feeds"
✓ "Kalshi WebSocket connected"
✓ "Found X active BTC markets"

### Every 5 Minutes (Ongoing Operation)

✓ "Balance reconciliation: ... drift=$0.00"
✓ "✓ Cached orderbook for ..." (orderbook updates)

### Never Appear

✗ "ERROR Main event loop not available"
✗ "temporary event loop" or "new_event_loop"
✗ "Failed to initialize OMS"
✗ "Circuit breaker triggered" (in paper mode)
✗ Any ERROR-level logs

---

**Last Updated:** 2026-03-02
**Version:** 1.0
**Related:** `docs/PHASE1_VALIDATION_CHECKLIST.md`, `scripts/analyze_validation_logs.py`
