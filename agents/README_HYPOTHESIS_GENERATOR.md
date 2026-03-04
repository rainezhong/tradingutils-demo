# Hypothesis Generator Agent

LLM-powered agent for generating structured trading strategy hypotheses from data patterns or novel brainstorming.

## Overview

The `HypothesisGeneratorAgent` uses Claude to analyze market patterns and generate exploitable trading opportunities. It operates in two modes:

1. **Pattern-Based**: Given an observed anomaly (e.g., spread widening), generate strategies to exploit it
2. **Brainstorming**: Generate novel trading ideas from scratch

## Installation

Requires the Anthropic SDK:

```bash
pip install anthropic
```

Set your API key:

```bash
export ANTHROPIC_API_KEY='your-key-here'
```

## Quick Start

```python
from agents.hypothesis_generator import (
    HypothesisGeneratorAgent,
    TradingHypothesis,
    MarketType,
    HypothesisConfidence
)

# Initialize the agent
generator = HypothesisGeneratorAgent(
    model="claude-sonnet-4-5-20250929",
    temperature=0.7  # Higher = more creative
)

# Example 1: Generate from observed pattern
pattern = {
    "market_type": "NBA",
    "observation": "Spreads widen to 10-15 cents in the 2 hours before game start",
    "statistics": {
        "typical_spread": "3-5 cents",
        "widened_spread": "10-15 cents",
        "frequency": "~60% of games"
    },
    "timeframe": "2 hours before game start"
}

hypotheses = generator.generate_from_pattern(pattern, num_hypotheses=3)

for h in hypotheses:
    print(f"{h.name}: {h.description}")
    print(f"Expected Sharpe: {h.expected_sharpe}")
    print(f"Confidence: {h.confidence}")

# Example 2: Brainstorm novel ideas
novel_ideas = generator.brainstorm_hypotheses(
    market_type="crypto",
    num_hypotheses=5
)

# Example 3: Rank hypotheses by promise
ranked = generator.rank_hypotheses(
    hypotheses,
    criteria={
        "novelty": 0.3,
        "confidence": 0.5,
        "implementation": 0.2
    }
)
```

## Pattern-Based Generation

When you observe an anomaly in data, use pattern-based generation to get actionable strategies:

```python
pattern_data = {
    "market_type": str,           # e.g. "NBA", "crypto", "politics"
    "observation": str,           # Description of the pattern
    "statistics": dict,           # Relevant stats (optional)
    "timeframe": str             # When pattern occurs (optional)
}

context = {
    "existing_strategies": [],    # What strategies you already have
    "constraints": {}            # Position limits, risk tolerance, etc.
}

hypotheses = generator.generate_from_pattern(
    pattern_data=pattern_data,
    context=context,
    num_hypotheses=3
)
```

### Pattern Example: NBA Spread Widening

```python
pattern = {
    "market_type": "NBA",
    "observation": "Market spreads widen from 3-5 cents to 10-15 cents in the 2 hours before game start",
    "statistics": {
        "typical_spread": "3-5 cents",
        "widened_spread": "10-15 cents",
        "timeframe": "2 hours before game start",
        "frequency": "~60% of games",
        "avg_volume_drop": "40% decrease"
    },
    "timeframe": "2 hours before game start"
}

# Generate 2 strategies to exploit this
hypotheses = generator.generate_from_pattern(pattern, num_hypotheses=2)

# Each hypothesis includes:
# - Name and description
# - Theoretical basis (why edge exists)
# - Entry/exit logic
# - Expected Sharpe, win rate, avg profit
# - Risk factors
# - Confidence level
# - Implementation difficulty
```

### Pattern Example: Crypto Volatility Spike

```python
pattern = {
    "market_type": "crypto",
    "observation": "BTC volatility spikes 3x normal in the 5 minutes before Kalshi expiry",
    "statistics": {
        "normal_vol": "0.8% per 5min",
        "spike_vol": "2.5% per 5min",
        "frequency": "~40% of expirations",
        "kalshi_lag": "1-2 seconds behind Kraken"
    },
    "timeframe": "5 minutes before expiry"
}

hypotheses = generator.generate_from_pattern(pattern, num_hypotheses=3)
```

## Brainstorming Mode

Generate novel trading ideas without a specific pattern:

```python
hypotheses = generator.brainstorm_hypotheses(
    market_type="crypto",  # Optional: focus on specific market
    constraints={
        "max_holding_period": "7 days",
        "min_sharpe": 1.0,
        "focus": "market microstructure"
    },
    num_hypotheses=5
)
```

### Brainstorming Example: Cross-Market Ideas

```python
# Generate ideas across all markets
all_market_ideas = generator.brainstorm_hypotheses(
    market_type=None,  # No specific market
    num_hypotheses=10
)

# Focus on specific domain
nba_ideas = generator.brainstorm_hypotheses(
    market_type="NBA",
    constraints={
        "focus": "timing and momentum patterns",
        "max_holding_period": "4 hours"
    },
    num_hypotheses=5
)
```

## Hypothesis Structure

Each `TradingHypothesis` contains:

```python
@dataclass
class TradingHypothesis:
    name: str                          # "NBA Underdog Mispricing"
    description: str                   # Detailed explanation
    theoretical_basis: str             # Why edge exists
    market_type: str                   # "NBA", "crypto", etc.

    # Expected characteristics
    expected_sharpe: Optional[float]   # Estimated Sharpe ratio
    expected_win_rate: Optional[float] # Win rate percentage
    expected_avg_profit: Optional[float] # Avg profit per trade

    # Strategy logic
    entry_logic: str                   # Entry conditions
    exit_logic: str                    # Exit management

    # Risk assessment
    risk_factors: List[str]            # Specific risks/failure modes

    # Meta information
    confidence: str                    # "high", "medium", "low"
    novelty_score: float              # 0-1, how novel the idea is
    implementation_difficulty: str     # "easy", "medium", "hard"
    data_requirements: List[str]       # Data needed to validate
    related_hypotheses: List[str]      # Similar ideas
```

## Ranking Hypotheses

Rank hypotheses by novelty, confidence, and implementation difficulty:

```python
# Default criteria (balanced)
ranked = generator.rank_hypotheses(hypotheses)

# Custom criteria
ranked = generator.rank_hypotheses(
    hypotheses,
    criteria={
        "novelty": 0.7,        # Weight for novelty
        "confidence": 0.2,     # Weight for confidence
        "implementation": 0.1  # Weight for ease of implementation
    }
)

# Results are sorted best-first
best_hypothesis = ranked[0]
```

## Integration with Data Scout

Combine with `DataScoutAgent` for automated pattern → hypothesis workflow:

```python
from agents.data_scout import DataScoutAgent
from agents.hypothesis_generator import HypothesisGeneratorAgent

# Step 1: Detect patterns
scout = DataScoutAgent(db_path="data/btc_latency_probe.db")
patterns = scout.scan_for_patterns(min_snapshots=100)

# Step 2: Convert top patterns to hypothesis input
generator = HypothesisGeneratorAgent()

for pattern in patterns[:5]:  # Top 5 patterns
    pattern_data = {
        "market_type": "crypto",
        "observation": pattern.description,
        "statistics": {
            "confidence": pattern.confidence,
            "significance": pattern.statistical_significance,
            "data_points": pattern.data_points
        },
        "timeframe": "varies"
    }

    # Generate strategies
    hypotheses = generator.generate_from_pattern(pattern_data, num_hypotheses=2)

    # Rank and select best
    ranked = generator.rank_hypotheses(hypotheses)
    best = ranked[0]

    print(f"\nPattern: {pattern.pattern_type} - {pattern.ticker}")
    print(f"Best Strategy: {best.name}")
    print(f"Expected Sharpe: {best.expected_sharpe}")
    print(f"Confidence: {best.confidence}")
```

## Output Examples

### Pattern-Based Hypothesis

```
Name: "Pre-Game Liquidity Provision"

Description: Act as liquidity provider during the 2-hour pre-game window when
spreads widen due to reduced market maker activity. Place limit orders on both
sides at favorable prices, capturing the spread as recreational traders take liquidity.

Theoretical Basis: Market makers reduce activity before games due to increased
uncertainty and higher inventory risk. Recreational traders continue to trade
at market, creating opportunity for patient limit orders at wide spreads.

Entry Logic:
- 90-120 minutes before game start
- Spread >= 8 cents (2x normal)
- Place limit orders at mid ± 4 cents

Exit Logic:
- Cancel orders 10 minutes before game (volatility risk)
- Take profit if one side fills and can exit at 2+ cent profit
- Max holding time: 2 hours

Expected Characteristics:
- Sharpe: 1.2-1.8
- Win Rate: 65%
- Avg Profit: $1.50/trade

Risk Factors:
- Game news can cause sudden price moves
- May get adversely selected if sharp money enters
- Inventory risk if can't exit other side
- Requires active monitoring
```

### Brainstormed Hypothesis

```
Name: "Cross-Exchange Arbitrage with Latency Edge"

Description: Exploit 1-2 second lag between Kraken price movements and Kalshi
market updates by monitoring order flow imbalance on Kraken and pre-positioning
on Kalshi before the crowd reacts.

Theoretical Basis: Kalshi market makers are slower to react than CEX market
makers. Large Kraken orders create predictable short-term price pressure that
takes seconds to propagate to prediction markets.

Entry Logic:
- Detect >$500k order flow imbalance on Kraken L2
- Confirm directional movement (>0.2% in 10 seconds)
- Enter Kalshi market in same direction within 1 second

Exit Logic:
- Exit when Kalshi spread normalizes (< 5 cents)
- Stop loss at -3% unrealized
- Max hold: 2 minutes

Expected Characteristics:
- Sharpe: 2.0-2.5 (if execution edge holds)
- Win Rate: 72%
- Avg Profit: $2.80/trade

Risk Factors:
- Execution speed critical (need <500ms total latency)
- Risk of false signals from spoofing
- Kalshi liquidity may not support desired size
- Edge degrades as market becomes more efficient
- Requires real-time orderbook data and fast execution
```

## Model Configuration

```python
# More creative (higher temperature)
creative_generator = HypothesisGeneratorAgent(
    model="claude-sonnet-4-5-20250929",
    temperature=0.9,
    max_tokens=4096
)

# More conservative (lower temperature)
conservative_generator = HypothesisGeneratorAgent(
    model="claude-sonnet-4-5-20250929",
    temperature=0.5,
    max_tokens=4096
)

# Use different model
opus_generator = HypothesisGeneratorAgent(
    model="claude-opus-4-6",
    temperature=0.7
)
```

## Best Practices

1. **Be Specific in Patterns**: More specific observations lead to better hypotheses
   - Bad: "Prices move a lot"
   - Good: "Prices spike 15% in the 5 minutes before expiry, then mean revert within 2 minutes"

2. **Provide Context**: Help the LLM understand your constraints
   ```python
   context = {
       "existing_strategies": ["latency arb", "market making"],
       "constraints": {
           "max_position": "$100",
           "no_overnight_holds": True,
           "available_data": ["L2 orderbook", "trades"]
       }
   }
   ```

3. **Iterate and Refine**: Use generated hypotheses as input for follow-up questions
   ```python
   # First pass
   hypotheses = generator.generate_from_pattern(pattern)

   # Refine best idea
   refined_pattern = {
       "market_type": pattern["market_type"],
       "observation": f"Building on '{hypotheses[0].name}': {pattern['observation']}",
       "statistics": pattern["statistics"]
   }
   refined = generator.generate_from_pattern(refined_pattern, num_hypotheses=2)
   ```

4. **Validate Before Implementing**: Generated hypotheses are starting points, not proven strategies
   - Validate theoretical basis with domain knowledge
   - Backtest on historical data
   - Paper trade before going live

5. **Rank Appropriately**: Adjust ranking criteria based on your goals
   ```python
   # Conservative portfolio: favor confidence over novelty
   safe_criteria = {"novelty": 0.1, "confidence": 0.7, "implementation": 0.2}

   # Research mode: favor novel ideas
   research_criteria = {"novelty": 0.7, "confidence": 0.2, "implementation": 0.1}
   ```

## Testing

Run the test suite:

```bash
# With API key (full tests)
export ANTHROPIC_API_KEY='your-key'
python3 test_hypothesis_generator.py

# Without API key (ranking tests only)
python3 test_hypothesis_generator.py
```

## Limitations

- **Not a Proven Strategy**: Generated hypotheses are ideas, not validated strategies
- **Requires Domain Knowledge**: Review output critically with market expertise
- **API Costs**: Each generation costs tokens (~$0.01-0.05 per request)
- **Quality Varies**: Use higher-quality models (Sonnet/Opus) for best results
- **No Backtesting**: Must integrate with `BacktestRunnerAgent` for validation

## Roadmap

- [ ] Integration with research tracking database
- [ ] Automatic hypothesis validation pipeline
- [ ] Multi-LLM consensus (query multiple models, aggregate results)
- [ ] Fine-tuning on successful trading strategies
- [ ] Integration with portfolio optimizer for strategy allocation
- [ ] Feedback loop: learn from backtest results

## See Also

- `DataScoutAgent`: Detect patterns in trading databases
- `BacktestRunnerAgent`: Validate hypotheses with historical data
- `ReportGeneratorAgent`: Generate research reports from results
