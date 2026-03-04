# Research Tracking System

This directory contains the research tracking database and tools for managing the hypothesis-to-deployment lifecycle.

## Database Schema

The research database (`data/research.db`) tracks:

### Tables

1. **hypotheses** - Trading strategy hypotheses
   - `id`: Primary key
   - `name`: Strategy name
   - `description`: Detailed description
   - `source`: Origin (e.g., "mcp_research", "manual", "ml_generator")
   - `created_at`: Timestamp
   - `status`: "pending", "backtesting", "validated", "rejected", "deployed"
   - `metadata`: JSON object with additional context

2. **backtests** - Backtest results
   - `id`: Primary key
   - `hypothesis_id`: Foreign key to hypotheses
   - `sharpe`: Sharpe ratio
   - `max_drawdown`: Maximum drawdown (0-1)
   - `win_rate`: Win rate (0-1)
   - `p_value`: Statistical significance
   - `num_trades`: Number of trades
   - `config`: JSON object with backtest configuration
   - `created_at`: Timestamp

3. **reports** - Research reports (Jupyter notebooks)
   - `id`: Primary key
   - `hypothesis_id`: Foreign key to hypotheses
   - `backtest_id`: Optional foreign key to backtests
   - `notebook_path`: Path to notebook file
   - `recommendation`: "deploy", "reject", "needs_work"
   - `created_at`: Timestamp

4. **deployments** - Live deployments
   - `id`: Primary key
   - `hypothesis_id`: Foreign key to hypotheses
   - `deployed_at`: Timestamp
   - `status`: "active", "paused", "retired"
   - `allocation`: Capital allocation in dollars

## Usage

```python
from research.research_db import ResearchDB, Hypothesis, BacktestResult
from datetime import datetime

# Initialize database
db = ResearchDB()  # Uses data/research.db by default

# Create a hypothesis
hypothesis = Hypothesis(
    id=None,
    name="NBA Underdog Strategy",
    description="Bet on underdogs in specific game states",
    source="mcp_research",
    created_at=datetime.now(),
    status="pending",
    metadata={"sport": "nba", "market": "moneyline"}
)

h_id = db.save_hypothesis(hypothesis)

# Save backtest results
result = BacktestResult(
    id=None,
    hypothesis_id=h_id,
    sharpe=1.5,
    max_drawdown=0.15,
    win_rate=0.55,
    p_value=0.02,
    num_trades=100,
    config={"param1": 10},
    created_at=datetime.now()
)

bt_id = db.save_backtest_results(h_id, result)

# Save report
report_id = db.save_report(
    hypothesis_id=h_id,
    notebook_path="research/reports/nba_underdog.ipynb",
    recommendation="deploy",
    backtest_id=bt_id
)

# Mark as deployed
dep_id = db.mark_deployed(h_id, allocation=5000.0)

# Query pending hypotheses
pending = db.get_pending_hypotheses()

# Query active deployments
active_deps = db.get_deployments(status="active")

db.close()
```

## Context Manager

The database supports context manager protocol:

```python
with ResearchDB() as db:
    hypothesis = db.get_hypothesis(1)
    # ... work with db ...
    # Connection automatically closed on exit
```

## Testing

Run the test script to verify the database:

```bash
python3 research/test_research_db.py
```

## Files

- `research_db.py` - Database schema and helper class
- `test_research_db.py` - Test script demonstrating full lifecycle
- `reports/` - Directory for generated Jupyter notebook reports

## Integration

The research database is designed to integrate with:
- MCP research server (hypothesis generation)
- Backtest framework (result storage)
- Report generator (notebook tracking)
- Portfolio optimizer (deployment tracking)
