# Research Agents

Autonomous agents for automated strategy research, backtesting, and deployment.

## Overview

The agents module provides intelligent automation for the full strategy research lifecycle:

1. **Data Scout Agent** - Scans market data for anomalies and patterns
2. **Backtest Runner Agent** - Executes comprehensive backtests with validation
3. **Hypothesis Generator Agent** - Uses LLM to generate testable hypotheses
4. **Report Generator Agent** - Creates publication-ready research reports
5. **Research Orchestrator** - Coordinates the full research workflow

## ReportGeneratorAgent

Generates comprehensive Jupyter notebook reports from backtest results.

### Features

- **Executive Summary**: LLM-style summary with key metrics and verdict
- **Hypothesis Documentation**: Full parameter specs and rationale
- **Performance Tables**: Core, risk, and trade metrics
- **Visualizations**:
  - Equity curve with profit/loss regions
  - Drawdown analysis with peak equity
  - Returns distribution and Q-Q plot
- **Statistical Validation**: Sharpe ratio, p-values, significance testing
- **Trade Analysis**: Top winners/losers, P&L by side
- **Deployment Recommendation**: Deploy/Paper/Reject with rationale

### Usage

```python
from agents.report_generator import ReportGeneratorAgent, HypothesisInfo
from src.backtesting.metrics import BacktestResult

# Create hypothesis info
hypothesis = HypothesisInfo(
    name="nba_underdog_momentum",
    description="Buy underdog teams with early momentum",
    market_type="NBA",
    strategy_family="momentum",
    parameters={
        "lookback_period": 10,
        "momentum_threshold": 0.15,
    },
    data_source="nba_games.db",
    time_period="2026-01-01 to 2026-02-27",
)

# Run backtest (get BacktestResult)
result = run_backtest(...)

# Generate report
agent = ReportGeneratorAgent()
report_path = agent.generate(hypothesis, result)

# Report is saved to research/reports/
print(f"Report: {report_path}")
```

### Deployment Criteria

**Production Deployment** (✅ DEPLOY):
- Sharpe ratio ≥ 1.0
- Minimum 50 trades
- Positive P&L
- Statistically significant (p < 0.05)

**Paper Trading** (📝 PAPER):
- Sharpe ratio ≥ 0.5
- Minimum 20 trades
- Positive P&L
- Statistically significant (p < 0.05)

**Rejected** (❌ REJECT):
- Does not meet paper trading criteria
- Negative P&L
- Not statistically significant

### Configuration

```python
agent = ReportGeneratorAgent(
    reports_dir=Path("research/reports"),  # Output directory
    min_sharpe_deploy=1.0,                  # Min Sharpe for production
    min_sharpe_paper=0.5,                   # Min Sharpe for paper
    min_trades_deploy=50,                   # Min trades for production
    min_trades_paper=20,                    # Min trades for paper
)
```

### Advanced Metrics

The agent automatically calculates:

- **Sharpe Ratio**: Risk-adjusted returns (annualized)
- **Sortino Ratio**: Downside risk-adjusted returns
- **Calmar Ratio**: Return / max drawdown
- **Profit Factor**: Gross profits / gross losses
- **Max Consecutive Losses**: Longest losing streak
- **P-Value**: Statistical significance (one-tailed t-test)

### Output Structure

Generated notebooks include:

1. **Setup Cell**: Auto-imports for numpy, pandas, matplotlib, scipy
2. **Executive Summary**: Markdown with verdict and key metrics
3. **Hypothesis Section**: Full strategy documentation
4. **Results Summary**: Tabular metrics (core, risk, trade)
5. **Equity Curve**: Line plot with profit/loss regions
6. **Drawdown Analysis**: Dual plot (equity + drawdown %)
7. **Returns Distribution**: Histogram + Q-Q plot
8. **Statistical Validation**: Significance tests and interpretations
9. **Trade Analysis**: Trade-by-trade details and aggregations
10. **Recommendation**: Deploy/paper/reject with next steps

### Example

See `agents/example_report_usage.py` for a complete working example:

```bash
python3 agents/example_report_usage.py
```

### Integration with MCP Research Server

The agent uses the MCP research server's `create_notebook()` function when available,
with automatic fallback to direct `nbformat` usage if the server is unavailable.

### Dependencies

- `numpy`: Numerical computations
- `scipy`: Statistical validation
- `nbformat`: Notebook creation (fallback)
- `src.backtesting`: Backtest framework
- MCP research server (optional)

## Directory Structure

```
agents/
├── __init__.py              # Package exports
├── README.md                # This file
├── data_scout.py            # Pattern detection agent
├── backtest_runner.py       # Automated backtest execution
├── hypothesis_generator.py  # LLM-powered hypothesis generation
├── report_generator.py      # Research report creation
├── orchestrator.py          # Full workflow coordination
└── example_report_usage.py  # Usage example
```

## Development

When creating new agents:

1. Add to `agents/__init__.py` exports
2. Follow the agent naming pattern: `{Name}Agent`
3. Include comprehensive docstrings
4. Add example usage in README
5. Create unit tests in `tests/agents/`

## Future Enhancements

- [ ] Multi-strategy comparison reports
- [ ] Walk-forward optimization reports
- [ ] Monte Carlo simulation reports
- [ ] Risk attribution analysis
- [ ] Live performance tracking integration
- [ ] Automated report distribution (email, Slack)
