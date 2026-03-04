# Portfolio Allocation Optimizer

Multi-variate Kelly criterion optimizer for coordinating capital allocation across strategies.

## Overview

When running multiple strategies simultaneously, independent Kelly sizing can lead to over-leverage when strategies are correlated. The portfolio optimizer solves this by:

1. **Tracking Performance**: Records all trades in a central database
2. **Estimating Correlation**: Blends empirical correlation with domain priors
3. **Optimizing Allocation**: Uses multi-variate Kelly to compute optimal capital distribution
4. **Rebalancing**: Updates strategy bankrolls transparently and periodically

**Expected Benefits**:
- Higher geometric growth rate than independent strategies
- Risk-adjusted through correlation awareness
- Automatic rebalancing as performance evolves
- Zero coupling (strategies don't need modification)

---

## Quick Start

### 1. Enable Portfolio Mode

Edit `config/portfolio_config.yaml`:

```yaml
portfolio:
  enabled: true  # Enable portfolio optimization
```

### 2. Configure Prior Correlations

Specify domain knowledge about strategy correlations:

```yaml
prior_correlations:
  "nba-underdog:nba-mispricing": 0.4  # NBA strategies correlate
  "crypto-latency:crypto-scalp": 0.5  # Crypto strategies correlate
  "nba-underdog:crypto-latency": 0.1  # Cross-asset: low correlation
```

### 3. Run Multiple Strategies

```bash
# Enable via environment variable
export ENABLE_PORTFOLIO_OPT=true

# Run strategies in portfolio mode
python main.py run nba-underdog --live
python main.py run crypto-latency --live
```

The portfolio manager will:
- Track all trades in `data/portfolio_trades.db`
- Rebalance daily (default 9:30 AM ET)
- Update strategy bankrolls automatically

---

## Architecture

### Components

```
PortfolioManager (orchestrator)
    ├── PerformanceTracker (trade logging + stats)
    ├── CorrelationEstimator (empirical + prior correlation)
    └── AllocationOptimizer (multi-variate Kelly solver)
```

### Integration Points

**Data Collection**:
- Backtest: `src/backtesting/engine.py` writes fills to DB after run
- Live: `core/order_manager/order_manager.py` logs fills in `on_fill()` callback

**Allocation**:
- Portfolio manager updates `strategy._config.bankroll`
- Strategies use existing Kelly sizing with updated bankroll
- Zero coupling: strategies unaware of portfolio optimization

---

## Multi-Variate Kelly Algorithm

**Standard Kelly** (single strategy):
```
f* = edge / variance
```

**Multi-Variate Kelly** (correlated strategies):
```
f* = Σ⁻¹ · m

Where:
- f* = vector of optimal fractions [f1, f2, ..., fn]
- Σ = covariance matrix (correlation × std devs)
- m = vector of mean returns (edges)
```

**Constraints**:
1. Individual caps: Max 25% per strategy (default)
2. Total allocation: Max 80% deployed (20% reserve)
3. Minimum threshold: Ignore allocations < 5%
4. Fractional Kelly: Use 0.5x (half-Kelly) for conservatism
5. Non-negative: Zero allocation if negative edge

**Empirical Kelly Adjustment** (optional):

When enabled (`use_empirical_kelly: true`), applies CV-based haircut to account for edge estimation uncertainty:

```
f_empirical = f_kelly × (1 - CV_edge)

Where:
- CV_edge = coefficient of variation of edge estimates
- CV_edge = std(edge) / mean(edge)
- std(edge) estimated via Monte Carlo resampling (bootstrap)
```

**Benefits**:
- Reduces position sizes when edge estimates are uncertain
- Protects against overfitting to noisy data
- Automatically scales with estimation quality
- More conservative when sample size is small

**Trade-off**: Lower allocations but more robust to estimation error.

---

## Configuration

### Allocation Constraints

```yaml
allocation:
  kelly_fraction: 0.5  # Half Kelly (conservative)
  max_allocation_per_strategy: 0.25  # Max 25% per strategy
  max_total_allocation: 0.80  # Max 80% deployed (20% reserve)
  min_allocation_threshold: 0.05  # Ignore allocations < 5%
  min_trades_per_strategy: 10  # Need ≥10 trades for allocation

  # Empirical Kelly with Monte Carlo uncertainty adjustment
  use_empirical_kelly: false  # Enable CV-based haircut (default: disabled)
  empirical_kelly_simulations: 1000  # Monte Carlo simulations for edge uncertainty
  empirical_kelly_seed: null  # Random seed for reproducibility (null = random)
```

**Empirical Kelly**: When enabled, uses Monte Carlo bootstrap resampling to estimate edge uncertainty and applies a CV-based haircut to allocations. This protects against overfitting when edge estimates are noisy or based on small sample sizes. The coefficient of variation (CV) measures how uncertain the edge estimate is, and positions are scaled down proportionally.

### Rebalancing

```yaml
rebalance_interval_sec: 86400  # Daily (24 hours)
rebalance_on_pnl_change_pct: 0.20  # Trigger on ±20% bankroll change
rebalance_min_interval_sec: 43200  # Rate limit: 12 hours minimum
```

### Correlation

```yaml
correlation_shrinkage: 0.70  # 70% sample, 30% prior
default_correlation: 0.1  # Default between unrelated strategies
market_overlap_threshold: 0.20  # 20% ticker overlap
market_overlap_correlation: 0.5  # Force correlation if overlap
```

**Shrinkage Estimator**: Blends empirical correlation with priors
- 70% weight on empirical data (from time-aligned returns)
- 30% weight on domain priors (from config)
- Market overlap detection: force ≥0.5 correlation if ticker overlap >20%

---

## CLI Commands

### Portfolio Status

Show current allocations and performance:

```bash
python main.py portfolio status
```

Output:
```
================================================================================
PORTFOLIO STATUS
================================================================================
Database: data/portfolio_trades.db
Strategies: 3

Strategy Performance:
--------------------------------------------------------------------------------
nba-underdog                   PnL=$  234.50  trades=  42  edge=$  5.58  sharpe= 1.23  win%=65.0
crypto-latency                 PnL=$  -12.30  trades=  15  edge=$ -0.82  sharpe=-0.15  win%=46.7
nba-mispricing                 PnL=$   78.90  trades=  28  edge=$  2.82  sharpe= 0.89  win%=60.7
================================================================================
```

### Force Rebalance

Manually trigger rebalancing:

```bash
python main.py portfolio rebalance --reason "manual override"
```

### Analyze Performance

Generate detailed analysis with correlation matrix:

```bash
python main.py portfolio analyze --days 60 --export portfolio_report.csv
```

Output:
```
================================================================================
PORTFOLIO ANALYSIS (Last 60 days)
================================================================================

Individual Strategy Performance:
--------------------------------------------------------------------------------
Strategy                       Total PnL     Trades       Edge   Std Dev    Sharpe  Win %    Avg Win   Avg Loss
--------------------------------------------------------------------------------
crypto-latency                 $    -12.30       15  $   -0.82  $    5.23     -0.15   46.7%  $     8.50  $    -6.20
nba-mispricing                 $     78.90       28  $    2.82  $    3.17      0.89   60.7%  $     7.20  $    -4.10
nba-underdog                   $    234.50       42  $    5.58  $    4.54      1.23   65.0%  $     9.80  $    -5.30
--------------------------------------------------------------------------------
TOTAL                          $    301.10       85

Correlation Matrix:
--------------------------------------------------------------------------------
                              crypto-lat  nba-mispri  nba-underd
crypto-latency                       1.00        0.15        0.12
nba-mispricing                       0.15        1.00        0.42
nba-underdog                         0.12        0.42        1.00

Average Pairwise Correlation: 0.23

✓ Exported to portfolio_report.csv
================================================================================
```

### Advanced Analysis

Generate visualizations (requires matplotlib):

```bash
python scripts/analyze_portfolio.py --db data/portfolio_trades.db --plot
```

Generates:
- PnL by strategy (bar chart)
- Sharpe ratios (bar chart)
- Win rate vs edge (scatter plot)
- Correlation heatmap

---

## Example Allocation

**Scenario**: Running 3 strategies

| Strategy | Edge | Std Dev | Sharpe | Prior Correlation |
|----------|------|---------|--------|------------------|
| nba-underdog | $5.50 | $4.50 | 1.22 | 0.4 with nba-mispricing |
| nba-mispricing | $2.80 | $3.20 | 0.88 | 0.4 with nba-underdog |
| crypto-latency | $8.00 | $12.00 | 0.67 | 0.1 with NBA strategies |

**Independent Kelly** (no correlation):
- nba-underdog: 27% (5.5 / 20.25 = 0.27)
- nba-mispricing: 27% (2.8 / 10.24 = 0.27)
- crypto-latency: 5.5% (8.0 / 144 = 0.055)
- **Total: 59.5%**

**Multi-Variate Kelly** (with correlation):
- nba-underdog: **20%** (reduced due to correlation with nba-mispricing)
- nba-mispricing: **15%** (reduced due to correlation)
- crypto-latency: **5%** (unchanged, uncorrelated)
- **Total: 40%** (safer due to NBA correlation)

**Result**: Lower total allocation but higher Sharpe ratio (diversification benefit).

---

## Risk Management

### Safeguards

1. **Allocation caps**: Max 25% per strategy (prevent over-concentration)
2. **Reserve buffer**: Keep 20% unallocated (emergency cushion)
3. **Minimum threshold**: Ignore allocations < 5% (eliminate noise)
4. **Rebalance rate limit**: Max 1 rebalance per 12 hours (avoid thrashing)
5. **Negative edge filter**: Strategies with negative edge get 0 allocation
6. **Minimum trades**: Need ≥10 trades before including in allocation
7. **Fallback to equal-weight**: If solver fails or insufficient data
8. **Covariance regularization**: Add ridge term (1e-6) to prevent singularity

### Monitoring

**Check allocation history**:
```bash
sqlite3 data/portfolio_trades.db "SELECT * FROM strategy_performance ORDER BY date DESC LIMIT 20"
```

**Check for over-allocation**:
```bash
python main.py portfolio status | grep "Total Allocated"
```

Should be ≤80% (max_total_allocation).

---

## Testing

Run unit tests:

```bash
pytest tests/portfolio/ -v
```

Tests cover:
- Performance tracking (edge, variance, win rate)
- Correlation estimation (shrinkage, market overlap)
- Allocation optimizer (Kelly solver, constraints)
- End-to-end integration

---

## Troubleshooting

### "No strategies with sufficient stats"

**Cause**: Strategies need ≥10 settled trades before allocation.

**Solution**: Run strategies longer or lower `min_trades_per_strategy` in config.

### "Singular matrix error"

**Cause**: Covariance matrix is not invertible (perfect correlation or insufficient data).

**Solution**: Increase `ridge_regularization` or wait for more data. System falls back to equal-weight.

### "Allocation exceeds 100%"

**Cause**: Bug in constraint enforcement.

**Solution**: Check `max_total_allocation` and `max_allocation_per_strategy` are correctly applied. File a bug report.

### Strategies not recording trades

**Cause**: Integration hooks not called.

**Solution**: Verify:
- Backtest: `performance_tracker.record_backtest_fills()` called after `engine.run()`
- Live: `performance_tracker.record_trade()` called in `OrderManager.on_fill()`

---

## Advanced Usage

### Custom Correlation Priors

For new strategy pairs, add to `prior_correlations`:

```yaml
prior_correlations:
  "my-strategy:nba-underdog": 0.2  # Low correlation
  "my-strategy:crypto-scalp": 0.6  # High correlation
```

### Aggressive Kelly

Use full Kelly instead of half Kelly (riskier):

```yaml
allocation:
  kelly_fraction: 1.0  # Full Kelly
```

### Higher Reserve

Keep more cash reserve:

```yaml
allocation:
  max_total_allocation: 0.60  # 60% deployed, 40% reserve
```

### Enable Empirical Kelly

Use CV-based adjustment for more robust sizing under uncertainty:

```yaml
allocation:
  use_empirical_kelly: true  # Enable
  empirical_kelly_simulations: 1000  # 1000 bootstrap samples
  empirical_kelly_seed: 42  # Optional: for reproducibility
```

**When to use**:
- Small sample sizes (< 50 trades per strategy)
- High variance strategies (edge/std_dev < 1)
- Uncertain edge estimates (new or untested strategies)

**Effect**: Reduces allocations for strategies with high edge uncertainty, protects against overfitting.

**Example**:
- Strategy A: edge=$10, CV=0.2 → haircut=0.8 (20% reduction)
- Strategy B: edge=$10, CV=0.5 → haircut=0.5 (50% reduction)

---

## References

- **Kelly Criterion**: J. L. Kelly Jr., "A New Interpretation of Information Rate" (1956)
- **Multi-Variate Kelly**: E. O. Thorp, "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market" (1997)
- **Shrinkage Estimation**: O. Ledoit & M. Wolf, "Honey, I Shrunk the Sample Covariance Matrix" (2004)

---

## Implementation Checklist

- [x] Phase 1: Data collection infrastructure
- [x] Phase 2: Correlation estimation
- [x] Phase 3: Multi-variate Kelly optimizer
- [x] Phase 4: Portfolio manager integration
- [x] Phase 5: Monitoring & tooling
- [ ] Integration hooks in backtest engine
- [ ] Integration hooks in OMS
- [ ] End-to-end testing with live strategies
- [ ] Performance validation (portfolio vs independent)
