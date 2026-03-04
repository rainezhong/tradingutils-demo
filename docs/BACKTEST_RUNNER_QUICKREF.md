# Backtest Runner Agent - Quick Reference

## Installation

```python
from agents.backtest_runner import BacktestRunnerAgent
```

## Basic Usage

```python
# Initialize
agent = BacktestRunnerAgent(
    db_path="data/backtest_results.db",  # Optional
    enable_walk_forward=True,             # Out-of-sample testing
    enable_sensitivity=True,              # Parameter robustness
)

# Test hypothesis
results = agent.test_hypothesis(
    hypothesis="Your hypothesis here",
    adapter_config={"type": "...", "params": {...}},
    data_config={"type": "...", "path": "..."},
)

# View results
print(results.summary())
```

## Strategy Configs

### NBA Mispricing
```python
adapter_config = {
    "type": "nba-mispricing",
    "params": {
        "min_edge_cents": 3.0,
        "max_period": 2,
        "position_size": 10,
    },
}
data_config = {"type": "nba", "path": "data/recordings/game.json"}
```

### Late-Game Blowout
```python
adapter_config = {
    "type": "blowout",
    "params": {
        "min_point_differential": 10,
        "max_time_remaining_seconds": 600,
        "base_position_size": 5.0,
        "one_trade_per_game": True,
    },
}
data_config = {"type": "nba", "path": "data/recordings/game.json"}
```

### Crypto Latency
```python
adapter_config = {
    "type": "crypto-latency",
    "params": {
        "vol": 0.30,
        "min_edge": 0.10,
        "slippage_cents": 3,
        "min_ttx_sec": 120,
        "max_ttx_sec": 900,
        "kelly_fraction": 0.5,
        "max_bet_dollars": 50.0,
    },
}
data_config = {
    "type": "crypto",
    "path": "data/btc_latency_probe.db",
    "use_spot_price": True,
}
```

## Key Metrics

### Access Results
```python
# Core metrics
results.backtest_result.metrics.return_pct
results.backtest_result.metrics.net_pnl
results.backtest_result.metrics.total_fills

# Validation metrics
results.validation.sharpe_ratio
results.validation.win_rate_pct
results.validation.profit_factor
results.validation.max_drawdown_pct
results.validation.p_value
results.validation.is_significant

# Walk-forward
results.walk_forward.train_sharpe
results.walk_forward.test_sharpe
results.walk_forward.is_overfit

# Sensitivity
for s in results.sensitivity:
    print(s.parameter_name, s.is_robust)
```

### Quality Thresholds
```python
# Good strategy indicators
sharpe_ratio > 1.5              # Strong risk-adjusted returns
p_value < 0.05                  # Statistically significant
max_drawdown_pct < 20.0         # Acceptable risk
profit_factor > 1.5             # Good risk/reward
win_rate_pct > 55.0             # Positive win rate

# Overfitting checks
not walk_forward.is_overfit     # Test performance holds
sharpe_degradation_pct < 30.0   # Reasonable out-of-sample degradation

# Robustness
all(s.is_robust for s in sensitivity)  # All parameters stable
```

## CLI Demo

```bash
# Test crypto latency strategy
python3 scripts/demo_backtest_runner.py crypto

# Test NBA mispricing strategy
python3 scripts/demo_backtest_runner.py nba

# Test blowout strategy
python3 scripts/demo_backtest_runner.py blowout
```

## Export Results

### To JSON
```python
import json

with open("results.json", "w") as f:
    json.dump(results.to_dict(), f, indent=2)
```

### To Database
```python
# Automatic if db_path provided
agent = BacktestRunnerAgent(db_path="data/results.db")
results = agent.test_hypothesis(...)  # Saved automatically
```

### Print Summary
```python
print(results.summary())  # Full validation report
print(results.backtest_result.summary())  # One-line summary
```

## Common Patterns

### Batch Testing
```python
hypotheses = [
    ("Hypothesis 1", config1),
    ("Hypothesis 2", config2),
    ("Hypothesis 3", config3),
]

for hypothesis, config in hypotheses:
    results = agent.test_hypothesis(hypothesis, config["adapter"], config["data"])
    print(f"{hypothesis}: Sharpe={results.validation.sharpe_ratio:.2f}")
```

### Parameter Sweep
```python
for edge in [2.0, 3.0, 4.0, 5.0]:
    adapter_config["params"]["min_edge_cents"] = edge
    results = agent.test_hypothesis(
        f"Test edge threshold: {edge}c",
        adapter_config,
        data_config,
    )
    print(f"Edge {edge}c: Sharpe={results.validation.sharpe_ratio:.2f}")
```

### Compare Strategies
```python
strategies = {
    "mispricing": mispricing_config,
    "blowout": blowout_config,
    "total_points": total_points_config,
}

best_sharpe = 0
best_strategy = None

for name, config in strategies.items():
    results = agent.test_hypothesis(f"Test {name}", config["adapter"], config["data"])
    sharpe = results.validation.sharpe_ratio
    if sharpe > best_sharpe:
        best_sharpe = sharpe
        best_strategy = name

print(f"Best strategy: {best_strategy} (Sharpe={best_sharpe:.2f})")
```

## Troubleshooting

### No trades generated
```python
# Check fills
if results.backtest_result.metrics.total_fills == 0:
    print("No trades - check strategy parameters")
    # Try lowering thresholds (min_edge, min_point_differential, etc.)
```

### Low Sharpe ratio
```python
# Check trade quality
if results.validation.sharpe_ratio < 1.0:
    print(f"Win rate: {results.validation.win_rate_pct:.1f}%")
    print(f"Profit factor: {results.validation.profit_factor:.2f}")
    print(f"Avg trade: ${results.validation.expectancy:.2f}")
    # Consider adjusting entry/exit criteria
```

### Overfitting detected
```python
if results.walk_forward.is_overfit:
    print(f"Train Sharpe: {results.walk_forward.train_sharpe:.2f}")
    print(f"Test Sharpe: {results.walk_forward.test_sharpe:.2f}")
    print(f"Degradation: {results.walk_forward.sharpe_degradation_pct:.1f}%")
    # Simplify strategy or collect more data
```

### Fragile parameters
```python
fragile = [s for s in results.sensitivity if not s.is_robust]
if fragile:
    print("Fragile parameters:")
    for s in fragile:
        print(f"  {s.parameter_name}: {s.sharpe_change_pct:+.1f}% change")
    # Consider parameter constraints or regularization
```

## Documentation

- **Full Guide**: `docs/BACKTEST_RUNNER_AGENT.md`
- **Implementation**: `docs/BACKTEST_RUNNER_SUMMARY.md`
- **Source Code**: `agents/backtest_runner.py`
- **Tests**: `tests/agents/test_backtest_runner.py`
- **Demo**: `scripts/demo_backtest_runner.py`
