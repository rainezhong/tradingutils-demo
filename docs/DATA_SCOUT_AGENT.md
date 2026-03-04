# Data Scout Agent

The Data Scout Agent is an autonomous pattern detection system that scans trading databases for statistically significant patterns and anomalies.

## Overview

The agent implements four types of pattern detection:

1. **Spread Anomalies** - Detects when bid-ask spreads widen beyond 2x the average
2. **Price Movements** - Identifies significant price jumps (>2 standard deviations)
3. **Mean Reversion** - Finds prices that deviate significantly from their moving average
4. **Momentum** - Detects persistent directional price movements

## Usage

### Command Line

```bash
# Scan a database for patterns
python3 agents/data_scout.py data/btc_latency_probe.db

# Or use the default database
python3 agents/data_scout.py
```

### Programmatic Usage

```python
from agents.data_scout import DataScoutAgent

# Scan for all pattern types
with DataScoutAgent("data/btc_latency_probe.db") as agent:
    hypotheses = agent.scan_for_patterns(min_snapshots=100)

    for h in hypotheses:
        print(f"{h.pattern_type}: {h.description}")
        print(f"  Confidence: {h.confidence:.2%}")
        print(f"  Significance: {h.statistical_significance:.2f}")

# Scan for specific pattern types
with DataScoutAgent("data/btc_latency_probe.db") as agent:
    # Find spread anomalies for a specific ticker
    spread_hyps = agent.find_spread_anomalies("KXBTC15M-26FEB180130-30")

    # Find price jumps
    price_hyps = agent.find_price_movements("KXBTC15M-26FEB180130-30")

    # Find mean reversion opportunities
    reversion_hyps = agent.find_mean_reversion("KXBTC15M-26FEB180130-30")

    # Find momentum patterns
    momentum_hyps = agent.find_momentum("KXBTC15M-26FEB180130-30")
```

## Pattern Detection Methods

### Spread Anomalies

Detects when the bid-ask spread widens significantly beyond normal levels.

- **Method**: `find_spread_anomalies(ticker)`
- **Detection Criteria**: Spread > 2x average spread
- **Statistical Test**: Z-score of spread vs. historical distribution
- **Confidence Calculation**: Based on z-score magnitude

**Example Output:**
```
[SPREAD_ANOMALY] KXBTC15M-26FEB180130-30
  Spread widened to 8 cents (5.1x average). Avg spread: 1.56 cents
  Confidence: 87.12% | Significance: 6.7624 | N=1427
```

### Price Movements

Identifies sudden, significant price jumps using a rolling window approach.

- **Method**: `find_price_movements(ticker, window_size=10)`
- **Detection Criteria**: Price change > 2 standard deviations from local mean
- **Statistical Test**: Z-score of price change vs. rolling window
- **Confidence Calculation**: Based on magnitude of deviation

**Example Output:**
```
[PRICE_MOVEMENT] KXBTC15M-26FEB180215-15
  Price jumped down by 56.3 cents (116.6 std devs). From 56.8 to 0.5
  Confidence: 98.31% | Significance: 116.5521 | N=317
```

### Mean Reversion

Finds prices that have deviated significantly from their moving average, suggesting potential reversion opportunities.

- **Method**: `find_mean_reversion(ticker, lookback=50)`
- **Detection Criteria**: Price > 2 standard deviations from moving average
- **Statistical Test**: Z-score of current price vs. MA
- **Confidence Calculation**: Based on deviation magnitude

**Example Output:**
```
[MEAN_REVERSION] KXBTC15M-26FEB180145-45
  Price overbought at 40.5 cents (42.3 std devs from MA of 37.5). Potential reversion opportunity
  Confidence: 95.00% | Significance: 42.2850 | N=1416
```

### Momentum

Detects persistent directional price movements (streaks).

- **Method**: `find_momentum(ticker, min_streak=5)`
- **Detection Criteria**: N consecutive price moves in same direction
- **Statistical Test**: Streak length significance
- **Confidence Calculation**: Based on streak length and consistency

**Example Output:**
```
[MOMENTUM] TEST-MOMENTUM
  Strong up momentum: 14 consecutive moves, total change 14.0 cents (avg 1.00 per move)
  Confidence: 70.00% | Significance: 2.8000 | N=14
```

## Hypothesis Data Structure

Each detected pattern is represented as a `Hypothesis` object:

```python
@dataclass
class Hypothesis:
    pattern_type: str              # 'spread_anomaly', 'price_movement', etc.
    ticker: str                    # Market ticker
    description: str               # Human-readable description
    confidence: float              # Confidence level (0-1)
    statistical_significance: float # Z-score or other significance measure
    data_points: int               # Number of data points analyzed
    timestamp: str                 # ISO timestamp of detection
    metadata: Dict                 # Additional context-specific data
```

## Statistical Methods

The agent provides several statistical utility functions:

### Z-Score Calculation

```python
z_score = agent.calculate_z_score(value, mean, std)
```

Measures how many standard deviations a value is from the mean.

### T-Statistic Calculation

```python
t_stat = agent.calculate_t_statistic(sample_mean, pop_mean, sample_std, n)
```

Calculates the t-statistic for hypothesis testing.

## Database Schema

The agent expects a database with the following schema:

```sql
CREATE TABLE kalshi_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    ticker TEXT NOT NULL,
    yes_bid INTEGER,
    yes_ask INTEGER,
    yes_mid REAL,
    floor_strike REAL,
    close_time TEXT,
    seconds_to_close REAL,
    volume INTEGER,
    open_interest INTEGER
)
```

## Configuration

Pattern detection can be tuned via method parameters:

- `min_snapshots` (scan_for_patterns): Minimum data points required per ticker (default: 100)
- `window_size` (find_price_movements): Rolling window size for local statistics (default: 10)
- `lookback` (find_mean_reversion): Periods for moving average (default: 50)
- `min_streak` (find_momentum): Minimum consecutive moves for momentum (default: 5)

## Performance

The agent has been tested on databases with:
- 16,000+ snapshots across 14 tickers (3.8 hours of data)
- Detection time: <1 second per ticker
- Typical output: 100-500 hypotheses per database scan

Example performance on `btc_latency_probe.db`:
```
Scanning 14 tickers with >=100 snapshots...
Found 3806 hypotheses:
  mean_reversion: 2220 (avg confidence: 58.24%)
  price_movement: 829 (avg confidence: 58.83%)
  spread_anomaly: 757 (avg confidence: 73.30%)
```

## Testing

Run the standalone test suite:

```bash
python3 tests/agents/test_data_scout_standalone.py
```

This will execute 6 tests covering:
- Spread anomaly detection
- Price movement detection
- Momentum detection
- Full pattern scanning
- Statistical functions
- Hypothesis representation

## Future Enhancements

Potential improvements for the Data Scout Agent:

1. **Additional Patterns**
   - Volume anomalies
   - Volatility clustering
   - Quote stuffing detection
   - Correlated market movements

2. **Advanced Statistics**
   - Autocorrelation analysis
   - Change point detection
   - Regime switching models
   - Time series decomposition

3. **Machine Learning Integration**
   - Pattern classification
   - Anomaly detection using isolation forests
   - Clustering similar patterns
   - Predictive modeling

4. **Performance Optimization**
   - Parallel processing for multiple tickers
   - Incremental updates (avoid rescanning)
   - Caching of statistical computations
   - Database indexing recommendations

5. **Integration**
   - Save findings to research database
   - Trigger automated backtests for patterns
   - Generate hypothesis for LLM agent
   - Real-time pattern monitoring

## Related Documentation

- [Research Orchestrator](./RESEARCH_ORCHESTRATOR.md) - Coordinates multiple research agents
- [Latency Probe Framework](../LATENCY_PROBE_USAGE.md) - Data collection for analysis
- [Portfolio Optimizer](./PORTFOLIO_OPTIMIZER.md) - Position sizing based on research
