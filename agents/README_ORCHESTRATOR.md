# Research Orchestrator

The Research Orchestrator is an autonomous trading research pipeline that coordinates all research agents to discover, validate, and report on trading strategies.

## Architecture

The orchestrator coordinates 5 specialized agents:

1. **Data Scout Agent** - Scans databases for statistical patterns
2. **Hypothesis Generator Agent** - Creates trading hypotheses from patterns using LLM
3. **Backtest Runner Agent** - Validates hypotheses with statistical testing
4. **Report Generator Agent** - Creates comprehensive Jupyter notebooks
5. **Research Database** - Tracks all hypotheses, backtests, and reports

## Complete Research Cycle

```
┌─────────────────┐
│  Data Sources   │
│ (DBs, recordings)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Data Scout     │ ──► Patterns detected
│  Agent          │     (spread anomalies, momentum, etc.)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Hypothesis     │ ──► Trading strategies generated
│  Generator      │     (LLM-powered)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Backtest       │ ──► Statistical validation
│  Runner         │     (Sharpe, p-value, walk-forward)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Quality        │ ──► Filter by Sharpe, p-value, trades
│  Filters        │
└────────┬────────┘
         │
         ├──► Passed ──┬─► Report Generator ──► Jupyter notebook
         │             │
         │             └─► Research DB ──► Track hypothesis
         │
         └──► Failed ──► Research DB (rejected status)
```

## Installation

```bash
# Install dependencies
pip install anthropic pyyaml scipy numpy

# Or add to requirements.txt
anthropic>=0.20.0
pyyaml>=6.0
scipy>=1.9.0
numpy>=1.24.0
```

## Configuration

Edit `config/research_orchestrator.yaml`:

```yaml
# Data sources to scan
data_sources:
  - name: "BTC Latency Probe"
    type: "crypto"
    db_path: "data/btc_latency_probe.db"
    enabled: true

# Quality filters
filters:
  min_sharpe: 0.5      # Minimum Sharpe ratio
  max_pvalue: 0.05     # Maximum p-value (significance)
  min_trades: 20       # Minimum trade count
  min_return_pct: 0.0  # Minimum return %

# Notifications for exceptional findings
notifications:
  enabled: false
  min_sharpe_notify: 1.0  # Sharpe threshold for alerts
```

## Usage

### Manual Run (One Cycle)

```bash
python3 -m agents.research_orchestrator --mode manual
```

This runs one complete research cycle:
1. Scans all enabled data sources for patterns
2. Generates hypotheses from top patterns
3. Backtests each hypothesis with full validation
4. Generates reports for strategies that pass filters
5. Saves everything to research database

### Daily/Weekly Mode (Cron)

```bash
# Daily research cycle (cron)
0 2 * * * cd /path/to/tradingutils && python3 -m agents.research_orchestrator --mode daily

# Weekly research cycle
0 2 * * 0 cd /path/to/tradingutils && python3 -m agents.research_orchestrator --mode weekly
```

### Custom Filters

```bash
# Override quality filters from CLI
python3 -m agents.research_orchestrator \
  --min-sharpe 1.0 \
  --max-pvalue 0.01 \
  --min-trades 50

# Verbose logging
python3 -m agents.research_orchestrator --mode manual --verbose
```

### Custom Configuration

```bash
# Use custom config file
python3 -m agents.research_orchestrator \
  --config config/my_research_config.yaml \
  --mode manual
```

## Output

### Research Database

All results are saved to `data/research.db`:

- **hypotheses** - All generated hypotheses with status tracking
- **backtests** - Backtest results with metrics
- **reports** - Generated Jupyter notebooks
- **deployments** - Strategies approved for live trading

Query the database:

```python
from research.research_db import ResearchDB

db = ResearchDB("data/research.db")

# Get validated hypotheses
validated = [h for h in db.get_pending_hypotheses() if h.status == "validated"]

# Get backtest results
results = db.get_backtest_results(hypothesis_id=1)
```

### Research Reports

Jupyter notebooks are saved to `research/reports/`:

Each report includes:
- Executive summary with deployment recommendation
- Hypothesis description and theoretical basis
- Comprehensive metrics (Sharpe, Sortino, Calmar, etc.)
- Equity curve visualization
- Drawdown analysis
- Returns distribution
- Statistical validation (p-value, significance tests)
- Trade-by-trade analysis
- Top winners and losers

### Console Output

```
================================================================================
STARTING RESEARCH CYCLE
================================================================================

Processing data source: BTC Latency Probe
Found 12 patterns in BTC Latency Probe

--- Pattern 1/5: KXBTC-26JAN31-B73200 ---
Generated 2 hypotheses
Testing hypothesis: BTC Spread Capture Strategy
Backtest complete: Sharpe 1.23, Return +12.4%, 45 trades
Hypothesis passed filters: BTC Spread Capture Strategy
Generated report: research/reports/BTC_Spread_Capture_20260227_143052.ipynb
*** PROMISING STRATEGY FOUND: BTC Spread Capture Strategy ***

================================================================================
RESEARCH CYCLE SUMMARY
================================================================================
Duration: 145.2s
Patterns Found: 12
Hypotheses Generated: 10
Backtests Run: 10
Backtests Passed: 3
Reports Generated: 3

PROMISING STRATEGIES (1):
  - BTC Spread Capture Strategy
================================================================================
```

## Quality Filters

Hypotheses must pass ALL filters to generate a report:

1. **Sharpe Ratio** - Must be ≥ `min_sharpe` (default: 0.5)
2. **P-Value** - Must be < `max_pvalue` (default: 0.05)
3. **Trade Count** - Must have ≥ `min_trades` (default: 20)
4. **Return** - Must be ≥ `min_return_pct` (default: 0%)

Strategies that fail filters are saved to the database with status `"rejected"`.

## Notifications

For exceptional strategies (Sharpe ≥ `min_sharpe_notify`), the orchestrator sends notifications via:

- **Email** - Requires SMTP configuration (not implemented)
- **Slack** - Webhook URL in config (not implemented)

Example notification:

```
PROMISING STRATEGY DETECTED

Name: BTC Spread Capture Strategy
Market: crypto
Confidence: high

PERFORMANCE:
- Sharpe Ratio: 1.45
- Return: +18.2%
- Win Rate: 64.3%
- Max Drawdown: -8.5%

VALIDATION:
- P-value: 0.0012 ***
- Total Trades: 87
- Profit Factor: 2.34

DESCRIPTION:
Capture spread compression when BTC volatility spikes create wide spreads
on Kalshi markets, then mean-revert within 5-10 minutes.

THEORETICAL BASIS:
Market makers widen spreads during volatility spikes, but often overshoot
due to uncertainty. This creates arbitrage opportunities when spreads
compress back to fair value.
```

## Integration with Existing Backtesting

The orchestrator uses the unified backtest framework (`src/backtesting/`):

- **NBA Strategies** - `NBAMispricingAdapter`, `BlowoutAdapter`, `TotalPointsAdapter`
- **Crypto Strategies** - `CryptoLatencyAdapter`
- **Data Feeds** - `NBADataFeed`, `CryptoLatencyDataFeed`

See `src/backtesting/README.md` for details on the backtest framework.

## Development

### Adding New Data Sources

1. Add to `config/research_orchestrator.yaml`:

```yaml
data_sources:
  - name: "NFL Game Data"
    type: "nfl"
    db_path: "data/nfl_data.db"
    enabled: true
```

2. Add scout logic in `_scout_patterns()`:

```python
if source.type == "nfl":
    # Implement NFL pattern scanning
    pass
```

3. Add adapter mapping in `_hypothesis_to_adapter_config()`:

```python
if market_type == "nfl":
    return {
        "type": "nfl-mispricing",
        "params": {...}
    }
```

### Adding New Pattern Types

Extend `DataScoutAgent` in `agents/data_scout.py`:

```python
def find_orderflow_imbalance(self, ticker: str) -> List[Hypothesis]:
    """Find orderflow imbalance patterns."""
    # Implement detection logic
    pass
```

Then add to `scan_for_patterns()`:

```python
orderflow_hyps = self.find_orderflow_imbalance(ticker)
all_hypotheses.extend(orderflow_hyps)
```

### Testing Individual Components

```python
# Test data scout
from agents.data_scout import DataScoutAgent

with DataScoutAgent("data/btc_latency_probe.db") as scout:
    patterns = scout.scan_for_patterns(min_snapshots=100)
    print(f"Found {len(patterns)} patterns")

# Test hypothesis generator
from agents.hypothesis_generator import HypothesisGeneratorAgent

gen = HypothesisGeneratorAgent()
hypotheses = gen.generate_from_pattern(pattern_data, num_hypotheses=3)

# Test backtest runner
from agents.backtest_runner import BacktestRunnerAgent

runner = BacktestRunnerAgent(enable_walk_forward=True)
results = runner.test_hypothesis(hypothesis, adapter_config, data_config)

# Test report generator
from agents.report_generator import ReportGeneratorAgent

generator = ReportGeneratorAgent()
report_path = generator.generate(hypothesis_info, backtest_result)
```

## Troubleshooting

### No patterns found

- Check database has data: `sqlite3 data/btc_latency_probe.db "SELECT COUNT(*) FROM kalshi_snapshots;"`
- Lower `min_snapshots` threshold in scout
- Check data quality (non-null bids/asks)

### Backtest failures

- Check data format matches adapter expectations
- Enable `--verbose` for detailed logs
- Test adapter directly without orchestrator

### Reports not generating

- Check `research/reports/` directory exists
- Check Python packages installed: `nbformat`, `matplotlib`, `pandas`
- Check MCP research server is available

### No promising strategies

- Lower quality filters: `--min-sharpe 0.3`
- Increase pattern/hypothesis limits in config
- Check if patterns are actually exploitable

## See Also

- `agents/data_scout.py` - Pattern detection implementation
- `agents/hypothesis_generator.py` - LLM-powered hypothesis generation
- `agents/backtest_runner.py` - Statistical validation
- `agents/report_generator.py` - Report generation
- `research/research_db.py` - Database schema
- `src/backtesting/` - Unified backtest framework
