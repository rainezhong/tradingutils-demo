# Phase 1 Validation Checklist

**Purpose:** Verify P0 and P1 bug fixes are working correctly before proceeding to longer integration tests.

**Duration:** 2 hours

**Requirements:**
- Python 3.9+ environment
- Kalshi API credentials in `.env`
- No other instances of crypto-scalp running
- Clean working directory (no stale lock files)

---

## Quick Start

### Automated Testing (Recommended)

```bash
# Run full automated validation suite
./scripts/validate_phase1.sh

# Custom duration (e.g., 4 hours)
./scripts/validate_phase1.sh --duration-hours 4

# Skip process lock test (if already validated)
./scripts/validate_phase1.sh --skip-process-lock
```

The script will:
1. Run all three tests automatically
2. Generate logs in `logs/validation_YYYYMMDD_HHMMSS/`
3. Create a comprehensive report
4. Exit with pass/fail status

### Manual Testing

Follow the step-by-step instructions below for manual validation.

---

## Pre-Flight Checks

### ✓ Environment Setup

**Check Python version:**
```bash
python3 --version
# Expected: Python 3.9.x
```

**Check .env file exists:**
```bash
ls -la .env
# Should show .env file with Kalshi credentials
```

**Check for stale processes:**
```bash
ps aux | grep crypto-scalp
# Should return empty (no running instances)
```

**Check for stale lock files:**
```bash
ls -la /tmp/crypto_scalp.lock
# Should return "No such file or directory"
```

**Clean lock file if needed:**
```bash
rm /tmp/crypto_scalp.lock
```

---

## Test 1: Process Lock Protection (5 minutes)

**Purpose:** Verify that only one instance can run at a time.

### Steps

**1.1 Start first instance**

```bash
cd /Users/raine/tradingutils
python3 main.py run crypto-scalp --dry-run > logs/test1_instance1.log 2>&1 &
```

**Record the PID:**
```bash
echo $!
# Example output: 12345
```

**1.2 Wait for initialization (10 seconds)**

```bash
sleep 10
```

**1.3 Verify process is running**

```bash
ps -p <PID>
# Should show running process
```

**1.4 Verify lock file created**

```bash
cat /tmp/crypto_scalp.lock
# Should show PID matching your process
```

**1.5 Attempt to start second instance (should fail)**

```bash
python3 main.py run crypto-scalp --dry-run 2>&1 | tee logs/test1_instance2.log
```

**Expected output:**
```
RuntimeError: Crypto scalp strategy already running (PID: 12345)
```

**Or:**
```
Another instance is already running (PID: 12345)
```

**1.6 Verify second instance failed**

Check the exit code:
```bash
echo $?
# Should be non-zero (1 or 2)
```

**1.7 Stop first instance**

```bash
kill <PID>
```

**1.8 Verify lock file removed**

```bash
ls /tmp/crypto_scalp.lock
# Should return "No such file or directory"
```

### ✓ Success Criteria

- [x] First instance starts successfully
- [x] Lock file created with correct PID
- [x] Second instance fails with RuntimeError
- [x] Lock file removed on clean shutdown

### Common Issues

**Issue:** Lock file exists but no process running
```bash
# Solution: Remove stale lock
rm /tmp/crypto_scalp.lock
```

**Issue:** Second instance starts successfully
- **Root cause:** Lock mechanism not working
- **Action:** DO NOT PROCEED - fix lock implementation first

---

## Test 2: Initialization & Stability (30 minutes)

**Purpose:** Verify critical components initialize correctly and run stably.

### Steps

**2.1 Start strategy in dry-run mode**

```bash
python3 main.py run crypto-scalp --dry-run > logs/test2_init.log 2>&1 &
STRATEGY_PID=$!
echo "Strategy PID: $STRATEGY_PID"
```

**2.2 Monitor initialization logs (first 30 seconds)**

```bash
# In a separate terminal, tail the log
tail -f logs/test2_init.log
```

**Look for these key messages (in order):**

1. **Kalshi connection:**
   ```
   INFO Connected to Kalshi API
   INFO Initialized Crypto Scalp Strategy: feed=all, lookback=5.0s, min_move=$15.0 [DRY RUN]
   ```

2. **Event loop capture:**
   ```
   INFO Captured main event loop for cross-thread async calls
   ```

3. **OMS initialization:**
   ```
   INFO Initializing OMS (WebSocket fill stream)...
   INFO Initializing OMS...
   ```

4. **Position reconciliation:**
   ```
   INFO ✓ No open positions found - clean slate
   ```

   **OR (if positions from previous runs):**
   ```
   INFO Position updated: KXBTC15M-... yes 0 → 5 (delta=5)
   ```

5. **Orderbook processing:**
   ```
   INFO ✓ Cached orderbook for KXBTC15M-...: bid=25, ask=26
   ```

   **OR:**
   ```
   INFO Processing orderbook snapshot for KXBTC15M-...
   ```

**2.3 Check for forbidden patterns (should NOT appear)**

```bash
# These patterns indicate bugs - should NOT be present
grep -i "temporary event loop\|new_event_loop" logs/test2_init.log
# Expected: empty output
```

**2.4 Check for errors in first 5 minutes**

```bash
# Wait 5 minutes
sleep 300

# Check error count
grep "ERROR" logs/test2_init.log | wc -l
# Expected: 0

# If errors found, investigate
grep "ERROR" logs/test2_init.log
```

**2.5 Verify process still running**

```bash
ps -p $STRATEGY_PID
# Should show running process
```

**2.6 Wait 30 minutes for stability test**

```bash
# Set a timer for 30 minutes
sleep 1800

# Check if still running
ps -p $STRATEGY_PID
# Should still show running process
```

**2.7 Analyze logs after 30 minutes**

```bash
# Use automated analyzer
python3 scripts/analyze_validation_logs.py logs/test2_init.log
```

### ✓ Success Criteria

- [x] OMS initialized with real-time fills
- [x] Event loop captured (no "temporary" or "new" event loops)
- [x] Position reconciliation completed
- [x] Orderbook processing active
- [x] Zero errors in logs
- [x] Process runs stable for 30+ minutes

### Common Issues

**Issue:** "OMS initialized" but no "real-time fills" mention
- **Root cause:** WebSocket initialization failed
- **Check:** Look for WebSocket connection errors
- **Action:** Verify Kalshi API credentials and network

**Issue:** "Captured main event loop" not found
- **Root cause:** Event loop architecture issue (BUG #4)
- **Action:** DO NOT PROCEED - fix event loop issue first

**Issue:** Process dies within 30 minutes
- **Root cause:** Unhandled exception or crash
- **Check:** Last 50 lines of log for crash details
- **Action:** Fix crash before proceeding

### What to Look For

**Good patterns:**
```
✓ OMS initialized with real-time fills
✓ No open positions found - clean slate
✓ Cached orderbook for KXBTC15M-...
Captured main event loop
Position updated: KXBTC15M-... (from fill reconciliation)
```

**Bad patterns (red flags):**
```
ERROR: Main event loop not available
temporary event loop
new_event_loop()
WARNING: WebSocket connection failed
ERROR: Failed to initialize OMS
```

---

## Test 3: Balance Tracking & Reconciliation (1+ hours)

**Purpose:** Verify balance reconciliation runs periodically and detects drift correctly.

### Steps

**3.1 Continue from Test 2**

Leave the strategy running from Test 2. The process should still be running.

```bash
# Verify still running
ps -p $STRATEGY_PID
```

**3.2 Monitor for balance reconciliation (every 5 minutes)**

```bash
# In a separate terminal
tail -f logs/test2_init.log | grep "Balance reconciliation"
```

**Expected format (every 5 minutes):**
```
INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
```

**3.3 Track reconciliation events**

After 1 hour, you should see ~12 reconciliation events (every 5 minutes):

```bash
grep -c "Balance reconciliation" logs/test2_init.log
# Expected: 12 (for 1 hour) or 24 (for 2 hours)
```

**3.4 Check drift values**

In paper mode, drift should always be $0.00:

```bash
grep "Balance reconciliation" logs/test2_init.log | tail -10
# All should show: drift=$0.00
```

**If drift is non-zero:**
- This indicates a bug in position tracking or P&L calculation
- DO NOT PROCEED - investigate immediately

**3.5 Check for circuit breaker triggers**

Circuit breaker should NEVER trigger in paper mode:

```bash
grep -i "circuit breaker\|trading halted\|max_daily_loss" logs/test2_init.log
# Expected: empty output
```

**3.6 Let run for full test duration**

Continue monitoring for the full test period (1-2 hours minimum).

**3.7 Final analysis**

```bash
# Run automated analyzer
python3 scripts/analyze_validation_logs.py logs/test2_init.log --verbose

# Check final error count
grep "ERROR" logs/test2_init.log | wc -l
# Expected: 0
```

### ✓ Success Criteria

- [x] Reconciliation events every 5 minutes
- [x] All drift values = $0.00 (paper mode)
- [x] No circuit breaker triggers
- [x] No errors in logs
- [x] Process stable for entire duration

### Common Issues

**Issue:** No reconciliation events
- **Root cause:** Reconciliation thread not started or crashed
- **Check:** Look for threading errors
- **Action:** Fix reconciliation initialization

**Issue:** Non-zero drift in paper mode
- **Root cause:** Position tracking bug (BUG #9) or fee calculation error (BUG #7)
- **Action:** DO NOT PROCEED - this is a critical bug

**Issue:** Circuit breaker triggered
- **Root cause:** Phantom losses being detected (should not happen in paper mode)
- **Action:** Investigate P&L calculation bug

### What to Look For

**Good patterns:**
```
Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
```

**Bad patterns (red flags):**
```
Balance reconciliation: ... | drift=$6.32
Circuit breaker triggered: max_daily_loss exceeded
TRADING HALTED: Loss limit reached
```

---

## Final Analysis

### Run Automated Analyzer

```bash
python3 scripts/analyze_validation_logs.py logs/test2_init.log
```

### Manual Verification

**Count errors:**
```bash
grep "ERROR" logs/test2_init.log | wc -l
# Expected: 0
```

**Count warnings:**
```bash
grep "WARNING" logs/test2_init.log | wc -l
# Review any warnings - some may be acceptable
```

**Count reconciliations:**
```bash
grep -c "Balance reconciliation" logs/test2_init.log
# Expected: ~12 per hour
```

**Check runtime:**
```bash
head -1 logs/test2_init.log  # Start time
tail -1 logs/test2_init.log  # End time
```

### Generate Report

The automated script generates a full report:

```bash
cat logs/validation_*/VALIDATION_REPORT.md
```

---

## Pass/Fail Criteria

### PASS Criteria (all must be true)

- [x] Process lock prevents duplicate instances ✓
- [x] OMS initializes with WebSocket fills ✓
- [x] Event loop captured (no temporary loops) ✓
- [x] Position reconciliation runs ✓
- [x] Balance reconciliation every 5 minutes ✓
- [x] Zero drift in paper mode ✓
- [x] No circuit breaker triggers ✓
- [x] No errors in logs ✓
- [x] Stable for entire test duration ✓

### FAIL Criteria (any of these)

- [ ] Second instance starts successfully (lock broken)
- [ ] OMS initialization fails
- [ ] Event loop architecture issues (temporary/new loops)
- [ ] No balance reconciliation events
- [ ] Non-zero drift in paper mode
- [ ] Circuit breaker triggers
- [ ] Any ERROR-level log entries
- [ ] Process crashes during test

---

## Troubleshooting Guide

### Scenario 1: Lock File Issues

**Symptom:** Lock file exists but no process running

**Diagnosis:**
```bash
cat /tmp/crypto_scalp.lock
ps -p <PID>
```

**Solution:**
```bash
rm /tmp/crypto_scalp.lock
```

### Scenario 2: OMS Initialization Fails

**Symptom:** "Failed to initialize OMS" or no OMS logs

**Diagnosis:**
```bash
grep "OMS\|WebSocket" logs/test2_init.log | head -20
```

**Common causes:**
- Kalshi API credentials invalid/expired
- Network connectivity issues
- Event loop not available (BUG #4)

**Solution:**
- Verify `.env` file has correct credentials
- Test network connection to Kalshi API
- Fix event loop architecture if needed

### Scenario 3: Process Crashes

**Symptom:** Strategy exits unexpectedly during test

**Diagnosis:**
```bash
tail -50 logs/test2_init.log
# Look for stack trace or exception
```

**Common causes:**
- Unhandled exception in WebSocket handler
- Event loop thread crash
- Network timeout not handled

**Solution:**
- Add try/except around crash point
- Fix root cause before proceeding

### Scenario 4: No Reconciliation Events

**Symptom:** No "Balance reconciliation" logs after 10+ minutes

**Diagnosis:**
```bash
grep -i "reconcil" logs/test2_init.log
# Check if thread started
```

**Common causes:**
- Reconciliation thread not started
- Thread crashed silently
- Timer interval configuration issue

**Solution:**
- Check reconciliation thread initialization
- Verify timer/scheduler working

### Scenario 5: Non-Zero Drift

**Symptom:** drift != $0.00 in paper mode

**Diagnosis:**
```bash
grep "Balance reconciliation" logs/test2_init.log | grep -v "drift=\$0.00"
```

**Common causes:**
- Position tracking bug (BUG #9)
- Fee calculation error (BUG #7)
- Exit price recording wrong (BUG #6)

**Solution:**
- This is a CRITICAL bug - do not proceed
- Investigate position tracking logic
- Verify P&L calculation matches Kalshi API

---

## Next Steps

### If All Tests Pass ✓

1. Review this checklist - all items marked ✓
2. Review validation report for any warnings
3. **Proceed to Phase 2:** 8-hour integration test
   ```bash
   # Start Phase 2
   python3 main.py run crypto-scalp --dry-run > logs/phase2_integration.log 2>&1 &

   # Monitor with analyzer
   watch -n 300 python3 scripts/analyze_validation_logs.py logs/phase2_integration.log
   ```
4. After Phase 2 success, proceed to Phase 3 stress tests

### If Any Test Fails ✗

1. **DO NOT PROCEED** to Phase 2
2. Review error logs for root cause
3. Fix identified bugs
4. Re-run Phase 1 from the beginning
5. Only proceed when all tests pass

---

## Expected Log Patterns Reference

### Successful Initialization

```
2026-03-02 19:31:04,203 INFO Connected to Kalshi API
2026-03-02 19:31:04,203 INFO Initialized Crypto Scalp Strategy: feed=all, lookback=5.0s, min_move=$15.0 [DRY RUN]
2026-03-02 19:31:04,220 INFO Captured main event loop for cross-thread async calls
2026-03-02 19:31:04,220 INFO Initializing OMS (WebSocket fill stream)...
2026-03-02 19:31:04,220 INFO Initializing OMS...
2026-03-02 19:31:04,307 INFO ✓ Canceled 0 stale order(s)
2026-03-02 19:31:04,307 INFO Recovering positions from recent fills...
2026-03-02 19:31:04,838 INFO ✓ No open positions found - clean slate
```

### Position Reconciliation (if positions exist)

```
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022230-30 yes 0 → 1 (delta=1)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022230-30 yes 1 → 2 (delta=1)
2026-03-02 19:31:04,838 INFO Position updated: KXBTC15M-26MAR022145-45 yes 0 → 1 (delta=1)
```

### Orderbook Processing

```
2026-03-02 19:31:15,123 INFO ✓ Cached orderbook for KXBTC15M-26MAR022230-30: bid=25, ask=26
2026-03-02 19:31:15,234 INFO ✓ Cached orderbook for KXBTC15M-26MAR022245-45: bid=48, ask=50
```

### Balance Reconciliation

```
2026-03-02 19:36:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
2026-03-02 19:41:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
2026-03-02 19:46:04,500 INFO Balance reconciliation: initial=$100.00 + pnl=$0.00 = expected=$100.00 | actual=$100.00 | drift=$0.00
```

### Process Lock Failure (expected when testing)

```
RuntimeError: Crypto scalp strategy already running (PID: 12345)
```

OR

```
ERROR Another instance is already running (PID: 12345)
```

---

## Files Generated

After validation, you should have:

```
logs/validation_YYYYMMDD_HHMMSS/
├── phase1_validation_TIMESTAMP.log      # Main validation script log
├── test1_process_lock.log               # First instance log
├── test1_second_instance.log            # Second instance failure log
├── test2_initialization.log             # Full strategy log
└── VALIDATION_REPORT.md                 # Generated report
```

---

**Last Updated:** 2026-03-02
**Version:** 1.0
**Related:** `scripts/validate_phase1.sh`, `scripts/analyze_validation_logs.py`
