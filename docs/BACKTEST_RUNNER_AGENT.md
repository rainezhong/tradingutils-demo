# Backtest Runner Agent

Automated backtest runner with comprehensive statistical validation, walk-forward testing, and parameter sensitivity analysis.

## Overview

The `BacktestRunnerAgent` automates the process of testing trading hypotheses by:

1. Running backtests using the unified framework
2. Performing statistical validation (Sharpe, Sortino, t-test, p-value)
3. Walk-forward validation (train/test split for out-of-sample testing)
4. Parameter sensitivity analysis (robustness checks)
5. Storing results in a SQLite database for later analysis

## Quick Start

```python
from agents.backtest_runner import BacktestRunnerAgent

# Initialize agent
agent = BacktestRunnerAgent(
    db_path="data/backtest_results.db",
    enable_walk_forward=True,
    enable_sensitivity=True,
)

# Define hypothesis
hypothesis = "Early-game NBA mispricing provides exploitable edge"

# Configure adapter
adapter_config = {
    "type": "nba-mispricing",
    "params": {
        "min_edge_cents": 3.0,
        "max_period": 2,
        "position_size": 10,
    },
}

# Configure data
data_config = {
    "type": "nba",
    "path": "data/recordings/game_001.json",
}

# Run backtest with validation
results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

# View results
print(results.summary())
```

## Supported Strategies

### NBA Strategies

#### 1. NBA Mispricing
Early-game win probability mispricing detection.

```python
adapter_config = {
    "type": "nba-mispricing",
    "params": {
        "min_edge_cents": 3.0,      # Minimum edge in cents
        "max_period": 2,             # Max quarter (1 or 2)
        "position_size": 10,         # Contracts per trade
    },
}

data_config = {
    "type": "nba",
    "path": "data/recordings/game.json",
}
```

#### 2. Late-Game Blowout
Fading large leads in final minutes.

```python
adapter_config = {
    "type": "blowout",
    "params": {
        "min_point_differential": 10,         # Min point lead
        "max_time_remaining_seconds": 600,    # Last 10 min
        "base_position_size": 5.0,
        "one_trade_per_game": True,
    },
}

data_config = {
    "type": "nba",
    "path": "data/recordings/game.json",
}
```

#### 3. Total Points Over/Under
Total points projection vs market line.

```python
adapter_config = {
    "type": "total-points",
    "params": {
        "test_line": 220.0,          # Test line (optional)
        "min_edge_cents": 3.0,
        "max_period": 3,
        "position_size": 10,
        "market_noise": 0.03,        # Simulated market noise
    },
}

data_config = {
    "type": "nba",
    "path": "data/recordings/game.json",
}
```

### Crypto Strategies

#### Crypto Latency Arbitrage
Black-Scholes model vs Kalshi binary options.

```python
adapter_config = {
    "type": "crypto-latency",
    "params": {
        "vol": 0.30,                  # Annualized volatility
        "min_edge": 0.10,             # 10% minimum edge
        "slippage_cents": 3,
        "min_ttx_sec": 120,           # Min time to expiry
        "max_ttx_sec": 900,           # Max time to expiry
        "kelly_fraction": 0.5,        # Half Kelly sizing
        "max_bet_dollars": 50.0,
    },
}

data_config = {
    "type": "crypto",
    "path": "data/btc_latency_probe.db",
    "use_spot_price": True,
}
```

## Validation Metrics

### Performance Metrics

- **Sharpe Ratio**: Risk-adjusted returns (annualized)
- **Sortino Ratio**: Risk-adjusted returns (downside deviation only)
- **Calmar Ratio**: Return / max drawdown
- **Information Ratio**: Returns vs benchmark (zero)

### Statistical Tests

- **T-statistic**: Test if returns are significantly different from zero
- **P-value**: Probability of observing results by chance
- **Is Significant**: True if p < 0.05

### Risk Metrics

- **Max Drawdown %**: Largest peak-to-trough decline
- **Avg Drawdown %**: Average drawdown across all drawdown periods
- **Recovery Time**: Days to recover from max drawdown
- **VaR (95%)**: 95th percentile of losses

### Trade Metrics

- **Win Rate %**: Percentage of winning trades
- **Profit Factor**: Gross wins / gross losses
- **Avg Win**: Average winning trade PnL
- **Avg Loss**: Average losing trade PnL
- **Expectancy**: Average trade PnL

### Consistency Metrics

- **Longest Win Streak**: Max consecutive winning trades
- **Longest Lose Streak**: Max consecutive losing trades
- **% Profitable Months**: Percentage of months with positive PnL
- **% Profitable Weeks**: Percentage of weeks with positive PnL

## Walk-Forward Validation

Tests out-of-sample performance to detect overfitting.

```python
agent = BacktestRunnerAgent(
    enable_walk_forward=True,  # Enable walk-forward
)

results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

# Check walk-forward results
wf = results.walk_forward
print(f"Train Sharpe: {wf.train_sharpe:.2f}")
print(f"Test Sharpe: {wf.test_sharpe:.2f}")
print(f"Degradation: {wf.sharpe_degradation_pct:.1f}%")
print(f"Overfit: {wf.is_overfit}")
```

**Overfitting Detection:**
- If test Sharpe < 70% of train Sharpe → flagged as overfit
- Overfit score: 0-1, higher = more overfit

## Parameter Sensitivity

Tests parameter robustness by varying key parameters ±20%.

```python
agent = BacktestRunnerAgent(
    enable_sensitivity=True,  # Enable sensitivity analysis
)

results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

# Check sensitivity
for s in results.sensitivity:
    print(f"{s.parameter_name}: {s.variation_pct:+.0f}%")
    print(f"  Sharpe change: {s.sharpe_change_pct:+.1f}%")
    print(f"  Robust: {s.is_robust}")
```

**Robustness Criteria:**
- Parameter is robust if Sharpe changes < 30% with ±20% parameter variation
- Tests all numeric parameters in adapter config

## Results Database

Results are stored in SQLite for historical tracking.

```python
agent = BacktestRunnerAgent(db_path="data/backtest_results.db")
```

**Schema:**

```sql
CREATE TABLE backtest_runs (
    id INTEGER PRIMARY KEY,
    hypothesis TEXT,
    strategy_type TEXT,
    data_source TEXT,
    created_at TEXT,

    -- Core metrics
    return_pct REAL,
    sharpe_ratio REAL,
    max_drawdown_pct REAL,
    win_rate_pct REAL,
    total_trades INTEGER,

    -- Statistical validation
    t_statistic REAL,
    p_value REAL,
    is_significant INTEGER,

    -- Walk-forward
    train_sharpe REAL,
    test_sharpe REAL,
    is_overfit INTEGER,

    -- Full results JSON
    results_json TEXT
)
```

## Result Types

### BacktestResults

Main result object containing all validation metrics.

```python
@dataclass
class BacktestResults:
    backtest_result: BacktestResult       # Core backtest result
    validation: ValidationMetrics         # Statistical validation
    walk_forward: WalkForwardResults      # Train/test split (optional)
    sensitivity: List[SensitivityResult]  # Parameter tests (optional)

    hypothesis: str
    strategy_type: str
    data_source: str
    created_at: datetime

    def to_dict() -> Dict
    def summary() -> str
```

### ValidationMetrics

Comprehensive statistical metrics.

```python
@dataclass
class ValidationMetrics:
    # Returns statistics
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    information_ratio: float

    # Statistical tests
    t_statistic: float
    p_value: float
    is_significant: bool

    # Risk metrics
    max_drawdown_pct: float
    avg_drawdown_pct: float
    recovery_time_days: float
    value_at_risk_95: float

    # Performance metrics
    win_rate_pct: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    expectancy: float

    # Consistency
    longest_win_streak: int
    longest_lose_streak: int
    pct_profitable_months: float
    pct_profitable_weeks: float

    # Trade stats
    total_trades: int
    avg_holding_time_hours: float
    trade_frequency_per_day: float
```

### WalkForwardResults

Train/test split validation.

```python
@dataclass
class WalkForwardResults:
    train_result: BacktestResult
    test_result: BacktestResult

    train_sharpe: float
    test_sharpe: float
    sharpe_degradation_pct: float

    train_return_pct: float
    test_return_pct: float
    return_degradation_pct: float

    is_overfit: bool
    overfit_score: float  # 0-1
```

### SensitivityResult

Parameter variation test result.

```python
@dataclass
class SensitivityResult:
    parameter_name: str
    base_value: Any
    test_value: Any
    variation_pct: float  # +20% or -20%

    base_sharpe: float
    test_sharpe: float
    sharpe_change_pct: float

    base_return_pct: float
    test_return_pct: float
    return_change_pct: float

    is_robust: bool
```

## Example Output

```
======================================================================
  BACKTEST VALIDATION REPORT
======================================================================

Hypothesis: Early-game mispricing in NBA markets (Q1-Q2) provides edge
Strategy: nba-mispricing
Data Source: nba

--- Performance ---
  Return: +12.3%
  Sharpe Ratio: 1.85
  Max Drawdown: 8.2%
  Win Rate: 58.3%
  Profit Factor: 1.42

--- Statistical Validation ---
  T-statistic: 3.24
  P-value: 0.0012
  Significant (p<0.05): True

--- Risk Metrics ---
  Sortino Ratio: 2.14
  Calmar Ratio: 1.50
  VaR (95%): -1.23

--- Trade Analysis ---
  Total Trades: 45
  Avg Win: $2.45
  Avg Loss: -$1.73
  Expectancy: $0.54

--- Walk-Forward Validation ---
  Train Sharpe: 2.01
  Test Sharpe: 1.68
  Degradation: 16.4%
  Overfit: False
  Overfit Score: 0.16

--- Parameter Sensitivity ---
  min_edge_cents: +20% → Sharpe -12.3% (ROBUST)
  max_period: +20% → Sharpe +8.7% (ROBUST)
  position_size: +20% → Sharpe +2.1% (ROBUST)

======================================================================
```

## CLI Demo Script

```bash
# Crypto latency backtest
python3 scripts/demo_backtest_runner.py crypto

# NBA mispricing backtest
python3 scripts/demo_backtest_runner.py nba

# Late-game blowout backtest
python3 scripts/demo_backtest_runner.py blowout
```

## Integration with Research Pipeline

The backtest runner is designed to integrate with:

1. **Hypothesis Generator** - Generate hypotheses to test
2. **Data Scout** - Discover patterns in data
3. **Research Orchestrator** - Coordinate multi-hypothesis testing
4. **Report Generator** - Create research reports from results

```python
# Full pipeline example
from agents import (
    HypothesisGeneratorAgent,
    BacktestRunnerAgent,
    ReportGeneratorAgent,
)

# Generate hypothesis
hypothesis_agent = HypothesisGeneratorAgent()
hypothesis = hypothesis_agent.generate(market_type="nba")

# Test hypothesis
backtest_agent = BacktestRunnerAgent(db_path="data/results.db")
results = backtest_agent.test_hypothesis(
    hypothesis.description,
    hypothesis.adapter_config,
    hypothesis.data_config,
)

# Generate report
report_agent = ReportGeneratorAgent()
report = report_agent.create_report(results)
```

## Best Practices

1. **Always enable statistical validation** - Default behavior
2. **Use walk-forward for production strategies** - Detects overfitting
3. **Check parameter sensitivity** - Ensures robustness
4. **Store results in database** - Enables historical analysis
5. **Review p-values** - p < 0.05 indicates statistical significance
6. **Check Sharpe ratio** - Aim for > 1.5 for robust strategies
7. **Monitor drawdowns** - Max DD should be < 20% for risk management
8. **Verify profit factor** - Should be > 1.5 for good risk/reward

## Limitations

1. **Walk-forward implementation** - Current version is simplified (uses full data for both train/test as placeholder)
2. **Consistency metrics** - Streak and profitable period calculations are placeholders
3. **Holding time metrics** - Requires exit timestamp tracking (not yet implemented)
4. **Data splitting** - Proper train/test feed splitting needs implementation

## Future Enhancements

- [ ] Proper walk-forward data splitting
- [ ] Rolling window walk-forward
- [ ] Monte Carlo simulation for confidence intervals
- [ ] Bootstrapping for return distribution
- [ ] Multi-market correlation analysis
- [ ] Regime-based performance metrics
- [ ] Custom benchmark comparison
- [ ] Transaction cost modeling improvements

## See Also

- [Unified Backtest Framework](./BACKTEST_FRAMEWORK.md)
- [Strategy Adapters](./STRATEGY_ADAPTERS.md)
- [Portfolio Optimizer](./PORTFOLIO_OPTIMIZER.md)
