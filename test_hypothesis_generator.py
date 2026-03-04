#!/usr/bin/env python3
"""Test script for HypothesisGeneratorAgent.

This tests the LLM-powered hypothesis generator in both modes:
1. Pattern-based generation (from observed anomalies)
2. Brainstorming mode (novel ideas)
"""

import os
import sys
import json
from agents.hypothesis_generator import (
    HypothesisGeneratorAgent,
    TradingHypothesis,
    MarketType,
    HypothesisConfidence
)


def test_pattern_based_generation():
    """Test hypothesis generation from observed patterns."""
    print("\n" + "="*80)
    print("TEST 1: Pattern-Based Hypothesis Generation")
    print("="*80)

    # Initialize generator
    generator = HypothesisGeneratorAgent(temperature=0.7)

    # Example pattern: NBA spread widening
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

    context = {
        "existing_strategies": ["NBA underdog betting", "late game blowout"],
        "constraints": {
            "max_position_size": 100,
            "max_daily_loss": 200
        }
    }

    try:
        hypotheses = generator.generate_from_pattern(
            pattern_data=pattern,
            context=context,
            num_hypotheses=2
        )

        print(f"\nGenerated {len(hypotheses)} hypotheses:\n")

        for i, h in enumerate(hypotheses, 1):
            print(f"\n{'─'*80}")
            print(f"Hypothesis {i}: {h.name}")
            print(f"{'─'*80}")
            print(f"\nDescription:")
            print(f"  {h.description}")
            print(f"\nTheoretical Basis:")
            print(f"  {h.theoretical_basis}")
            print(f"\nEntry Logic:")
            print(f"  {h.entry_logic}")
            print(f"\nExit Logic:")
            print(f"  {h.exit_logic}")
            print(f"\nExpected Characteristics:")
            print(f"  - Sharpe Ratio: {h.expected_sharpe}")
            print(f"  - Win Rate: {h.expected_win_rate}%")
            print(f"  - Avg Profit: ${h.expected_avg_profit}")
            print(f"\nRisk Factors:")
            for risk in h.risk_factors:
                print(f"  - {risk}")
            print(f"\nMeta:")
            print(f"  - Confidence: {h.confidence}")
            print(f"  - Novelty Score: {h.novelty_score}")
            print(f"  - Implementation: {h.implementation_difficulty}")
            print(f"  - Market Type: {h.market_type}")

        # Save to file
        output_file = "/tmp/hypothesis_pattern_test.json"
        with open(output_file, "w") as f:
            json.dump([h.to_dict() for h in hypotheses], f, indent=2)
        print(f"\n\nSaved hypotheses to: {output_file}")

        return True

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_brainstorming_mode():
    """Test brainstorming novel hypotheses."""
    print("\n" + "="*80)
    print("TEST 2: Brainstorming Novel Hypotheses")
    print("="*80)

    generator = HypothesisGeneratorAgent(temperature=0.8)

    constraints = {
        "max_holding_period": "7 days",
        "min_sharpe": 1.0,
        "focus": "market microstructure and timing advantages"
    }

    try:
        hypotheses = generator.brainstorm_hypotheses(
            market_type="crypto",
            constraints=constraints,
            num_hypotheses=2
        )

        print(f"\nGenerated {len(hypotheses)} novel hypotheses:\n")

        for i, h in enumerate(hypotheses, 1):
            print(f"\n{'─'*80}")
            print(f"Hypothesis {i}: {h.name}")
            print(f"{'─'*80}")
            print(f"\nDescription:")
            print(f"  {h.description}")
            print(f"\nTheoretical Basis:")
            print(f"  {h.theoretical_basis}")
            print(f"\nData Requirements:")
            for req in h.data_requirements:
                print(f"  - {req}")
            print(f"\nRelated Hypotheses:")
            for related in h.related_hypotheses:
                print(f"  - {related}")

        # Save to file
        output_file = "/tmp/hypothesis_brainstorm_test.json"
        with open(output_file, "w") as f:
            json.dump([h.to_dict() for h in hypotheses], f, indent=2)
        print(f"\n\nSaved hypotheses to: {output_file}")

        return True

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_ranking():
    """Test hypothesis ranking."""
    print("\n" + "="*80)
    print("TEST 3: Hypothesis Ranking")
    print("="*80)

    # Create some mock hypotheses
    hypotheses = [
        TradingHypothesis(
            name="High Confidence, Low Novelty",
            description="A safe bet",
            theoretical_basis="Well established",
            market_type="NBA",
            confidence=HypothesisConfidence.HIGH.value,
            novelty_score=0.3,
            implementation_difficulty="easy"
        ),
        TradingHypothesis(
            name="Medium Everything",
            description="Balanced approach",
            theoretical_basis="Some evidence",
            market_type="NBA",
            confidence=HypothesisConfidence.MEDIUM.value,
            novelty_score=0.5,
            implementation_difficulty="medium"
        ),
        TradingHypothesis(
            name="Novel but Risky",
            description="Experimental idea",
            theoretical_basis="Unproven but promising",
            market_type="crypto",
            confidence=HypothesisConfidence.LOW.value,
            novelty_score=0.9,
            implementation_difficulty="hard"
        ),
    ]

    generator = HypothesisGeneratorAgent()

    # Rank with default criteria
    ranked = generator.rank_hypotheses(hypotheses)

    print("\nRanked hypotheses (default criteria):")
    for i, h in enumerate(ranked, 1):
        print(f"{i}. {h.name} (conf: {h.confidence}, novelty: {h.novelty_score}, impl: {h.implementation_difficulty})")

    # Rank favoring novelty
    novelty_criteria = {
        "novelty": 0.7,
        "confidence": 0.2,
        "implementation": 0.1
    }
    ranked_novelty = generator.rank_hypotheses(hypotheses, criteria=novelty_criteria)

    print("\nRanked hypotheses (favoring novelty):")
    for i, h in enumerate(ranked_novelty, 1):
        print(f"{i}. {h.name} (conf: {h.confidence}, novelty: {h.novelty_score}, impl: {h.implementation_difficulty})")

    return True


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "="*78 + "╗")
    print("║" + " "*20 + "Hypothesis Generator Test Suite" + " "*26 + "║")
    print("╚" + "="*78 + "╝")

    # Check for API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key: YOUR_API_KEY_HERE("\n⚠️  WARNING: ANTHROPIC_API_KEY not found in environment")
        print("Please set it to run LLM-based tests:")
        print("  export ANTHROPIC_API_KEY='your-key-here'")
        print("\nSkipping LLM tests, running local tests only...\n")

        # Only run ranking test (doesn't need LLM)
        test_ranking()
        return

    results = []

    # Test 1: Pattern-based generation
    results.append(("Pattern-based generation", test_pattern_based_generation()))

    # Test 2: Brainstorming
    results.append(("Brainstorming mode", test_brainstorming_mode()))

    # Test 3: Ranking
    results.append(("Hypothesis ranking", test_ranking()))

    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    for test_name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {test_name}")

    all_passed = all(result for _, result in results)
    print("\n" + ("="*80))
    if all_passed:
        print("🎉 All tests passed!")
    else:
        print("⚠️  Some tests failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
