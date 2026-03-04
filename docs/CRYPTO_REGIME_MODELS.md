# Crypto Scalp Regime Detection: Model Options

**Last Updated:** 2026-02-27

---

## **TL;DR**

**Current approach:** Simple oscillation ratio (total_path / net_move < 3.0)
- ✅ Works well (54% WR vs 41%)
- ✅ Fast, interpretable, no training needed
- ❌ Single feature, hard threshold

**HMM approach:** Learn hidden market states from price patterns
- ✅ Multi-dimensional (price + volume + orderbook)
- ✅ Probabilistic (not binary)
- ✅ Infrastructure already exists in codebase
- ❌ Needs training data (1000+ hours)
- ❌ More complex, harder to debug

**Recommendation:** Start with HMM if you have training data. Otherwise, enhance current approach with additional features (volume, spread, orderflow).

---

## **1. Hidden Markov Model (HMM)**

### **What It Does**

HMM learns **hidden market states** (trending up, trending down, choppy, volatile, quiet) from observable features (price changes, volume, spread, etc.).

```
Hidden states:     [Trending Up] [Choppy] [Trending Down]
                         ↓          ↓           ↓
Observable features: price_move  price_move  price_move
                     volume      volume      volume
                     spread      spread      spread
```

Instead of a binary "trade or not" decision, you get **state probabilities**:
```
P(trending_up) = 0.75
P(choppy) = 0.20
P(trending_down) = 0.05

→ Trade if P(trending_up or trending_down) > 0.8
```

### **How It Works**

1. **Feature extraction:** Compute features from recent price/volume ticks:
   ```python
   features = [
       net_move_5s,        # Net BTC move in last 5s
       oscillation_ratio,  # total_path / net_move
       volume_5s,          # BTC volume traded
       spread_bps,         # Binance L2 spread
       orderflow_imbalance # Buy pressure
   ]
   ```

2. **Train HMM:** Learn state transitions + emissions from historical data:
   ```python
   from hmmlearn.hmm import GaussianHMM

   model = GaussianHMM(
       n_components=3,  # 3 states: trending_up, choppy, trending_down
       covariance_type="diag",
       n_iter=100
   )

   # Train on historical 5-second feature windows
   model.fit(historical_features, lengths)
   ```

3. **Real-time inference:** Get state posteriors from recent features:
   ```python
   current_features = extract_features(last_60_seconds)
   state_probs = model.predict_proba(current_features)

   # state_probs = [0.75, 0.20, 0.05] = [trending_up, choppy, trending_down]
   ```

4. **Trade decision:**
   ```python
   # Trade if in strong trending state
   if state_probs[0] > 0.7 or state_probs[2] > 0.7:
       # Trending - take the trade
       return Signal.buy(...)
   else:
       # Choppy - skip
       return Signal.no_signal("Choppy regime")
   ```

### **Existing HMM Infrastructure**

You already have HMM code for NBA game momentum:

**`src/models/hmm_feature_extractor.py`:**
- `HMMFeatureExtractor` class
- Training: `fit(sequences)` on list of per-game feature arrays
- Inference: `predict_proba(sequence)` returns state posteriors
- BIC model selection: `bic_select()` finds optimal # of states
- Save/load: `save(path)`, `load(path)`

**`models/hmm_win_prob.pkl`:**
- Pre-trained 5-state HMM for NBA momentum
- Trained on 7-dimensional PBP features
- States: home_surge, away_surge, neutral, mild_momentum

**Adapting for crypto:**
```python
from src.models.hmm_feature_extractor import HMMFeatureExtractor

# Define crypto regime features (5-second windows)
def extract_crypto_features(window_data):
    return np.array([
        window_data['net_move'],           # BTC move in window
        window_data['oscillation_ratio'],  # path / net
        window_data['volume'],             # BTC volume
        window_data['spread_bps'],         # L2 spread
        window_data['orderflow_imbalance'] # buy - sell pressure
    ])

# Train HMM on historical 48h+ data
sequences = []  # List of per-episode feature sequences
for episode in historical_episodes:
    features = [extract_crypto_features(w) for w in episode.windows]
    sequences.append(np.array(features))

hmm = HMMFeatureExtractor(n_states=3)  # trending_up, choppy, trending_down
hmm.fit(sequences)
hmm.save('models/crypto_regime_hmm.pkl')

# Real-time usage
hmm = HMMFeatureExtractor.load('models/crypto_regime_hmm.pkl')
current_seq = build_recent_feature_sequence(last_60_seconds)
state_probs = hmm.get_current_posteriors(current_seq)  # Shape: (3,)

if state_probs[0] > 0.7 or state_probs[2] > 0.7:  # trending_up or trending_down
    # Take the trade
```

### **Pros:**
✅ **Multi-dimensional:** Uses price + volume + spread + orderflow (not just price)
✅ **Probabilistic:** Returns state probabilities (not binary decision)
✅ **Captures dynamics:** Models state transitions (how regimes evolve)
✅ **Existing code:** HMMFeatureExtractor already written and tested
✅ **Interpretable:** Can describe learned states (e.g., "State 0 = low vol trend, State 1 = high vol chop")

### **Cons:**
❌ **Needs training data:** Requires 1000+ hours of historical tick data with labels
❌ **Complexity:** More code, harder to debug than simple threshold
❌ **Overfitting risk:** Can learn spurious patterns from limited data
❌ **Latency:** Inference takes 1-5ms (vs <0.1ms for simple ratio)

---

## **2. Other Applicable Models**

### **2.1 Markov Regime Switching (MRS)**

Similar to HMM but explicitly models regime changes with transition probabilities.

**Use case:** Detect shifts between trending and mean-reverting regimes.

```python
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

# Model: price returns ~ regime-dependent mean + variance
model = MarkovRegression(
    returns,
    k_regimes=2,  # trending vs choppy
    trend='c',
    switching_variance=True
)
model.fit()

# Get current regime probabilities
regime_probs = model.smoothed_marginal_probabilities[-1]
```

**Pros:**
- ✅ Explicitly models regime switches
- ✅ Statistical theory (EM algorithm, AIC/BIC selection)
- ✅ `statsmodels` implementation available

**Cons:**
- ❌ Slower than HMM (EM iterations)
- ❌ Requires stationary time series
- ❌ Hard to incorporate external features (volume, spread)

**Verdict:** HMM is more flexible. Use MRS if you need formal statistical tests.

---

### **2.2 GARCH / EGARCH**

Models volatility clustering (high vol → high vol, low vol → low vol).

**Use case:** Detect high-volatility regimes (avoid choppy markets).

```python
from arch import arch_model

# Fit GARCH(1,1)
model = arch_model(returns, vol='GARCH', p=1, q=1)
res = model.fit()

# Get conditional variance forecast
vol_forecast = res.conditional_volatility[-1]

if vol_forecast > threshold:
    # High volatility - skip trade
```

**Pros:**
- ✅ Well-studied for financial time series
- ✅ Captures volatility clustering
- ✅ Fast inference

**Cons:**
- ❌ Only models volatility (not directional trends)
- ❌ Doesn't distinguish trending vs choppy
- ❌ Needs long history (100+ periods)

**Verdict:** Good for volatility filter, but HMM is better for trend/chop detection.

---

### **2.3 K-Means Clustering**

Cluster historical windows into regime categories.

**Use case:** Unsupervised regime discovery.

```python
from sklearn.cluster import KMeans

# Cluster historical 5-second windows
features = np.array([extract_features(w) for w in historical_windows])
kmeans = KMeans(n_clusters=3, random_state=42)
kmeans.fit(features)

# Real-time: assign current window to nearest cluster
current_features = extract_features(current_window)
regime = kmeans.predict([current_features])[0]

if regime == 0:  # Cluster 0 = trending (learned from data)
    # Take the trade
```

**Pros:**
- ✅ Simple, fast, interpretable
- ✅ Unsupervised (no labels needed)
- ✅ `sklearn` implementation

**Cons:**
- ❌ No temporal dynamics (treats windows as i.i.d.)
- ❌ Hard boundaries (not probabilistic)
- ❌ Cluster labels may not align with "trending vs choppy"

**Verdict:** Good for exploratory analysis, but HMM is superior for sequential data.

---

### **2.4 Random Forest / Gradient Boosting**

Supervised classifier: predict "trending or not" from features.

**Use case:** Binary regime classification with non-linear feature interactions.

```python
from sklearn.ensemble import RandomForestClassifier

# Train on labeled data (trending=1, choppy=0)
X = historical_features  # (n_windows, n_features)
y = historical_labels    # (n_windows,) binary

rf = RandomForestClassifier(n_estimators=100)
rf.fit(X, y)

# Real-time prediction
current_features = extract_features(current_window)
is_trending = rf.predict_proba([current_features])[0, 1]

if is_trending > 0.7:
    # Trending - take the trade
```

**Pros:**
- ✅ Handles non-linear feature interactions
- ✅ Feature importance analysis
- ✅ Fast inference
- ✅ You already have LightGBM infrastructure (`src/models/gbm_trainer.py`)

**Cons:**
- ❌ Needs labeled data (trending vs choppy)
- ❌ No temporal modeling (treats windows as i.i.d.)
- ❌ Overfitting risk with small datasets

**Verdict:** Good complement to HMM. Train GBM on HMM state posteriors as features.

---

### **2.5 Technical Indicators (ADX, Bollinger Bands, etc.)**

Use classic TA indicators to detect trending vs ranging markets.

**2.5a ADX (Average Directional Index):**
```python
# ADX > 25 = trending, ADX < 20 = ranging
from ta.trend import ADXIndicator

adx = ADXIndicator(high, low, close, window=14)
adx_value = adx.adx()

if adx_value > 25:
    # Trending
```

**2.5b Bollinger Band Width:**
```python
# Narrow bands = low volatility (avoid), wide bands = trending
from ta.volatility import BollingerBands

bb = BollingerBands(close, window=20, window_dev=2)
bb_width = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()

if bb_width > threshold:
    # Trending
```

**Pros:**
- ✅ Simple, fast, well-understood
- ✅ No training needed
- ✅ Many libraries (`ta`, `ta-lib`)

**Cons:**
- ❌ Fixed parameters (window=14, etc.)
- ❌ Lagging indicators
- ❌ Not adaptive to market conditions

**Verdict:** Good for quick enhancement, but less powerful than ML models.

---

### **2.6 LSTM / Transformer**

Deep learning for sequence modeling.

**Use case:** Learn complex temporal patterns from raw price/volume data.

```python
import torch
import torch.nn as nn

class RegimeLSTM(nn.Module):
    def __init__(self, input_size, hidden_size, num_regimes):
        super().__init__()
        self.lstm = nn.LSTM(input_size, hidden_size, batch_first=True)
        self.fc = nn.Linear(hidden_size, num_regimes)

    def forward(self, x):
        _, (h, _) = self.lstm(x)
        return torch.softmax(self.fc(h[-1]), dim=-1)

# Train on sequences
model = RegimeLSTM(input_size=5, hidden_size=32, num_regimes=3)
# ... training loop
```

**Pros:**
- ✅ Can learn complex patterns
- ✅ End-to-end feature learning

**Cons:**
- ❌ Needs 10,000+ labeled examples
- ❌ Slow training (GPU required)
- ❌ Black box (hard to interpret)
- ❌ Overkill for simple regime detection

**Verdict:** Only if you have massive datasets and GPU infrastructure. HMM is better for your use case.

---

## **3. Recommended Approach**

### **Phase 1: Enhance Current Simple Model (Quick Win)**

Add more features to the oscillation ratio filter:

```python
class EnhancedRegimeDetector:
    def get_regime(self, source: str) -> dict:
        # Current feature
        osc_ratio = self._compute_oscillation_ratio(source)

        # NEW: Add volume concentration
        volume_concentration = self._compute_volume_concentration(source)

        # NEW: Add spread stability
        spread_volatility = self._compute_spread_volatility(source)

        # NEW: Add orderflow imbalance
        orderflow_imbalance = self._compute_orderflow_imbalance(source)

        # Composite score
        if osc_ratio < 3.0 and volume_concentration > 0.5 and spread_volatility < 10:
            return {"regime": "trending", "confidence": 0.9}
        elif osc_ratio > 5.0 or spread_volatility > 20:
            return {"regime": "choppy", "confidence": 0.8}
        else:
            return {"regime": "uncertain", "confidence": 0.5}
```

**Pros:**
- ✅ Fast to implement (1-2 hours)
- ✅ No training data needed
- ✅ Interpretable
- ✅ Can backtest immediately

**Expected improvement:** 54% → 58% win rate

---

### **Phase 2: Train HMM on Historical Data (Better Edge)**

Collect 1000+ hours of probe data, then train HMM:

```python
# 1. Collect features from probe DB
features = []
for window in probe_db.get_windows(duration_sec=5):
    features.append([
        window.net_move,
        window.oscillation_ratio,
        window.volume,
        window.spread_bps,
        window.orderflow_imbalance
    ])

# 2. Segment into episodes (continuous trading sessions)
episodes = segment_by_trading_session(features)

# 3. Train HMM
from src.models.hmm_feature_extractor import HMMFeatureExtractor

hmm = HMMFeatureExtractor(n_states=3)
hmm.fit(episodes)
hmm.save('models/crypto_regime_hmm.pkl')

# 4. Backtest with HMM filter
from src.backtesting.adapters.scalp_adapter import ScalpAdapter

adapter = ScalpAdapter(
    db_path=probe_db_path,
    regime_model=hmm  # NEW: Pass trained HMM
)
results = engine.run(adapter)
```

**Pros:**
- ✅ Multi-dimensional features
- ✅ Probabilistic (confidence scores)
- ✅ Learns from data (adaptive)

**Expected improvement:** 54% → 60%+ win rate

---

### **Phase 3: Hybrid HMM + GBM (Maximum Edge)**

Use HMM state posteriors as features for GBM classifier:

```python
# 1. Train HMM (as above)
hmm = HMMFeatureExtractor.load('models/crypto_regime_hmm.pkl')

# 2. Generate HMM posteriors for all historical windows
X = []
y = []
for window in historical_windows:
    state_probs = hmm.get_current_posteriors(window.features)
    X.append(np.concatenate([window.raw_features, state_probs]))
    y.append(window.was_profitable)  # Label: did trade make money?

# 3. Train GBM on (raw features + HMM posteriors) → profit
from src.models.gbm_trainer import GBMTrainer

gbm = GBMTrainer()
gbm.hyperparameter_search(X, y, groups=window.game_ids)
gbm.train_final(X, y)
gbm.save('models/crypto_regime_gbm.txt', 'models/crypto_regime_cal.pkl')

# 4. Real-time: HMM → GBM → trade decision
state_probs = hmm.get_current_posteriors(current_window)
features = np.concatenate([current_window.raw_features, state_probs])
profit_prob = gbm.predict([features], calibrate=True)[0]

if profit_prob > 0.6:
    # High confidence - take trade
```

**Pros:**
- ✅ Best of both worlds (HMM dynamics + GBM non-linearity)
- ✅ You already have both components
- ✅ Feature importance analysis (which states matter?)

**Expected improvement:** 54% → 62%+ win rate

---

## **4. Data Requirements**

| Model | Training Data | Latency | Interpretability |
|-------|---------------|---------|------------------|
| Simple threshold | None | <0.1ms | ⭐⭐⭐⭐⭐ |
| Enhanced features | None | <0.5ms | ⭐⭐⭐⭐ |
| Technical indicators | None | <1ms | ⭐⭐⭐⭐ |
| K-Means | 100+ hours | 1ms | ⭐⭐⭐ |
| HMM | 500+ hours | 2-5ms | ⭐⭐⭐ |
| GARCH | 1000+ periods | 5ms | ⭐⭐ |
| GBM | 1000+ labeled | 1ms | ⭐⭐ |
| HMM + GBM | 1000+ hours | 3-8ms | ⭐⭐ |
| LSTM | 10,000+ hours | 10-50ms | ⭐ |

---

## **5. Implementation Roadmap**

### **Week 1: Enhanced Simple Model**
- Add volume concentration filter
- Add spread volatility filter
- Add orderflow imbalance (from BRTI tracker)
- Backtest on existing 48h data
- **Goal:** 54% → 58% WR

### **Week 2-3: Data Collection**
- Run probe for 500+ hours (3 weeks)
- Collect Binance trades, Coinbase trades, Kraken snapshots
- Store volume, spread, orderflow in new DB columns
- **Goal:** 500+ hours of clean data

### **Week 4: Train HMM**
- Extract 5-dimensional features from probe DB
- Segment into trading episodes
- Train HMM with BIC model selection (3-5 states)
- Backtest HMM filter on historical data
- **Goal:** 54% → 60% WR

### **Week 5: Hybrid HMM + GBM (Optional)**
- Generate HMM state posteriors for all windows
- Label windows by profitability (1 = profitable, 0 = loss)
- Train GBM with hyperparameter search
- Backtest hybrid model
- **Goal:** 60% → 62%+ WR

---

## **6. Quick Start: HMM with Existing Code**

```python
# File: strategies/crypto_scalp/hmm_regime_detector.py

import numpy as np
from typing import Optional
from src.models.hmm_feature_extractor import HMMFeatureExtractor

class HMMRegimeDetector:
    """Crypto regime detection using pre-trained HMM."""

    def __init__(self, model_path: str = "models/crypto_regime_hmm.pkl"):
        self.hmm = HMMFeatureExtractor.load(model_path)
        self.feature_buffer = []  # Last 60s of features

    def update(self, price: float, volume: float, spread_bps: float, ts: float):
        """Feed a tick."""
        # Compute 5s window features
        if len(self.feature_buffer) >= 12:  # 60s / 5s = 12 windows
            features = self._compute_window_features()
            self.feature_buffer.append(features)
            if len(self.feature_buffer) > 12:
                self.feature_buffer.pop(0)

    def get_regime(self) -> dict:
        """Get current regime probabilities."""
        if len(self.feature_buffer) < 5:
            return {"regime": "unknown", "trending_prob": 0.5}

        sequence = np.array(self.feature_buffer)
        state_probs = self.hmm.get_current_posteriors(sequence)

        # State 0 = trending_up, State 1 = choppy, State 2 = trending_down
        trending_prob = state_probs[0] + state_probs[2]

        return {
            "regime": "trending" if trending_prob > 0.7 else "choppy",
            "trending_prob": float(trending_prob),
            "state_probs": state_probs.tolist()
        }

    def _compute_window_features(self) -> np.ndarray:
        """Compute features for last 5 seconds."""
        # TODO: Extract net_move, osc_ratio, volume, spread, orderflow
        return np.array([0.0, 0.0, 0.0, 0.0, 0.0])
```

**Integration with CryptoScalpStrategy:**
```python
# In strategies/crypto_scalp/orchestrator.py

from strategies.crypto_scalp.hmm_regime_detector import HMMRegimeDetector

class CryptoScalpStrategy(I_Strategy):
    def __init__(self, ...):
        # ...
        if self._config.use_hmm_regime:
            self._hmm_regime = HMMRegimeDetector()

    def get_signal(self, market: Any) -> Signal:
        # ...

        # NEW: HMM regime filter
        if self._config.use_hmm_regime:
            regime = self._hmm_regime.get_regime()
            if regime['trending_prob'] < 0.7:
                return Signal.no_signal(
                    f"HMM: choppy regime (trending_prob={regime['trending_prob']:.2f})"
                )

        # Existing logic...
```

---

## **7. Summary**

| Approach | Complexity | Data Needed | Expected WR | Time to Implement |
|----------|-----------|-------------|-------------|-------------------|
| **Current (osc < 3.0)** | Low | None | 54% | ✅ Done |
| **Enhanced features** | Low | None | 58% | 1-2 days |
| **HMM** | Medium | 500+ hours | 60% | 1-2 weeks |
| **HMM + GBM** | High | 1000+ hours | 62%+ | 3-4 weeks |

**Recommendation:**
1. **Short-term (this week):** Add volume + spread filters (Phase 1)
2. **Medium-term (4 weeks):** Train HMM after collecting data (Phase 2)
3. **Long-term (8 weeks):** Hybrid HMM + GBM (Phase 3) if edge persists

**Bottom line:** HMM is definitely applicable and you already have the infrastructure. The question is whether you want to invest 2-4 weeks in data collection + training, or if simpler enhancements will suffice.
