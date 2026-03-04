# Latency Probe Framework

The latency probe framework measures the reaction time between "truth" data sources (e.g., Kraken spot price, ESPN live scores) and Kalshi market updates. This helps identify potential arbitrage windows for latency-based trading strategies.

## Architecture

- **TruthSource** (ABC): Provides real-time probability readings from external data
- **ProbeRecorder**: SQLite database for storing snapshots and analysis
- **LatencyProbe**: Orchestrates Kalshi polling + truth source + recording
- **ProbeAnalyzer**: Post-hoc analysis of latency distributions

## Implementations

### 1. Crypto (BTC/ETH)

Uses Kraken WebSocket for spot prices and Black-Scholes model to calculate probability.

**Truth Source:** Kraken 60s rolling average → Black-Scholes N(d2)

**Run:**
```bash
# BTC (default)
python3 scripts/latency_probe/run.py crypto --duration 3600 --db data/probe_btc.db

# ETH
python3 scripts/latency_probe/run.py crypto --series KXETH15M --duration 3600 --db data/probe_eth.db
```

**Analysis:**
```bash
python3 scripts/latency_probe/run.py analyze --db data/probe_btc.db
```

### 2. NBA

Uses ESPN API for live game scores and normal distribution model for win probability.

**Truth Source:** ESPN NBA scoreboard (5s poll) → Win probability from score differential + time remaining

**Run:**
```bash
python3 scripts/latency_probe/run.py nba --duration 7200 --db data/probe_nba.db
```

**Options:**
- `--espn-poll-interval`: Seconds between ESPN API calls (default: 5s)
- `--poll-interval`: Seconds between Kalshi polls (default: 0.5s)

### 3. NCAAB

Uses ESPN API for live college basketball scores.

**Truth Source:** ESPN NCAAB scoreboard (5s poll) → Win probability model

**Run:**
```bash
python3 scripts/latency_probe/run.py ncaab --duration 7200 --db data/probe_ncaab.db
```

## Database Schema

All probes share core tables:

- **kalshi_snapshots**: Market state from Kalshi API (bid/ask/mid/volume/OI)
- **truth_readings**: Probability readings from truth source
- **market_settlements**: Final outcomes for verification

Each implementation adds extension tables:

- **Crypto**: `kraken_snapshots` (spot price, avg_60s, trade count)
- **Basketball**: `espn_game_states` (scores, period, clock, win probability)

## Analysis Output

The analyzer computes:
- **Latency distribution**: How long Kalshi takes to react to truth source changes
- **Accuracy**: Does Kalshi market price match truth source probability?
- **Settlement correctness**: Did markets settle to correct outcomes?
- **Arbitrage windows**: Periods where truth source diverged from Kalshi by >N%

## Win Probability Model (Basketball)

Uses normal distribution to model final score given current state:

```
P(leading team wins) = N(z)
where z = score_diff / sqrt(remaining_possessions * variance_per_possession)
```

**Assumptions:**
- Possessions per 48 min: ~100 (NBA average pace)
- Points per possession: ~1.1
- Variance per possession: ~1.0

**Example:**
- Leading by 10 with 2 min left → 99.9% win probability
- Leading by 3 with 5 min left → 85.5% win probability
- Tied with 10 min left → 50.0% win probability

## Ticker Parsing (Basketball)

Kalshi game winner tickers follow the format:

```
KXNBAGAME-{DATE}-{AWAY}-{HOME}-{H|A}
KXNCAAMBGAME-{DATE}-{AWAY}-{HOME}-{H|A}
```

Examples:
- `KXNBAGAME-26FEB22-LAL-GSW-H` → Home: GSW, Away: LAL
- `KXNCAAMBGAME-26FEB22-DUKE-UNC-A` → Home: UNC, Away: DUKE

The truth source matches Kalshi tickers to ESPN games by comparing team abbreviations (case-insensitive, partial match supported).

## Use Cases

1. **Feasibility Check**: Does Kalshi react slowly enough to create an arbitrage window?
2. **Source Comparison**: Which data source is faster? (ESPN vs NBA.com vs Sportradar)
3. **Strategy Validation**: Backtest latency arb strategy before going live
4. **Market Analysis**: Study Kalshi's pricing efficiency across different sports
