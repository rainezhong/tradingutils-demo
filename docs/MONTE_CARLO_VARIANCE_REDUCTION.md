# Monte Carlo Variance Reduction Techniques

**Status:** Reference documentation for future implementation
**Applicable to:** Complex derivatives pricing, portfolio risk simulation, exotic payoffs
**Current usage:** None (analytical pricing sufficient for existing strategies)

This document describes three variance reduction techniques that can dramatically improve Monte Carlo estimation efficiency. These techniques are orthogonal and **stack multiplicatively** — combined, they can reduce required samples by 100–500x.

---

## Three Variance Reduction Tricks That Stack

These three techniques are the "engineering" layer on top of the Monte Carlo theory. Each one independently shrinks the noise in your estimates, and crucially, they **multiply** together — use all three and you can turn a noisy mess into a precise answer with the same number of samples.

---

## 1. Free Symmetry (Antithetic Variates)

The idea is almost embarrassingly simple. When you generate a random sample Z (say, a standard normal), you **also evaluate the payoff at −Z** and average the two.

Why does this work? If Z produces a high price path, −Z produces a low one, and vice versa. The two estimates are **negatively correlated**. When you average two negatively correlated things, their errors partially cancel — the variance of the average is less than the variance of either alone.

For **monotone payoffs** (like binary contracts, where higher price → higher payoff), this negative correlation is guaranteed. The payoff at Z and the payoff at −Z always "pull" in opposite directions, so you always get variance reduction.

The "free" part: you were going to evaluate N payoffs anyway. Now you evaluate N/2 pairs of (Z, −Z), getting N function evaluations with lower variance. The only "cost" is generating the negated sample, which is trivially cheap.

Think of it like this: instead of taking N independent photos of a landscape to estimate average brightness, you take N/2 photos and N/2 negatives. The average of a photo and its negative is more stable than the average of two random photos.

## 2. Exploit What You Already Know (Control Variates)

This is conceptually the most powerful idea here. Suppose you're pricing a binary contract under some complex stochastic volatility model where no closed-form solution exists (that's why you're doing Monte Carlo). But you **do** know the exact Black-Scholes digital price, p_BS, under simplified assumptions.

The insight: on each simulation path, you can compute **both** your complex-model payoff **and** the Black-Scholes payoff. These two quantities are highly correlated (they're driven by similar randomness). You know the true expected value of the Black-Scholes one (it's p_BS), so you can use the *error* in your Black-Scholes MC estimate to correct your complex-model estimate.

Intuitively: if your Monte Carlo run happens to oversample high-price paths, both your complex estimate and your BS estimate will be too high. But since you *know* the BS answer, you can see exactly how much you overshot and subtract that error from your complex estimate.

This is like estimating the population of an obscure city. If your method also estimates New York's population (which you already know), and it says 9M instead of 8.3M, you know your method is running ~8% high and can correct your obscure-city estimate accordingly.

The variance reduction depends on how correlated the control variate is with your target. Black-Scholes and stochastic vol prices are typically very highly correlated, so the reduction can be dramatic.

## 3. Divide and Conquer (Stratified Sampling)

Instead of drawing N samples from the entire probability space, you **partition** it into J strata (bins) and draw N/J samples from each.

Why this helps: crude Monte Carlo can, by bad luck, oversample one region and undersample another. Stratification **forces** even coverage. The variance within each stratum is smaller than the overall variance, and by the **law of total variance**, the stratified estimator's variance is always ≤ crude MC's variance.

The code illustrates this nicely: it divides the uniform [0,1] interval into J equal bins, draws samples within each bin, then transforms them to normal samples via the inverse CDF (this is called **stratified inverse-CDF sampling**). Every "slice" of the distribution is guaranteed representation.

**Neyman allocation** is the optimization: instead of equal samples per stratum, you allocate more samples to strata with higher variance (n_j ∝ ω_j σ_j, where ω_j is the stratum weight and σ_j is the within-stratum standard deviation). Strata where the payoff is always 0 or always 1 (deep out-of-the-money or deep in-the-money) need barely any samples. The strata near the strike — where the payoff is uncertain — get the most.

## Why They Stack Multiplicatively

Each technique attacks a **different source of variance**:

- **Antithetic variates** exploit symmetry in the sampling distribution
- **Control variates** exploit known analytical results to correct systematic errors
- **Stratification** eliminates variance from uneven coverage of the probability space

Because they're orthogonal, you can use antithetic pairs *within* each stratum, then apply a control variate correction to the whole thing. If each gives 3–5x reduction independently, combined you get 100–500x — turning what would require 10⁸ samples into a 10⁵-sample problem. That's the difference between "runs overnight on a cluster" and "runs in seconds on a laptop."

---

## Potential Applications in TradingUtils

### 1. Prediction Market Maker (`strategies/prediction_mm/`)
- **Current:** Analytical Black-Scholes for binary options
- **Future:** Price exotic/path-dependent contracts using MC with BS control variate
- **Example:** Barrier options, lookback contracts, Asian-style payoffs

### 2. Portfolio Risk Management (`core/risk/`)
- **Current:** Analytical Kelly sizing assuming normal returns
- **Future:** VaR/CVaR simulation with correlated strategies
- **Example:** Multi-strategy portfolio drawdown distribution

### 3. Backtest Stress Testing (`src/backtesting/`)
- **Current:** Historical replay with fixed price paths
- **Future:** Generate synthetic market scenarios with stochastic volatility
- **Example:** Test strategy robustness across 10,000 plausible futures

### 4. Complex Derivatives Pricing
- **Use case:** When markets add path-dependent binary contracts
- **Example:** "Will BTC touch $100K before March 31?" (barrier digital)

---

## Implementation Notes

When implementing these techniques, follow the interface-first design pattern:

```python
# core/pricing/monte_carlo.py (future)
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class MonteCarloConfig:
    num_samples: int
    use_antithetic: bool = True
    use_control_variate: bool = True
    use_stratification: bool = True
    num_strata: int = 10

class I_MonteCarloEngine(ABC):
    @abstractmethod
    def price(self, payoff_fn, control_fn=None) -> float:
        """Estimate E[payoff_fn(X)] with variance reduction."""
        pass
```

See `ARCHITECTURE.md` for the full interface-first design principles.

---

**References:**
- Glasserman, P. (2003). *Monte Carlo Methods in Financial Engineering*
- Owen, A. B. (2013). *Monte Carlo theory, methods and examples*
