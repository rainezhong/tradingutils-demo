# NBA Underdog Strategy - Kelly Sizing & Performance Tracking Guide

## Overview

The NBA Underdog Strategy now supports **Half Kelly sizing** and **live performance tracking by price bucket**. This guide explains how these features work and how to use them.

---

## Half Kelly Position Sizing

### What is Kelly Criterion?

The Kelly Criterion calculates the optimal bet size to maximize long-term growth given:
- Your edge (expected win rate vs implied probability)
- The odds (potential profit vs risk)

### Formula

```
Kelly % = (p × b - q) / b

where:
  p = probability of winning (from historical data)
  q = 1 - p = probability of losing
  b = net odds = (1 - price) / price
```

### Half Kelly (Safety First)

**Full Kelly can be aggressive**, risking large swings. **Half Kelly** uses 50% of the calculated Kelly size for:
- Reduced variance
- Protection against estimation errors
- More conservative growth
- Better psychological comfort

### Example Calculation

**15¢ underdog, 21.2% historical win rate:**

```python
price = 0.15
p = 0.212  # Historical win rate (15-20¢ bucket)
q = 0.788
b = (1 - 0.15) / 0.15 = 5.67  # Net odds

# Full Kelly
kelly_full = (0.212 × 5.67 - 0.788) / 5.67
           = (1.202 - 0.788) / 5.67
           = 0.073 = 7.3%

# Half Kelly
kelly_half = 0.073 × 0.5 = 3.65%

# With $1000 bankroll
bet_size = $1000 × 0.0365 = $36.50
contracts = $36.50 / $0.15 = 243 contracts
```

But we cap at `max_kelly_bet_size` (default: 100) to prevent overleveraging.

---

## Configuration

### Enable Kelly Sizing

```python
from strategies.nba_underdog_strategy import NBAUnderdogConfig

# Option 1: Use kelly preset
config = NBAUnderdogConfig.kelly(bankroll=1000.0)

# Option 2: Manual configuration
config = NBAUnderdogConfig(
    min_price_cents=10,
    max_price_cents=30,
    use_kelly_sizing=True,
    kelly_fraction=0.5,  # Half Kelly
    bankroll=1000.0,
    max_kelly_bet_size=100,  # Max contracts even if Kelly says more
)
```

### Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `use_kelly_sizing` | Enable Kelly Criterion | `False` |
| `kelly_fraction` | Fraction of Kelly (0.5 = half) | `0.5` |
| `bankroll` | Total bankroll in dollars | `1000.0` |
| `max_kelly_bet_size` | Maximum contracts per bet | `100` |

### Historical Win Rates by Bucket

The Kelly calculator uses these expected win rates from validation data:

| Bucket | Expected Win Rate | Historical Sample |
|--------|-------------------|-------------------|
| 10-15¢ | 20.6% | 34 games |
| 15-20¢ | 21.2% | 33 games |
| 25-30¢ | 32.4% | 37 games |
| 30-35¢ | 36.4% | 33 games |

**Note:** 20-25¢ bucket is excluded (negative EV).

---

## Live Performance Tracking

### What It Tracks

The strategy maintains a **real-time performance table** showing:
- Number of bets per price bucket
- Win rate (actual vs expected)
- Investment, returns, profit
- ROI by bucket
- Overall performance

### Performance Table Format

```
================================================================================
LIVE PERFORMANCE TRACKING
================================================================================
Bucket     Bets   Wins   Win Rate     Expected     Diff       Invested     Profit       ROI
------------------------------------------------------------------------------------------------
10-15¢     12     3      25.0%        20.6%        +4.4%      $14.28       $2.72        19.0%
15-20¢     18     4      22.2%        21.2%        +1.0%      $30.42       $1.58        5.2%
25-30¢     15     6      40.0%        32.4%        +7.6%      $40.50       $7.50        18.5%
30-35¢     8      3      37.5%        36.4%        +1.1%      $26.40       $3.60        13.6%
------------------------------------------------------------------------------------------------
TOTAL      53     16     30.2%        -            -          $111.60      $15.40       13.8%
================================================================================
```

### When It's Displayed

The performance table shows automatically:
1. **After each scan** (if there are settled bets)
2. **When calling `strategy.get_status()`**
3. **On demand via `strategy.performance.print_table()`**

### Recording Settlements

The strategy automatically records bets when placed, but you need to manually record settlements:

```python
# When a bet settles
strategy.performance.record_settlement(
    ticker="KXNBAGAME-26FEB22GSWLAL-GSW",
    won=True  # or False
)
```

For production use, you'd want to poll the exchange for settlement data and update automatically.

---

## Usage Examples

### Example 1: Half Kelly with $1000 Bankroll

```python
import asyncio
from strategies.nba_underdog_strategy import NBAUnderdogConfig, NBAUnderdogStrategy
from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient

async def main():
    # Initialize exchange
    exchange = KalshiExchangeClient.from_env()
    await exchange.connect()

    # Kelly config with $1000 bankroll
    config = NBAUnderdogConfig.kelly(bankroll=1000.0)

    # Create strategy
    strategy = NBAUnderdogStrategy(exchange, config, dry_run=False)

    # Run one scan
    await strategy.scan_and_bet()

    # Check status (shows performance table)
    status = strategy.get_status()
    print(status)

    await exchange.exit()

asyncio.run(main())
```

### Example 2: Half Kelly with $5000 Bankroll

```python
# Larger bankroll = larger bets
config = NBAUnderdogConfig.kelly(bankroll=5000.0)
strategy = NBAUnderdogStrategy(exchange, config)
```

**Result:** Bet sizes scale proportionally:
- $1000 bankroll, 15¢ bet → ~24 contracts
- $5000 bankroll, 15¢ bet → ~121 contracts (capped at 100)

### Example 3: Conservative Kelly (Quarter Kelly)

```python
config = NBAUnderdogConfig.kelly(bankroll=1000.0)
config.kelly_fraction = 0.25  # Even more conservative

strategy = NBAUnderdogStrategy(exchange, config)
```

---

## Testing with CLI

### Test Half Kelly Sizing

```bash
# Test with $1000 bankroll
python3 scripts/test_underdog_kelly.py 1000

# Test with $5000 bankroll
python3 scripts/test_underdog_kelly.py 5000
```

**Output shows:**
- Position size for each bet
- % of bankroll used
- Expected performance by bucket

### Compare Fixed vs Kelly

```bash
# Fixed sizing (10 contracts per bet)
python3 scripts/test_underdog_scan.py

# Kelly sizing ($1000 bankroll)
python3 scripts/test_underdog_kelly.py 1000
```

---

## Kelly Sizing Behavior

### When Kelly Returns 0

Kelly will skip a bet if:
- Edge is too small (< 1% of bankroll)
- Win rate ≤ implied probability (no edge)
- Price too high relative to edge

**Example:**
```
30¢ underdog, 32.4% expected win rate
Implied: 30%
Edge: 2.4%
Kelly might say: "Edge too small, skip"
```

### Max Bet Size Protection

Even if Kelly says bet 200 contracts, we cap at `max_kelly_bet_size` (default: 100):

```python
contracts = min(kelly_contracts, config.max_kelly_bet_size)
```

**Why?**
- Prevent overleveraging
- Reduce impact of estimation errors
- Comply with position limits
- Manage liquidity constraints

---

## Performance Tracking Details

### Data Structure

Each bet is recorded with:
```python
{
    "ticker": "KXNBAGAME-26FEB22GSWLAL-GSW",
    "price_cents": 15,
    "quantity": 24,
    "side": "YES",
    "bucket": "15-20",  # Price bucket
    "timestamp": datetime,
    "settled": False,
    "won": None  # Set when settled
}
```

### Calculating Metrics

**Per bucket:**
```python
n_bets = count of bets in bucket
wins = count of won bets
win_rate = wins / n_bets
expected_wr = historical win rate for bucket

invested = sum(quantity × price / 100)
returned = sum(quantity × 1.0 for winning bets)
profit = returned - invested
roi = (profit / invested) × 100
```

**Diff column:**
```
diff = actual_win_rate - expected_win_rate

+7.6% = beating expectations ✅
-2.1% = underperforming ❌
```

---

## Advanced: Custom Win Rates

You can override expected win rates for Kelly sizing:

```python
from strategies.nba_underdog_strategy import PerformanceTracker

# Update expected win rates based on your own analysis
PerformanceTracker.EXPECTED_WIN_RATES = {
    "10-15": 0.25,  # 25% instead of 20.6%
    "15-20": 0.23,  # 23% instead of 21.2%
    "25-30": 0.35,  # 35% instead of 32.4%
    "30-35": 0.38,  # 38% instead of 36.4%
}

# Kelly sizing will now use your custom win rates
config = NBAUnderdogConfig.kelly(bankroll=1000.0)
strategy = NBAUnderdogStrategy(exchange, config)
```

---

## Risk Management with Kelly

### Advantages

✅ **Optimal growth** - Maximizes long-term bankroll growth
✅ **Automatic scaling** - Bet sizes scale with bankroll
✅ **Edge-based** - Bigger bets when edge is higher
✅ **Adaptive** - Can update win rates as data grows

### Risks

⚠️ **Estimation error** - If win rates are wrong, Kelly overbets
⚠️ **Variance** - Even half Kelly has swings
⚠️ **Bankroll tracking** - Must accurately track bankroll
⚠️ **Correlation** - Assumes bets are independent (NBA games aren't fully independent)

### Mitigations

1. **Use Half Kelly** (or even Quarter Kelly)
2. **Cap max bet size** (already implemented)
3. **Update win rates** as you collect data
4. **Monitor performance table** - adjust if underperforming
5. **Start with small bankroll** to test

---

## Comparison: Fixed vs Kelly

### Fixed Sizing (10 contracts per bet)

**Pros:**
- Simple, predictable
- Easy to backtest
- Less sensitive to estimation errors

**Cons:**
- Not optimal for growth
- Doesn't scale with bankroll
- Same size regardless of edge

**Example:**
```
All bets: 10 contracts
Investment per bet: $1-3 (depending on price)
```

### Half Kelly Sizing

**Pros:**
- Optimal long-term growth
- Scales with bankroll
- Larger bets when edge is higher

**Cons:**
- More complex
- Sensitive to win rate estimates
- Requires accurate bankroll tracking

**Example with $1000 bankroll:**
```
15¢ bet (21.2% WR): ~24 contracts = $3.60
28¢ bet (32.4% WR): ~30 contracts = $8.40
```

---

## Recommendations

### For Beginners
```python
# Start with fixed sizing
config = NBAUnderdogConfig.moderate()
config.position_size = 5  # Small fixed size
```

### For Intermediate
```python
# Use Half Kelly with conservative bankroll
config = NBAUnderdogConfig.kelly(bankroll=500.0)
config.kelly_fraction = 0.5
config.max_kelly_bet_size = 50  # Lower cap
```

### For Advanced
```python
# Full Half Kelly with larger bankroll
config = NBAUnderdogConfig.kelly(bankroll=5000.0)
config.kelly_fraction = 0.5
config.max_kelly_bet_size = 100

# Monitor performance closely
# Adjust win rates as data accumulates
```

---

## FAQ

### Q: Why are all my bets hitting max_kelly_bet_size?

**A:** The edge is high and prices are low, so Kelly wants to bet a lot. This is actually a GOOD sign (strong edge). The cap protects you from overleveraging.

### Q: Can I use full Kelly instead of half?

**A:** Yes, set `kelly_fraction=1.0`, but **not recommended**. Full Kelly is very aggressive and can lead to large drawdowns.

### Q: How do I know if my win rate estimates are accurate?

**A:** Watch the performance table's "Diff" column. If you're consistently underperforming (negative diff), your estimates may be too optimistic.

### Q: Should I update my bankroll after wins/losses?

**A:** For pure Kelly, yes. But many prefer **fixed bankroll** (update monthly/quarterly) to reduce variance and psychological impact of short-term swings.

### Q: What if I have multiple strategies running?

**A:** Allocate separate bankrolls to each, or use fractional Kelly (like 0.25×) to leave room for other strategies.

---

## Summary

- **Half Kelly** = optimal growth with reduced risk
- **Performance tracking** = real-time feedback on actual vs expected
- **Price buckets** = different edges require different sizing
- **Start conservative** = test with small bankroll first
- **Monitor closely** = adjust if reality diverges from expectations

**The goal:** Maximize long-term profit while managing risk through mathematically optimal position sizing.
