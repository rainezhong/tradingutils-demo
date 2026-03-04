# VPIN Kill Switch - Quick Reference

## TL;DR

Automatic toxicity detection that protects the market maker by widening spreads (WARNING) or pulling quotes (TOXIC) when order flow becomes imbalanced.

## Enable

```yaml
# strategies/configs/prediction_mm_strategy.yaml
vpin_kill_switch:
  enabled: true
```

## States

| State | VPIN Range | Action |
|-------|------------|--------|
| NORMAL | < 0.50 | Normal quoting |
| WARNING | 0.50-0.70 | Spreads widened 2.5x |
| TOXIC | ≥ 0.70 | All quotes cancelled for 60s |

## Config Defaults

```yaml
vpin_kill_switch:
  enabled: false              # Opt-in
  check_interval_sec: 5.0     # Check every 5s
  bucket_volume: 10.0         # 10 contracts per bucket
  num_buckets: 50             # 50-bucket rolling window
  warning_threshold: 0.50     # Widen at 50% imbalance
  toxic_threshold: 0.70       # Pull at 70% imbalance
  toxic_cooldown_sec: 60.0    # 60s cooldown after toxic
  warning_spread_multiplier: 2.5  # Widen by 2.5x
```

## Log Messages

```
[12:34:56] PredictionMM: VPIN kill switch enabled: warn=0.50, toxic=0.70
[12:35:10] PredictionMM: VPIN KILL SWITCH: NORMAL → WARNING (VPIN=0.550, reason: VPIN in warning zone)
[12:35:25] PredictionMM: VPIN KILL SWITCH: WARNING → TOXIC (VPIN=0.720, reason: VPIN exceeded toxic threshold)
[12:35:25] PredictionMM: KILL SWITCH ACTIVATED - all quotes cancelled, cooldown until 1677512185.0
[12:36:30] PredictionMM: VPIN KILL SWITCH: TOXIC → NORMAL (VPIN=0.420, reason: VPIN returned to normal)
```

## Quick Tuning

**More sensitive** (pull quotes sooner):
```yaml
warning_threshold: 0.40  # Was 0.50
toxic_threshold: 0.60    # Was 0.70
bucket_volume: 5.0       # Was 10.0
```

**Less sensitive** (wider spreads, fewer pulls):
```yaml
warning_threshold: 0.60  # Was 0.50
toxic_threshold: 0.80    # Was 0.70
bucket_volume: 20.0      # Was 10.0
```

**More protection in WARNING**:
```yaml
warning_spread_multiplier: 4.0  # Was 2.5
```

**Faster recovery from TOXIC**:
```yaml
toxic_cooldown_sec: 30.0  # Was 60.0
```

## Testing

```bash
# Unit tests
pytest tests/strategies/test_vpin_kill_switch.py -v

# All 22 tests should pass
```

## See Also

- Full docs: `docs/VPIN_KILL_SWITCH.md`
- VPIN implementation: `core/indicators/vpin.py`
- Tests: `tests/strategies/test_vpin_kill_switch.py`
