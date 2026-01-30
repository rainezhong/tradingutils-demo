# Market Selection Guide

A practical guide for using the market analysis system to identify and select trading opportunities.

## Table of Contents

1. [Quick Start](#quick-start)
2. [Understanding Market Scores](#understanding-market-scores)
3. [Trading Strategies](#trading-strategies)
4. [CLI Commands](#cli-commands)
5. [Workflow Examples](#workflow-examples)
6. [Interpreting Results](#interpreting-results)

---

## Quick Start

### View Top Markets
```bash
python main.py analyze --top 20
```

### View Markets with Strategy Labels
```bash
python main.py analyze --show-strategies
```

### Find Markets for a Specific Strategy
```bash
python main.py analyze --strategy market_making --min-suitability 6
```

---

## Understanding Market Scores

Every market receives a **score from 0-20** based on four factors:

| Factor | Max Points | What It Measures |
|--------|------------|------------------|
| Spread | 5 | Bid-ask spread percentage (wider = more profit potential) |
| Volume | 5 | 24h trading volume (higher = more liquidity) |
| Stability | 5 | Spread consistency (lower volatility = more predictable) |
| Depth | 5 | Order book depth (deeper = easier to enter/exit) |

### Score Interpretation

| Score | Rating | Recommendation |
|-------|--------|----------------|
| 16-20 | Excellent | Prime trading opportunity |
| 12-15 | Good | Solid opportunity, worth monitoring |
| 8-11 | Fair | Trade with caution |
| 0-7 | Poor | Generally avoid |

### Scoring Thresholds

**Spread Score:**
- >5% spread = 5 points
- 4-5% spread = 4 points
- 3-4% spread = 2 points
- <3% spread = 0 points

**Volume Score:**
- >5,000 = 5 points
- 2,000-5,000 = 3 points
- 1,000-2,000 = 1 point
- <1,000 = 0 points

**Stability Score (spread volatility):**
- <1.5% std dev = 5 points
- 1.5-3% std dev = 3 points
- 3-5% std dev = 1 point
- >5% std dev = 0 points

**Depth Score:**
- >100 contracts = 5 points
- 50-100 contracts = 3 points
- 20-50 contracts = 1 point
- <20 contracts = 0 points

---

## Trading Strategies

The system labels each market with applicable trading strategies and a **suitability score (0-10)**.

### Strategy Overview

| Strategy | Best For | Key Requirements |
|----------|----------|------------------|
| **Market Making** | Capturing bid-ask spread | Wide spreads, stable spreads, decent volume |
| **Spread Trading** | Profiting from spread fluctuations | Volatile spreads, enough volume to enter/exit |
| **Momentum** | Betting on price direction | Price volatility, volume trends, thinner books |
| **Scalping** | Quick small profits | Tight spreads, high volume, stable prices |
| **Arbitrage** | Cross-market price discrepancies | Correlated markets, sufficient volume in both |
| **Event Trading** | Trading near resolution | Close to expiration, volume spikes |

### Strategy Details

#### Market Making
Profit from consistently providing liquidity on both sides of the book.

**Ideal Conditions:**
- Spread >= 5% (optimal) or 3-5% (good)
- Spread volatility < 1.5% (stable)
- Volume >= 2,000
- Depth >= 50 contracts

**Risks:** Getting picked off when prices move quickly

#### Spread Trading
Capture value when spreads widen or narrow from their typical range.

**Ideal Conditions:**
- Spread volatility >= 4% (spreads move around)
- Volume >= 2,000 (can enter/exit)
- Base spread >= 2%

**Risks:** Spreads may not revert; timing is difficult

#### Momentum
Trade in the direction of price movement.

**Ideal Conditions:**
- Price volatility >= 8 cents
- Rising volume trend (>+50)
- Thinner order books (<50 depth)

**Risks:** Reversals, slippage on thin books

#### Scalping
Make many small, quick trades for small profits.

**Ideal Conditions:**
- Tight spreads (<= 2%)
- High volume (>= 5,000)
- Stable prices (volatility <= 3 cents)
- Deep books (>= 100 contracts)

**Risks:** Transaction costs, requires constant monitoring

#### Arbitrage
Exploit price differences between correlated markets.

**Ideal Conditions:**
- Correlated market exists
- Volume >= 2,000 in both markets
- Spreads <= 3%

**Risks:** Correlations can break down; execution timing

#### Event Trading
Trade markets approaching their resolution date.

**Ideal Conditions:**
- Closes within 3 days (optimal) or 7 days (good)
- Volume trend spiking (>+100)
- Price near extremes (<10 or >90 cents)

**Risks:** Increased volatility; information disadvantage

---

## CLI Commands

### Basic Analysis

```bash
# View top 10 markets with score >= 12
python main.py analyze

# View top 20 markets
python main.py analyze --top 20

# Lower the minimum score threshold
python main.py analyze --min-score 8

# Analyze with more historical data (7 days instead of 3)
python main.py analyze --days 7
```

### Strategy-Based Selection

```bash
# Find markets suitable for market making
python main.py analyze --strategy market_making

# Find scalping opportunities with high suitability
python main.py analyze --strategy scalping --min-suitability 7

# Show strategy labels for all top markets
python main.py analyze --show-strategies

# All available strategies:
#   market_making, spread_trading, momentum,
#   scalping, arbitrage, event_trading
```

### Exporting Data

```bash
# Export to CSV for further analysis
python main.py analyze --export rankings.csv

# Export with strategy data
python main.py analyze --export rankings.csv --show-strategies
```

### Data Collection

```bash
# Scan for new markets
python main.py scan

# Log current snapshots for all tracked markets
python main.py log

# Run full data pipeline (scan + log + analyze)
python main.py pipeline
```

---

## Workflow Examples

### Daily Market Selection Workflow

1. **Update data:**
   ```bash
   python main.py pipeline
   ```

2. **Review top opportunities:**
   ```bash
   python main.py analyze --top 20 --show-strategies
   ```

3. **Drill into your preferred strategy:**
   ```bash
   python main.py analyze --strategy market_making --min-suitability 6
   ```

4. **Export for tracking:**
   ```bash
   python main.py analyze --export daily_picks.csv --show-strategies
   ```

### Finding Market Making Opportunities

```bash
# Get markets ranked by market making suitability
python main.py analyze --strategy market_making --min-suitability 7

# Look for:
# - Suitability >= 7
# - Spread% between 4-8%
# - Volume > 2000
```

**What to check manually:**
- Is the market topic something you understand?
- Are there upcoming events that could cause volatility?
- Check the actual order book depth on the exchange

### Finding Scalping Opportunities

```bash
python main.py analyze --strategy scalping --min-suitability 7
```

**What to check manually:**
- Can you monitor this market continuously?
- Are transaction costs acceptable given the tight spreads?
- Is there enough volume to get fills quickly?

### Finding Event Trading Opportunities

```bash
python main.py analyze --strategy event_trading --min-suitability 6
```

**What to check manually:**
- When exactly does the market close?
- What event will resolve it?
- Do you have an edge on the outcome?

---

## Interpreting Results

### Sample Output

```
=== Market Analysis ===

Top 10 Markets (score >= 12.0):

------------------------------------------------------------------------------------------
Rank  Ticker                    Score    Spread%    Volume       Best Strategy        Suit.
------------------------------------------------------------------------------------------
1     FED-RATE-DEC              18.0     5.50       8500         market_making        9.5
2     BTC-100K-2024             16.0     4.80       6200         spread_trading       8.0
3     ELECTION-2024-WINNER      15.0     4.20       5800         event_trading        7.5
4     GDP-Q4-GROWTH             14.0     3.90       4100         market_making        7.0
------------------------------------------------------------------------------------------
```

### Reading the Columns

| Column | Meaning |
|--------|---------|
| Rank | Position by overall score |
| Ticker | Market identifier |
| Score | Overall score (0-20) |
| Spread% | Average bid-ask spread percentage |
| Volume | Average 24h trading volume |
| Best Strategy | Highest-scoring strategy for this market |
| Suit. | Suitability score for that strategy (0-10) |

### Red Flags to Watch For

- **Low snapshot count:** Not enough data for reliable metrics
- **High spread volatility with market making:** Spreads may be unstable
- **Low volume with any strategy:** Difficulty entering/exiting positions
- **Score dropped significantly:** Market conditions may have changed

---

## Database Schema Reference

The system tracks two main data types:

### Markets Table
- `ticker` - Unique market identifier
- `title` - Market description/question
- `category` - Market category
- `close_time` - When the market resolves
- `status` - Current status (open/closed)

### Snapshots Table
- `ticker` - Market identifier
- `timestamp` - When snapshot was taken
- `yes_bid` / `yes_ask` - Current prices (0-100 cents)
- `spread_cents` / `spread_pct` - Bid-ask spread
- `mid_price` - Midpoint price
- `volume_24h` - 24-hour volume
- `orderbook_bid_depth` / `orderbook_ask_depth` - Order book depth

### Calculated Metrics

When you run analysis, these metrics are calculated from snapshot history:

| Metric | Description |
|--------|-------------|
| `avg_spread_pct` | Mean spread over analysis period |
| `spread_volatility` | Standard deviation of spread |
| `avg_volume` | Mean 24h volume |
| `volume_trend` | Slope of volume over time (+ = increasing) |
| `price_volatility` | Standard deviation of mid-price |
| `price_range` | (min_price, max_price) observed |
| `avg_depth` | Mean total order book depth |

---

## Tips for Success

1. **Don't rely solely on scores** - Use them as a starting point, then do your own research

2. **Match strategy to your style** - If you can't monitor constantly, avoid scalping

3. **Understand the market** - High scores mean nothing if you don't understand what you're trading

4. **Watch for regime changes** - A market's characteristics can change; re-analyze regularly

5. **Consider transaction costs** - Tighter spreads mean more competition from other traders

6. **Diversify strategies** - Don't put all capital into one strategy or market

7. **Track your results** - Export data and compare your actual returns to predicted suitability

---

## Troubleshooting

### "No markets meet the criteria"
- Lower `--min-score` threshold
- Run `python main.py scan` to discover new markets
- Run `python main.py log` to capture fresh data

### Stale data
- Check when snapshots were last logged: data may be outdated
- Run `python main.py pipeline` to refresh everything

### Strategy not appearing for a market
- The market may not meet minimum thresholds for that strategy
- Some strategies require specific conditions (arbitrage needs correlated markets, event trading needs close_time)

### Need more history
- Increase `--days` parameter (default is 3)
- More historical data = more reliable metrics
