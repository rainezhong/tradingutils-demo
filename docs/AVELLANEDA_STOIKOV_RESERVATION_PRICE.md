# Avellaneda-Stoikov Reservation Price Implementation

## Overview

This document describes the implementation of the Avellaneda-Stoikov (A-S) reservation price formula for the prediction market maker strategy. The A-S formula provides a theoretically grounded approach to inventory management that accounts for time-to-expiry, volatility, and risk preferences.

## The Formula

The basic A-S reservation price formula is:

```
r = s - q × γ × σ² × (T - t)
```

Where:
- **r**: Reservation price (where the market maker centers quotes)
- **s**: Fair value / mid-price (from the pricing model)
- **q**: Net position (positive = long, negative = short)
- **γ** (gamma): Risk aversion parameter (controls aggressiveness)
- **σ²**: Annualized variance
- **T - t**: Time to expiry in years

## Key Properties

### 1. Inventory Sensitivity
- **Long position (q > 0)**: Reservation price < fair value → quotes shift down to encourage selling
- **Short position (q < 0)**: Reservation price > fair value → quotes shift up to encourage buying
- **No position (q = 0)**: Reservation price = fair value → symmetric quotes

### 2. Time Decay
- Adjustment magnitude increases linearly with time to expiry
- Far from expiry: Large adjustment → aggressive inventory unwinding
- Near expiry: Small adjustment → less aggressive (less time to unwind)
- At expiry (T = 0): No adjustment (no time left to trade)

### 3. Risk Awareness
- Higher variance (σ²) → larger adjustment (riskier to hold inventory)
- Higher risk aversion (γ) → larger adjustment (more conservative)

### 4. Comparison to Simple Inventory Skew
| Property | Simple Skew | A-S Reservation |
|----------|-------------|-----------------|
| Time-aware | ✗ | ✓ |
| Variance-aware | ✗ | ✓ |
| Theoretically grounded | ✗ | ✓ |
| Simplicity | ✓ | ✗ |

## Implementation

### Module Structure

```
strategies/prediction_mm/
├── reservation_pricer.py    # A-S calculation engine
├── quote_engine.py           # Updated to support A-S mode
└── config.py                 # Config parameters
```

### Core Classes

#### `ReservationPricer`

Calculates the A-S reservation price given market state and position.

```python
from strategies.prediction_mm import ReservationPricer

pricer = ReservationPricer(
    risk_aversion=0.01,           # γ parameter
    use_log_odds=False,           # Optional log-odds transformation
    clamp_range=(0.01, 0.99),     # Output clamping
)

result = pricer.calculate(
    fair_price=0.5,               # Fair value from pricing model
    net_position=50,              # Current position
    variance=0.25,                # Annualized variance (σ²)
    time_to_expiry_sec=600,       # Time until expiry
)

print(result.reservation_price)      # Final reservation price
print(result.inventory_adjustment)   # r - s
```

#### `QuoteEngine` (Updated)

Quote engine now supports A-S reservation pricing via config flags.

```python
from strategies.prediction_mm.pricer import BinaryBSPricer, MarketState
from strategies.prediction_mm.quote_engine import QuoteEngine

pricer = BinaryBSPricer()
engine = QuoteEngine(
    pricer=pricer,
    base_half_spread_vol=0.03,
    use_reservation_price=True,   # Enable A-S mode
    risk_aversion=0.05,            # γ parameter
    reservation_use_log_odds=False,
)

state = MarketState(
    ticker="KXBTC15M-...",
    spot_price=100_000,
    strike_price=100_000,
    time_to_expiry_sec=600,
)

quote = engine.generate(
    state=state,
    sigma=0.5,
    net_position=50,               # Required in A-S mode
    adverse_premium=0.0,
)

# Diagnostics
print(quote.reservation_result.reservation_price)
print(quote.reservation_result.inventory_adjustment)
```

### Configuration

Add to `strategies/configs/prediction_mm_strategy.yaml`:

```yaml
quotes:
  base_half_spread_vol: 0.03
  min_spread_cents: 2
  max_spread_cents: 15

  # Avellaneda-Stoikov reservation price
  use_reservation_price: false    # Opt-in (default: false)
  risk_aversion: 0.01              # γ parameter (default: 0.01)
  reservation_use_log_odds: false  # Log-odds mode (default: false)
```

Or via `PredictionMMConfig`:

```python
from strategies.prediction_mm import PredictionMMConfig

config = PredictionMMConfig(
    use_reservation_price=True,
    risk_aversion=0.05,
    reservation_use_log_odds=False,
)
```

## Log-Odds Transformation (Optional)

For markets bounded in [0, 1], the direct A-S formula can produce out-of-bounds results. The log-odds transformation works in unbounded space:

```
L_s = ln(s / (1 - s))              # Fair value in log-odds
L_r = L_s - q × γ × σ² × T         # Adjustment in log-odds
r = 1 / (1 + exp(-L_r))            # Back to probability
```

Enable via `reservation_use_log_odds=True`. Generally not necessary for typical positions due to clamping.

## Parameter Tuning

### Risk Aversion (γ)

Typical range: 0.01 - 0.1

- **Low (0.01)**: Conservative inventory unwinding, smaller quote adjustments
- **Medium (0.05)**: Balanced approach
- **High (0.1)**: Aggressive unwinding, larger quote adjustments

Rule of thumb: Start with γ = 0.01 and increase if inventory builds up excessively.

### Practical Example

Market state:
- Fair value: 50¢
- Position: +50 contracts (long)
- Volatility: σ = 0.5 → variance = 0.25
- Time to expiry: 10 minutes = 600 seconds
- Risk aversion: γ = 0.05

Calculation:
```
T = 600 / (365.25 × 24 × 60 × 60) = 0.0000190 years
r = 0.5 - 50 × 0.05 × 0.25 × 0.0000190
r = 0.5 - 0.0000119
r ≈ 0.4999¢
```

Small adjustment due to short time horizon. With 1 hour to expiry:
```
T = 3600 / SECONDS_PER_YEAR = 0.000114 years
r = 0.5 - 50 × 0.05 × 0.25 × 0.000114
r ≈ 0.4993¢
```

Larger adjustment with more time.

## Testing

### Unit Tests

Two comprehensive test suites:

1. **`test_reservation_pricer.py`** (21 tests)
   - Basic initialization and edge cases
   - Direct mode formula validation
   - Log-odds mode validation
   - Time decay, variance sensitivity, risk aversion effects
   - Comparison to simple skew

2. **`test_quote_engine_reservation.py`** (20 tests)
   - A-S mode integration with QuoteEngine
   - Time decay in quote generation
   - Risk aversion parameter effects
   - Log-odds mode in QuoteEngine
   - Edge cases and robustness
   - Backward compatibility

Run tests:
```bash
pytest tests/strategies/test_reservation_pricer.py -v
pytest tests/strategies/test_quote_engine_reservation.py -v
pytest tests/strategies/test_quote_engine.py -v  # Backward compat
```

### Test Coverage

- ✓ Basic formula correctness (manual calculations)
- ✓ Edge cases (zero position, zero time, extreme values)
- ✓ Time decay behavior
- ✓ Variance sensitivity
- ✓ Risk aversion scaling
- ✓ Clamping to valid range [0.01, 0.99]
- ✓ Log-odds transformation
- ✓ Integration with QuoteEngine
- ✓ Backward compatibility with existing code

## Migration Guide

### Enabling A-S Mode

**Option 1: YAML config**

```yaml
# strategies/configs/prediction_mm_strategy.yaml
quotes:
  use_reservation_price: true
  risk_aversion: 0.05
```

**Option 2: Code**

```python
config = PredictionMMConfig(
    use_reservation_price=True,
    risk_aversion=0.05,
)
```

### Backward Compatibility

The implementation is fully backward compatible:

- **Default behavior**: `use_reservation_price=False` → existing vol-space skew
- **Optional parameter**: `net_position` in `QuoteEngine.generate()` is optional in standard mode
- **Existing tests pass**: All 13 existing quote engine tests pass unchanged
- **New field**: `QuoteResult.reservation_result` is `None` in standard mode

### Switching from Simple Skew to A-S

When enabling A-S mode:

1. **`inventory_skew` is ignored**: A-S replaces the simple skew mechanism
2. **Must provide `net_position`**: Required for A-S calculation
3. **Start with low γ**: Begin with `risk_aversion=0.01` and tune upward
4. **Monitor diagnostics**: Use `reservation_result` to inspect adjustments

Example orchestrator update:

```python
# Before (simple skew)
inv_state = inventory_mgr.get_inventory_state(ticker)
quote = engine.generate(
    state=state,
    sigma=sigma,
    inventory_skew=inv_state.skew_vol_points,  # Old approach
)

# After (A-S)
pos = inventory_mgr.get_position(ticker)
quote = engine.generate(
    state=state,
    sigma=sigma,
    net_position=pos.net_position,  # New approach
)
```

## Theory Background

The A-S reservation price comes from the HJB equation for optimal market making:

```
V_t + (r - δ) × s × V_s + 0.5 × σ² × s² × V_ss = 0
```

With inventory penalty, the optimal reservation price satisfies:

```
r*(q) = s - q × γ × σ² × (T - t)
```

This creates optimal bid/ask spread:

```
δ_bid = δ_ask = γ × σ² × (T - t) / 2 + (1/γ) × ln(1 + γ/k)
```

Where k is order arrival rate. In our implementation, we use the base half-spread parameter instead of deriving it from arrival rate.

## References

- Avellaneda, M., & Stoikov, S. (2008). "High-frequency trading in a limit order book." Quantitative Finance, 8(3), 217-224.
- Guéant, O., Lehalle, C. A., & Fernandez-Tapia, J. (2013). "Dealing with the inventory risk: a solution to the market making problem." Mathematics and Financial Economics, 7(4), 477-507.

## Files Modified

1. **`strategies/prediction_mm/reservation_pricer.py`** (NEW): Core A-S calculation
2. **`strategies/prediction_mm/quote_engine.py`**: Added A-S mode support
3. **`strategies/prediction_mm/config.py`**: Added config parameters
4. **`strategies/prediction_mm/__init__.py`**: Export new classes
5. **`tests/strategies/test_reservation_pricer.py`** (NEW): 21 unit tests
6. **`tests/strategies/test_quote_engine_reservation.py`** (NEW): 20 integration tests

Total: 41 new tests, all passing.
