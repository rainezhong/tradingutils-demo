# Arbitrage Testing Framework

A comprehensive testing framework for arbitrage trading that provides detailed visibility into trade execution, identifies where money is being lost, and outputs reports analyzable by both humans and Claude.

## Table of Contents

- [Quick Start](#quick-start)
- [CLI Usage](#cli-usage)
  - [Demo Mode](#demo-mode)
  - [Analyze Command](#analyze-command)
  - [Report Command](#report-command)
  - [Inspect Command](#inspect-command)
- [Use Cases (Python API)](#use-cases-python-api)
  - [Running a Test Session](#use-case-1-running-a-test-session)
  - [Analyzing Historical Trades](#use-case-2-analyzing-historical-trades)
  - [Generating Reports for Review](#use-case-3-generating-reports-for-review)
  - [Real-Time Monitoring](#use-case-4-real-time-monitoring)
  - [Debugging Specific Trades](#use-case-5-debugging-specific-trades)
- [Architecture](#architecture)
  - [Component Overview](#component-overview)
  - [Data Flow](#data-flow)
  - [Key Data Structures](#key-data-structures)
- [Extension Guide](#extension-guide)
- [API Reference](#api-reference)

---

## Quick Start

```python
from pathlib import Path
from src.testing import ArbitrageTestHarness
from src.arbitrage.config import ArbitrageConfig

# 1. Create the test harness
harness = ArbitrageTestHarness(
    config=ArbitrageConfig(paper_mode=True),
    initial_capital=10000.0,
    output_dir=Path("test_results/my_test"),
)

# 2. Set up with a market data source
harness.setup(market_data_client=your_market_client)

# 3. Define opportunities to test
opportunities = [
    {
        "leg1_ticker": "MARKET-YES",
        "leg1_price": 0.45,
        "leg1_size": 10,
        "leg2_ticker": "MARKET-NO",
        "leg2_price": 0.52,
        "leg2_size": 10,
        "expected_profit": 0.05,
        "reason": "Cross-platform arbitrage",
    },
    # ... more opportunities
]

# 4. Run the test scenario
analysis = harness.run_scenario(opportunities)

# 5. Generate reports
report_paths = harness.generate_reports(analysis)
print(f"Reports saved to: {report_paths}")

# 6. Clean up
harness.teardown()
```

---

## CLI Usage

The testing framework includes a command-line interface for quick access to common operations.

### Demo Mode

Run a simulated test session to explore the framework without needing exchange connections:

```bash
# Run demo with 20 simulated trades (shows live display)
python3 -m src.testing.cli run --demo --num-trades 20

# Run quietly (no live display, just results)
python3 -m src.testing.cli run --demo --num-trades 20 --quiet

# Custom output directory and session ID
python3 -m src.testing.cli run --demo --num-trades 50 \
    --output test_results/experiment_1 \
    --session-id my_test_session

# Adjust initial capital
python3 -m src.testing.cli run --demo --num-trades 30 --capital 5000
```

Demo mode simulates realistic trade outcomes:
- ~70% successful trades (both legs fill)
- ~15% partial fills
- ~10% rollbacks (leg 2 fails, leg 1 unwound)
- ~5% complete failures

**Example output:**
```
============================================================
DEMO SESSION COMPLETE
============================================================
Session ID:    demo_20260127_192720
Total Trades:  20
  Successful:  14
  Partial:     3
  Rolled Back: 2
  Failed:      1

Win Rate:      65.0%
Total P&L:     $+12.34
Profit Factor: 1.85
Max Drawdown:  $8.50

Warnings (2):
  [HIGH] Slippage accounts for 35.2% of total losses
  [MEDIUM] 2 trades had stale quotes at execution

Reports saved to:
  markdown: test_results/demo_20260127_192720/reports/report_demo_20260127_192720.md
  json: test_results/demo_20260127_192720/reports/report_demo_20260127_192720.json
  summary: test_results/demo_20260127_192720/reports/summary_demo_20260127_192720.txt
============================================================
```

### Analyze Command

Analyze an existing journal file and display metrics:

```bash
# Basic analysis
python3 -m src.testing.cli analyze --journal test_results/session/journal.json

# Include per-trade breakdown
python3 -m src.testing.cli analyze --journal test_results/session/journal.json --verbose
```

**Example output:**
```
============================================================
SESSION ANALYSIS: demo_20260127_192720
============================================================
Duration:      45.3s
Total Trades:  50
  Successful:  35
  Partial:     8
  Rolled Back: 5
  Failed:      2

Win Rate:      68.0%
Total P&L:     $+127.50
Profit Factor: 2.31
Max Drawdown:  $45.20 (4.5%)

LOSS BREAKDOWN:
  Slippage (Leg 1): $22.10
  Slippage (Leg 2): $23.10
  Fee Variance:     $12.30
  Partial Fills:    $28.50
  Rollback Costs:   $18.30
  TOTAL:            $104.30

WARNINGS (3):
  [HIGH] Slippage accounts for 43.3% of total losses
  [MEDIUM] 5 trades had stale quotes at execution
  [MEDIUM] Partial fills account for 27.3% of losses

============================================================
```

### Report Command

Generate reports from a journal file:

```bash
# Generate all report formats
python3 -m src.testing.cli report --journal test_results/session/journal.json --format all

# Generate only markdown report
python3 -m src.testing.cli report --journal test_results/session/journal.json --format markdown

# Generate only JSON report (for Claude analysis)
python3 -m src.testing.cli report --journal test_results/session/journal.json --format json

# Print summary to console
python3 -m src.testing.cli report --journal test_results/session/journal.json --format summary

# Custom output directory
python3 -m src.testing.cli report --journal test_results/session/journal.json \
    --output reports/analysis_v2 --format all
```

### Inspect Command

Deep-dive into specific trades:

```bash
# Inspect the worst trade (most negative P&L)
python3 -m src.testing.cli inspect --journal test_results/session/journal.json --worst

# Inspect the best trade
python3 -m src.testing.cli inspect --journal test_results/session/journal.json --best

# Inspect a specific trade by ID
python3 -m src.testing.cli inspect --journal test_results/session/journal.json \
    --trade-id JOURNAL-ABC123DEF456

# Include full execution timeline
python3 -m src.testing.cli inspect --journal test_results/session/journal.json --worst --verbose
```

**Example output:**
```
======================================================================
TRADE: JOURNAL-ABC123DEF456
======================================================================
Spread ID:  SPREAD-0042
Status:     rolled_back
Duration:   2450ms

INPUT STATE:
  Leg 1: kalshi/PRES-24-DEM-YES
         bid=0.4500 ask=0.4700 age=125ms
  Leg 2: polymarket/PRES-24-DEM-YES
         bid=0.5200 ask=0.5400 age=340ms
  Expected profit: $0.35

DECISION:
  Rank: 1/3
  Edge: 5.00 cents, ROI: 3.55%
  Reason: Cross-platform presidential market

P&L BREAKDOWN:
  Expected gross: $0.50
  Expected net:   $0.35
  Actual gross:   N/A
  Actual net:     $-0.82

  Leg 1: expected=0.4700 actual=0.4720 slippage=$0.02
  Leg 2: expected=0.5200 actual=N/A slippage=$0.00
  Fee variance:     $0.03
  Partial fill:     $0.00
  Rollback loss:    $0.77
  Primary category: rollback_cost

WHAT-IF:
  Optimal profit:       $0.42
  Maker fee savings:    $0.05
  Timing loss:          $0.00
  Detection prices ok:  True

EXECUTION TIMELINE:
  +    0.0ms  detection
  +   12.3ms  decision
  +   15.1ms  leg1_submitted
  +  850.2ms  leg1_filled
              price: 0.472
              size: 10
  +  855.0ms  leg2_submitted
  + 2100.5ms  leg2_timeout
  + 2105.0ms  rollback_started
  + 2445.3ms  rollback_completed
              price: 0.395
  + 2450.1ms  completed
              status: rolled_back
```

### CLI Help

```bash
# General help
python3 -m src.testing.cli --help

# Command-specific help
python3 -m src.testing.cli run --help
python3 -m src.testing.cli analyze --help
python3 -m src.testing.cli report --help
python3 -m src.testing.cli inspect --help
```

---

## Use Cases (Python API)

### Use Case 1: Running a Test Session

**Goal:** Test your arbitrage strategy against live market data using paper trading.

```python
from pathlib import Path
from src.testing import ArbitrageTestHarness
from src.arbitrage.config import ArbitrageConfig
from src.kalshi.client import KalshiClient  # Your market data source

# Initialize with custom configuration
config = ArbitrageConfig(
    min_edge_cents=2.0,
    min_roi_pct=0.02,
    max_position_per_market=50,
)

harness = ArbitrageTestHarness(
    config=config,
    initial_capital=5000.0,
    output_dir=Path("test_results/strategy_v2"),
    enable_live_display=True,  # Show real-time metrics
)

# Connect to market data
kalshi = KalshiClient(api_key="...")
harness.setup(market_data_client=kalshi)

# Run with delays to simulate realistic timing
analysis = harness.run_scenario(
    opportunities=my_opportunities,
    delay_between_trades_ms=500,  # Half-second between trades
)

# Check results
print(f"Win Rate: {analysis.win_rate:.1%}")
print(f"Total P&L: ${analysis.total_pnl_usd:+.2f}")
print(f"Profit Factor: {analysis.profit_factor:.2f}")

# Generate all report formats
paths = harness.generate_reports(analysis)
# paths = {"markdown": Path(...), "json": Path(...), "summary": Path(...)}

harness.teardown()
```

### Use Case 2: Analyzing Historical Trades

**Goal:** Load and analyze a previous test session's journal.

```python
from pathlib import Path
from src.testing import TradeJournal, SessionAnalyzer, ReportGenerator

# Load existing journal
journal = TradeJournal.load_from_json(
    Path("test_results/run_001/journal/journal_test_20260127.json")
)

# Create analyzer
analyzer = SessionAnalyzer(journal)
analysis = analyzer.analyze()

# Examine loss breakdown
print("=== Loss Breakdown ===")
breakdown = analysis.loss_breakdown
print(f"Slippage (Leg 1): ${breakdown.slippage_leg1_usd:.2f}")
print(f"Slippage (Leg 2): ${breakdown.slippage_leg2_usd:.2f}")
print(f"Partial Fills:    ${breakdown.partial_fill_usd:.2f}")
print(f"Rollback Costs:   ${breakdown.rollback_cost_usd:.2f}")
print(f"Fee Variance:     ${breakdown.fees_exceeded_usd:.2f}")
print(f"Total Losses:     ${breakdown.total_loss_usd:.2f}")

# Find trades affected by specific loss categories
from src.testing import LossCategory

slippage_trades = analyzer.get_trades_by_loss_category(LossCategory.SLIPPAGE_LEG1)
print(f"\n{len(slippage_trades)} trades had leg 1 slippage")

# Get category impact analysis
impact = analyzer.calculate_category_impact()
for category, stats in impact.items():
    print(f"{category}: {stats['count']} trades, ${stats['total_impact']:.2f} impact")
```

### Use Case 3: Generating Reports for Review

**Goal:** Create reports for team review or Claude analysis.

```python
from pathlib import Path
from src.testing import TradeJournal, SessionAnalyzer, ReportGenerator

# Load data
journal = TradeJournal.load_from_json(Path("journal.json"))
analyzer = SessionAnalyzer(journal)
analysis = analyzer.analyze()

# Create reporter
reporter = ReportGenerator(journal, analyzer)

# Generate human-readable Markdown report
md_report = reporter.generate_markdown_report(
    analysis,
    output_path=Path("reports/session_report.md")
)

# Generate JSON report for Claude analysis
json_report = reporter.generate_json_report(
    analysis,
    output_path=Path("reports/session_report.json"),
    include_all_trades=True,  # Include full trade details
)

# Generate quick summary tables (for Slack/terminal)
summary = reporter.generate_summary_table(analysis)
print(summary)

loss_table = reporter.generate_loss_table(analysis.loss_breakdown)
print(loss_table)
```

**Example Markdown Output:**

```markdown
# Arbitrage Trading Session Report

**Session ID:** test_20260127_183000
**Duration:** 45m 30s

## Executive Summary

| Metric | Value |
|--------|-------|
| Total Trades | 50 |
| Win Rate | 68.0% |
| Total P&L | $+127.50 |
| Profit Factor | 2.31 |

## Where Money Was Lost

| Category | Amount | % of Total |
|----------|--------|------------|
| Slippage (Leg 1) | $22.10 | 17.5% |
| Slippage (Leg 2) | $23.10 | 18.3% |
| Partial Fills | $28.50 | 22.6% |
...
```

**Example JSON Output (for Claude):**

```json
{
  "summary": {
    "total_trades": 50,
    "win_rate": 0.68,
    "total_pnl_usd": 127.50,
    "profit_factor": 2.31
  },
  "loss_breakdown": {
    "slippage": {"leg1_usd": 22.10, "leg2_usd": 23.10, "total_usd": 45.20},
    "partial_fills_usd": 28.50,
    "rollback_costs_usd": 18.30
  },
  "all_trades": [
    {
      "journal_id": "JOURNAL-ABC123",
      "status": "success",
      "pnl": {"expected_net": 0.05, "actual_net": 0.03, "slippage_leg1": 0.01}
    }
  ]
}
```

### Use Case 4: Real-Time Monitoring

**Goal:** Monitor test execution with live metrics display.

```python
from src.testing import ArbitrageTestHarness, LiveMetricsDisplay

# Option 1: Built-in display (enabled by default)
harness = ArbitrageTestHarness(
    initial_capital=10000.0,
    enable_live_display=True,
)

# Option 2: Custom callbacks for external monitoring
def on_trade_complete(status, pnl):
    # Send to your monitoring system
    send_to_datadog({"status": status.value, "pnl": pnl})

harness.register_on_trade_complete(on_trade_complete)

# Option 3: Access live metrics programmatically
harness.setup(market_data_client=client)

# During execution, get current state
# (useful for dashboards or external integrations)
display = harness._live_display
metrics = display.get_current_metrics()
# {
#   "completed_trades": 23,
#   "total_pnl": 47.30,
#   "win_rate": 0.696,
#   "loss_breakdown": {...}
# }
```

**Live Display Output:**

```
+------------------------------------------------------------+
|  LIVE TRADING SESSION: test_20260127_183000                |
+------------------------------------------------------------+
| Trades: 23/50    P&L: $+47.30    Win Rate: 69.6%           |
| Current: Executing PRES-24-DEM spread (leg 1 filled)       |
+------------------------------------------------------------+
| Loss Breakdown (cumulative):                               |
|   Slippage: $12.40 (41%)  Partial: $8.20 (27%)            |
|   Fees: $5.10 (17%)  Rollback: $4.50 (15%)                |
+------------------------------------------------------------+
| [!] Warning: 3 trades had >5% slippage                     |
+------------------------------------------------------------+
```

### Use Case 5: Debugging Specific Trades

**Goal:** Deep-dive into why a specific trade lost money.

```python
from src.testing import TradeJournal

journal = TradeJournal.load_from_json(Path("journal.json"))

# Find the worst trade
losing_trades = journal.get_losing_entries()
worst = min(losing_trades, key=lambda e: e.pnl_breakdown.actual_net_profit)

print(f"=== Worst Trade: {worst.journal_id} ===")
print(f"Status: {worst.status.value}")
print(f"Duration: {worst.total_duration_ms}ms")

# Examine the input state
snapshot = worst.input_snapshot
print(f"\nInput State:")
print(f"  Leg 1: {snapshot.leg1_quote.ticker} ask={snapshot.leg1_quote.ask} (age: {snapshot.leg1_quote.age_ms}ms)")
print(f"  Leg 2: {snapshot.leg2_quote.ticker} bid={snapshot.leg2_quote.bid} (age: {snapshot.leg2_quote.age_ms}ms)")
print(f"  Expected profit: ${snapshot.expected_net_spread:.4f}")

# Examine the decision
decision = worst.decision_record
print(f"\nDecision:")
print(f"  Rank: {decision.opportunity_rank}/{decision.total_opportunities}")
print(f"  Edge: {decision.edge_cents} cents")
print(f"  Reason: {decision.decision_reason}")

# Examine P&L breakdown
pnl = worst.pnl_breakdown
print(f"\nP&L Breakdown:")
print(f"  Expected: ${pnl.expected_net_profit:.4f}")
print(f"  Actual:   ${pnl.actual_net_profit:.4f}")
print(f"  Leg 1 slippage: ${pnl.leg1_slippage_cost:.4f}")
print(f"  Leg 2 slippage: ${pnl.leg2_slippage_cost:.4f}")
print(f"  Fee variance:   ${pnl.fee_variance:.4f}")
print(f"  Primary loss category: {pnl.primary_loss_category.value}")

# Examine execution timeline
print(f"\nExecution Timeline:")
for event in worst.execution_events:
    print(f"  +{event.elapsed_ms:6.0f}ms: {event.event_type.value}")
    if event.details:
        for k, v in event.details.items():
            print(f"           {k}: {v}")

# What-if analysis
whatif = worst.what_if_analysis
print(f"\nWhat-If Analysis:")
print(f"  Optimal profit: ${whatif.optimal_profit:.4f}")
print(f"  Profit at detection prices: ${whatif.profit_at_detection_prices:.4f}")
print(f"  Maker fee savings potential: ${whatif.maker_fee_savings:.4f}")
print(f"  Timing loss (stale quotes): ${whatif.timing_loss:.4f}")
```

---

## Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     ArbitrageTestHarness                        │
│  Unified entry point for test execution                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐    ┌─────────────────┐                    │
│  │ PaperTradingClient│    │  SpreadExecutor │                    │
│  │  (simulation)    │◄───│  (execution)    │                    │
│  └────────┬────────┘    └────────┬────────┘                    │
│           │                      │                              │
│           │     Callbacks        │                              │
│           │    ┌─────────────────┘                              │
│           ▼    ▼                                                │
│  ┌─────────────────────────────────────────┐                   │
│  │             TradeJournal                 │                   │
│  │  Records execution events & P&L          │                   │
│  └────────────────┬────────────────────────┘                   │
│                   │                                             │
│                   ▼                                             │
│  ┌─────────────────────────────────────────┐                   │
│  │           SessionAnalyzer                │                   │
│  │  Aggregates metrics & identifies losses  │                   │
│  └────────────────┬────────────────────────┘                   │
│                   │                                             │
│                   ▼                                             │
│  ┌─────────────────────────────────────────┐                   │
│  │          ReportGenerator                 │                   │
│  │  Markdown + JSON output                  │                   │
│  └─────────────────────────────────────────┘                   │
│                                                                 │
│  ┌─────────────────────────────────────────┐                   │
│  │         LiveMetricsDisplay               │                   │
│  │  Real-time console output                │                   │
│  └─────────────────────────────────────────┘                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
1. DETECTION
   ┌──────────────┐
   │ Market Data  │──► Opportunity detected
   └──────────────┘

2. JOURNALING START
   ┌──────────────┐     ┌────────────────┐
   │ InputSnapshot │────►│  TradeJournal  │──► journal_id assigned
   │ DecisionRecord│     │  start_trade() │
   └──────────────┘     └────────────────┘

3. EXECUTION
   ┌──────────────┐     ┌────────────────┐     ┌────────────────┐
   │SpreadExecutor│────►│   Callbacks    │────►│  TradeJournal  │
   │execute_spread│     │ on_leg_fill    │     │ record_event() │
   └──────────────┘     │ on_rollback    │     └────────────────┘
                        └────────────────┘

4. COMPLETION
   ┌──────────────┐     ┌────────────────┐
   │   Results    │────►│  TradeJournal  │──► TradeJournalEntry saved
   │ (prices/fees)│     │complete_trade()│
   └──────────────┘     └────────────────┘

5. ANALYSIS
   ┌──────────────┐     ┌────────────────┐
   │ TradeJournal │────►│SessionAnalyzer │──► SessionAnalysis
   │   entries    │     │   analyze()    │    (metrics, warnings)
   └──────────────┘     └────────────────┘

6. REPORTING
   ┌──────────────┐     ┌────────────────┐
   │SessionAnalysis────►│ReportGenerator │──► Markdown / JSON files
   │ TradeJournal │     │generate_*()    │
   └──────────────┘     └────────────────┘
```

### Key Data Structures

#### TradeJournalEntry

The core data structure capturing everything about a trade:

```python
TradeJournalEntry
├── journal_id: str              # Unique identifier
├── spread_id: str               # Links to SpreadExecutor
├── session_id: str              # Groups trades in a session
│
├── Timing
│   ├── detected_at: datetime
│   ├── execution_started_at: datetime
│   ├── execution_completed_at: datetime
│   └── total_duration_ms: int
│
├── input_snapshot: InputSnapshot
│   ├── leg1_quote: QuoteSnapshot  # Bid/ask/size at detection
│   ├── leg2_quote: QuoteSnapshot
│   ├── expected_gross_spread: float
│   ├── expected_net_spread: float
│   ├── expected_fees: float
│   └── capital_available: float
│
├── decision_record: DecisionRecord
│   ├── opportunity_rank: int      # Rank among alternatives
│   ├── edge_cents: float
│   ├── roi_pct: float
│   ├── filters_passed: List[str]
│   └── decision_reason: str
│
├── execution_events: List[ExecutionEvent]
│   └── [{event_type, timestamp, elapsed_ms, details}, ...]
│
├── pnl_breakdown: PnLBreakdown
│   ├── expected_* / actual_*      # Gross/net profit
│   ├── leg1_slippage_cost: float
│   ├── leg2_slippage_cost: float
│   ├── fee_variance: float
│   ├── partial_fill_loss: float
│   ├── rollback_loss: float
│   └── primary_loss_category: LossCategory
│
├── what_if_analysis: WhatIfAnalysis
│   ├── optimal_profit: float
│   ├── maker_fee_savings: float
│   └── timing_loss: float
│
└── status: TradeJournalStatus
    # SUCCESS | PARTIAL | ROLLED_BACK | FAILED
```

#### Loss Categories

The framework tracks these loss categories:

| Category | Description | Typical Cause |
|----------|-------------|---------------|
| `SLIPPAGE_LEG1` | Paid more than expected on buy | Market moved, thin book |
| `SLIPPAGE_LEG2` | Received less than expected on sell | Market moved, thin book |
| `FEES_EXCEEDED` | Actual fees > calculated | Taker vs maker, gas spikes |
| `PARTIAL_FILL` | Unfilled portion lost profit | Insufficient liquidity |
| `FAILED_LEG2` | Leg 2 failed after leg 1 filled | Timeout, rejection |
| `ROLLBACK_COST` | Loss from unwinding leg 1 | Price moved during rollback |
| `TIMING_STALE_QUOTE` | Quote was stale at execution | Network latency |
| `OPPORTUNITY_CLOSED` | Market moved before execution | Slow detection/execution |

#### SessionAnalysis

Aggregated session metrics:

```python
SessionAnalysis
├── Trade Counts
│   ├── total_trades, successful_trades
│   ├── partial_trades, rolled_back_trades, failed_trades
│
├── P&L Metrics
│   ├── total_pnl_usd, gross_profit_usd, gross_loss_usd
│   ├── total_fees_usd
│   ├── average_profit_per_trade, average_loss_per_trade
│
├── Performance Metrics
│   ├── win_rate, profit_factor
│   ├── sharpe_ratio (optional)
│   ├── max_drawdown_usd, max_drawdown_pct
│
├── loss_breakdown: LossBreakdown
│   └── Per-category USD amounts
│
├── Timing Metrics
│   ├── avg/max/min_execution_time_ms
│   ├── avg/max_quote_age_ms
│   └── stale_quote_count
│
├── warnings: List[Warning]
│   └── [{level, category, message, affected_trade_ids}, ...]
│
└── Notable Trades
    ├── best_trade_id, best_trade_pnl
    └── worst_trade_id, worst_trade_pnl
```

---

## Extension Guide

### Adding a New Loss Category

1. **Add the enum value** in `models.py`:

```python
class LossCategory(Enum):
    # ... existing categories
    EXCHANGE_LATENCY = "exchange_latency"  # New category
```

2. **Update P&L calculation** in `trade_journal.py`:

```python
def _calculate_pnl_breakdown(self, ...):
    # Add detection logic
    if exchange_latency_detected:
        loss_categories.append(LossCategory.EXCHANGE_LATENCY)
        total_loss += latency_impact
```

3. **Update LossBreakdown** in `models.py`:

```python
@dataclass
class LossBreakdown:
    # ... existing fields
    exchange_latency_usd: float = 0.0

    @property
    def total_loss_usd(self) -> float:
        return (
            # ... existing
            + self.exchange_latency_usd
        )
```

4. **Update SessionAnalyzer** in `session_analyzer.py`:

```python
def _calculate_loss_breakdown(self) -> LossBreakdown:
    # Add accumulation logic
    if LossCategory.EXCHANGE_LATENCY in pnl.loss_categories:
        breakdown.exchange_latency_usd += calculated_impact
```

5. **Update reports** in `report_generator.py`:

```python
loss_items = [
    # ... existing
    ("Exchange Latency", breakdown.exchange_latency_usd),
]
```

### Adding a New Warning Type

1. **Add detection logic** in `session_analyzer.py`:

```python
def _generate_warnings(self, ...):
    # Add new warning detection
    if some_condition:
        warnings.append(Warning(
            level=WarningLevel.MEDIUM,
            category="new_category",
            message="Description of the issue",
            metric_value=measured_value,
            threshold=expected_threshold,
        ))
```

### Adding a New Report Format

1. **Create a new generator method** in `report_generator.py`:

```python
def generate_csv_report(
    self,
    analysis: SessionAnalysis,
    output_path: Optional[Path] = None,
) -> str:
    """Generate CSV report for spreadsheet analysis."""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(["journal_id", "status", "expected_pnl", "actual_pnl", ...])

    # Data rows
    for entry in self.journal.entries:
        writer.writerow([
            entry.journal_id,
            entry.status.value,
            entry.pnl_breakdown.expected_net_profit,
            entry.pnl_breakdown.actual_net_profit,
            # ...
        ])

    csv_content = output.getvalue()

    if output_path:
        with open(output_path, "w") as f:
            f.write(csv_content)

    return csv_content
```

2. **Update `generate_all_reports()`**:

```python
def generate_all_reports(self, analysis, output_dir):
    # ... existing
    csv_path = output_dir / f"report_{analysis.session_id}.csv"
    self.generate_csv_report(analysis, csv_path)
    paths["csv"] = csv_path
    return paths
```

### Integrating with External Systems

#### Sending Metrics to Datadog

```python
from datadog import statsd

harness = ArbitrageTestHarness(...)

def send_metrics(status, pnl):
    statsd.increment("arbitrage.trades", tags=[f"status:{status.value}"])
    statsd.gauge("arbitrage.pnl", pnl)

harness.register_on_trade_complete(send_metrics)
```

#### Streaming to Kafka

```python
from kafka import KafkaProducer
import json

producer = KafkaProducer(bootstrap_servers=['localhost:9092'])

def stream_trade(entry):
    producer.send(
        'arbitrage-trades',
        json.dumps(entry.to_dict()).encode()
    )

journal = harness.get_journal()
journal.register_callback("on_trade_completed", stream_trade)
```

### Using Custom Paper Trading

```python
from src.simulation.paper_trading import PaperTradingClient

class MyCustomPaperClient(PaperTradingClient):
    """Paper client with custom fill simulation."""

    def check_fills(self):
        # Custom fill logic (e.g., realistic slippage model)
        for order_id, order in self._open_orders.items():
            market = self.get_market_data(order.ticker)
            slippage = self._calculate_realistic_slippage(order, market)
            # ... apply slippage to fill
```

---

## API Reference

### ArbitrageTestHarness

```python
class ArbitrageTestHarness:
    def __init__(
        self,
        config: Optional[ArbitrageConfig] = None,
        initial_capital: float = 10000.0,
        output_dir: Optional[Path] = None,
        session_id: Optional[str] = None,
        enable_live_display: bool = True,
    ): ...

    def setup(
        self,
        market_data_client: APIClient,
        executor_config: Optional[SpreadExecutorConfig] = None,
    ) -> None: ...

    def teardown(self) -> None: ...

    def execute_trade(
        self,
        leg1_exchange: str,
        leg1_ticker: str,
        leg1_side: str,
        leg1_price: float,
        leg1_size: int,
        leg2_exchange: str,
        leg2_ticker: str,
        leg2_side: str,
        leg2_price: float,
        leg2_size: int,
        expected_profit: float = 0.0,
        opportunity_rank: int = 1,
        total_opportunities: int = 1,
        decision_reason: str = "Test execution",
    ) -> SpreadExecutionResult: ...

    def run_scenario(
        self,
        opportunities: List[Dict[str, Any]],
        delay_between_trades_ms: int = 0,
    ) -> SessionAnalysis: ...

    def generate_reports(
        self,
        analysis: Optional[SessionAnalysis] = None,
    ) -> Dict[str, Path]: ...

    def get_journal(self) -> TradeJournal: ...
    def get_paper_client(self) -> PaperTradingClient: ...

    def register_on_trade_complete(
        self,
        callback: Callable[[TradeJournalStatus, float], None],
    ) -> None: ...

    @classmethod
    def from_journal(
        cls,
        journal_path: Path,
        output_dir: Optional[Path] = None,
    ) -> "ArbitrageTestHarness": ...
```

### TradeJournal

```python
class TradeJournal:
    def __init__(
        self,
        session_id: str,
        output_dir: Optional[Path] = None,
        auto_save: bool = True,
        fee_calculator: Optional[FeeCalculator] = None,
    ): ...

    @property
    def entries(self) -> List[TradeJournalEntry]: ...

    def register_callback(
        self,
        event_type: str,  # "on_trade_started", "on_trade_completed", "on_event"
        callback: Callable,
    ) -> None: ...

    def start_trade(
        self,
        spread_id: str,
        input_snapshot: InputSnapshot,
        decision_record: DecisionRecord,
    ) -> str: ...  # Returns journal_id

    def record_event(
        self,
        spread_id: str,
        event_type: ExecutionEventType,
        details: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    def complete_trade(
        self,
        spread_id: str,
        status: TradeJournalStatus,
        leg1_actual_price: Optional[float],
        leg1_actual_size: int,
        leg2_actual_price: Optional[float],
        leg2_actual_size: int,
        actual_leg1_fee: float,
        actual_leg2_fee: float,
        rollback_loss: float = 0.0,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TradeJournalEntry: ...

    def save_all(self, filepath: Optional[Path] = None) -> Path: ...

    @classmethod
    def load_from_json(cls, filepath: Path) -> "TradeJournal": ...

    def get_entry(self, journal_id: str) -> Optional[TradeJournalEntry]: ...
    def get_entries_by_status(self, status: TradeJournalStatus) -> List[TradeJournalEntry]: ...
    def get_profitable_entries(self) -> List[TradeJournalEntry]: ...
    def get_losing_entries(self) -> List[TradeJournalEntry]: ...

    def create_executor_callbacks(self) -> Dict[str, Callable]: ...
```

### SessionAnalyzer

```python
class SessionAnalyzer:
    def __init__(self, journal: TradeJournal): ...

    def analyze(self) -> SessionAnalysis: ...

    def get_loss_breakdown_by_trade(self) -> List[Dict]: ...

    def get_trades_by_loss_category(
        self,
        category: LossCategory,
    ) -> List[TradeJournalEntry]: ...

    def calculate_category_impact(self) -> Dict[str, Dict]: ...
```

### ReportGenerator

```python
class ReportGenerator:
    def __init__(
        self,
        journal: TradeJournal,
        analyzer: Optional[SessionAnalyzer] = None,
    ): ...

    def generate_markdown_report(
        self,
        analysis: SessionAnalysis,
        output_path: Optional[Path] = None,
    ) -> str: ...

    def generate_json_report(
        self,
        analysis: SessionAnalysis,
        output_path: Optional[Path] = None,
        include_all_trades: bool = True,
    ) -> Dict[str, Any]: ...

    def generate_summary_table(self, analysis: SessionAnalysis) -> str: ...
    def generate_loss_table(self, breakdown: LossBreakdown) -> str: ...

    def generate_all_reports(
        self,
        analysis: SessionAnalysis,
        output_dir: Path,
    ) -> Dict[str, Path]: ...
```

---

## File Structure

```
src/testing/
├── __init__.py          # Public API exports
├── cli.py               # Command-line interface
├── models.py            # Data schemas (TradeJournalEntry, PnLBreakdown, etc.)
├── trade_journal.py     # Per-trade instrumentation and recording
├── session_analyzer.py  # Aggregate analysis and loss breakdown
├── report_generator.py  # Markdown/JSON report generation
├── test_harness.py      # Integrated test runner
├── live_display.py      # Real-time console metrics
└── README.md            # This documentation
```

---

## Troubleshooting

### Common Issues

**"Test harness not set up"**
- Call `harness.setup(market_data_client)` before executing trades

**Empty journal after test run**
- Ensure `auto_save=True` or call `journal.save_all()` manually
- Check output directory permissions

**Missing P&L data in analysis**
- Verify `complete_trade()` was called with actual prices
- Check that `actual_net_profit` is being calculated (both legs need prices)

**Stale quote warnings but trades still profitable**
- This is expected - warnings are informational
- High quote age may indicate latency issues worth investigating

### Getting Help

For questions or issues:
1. Check the examples in this README
2. Review the source code (well-documented)
3. Ask Claude to analyze your session JSON report
