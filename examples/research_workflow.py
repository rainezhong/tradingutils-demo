#!/usr/bin/env python3
"""Example: Complete Research Workflow

Demonstrates the full research pipeline:
1. DataScoutAgent detects patterns in trading database
2. HypothesisGeneratorAgent generates trading strategies from patterns
3. Rank and export top hypotheses for backtesting

This is the core autonomous research loop.
"""

import os
import sys
import json
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.data_scout import DataScoutAgent, Hypothesis
from agents.hypothesis_generator import (
    HypothesisGeneratorAgent,
    TradingHypothesis,
    MarketType,
    HypothesisConfidence
)


def pattern_to_hypothesis_input(pattern: Hypothesis) -> dict:
    """Convert DataScout pattern to HypothesisGenerator input format."""
    # Map pattern types to market types
    market_type_map = {
        'spread_anomaly': 'crypto',
        'price_movement': 'crypto',
        'mean_reversion': 'crypto',
        'momentum': 'crypto'
    }

    return {
        "market_type": market_type_map.get(pattern.pattern_type, "other"),
        "observation": pattern.description,
        "statistics": {
            "pattern_type": pattern.pattern_type,
            "ticker": pattern.ticker,
            "confidence": pattern.confidence,
            "statistical_significance": pattern.statistical_significance,
            "data_points": pattern.data_points,
            **pattern.metadata
        },
        "timeframe": "historical"
    }


def main():
    print("\n" + "="*80)
    print("AUTONOMOUS RESEARCH WORKFLOW")
    print("="*80 + "\n")

    # Configuration
    db_path = "data/btc_latency_probe.db"
    min_snapshots = 100
    num_hypotheses_per_pattern = 2
    top_patterns_to_analyze = 3

    # Check for database
    if not os.path.exists(db_path):
        print(f"❌ Database not found: {db_path}")
        print("Run the latency probe first to collect data.")
        return

    # Check for API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key: YOUR_API_KEY_HERE("❌ ANTHROPIC_API_KEY not set")
        print("Set it to run the full workflow:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        return

    # Step 1: Detect Patterns
    print("STEP 1: PATTERN DETECTION")
    print("-" * 80)

    with DataScoutAgent(db_path=db_path) as scout:
        patterns = scout.scan_for_patterns(min_snapshots=min_snapshots)

    print(f"\nFound {len(patterns)} patterns")

    if not patterns:
        print("No patterns detected. Collect more data and try again.")
        return

    # Show top patterns
    print(f"\nTop {min(5, len(patterns))} patterns by confidence:\n")
    for i, pattern in enumerate(patterns[:5], 1):
        print(f"{i}. [{pattern.pattern_type}] {pattern.ticker}")
        print(f"   {pattern.description}")
        print(f"   Confidence: {pattern.confidence:.2%}, "
              f"Significance: {pattern.statistical_significance:.4f}, "
              f"N={pattern.data_points}")
        print()

    # Step 2: Generate Hypotheses
    print("\nSTEP 2: HYPOTHESIS GENERATION")
    print("-" * 80)

    generator = HypothesisGeneratorAgent(temperature=0.7)
    all_hypotheses = []

    for i, pattern in enumerate(patterns[:top_patterns_to_analyze], 1):
        print(f"\n[{i}/{top_patterns_to_analyze}] Analyzing pattern: "
              f"{pattern.pattern_type} - {pattern.ticker}")

        # Convert to hypothesis input
        pattern_data = pattern_to_hypothesis_input(pattern)

        try:
            # Generate trading strategies
            hypotheses = generator.generate_from_pattern(
                pattern_data=pattern_data,
                num_hypotheses=num_hypotheses_per_pattern
            )

            print(f"  Generated {len(hypotheses)} hypotheses:")
            for h in hypotheses:
                print(f"    - {h.name} (confidence: {h.confidence}, "
                      f"novelty: {h.novelty_score:.2f})")

            # Tag with source pattern
            for h in hypotheses:
                h.related_hypotheses.append(f"Source: {pattern.ticker} - {pattern.pattern_type}")

            all_hypotheses.extend(hypotheses)

        except Exception as e:
            print(f"  ❌ Error generating hypotheses: {e}")
            continue

    print(f"\n✅ Generated {len(all_hypotheses)} total hypotheses")

    if not all_hypotheses:
        print("No hypotheses generated. Check API key and try again.")
        return

    # Step 3: Rank Hypotheses
    print("\n\nSTEP 3: HYPOTHESIS RANKING")
    print("-" * 80)

    # Rank by balanced criteria
    ranked = generator.rank_hypotheses(
        all_hypotheses,
        criteria={
            "novelty": 0.3,
            "confidence": 0.5,
            "implementation": 0.2
        }
    )

    print("\nTop 5 Hypotheses (ranked by promise):\n")
    for i, h in enumerate(ranked[:5], 1):
        print(f"\n{i}. {h.name}")
        print(f"   Confidence: {h.confidence} | Novelty: {h.novelty_score:.2f} | "
              f"Difficulty: {h.implementation_difficulty}")
        print(f"\n   {h.description}")
        print(f"\n   Theoretical Basis:")
        print(f"   {h.theoretical_basis}")
        print(f"\n   Expected Characteristics:")
        print(f"   - Sharpe: {h.expected_sharpe}")
        print(f"   - Win Rate: {h.expected_win_rate}%")
        print(f"   - Avg Profit: ${h.expected_avg_profit}")
        print(f"\n   Top Risk Factors:")
        for risk in h.risk_factors[:3]:
            print(f"   - {risk}")
        print()

    # Step 4: Export Results
    print("\nSTEP 4: EXPORT RESULTS")
    print("-" * 80)

    output_dir = Path("research_output")
    output_dir.mkdir(exist_ok=True)

    # Export all hypotheses
    all_output = output_dir / "all_hypotheses.json"
    with open(all_output, "w") as f:
        json.dump([h.to_dict() for h in all_hypotheses], f, indent=2)
    print(f"\n✅ Saved all hypotheses to: {all_output}")

    # Export top ranked
    top_output = output_dir / "top_hypotheses.json"
    with open(top_output, "w") as f:
        json.dump([h.to_dict() for h in ranked[:5]], f, indent=2)
    print(f"✅ Saved top 5 hypotheses to: {top_output}")

    # Export patterns for reference
    patterns_output = output_dir / "source_patterns.json"
    pattern_dicts = [
        {
            "pattern_type": p.pattern_type,
            "ticker": p.ticker,
            "description": p.description,
            "confidence": p.confidence,
            "significance": p.statistical_significance,
            "data_points": p.data_points,
            "metadata": p.metadata
        }
        for p in patterns[:top_patterns_to_analyze]
    ]
    with open(patterns_output, "w") as f:
        json.dump(pattern_dicts, f, indent=2)
    print(f"✅ Saved source patterns to: {patterns_output}")

    # Summary report
    print("\n\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    print(f"\nPatterns detected: {len(patterns)}")
    print(f"Patterns analyzed: {top_patterns_to_analyze}")
    print(f"Hypotheses generated: {len(all_hypotheses)}")
    print(f"Top hypothesis: {ranked[0].name}")
    print(f"  - Confidence: {ranked[0].confidence}")
    print(f"  - Expected Sharpe: {ranked[0].expected_sharpe}")
    print(f"  - Implementation: {ranked[0].implementation_difficulty}")

    print("\n\nNEXT STEPS:")
    print("1. Review top hypotheses in research_output/top_hypotheses.json")
    print("2. Design backtest for most promising ideas")
    print("3. Validate with BacktestRunnerAgent")
    print("4. Paper trade before deploying live")

    print("\n✅ Research workflow complete!\n")


if __name__ == "__main__":
    main()
