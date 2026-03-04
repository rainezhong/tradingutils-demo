# VPIN Kill Switch

Automatic toxicity detection and quote protection for the prediction market maker.

## Overview

The VPIN (Volume-Synchronized Probability of Informed Trading) kill switch automatically detects toxic order flow and protects the market maker by:

- **WARNING state**: Widening spreads when order flow becomes imbalanced (VPIN >= 0.50)
- **TOXIC state**: Pulling all quotes when order flow is highly toxic (VPIN >= 0.70)
- **Cooldown**: Maintaining protection for a cooldown period after toxic events

## How It Works

### VPIN Calculation

VPIN measures the imbalance between buyer-initiated and seller-initiated volume across equal-volume buckets:

1. Each trade is classified as buy or sell using the Lee-Ready rule
2. Trades are accumulated into equal-volume buckets (e.g., 10 contracts each)
3. For each bucket, compute `|V_buy - V_sell| / bucket_volume`
4. VPIN = mean of the last N bucket imbalances

VPIN ranges from 0 to 1, where higher values indicate more toxic/informed flow.

### State Machine

```
NORMAL (VPIN < 0.50)
  ↓
WARNING (0.50 ≤ VPIN < 0.70)
  → Spreads widened by 2.5x
  ↓
TOXIC (VPIN ≥ 0.70)
  → All quotes cancelled
  → 60-second cooldown
  ↓
NORMAL (after cooldown + VPIN drops)
```

### Trade Classification

Trades are classified using the Lee-Ready algorithm:
- Trade at ask or above → buyer-initiated
- Trade at bid or below → seller-initiated
- Trade at mid → tick rule (compare to last price)

The kill switch feeds VPIN with our own fills, using the market's best bid/ask at fill time.

## Configuration

Add to `strategies/configs/prediction_mm_strategy.yaml`:

```yaml
vpin_kill_switch:
  enabled: true               # Enable VPIN kill switch
  check_interval_sec: 5.0     # How often to check VPIN (rate-limited)
  bucket_volume: 10.0         # Volume per VPIN bucket (contracts)
  num_buckets: 50             # Rolling window of buckets
  warning_threshold: 0.50     # VPIN >= 0.50 → widen spreads
  toxic_threshold: 0.70       # VPIN >= 0.70 → pull all quotes
  toxic_cooldown_sec: 60.0    # Stay pulled for 60s after toxic event
  warning_spread_multiplier: 2.5  # Widen spreads by 2.5x in WARNING
```

### Parameter Tuning

**bucket_volume**:
- Smaller values (5-10) → more sensitive to short bursts of toxicity
- Larger values (20-50) → smoother, less reactive to noise
- Typical: 10 contracts for Kalshi markets

**num_buckets**:
- More buckets (50-100) → longer memory of past flow
- Fewer buckets (20-30) → faster response to recent changes
- Typical: 50 buckets

**warning_threshold**:
- VPIN above this triggers spread widening
- Typical: 0.50 (moderate imbalance)
- Lower (0.40) → more conservative (widen sooner)
- Higher (0.60) → less reactive

**toxic_threshold**:
- VPIN above this pulls all quotes
- Typical: 0.70 (severe imbalance)
- Lower (0.65) → more protective
- Higher (0.75) → only react to extreme toxicity

**toxic_cooldown_sec**:
- How long to stay pulled after a toxic event
- Typical: 60 seconds
- Longer (120s) → more conservative
- Shorter (30s) → faster return to quoting

**warning_spread_multiplier**:
- How much to widen spreads in WARNING state
- Typical: 2.5x (e.g., 4c spread → 10c spread)
- Higher (3.0-4.0) → more protection
- Lower (1.5-2.0) → less impact on competitiveness

## Usage

### Enable via Config

```yaml
vpin_kill_switch:
  enabled: true
```

### Run Strategy

```bash
python3 main.py run prediction-mm --config strategies/configs/prediction_mm_strategy.yaml
```

### Monitor Logs

The kill switch logs all state transitions:

```
[12:34:56] PredictionMM: VPIN kill switch enabled: warn=0.50, toxic=0.70
[12:35:10] PredictionMM: VPIN KILL SWITCH: NORMAL → WARNING (VPIN=0.550, reason: VPIN in warning zone)
[12:35:25] PredictionMM: VPIN KILL SWITCH: WARNING → TOXIC (VPIN=0.720, reason: VPIN exceeded toxic threshold)
[12:35:25] PredictionMM: KILL SWITCH ACTIVATED - all quotes cancelled, cooldown until 1677512185.0
[12:36:30] PredictionMM: VPIN KILL SWITCH: TOXIC → NORMAL (VPIN=0.420, reason: VPIN returned to normal)
```

## Behavior by State

### NORMAL State
- Quotes generated normally
- VPIN < 0.50
- No protection active

### WARNING State
- Spreads widened by 2.5x (configurable)
- VPIN between 0.50 and 0.70
- Still quoting both bid and ask
- Provides partial protection while maintaining liquidity

Example:
- Normal spread: 4 cents (bid=48c, ask=52c)
- WARNING spread: 10 cents (bid=45c, ask=55c)

### TOXIC State
- **All quotes cancelled immediately**
- VPIN >= 0.70
- No quoting for `toxic_cooldown_sec` (default 60s)
- Even if VPIN drops, cooldown period must expire first
- Logs: `KILL SWITCH ACTIVATED - all quotes cancelled`

## Integration Points

### Feed VPIN on Fills

The orchestrator automatically feeds VPIN when fills occur:

```python
def _on_fill(self, ticker: str, is_buy: bool, size: int, price_cents: int):
    # ... inventory updates ...

    # Feed to VPIN calculator
    if self._vpin is not None:
        market = self._markets.get(ticker)
        if market:
            self._vpin.on_trade(
                price=price_cents,
                size=size,
                bid=market.yes_bid,
                ask=market.yes_ask,
                is_buy=is_buy
            )
```

### Check VPIN on Tick

The kill switch is checked on every tick (rate-limited to `check_interval_sec`):

```python
async def on_tick(self):
    # Check VPIN kill switch first
    self._check_vpin_kill_switch()

    # ... rest of tick logic ...
```

### Spread Adjustment

In WARNING state, spreads are widened:

```python
spread_multiplier = self._get_vpin_spread_multiplier()  # Returns 2.5 in WARNING
if spread_multiplier > 1.0:
    old_spread = quote.spread_cents
    new_spread = int(old_spread * spread_multiplier)
    # ... widen bid/ask ...
```

### Quote Suppression

In TOXIC state, quotes are suppressed:

```python
should_quote_bid = inv_state.should_quote_bid
should_quote_ask = inv_state.should_quote_ask

if self._kill_switch_state == KillSwitchState.TOXIC:
    should_quote_bid = False
    should_quote_ask = False
```

## Testing

Run the test suite:

```bash
pytest tests/strategies/test_vpin_kill_switch.py -v
```

Tests cover:
- Config loading and YAML serialization
- VPIN calculator initialization
- Trade data feeding from fills
- State transitions (NORMAL → WARNING → TOXIC → NORMAL)
- Automatic quote cancellation in TOXIC
- Spread widening in WARNING
- Rate limiting of VPIN checks
- Cooldown enforcement
- Logging of state transitions
- Graceful degradation when VPIN module unavailable

## Performance Considerations

### Memory

VPIN maintains:
- Rolling window of `num_buckets` bucket imbalances (50 floats = 400 bytes)
- Current bucket state (3 floats = 24 bytes)
- Running totals (2 floats = 16 bytes)

Total: ~500 bytes per VPIN calculator instance.

### CPU

- VPIN check: O(1) per bucket completion, O(num_buckets) for reading
- Rate-limited to 1 check per `check_interval_sec` (default 5s)
- Negligible overhead (<0.1ms per check)

### Network

- No additional network calls
- Uses existing fill data
- Cancel-all in TOXIC uses existing order manager API

## Limitations

### Bucket Completion Delay

VPIN requires completing at least one full bucket before producing readings. With `bucket_volume=10.0`, VPIN won't trigger until 10 contracts have traded.

**Mitigation**: Use smaller bucket volumes (5.0) for faster response in low-volume markets.

### Fill Data Only

VPIN only sees our own fills, not the full market tape. This means:
- Underestimates toxicity if we're getting adversely selected but others aren't
- Overestimates toxicity if our fills are unrepresentative of market flow

**Mitigation**: Use conservative thresholds (warning=0.50, toxic=0.70) to account for sampling bias.

### No Persistence

VPIN state resets when the strategy restarts. A toxic event just before shutdown won't carry over.

**Mitigation**: Conservative cooldown periods (60s) ensure protection persists within a session.

## Troubleshooting

### "VPIN kill switch enabled but no readings"

VPIN requires at least one completed bucket. Check:
- Have you had any fills yet?
- Is `bucket_volume` too large for your volume?

### "Too many false positives (WARNING state)"

Lower the `warning_threshold`:
```yaml
warning_threshold: 0.60  # Was 0.50
```

Or increase `bucket_volume` for less sensitivity:
```yaml
bucket_volume: 20.0  # Was 10.0
```

### "Kill switch doesn't trigger fast enough"

Reduce `bucket_volume` for faster bucket completion:
```yaml
bucket_volume: 5.0  # Was 10.0
```

Reduce `check_interval_sec` for more frequent checks:
```yaml
check_interval_sec: 2.0  # Was 5.0
```

### "Stuck in TOXIC state too long"

Reduce cooldown period:
```yaml
toxic_cooldown_sec: 30.0  # Was 60.0
```

## References

- Easley, D., López de Prado, M. M., & O'Hara, M. (2012). "Flow Toxicity and Liquidity in a High-Frequency World." *Review of Financial Studies*, 25(5), 1457-1493.
- Lee, C. M., & Ready, M. J. (1991). "Inferring Trade Direction from Intraday Data." *Journal of Finance*, 46(2), 733-746.

## See Also

- `core/indicators/vpin.py` - VPIN calculator implementation
- `strategies/prediction_mm/adverse_selection.py` - Trade flow imbalance detector (complementary)
- `tests/strategies/test_vpin_kill_switch.py` - Test suite
