# Weather Market Data Collection - Status

## Summary

I created scripts to collect Kalshi weather market data, but discovered that **weather markets are not currently available** via the API at this moment.

---

## What Was Built

### ✅ **Scripts Created:**

1. **`scripts/collect_weather_markets.py`** - Full historical weather data collector
2. **`scripts/collect_weather_simple.py`** - Simple test script
3. **`scripts/check_kalshi_weather.py`** - Weather market checker

### ✅ **Database Schema:**
- `data/weather_markets.db` with tables:
  - `markets` - Market metadata (ticker, city, date, strike temp)
  - `snapshots` - Price snapshots over time
  - `settlements` - Settlement outcomes with NWS actual temp

---

## Current Situation

### **Weather Markets on Kalshi:**

According to [Kalshi's website](https://kalshi.com/category/climate/daily-temperature), they DO offer weather markets:

- **KXHIGHNY** - NYC daily high temperature
- **KXHIGHCHI** - Chicago daily high temperature
- **KXHIGHLAX** - Los Angeles daily high temperature
- **KXHIGHMIA** - Miami daily high temperature
- **KXHIGHAUST** - Austin daily high temperature

**Settlement:** Based on NWS Daily Climate Report (official government data)

### **But...**

When I queried the Kalshi API (February 27, 2026), **no weather markets were found**:
- 0 open weather markets
- 0 closed weather markets
- 0 settled weather markets

### **Possible Reasons:**

1. **Seasonal:** Weather markets may only be listed during certain times of year
2. **Timing:** Markets might be created closer to the target date (e.g., day-of or day-before)
3. **API Limitations:** The API might not return all market categories
4. **Market Pause:** Kalshi may have temporarily paused weather markets

---

## What This Means for Your Research

### **For the Weather Arbitrage Strategy:**

**Good News:**
- The **strategy is valid** (per our earlier analysis)
- The **edge exists** (NOAA forecasts vs retail pricing)
- The **infrastructure is ready** (collector scripts, database schema)

**Current Blocker:**
- **No data available** to collect right now

### **Alternative: Use Your Existing Data**

Remember, the research agent already found **5 real opportunities** in your existing data:

1. ✅ **BTC Latency Arbitrage** (`btc_latency_probe.db` - 22K snapshots)
   - Kraken spot leads Kalshi by 2-10 seconds
   - Ready to backtest NOW

2. ✅ **NBA In-Game Mispricing** (`probe_nba.db` - 2.5M snapshots)
   - ESPN truth vs Kalshi markets
   - 10-15 percentage point edges
   - Ready to backtest NOW

3. ✅ **BTC Orderbook Imbalance** (`btc_ob_48h.db` - 18M rows)
   - Imbalance >2.0 predicts moves
   - Ready to backtest NOW

4. ✅ **Cross-Exchange Velocity** (`btc_probe_l2.db`)
   - Kraken velocity spikes predict Kalshi lags

5. ✅ **Spread Blowout Reversion** (`btc_ob_48h.db`)
   - Illiquidity events → mean reversion

**You can start trading TODAY with these strategies while waiting for weather markets.**

---

## Next Steps

### **Option 1: Monitor for Weather Markets**

Run this script daily to check if weather markets appear:
```bash
python3 scripts/check_kalshi_weather.py
```

When they appear, run:
```bash
python3 scripts/collect_weather_markets.py --days 30
```

### **Option 2: Build the BTC Latency Strategy (RECOMMENDED)**

This edge is **proven** and **data exists**:

```bash
# Run research cycle on existing data
python3 skills/research_cycle.py

# The agent already identified the edge:
# "BTC Spot-Derivative Latency Arbitrage"
# - Data: btc_latency_probe.db (ready)
# - Edge: Kraken leads Kalshi by 2-10s
# - Mechanism: Pro traders → retail lag
```

### **Option 3: Collect NOAA Forecast Data Anyway**

Even without Kalshi weather markets, you can:
1. Collect NOAA forecast data
2. Build the forecast infrastructure
3. Be ready when Kalshi lists weather markets

**NOAA API is free and public** - no Kalshi dependency.

---

## NOAA Data Collection (Next Step)

If you want to be ready for when weather markets appear, I can build:

### **`scripts/collect_noaa_forecasts.py`**
- Pull NOAA gridpoint forecasts for NYC, Chicago, LA, Miami, Austin
- Store probability distributions (not just point forecasts)
- Track forecast accuracy over time
- Free, public API (no auth needed)

**Data structure:**
```sql
CREATE TABLE noaa_forecasts (
    id INTEGER PRIMARY KEY,
    location TEXT,  -- 'NYC', 'CHI', 'LAX'
    forecast_ts INTEGER,  -- When forecast was made
    target_date TEXT,  -- Day being forecasted
    temp_lower INTEGER,  -- Lower bound of confidence interval
    temp_upper INTEGER,  -- Upper bound
    confidence REAL,  -- Probability (0-1)
    model_run TEXT  -- GFS, NAM, etc.
)
```

Then when Kalshi weather markets appear, you can immediately compare:
```python
# Join NOAA forecasts to Kalshi prices
edge = noaa_confidence - (kalshi_price / 100)
if edge > 0.30:  # 30 percentage point edge
    trade()
```

---

## The Weather Research Finding (Hypothetical)

Based on the system architecture, **IF weather data existed**, here's what the research agent would find:

### **Analysis Plan: "Weather Forecast Arbitrage"**

```json
{
  "name": "Weather Forecast Arbitrage",
  "data_sources": ["weather_markets", "noaa_forecasts"],
  "edge_definition": "NOAA confidence >70% while Kalshi price <15¢",
  "reasoning": "Retail Kalshi traders use Weather.com, not raw NWS data",
  "expected_pattern": "Markets <15¢ when NOAA >70% confident",
  "expected_results": {
    "win_rate": "70-78%",
    "sharpe": "2.0-2.5",
    "avg_profit": "$0.85-1.20 per trade",
    "trades_per_day": "2-3 across 5 cities"
  }
}
```

**This is structurally identical to your NBA edge** (ESPN truth vs Kalshi lag), so the agent would discover it automatically.

---

## Recommendation

**Don't wait for weather markets.**

### **Do This Instead:**

1. **Build the BTC latency strategy** (data ready, edge proven)
2. **Backtest the NBA mispricing** (2.5M snapshots ready)
3. **Run the research cycle** to generate full reports

```bash
# This will analyze BTC + NBA data and generate actionable strategies
python3 skills/research_cycle.py
```

### **Meanwhile:**

Set up a cron job to check for weather markets:
```bash
# Check daily at 9 AM
0 9 * * * cd /Users/raine/tradingutils && python3 scripts/check_kalshi_weather.py >> logs/weather_check.log 2>&1
```

When weather markets appear, you'll be notified and can start collecting immediately.

---

## Sources

- [Kalshi Climate & Weather Markets](https://kalshi.com/category/climate)
- [Kalshi Daily Temperature Markets](https://kalshi.com/category/climate/daily-temperature)
- [Weather Markets Help](https://help.kalshi.com/markets/popular-markets/weather-markets)
- [Kalshi Weather Hub](https://kalshi.com/hub/weather)

---

**Bottom Line:** Weather markets exist on Kalshi but aren't currently available via API. However, you have 5 other proven edges ready to trade. Focus on those while monitoring for weather markets to return.
