# Phase 0: Kalshi Sports Discovery

This guide walks you through validating which sports have latency arbitrage potential on Kalshi.

## Goal

Determine:
1. **Which sports have liquid in-play markets on Kalshi?**
2. **What is the oracle lag (ESPN → Kalshi price update)?**
3. **Is the edge profitable after fees?**

## Prerequisites

Set your Kalshi credentials:
```bash
export KALSHI_EMAIL='user@example.com'
export KALSHI_PASSWORD='your_password'
```

Install httpx if needed:
```bash
pip install httpx
```

---

## Step 1: Market Discovery (5 minutes)

**Run the discovery script** to scan all Kalshi sports series:

```bash
python3 scripts/discover_kalshi_sports.py
```

### What It Does

- Scans: NBA, NCAAB, NHL, NFL, MLB, Soccer
- Finds active markets with volume, open interest, spread
- Identifies "in-play" markets (close within 4 hours)
- Shows ticker format examples per sport
- Recommends which sport to target first

### Example Output

```
================================================================================
NHL Game Winner
================================================================================
Total Markets:        12
In-Play Markets:      3 (close within 4 hours)
Total Volume:         1,248 contracts
Total Open Interest:  3,456 contracts
Avg Spread:           4.2¢

IN-PLAY MARKETS (3):
Ticker                              TTX (hrs)  Vol      OI       Spread
--------------------------------------------------------------------------------
KXNHLGAME-26MAR01TORBOS-TOR            1.2      412      1,234    3¢
KXNHLGAME-26MAR01NYRFLA-NYR            1.5      318      892      5¢
KXNHLGAME-26MAR01CHITAM-CHI            2.1      203      567      6¢

🎯 HIGHEST PRIORITY: NHL Game Winner
   (3 in-play markets, highest liquidity)
```

### Interpretation

- **In-Play Markets > 0**: Good! Latency arb is possible
- **In-Play Markets = 0**: Skip this sport (no live trading)
- **High volume/OI**: More liquidity = easier fills
- **Low spread (<5¢)**: Better for taking positions

---

## Step 2: Lag Measurement (10-30 minutes)

**Pick the highest-priority sport from Step 1** and measure oracle lag.

### NHL Example

```bash
python3 scripts/measure_kalshi_sports_lag.py \
  --series KXNHLGAME \
  --sport hockey \
  --league nhl \
  --duration 600
```

### NFL Example

```bash
python3 scripts/measure_kalshi_sports_lag.py \
  --series KXNFLGAME \
  --sport football \
  --league nfl \
  --duration 1800
```

### Soccer Example (Premier League)

```bash
python3 scripts/measure_kalshi_sports_lag.py \
  --series KXSOCCER \
  --sport soccer \
  --league eng.1 \
  --duration 1200
```

### What It Does

1. Polls ESPN API every 2 seconds for live scores
2. Polls Kalshi REST API every 1 second for prices
3. Detects score changes on ESPN
4. Measures time until Kalshi prices react
5. Reports lag distribution and edge potential

### Example Output

```
LAG MEASUREMENT RESULTS
================================================================================

Sample Size:       14 matched score→price events
Mean Lag:          12.3 seconds
Median Lag:        11.5 seconds
Std Dev:           4.2 seconds
Min Lag:           6.1 seconds
Max Lag:           22.4 seconds

LAG DISTRIBUTION:
    0-2s:   0 (  0.0%)
    2-5s:   1 (  7.1%) ███
    5-10s:  4 ( 28.6%) ██████████████
   10-20s:  8 ( 57.1%) ████████████████████████████
   20-30s:  1 (  7.1%) ███
    30s+:   0 (  0.0%)

EDGE POTENTIAL ANALYSIS:
  Edge Assessment:  ✓✓ STRONG
  Explanation:      Strong edge. 10-20s lag (like Polymarket) is highly profitable.

RECOMMENDATIONS:
  ✓ Proceed with building latency arb for this sport
  ✓ Use ESPN API (free, 2s updates is sufficient)
  ✓ Target min edge: 3-5¢ (given 11.5s median lag)
  ✓ Execution window: ~10s after score change
```

### Interpretation

| Median Lag | Edge Potential | Action |
|------------|---------------|--------|
| **< 3s** | ❌ Very Low | Skip (edge too small) |
| **3-5s** | ⚠️ Low | Consider (needs fast execution) |
| **5-10s** | ✓ Moderate | Build it (good edge) |
| **10-20s** | ✓✓ Strong | Build it (great edge) |
| **20s+** | ✓✓✓ Very Strong | Build it (exceptional edge) |

---

## Step 3: Decision Matrix

Based on Phase 0 results, decide which sport(s) to target:

| Sport | In-Play Markets | Median Lag | Priority |
|-------|----------------|------------|----------|
| NHL | ✓ Yes | 11.5s | **HIGH** ✓ |
| NFL | ✓ Yes | 8.2s | **MEDIUM** ✓ |
| Soccer | ✓ Yes | 14.3s | **HIGH** ✓ |
| NBA | ✓ Yes | 9.1s | **HIGH** ✓ (already implemented) |
| MLB | ✗ No | N/A | Skip |
| NCAAB | ✓ Yes | 10.4s | **MEDIUM** ✓ |

### If All Sports Have < 5s Lag

The edge may be too small on Kalshi. Consider:
1. **Polymarket** - often has 10-20s oracle lag
2. **Paid data** - SportsRadar has sub-1 second updates
3. **Different markets** - total points, spreads may have different lag

### If Any Sport Has 10s+ Lag

**Proceed to Phase 1** - build the latency arb system for that sport!

---

## Common Issues

### "No in-play markets found"

- **Cause**: No live games at the time you ran the script
- **Fix**: Run during game times (evenings for US sports, afternoons for European soccer)
- **Check**: https://kalshi.com/sports to see what's live

### "No lag measurements recorded"

- **Cause**: No score changes during measurement window
- **Fix**: Run during an active game, increase `--duration`
- **Note**: Hockey/soccer have fewer scoring events than basketball

### "Kalshi API connection failed"

- **Cause**: Invalid credentials or network issue
- **Fix**: Double-check KALSHI_EMAIL and KALSHI_PASSWORD
- **Test**: Try logging into https://kalshi.com manually

---

## Next Steps

### If Phase 0 Shows Strong Edge (10s+ lag)

1. **Phase 1**: Build ESPN feed + Poisson model + scanner (~3-4 hours)
2. **Phase 2**: Build orchestrator (~3-4 hours)
3. **Phase 3**: Test live (~2-3 hours)
4. **Deploy**: AWS EC2 in us-east-1 for <5ms to Kalshi

### If Phase 0 Shows Weak Edge (< 5s lag)

Consider:
- **Polymarket** instead (longer oracle lag)
- **Different sports** (try all sports from discovery script)
- **Paid data feeds** (SportsRadar sub-1s updates)

---

## ESPN League Codes (for `--league` flag)

**Basketball:**
- `nba` - NBA
- `mens-college-basketball` - NCAAB

**Football:**
- `nfl` - NFL
- `college-football` - NCAAF

**Hockey:**
- `nhl` - NHL

**Soccer:**
- `eng.1` - English Premier League
- `usa.1` - MLS
- `esp.1` - La Liga
- `ger.1` - Bundesliga
- `ita.1` - Serie A
- `fra.1` - Ligue 1
- `uefa.champions` - Champions League

**Baseball:**
- `mlb` - MLB

---

## Questions?

- See existing NBA latency arb: `strategies/latency_arb/nba.py`
- Check latency probe framework: `core/latency_probe/`
- ESPN API docs: https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b
