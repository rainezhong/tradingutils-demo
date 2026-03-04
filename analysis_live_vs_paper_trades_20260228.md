# Crypto Scalp Live Trading Analysis - February 28, 2026

## Executive Summary

**Session Duration**: 22:14:41 - 22:46:23 (~32 minutes)
**Total Completed Trades**: 9
**Win Rate**: 11.1% (1 winner, 8 losers)
**Total P&L**: -38¢ (-$0.38)
**Average P&L per Trade**: -4.2¢ (-$0.042)
**Average Hold Time**: 20.1 seconds

## Performance Breakdown

### By Direction
- **YES trades**: 7 trades, -37¢ (-$0.37)
- **NO trades**: 2 trades, -1¢ (-$0.01)

### By Signal Source
- **Binance signals**: 5 trades, -23¢ (-$0.23)
- **Coinbase signals**: 4 trades, -15¢ (-$0.15)

## Trade-by-Trade Details

| # | Time | Source | Side | Ticker | Entry | Exit | P&L | Hold Time |
|---|------|--------|------|--------|-------|------|-----|-----------|
| 1 | 22:16:28 | binance | YES | KXBTC15M-26MAR010130-30 | 64¢ | 46¢ | -18¢ | 20s |
| 2 | 22:17:05 | coinbase | YES | KXBTC15M-26MAR010130-30 | 48¢ | 46¢ | -2¢ | 20s |
| 3 | 22:18:03 | coinbase | YES | KXBTC15M-26MAR010130-30 | 41¢ | 39¢ | -2¢ | 20s |
| 4 | 22:18:44 | binance | NO | KXBTC15M-26MAR010130-30 | 62¢ | 66¢ | -4¢ | 20s |
| 5 | 22:19:34 | coinbase | YES | KXBTC15M-26MAR010130-30 | 35¢ | 26¢ | -9¢ | 21s |
| 6 | 22:20:16 | coinbase | YES | KXBTC15M-26MAR010130-30 | 28¢ | 26¢ | -2¢ | 20s |
| 7 | 22:25:06 | binance | YES | KXBTC15M-26MAR010130-30 | 63¢ | 61¢ | -2¢ | 20s |
| 8 | 22:25:58 | binance | NO | KXBTC15M-26MAR010130-30 | 54¢ | 51¢ | **+3¢** | 20s |
| 9 | 22:31:33 | binance | YES | KXBTC15M-26MAR010145-45 | 39¢ | 37¢ | -2¢ | 20s |

## Issues Identified

### 1. **Very Low Fill Rate (23.1%)**
- **Filled orders**: 9 entries + 9 exits = 18 orders
- **Unfilled orders**: 30 cancelled after 3-second timeout
- **Total order attempts**: 48
- **Fill rate**: 18/48 = 37.5% (but this includes exits)
- **Entry fill rate**: 9/39 = **23.1%**

The strategy is missing ~77% of entry opportunities due to orders not filling within 3 seconds.

### 2. **One Open Position**
Trade entered at 22:22:45 but never exited:
- **Ticker**: KXBTC15M-26MAR010130-30
- **Side**: YES
- **Entry Price**: 29¢
- **Entry Order**: 3a8e5e1a-7112-4b7e-93a7-d4cb6a82ba7e

This position was still open when the first log file ended. The second session started fresh without carrying over this position, suggesting it was either manually closed or the session crashed.

### 3. **Consistent Losses Pattern**

**Loss distribution**:
- -2¢: 6 trades (67% of trades)
- -4¢: 1 trade
- -9¢: 1 trade
- -18¢: 1 trade

The most common loss is **-2¢ per contract**, which suggests:
- **Bid-ask spread capture**: Strategy is likely buying at market and selling 20 seconds later at a worse price
- **No edge**: The -2¢ loss is consistent with paying the spread plus minor slippage
- **Trade #1 (-18¢)**: Largest loss occurred during the very first trade, possibly due to initialization issues or a fast market move

### 4. **Only One Winner**

Trade #8 was the only profitable trade:
- **P&L**: +3¢
- **Side**: NO
- **Entry**: 54¢, Exit: 51¢
- **NO trade profit formula**: Entry - Exit = 54 - 51 = +3¢

This is the only trade where the market moved in the strategy's favor quickly enough within the 20-second exit window.

### 5. **Very Short Hold Times**

All trades were held for **20-21 seconds**, which is:
- Exactly at the configured `exit_delay: 20.0s`
- No trades exited early despite the `max_hold: 35.0s` setting
- Suggests the strategy is hitting the minimum exit delay on every trade

### 6. **Market Conditions**

**Ticker transitions**:
- Trades 1-8: KXBTC15M-26MAR010130-30 (BTC $67,010-$67,130 range)
- Trade 9: KXBTC15M-26MAR010145-45 (new strike)

The market appears to have been relatively stable (tight $120 range), which may explain:
- Low fill rates (not enough edge to overcome spread)
- Consistent -2¢ losses (spread + minimal movement)

## Comparison to Backtest Performance

Based on the backtest results mentioned in the logs ("6 trades, 17% win, -30c P&L"), live trading is performing **worse** than backtest:

| Metric | Backtest | Live | Delta |
|--------|----------|------|-------|
| Win Rate | 17% | 11% | -6% |
| Avg P&L | -5¢ | -4.2¢ | +0.8¢ |
| Total P&L | -30¢ (6 trades) | -38¢ (9 trades) | -8¢ |

**Note**: The backtest mentioned in the dashboard log appears to be from a previous session, not the same period.

## Root Cause Analysis

### Why are all trades losing money?

1. **Spread costs**: Buying at ask, selling at bid = guaranteed -1 to -2¢ loss
2. **Adverse selection**: Only filling when market is moving against us
3. **No momentum follow-through**: 20-second hold time too short for mean reversion
4. **Entry timing**: Signals may be stale by the time order reaches market

### Why is the fill rate so low?

1. **Tight pricing**: Limit orders may be too aggressive (entering at exact fair value)
2. **Fast markets**: BTC moves quickly, prices outdated by submission time
3. **Kalshi liquidity**: May not have enough depth at the prices we're targeting
4. **3-second timeout**: Very tight window, may need to be extended

## Recommendations

### Immediate Actions

1. **Disable live trading** until fill rate and loss patterns are addressed
2. **Review entry pricing logic**: Consider entering 1-2¢ worse to improve fills
3. **Increase fill timeout**: Change from 3s to 5-10s to see if fills improve
4. **Check Kalshi orderbook depth**: Are we trying to trade sizes larger than available liquidity?

### Strategic Changes

1. **Exit logic review**: Why is every trade exiting at exactly 20s? Should implement:
   - Take-profit targets (e.g., +2¢ edge)
   - Stop-loss levels (e.g., -5¢)
   - Momentum continuation (hold longer if moving in our favor)

2. **Entry signal validation**: Add checks:
   - Minimum edge requirement (e.g., >5¢ after spread)
   - Orderbook depth confirmation
   - Signal staleness check (time since CEX trade)

3. **Spread cost modeling**: Backtest should include:
   - Realistic spread costs (-2¢ minimum)
   - Fill rate simulation (reject 77% of signals)
   - Slippage from limit order non-fills

4. **Position management**: The open position issue suggests:
   - Need better session state management
   - Consider exit-on-shutdown logic
   - Track positions across restarts

### Data Collection

Before resuming live trading:
1. Run paper trading mode for 24 hours
2. Log all orderbook snapshots at signal time
3. Measure actual spreads and available liquidity
4. Compare paper fills vs real fills
5. Analyze which signals would have been profitable

## Conclusion

The live trading session revealed critical issues:
- **23% fill rate** makes the strategy unviable (need >50%)
- **89% loss rate** indicates no edge after spread costs
- **-2¢ per trade** is consistent with spread + adverse selection
- **20-second exits** are not giving trades time to develop

The strategy appears to be:
1. Generating signals based on CEX price moves
2. Submitting limit orders that rarely fill
3. When they do fill, exiting exactly 20s later at a loss
4. No dynamic exit logic or profit targets

**Recommendation**: Pause live trading and return to paper trading with improved fill rate modeling and exit logic before risking more capital.
