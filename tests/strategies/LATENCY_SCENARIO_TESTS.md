# Latency Strategy Scenario Tests

## Overview

This test suite evaluates how latency arbitrage strategies react to different market behaviors. Instead of testing individual components in isolation, these tests simulate realistic market scenarios to identify potential improvements and weaknesses in strategy logic.

## Test Philosophy

Traditional unit tests verify that code works as written. **Scenario tests** verify that the strategy **behaves intelligently** across different market conditions. They answer questions like:

- Does the strategy avoid adverse selection in oscillating markets?
- Does it properly size positions based on edge magnitude?
- Does early exit protection save us from reversals?
- Does signal stability filtering prevent whipsaw trades?

## Market Scenarios

### 1. **Market Follows Trend** (`MarketFollowsTrend`)
**Description**: External price moves, Kalshi follows quickly (200ms lag)
**Expected Behavior**: ✅ GOOD - Should enter and profit
**Strategy Requirements**:
- Detect edge during lag window
- Enter before market catches up
- Hold through small adverse moves

**Example**:
```
t=0.0: Fair=50%, Kalshi bid=48
t=1.0: Fair→65% (external move)
t=1.2: Kalshi bid→63 (follows)
Result: Buy at 52, exit at 63 = +11 cents profit
```

### 2. **Market Oscillates** (`MarketOscillates`)
**Description**: Price bounces rapidly between two levels
**Expected Behavior**: ⚠️ BAD - Adverse selection risk
**Strategy Requirements**:
- **Signal stability filter should prevent entry**
- If entered, early exit on first profitable move
- Recognize unstable price action

**Example**:
```
t=0.0: bid=55
t=0.5: bid=41
t=1.0: bid=55
t=1.5: bid=41
Result: Buy at 55, market drops to 41 = -14 cents loss (without stability filter)
```

### 3. **Market Ignores External** (`MarketIgnoresExternal`)
**Description**: External price moves but Kalshi stays flat
**Expected Behavior**: ⏸️ NO OPPORTUNITY
**Strategy Requirements**:
- Detect edge (fair value divergence)
- Recognize market isn't responding
- Avoid entering illiquid/stale markets

**Example**:
```
t=0.0: Fair=50%, Kalshi=50
t=1.0: Fair→70%, Kalshi=50 (ignores)
Result: 20% edge detected, but market won't move → no execution or hold forever
```

### 4. **Market Goes Opposite** (`MarketGoesOpposite`)
**Description**: Kalshi moves opposite to external signal
**Expected Behavior**: ❌ VERY BAD - Should lose (or avoid)
**Strategy Requirements**:
- Ideally detect this is happening
- Stop-loss or early exit protection
- Post-trade analysis: why did this happen?

**Example**:
```
t=1.0: Fair→70% (BUY signal)
t=1.2: Kalshi bid→35 (market goes DOWN)
Result: Buy at 52, market at 35 = -17 cents loss
```

### 5. **Market Overshoots** (`MarketOvershoots`)
**Description**: Kalshi moves MORE than fair value suggests
**Expected Behavior**: 🔄 MEAN REVERSION opportunity
**Strategy Requirements**:
- Detect overshoot (market > fair value)
- Consider reverse edge (sell signal)
- Early exit before mean reversion

**Example**:
```
t=1.0: Fair→60%
t=1.2: Kalshi→75% (overshoot)
Result: Initial BUY edge, then SELL edge appears
```

### 6. **Market Delayed Follow** (`MarketDelayedFollow`)
**Description**: Market follows but with 3 second lag
**Expected Behavior**: ⏱️ TIMING CRITICAL
**Strategy Requirements**:
- Enter during lag window
- Hold through lag period
- Exit when market catches up

**Example**:
```
t=1.0: Fair→65%, Kalshi=50
t=4.0: Kalshi→65 (3s lag)
Result: Enter at t=1, exit at t=4 when converged
```

### 7. **Market Sudden Reversal** (`MarketSuddenReversal`)
**Description**: Market initially follows then reverses sharply
**Expected Behavior**: 🔪 WHIPSAW risk
**Strategy Requirements**:
- **Early exit should save us**
- Exit on partial profit before reversal
- Recognize when "truth source" was wrong

**Example**:
```
t=1.0: Fair→65%, enter long
t=1.5: Kalshi→65 (follows, small profit)
t=2.0: Fair→45% (reversal!), Kalshi→45
Result: Early exit at +3 cents vs. final -5 cents
```

### 8. **Market Low Liquidity** (`MarketLowLiquidity`)
**Description**: Wide spreads (40-60), low depth
**Expected Behavior**: ⚠️ EXECUTION RISK
**Strategy Requirements**:
- Detect wide spreads
- Account for slippage
- Filter illiquid markets

**Example**:
```
Spread: bid=40, ask=60 (20 cent spread!)
Fair=55%, theoretical edge=15%
Actual edge after spread/slippage: ~5%
```

## Running Tests

### Run All Scenarios
```bash
python3 -m pytest tests/strategies/test_latency_scenarios.py -v
```

### Run Specific Scenario
```bash
python3 -m pytest tests/strategies/test_latency_scenarios.py::TestLatencyScenarios::test_market_follows_trend_profitable -v
```

### Run Comparative Analysis
```bash
python3 -m pytest tests/strategies/test_latency_scenarios.py::TestStrategyComparison::test_config_variations_across_scenarios -v -s
```

The `-s` flag shows the comparative output table:
```
=== Scenario: follows_trend ===
aggressive      | Opps:  12 | Exec:  3 | PnL:   +45 cents
conservative    | Opps:   3 | Exec:  1 | PnL:   +22 cents
balanced        | Opps:   7 | Exec:  2 | PnL:   +38 cents
```

## Using Tests for Strategy Improvement

### 1. **Identify Weak Scenarios**

Run all tests and look for failures or poor performance:
```bash
python3 -m pytest tests/strategies/test_latency_scenarios.py --tb=short
```

If `test_market_oscillates_adverse_selection` fails, it means the strategy isn't properly filtering oscillating markets.

### 2. **Test Configuration Changes**

Modify a config parameter and re-run scenarios:

```python
# Test: Does increasing stability duration help with oscillations?
config = LatencyArbConfig(
    signal_stability_enabled=True,
    signal_stability_duration_sec=3.0,  # Increased from 1.0
)

sim = MarketOscillates(ticker="TEST")
result = simulate_strategy_response(sim, config)
print(f"Entries: {result['num_executions']}")  # Should decrease
```

### 3. **Add New Scenarios**

If you observe a specific market behavior in live trading, add it as a test:

```python
class MarketFlashCrash(MockMarketSimulator):
    """Market drops 30% instantly then recovers."""

    def step(self, elapsed_sec: float):
        if elapsed_sec == 1.0:
            # Flash crash
            self.current_bid = 20
            self.current_ask = 24
        elif elapsed_sec >= 2.0:
            # Recovery
            self.current_bid = 48
            self.current_ask = 52
```

### 4. **Compare Strategies**

Test different strategy variants:

```python
# Strategy A: Aggressive (high frequency, low edge threshold)
config_a = LatencyArbConfig(min_edge_pct=0.03, ...)

# Strategy B: Conservative (low frequency, high edge threshold)
config_b = LatencyArbConfig(min_edge_pct=0.20, ...)

# Run both against all scenarios
for scenario in [MarketFollowsTrend, MarketOscillates, ...]:
    result_a = simulate_strategy_response(scenario(), config_a)
    result_b = simulate_strategy_response(scenario(), config_b)
    # Compare P&L, Sharpe ratio, max drawdown, etc.
```

### 5. **Regression Testing**

After making changes to edge detection or execution logic, re-run scenarios:

```bash
# Before changes
python3 -m pytest tests/strategies/test_latency_scenarios.py > results_before.txt

# Make changes to detector.py or executor.py

# After changes
python3 -m pytest tests/strategies/test_latency_scenarios.py > results_after.txt

# Compare
diff results_before.txt results_after.txt
```

## Metrics to Track

For each scenario, the test returns:
- **Opportunities Detected**: How many times edge exceeded threshold
- **Executions**: How many trades entered
- **Final P&L**: Total profit/loss in cents
- **Position Status**: Still in trade vs. exited

Key ratios:
- **Entry Rate**: executions / opportunities (should be <100% due to filters)
- **Win Rate**: (profitable scenarios) / (total scenarios)
- **Profit Factor**: (total profits) / (total losses)

## Extending the Test Suite

### Add a New Scenario

1. Create a new `MockMarketSimulator` subclass
2. Implement `step(elapsed_sec)` to define market behavior
3. Add a test method that runs `simulate_strategy_response`
4. Define expected behavior in docstring

### Add New Metrics

Modify `simulate_strategy_response` to track:
- Max drawdown during trade
- Time to exit
- Number of early exits
- Fill slippage
- Etc.

### Test New Strategy Features

Example: Testing a new "momentum filter":

```python
class MarketWithMomentum(MockMarketSimulator):
    """Market has strong directional momentum."""

    def step(self, elapsed_sec: float):
        # Continuous upward drift
        self.current_fair += 0.01 * elapsed_sec
        self.current_bid = int(self.current_fair * 100) - 2
        self.current_ask = int(self.current_fair * 100) + 2

def test_momentum_filter():
    """Momentum filter should prefer trending markets."""
    config = LatencyArbConfig(
        momentum_filter_enabled=True,  # New feature
        min_momentum_score=0.5,
    )

    sim = MarketWithMomentum(ticker="TEST-MOMENTUM")
    result = simulate_strategy_response(sim, config)

    # Should detect momentum and enter
    assert result["num_executions"] > 0
```

## Debugging Scenario Failures

### Enable Detailed Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)

result = simulate_strategy_response(sim, config)
```

### Inspect Snapshots

```python
result = simulate_strategy_response(sim, config)

# Print all market snapshots
for snap in sim.snapshots:
    print(f"t={snap.timestamp:.1f}: bid={snap.bid}, ask={snap.ask}")

# Print all external moves
for move in sim.external_moves:
    print(f"t={move.timestamp:.1f}: fair={move.fair_value:.2%}")

# Print all executions
for t, side, price, size in result["executions"]:
    print(f"t={t:.1f}: {side} {size}@{price}¢")
```

### Compare Expected vs. Actual

```python
def test_market_follows_trend_debug():
    config = LatencyArbConfig(min_edge_pct=0.10)
    sim = MarketFollowsTrend(ticker="TEST")
    result = simulate_strategy_response(sim, config)

    # Expected: should enter around t=1.0-1.2
    entries = [e for e in result["executions"] if not e[1].startswith("exit")]
    assert len(entries) > 0, "No entries detected!"

    entry_time = entries[0][0]
    assert 0.9 <= entry_time <= 1.5, f"Entry at t={entry_time}, expected ~1.0-1.2"

    # Expected: should profit
    assert result["final_pnl_cents"] > 0, f"Lost {-result['final_pnl_cents']} cents!"
```

## Performance Benchmarks

Target performance by scenario (for balanced config):

| Scenario | Entry Rate | Win Rate | Avg P&L |
|----------|-----------|----------|---------|
| Follows Trend | 80%+ | 90%+ | +10¢+ |
| Oscillates | <20% | N/A | Skip |
| Ignores | 0-50% | <50% | ~0¢ |
| Goes Opposite | <50% | <30% | -5¢ |
| Overshoots | 50%+ | 60%+ | +5¢ |
| Delayed Follow | 80%+ | 80%+ | +8¢ |
| Sudden Reversal | 50%+ | 50%+ | 0¢ (early exit saves us) |
| Low Liquidity | <30% | N/A | Skip |

## Common Issues and Fixes

### Issue: Strategy enters every opportunity
**Symptom**: Entry rate = 100% across all scenarios
**Diagnosis**: Filters not working (stability, liquidity, slippage)
**Fix**: Enable `signal_stability_enabled=True`, increase `min_edge_pct`

### Issue: Strategy never enters
**Symptom**: Entry rate = 0% even in "Follows Trend"
**Diagnosis**: Thresholds too strict, Kelly says don't bet
**Fix**: Lower `min_edge_pct`, check Kelly calculation, set `kelly_fraction=0`

### Issue: Loses money on "Follows Trend"
**Symptom**: Should profit but getting negative P&L
**Diagnosis**: Slippage model wrong, entry timing off
**Fix**: Check `expected_slippage_cents`, verify entry price calculation

### Issue: Gets whipsawed on "Oscillates"
**Symptom**: Enters oscillating market, loses money
**Diagnosis**: Signal stability filter not working
**Fix**: Increase `signal_stability_duration_sec`, require consistent direction

## Next Steps

1. **Run baseline**: Establish current performance across all scenarios
2. **Identify weaknesses**: Which scenarios fail or underperform?
3. **Hypothesize fixes**: What config or code changes might help?
4. **Test fixes**: Re-run scenarios with changes
5. **Measure improvement**: Compare before/after metrics
6. **Iterate**: Repeat until satisfied with risk/reward profile

---

**Remember**: These tests simulate **idealized scenarios**. Real markets are messier (partial fills, network latency, quote staleness, etc.). Use these tests to validate **strategy logic**, then validate execution with backtests on real data.
