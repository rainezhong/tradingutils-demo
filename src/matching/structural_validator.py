"""
Structural Validation for Market Matching.

Validates that two markets are structurally compatible:
- Same market type (binary vs range)
- Temporal alignment (close times within tolerance)
- Outcome mapping (detect inversions)
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from .models import NormalizedMarket, MarketType, ExtractedEntity, EntityType


@dataclass
class ValidationResult:
    """Result of structural validation between two markets."""
    is_valid: bool
    structural_score: float  # 0.0-1.0
    temporal_score: float    # 0.0-1.0
    is_inverted: bool        # True if YES on one = NO on other
    expiration_diff_hours: float
    warnings: List[str]
    details: dict


class StructuralValidator:
    """Validate structural compatibility between markets."""

    def __init__(
        self,
        max_expiration_diff_hours: float = 24.0,
        allow_range_binary_match: bool = False,
    ):
        """Initialize the structural validator.

        Args:
            max_expiration_diff_hours: Maximum allowed difference in close times
            allow_range_binary_match: Whether to allow matching range to binary markets
        """
        self.max_expiration_diff_hours = max_expiration_diff_hours
        self.allow_range_binary_match = allow_range_binary_match

    def validate(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> ValidationResult:
        """Validate structural compatibility between two markets.

        Args:
            market_1: First market (typically Kalshi)
            market_2: Second market (typically Polymarket)

        Returns:
            ValidationResult with scores and details
        """
        warnings = []
        details = {}

        # Check market type compatibility
        type_compatible, type_score, type_details = self._check_market_types(
            market_1, market_2
        )
        details["market_type"] = type_details

        if not type_compatible:
            warnings.append(f"Incompatible market types: {market_1.market_type.value} vs {market_2.market_type.value}")

        # Check temporal alignment
        temp_score, exp_diff, temp_details = self._check_temporal_alignment(
            market_1, market_2
        )
        details["temporal"] = temp_details

        if exp_diff > self.max_expiration_diff_hours:
            warnings.append(f"Expiration times differ by {exp_diff:.1f} hours")

        # Check for inversion (YES on one = NO on other)
        is_inverted, inversion_details = self._detect_inversion(market_1, market_2)
        details["inversion"] = inversion_details

        if is_inverted:
            warnings.append("Markets appear to be inverted (Kalshi YES = Poly NO)")

        # Check price threshold compatibility
        price_compatible, price_details = self._check_price_compatibility(
            market_1, market_2
        )
        details["price"] = price_details

        if not price_compatible:
            warnings.append("Price thresholds may not be compatible")

        # Calculate overall validity
        is_valid = type_compatible and exp_diff <= self.max_expiration_diff_hours

        # Calculate structural score (weighted combination)
        structural_score = (
            type_score * 0.6 +
            (1.0 if price_compatible else 0.5) * 0.4
        )

        return ValidationResult(
            is_valid=is_valid,
            structural_score=structural_score,
            temporal_score=temp_score,
            is_inverted=is_inverted,
            expiration_diff_hours=exp_diff,
            warnings=warnings,
            details=details,
        )

    def _check_market_types(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[bool, float, dict]:
        """Check if market types are compatible.

        Returns:
            Tuple of (is_compatible, score, details)
        """
        type_1 = market_1.market_type
        type_2 = market_2.market_type

        details = {
            "market_1_type": type_1.value,
            "market_2_type": type_2.value,
        }

        # Same type = perfect compatibility
        if type_1 == type_2:
            return True, 1.0, {**details, "match": "exact"}

        # Binary vs Binary (should be caught above)
        if type_1 == MarketType.BINARY and type_2 == MarketType.BINARY:
            return True, 1.0, {**details, "match": "exact"}

        # Range vs Binary - only allow if configured
        if (
            (type_1 == MarketType.RANGE and type_2 == MarketType.BINARY) or
            (type_1 == MarketType.BINARY and type_2 == MarketType.RANGE)
        ):
            if self.allow_range_binary_match:
                return True, 0.7, {**details, "match": "range_binary_allowed"}
            else:
                return False, 0.3, {**details, "match": "range_binary_blocked"}

        # Multi-outcome markets need special handling
        if type_1 == MarketType.MULTI_OUTCOME or type_2 == MarketType.MULTI_OUTCOME:
            return False, 0.2, {**details, "match": "multi_outcome_not_supported"}

        return False, 0.0, {**details, "match": "incompatible"}

    def _check_temporal_alignment(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[float, float, dict]:
        """Check if market expiration times are aligned.

        Returns:
            Tuple of (temporal_score, hours_difference, details)
        """
        close_1 = market_1.close_time
        close_2 = market_2.close_time

        details = {
            "market_1_close": close_1.isoformat() if close_1 else None,
            "market_2_close": close_2.isoformat() if close_2 else None,
        }

        # If either is missing, can't validate - give benefit of doubt
        if close_1 is None or close_2 is None:
            return 0.5, 0.0, {**details, "status": "missing_close_time"}

        # Calculate difference in hours
        diff = abs((close_1 - close_2).total_seconds()) / 3600.0

        # Score based on difference
        if diff <= 1.0:
            score = 1.0
        elif diff <= 6.0:
            score = 0.9
        elif diff <= 24.0:
            score = 0.7
        elif diff <= 72.0:
            score = 0.4
        else:
            score = 0.1

        return score, diff, {
            **details,
            "diff_hours": diff,
            "status": "compared"
        }

    def _detect_inversion(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[bool, dict]:
        """Detect if markets have inverted YES/NO meanings.

        For example:
        - Kalshi: "Team A to win" (YES = Team A wins)
        - Poly: "Team B to win" (YES = Team B wins)
        These are inverted: Kalshi YES = Poly NO

        Returns:
            Tuple of (is_inverted, details)
        """
        details = {}

        # Get team entities from both markets
        teams_1 = market_1.get_entities_by_type(EntityType.TEAM)
        teams_2 = market_2.get_entities_by_type(EntityType.TEAM)

        if teams_1 and teams_2:
            # Check if teams are mentioned in opposite order
            team_names_1 = [t.normalized_form for t in teams_1]
            team_names_2 = [t.normalized_form for t in teams_2]

            details["teams_1"] = team_names_1
            details["teams_2"] = team_names_2

            # If same teams but first mentioned is different, likely inverted
            if len(team_names_1) >= 1 and len(team_names_2) >= 1:
                if team_names_1[0] != team_names_2[0]:
                    # Check if it's the same teams mentioned
                    if set(team_names_1[:2]) == set(team_names_2[:2]):
                        return True, {
                            **details,
                            "reason": "teams_reversed",
                            "inverted": True
                        }

        # Check for opposite price thresholds
        prices_1 = market_1.get_entities_by_type(EntityType.PRICE_THRESHOLD)
        prices_2 = market_2.get_entities_by_type(EntityType.PRICE_THRESHOLD)

        if prices_1 and prices_2:
            for p1 in prices_1:
                for p2 in prices_2:
                    if self._are_opposite_thresholds(p1, p2):
                        return True, {
                            **details,
                            "reason": "opposite_thresholds",
                            "threshold_1": p1.normalized_form,
                            "threshold_2": p2.normalized_form,
                            "inverted": True
                        }

        # Check for negation patterns in titles
        title_1_lower = market_1.normalized_title.lower()
        title_2_lower = market_2.normalized_title.lower()

        negation_patterns = [
            (r'\bnot\b', r'\bwill\b'),  # "will not" vs "will"
            (r'\bfail\b', r'\bsucceed\b'),
            (r'\blose\b', r'\bwin\b'),
            (r'\bbelow\b', r'\babove\b'),
            (r'\bunder\b', r'\bover\b'),
            (r'\bless\b', r'\bmore\b'),
        ]

        for neg, pos in negation_patterns:
            has_neg_1 = bool(re.search(neg, title_1_lower))
            has_pos_1 = bool(re.search(pos, title_1_lower))
            has_neg_2 = bool(re.search(neg, title_2_lower))
            has_pos_2 = bool(re.search(pos, title_2_lower))

            if (has_neg_1 and has_pos_2) or (has_pos_1 and has_neg_2):
                return True, {
                    **details,
                    "reason": "negation_pattern",
                    "pattern": f"{neg}/{pos}",
                    "inverted": True
                }

        return False, {**details, "inverted": False}

    def _are_opposite_thresholds(
        self,
        entity_1: ExtractedEntity,
        entity_2: ExtractedEntity,
    ) -> bool:
        """Check if two price threshold entities are opposites.

        For example: "above 6000" and "below 6000" are opposites.
        """
        type_1 = entity_1.metadata.get("type", "")
        type_2 = entity_2.metadata.get("type", "")

        opposites = {
            ("above", "below"),
            ("below", "above"),
            ("at_least", "at_most"),
            ("at_most", "at_least"),
        }

        if (type_1, type_2) in opposites:
            # Check if values are similar (within 5%)
            val_1 = entity_1.metadata.get("value", 0)
            val_2 = entity_2.metadata.get("value", 0)

            if val_1 > 0 and val_2 > 0:
                diff_pct = abs(val_1 - val_2) / max(val_1, val_2)
                return diff_pct < 0.05

        return False

    def _check_price_compatibility(
        self,
        market_1: NormalizedMarket,
        market_2: NormalizedMarket,
    ) -> Tuple[bool, dict]:
        """Check if price thresholds are compatible.

        Returns:
            Tuple of (is_compatible, details)
        """
        prices_1 = market_1.get_entities_by_type(EntityType.PRICE_THRESHOLD)
        prices_2 = market_2.get_entities_by_type(EntityType.PRICE_THRESHOLD)

        details = {
            "prices_1": [p.normalized_form for p in prices_1],
            "prices_2": [p.normalized_form for p in prices_2],
        }

        # If neither has prices, that's fine
        if not prices_1 and not prices_2:
            return True, {**details, "status": "no_prices"}

        # If only one has prices, might be incompatible
        if bool(prices_1) != bool(prices_2):
            return False, {**details, "status": "one_missing_prices"}

        # Check if any prices match
        for p1 in prices_1:
            for p2 in prices_2:
                if self._prices_match(p1, p2):
                    return True, {
                        **details,
                        "status": "prices_match",
                        "matched": (p1.normalized_form, p2.normalized_form)
                    }

        # No matching prices found
        return False, {**details, "status": "prices_differ"}

    def _prices_match(
        self,
        entity_1: ExtractedEntity,
        entity_2: ExtractedEntity,
    ) -> bool:
        """Check if two price entities match (same type and similar value)."""
        type_1 = entity_1.metadata.get("type", "")
        type_2 = entity_2.metadata.get("type", "")

        # Types must match
        if type_1 != type_2:
            return False

        # Values must be close (within 1%)
        val_1 = entity_1.metadata.get("value", 0)
        val_2 = entity_2.metadata.get("value", 0)

        if val_1 > 0 and val_2 > 0:
            diff_pct = abs(val_1 - val_2) / max(val_1, val_2)
            return diff_pct < 0.01

        # For ranges, check both bounds
        if type_1 == "range":
            low_1 = entity_1.metadata.get("low", 0)
            high_1 = entity_1.metadata.get("high", 0)
            low_2 = entity_2.metadata.get("low", 0)
            high_2 = entity_2.metadata.get("high", 0)

            if low_1 > 0 and low_2 > 0:
                low_match = abs(low_1 - low_2) / max(low_1, low_2) < 0.01
                high_match = abs(high_1 - high_2) / max(high_1, high_2) < 0.01
                return low_match and high_match

        return False


def detect_market_type(title: str) -> MarketType:
    """Infer market type from title.

    Args:
        title: Market title

    Returns:
        Inferred MarketType
    """
    title_lower = title.lower()

    # Check for range indicators
    range_patterns = [
        r'between\s+\d+\s+and\s+\d+',
        r'\d+\s*-\s*\d+',
        r'\d+\s+to\s+\d+',
        r'in the range',
    ]

    for pattern in range_patterns:
        if re.search(pattern, title_lower):
            return MarketType.RANGE

    # Check for multi-outcome indicators
    multi_patterns = [
        r'which\s+of',
        r'who\s+will\s+win',
        r'select\s+one',
        r'choose\s+from',
    ]

    for pattern in multi_patterns:
        if re.search(pattern, title_lower):
            return MarketType.MULTI_OUTCOME

    # Default to binary
    return MarketType.BINARY


def validate_markets(
    market_1: NormalizedMarket,
    market_2: NormalizedMarket,
) -> ValidationResult:
    """Convenience function for quick validation."""
    validator = StructuralValidator()
    return validator.validate(market_1, market_2)
