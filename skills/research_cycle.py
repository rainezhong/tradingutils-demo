#!/usr/bin/env python3
"""
Research Cycle Skill for Claude Code

This skill orchestrates the autonomous research pipeline using Claude Code's Task tool
for LLM reasoning instead of direct API calls.

Usage:
    claude run research cycle
    OR
    python3 skills/research_cycle.py

The skill:
1. Runs the Data Scout to discover patterns
2. When LLM reasoning is needed, signals Claude Code to spawn sub-agents
3. Processes LLM responses
4. Continues with hypothesis generation and backtesting
5. Returns comprehensive research summary
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.data_scout_llm import HybridDataScout


class ResearchCycleOrchestrator:
    """Orchestrates the full research cycle with LLM reasoning via Claude Code."""

    def __init__(self):
        self.data_scout = HybridDataScout()
        self.request_dir = Path("tmp/llm_requests")
        self.response_dir = Path("tmp/llm_responses")
        self.state_file = Path("tmp/research_state.json")

        # Ensure directories exist
        for d in [self.request_dir, self.response_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict:
        """
        Run one complete research cycle.

        Returns:
            Dictionary with research summary
        """
        print("\n" + "="*70)
        print(" "*20 + "🔬 RESEARCH CYCLE")
        print("="*70)
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

        # Save state
        self._save_state({"step": "data_scout", "status": "running"})

        # Step 1: Data Scout
        print("Step 1: Data Scout - Pattern Discovery")
        print("-" * 70)

        scout_result = self.data_scout.scan_for_patterns()

        if scout_result["status"] == "NEEDS_LLM":
            # Signal that LLM reasoning is needed
            print("\n⏸️  PAUSED: LLM Reasoning Required")
            print("="*70)
            print("\n📝 Request Details:")
            print(f"   Request file:  {scout_result['request_file']}")
            print(f"   Response file: {scout_result['response_file']}")
            print(f"\n   Data sources discovered: {len(scout_result['data_sources'])}")

            for ds in scout_result['data_sources']:
                print(f"   - {ds['name']}: {ds['description']}")

            print("\n🤖 Next Action:")
            print("   Claude Code will spawn a sub-agent to analyze these data sources")
            print("   and suggest trading edge comparisons.")
            print("\n   The agent will:")
            print("   1. Read the data source descriptions")
            print("   2. Identify correlations that could reveal edges")
            print("   3. Generate analysis plans (e.g., 'compare NOAA to Kalshi prices')")
            print("   4. Write response to:", scout_result['response_file'])

            # Return signal for Claude Code to process
            self._save_state({
                "step": "awaiting_llm",
                "llm_task": "data_scout_analysis_plans",
                "request_file": scout_result["request_file"],
                "response_file": scout_result["response_file"]
            })

            return {
                "status": "NEEDS_LLM",
                "step": "data_scout",
                "request_file": scout_result["request_file"],
                "response_file": scout_result["response_file"],
                "instruction": "spawn_analysis_plan_agent"
            }

        elif scout_result["status"] == "COMPLETE":
            # Data Scout completed
            hypotheses = scout_result["hypotheses"]
            print(f"\n✓ Data Scout Complete")
            print(f"   Patterns found: {len(hypotheses)}")

            # Display top findings
            for i, hyp in enumerate(hypotheses[:5], 1):
                print(f"\n   {i}. [{hyp.pattern_type}] {hyp.ticker}")
                print(f"      {hyp.description}")
                print(f"      Confidence: {hyp.confidence:.1%}, Significance: {hyp.statistical_significance:.2f}")

            # Step 2: Hypothesis Generation (would go here)
            print("\n\nStep 2: Hypothesis Generation")
            print("-" * 70)
            print("   TODO: Generate trading hypotheses from patterns")

            # Step 3: Backtesting (would go here)
            print("\n\nStep 3: Backtesting")
            print("-" * 70)
            print("   TODO: Run backtests on hypotheses")

            # Final summary
            print("\n" + "="*70)
            print("✓ Research Cycle Complete")
            print("="*70)

            summary = {
                "status": "COMPLETE",
                "hypotheses_found": len(hypotheses),
                "hypotheses": [
                    {
                        "pattern_type": h.pattern_type,
                        "ticker": h.ticker,
                        "description": h.description,
                        "confidence": h.confidence
                    }
                    for h in hypotheses[:10]
                ]
            }

            self._save_state({"step": "complete", "summary": summary})
            return summary

        else:
            # Unknown status
            print(f"⚠️  Unexpected status: {scout_result['status']}")
            return {"status": "ERROR", "message": scout_result}

    def resume_after_llm(self) -> dict:
        """
        Resume research cycle after LLM response is ready.

        Returns:
            Dictionary with next step or completion
        """
        print("\n🔄 Resuming research cycle after LLM reasoning...")

        # Re-run data scout (it will now find the response file)
        scout_result = self.data_scout.scan_for_patterns()

        if scout_result["status"] == "COMPLETE":
            return self.run()  # Continue with full cycle
        else:
            return scout_result

    def _save_state(self, state: dict):
        """Save orchestrator state to file."""
        state["timestamp"] = datetime.now().isoformat()
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _load_state(self) -> dict:
        """Load orchestrator state from file."""
        if self.state_file.exists():
            with open(self.state_file) as f:
                return json.load(f)
        return {}


def main():
    """Main entry point."""
    orchestrator = ResearchCycleOrchestrator()

    # Check if we're resuming after LLM
    state = orchestrator._load_state()

    if state.get("step") == "awaiting_llm":
        # Resuming
        result = orchestrator.resume_after_llm()
    else:
        # Fresh start
        result = orchestrator.run()

    # Print result
    print("\n" + "="*70)
    print("RESULT:")
    print(json.dumps(result, indent=2, default=str))
    print("="*70)

    return result


if __name__ == "__main__":
    main()
