"""
Cross-Platform Market Matching Module.

Automated matching of equivalent markets between Kalshi and Polymarket
for cross-platform arbitrage detection.

Usage:
------
from src.matching import MarketMatcher, MatchedMarketPair

# Create matcher
matcher = MarketMatcher()

# Get matched pairs
pairs = matcher.match_markets(kalshi_markets, poly_markets)

for pair in pairs:
    print(f"{pair.kalshi_ticker} <-> {pair.poly_token_id}")
    print(f"  Confidence: {pair.confidence:.2f}")
    print(f"  Category: {pair.category}")
"""

from .models import (
    NormalizedMarket,
    MatchedMarketPair,
    ExtractedEntity,
    EntityType,
    MarketType,
    MatchType,
)
from .normalizer import TextNormalizer
from .entity_extractor import EntityExtractor
from .structural_validator import StructuralValidator
from .confidence_scorer import ConfidenceScorer
from .matcher import MarketMatcher, MatcherConfig
from .quote_provider import QuoteProvider, LiveQuoteMarketMatcher, QuoteProviderConfig

__all__ = [
    # Models
    "NormalizedMarket",
    "MatchedMarketPair",
    "ExtractedEntity",
    "EntityType",
    "MarketType",
    "MatchType",
    # Components
    "TextNormalizer",
    "EntityExtractor",
    "StructuralValidator",
    "ConfidenceScorer",
    "MarketMatcher",
    "MatcherConfig",
    # Quote Provider (for SpreadDetector integration)
    "QuoteProvider",
    "QuoteProviderConfig",
    "LiveQuoteMarketMatcher",
]
