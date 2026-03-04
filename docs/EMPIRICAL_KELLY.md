# Empirical Kelly Criterion with Monte Carlo Uncertainty Adjustment

## Overview

The empirical Kelly criterion extends standard Kelly position sizing to account for estimation uncertainty in edge calculations. When you have limited trade history or noisy data, point estimates of edge can be unreliable. The empirical Kelly method uses Monte Carlo bootstrap resampling to measure this uncertainty and applies a coefficient of variation (CV) based haircut to position sizes.

## Problem

**Standard Kelly**: Uses point estimate of edge
```python
f_kelly = edge / variance
```

**Issue**: When edge is estimated from small samples or has high variance, the point estimate may be unreliable. Using it directly can lead to:
- Overfitting to noise
- Excessive position sizes
- Higher risk of ruin

## Solution

**Empirical Kelly**: Adjusts for estimation uncertainty
```python
f_empirical = f_kelly × (1 - CV_edge)

where:
  CV_edge = std(edge) / mean(edge)  # Coefficient of variation
  std(edge) estimated via Monte Carlo bootstrap resampling
```

**Key insight**: If edge estimates are highly variable across different bootstrap samples, reduce position size proportionally.

## Algorithm

### Step 1: Bootstrap Resampling

For each strategy, resample trade PnLs with replacement N times (default: 1000):

```python
for i in range(n_simulations):
    resampled_pnls = [random.choice(pnls) for _ in range(len(pnls))]
    edge_estimates[i] = mean(resampled_pnls)
```

### Step 2: Calculate CV

Compute coefficient of variation of edge estimates:

```python
mean_edge = mean(edge_estimates)
std_edge = std(edge_estimates)
CV_edge = std_edge / mean_edge  # if mean_edge > 0
```

### Step 3: Apply Haircut

Reduce edge by CV factor before Kelly calculation:

```python
adjusted_edge = edge × (1 - CV_edge)
f_empirical = adjusted_edge / variance
```

**Effect**:
- CV = 0.2 → 20% reduction in position size
- CV = 0.5 → 50% reduction in position size
- CV = 1.0 → 100% reduction (zero allocation)

## Configuration

Enable in `config/portfolio_config.yaml`:

```yaml
allocation:
  # Empirical Kelly with Monte Carlo uncertainty adjustment
  use_empirical_kelly: true  # Enable CV-based haircut
  empirical_kelly_simulations: 1000  # Bootstrap samples
  empirical_kelly_seed: null  # Random seed (null = random)
```

## When to Use

**Use empirical Kelly when**:
- Small sample sizes (< 50 trades)
- High variance strategies (Sharpe < 1)
- New or untested strategies
- Edge estimates are uncertain
- You want more conservative sizing

**Skip empirical Kelly when**:
- Large sample sizes (> 200 trades)
- Low variance strategies (Sharpe > 2)
- Well-established strategies
- Edge estimates are stable

## Example

### Scenario: Two strategies with same point estimate edge

| Strategy | Edge (point) | Trade PnLs | CV_edge | Haircut | Effective Edge | Allocation |
|----------|--------------|------------|---------|---------|----------------|------------|
| Stable   | $10.00       | Tight dist | 0.20    | 0.80    | $8.00          | 8.9%       |
| Noisy    | $10.00       | Wide dist  | 0.80    | 0.20    | $2.00          | 2.2%       |

**Result**: Stable strategy gets 4x higher allocation despite same point estimate.

### Code Example

```python
from core.portfolio.allocation_optimizer import AllocationOptimizer
from core.portfolio.types import AllocationConfig

# Enable empirical Kelly
config = AllocationConfig(
    kelly_fraction=0.5,
    use_empirical_kelly=True,
    empirical_kelly_simulations=1000,
    empirical_kelly_seed=42,  # Reproducibility
)

optimizer = AllocationOptimizer(config)

# Provide trade PnLs for CV estimation
trade_pnls = {
    "strategy-a": [10.2, 9.8, 10.5, ...],  # Tight
    "strategy-b": [20.0, -5.0, 15.0, ...],  # Wide
}

result = optimizer.calculate_allocations(
    strategy_stats,
    correlation_matrix,
    strategy_names,
    trade_pnls=trade_pnls,  # Required for empirical Kelly
)
```

## Mathematical Details

### Bootstrap Standard Error

The standard error of the mean edge estimate is:
```
SE(edge) ≈ σ / sqrt(n)
```

Where:
- σ = population standard deviation
- n = number of trades

Bootstrap resampling estimates this empirically without parametric assumptions.

### CV as Uncertainty Metric

Coefficient of variation normalizes uncertainty by magnitude:
```
CV = σ / μ
```

**Interpretation**:
- CV < 0.5: Low uncertainty (high confidence in edge)
- CV = 0.5-1.0: Moderate uncertainty
- CV > 1.0: High uncertainty (edge may be zero or negative)

### Haircut Formula

The haircut `(1 - CV)` is conservative:
- CV = 0: No haircut (100% of Kelly)
- CV = 0.5: 50% haircut (use 50% of Kelly)
- CV ≥ 1.0: 100% haircut (zero allocation)

This ensures position size scales inversely with estimation uncertainty.

## Benefits

1. **Protects against overfitting**: Reduces allocation when edge is uncertain
2. **Adaptive conservatism**: Scales automatically with data quality
3. **Sample-size aware**: Small samples get larger haircuts
4. **Distribution-free**: Bootstrap makes no parametric assumptions
5. **Backward compatible**: Disable to recover standard Kelly

## Limitations

1. **Computational cost**: Requires 1000+ bootstrap iterations
2. **Assumes IID trades**: Bootstrap assumes independent samples
3. **Can be overly conservative**: May miss profitable opportunities
4. **Requires sufficient data**: Needs ≥10 trades for meaningful estimates

## Testing

Run unit tests:
```bash
pytest tests/portfolio/test_allocation_optimizer.py::test_empirical_kelly_cv_adjustment -v
```

Run demo:
```bash
PYTHONPATH=/Users/raine/tradingutils python3 examples/empirical_kelly_demo.py
```

## References

- **Bootstrap Methods**: Efron & Tibshirani, "An Introduction to the Bootstrap" (1993)
- **Kelly Criterion**: J. L. Kelly Jr., "A New Interpretation of Information Rate" (1956)
- **Coefficient of Variation**: Abdi, H., "Coefficient of Variation" (2010)

## Implementation Notes

- Monte Carlo seed can be fixed for reproducibility in tests
- Minimum 10 trades required before applying empirical adjustment
- Negative edges get zero allocation regardless of CV
- Falls back to point estimate if trade_pnls not provided
- Logging shows CV and haircut for each strategy

## Integration

The empirical Kelly adjustment is integrated into the portfolio optimizer:

1. **PerformanceTracker**: Added `get_trade_pnls()` method to fetch historical PnLs
2. **AllocationOptimizer**: Added `_estimate_edge_uncertainty()` and `_apply_empirical_kelly_adjustment()` methods
3. **PortfolioManager**: Passes trade PnLs to optimizer when empirical Kelly is enabled
4. **AllocationConfig**: Added `use_empirical_kelly`, `empirical_kelly_simulations`, `empirical_kelly_seed` parameters

## Future Enhancements

Potential improvements:
- Block bootstrap for non-IID trades
- Time-weighted edge estimates (recent > old)
- Variance uncertainty (CV on variance estimate)
- Bayesian shrinkage instead of CV haircut
- Multi-strategy correlation uncertainty
