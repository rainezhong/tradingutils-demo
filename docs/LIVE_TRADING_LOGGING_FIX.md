# Live Trading Logging Fix (2026-02-28)

## Problem

Live trading sessions were executing real orders on Kalshi but **not saving logs to files**, making it impossible to:
- Audit trades after the fact
- Debug issues
- Track P&L accurately
- Verify strategy behavior

### What Was Happening

1. **Paper mode** → Logs saved to `logs/paper_scalp_YYYYMMDD.log` ✅
2. **Live mode** → Logs only to stdout/stderr (terminal) ❌
3. Result: Live trades executed but **no audit trail** left on disk

### Root Cause

The `cmd_run()` function in `main.py` only configured console logging via `logging.basicConfig()`, which writes to stdout. When running in live mode, output was lost unless the user manually redirected it.

## Solution

Modified `main.py` to automatically create timestamped log files for all live trading sessions.

### Changes Made

**File:** `main.py` (lines ~370-380)

**Added:**
1. **Automatic file logging** when `--live` flag is used (i.e., `args.dry_run == False`)
2. **Timestamped log files** with format: `logs/{strategy}_live_{YYYYMMDD_HHMMSS}.log`
3. **Dual logging**: Both console (stdout) AND file simultaneously
4. **Log file notification**: Prints log file path on startup
5. **Graceful cleanup**: Flushes and closes file handlers on exit

### Log File Format

**Filename:** `logs/crypto-scalp_live_20260228_230145.log`

**Format:**
```
2026-02-28 23:01:45 | INFO     | strategies.crypto_scalp.orchestrator | ENTRY [binance]: NO KXBTC15M-26FEB280200-00 5 @ 48c (order abc123)
2026-02-28 23:02:10 | INFO     | strategies.crypto_scalp.orchestrator | EXIT: NO KXBTC15M-26FEB280200-00 5 @ 52c (was 48c) | P&L=+20c
```

### Usage

**Before (logs lost):**
```bash
python main.py run crypto-scalp --live --config strategies/configs/crypto_scalp_live.yaml
# Output only to terminal, lost when closed
```

**After (logs saved automatically):**
```bash
python main.py run crypto-scalp --live --config strategies/configs/crypto_scalp_live.yaml
# Starting strategy: crypto-scalp
# Mode: LIVE
# Log file: logs/crypto-scalp_live_20260228_230145.log
# Monitor logs: tail -f logs/crypto-scalp_live_20260228_230145.log
```

**Monitor in real-time:**
```bash
# In another terminal
tail -f logs/crypto-scalp_live_20260228_230145.log
```

## Benefits

✅ **Full audit trail** for all live trades
✅ **Easy debugging** with complete context
✅ **P&L verification** against Kalshi order history
✅ **No manual log redirection** required
✅ **Automatic timestamping** prevents overwrites
✅ **Strategy-specific logs** for multi-strategy sessions

## Migration Notes

### For Existing Live Sessions

If you have a live process running (like PID 74076):

1. **Stop the process** to prevent more unlogged trades:
   ```bash
   kill 74076  # Or Ctrl+C in the terminal
   ```

2. **Restart with new logging**:
   ```bash
   python main.py run crypto-scalp --live --config strategies/configs/crypto_scalp_live.yaml
   ```

3. **Check log file created**:
   ```bash
   ls -lth logs/*_live_*.log | head -1
   ```

### For Paper Trading

Paper trading is **unchanged** - continue using your existing workflow. The crypto-scalp orchestrator still creates paper mode logs internally.

## Testing

```bash
# Test live logging (with dry_run override for safety)
python main.py run crypto-scalp --config strategies/configs/crypto_scalp_live.yaml

# Verify log file created
ls logs/crypto-scalp_live_*.log

# Check log contents
tail -20 logs/crypto-scalp_live_*.log
```

## Related Issues

- **Issue:** Live trades logged as "[PAPER]" - **Status:** False alarm, different process
- **Issue:** Missing trade history - **Status:** Fixed by this change
- **Issue:** Can't audit Kalshi fills - **Status:** Fixed by this change

## Implementation Details

### Code Location
- **File:** `main.py`
- **Function:** `cmd_run()`
- **Lines:** ~370-430

### Handler Configuration
```python
file_handler = logging.FileHandler(log_file_path)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)
root_logger.addHandler(file_handler)
```

### Cleanup on Exit
```python
finally:
    if log_file_path:
        for handler in logging.getLogger().handlers[:]:
            if isinstance(handler, logging.FileHandler):
                handler.flush()
                handler.close()
```

## Future Improvements

- [ ] Add log rotation for long-running sessions (> 24h)
- [ ] Compress old log files automatically
- [ ] Add structured logging (JSON) for easier parsing
- [ ] Integrate with portfolio tracker to cross-reference fills
- [ ] Add Kalshi order ID to every log line for reconciliation

---

**Date:** 2026-02-28
**Author:** Claude + raine
**Status:** ✅ Deployed
**Testing:** Verified with crypto-scalp live session
