# Integration Test Scripts

This directory contains the **Phase 2 Integration Test Framework** for validating crypto scalp strategy bug fixes.

## Quick Start

```bash
# Start 8-hour integration test
./run_integration_test.sh

# Monitor in real-time (separate terminal)
./monitor_live.sh

# After completion, generate report
python3 generate_integration_report.py logs/integration_test_*.log

# Open report
open logs/integration_test_*_report.html
```

## Scripts

| Script | Purpose | Usage |
|--------|---------|-------|
| `run_integration_test.sh` | Main test runner | `./run_integration_test.sh [--duration-hours N]` |
| `collect_metrics.py` | Hourly metrics collector | Auto-started by test runner |
| `generate_integration_report.py` | Report generator | `python3 generate_integration_report.py LOG_FILE` |
| `monitor_live.sh` | Real-time log monitor | `./monitor_live.sh [LOG_FILE]` |

## Documentation

- **[QUICK_START.md](QUICK_START.md)** - One-page quick reference
- **[INTEGRATION_TEST_FRAMEWORK.md](INTEGRATION_TEST_FRAMEWORK.md)** - Complete documentation

## What This Tests

Validates 10 critical bug fixes from March 2, 2026:

1. Exit fill confirmation via WebSocket
2. Orderbook WebSocket reliability (>50% entry success)
3. OMS WebSocket initialization
4. Event loop architecture (REST fallback)
5. WebSocket reconnection (REST fallback)
6. Exit price accuracy (use actual fills)
7. Entry fee logging
8. Balance tracking and drift
9. Position reconciliation
10. Duplicate position prevention

## Success Criteria

- ✓ All bug validations pass
- ✓ Entry success rate ≥50%
- ✓ Exit success rate ≥95%
- ✓ Balance drift ≤10¢
- ✓ No stranded positions
- ✓ No critical errors

## Output Files

All files saved to `logs/`:

```
integration_test_YYYY-MM-DD_HH-MM-SS.log          # Main log
integration_test_YYYY-MM-DD_HH-MM-SS_metrics.json # Metrics
integration_test_YYYY-MM-DD_HH-MM-SS_config.yaml  # Config
integration_test_YYYY-MM-DD_HH-MM-SS_report.html  # Report
```

## Examples

### Run 2-hour test (faster iteration)
```bash
./run_integration_test.sh --duration-hours 2
```

### Use custom configuration
```bash
./run_integration_test.sh --config my_config.yaml
```

### Generate Markdown report
```bash
python3 generate_integration_report.py logs/test.log --format md
```

### Monitor with all lines (no filtering)
```bash
./monitor_live.sh --all
```

### Collect metrics once (manual snapshot)
```bash
python3 collect_metrics.py \
    --log-file logs/test.log \
    --output metrics.json \
    --once
```

## Troubleshooting

### Test won't start
```bash
# Remove stale PID file
rm logs/integration_test.pid
```

### Check test status
```bash
# Is test running?
cat logs/integration_test.pid
ps -p $(cat logs/integration_test.pid)

# Watch progress
tail -f logs/integration_test_*.log
```

### Stop test early
```bash
kill $(cat logs/integration_test.pid)
```

## Next Steps

After successful Phase 2:

1. **Phase 3:** Stress tests (circuit breaker, reconnection, reconciliation)
2. **Production:** Deploy with real money (start small!)

## Support

Questions? See:
- [INTEGRATION_TEST_FRAMEWORK.md](INTEGRATION_TEST_FRAMEWORK.md) - Full documentation
- [QUICK_START.md](QUICK_START.md) - Quick reference
- Project root documentation for bug fix details

---

**Created:** March 2, 2026 | **Framework Version:** 1.0
