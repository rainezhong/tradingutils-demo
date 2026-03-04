"""LLM-powered hypothesis generator for trading strategies.

Generates structured trading hypotheses from data patterns or novel brainstorming.
Uses Claude to analyze anomalies and suggest exploitable trading opportunities.
"""

import json
import logging
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any
from enum import Enum

try:
    from anthropic import Anthropic
except ImportError:
    Anthropic = None

logger = logging.getLogger(__name__)


class MarketType(Enum):
    """Supported market types for hypothesis generation."""
    NBA = "nba"
    NCAAB = "ncaab"
    CRYPTO = "crypto"
    POLITICS = "politics"
    ECONOMICS = "economics"
    OTHER = "other"


class HypothesisConfidence(Enum):
    """Confidence levels for hypothesis viability."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class TradingHypothesis:
    """A structured trading strategy hypothesis.

    Attributes:
        name: Short name (e.g. "NBA Underdog Mispricing")
        description: Detailed explanation of the strategy
        theoretical_basis: Why this edge might exist
        market_type: Type of market (NBA, crypto, etc.)
        expected_sharpe: Estimated Sharpe ratio (if available)
        expected_win_rate: Estimated win rate percentage
        expected_avg_profit: Expected average profit per trade
        risk_factors: List of risks and failure modes
        entry_logic: How to identify entry opportunities
        exit_logic: How to manage exits
        confidence: Agent's confidence in hypothesis viability
        novelty_score: How novel/unique this hypothesis is (0-1)
        implementation_difficulty: Easy/Medium/Hard
        data_requirements: What data is needed to validate
        related_hypotheses: Similar or related strategy ideas
    """
    name: str
    description: str
    theoretical_basis: str
    market_type: str
    expected_sharpe: Optional[float] = None
    expected_win_rate: Optional[float] = None
    expected_avg_profit: Optional[float] = None
    risk_factors: List[str] = None
    entry_logic: str = ""
    exit_logic: str = ""
    confidence: str = HypothesisConfidence.MEDIUM.value
    novelty_score: float = 0.5
    implementation_difficulty: str = "medium"
    data_requirements: List[str] = None
    related_hypotheses: List[str] = None

    def __post_init__(self):
        """Initialize empty lists."""
        if self.risk_factors is None:
            self.risk_factors = []
        if self.data_requirements is None:
            self.data_requirements = []
        if self.related_hypotheses is None:
            self.related_hypotheses = []

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradingHypothesis":
        """Create from dictionary."""
        return cls(**data)


class HypothesisGeneratorAgent:
    """LLM-powered agent for generating trading hypotheses.

    Can operate in two modes:
    1. Pattern-based: Given a data anomaly, generate exploitable strategies
    2. Brainstorming: Generate novel trading ideas from scratch
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        api_key: YOUR_API_KEY_HERE[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.7
    ):
        """Initialize hypothesis generator.

        Args:
            model: Claude model to use
            api_key: YOUR_API_KEY_HERE API key (or set ANTHROPIC_API_KEY env var)
            max_tokens: Maximum tokens for response
            temperature: Sampling temperature (higher = more creative)
        """
        if Anthropic is None:
            raise ImportError(
                "anthropic package not installed. Install with: pip install anthropic"
            )

        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = Anthropic(api_key=api_key) if api_key else Anthropic()
        logger.info(f"Initialized HypothesisGeneratorAgent with model {model}")

    def generate_from_pattern(
        self,
        pattern_data: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        num_hypotheses: int = 3
    ) -> List[TradingHypothesis]:
        """Generate trading hypotheses from observed patterns/anomalies.

        Args:
            pattern_data: Description of observed pattern/anomaly
                Expected keys:
                - market_type: str (e.g. "NBA", "crypto")
                - observation: str (description of pattern)
                - statistics: dict (relevant statistics)
                - timeframe: str (when pattern occurs)
            context: Additional context (e.g. existing strategies, constraints)
            num_hypotheses: Number of hypotheses to generate

        Returns:
            List of TradingHypothesis objects, ranked by promise
        """
        logger.info(f"Generating {num_hypotheses} hypotheses from pattern")

        prompt = self._build_pattern_prompt(pattern_data, context, num_hypotheses)
        response = self._query_llm(prompt)
        hypotheses = self._parse_hypotheses(response)

        logger.info(f"Generated {len(hypotheses)} hypotheses from pattern")
        return hypotheses[:num_hypotheses]

    def brainstorm_hypotheses(
        self,
        market_type: Optional[str] = None,
        constraints: Optional[Dict[str, Any]] = None,
        num_hypotheses: int = 5
    ) -> List[TradingHypothesis]:
        """Generate novel trading hypotheses through brainstorming.

        Args:
            market_type: Optional focus on specific market (e.g. "NBA", "crypto")
            constraints: Optional constraints (e.g. max_holding_period, risk_tolerance)
            num_hypotheses: Number of hypotheses to generate

        Returns:
            List of TradingHypothesis objects, ranked by novelty and promise
        """
        logger.info(f"Brainstorming {num_hypotheses} novel hypotheses")

        prompt = self._build_brainstorm_prompt(market_type, constraints, num_hypotheses)
        response = self._query_llm(prompt)
        hypotheses = self._parse_hypotheses(response)

        logger.info(f"Generated {len(hypotheses)} hypotheses from brainstorming")
        return hypotheses[:num_hypotheses]

    def rank_hypotheses(
        self,
        hypotheses: List[TradingHypothesis],
        criteria: Optional[Dict[str, float]] = None
    ) -> List[TradingHypothesis]:
        """Rank hypotheses by novelty and promise.

        Args:
            hypotheses: List of hypotheses to rank
            criteria: Optional weighting for ranking criteria
                - novelty: Weight for novelty_score
                - confidence: Weight for confidence level
                - implementation: Weight for implementation difficulty (inverted)

        Returns:
            Sorted list of hypotheses (best first)
        """
        if criteria is None:
            criteria = {
                "novelty": 0.3,
                "confidence": 0.5,
                "implementation": 0.2
            }

        confidence_map = {
            HypothesisConfidence.HIGH.value: 1.0,
            HypothesisConfidence.MEDIUM.value: 0.6,
            HypothesisConfidence.LOW.value: 0.3
        }

        difficulty_map = {
            "easy": 1.0,
            "medium": 0.6,
            "hard": 0.3
        }

        def score_hypothesis(h: TradingHypothesis) -> float:
            novelty_score = h.novelty_score * criteria.get("novelty", 0.3)
            confidence_score = confidence_map.get(h.confidence, 0.5) * criteria.get("confidence", 0.5)
            implementation_score = difficulty_map.get(h.implementation_difficulty, 0.6) * criteria.get("implementation", 0.2)
            return novelty_score + confidence_score + implementation_score

        ranked = sorted(hypotheses, key=score_hypothesis, reverse=True)
        logger.info(f"Ranked {len(ranked)} hypotheses")
        return ranked

    def _build_pattern_prompt(
        self,
        pattern_data: Dict[str, Any],
        context: Optional[Dict[str, Any]],
        num_hypotheses: int
    ) -> str:
        """Build prompt for pattern-based hypothesis generation."""
        market_type = pattern_data.get("market_type", "unknown")
        observation = pattern_data.get("observation", "")
        statistics = pattern_data.get("statistics", {})
        timeframe = pattern_data.get("timeframe", "")

        context_str = ""
        if context:
            context_str = f"\n\nAdditional context:\n{json.dumps(context, indent=2)}"

        stats_str = ""
        if statistics:
            stats_str = f"\n\nStatistics:\n{json.dumps(statistics, indent=2)}"

        prompt = f"""You are an expert quantitative trading strategist analyzing prediction markets.

Given this observed pattern, suggest {num_hypotheses} trading strategies that could exploit it.

Market Type: {market_type}
Pattern: {observation}
Timeframe: {timeframe}{stats_str}{context_str}

For each strategy hypothesis, provide:

1. **Name**: Short, descriptive name (e.g. "NBA Underdog Mispricing")

2. **Description**: Detailed 2-3 sentence explanation of the strategy

3. **Theoretical Basis**: Why this edge might exist (market inefficiency, behavioral bias, structural advantage, information asymmetry, etc.)

4. **Entry Logic**: Specific conditions for entering positions (price thresholds, timing, filters)

5. **Exit Logic**: How to manage exits (time-based, price-based, event-based)

6. **Expected Characteristics**:
   - Estimated Sharpe ratio (be realistic, 0.5-2.0 typical)
   - Estimated win rate (percentage)
   - Expected average profit per trade (dollars)

7. **Risk Factors**: List 3-5 specific risks and failure modes

8. **Confidence**: Your confidence in this hypothesis (high/medium/low)

9. **Novelty Score**: How novel/unique this is (0.0-1.0, where 1.0 is completely novel)

10. **Implementation Difficulty**: easy/medium/hard

11. **Data Requirements**: What data is needed to validate this hypothesis

12. **Related Hypotheses**: Similar or complementary strategy ideas

Return your response as a JSON array of hypotheses with these exact field names:
- name
- description
- theoretical_basis
- entry_logic
- exit_logic
- expected_sharpe
- expected_win_rate
- expected_avg_profit
- risk_factors (array of strings)
- confidence (high/medium/low)
- novelty_score (float 0-1)
- implementation_difficulty (easy/medium/hard)
- data_requirements (array of strings)
- related_hypotheses (array of strings)
- market_type

Return ONLY valid JSON, no markdown formatting or explanation."""

        return prompt

    def _build_brainstorm_prompt(
        self,
        market_type: Optional[str],
        constraints: Optional[Dict[str, Any]],
        num_hypotheses: int
    ) -> str:
        """Build prompt for brainstorming novel hypotheses."""
        market_focus = f" in {market_type} markets" if market_type else ""

        constraints_str = ""
        if constraints:
            constraints_str = f"\n\nConstraints:\n{json.dumps(constraints, indent=2)}"

        prompt = f"""You are an expert quantitative trading strategist specializing in prediction markets.

Brainstorm {num_hypotheses} novel trading strategy ideas{market_focus}.

Focus on:
- Market microstructure inefficiencies
- Information asymmetries
- Behavioral biases
- Statistical anomalies
- Cross-market arbitrage
- Timing/latency advantages
- Sentiment/momentum patterns
- Mean reversion opportunities{constraints_str}

For each strategy hypothesis, provide:

1. **Name**: Short, descriptive name

2. **Description**: Detailed 2-3 sentence explanation

3. **Theoretical Basis**: Why this edge might exist

4. **Entry Logic**: Specific entry conditions

5. **Exit Logic**: Exit management approach

6. **Expected Characteristics**:
   - Estimated Sharpe ratio (realistic, 0.5-2.0 typical)
   - Estimated win rate (percentage)
   - Expected average profit per trade

7. **Risk Factors**: 3-5 specific risks and failure modes

8. **Confidence**: Your confidence in viability (high/medium/low)

9. **Novelty Score**: How novel this idea is (0.0-1.0)

10. **Implementation Difficulty**: easy/medium/hard

11. **Data Requirements**: Data needed for validation

12. **Related Hypotheses**: Similar/complementary ideas

Return your response as a JSON array with these exact field names:
- name
- description
- theoretical_basis
- entry_logic
- exit_logic
- expected_sharpe
- expected_win_rate
- expected_avg_profit
- risk_factors (array)
- confidence (high/medium/low)
- novelty_score (float)
- implementation_difficulty (easy/medium/hard)
- data_requirements (array)
- related_hypotheses (array)
- market_type

Return ONLY valid JSON, no markdown formatting."""

        return prompt

    def _query_llm(self, prompt: str) -> str:
        """Query Claude API with the given prompt.

        Args:
            prompt: The prompt to send

        Returns:
            Raw response text
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            # Extract text from response
            if response.content and len(response.content) > 0:
                return response.content[0].text
            else:
                logger.error("Empty response from Claude API")
                return "[]"

        except Exception as e:
            logger.error(f"Error querying Claude API: {e}")
            raise

    def _parse_hypotheses(self, response: str) -> List[TradingHypothesis]:
        """Parse LLM response into TradingHypothesis objects.

        Args:
            response: Raw JSON response from LLM

        Returns:
            List of TradingHypothesis objects
        """
        try:
            # Remove markdown code blocks if present
            response = response.strip()
            if response.startswith("```"):
                # Extract JSON from code block
                lines = response.split("\n")
                response = "\n".join(lines[1:-1])  # Remove first and last line
                if lines[0].strip().lower() == "```json":
                    pass  # Already stripped

            # Parse JSON
            data = json.loads(response)

            if not isinstance(data, list):
                logger.warning("Response is not a list, wrapping in array")
                data = [data]

            hypotheses = []
            for item in data:
                try:
                    # Ensure all required fields exist
                    h = TradingHypothesis(
                        name=item.get("name", "Unnamed Hypothesis"),
                        description=item.get("description", ""),
                        theoretical_basis=item.get("theoretical_basis", ""),
                        market_type=item.get("market_type", "other"),
                        expected_sharpe=item.get("expected_sharpe"),
                        expected_win_rate=item.get("expected_win_rate"),
                        expected_avg_profit=item.get("expected_avg_profit"),
                        risk_factors=item.get("risk_factors", []),
                        entry_logic=item.get("entry_logic", ""),
                        exit_logic=item.get("exit_logic", ""),
                        confidence=item.get("confidence", "medium"),
                        novelty_score=item.get("novelty_score", 0.5),
                        implementation_difficulty=item.get("implementation_difficulty", "medium"),
                        data_requirements=item.get("data_requirements", []),
                        related_hypotheses=item.get("related_hypotheses", [])
                    )
                    hypotheses.append(h)
                except Exception as e:
                    logger.warning(f"Failed to parse hypothesis: {e}")
                    continue

            return hypotheses

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Response was: {response}")
            return []
        except Exception as e:
            logger.error(f"Error parsing hypotheses: {e}")
            return []


def main():
    """Example usage of hypothesis generator."""
    logging.basicConfig(level=logging.INFO)

    # Initialize generator
    generator = HypothesisGeneratorAgent()

    # Example 1: Generate from observed pattern
    print("\n=== Pattern-Based Hypothesis Generation ===\n")
    pattern = {
        "market_type": "NBA",
        "observation": "Spreads widen to 10-15 cents in the 2 hours before game start",
        "statistics": {
            "historical_avg_spread": "3-5 cents",
            "widened_spread": "10-15 cents",
            "timeframe": "2 hours before game",
            "frequency": "~60% of games"
        },
        "timeframe": "2 hours before game start"
    }

    hypotheses = generator.generate_from_pattern(pattern, num_hypotheses=2)
    for i, h in enumerate(hypotheses, 1):
        print(f"\nHypothesis {i}: {h.name}")
        print(f"Description: {h.description}")
        print(f"Theoretical Basis: {h.theoretical_basis}")
        print(f"Expected Sharpe: {h.expected_sharpe}")
        print(f"Expected Win Rate: {h.expected_win_rate}%")
        print(f"Confidence: {h.confidence}")
        print(f"Novelty: {h.novelty_score}")

    # Example 2: Brainstorm novel ideas
    print("\n\n=== Brainstorming Novel Hypotheses ===\n")
    novel_ideas = generator.brainstorm_hypotheses(
        market_type="crypto",
        num_hypotheses=2
    )

    for i, h in enumerate(novel_ideas, 1):
        print(f"\nIdea {i}: {h.name}")
        print(f"Description: {h.description}")
        print(f"Risk Factors: {', '.join(h.risk_factors[:2])}")

    # Example 3: Rank hypotheses
    print("\n\n=== Ranking Hypotheses ===\n")
    all_hypotheses = hypotheses + novel_ideas
    ranked = generator.rank_hypotheses(all_hypotheses)

    print("\nRanked by promise:")
    for i, h in enumerate(ranked, 1):
        print(f"{i}. {h.name} (confidence: {h.confidence}, novelty: {h.novelty_score:.2f})")


if __name__ == "__main__":
    main()
