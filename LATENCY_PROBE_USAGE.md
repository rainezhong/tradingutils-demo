# NBA/NCAAB Latency Probe - Quick Start

## What It Does

The latency probe measures reaction time between ESPN live scores and Kalshi market updates to identify potential arbitrage windows.

**Key Question:** Can you get game score updates from ESPN faster than Kalshi updates their markets?

## Running the Probe

### NBA

```bash
# Run during live NBA games (2 hour default)
python3 scripts/latency_probe/run.py nba --duration 7200 --db data/probe_nba.db

# Custom polling intervals
python3 scripts/latency_probe/run.py nba \
    --espn-poll-interval 3.0 \    # Poll ESPN every 3s
    --poll-interval 0.5 \          # Poll Kalshi every 0.5s
    --db data/probe_nba.db
```

### NCAAB

```bash
# Run during live college basketball games
python3 scripts/latency_probe/run.py ncaab --duration 7200 --db data/probe_ncaab.db
```

## What Gets Recorded

1. **Kalshi Market Snapshots** (every 0.5s)
   - Bid/ask prices
   - Market volume and open interest
   - Time until market closes

2. **ESPN Game States** (every 5s)
   - Live scores
   - Period/quarter
   - Time remaining
   - Calculated win probability

3. **Truth Readings**
   - Win probability from score model
   - Score differential
   - Game metadata

## Analysis

```bash
python3 scripts/latency_probe/run.py analyze --db data/probe_nba.db
```

**Output includes:**
- Latency distribution (how long Kalshi takes to react)
- Accuracy comparison (Kalshi price vs ESPN-based probability)
- Settlement correctness
- Potential arbitrage windows

## Example Scenario

**Live game: Lakers @ Warriors**

```
Time: Q4 5:00 remaining
Score: LAL 98, GSW 102 (GSW +4)

ESPN Truth:
  - Win prob: GSW 71.3% (based on +4 lead with 5 min left)

Kalshi Market (KXNBAGAME-26FEB22-LAL-GSW-H):
  - Yes (GSW wins): 68¢ bid / 70¢ ask
  - Implied prob: 69%

Latency Window:
  - If ESPN updates score first (GSW +5), truth prob → 75%
  - If Kalshi still shows 69%, there's a 6% edge
  - Can you get the order in before Kalshi updates?
```

## Interpreting Results

### Good Signs for Latency Arb:
✅ ESPN consistently updates 2-5+ seconds before Kalshi
✅ Large score swings create 10%+ probability jumps
✅ Kalshi markets are liquid enough to fill orders

### Bad Signs:
❌ Kalshi updates simultaneously with or faster than ESPN
❌ Probability changes are too small (<5% edge)
❌ Markets are illiquid (wide spreads, can't get filled)

## Next Steps

If the probe shows a consistent latency advantage:

1. **Validate the source:** Is ESPN the same data Kalshi uses?
2. **Test order execution:** Can you actually get filled in the window?
3. **Calculate costs:** Fees + spread + slippage vs edge
4. **Build strategy:** Automate the NBA latency arb strategy

If no latency advantage:

1. **Try different sources:** Check if NBA.com or other APIs are faster
2. **Check other sports:** Maybe NCAAB or NFL have different dynamics
3. **Accept reality:** Professional operators likely have better feeds (Sportradar, etc.)

## Technical Notes

**Win Probability Model:**
```
P(leading team wins) = N(z)
where:
  z = score_diff / sqrt(remaining_possessions * variance)
  remaining_possessions = (time_sec / 48min) * 100 possessions
```

**ESPN API:**
- NBA: `site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard`
- NCAAB: `site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard`
- Public, no authentication required
- Returns JSON with all live games

**Kalshi Ticker Format:**
```
KXNBAGAME-{DATE}-{AWAY}-{HOME}-{H|A}
Example: KXNBAGAME-26FEB22-LAL-GSW-H
         (Lakers @ Warriors, bet on Home team Warriors)
```

## Comparison to Crypto Latency Probe

| Feature | Crypto (BTC) | Basketball (NBA/NCAAB) |
|---------|--------------|------------------------|
| Data Source | Kraken WebSocket | ESPN API (polling) |
| Update Speed | Sub-second (push) | 5 seconds (poll) |
| Truth Model | Black-Scholes | Normal distribution |
| Market Type | Binary options | Game winner |
| Viability | Proven edge exists | Unknown (testing now) |

The crypto probe has shown that latency arb can work. The question is whether basketball has the same dynamics.
