"""
Confidence Scoring for Market Matching.

Multi-signal scoring system that combines:
- Entity overlap
- Semantic similarity
- Text similarity
- Temporal alignment
- Structural match
- Category-specific bonuses
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .models import (
    NormalizedMarket,
    MatchedMarketPair,
    ExtractedEntity,
    EntityType,
    MatchType,
    SCORE_WEIGHTS,
    CONFIDENCE_THRESHOLDS,
)
from .structural_validator import StructuralValidator, ValidationResult

# Try to import rapidfuzz for text similarity
try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

# Try to import sentence-transformers for semantic similarity
try:
    from sentence_transformers import SentenceTransformer
    import numpy as np
    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    HAS_SENTENCE_TRANSFORMERS = False


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of confidence score components."""
    entity_score: float
    semantic_score: float
    text_score: float
    temporal_score: float
    structural_score: float
    category_score: float
    total_score: float
    details: dict


class ConfidenceScorer:
    """Calculate confidence scores for market matches."""

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        use_semantic: bool = True,
        semantic_model: str = "all-MiniLM-L6-v2",
    ):
        """Initialize the confidence scorer.

        Args:
            weights: Custom score weights (defaults to SCORE_WEIGHTS)
            use_semantic: Whether to use semantic similarity (requires sentence-transformers)
            semantic_model: Model name for sentence-transformers
        """
        self.weights = weights or SCORE_WEIGHTS
        self.use_semantic = use_semantic and HAS_SENTENCE_TRANSFORMERS
        self.structural_validator = StructuralValidator()

        # Lazy load semantic model
        self._semantic_model = None
        self._semantic_model_name = semantic_model

    @property
    def semantic_model(self):
        """Lazy load the semantic model."""
        if self._semantic_model is None and self.use_semantic:
            try:
                self._semantic_model = SentenceTransformer(self._semantic_model_name)
            except Exception:
                self.use_semantic = False
        return self._semantic_model

    def score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
        validation_result: Optional[ValidationResult] = None,
    ) -> ScoreBreakdown:
        """Calculate confidence score for a potential match.

        Args:
            market_1: First market (typically Kalshi)
            market_2: Second market (typically Polymarket)
            validation_result: Pre-computed validation result (computed if not provided)

        Returns:
            ScoreBreakdown with detailed component scores
        """
        details = {}

        # Get validation result if not provided
        if validation_result is None:
            validation_result = self.structural_validator.validate(market_1, market_2)

        # Calculate component scores
        entity_score, entity_details = self._calculate_entity_score(market_1, market_2)
        details["entity"] = entity_details

        semantic_score, semantic_details = self._calculate_semantic_score(market_1, market_2)
        details["semantic"] = semantic_details

        text_score, text_details = self._calculate_text_score(market_1, market_2)
        details["text"] = text_details

        temporal_score = validation_result.temporal_score
        details["temporal"] = {"score": temporal_score}

        structural_score = validation_result.structural_score
        details["structural"] = {"score": structural_score}

        category_score, category_details = self._calculate_category_score(market_1, market_2)
        details["category"] = category_details

        # Calculate weighted total
        total_score = (
            entity_score * self.weights["entity_overlap"] +
            semantic_score * self.weights["semantic_similarity"] +
            text_score * self.weights["text_similarity"] +
            temporal_score * self.weights["temporal_alignment"] +
            structural_score * self.weights["structural_match"] +
            category_score * self.weights["category_specific"]
        )

        # Clamp to [0, 1]
        total_score = max(0.0, min(1.0, total_score))

        return ScoreBreakdown(
            entity_score=entity_score,
            semantic_score=semantic_score,
            text_score=text_score,
            temporal_score=temporal_score,
            structural_score=structural_score,
            category_score=category_score,
            total_score=total_score,
            details=details,
        )

    def _calculate_entity_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[float, dict]:
        """Calculate entity overlap score.

        Uses Jaccard similarity of entity sets.
        """
        set_1 = market_1.entity_set
        set_2 = market_2.entity_set

        details = {
            "entities_1": len(set_1),
            "entities_2": len(set_2),
        }

        if not set_1 and not set_2:
            # No entities to compare - neutral score
            return 0.5, {**details, "overlap": "none", "score": 0.5}

        if not set_1 or not set_2:
            # Only one has entities - low score
            return 0.2, {**details, "overlap": "one_empty", "score": 0.2}

        # Calculate Jaccard similarity
        intersection = set_1 & set_2
        union = set_1 | set_2

        jaccard = len(intersection) / len(union) if union else 0.0

        # Boost score if high-value entities match (teams, indices)
        high_value_types = {EntityType.TEAM, EntityType.INDEX, EntityType.CRYPTOCURRENCY}
        matched_high_value = [
            e for e in intersection
            if e[0] in high_value_types
        ]

        if matched_high_value:
            jaccard = min(1.0, jaccard + 0.2)

        details.update({
            "intersection": len(intersection),
            "union": len(union),
            "jaccard": jaccard,
            "matched_entities": [str(e) for e in intersection],
            "high_value_matches": len(matched_high_value),
        })

        return jaccard, details

    def _calculate_semantic_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[float, dict]:
        """Calculate semantic similarity using embeddings."""
        if not self.use_semantic or self.semantic_model is None:
            # Fall back to text similarity
            return self._calculate_text_score(market_1, market_2)

        try:
            # Get embeddings
            text_1 = market_1.normalized_title
            text_2 = market_2.normalized_title

            embeddings = self.semantic_model.encode([text_1, text_2])
            emb_1, emb_2 = embeddings[0], embeddings[1]

            # Calculate cosine similarity
            similarity = np.dot(emb_1, emb_2) / (
                np.linalg.norm(emb_1) * np.linalg.norm(emb_2)
            )

            # Convert from [-1, 1] to [0, 1]
            score = (similarity + 1) / 2

            return score, {
                "method": "sentence_transformer",
                "model": self._semantic_model_name,
                "cosine_similarity": float(similarity),
                "score": float(score),
            }

        except Exception as e:
            # Fall back to text similarity
            text_score, text_details = self._calculate_text_score(market_1, market_2)
            return text_score, {
                **text_details,
                "semantic_error": str(e),
                "fallback": True,
            }

    def _calculate_text_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[float, dict]:
        """Calculate text similarity using fuzzy matching."""
        text_1 = market_1.normalized_title
        text_2 = market_2.normalized_title

        if not HAS_RAPIDFUZZ:
            # Simple fallback: word overlap
            words_1 = set(text_1.lower().split())
            words_2 = set(text_2.lower().split())

            if not words_1 or not words_2:
                return 0.0, {"method": "word_overlap", "score": 0.0}

            overlap = len(words_1 & words_2) / len(words_1 | words_2)
            return overlap, {
                "method": "word_overlap",
                "overlap": overlap,
                "score": overlap,
            }

        # Use rapidfuzz for multiple similarity metrics
        ratio = fuzz.ratio(text_1, text_2) / 100.0
        partial_ratio = fuzz.partial_ratio(text_1, text_2) / 100.0
        token_sort_ratio = fuzz.token_sort_ratio(text_1, text_2) / 100.0
        token_set_ratio = fuzz.token_set_ratio(text_1, text_2) / 100.0

        # Weighted combination (token_set_ratio is most robust for our use case)
        score = (
            ratio * 0.15 +
            partial_ratio * 0.20 +
            token_sort_ratio * 0.25 +
            token_set_ratio * 0.40
        )

        return score, {
            "method": "rapidfuzz",
            "ratio": ratio,
            "partial_ratio": partial_ratio,
            "token_sort_ratio": token_sort_ratio,
            "token_set_ratio": token_set_ratio,
            "score": score,
        }

    def _calculate_category_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[float, dict]:
        """Calculate category-specific bonus score."""
        cat_1 = market_1.category
        cat_2 = market_2.category

        details = {
            "category_1": cat_1,
            "category_2": cat_2,
        }

        # Categories must match for bonus
        if cat_1 and cat_2 and cat_1 == cat_2:
            # Apply category-specific rules
            if cat_1 == "sports":
                score = self._sports_category_score(market_1, market_2)
            elif cat_1 == "finance":
                score = self._finance_category_score(market_1, market_2)
            elif cat_1 == "crypto":
                score = self._crypto_category_score(market_1, market_2)
            elif cat_1 == "politics":
                score = self._politics_category_score(market_1, market_2)
            else:
                score = 0.8  # Same category, but no specific rules

            return score, {**details, "match": True, "score": score}

        # Categories don't match
        if cat_1 and cat_2:
            return 0.0, {**details, "match": False, "score": 0.0}

        # Missing categories - neutral
        return 0.5, {**details, "match": "unknown", "score": 0.5}

    def _sports_category_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> float:
        """Sports-specific scoring rules."""
        teams_1 = market_1.get_entities_by_type(EntityType.TEAM)
        teams_2 = market_2.get_entities_by_type(EntityType.TEAM)

        if not teams_1 or not teams_2:
            return 0.5

        # Check if same teams are mentioned
        team_set_1 = {t.normalized_form for t in teams_1}
        team_set_2 = {t.normalized_form for t in teams_2}

        if team_set_1 == team_set_2:
            return 1.0  # Exact team match
        elif team_set_1 & team_set_2:
            return 0.8  # Partial team overlap
        else:
            return 0.3  # Different teams

    def _finance_category_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> float:
        """Finance-specific scoring rules."""
        indices_1 = market_1.get_entities_by_type(EntityType.INDEX)
        indices_2 = market_2.get_entities_by_type(EntityType.INDEX)
        prices_1 = market_1.get_entities_by_type(EntityType.PRICE_THRESHOLD)
        prices_2 = market_2.get_entities_by_type(EntityType.PRICE_THRESHOLD)

        score = 0.5

        # Check index match
        if indices_1 and indices_2:
            index_set_1 = {i.normalized_form for i in indices_1}
            index_set_2 = {i.normalized_form for i in indices_2}
            if index_set_1 == index_set_2:
                score += 0.3

        # Check price threshold match
        if prices_1 and prices_2:
            # Get first price from each
            p1 = prices_1[0].metadata.get("value", 0)
            p2 = prices_2[0].metadata.get("value", 0)

            if p1 > 0 and p2 > 0:
                diff_pct = abs(p1 - p2) / max(p1, p2)
                if diff_pct < 0.01:  # Within 1%
                    score += 0.2
                elif diff_pct < 0.05:  # Within 5%
                    score += 0.1

        return min(1.0, score)

    def _crypto_category_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> float:
        """Crypto-specific scoring rules."""
        crypto_1 = market_1.get_entities_by_type(EntityType.CRYPTOCURRENCY)
        crypto_2 = market_2.get_entities_by_type(EntityType.CRYPTOCURRENCY)
        prices_1 = market_1.get_entities_by_type(EntityType.PRICE_THRESHOLD)
        prices_2 = market_2.get_entities_by_type(EntityType.PRICE_THRESHOLD)

        score = 0.5

        # Check crypto match
        if crypto_1 and crypto_2:
            crypto_set_1 = {c.normalized_form for c in crypto_1}
            crypto_set_2 = {c.normalized_form for c in crypto_2}
            if crypto_set_1 == crypto_set_2:
                score += 0.3

        # Check price threshold match
        if prices_1 and prices_2:
            p1 = prices_1[0].metadata.get("value", 0)
            p2 = prices_2[0].metadata.get("value", 0)

            if p1 > 0 and p2 > 0:
                diff_pct = abs(p1 - p2) / max(p1, p2)
                if diff_pct < 0.01:
                    score += 0.2
                elif diff_pct < 0.05:
                    score += 0.1

        return min(1.0, score)

    def _politics_category_score(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> float:
        """Politics-specific scoring rules."""
        candidates_1 = market_1.get_entities_by_type(EntityType.CANDIDATE)
        candidates_2 = market_2.get_entities_by_type(EntityType.CANDIDATE)

        if not candidates_1 or not candidates_2:
            return 0.5

        candidate_set_1 = {c.normalized_form for c in candidates_1}
        candidate_set_2 = {c.normalized_form for c in candidates_2}

        if candidate_set_1 == candidate_set_2:
            return 1.0
        elif candidate_set_1 & candidate_set_2:
            return 0.8
        else:
            return 0.3

    def create_matched_pair(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
        score_breakdown: ScoreBreakdown,
        validation_result: ValidationResult,
    ) -> MatchedMarketPair:
        """Create a MatchedMarketPair from scoring results.

        Args:
            market_1: Kalshi market
            market_2: Polymarket
            score_breakdown: Calculated score breakdown
            validation_result: Structural validation result

        Returns:
            MatchedMarketPair object
        """
        # Determine match type from score
        confidence = score_breakdown.total_score
        if confidence >= CONFIDENCE_THRESHOLDS["exact"]:
            match_type = MatchType.EXACT
        elif confidence >= CONFIDENCE_THRESHOLDS["equivalent"]:
            match_type = MatchType.EQUIVALENT
        elif confidence >= CONFIDENCE_THRESHOLDS["related"]:
            match_type = MatchType.RELATED
        else:
            match_type = MatchType.NO_MATCH

        # Get matched entities
        matched_entities = []
        if "matched_entities" in score_breakdown.details.get("entity", {}):
            matched_entities = score_breakdown.details["entity"]["matched_entities"]

        return MatchedMarketPair(
            kalshi_ticker=market_1.market_id,
            poly_token_id=market_2.market_id,
            confidence=confidence,
            match_type=match_type,
            kalshi_yes_equals_poly="No" if validation_result.is_inverted else "Yes",
            category=market_1.category or market_2.category,
            matched_entities=matched_entities,
            expiration_diff_hours=validation_result.expiration_diff_hours,
            warnings=validation_result.warnings,
            entity_score=score_breakdown.entity_score,
            semantic_score=score_breakdown.semantic_score,
            text_score=score_breakdown.text_score,
            temporal_score=score_breakdown.temporal_score,
            structural_score=score_breakdown.structural_score,
            category_score=score_breakdown.category_score,
            kalshi_market=market_1,
            poly_market=market_2,
        )


def calculate_confidence(
    market_1: NormalizedMarket,
    market_2: NormalizedMarket,
) -> float:
    """Convenience function for quick confidence calculation."""
    scorer = ConfidenceScorer(use_semantic=False)  # Faster without semantic
    breakdown = scorer.score(market_1, market_2)
    return breakdown.total_score
