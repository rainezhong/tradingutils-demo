"""Correlation detector for identifying related markets."""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from src.core import MarketDatabase, Market, setup_logger

logger = setup_logger(__name__)


@dataclass
class CorrelationMatch:
    """Represents a potential correlation between markets."""

    category: str
    markets: List[str] = field(default_factory=list)
    keywords_matched: Set[str] = field(default_factory=set)


class CorrelationDetector:
    """
    Detects markets with overlapping keywords that may be correlated.

    Flags potential correlations for manual review based on keyword
    categories like fed_rates, employment, politics, weather, etc.
    """

    # Keyword categories and their associated terms
    KEYWORD_CATEGORIES: Dict[str, List[str]] = {
        "fed_rates": [
            "fed", "fomc", "interest rate", "federal reserve",
            "rate cut", "rate hike", "monetary policy", "powell",
            "basis point", "bps",
        ],
        "employment": [
            "jobs", "employment", "unemployment", "labor",
            "nonfarm", "payroll", "jobless", "hiring",
            "workforce", "wage",
        ],
        "politics": [
            "election", "president", "congress", "senate",
            "house", "democrat", "republican", "vote",
            "poll", "candidate", "primary", "biden", "trump",
        ],
        "weather": [
            "hurricane", "storm", "temperature", "weather",
            "rain", "snow", "flood", "drought", "climate",
            "celsius", "fahrenheit",
        ],
        "economy": [
            "gdp", "inflation", "cpi", "recession",
            "growth", "economic", "treasury", "yield",
            "debt", "deficit",
        ],
        "crypto": [
            "bitcoin", "btc", "ethereum", "eth", "crypto",
            "blockchain", "token", "coin",
        ],
        "sports": [
            "nfl", "nba", "mlb", "nhl", "soccer",
            "football", "basketball", "baseball", "hockey",
            "championship", "playoff", "super bowl", "world series",
        ],
        "tech": [
            "ai", "artificial intelligence", "tech", "software",
            "apple", "google", "microsoft", "amazon", "meta",
            "earnings", "stock",
        ],
    }

    def __init__(self, db: Optional[MarketDatabase] = None):
        """
        Initialize the correlation detector.

        Args:
            db: MarketDatabase instance
        """
        self.db = db or MarketDatabase()
        # Compile regex patterns for efficiency
        self._compiled_patterns: Dict[str, List[re.Pattern]] = {}
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for keyword matching."""
        for category, keywords in self.KEYWORD_CATEGORIES.items():
            self._compiled_patterns[category] = [
                re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
                for kw in keywords
            ]

    def detect_correlations(self) -> Dict[str, CorrelationMatch]:
        """
        Detect all potential correlations across markets.

        Returns:
            Dictionary mapping category names to CorrelationMatch objects
        """
        markets = self.db.get_all_markets()

        if not markets:
            logger.warning("No markets found in database")
            return {}

        # Group markets by category
        category_matches: Dict[str, CorrelationMatch] = {}

        for market in markets:
            matched_categories = self._categorize_market(market)

            for category, keywords in matched_categories.items():
                if category not in category_matches:
                    category_matches[category] = CorrelationMatch(
                        category=category,
                        markets=[],
                        keywords_matched=set(),
                    )

                category_matches[category].markets.append(market.ticker)
                category_matches[category].keywords_matched.update(keywords)

        # Filter to only categories with multiple markets (potential correlation)
        correlated = {
            cat: match
            for cat, match in category_matches.items()
            if len(match.markets) > 1
        }

        logger.info(
            f"Found {len(correlated)} categories with potential correlations"
        )

        return correlated

    def get_correlated_markets(self, ticker: str) -> List[Dict[str, any]]:
        """
        Find markets potentially correlated with a specific ticker.

        Args:
            ticker: Market ticker to find correlations for

        Returns:
            List of dicts with correlated market info and shared categories
        """
        market = self.db.get_market(ticker)
        if not market:
            logger.warning(f"Market {ticker} not found")
            return []

        # Get categories for this market
        market_categories = self._categorize_market(market)

        if not market_categories:
            return []

        # Find other markets in same categories
        all_markets = self.db.get_all_markets()
        correlated = []

        for other in all_markets:
            if other.ticker == ticker:
                continue

            other_categories = self._categorize_market(other)
            shared = set(market_categories.keys()) & set(other_categories.keys())

            if shared:
                correlated.append({
                    "ticker": other.ticker,
                    "title": other.title,
                    "shared_categories": list(shared),
                    "shared_keywords": list(
                        set().union(*[market_categories[c] for c in shared])
                        & set().union(*[other_categories[c] for c in shared])
                    ),
                })

        return correlated

    def get_category_report(self) -> List[Dict[str, any]]:
        """
        Generate a report of all categories and their markets.

        Returns:
            List of dicts with category info, sorted by market count
        """
        correlations = self.detect_correlations()

        report = []
        for category, match in correlations.items():
            report.append({
                "category": category,
                "market_count": len(match.markets),
                "markets": match.markets,
                "keywords_found": list(match.keywords_matched),
            })

        # Sort by market count descending
        report.sort(key=lambda x: x["market_count"], reverse=True)

        return report

    def flag_for_review(self, min_markets: int = 3) -> List[Dict[str, any]]:
        """
        Flag categories with many correlated markets for manual review.

        Args:
            min_markets: Minimum number of markets to flag category

        Returns:
            List of flagged categories with details
        """
        correlations = self.detect_correlations()

        flagged = []
        for category, match in correlations.items():
            if len(match.markets) >= min_markets:
                flagged.append({
                    "category": category,
                    "market_count": len(match.markets),
                    "markets": match.markets,
                    "keywords_found": list(match.keywords_matched),
                    "review_reason": (
                        f"High correlation risk: {len(match.markets)} markets "
                        f"share {category} keywords"
                    ),
                })

        flagged.sort(key=lambda x: x["market_count"], reverse=True)

        logger.info(f"Flagged {len(flagged)} categories for manual review")

        return flagged

    def _categorize_market(self, market: Market) -> Dict[str, Set[str]]:
        """
        Determine which keyword categories a market belongs to.

        Args:
            market: Market to categorize

        Returns:
            Dictionary mapping category names to matched keywords
        """
        text = f"{market.title} {market.ticker}".lower()
        matches: Dict[str, Set[str]] = {}

        for category, patterns in self._compiled_patterns.items():
            matched_keywords = set()
            for i, pattern in enumerate(patterns):
                if pattern.search(text):
                    matched_keywords.add(self.KEYWORD_CATEGORIES[category][i])

            if matched_keywords:
                matches[category] = matched_keywords

        return matches

    def add_custom_category(self, name: str, keywords: List[str]) -> None:
        """
        Add a custom keyword category for correlation detection.

        Args:
            name: Category name
            keywords: List of keywords for this category
        """
        self.KEYWORD_CATEGORIES[name] = keywords
        self._compiled_patterns[name] = [
            re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE)
            for kw in keywords
        ]
        logger.info(f"Added custom category '{name}' with {len(keywords)} keywords")
