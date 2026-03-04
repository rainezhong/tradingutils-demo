"""
LLM Agent Handler for Research Cycle

This module is called by Claude Code to process LLM reasoning requests.
It reads request files, uses Claude Code's context to reason, and writes responses.

This is the bridge between Python code and Claude Code's Task tool.
"""

import json
from pathlib import Path
from typing import Dict, Any, List


class LLMAgentHandler:
    """Handles LLM reasoning requests for the research pipeline."""

    def __init__(self):
        self.request_dir = Path("tmp/llm_requests")
        self.response_dir = Path("tmp/llm_responses")

    def process_data_scout_request(self, request_file: Path) -> Dict[str, Any]:
        """
        Process a Data Scout analysis plan request.

        This is called BY Claude Code, which has the full context and can
        spawn sub-agents to do the reasoning.

        Args:
            request_file: Path to the request JSON file

        Returns:
            Response dictionary to write back
        """
        # Read request
        with open(request_file) as f:
            request = json.load(f)

        data_sources = request.get("data_sources", [])
        instructions = request.get("instructions", "")

        print(f"\n📥 Processing LLM request: Data Scout Analysis Plans")
        print(f"   Data sources: {len(data_sources)}")

        # Format data sources for LLM
        ds_summary = "\n".join([
            f"- **{ds['name']}** ({ds['path']})\n"
            f"  Description: {ds['description']}\n"
            f"  Tables: {', '.join(ds['tables'])}\n"
            f"  Rows: {ds['row_count']}\n"
            f"  Date range: {ds.get('date_range', 'unknown')}\n"
            for ds in data_sources
        ])

        # This would be the prompt for a sub-agent
        prompt = f"""
You are a quantitative research analyst analyzing data sources for trading edges.

Available Data Sources:
{ds_summary}

Your task:
{instructions}

Analyze these data sources and generate analysis plans. For each potential edge:

1. **Which data sources should be compared?**
   - Identify sources that SHOULD correlate (e.g., forecasts vs markets)
   - Look for timing mismatches (latency opportunities)

2. **What defines an "edge"?**
   - Specific metric or condition (e.g., "NOAA prob > market price")

3. **Why might this edge exist?**
   - Information asymmetry
   - Behavioral bias
   - Latency differences
   - Market inefficiency

4. **What pattern indicates opportunity?**
   - Describe the signal clearly

5. **How to test statistically?**
   - T-test, correlation, regression, etc.

Return JSON with this structure:
```json
{{
  "analysis_plans": [
    {{
      "name": "Weather Forecast Arbitrage",
      "data_sources": ["weather_markets", "noaa_forecasts"],
      "comparison_type": "forecast_vs_market_price",
      "edge_definition": "noaa_confidence > market_implied_probability + 0.30",
      "reasoning": "Retail traders use Weather.com, not raw NWS data that determines settlement",
      "expected_pattern": "Markets priced < 15¢ when NOAA shows > 70% confidence",
      "statistical_test": "Compare forecast accuracy to market calibration, look for systematic bias",
      "join_conditions": {{"on": ["timestamp", "city", "temp_bucket"]}}
    }}
  ]
}}
```

Focus on discovering edges in the data that actually exists. Be specific and actionable.
"""

        # Return the prompt (Claude Code will use this with Task tool)
        return {
            "type": "data_scout_analysis_plans",
            "prompt": prompt,
            "expected_response_format": "JSON with analysis_plans array",
            "data_sources_analyzed": len(data_sources)
        }

    def process_hypothesis_generation_request(self, request_file: Path) -> Dict[str, Any]:
        """Process a hypothesis generation request."""
        with open(request_file) as f:
            request = json.load(f)

        patterns = request.get("patterns", [])

        prompt = f"""
You are a quantitative trader generating trading strategy hypotheses from data patterns.

Detected Patterns:
{json.dumps(patterns, indent=2)}

For each pattern, generate a structured trading hypothesis:

1. **Name**: Short, descriptive name
2. **Description**: Detailed strategy explanation
3. **Theoretical Basis**: WHY the edge exists
4. **Entry/Exit Logic**: Specific rules
5. **Expected Metrics**: Sharpe, win rate, avg profit estimates
6. **Risk Factors**: What could go wrong
7. **Data Requirements**: What you need to backtest it

Return JSON with TradingHypothesis objects.
"""

        return {
            "type": "hypothesis_generation",
            "prompt": prompt,
            "expected_response_format": "JSON with hypotheses array",
            "patterns_analyzed": len(patterns)
        }

    def create_sample_response(self, response_type: str) -> Dict[str, Any]:
        """
        Create a sample response for testing.

        In production, Claude Code will spawn a sub-agent to generate real responses.
        This is just for testing the file-based interface.
        """
        if response_type == "data_scout_analysis_plans":
            return {
                "analysis_plans": [
                    {
                        "name": "Weather Forecast Arbitrage",
                        "data_sources": ["weather_markets", "noaa_forecasts"],
                        "comparison_type": "forecast_vs_market_price",
                        "edge_definition": "noaa_confidence > market_price/100 + 0.30",
                        "reasoning": "Retail Kalshi traders use consumer weather apps (Weather.com, AccuWeather) which are less accurate than NOAA gridpoint forecasts used for settlement. This creates information asymmetry.",
                        "expected_pattern": "Markets priced < 15¢ when NOAA shows > 70% confidence",
                        "statistical_test": "Compare NOAA forecast accuracy to market-implied probabilities, test for systematic underpricing when NOAA confidence is high",
                        "join_conditions": {"on": ["timestamp", "city", "temp_bucket"]}
                    },
                    {
                        "name": "BTC Latency Arbitrage",
                        "data_sources": ["btc_latency_probe"],
                        "comparison_type": "latency_arbitrage",
                        "edge_definition": "kraken_price_change detected before kalshi_price_change",
                        "reasoning": "Kraken spot market updates faster than Kalshi derivative markets. If Kraken shows BTC pump, Kalshi lags by 2-10 seconds.",
                        "expected_pattern": "Kraken price movement > 0.5% followed by Kalshi delay",
                        "statistical_test": "Measure time delta between Kraken price change and Kalshi market adjustment, calculate edge decay rate",
                        "join_conditions": {"on": ["timestamp_window"]}
                    }
                ]
            }
        else:
            return {"error": "Unknown response type"}


def main():
    """Test the handler."""
    handler = LLMAgentHandler()

    # Check for pending requests
    request_dir = Path("tmp/llm_requests")
    if not request_dir.exists():
        print("No requests pending")
        return

    for request_file in request_dir.glob("*.json"):
        print(f"\nProcessing: {request_file}")

        if "data_scout" in request_file.name:
            response_info = handler.process_data_scout_request(request_file)
            print("\nGenerated prompt for Claude Code:")
            print("-" * 70)
            print(response_info["prompt"])
            print("-" * 70)

            # For testing, write a sample response
            print("\n⚠️  TEST MODE: Writing sample response")
            sample_response = handler.create_sample_response("data_scout_analysis_plans")

            response_file = Path("tmp/llm_responses") / "data_scout_response.json"
            response_file.parent.mkdir(parents=True, exist_ok=True)

            with open(response_file, "w") as f:
                json.dump(sample_response, f, indent=2)

            print(f"✓ Sample response written to: {response_file}")


if __name__ == "__main__":
    main()
