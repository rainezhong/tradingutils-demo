# The "Fair Value" Myth

## TL;DR

**The strategy does NOT calculate fair value.** It just uses the current Kalshi ask price.

## The Code (detector.py:198-210)

```python
# Determine direction
if delta > 0:  # BTC went UP
    side = "yes"
    if orderbook and orderbook.best_ask:
        entry_price = orderbook.best_ask.price  # ← JUST CURRENT ASK!
    else:
        entry_price = market.yes_ask
else:  # BTC went DOWN
    side = "no"
    if orderbook and orderbook.best_bid:
        entry_price = 100 - orderbook.best_bid.price  # ← NO ask = 100 - YES bid
    else:
        entry_price = market.no_ask
```

**That's it.** No Black-Scholes, no probability calculation, no fair value model.

## What The Strategy Actually Does

1. **Detect:** Binance/Coinbase BTC price moved $10+
2. **Snapshot:** Grab current Kalshi ask price (e.g., 60¢)
3. **Place order:** Buy @ 61¢ (60 + 1¢ slippage buffer)
4. **Hope:** Kalshi hasn't repriced yet

## The Race Condition

```
t=0ms   - BTC drops $22 on Binance
t=50ms  - Strategy detects move, reads Kalshi orderbook: NO ask = 60¢
t=100ms - Strategy places limit order: Buy NO @ 61¢
t=150ms - Kalshi traders see the same Binance drop, update their orders
t=200ms - Kalshi orderbook reprices: NO ask jumps to 71¢
t=3000ms- Your order @ 61¢ still sitting unfilled → TIMEOUT
```

You're racing against:
- Other HFT traders
- Kalshi market makers
- Network latency (SF → Chicago → NYC)
- API processing time

## Dry Run vs Live: Why The Gap?

### Dry Run (Paper Mode)
**Assumes instant magical fills:**
```
17:03:46 - SIGNAL: entry=46¢ (current ask)
17:03:46 - [PAPER] ENTRY: filled @ 46¢  ← INSTANT!
17:04:06 - [PAPER] EXIT: filled @ 50¢   ← INSTANT!
           P&L = +4¢
```

**Results from 27 hours:**
- 209 trades
- 43% win rate
- +$124.50 total
- Avg win: +9¢ | Avg loss: -2¢

### Live Trading
**Reality bites:**
```
20:15:50 - SIGNAL: entry=60¢ (current ask at signal time)
20:15:50 - POST order @ 61¢
           [orderbook shows NO ask = 71¢ by now]
           [order sits unfilled...]
20:15:53 - DELETE order (timeout after 3s)
           P&L = $0 (no fill)
```

**Results from 5 minutes:**
- 10 orders placed
- 1 filled (10% fill rate)
- 9 timeouts

## Why Dry Run Works At All

The strategy bets that **Kalshi will reprice in your direction** within 20 seconds:

**Example wins:**
- Entry NO @ 52¢ → Exit @ 64¢ = **+12¢** (Kalshi repriced NO from 52→64¢)
- Entry NO @ 50¢ → Exit @ 67¢ = **+17¢** (Kalshi repriced NO from 50→67¢)

**Example losses:**
- Entry YES @ 53¢ → Exit @ 52¢ = **-1¢** (Kalshi repriced against you)
- Entry YES @ 75¢ → Exit @ 72¢ = **-3¢** (False signal or reversion)

The 43% win rate means Kalshi reprices in the expected direction ~43% of the time within 20 seconds.

## The Fundamental Flaw

**Paper mode assumption:** "I can buy at the price I see"
**Live reality:** "By the time my order arrives, the price has moved"

This is a **latency arbitrage strategy** but it's:
1. Not calculating fair value (just using current market)
2. Not account for fill latency (assumes instant)
3. Not modeling slippage realistically (1¢ buffer is arbitrary)

## Why It Can Work Despite This

The strategy CAN work if:
1. **You're faster than other traders** (low latency execution)
2. **Kalshi is slow to reprice** (market inefficiency)
3. **You use aggressive pricing** (market orders or cross the spread)

The dry run's 43% win rate suggests Kalshi IS slow to reprice after spot moves. But you need to:
- Either **fill before Kalshi reprices** (race other traders)
- Or **wait for Kalshi to reprice** (longer timeout, accept current market)

## Current Strategy: The Worst of Both Worlds

**What it does:**
- Places limit orders at "pre-reprice" prices (60¢)
- Waits 3 seconds
- Cancels if unfilled

**Why this fails:**
- Not fast enough to win the race (10% fill rate)
- Not patient enough to wait for reprice (3s timeout)
- Not aggressive enough to cross the spread (uses stale prices)

## What Should It Do?

**Option A: Be Fast (HFT approach)**
- Co-locate in same datacenter as Kalshi
- Use websockets for sub-100ms latency
- Cross the spread immediately (market orders)
- Accept 1-2¢ slippage as cost of speed

**Option B: Be Patient (Value approach)**
- Increase timeout to 10-30 seconds
- Let Kalshi reprice naturally
- Accept that you'll miss some opportunities
- Focus on high-confidence signals only

**Option C: Be Smart (Hybrid approach)**
- Check current orderbook BEFORE placing order
- If current ask is within 5¢ of signal price → cross spread
- If current ask is 5-15¢ away → limit order + 10s timeout
- If current ask is >15¢ away → skip (missed the window)

The current strategy is trying to do Option A (fast arb) with Option B's execution (slow patient limits), resulting in 10% fills.
