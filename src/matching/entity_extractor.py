"""
Entity Extraction for Market Matching.

Extracts key entities (teams, prices, indices, candidates, dates)
from market titles for comparison and matching.
"""

import re
from datetime import datetime
from typing import List, Optional, Set, Tuple

from .models import ExtractedEntity, EntityType
from .normalizer import TextNormalizer
from .knowledge_bases.teams import get_team_canonical, get_all_aliases as get_team_aliases
from .knowledge_bases.indices import (
    get_index_canonical,
    get_all_aliases as get_index_aliases,
    is_cryptocurrency,
    get_asset_type,
)
from .knowledge_bases.candidates import (
    get_candidate_canonical,
    get_all_aliases as get_candidate_aliases,
)


class EntityExtractor:
    """Extract entities from market text."""

    def __init__(self, normalizer: Optional[TextNormalizer] = None):
        """Initialize the entity extractor.

        Args:
            normalizer: Text normalizer instance (created if not provided)
        """
        self.normalizer = normalizer or TextNormalizer()

        # Pre-compile regex patterns
        self._price_patterns = self._compile_price_patterns()
        self._date_patterns = self._compile_date_patterns()
        self._number_pattern = re.compile(r'\b(\d+(?:,\d{3})*(?:\.\d+)?)\b')

        # Build efficient alias lookups
        self._team_aliases = get_team_aliases()
        self._index_aliases = get_index_aliases()
        self._candidate_aliases = get_candidate_aliases()

    def _compile_price_patterns(self) -> List[Tuple[re.Pattern, str]]:
        """Compile regex patterns for price extraction."""
        return [
            # "above 6000", "over 6000", "greater than 6000", "> 6000"
            (re.compile(
                r'(?:above|over|greater\s+than|more\s+than|exceeds?|>\s*)\s*'
                r'\$?(\d+(?:,\d{3})*(?:\.\d+)?)',
                re.IGNORECASE
            ), 'above'),

            # "below 6000", "under 6000", "less than 6000", "< 6000"
            (re.compile(
                r'(?:below|under|less\s+than|fewer\s+than|<\s*)\s*'
                r'\$?(\d+(?:,\d{3})*(?:\.\d+)?)',
                re.IGNORECASE
            ), 'below'),

            # "at least 6000", "minimum 6000", ">= 6000"
            (re.compile(
                r'(?:at\s+least|minimum|min|>=\s*)\s*'
                r'\$?(\d+(?:,\d{3})*(?:\.\d+)?)',
                re.IGNORECASE
            ), 'at_least'),

            # "at most 6000", "maximum 6000", "<= 6000"
            (re.compile(
                r'(?:at\s+most|maximum|max|<=\s*)\s*'
                r'\$?(\d+(?:,\d{3})*(?:\.\d+)?)',
                re.IGNORECASE
            ), 'at_most'),

            # "between 6000 and 6050", "6000-6050"
            (re.compile(
                r'(?:between\s+)?\$?(\d+(?:,\d{3})*(?:\.\d+)?)\s*'
                r'(?:and|-|to)\s*'
                r'\$?(\d+(?:,\d{3})*(?:\.\d+)?)',
                re.IGNORECASE
            ), 'range'),

            # "$6000", "$100k", "$1M"
            (re.compile(
                r'\$(\d+(?:,\d{3})*(?:\.\d+)?)\s*([kKmMbBtT])?',
                re.IGNORECASE
            ), 'price'),
        ]

    def _compile_date_patterns(self) -> List[Tuple[re.Pattern, str]]:
        """Compile regex patterns for date extraction."""
        months = (
            r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|'
            r'may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|'
            r'oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)'
        )

        return [
            # "January 2025", "Jan 2025", "Jan. 2025"
            (re.compile(
                rf'({months})\.?\s+(\d{{4}})',
                re.IGNORECASE
            ), 'month_year'),

            # "2025-01", "2025/01"
            (re.compile(
                r'(\d{4})[-/](\d{1,2})',
                re.IGNORECASE
            ), 'year_month'),

            # "by January 2025", "before Jan 2025", "after December 2024"
            (re.compile(
                rf'(?:by|before|after|until|through)\s+({months})\.?\s+(\d{{4}})',
                re.IGNORECASE
            ), 'deadline'),

            # "Q1 2025", "Q4 2024"
            (re.compile(
                r'Q([1-4])\s*(\d{4})',
                re.IGNORECASE
            ), 'quarter'),

            # "end of year", "end of 2025", "EOY 2025"
            (re.compile(
                r'(?:end\s+of\s+(?:year|(\d{4}))|eoy\s*(\d{4})?)',
                re.IGNORECASE
            ), 'eoy'),

            # "December 31, 2025", "Dec 31 2025"
            (re.compile(
                rf'({months})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})',
                re.IGNORECASE
            ), 'full_date'),

            # "12/31/2025", "2025-12-31"
            (re.compile(
                r'(\d{1,2})/(\d{1,2})/(\d{4})|(\d{4})-(\d{1,2})-(\d{1,2})',
                re.IGNORECASE
            ), 'numeric_date'),
        ]

    def extract(self, text: str) -> List[ExtractedEntity]:
        """Extract all entities from text.

        Args:
            text: Market title or description

        Returns:
            List of extracted entities
        """
        entities = []

        # Extract each entity type
        entities.extend(self._extract_teams(text))
        entities.extend(self._extract_indices(text))
        entities.extend(self._extract_candidates(text))
        entities.extend(self._extract_prices(text))
        entities.extend(self._extract_dates(text))

        # Deduplicate while preserving order
        seen = set()
        unique_entities = []
        for entity in entities:
            key = (entity.entity_type, entity.normalized_form)
            if key not in seen:
                seen.add(key)
                unique_entities.append(entity)

        return unique_entities

    def _extract_teams(self, text: str) -> List[ExtractedEntity]:
        """Extract sports team entities."""
        entities = []
        text_lower = text.lower()

        # Check each known alias
        for alias in self._team_aliases:
            if self._word_in_text(alias, text_lower):
                canonical = get_team_canonical(alias)
                if canonical:
                    entities.append(ExtractedEntity(
                        entity_type=EntityType.TEAM,
                        raw_text=alias,
                        normalized_form=canonical,
                        confidence=1.0 if len(alias) > 3 else 0.8,
                        metadata={"alias_matched": alias}
                    ))

        return entities

    def _extract_indices(self, text: str) -> List[ExtractedEntity]:
        """Extract financial index and cryptocurrency entities."""
        entities = []
        text_lower = text.lower()

        # Check each known alias
        for alias in self._index_aliases:
            if self._word_in_text(alias, text_lower):
                canonical = get_index_canonical(alias)
                if canonical:
                    entity_type = EntityType.CRYPTOCURRENCY if is_cryptocurrency(alias) else EntityType.INDEX
                    asset_type = get_asset_type(alias)

                    entities.append(ExtractedEntity(
                        entity_type=entity_type,
                        raw_text=alias,
                        normalized_form=canonical,
                        confidence=1.0 if len(alias) > 3 else 0.85,
                        metadata={
                            "alias_matched": alias,
                            "asset_type": asset_type
                        }
                    ))

        return entities

    def _extract_candidates(self, text: str) -> List[ExtractedEntity]:
        """Extract political candidate entities."""
        entities = []
        text_lower = text.lower()

        # Check each known alias
        for alias in self._candidate_aliases:
            if self._word_in_text(alias, text_lower):
                canonical = get_candidate_canonical(alias)
                if canonical:
                    entities.append(ExtractedEntity(
                        entity_type=EntityType.CANDIDATE,
                        raw_text=alias,
                        normalized_form=canonical,
                        confidence=1.0 if len(alias) > 4 else 0.7,
                        metadata={"alias_matched": alias}
                    ))

        return entities

    def _extract_prices(self, text: str) -> List[ExtractedEntity]:
        """Extract price threshold entities."""
        entities = []

        for pattern, price_type in self._price_patterns:
            for match in pattern.finditer(text):
                if price_type == 'range':
                    low = self._parse_number(match.group(1))
                    high = self._parse_number(match.group(2))
                    normalized = f"range_{int(low)}_{int(high)}"
                    entities.append(ExtractedEntity(
                        entity_type=EntityType.PRICE_THRESHOLD,
                        raw_text=match.group(0),
                        normalized_form=normalized,
                        confidence=0.95,
                        metadata={
                            "type": "range",
                            "low": low,
                            "high": high
                        }
                    ))
                elif price_type == 'price':
                    value = self._parse_number(match.group(1))
                    suffix = match.group(2)
                    if suffix:
                        multipliers = {'k': 1000, 'm': 1000000, 'b': 1000000000, 't': 1000000000000}
                        value *= multipliers.get(suffix.lower(), 1)
                    normalized = f"price_{int(value)}"
                    entities.append(ExtractedEntity(
                        entity_type=EntityType.PRICE_THRESHOLD,
                        raw_text=match.group(0),
                        normalized_form=normalized,
                        confidence=0.9,
                        metadata={
                            "type": "exact",
                            "value": value
                        }
                    ))
                else:
                    value = self._parse_number(match.group(1))
                    normalized = f"{price_type}_{int(value)}"
                    entities.append(ExtractedEntity(
                        entity_type=EntityType.PRICE_THRESHOLD,
                        raw_text=match.group(0),
                        normalized_form=normalized,
                        confidence=0.95,
                        metadata={
                            "type": price_type,
                            "value": value
                        }
                    ))

        return entities

    def _extract_dates(self, text: str) -> List[ExtractedEntity]:
        """Extract date entities."""
        entities = []

        month_map = {
            'jan': 1, 'january': 1,
            'feb': 2, 'february': 2,
            'mar': 3, 'march': 3,
            'apr': 4, 'april': 4,
            'may': 5,
            'jun': 6, 'june': 6,
            'jul': 7, 'july': 7,
            'aug': 8, 'august': 8,
            'sep': 9, 'september': 9,
            'oct': 10, 'october': 10,
            'nov': 11, 'november': 11,
            'dec': 12, 'december': 12,
        }

        for pattern, date_type in self._date_patterns:
            for match in pattern.finditer(text):
                try:
                    if date_type == 'month_year':
                        month = month_map.get(match.group(1).lower()[:3], 0)
                        year = int(match.group(2))
                        normalized = f"{year}-{month:02d}"

                    elif date_type == 'year_month':
                        year = int(match.group(1))
                        month = int(match.group(2))
                        normalized = f"{year}-{month:02d}"

                    elif date_type == 'deadline':
                        month = month_map.get(match.group(1).lower()[:3], 0)
                        year = int(match.group(2))
                        normalized = f"by_{year}-{month:02d}"

                    elif date_type == 'quarter':
                        quarter = int(match.group(1))
                        year = int(match.group(2))
                        normalized = f"{year}-Q{quarter}"

                    elif date_type == 'eoy':
                        year = match.group(1) or match.group(2) or str(datetime.now().year)
                        normalized = f"{year}-12"

                    elif date_type == 'full_date':
                        month = month_map.get(match.group(1).lower()[:3], 0)
                        day = int(match.group(2))
                        year = int(match.group(3))
                        normalized = f"{year}-{month:02d}-{day:02d}"

                    elif date_type == 'numeric_date':
                        if match.group(4):  # YYYY-MM-DD format
                            year = int(match.group(4))
                            month = int(match.group(5))
                            day = int(match.group(6))
                        else:  # MM/DD/YYYY format
                            month = int(match.group(1))
                            day = int(match.group(2))
                            year = int(match.group(3))
                        normalized = f"{year}-{month:02d}-{day:02d}"

                    else:
                        continue

                    entities.append(ExtractedEntity(
                        entity_type=EntityType.DATE,
                        raw_text=match.group(0),
                        normalized_form=normalized,
                        confidence=0.9,
                        metadata={"date_type": date_type}
                    ))

                except (ValueError, IndexError):
                    continue

        return entities

    def _word_in_text(self, word: str, text: str) -> bool:
        """Check if word appears in text as a complete word.

        Args:
            word: Word to search for
            text: Text to search in

        Returns:
            True if word appears as a complete word
        """
        # Use word boundary matching
        pattern = r'\b' + re.escape(word) + r'\b'
        return bool(re.search(pattern, text, re.IGNORECASE))

    def _parse_number(self, text: str) -> float:
        """Parse a number string, removing commas."""
        return float(text.replace(',', ''))

    def extract_with_context(self, text: str) -> dict:
        """Extract entities with additional context.

        Args:
            text: Market title or description

        Returns:
            Dict with entities and context information
        """
        entities = self.extract(text)

        # Group by type
        by_type = {}
        for entity in entities:
            type_name = entity.entity_type.value
            if type_name not in by_type:
                by_type[type_name] = []
            by_type[type_name].append(entity)

        # Infer category
        category = self._infer_category(entities)

        # Check for comparison indicators
        has_comparison = any(e.entity_type == EntityType.PRICE_THRESHOLD for e in entities)

        # Check for "vs" or "versus" patterns
        has_versus = bool(re.search(r'\b(?:vs?\.?|versus)\b', text, re.IGNORECASE))

        return {
            "entities": entities,
            "by_type": by_type,
            "inferred_category": category,
            "has_price_comparison": has_comparison,
            "has_versus": has_versus,
            "entity_count": len(entities),
        }

    def _infer_category(self, entities: List[ExtractedEntity]) -> Optional[str]:
        """Infer market category from extracted entities."""
        has_team = any(e.entity_type == EntityType.TEAM for e in entities)
        has_crypto = any(
            e.entity_type == EntityType.CRYPTOCURRENCY for e in entities
        )
        has_index = any(
            e.entity_type == EntityType.INDEX for e in entities
        )
        has_candidate = any(e.entity_type == EntityType.CANDIDATE for e in entities)

        if has_team:
            return "sports"
        elif has_crypto:
            return "crypto"
        elif has_index:
            return "finance"
        elif has_candidate:
            return "politics"
        else:
            return None


def extract_entities(text: str) -> List[ExtractedEntity]:
    """Convenience function for quick entity extraction."""
    extractor = EntityExtractor()
    return extractor.extract(text)
