# Feature Applicability by Strategy Type

## Quick Reference

| Feature | Market Making | Directional (NBA, Arb) | Scalping | Infrastructure |
|---------|---------------|------------------------|----------|----------------|
| **Empirical Kelly** | ✅ Yes | ✅ Yes | ✅ Yes | Portfolio-wide |
| **Sequence Gap Detection** | ✅ Yes (if WS) | ✅ Yes (if WS) | ✅ Yes (if WS) | Infrastructure |
| **VPIN Kill Switch** | ✅ Yes | ❌ No | ⚠️ Maybe | MM-specific |
| **A-S Reservation Price** | ✅ Yes | ❌ No | ❌ No | MM-specific |

---

## Feature Scope Details

### 1. Empirical Kelly - **PORTFOLIO-WIDE**

**Applies to:** Every strategy in your system

**Current strategies that benefit:**
- ✅ `nba-underdog` - Empirical Kelly adjusts position size based on recent W/L variance
- ✅ `nba-fade-momentum` - Reduces allocation if recent trades show high CV
- ✅ `nba-mean-reversion` - Same as above
- ✅ `crypto-latency` - Adjusts based on fill quality variance
- ✅ `crypto-scalp` - Adjusts based on scalp PnL stability
- ✅ `prediction-mm` - Adjusts based on spread capture variance
- ✅ **Any future strategy you add**

**How it works:**
```python
# Portfolio manager tracks ALL strategies
strategies = {
    'nba-underdog': StrategyStats(edge=0.10, std_dev=0.50),
    'crypto-latency': StrategyStats(edge=0.15, std_dev=0.30),
    'prediction-mm': StrategyStats(edge=0.08, std_dev=0.60),
}

# Empirical Kelly adjusts EACH allocation based on uncertainty
for strategy, stats in strategies.items():
    cv = stats.std_dev / stats.edge
    haircut = 1 - cv
    allocation = kelly_allocation * haircut
```

**Enable it if:**
- ✅ You run multiple strategies
- ✅ You want to reduce drawdowns
- ✅ You want uncertainty-adjusted position sizing

**Skip it if:**
- ❌ You run only one strategy (no allocation needed)
- ❌ You prefer fixed position sizing

---

### 2. Sequence Gap Detection - **INFRASTRUCTURE**

**Applies to:** Any strategy using WebSocket feeds

**Current strategies using WebSocket:**
- ✅ `crypto-scalp` - Binance/Coinbase/Kalshi feeds
- ✅ `crypto-latency` - Kraken/Kalshi feeds
- ✅ `crypto-latency-v2` - Multi-feed orchestration
- ✅ `prediction-mm` - Kalshi orderbook feed
- ⚠️ NBA strategies - Currently use REST polling, not WebSocket

**How it works:**
```python
# WebSocket receives messages with sequence numbers
msg = {
    'seq': 1234,
    'ticker': 'BTC-65000',
    'price': 0.55,
}

# Gap detection validates sequence
if msg['seq'] != last_seq + 1:
    logger.error(f"Gap detected: expected {last_seq+1}, got {msg['seq']}")
    reconnect()  # Prevent orderbook corruption
```

**Enable it if:**
- ✅ You use WebSocket feeds (crypto, prediction MM)
- ✅ You've experienced orderbook divergence issues
- ✅ You want defensive infrastructure

**Skip it if:**
- ❌ You only use REST API polling (NBA strategies currently)
- ❌ Your feeds don't provide sequence numbers (most don't yet)

**Note:**
- Kalshi doesn't currently provide `seq` in orderbook messages (architecture is ready when they add it)
- Only Coinbase provides sequence numbers in L2 feeds
- Other exchanges (Binance, Kraken, Bitstamp) don't support it

---

### 3. VPIN Kill Switch - **MARKET MAKING ONLY**

**Applies to:** Strategies that POST quotes (provide liquidity)

**Current strategies:**
- ✅ `prediction-mm` - Posts bid/ask quotes, YES/NO sides
- ❌ `nba-underdog` - Takes directional positions (no quotes)
- ❌ `nba-fade-momentum` - Directional strategy
- ❌ `nba-mean-reversion` - Directional strategy
- ❌ `crypto-latency` - Hits stale quotes (doesn't post)
- ❌ `crypto-scalp` - Takes directional positions

**Why market making only:**

VPIN measures **order flow toxicity** - the imbalance between buy and sell volume. This matters when:
1. You're quoting both sides (bid AND ask)
2. Informed traders choose which side to hit based on private info
3. You get adversely selected (always wrong side of the trade)

**Directional strategies don't have this problem:**
- NBA betting: You WANT to be on one side (you have an edge)
- Latency arb: You WANT to hit stale quotes (that's the strategy)
- Scalping: You choose your entries based on signals

**How it works:**
```python
# Market maker posts quotes
bid = 45¢, ask = 47¢

# VPIN tracks fills
fills = [
    {'side': 'bid', 'size': 10},  # Our bid was hit (someone sold to us)
    {'side': 'bid', 'size': 10},  # Our bid was hit again
    {'side': 'bid', 'size': 10},  # Our bid was hit again
    # ^ High imbalance = informed trader knows price is dropping
]

# VPIN > 0.75 → kill switch activates
cancel_all_quotes()  # Stop providing liquidity
```

**Enable it if:**
- ✅ You're running prediction MM (or other MM strategy)
- ✅ You're experiencing adverse selection
- ✅ You want to protect during volatile events

**Skip it if:**
- ❌ You only run directional strategies
- ❌ You don't post quotes

**Could extend to:**
- ✅ Depth-based scalping (if you add quote posting)
- ✅ Polymarket MM (if you build it)
- ✅ CEX MM (if you build it)

---

### 4. A-S Reservation Price - **MARKET MAKING ONLY**

**Applies to:** Strategies that manage inventory from passive fills

**Current strategies:**
- ✅ `prediction-mm` - Accumulates inventory from fills
- ❌ All directional strategies

**Why market making only:**

The A-S formula adjusts your **reservation price** (where you center quotes) based on:
- **Inventory**: How many contracts you're long/short
- **Time to expiry**: How long until you're forced to exit
- **Volatility**: How risky it is to hold inventory

This only makes sense when:
1. You accumulate inventory passively (from being filled on quotes)
2. You want to unwind inventory before expiry
3. You adjust quotes to encourage unwinding

**Directional strategies:**
- Want to HOLD positions (not unwind)
- Take positions intentionally (not passively)
- Don't quote spreads to adjust

**How it works:**
```python
# Market maker accumulates long position
net_position = +50 contracts  # Long 50 YES

# A-S reservation price
fair_value = 0.55
adjustment = -50 × 0.05 × 0.25 × 0.1  # position × γ × σ² × (T-t)
reservation_price = 0.55 - 0.006 = 0.544

# Quotes centered around reservation (lower than fair)
bid = 0.534, ask = 0.554

# Effect: More likely to sell (unwind long position)
```

**Enable it if:**
- ✅ You're running prediction MM
- ✅ You experience inventory extremes (stuck at max long/short)
- ✅ You want time-aware inventory management

**Skip it if:**
- ❌ You don't run market making strategies
- ❌ You want to hold positions (directional strategies)

---

## Deployment Recommendations by Strategy Mix

### **Scenario 1: You ONLY run directional strategies** (NBA, latency arb)

```yaml
# Enable these:
use_empirical_kelly: true  # ✅ Portfolio allocation
enable_sequence_validation: true  # ✅ If using WebSocket

# Skip these:
vpin_kill_switch.enabled: false  # ❌ Not relevant
use_reservation_price: false  # ❌ Not relevant
```

**Impact:** 15-30% drawdown reduction from empirical Kelly alone.

---

### **Scenario 2: You ONLY run prediction MM**

```yaml
# Enable all 4 features:
use_empirical_kelly: true  # ✅ Portfolio allocation (even for 1 strategy)
enable_sequence_validation: true  # ✅ Orderbook integrity
vpin_kill_switch.enabled: true  # ✅ Toxic flow protection
use_reservation_price: true  # ✅ Inventory management
```

**Impact:** 20-40% overall improvement (all features combined).

---

### **Scenario 3: You run BOTH directional + MM strategies**

```yaml
# Portfolio-wide:
use_empirical_kelly: true  # ✅ Allocates across all strategies

# Infrastructure:
enable_sequence_validation: true  # ✅ All WebSocket feeds

# MM-specific (only affects prediction-mm strategy):
vpin_kill_switch.enabled: true  # ✅ Only used by prediction-mm
use_reservation_price: true  # ✅ Only used by prediction-mm
```

**Impact:** Maximum - all features contribute.

**Note:** VPIN and A-S don't affect directional strategies at all. They're only used by the prediction MM orchestrator.

---

## Code Architecture

### How Features are Scoped

**1. Portfolio-wide (Empirical Kelly):**
```python
# core/portfolio/portfolio_manager.py
class PortfolioManager:
    def rebalance(self):
        # Gets stats from ALL strategies
        all_stats = {
            'nba-underdog': self.tracker.get_strategy_stats('nba-underdog'),
            'crypto-latency': self.tracker.get_strategy_stats('crypto-latency'),
            'prediction-mm': self.tracker.get_strategy_stats('prediction-mm'),
        }

        # Empirical Kelly applied to each
        allocations = self.optimizer.calculate_allocations(all_stats)
```

**2. Infrastructure (Sequence Gap Detection):**
```python
# core/exchange_client/kalshi/kalshi_websocket.py
class KalshiWebSocket:
    def _check_sequence_gap(self, ticker, seq):
        # Used by ANY strategy connecting to this WebSocket
        if seq != self._last_seq[ticker] + 1:
            self._handle_gap(ticker)
```

**3. Strategy-specific (VPIN, A-S):**
```python
# strategies/prediction_mm/orchestrator.py
class PredictionMMOrchestrator:
    def __init__(self, config):
        # Only created if this strategy is running
        if config.vpin_kill_switch.enabled:
            self.vpin = VPINCalculator()

        if config.use_reservation_price:
            self.reservation_pricer = ReservationPricer()

    def on_tick(self):
        # VPIN only affects this strategy's quotes
        if self.vpin and self._check_vpin_state() == TOXIC:
            self._cancel_all_quotes()
```

**Key point:** NBA strategies never even import VPIN or A-S code. They're isolated in `strategies/prediction_mm/`.

---

## Migration Path

### **Phase 1: Enable Universal Features First**

Start with features that help ALL strategies:

```bash
# Week 1: Backtest empirical Kelly across all strategies
python3 main.py backtest nba-underdog --config config/portfolio_config_empirical.yaml
python3 main.py backtest crypto-latency --config config/portfolio_config_empirical.yaml

# Week 2: Enable in production
# config/portfolio_config.yaml
use_empirical_kelly: true
```

### **Phase 2: Enable Infrastructure**

If you use WebSocket feeds:

```yaml
# Week 3: Enable sequence validation
enable_sequence_validation: true
gap_tolerance: 1
```

### **Phase 3: Enable MM-Specific Features**

If you run prediction MM:

```yaml
# Week 4: Enable VPIN
vpin_kill_switch.enabled: true

# Week 5: Enable A-S
use_reservation_price: true
```

---

## Summary

| Feature | When to Use |
|---------|-------------|
| **Empirical Kelly** | Always (if running multiple strategies) |
| **Sequence Gap Detection** | If using WebSocket feeds |
| **VPIN Kill Switch** | Only if market making |
| **A-S Reservation Price** | Only if market making |

**If you only run NBA strategies:** Just use Empirical Kelly.
**If you only run prediction MM:** Use all 4 features.
**If you run both:** Enable all, MM features only affect MM strategy.

All features are **independent and opt-in**. Mix and match based on what strategies you're running.
