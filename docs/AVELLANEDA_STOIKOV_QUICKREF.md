# Avellaneda-Stoikov Reservation Price - Quick Reference

## Formula

```
r = s - q × γ × σ² × (T - t)
```

- **r**: Reservation price (quote center)
- **s**: Fair value
- **q**: Net position (+ long, - short)
- **γ**: Risk aversion (0.01-0.1, default: 0.01)
- **σ²**: Variance
- **T-t**: Time to expiry (years)

## Quick Start

### Enable in Config

```yaml
# strategies/configs/prediction_mm_strategy.yaml
quotes:
  use_reservation_price: true
  risk_aversion: 0.05
```

### Use in Code

```python
from strategies.prediction_mm import ReservationPricer

pricer = ReservationPricer(risk_aversion=0.05)
result = pricer.calculate(
    fair_price=0.5,
    net_position=50,
    variance=0.25,
    time_to_expiry_sec=600,
)
print(result.reservation_price)  # 0.4999
```

### QuoteEngine Integration

```python
from strategies.prediction_mm.quote_engine import QuoteEngine
from strategies.prediction_mm.pricer import BinaryBSPricer

engine = QuoteEngine(
    pricer=BinaryBSPricer(),
    use_reservation_price=True,
    risk_aversion=0.05,
)

quote = engine.generate(
    state=market_state,
    sigma=0.5,
    net_position=50,  # Required in A-S mode
)
```

## Key Behaviors

| Position | Effect | Reason |
|----------|--------|--------|
| Long (q > 0) | Lower quotes | Encourage selling to unwind |
| Short (q < 0) | Raise quotes | Encourage buying to unwind |
| None (q = 0) | No change | Symmetric quotes |

| Parameter | High Value | Low Value |
|-----------|-----------|-----------|
| γ (risk_aversion) | Aggressive unwinding | Passive unwinding |
| σ² (variance) | Larger adjustment | Smaller adjustment |
| T (time to expiry) | Larger adjustment | Smaller adjustment |

## Parameter Tuning

**Risk Aversion (γ)**
- Conservative: 0.01
- Balanced: 0.05
- Aggressive: 0.1

Start low and increase if inventory builds up.

## Advantages over Simple Skew

✓ Time-aware (adjustment increases with time to expiry)
✓ Variance-aware (adjustment scales with volatility)
✓ Theoretically grounded (optimal market making)
✓ Natural unwinding behavior as expiry approaches

## Files

- Implementation: `strategies/prediction_mm/reservation_pricer.py`
- Integration: `strategies/prediction_mm/quote_engine.py`
- Config: `strategies/prediction_mm/config.py`
- Tests: `tests/strategies/test_reservation_pricer.py` (21 tests)
- Docs: `docs/AVELLANEDA_STOIKOV_RESERVATION_PRICE.md`

## Common Gotchas

1. **Must provide `net_position`** when A-S mode enabled
2. **`inventory_skew` is ignored** in A-S mode
3. **Small adjustments** for short time horizons (expected)
4. **Opt-in by default** (`use_reservation_price=False`)

## Example Calculation

```
Fair value: 50¢
Position: +50 contracts (long)
Volatility: σ=0.5 → σ²=0.25
Time: 10 min = 600 sec = 0.000019 years
Risk aversion: γ=0.05

r = 0.5 - 50 × 0.05 × 0.25 × 0.000019
r = 0.5 - 0.0000119
r ≈ 49.99¢  (slightly below fair to encourage selling)
```

## Testing

```bash
# Run all A-S tests (54 total)
pytest tests/strategies/test_reservation_pricer.py \
       tests/strategies/test_quote_engine_reservation.py \
       tests/strategies/test_quote_engine.py -v
```

All tests passing ✓
