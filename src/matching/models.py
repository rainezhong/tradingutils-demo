"""
Data Models for Cross-Platform Market Matching.

Defines the core data structures used throughout the matching pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class EntityType(Enum):
    """Types of entities that can be extracted from market titles."""
    TEAM = "team"
    PRICE_THRESHOLD = "price_threshold"
    INDEX = "index"
    CANDIDATE = "candidate"
    DATE = "date"
    CRYPTOCURRENCY = "cryptocurrency"
    NUMBER = "number"
    UNKNOWN = "unknown"


class MarketType(Enum):
    """Types of prediction markets."""
    BINARY = "binary"           # Yes/No outcome
    RANGE = "range"             # Price within a range (e.g., 6000-6050)
    MULTI_OUTCOME = "multi"     # Multiple possible outcomes


class MatchType(Enum):
    """Classification of match quality."""
    EXACT = "exact"             # >0.90 confidence - High confidence match
    EQUIVALENT = "equivalent"   # 0.75-0.90 - Good match, safe to trade
    RELATED = "related"         # 0.50-0.75 - Same event, different structure
    NO_MATCH = "no_match"       # <0.50 - Not a match


class Platform(Enum):
    """Supported prediction market platforms."""
    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


@dataclass
class ExtractedEntity:
    """An entity extracted from market text.

    Attributes:
        entity_type: The type of entity (team, price, index, etc.)
        raw_text: The original text that was matched
        normalized_form: The canonical form of the entity
        confidence: Confidence in the extraction (0.0-1.0)
        metadata: Additional entity-specific information
    """
    entity_type: EntityType
    raw_text: str
    normalized_form: str
    confidence: float = 1.0
    metadata: dict = field(default_factory=dict)

    def __hash__(self):
        return hash((self.entity_type, self.normalized_form))

    def __eq__(self, other):
        if not isinstance(other, ExtractedEntity):
            return False
        return (
            self.entity_type == other.entity_type and
            self.normalized_form == other.normalized_form
        )


@dataclass
class NormalizedMarket:
    """A market with normalized text and extracted entities.

    Attributes:
        platform: The platform this market is from
        market_id: Platform-specific identifier (ticker for Kalshi, token_id for Poly)
        original_title: The original market title
        normalized_title: The normalized/cleaned title
        entities: List of extracted entities
        category: Market category (sports, finance, politics, crypto)
        market_type: Binary, range, or multi-outcome
        close_time: When the market expires/resolves
        volume_24h: 24-hour trading volume (if available)
        created_at: When this normalized market was created
    """
    platform: Platform
    market_id: str
    original_title: str
    normalized_title: str
    entities: List[ExtractedEntity] = field(default_factory=list)
    category: Optional[str] = None
    market_type: MarketType = MarketType.BINARY
    close_time: Optional[datetime] = None
    volume_24h: Optional[float] = None
    created_at: datetime = field(default_factory=datetime.now)

    # Additional metadata
    yes_sub_title: Optional[str] = None  # For Kalshi markets
    description: Optional[str] = None

    def get_entities_by_type(self, entity_type: EntityType) -> List[ExtractedEntity]:
        """Get all entities of a specific type."""
        return [e for e in self.entities if e.entity_type == entity_type]

    def has_entity_type(self, entity_type: EntityType) -> bool:
        """Check if market has any entities of the given type."""
        return any(e.entity_type == entity_type for e in self.entities)

    @property
    def entity_set(self) -> set:
        """Get set of (type, normalized_form) tuples for comparison."""
        return {(e.entity_type, e.normalized_form) for e in self.entities}


@dataclass
class MatchedMarketPair:
    """A matched pair of markets across platforms.

    Attributes:
        kalshi_ticker: Kalshi market ticker
        poly_token_id: Polymarket token ID
        confidence: Overall match confidence (0.0-1.0)
        match_type: Classification of match quality
        kalshi_yes_equals_poly: "Yes" if same direction, "No" if inverted
        category: Market category
        matched_entities: List of entities that matched
        expiration_diff_hours: Difference in expiration times
        warnings: List of potential issues with this match

        # Score breakdown for debugging
        entity_score: Score from entity overlap
        semantic_score: Score from semantic similarity
        text_score: Score from fuzzy text matching
        temporal_score: Score from expiration alignment
        structural_score: Score from market type matching
        category_score: Category-specific bonus

        # Reference to normalized markets
        kalshi_market: The normalized Kalshi market
        poly_market: The normalized Polymarket market
    """
    kalshi_ticker: str
    poly_token_id: str
    confidence: float
    match_type: MatchType
    kalshi_yes_equals_poly: str  # "Yes" or "No"
    category: Optional[str]
    matched_entities: List[str]
    expiration_diff_hours: float
    warnings: List[str] = field(default_factory=list)

    # Score breakdown
    entity_score: float = 0.0
    semantic_score: float = 0.0
    text_score: float = 0.0
    temporal_score: float = 0.0
    structural_score: float = 0.0
    category_score: float = 0.0

    # References
    kalshi_market: Optional[NormalizedMarket] = None
    poly_market: Optional[NormalizedMarket] = None

    def __post_init__(self):
        """Set match type based on confidence."""
        if self.confidence >= 0.90:
            self.match_type = MatchType.EXACT
        elif self.confidence >= 0.75:
            self.match_type = MatchType.EQUIVALENT
        elif self.confidence >= 0.50:
            self.match_type = MatchType.RELATED
        else:
            self.match_type = MatchType.NO_MATCH

    @property
    def is_inverted(self) -> bool:
        """Check if the markets have inverted yes/no meanings."""
        return self.kalshi_yes_equals_poly == "No"

    @property
    def pair_id(self) -> str:
        """Generate unique ID for this pair."""
        return f"{self.kalshi_ticker}:{self.poly_token_id}"

    def score_breakdown(self) -> dict:
        """Return breakdown of confidence score components."""
        return {
            "entity_overlap": self.entity_score,
            "semantic_similarity": self.semantic_score,
            "text_similarity": self.text_score,
            "temporal_alignment": self.temporal_score,
            "structural_match": self.structural_score,
            "category_specific": self.category_score,
            "total": self.confidence,
        }


@dataclass
class CandidateMatch:
    """A candidate match before final scoring.

    Used internally during the matching process.
    """
    kalshi_market: NormalizedMarket
    poly_market: NormalizedMarket
    initial_score: float  # From text/semantic similarity
    blocked_by_category: bool = False

    @property
    def pair_key(self) -> str:
        return f"{self.kalshi_market.market_id}:{self.poly_market.market_id}"


# Confidence thresholds
CONFIDENCE_THRESHOLDS = {
    "exact": 0.90,      # High confidence match
    "equivalent": 0.75, # Good match, safe to trade
    "related": 0.50,    # Same event, different structure
}

# Score weights for confidence calculation
SCORE_WEIGHTS = {
    "entity_overlap": 0.30,
    "semantic_similarity": 0.25,
    "text_similarity": 0.15,
    "temporal_alignment": 0.15,
    "structural_match": 0.10,
    "category_specific": 0.05,
}
