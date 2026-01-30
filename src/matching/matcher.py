"""
Market Matcher - Main Orchestrator.

Coordinates the full market matching pipeline:
1. Normalize market text
2. Extract entities
3. Generate candidate matches
4. Validate structure
5. Score confidence
6. Filter and return matches
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .models import (
    NormalizedMarket,
    MatchedMarketPair,
    CandidateMatch,
    Platform,
    MarketType,
    MatchType,
    CONFIDENCE_THRESHOLDS,
)
from .normalizer import TextNormalizer
from .entity_extractor import EntityExtractor
from .structural_validator import StructuralValidator, detect_market_type
from .confidence_scorer import ConfidenceScorer, ScoreBreakdown

# Import for quote fetching
from arb.spread_detector import MarketQuote, Platform as SpreadPlatform

# Try to import rapidfuzz for candidate generation
try:
    from rapidfuzz import fuzz, process
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


logger = logging.getLogger(__name__)


@dataclass
class MatcherConfig:
    """Configuration for the market matcher."""
    min_confidence: float = 0.75  # Minimum confidence to return a match
    max_candidates_per_market: int = 10  # Max candidates to score per market
    use_semantic_similarity: bool = True  # Use sentence embeddings
    semantic_model: str = "all-MiniLM-L6-v2"  # Model for embeddings
    max_expiration_diff_hours: float = 24.0  # Max expiration difference
    enable_blocking: bool = True  # Block candidates by category
    parallel_scoring: bool = False  # Enable parallel scoring (if multiprocessing available)


class MarketMatcher:
    """Main orchestrator for cross-platform market matching.

    Usage:
        matcher = MarketMatcher()

        # Match markets from both platforms
        pairs = matcher.match_markets(kalshi_markets, poly_markets)

        for pair in pairs:
            print(f"{pair.kalshi_ticker} <-> {pair.poly_token_id}")
            print(f"  Confidence: {pair.confidence:.2f}")
    """

    def __init__(self, config: Optional[MatcherConfig] = None):
        """Initialize the market matcher.

        Args:
            config: Matcher configuration (defaults to MatcherConfig())
        """
        self.config = config or MatcherConfig()

        # Initialize components
        self.normalizer = TextNormalizer()
        self.extractor = EntityExtractor(self.normalizer)
        self.validator = StructuralValidator(
            max_expiration_diff_hours=self.config.max_expiration_diff_hours
        )
        self.scorer = ConfidenceScorer(
            use_semantic=self.config.use_semantic_similarity,
            semantic_model=self.config.semantic_model,
        )

        # Cache for normalized markets
        self._kalshi_cache: Dict[str, NormalizedMarket] = {}
        self._poly_cache: Dict[str, NormalizedMarket] = {}

    def match_markets(
        self,
        kalshi_markets: List[Dict[str, Any]],
        poly_markets: List[Dict[str, Any]],
        min_confidence: Optional[float] = None,
    ) -> List[MatchedMarketPair]:
        """Match markets between Kalshi and Polymarket.

        Args:
            kalshi_markets: List of Kalshi market dicts (from API)
            poly_markets: List of Polymarket market dicts (from API)
            min_confidence: Override minimum confidence threshold

        Returns:
            List of matched market pairs above confidence threshold
        """
        threshold = min_confidence if min_confidence is not None else self.config.min_confidence

        # Normalize all markets
        normalized_kalshi = [
            self.normalize_kalshi_market(m) for m in kalshi_markets
        ]
        normalized_poly = [
            self.normalize_poly_market(m) for m in poly_markets
        ]

        # Filter out None values
        normalized_kalshi = [m for m in normalized_kalshi if m is not None]
        normalized_poly = [m for m in normalized_poly if m is not None]

        logger.info(
            f"Matching {len(normalized_kalshi)} Kalshi markets "
            f"with {len(normalized_poly)} Polymarket markets"
        )

        # Generate candidate matches
        candidates = self._generate_candidates(normalized_kalshi, normalized_poly)
        logger.info(f"Generated {len(candidates)} candidate matches")

        # Score and filter candidates
        matches = []
        for candidate in candidates:
            # Validate structure
            validation = self.validator.validate(
                candidate.kalshi_market,
                candidate.poly_market
            )

            # Skip invalid structural matches
            if not validation.is_valid:
                continue

            # Score the match
            score_breakdown = self.scorer.score(
                candidate.kalshi_market,
                candidate.poly_market,
                validation
            )

            # Create matched pair if above threshold
            if score_breakdown.total_score >= threshold:
                pair = self.scorer.create_matched_pair(
                    candidate.kalshi_market,
                    candidate.poly_market,
                    score_breakdown,
                    validation
                )
                matches.append(pair)

        # Sort by confidence (highest first)
        matches.sort(key=lambda p: p.confidence, reverse=True)

        # Remove duplicate matches (keep highest confidence)
        matches = self._deduplicate_matches(matches)

        logger.info(f"Found {len(matches)} matches above {threshold} confidence")
        return matches

    def normalize_kalshi_market(self, market: Dict[str, Any]) -> Optional[NormalizedMarket]:
        """Normalize a Kalshi market dict.

        Args:
            market: Kalshi market data from API

        Returns:
            NormalizedMarket or None if invalid
        """
        try:
            ticker = market.get("ticker", "")

            # Check cache
            if ticker in self._kalshi_cache:
                return self._kalshi_cache[ticker]

            title = market.get("title", "")
            if not ticker or not title:
                return None

            # Normalize and extract
            normalized_title = self.normalizer.normalize(title)
            entities = self.extractor.extract(title)

            # Infer category
            context = self.extractor.extract_with_context(title)
            category = context.get("inferred_category")

            # Parse close time
            close_time = None
            close_time_str = market.get("close_time") or market.get("expiration_time")
            if close_time_str:
                try:
                    close_time = datetime.fromisoformat(
                        close_time_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            # Detect market type
            market_type = detect_market_type(title)

            normalized = NormalizedMarket(
                platform=Platform.KALSHI,
                market_id=ticker,
                original_title=title,
                normalized_title=normalized_title,
                entities=entities,
                category=category or market.get("category"),
                market_type=market_type,
                close_time=close_time,
                volume_24h=market.get("volume_24h"),
                yes_sub_title=market.get("yes_sub_title"),
                description=market.get("description"),
            )

            self._kalshi_cache[ticker] = normalized
            return normalized

        except Exception as e:
            logger.warning(f"Failed to normalize Kalshi market: {e}")
            return None

    def normalize_poly_market(self, market: Dict[str, Any]) -> Optional[NormalizedMarket]:
        """Normalize a Polymarket market dict.

        Args:
            market: Polymarket market data from API

        Returns:
            NormalizedMarket or None if invalid
        """
        try:
            # Polymarket uses different field names
            token_id = market.get("token_id") or market.get("condition_id") or market.get("id", "")

            # Check cache
            if token_id in self._poly_cache:
                return self._poly_cache[token_id]

            title = market.get("question") or market.get("title", "")
            if not token_id or not title:
                return None

            # Normalize and extract
            normalized_title = self.normalizer.normalize(title)
            entities = self.extractor.extract(title)

            # Infer category
            context = self.extractor.extract_with_context(title)
            category = context.get("inferred_category")

            # Parse close time
            close_time = None
            close_time_str = market.get("end_date") or market.get("close_time")
            if close_time_str:
                try:
                    close_time = datetime.fromisoformat(
                        close_time_str.replace("Z", "+00:00")
                    )
                except (ValueError, TypeError):
                    pass

            # Detect market type
            market_type = detect_market_type(title)

            normalized = NormalizedMarket(
                platform=Platform.POLYMARKET,
                market_id=token_id,
                original_title=title,
                normalized_title=normalized_title,
                entities=entities,
                category=category or market.get("category"),
                market_type=market_type,
                close_time=close_time,
                volume_24h=market.get("volume") or market.get("volume_24h"),
                description=market.get("description"),
            )

            self._poly_cache[token_id] = normalized
            return normalized

        except Exception as e:
            logger.warning(f"Failed to normalize Polymarket market: {e}")
            return None

    def _generate_candidates(
        self,
        kalshi_markets: List[NormalizedMarket],
        poly_markets: List[NormalizedMarket],
    ) -> List[CandidateMatch]:
        """Generate candidate matches using blocking and similarity.

        Uses category blocking and fuzzy matching to reduce search space.
        """
        candidates = []

        # Group by category for blocking
        if self.config.enable_blocking:
            kalshi_by_category = self._group_by_category(kalshi_markets)
            poly_by_category = self._group_by_category(poly_markets)

            # Only compare within same category
            for category in kalshi_by_category:
                if category in poly_by_category:
                    category_candidates = self._find_candidates_in_category(
                        kalshi_by_category[category],
                        poly_by_category[category],
                    )
                    candidates.extend(category_candidates)

            # Also check markets with unknown category against all
            unknown_kalshi = kalshi_by_category.get(None, [])
            unknown_poly = poly_by_category.get(None, [])

            if unknown_kalshi or unknown_poly:
                # Compare unknown Kalshi with all Poly
                for k in unknown_kalshi:
                    for p in poly_markets:
                        candidate = self._create_candidate(k, p)
                        if candidate:
                            candidates.append(candidate)

                # Compare all Kalshi with unknown Poly
                for k in kalshi_markets:
                    for p in unknown_poly:
                        candidate = self._create_candidate(k, p)
                        if candidate:
                            candidates.append(candidate)
        else:
            # Compare all pairs (slower but more thorough)
            for kalshi in kalshi_markets:
                for poly in poly_markets:
                    candidate = self._create_candidate(kalshi, poly)
                    if candidate:
                        candidates.append(candidate)

        # Deduplicate
        seen = set()
        unique_candidates = []
        for c in candidates:
            key = c.pair_key
            if key not in seen:
                seen.add(key)
                unique_candidates.append(c)

        return unique_candidates

    def _group_by_category(
        self,
        markets: List[NormalizedMarket],
    ) -> Dict[Optional[str], List[NormalizedMarket]]:
        """Group markets by category."""
        groups: Dict[Optional[str], List[NormalizedMarket]] = {}
        for market in markets:
            category = market.category
            if category not in groups:
                groups[category] = []
            groups[category].append(market)
        return groups

    def _find_candidates_in_category(
        self,
        kalshi_markets: List[NormalizedMarket],
        poly_markets: List[NormalizedMarket],
    ) -> List[CandidateMatch]:
        """Find candidate matches within a category using fuzzy matching."""
        candidates = []

        if not HAS_RAPIDFUZZ:
            # Fallback: compare all pairs
            for kalshi in kalshi_markets:
                for poly in poly_markets:
                    candidate = self._create_candidate(kalshi, poly)
                    if candidate:
                        candidates.append(candidate)
            return candidates

        # Build lookup for Poly markets
        poly_titles = [m.normalized_title for m in poly_markets]
        poly_lookup = {m.normalized_title: m for m in poly_markets}

        # For each Kalshi market, find top N similar Poly markets
        for kalshi in kalshi_markets:
            # Use rapidfuzz to find similar titles
            matches = process.extract(
                kalshi.normalized_title,
                poly_titles,
                scorer=fuzz.token_set_ratio,
                limit=self.config.max_candidates_per_market,
            )

            for poly_title, score, _ in matches:
                if score >= 30:  # Low threshold for candidates
                    poly = poly_lookup.get(poly_title)
                    if poly:
                        candidate = CandidateMatch(
                            kalshi_market=kalshi,
                            poly_market=poly,
                            initial_score=score / 100.0,
                        )
                        candidates.append(candidate)

        return candidates

    def _create_candidate(
        self,
        kalshi: NormalizedMarket,
        poly: NormalizedMarket,
    ) -> Optional[CandidateMatch]:
        """Create a candidate match if basic criteria are met."""
        # Quick text similarity check
        if HAS_RAPIDFUZZ:
            score = fuzz.token_set_ratio(
                kalshi.normalized_title,
                poly.normalized_title
            ) / 100.0
        else:
            # Simple word overlap
            words_1 = set(kalshi.normalized_title.lower().split())
            words_2 = set(poly.normalized_title.lower().split())
            if words_1 and words_2:
                score = len(words_1 & words_2) / len(words_1 | words_2)
            else:
                score = 0.0

        # Skip if very low similarity
        if score < 0.2:
            return None

        return CandidateMatch(
            kalshi_market=kalshi,
            poly_market=poly,
            initial_score=score,
        )

    def _deduplicate_matches(
        self,
        matches: List[MatchedMarketPair],
    ) -> List[MatchedMarketPair]:
        """Remove duplicate matches, keeping highest confidence.

        A market should only appear in one pair.
        """
        used_kalshi: Set[str] = set()
        used_poly: Set[str] = set()
        deduplicated = []

        # Already sorted by confidence (highest first)
        for match in matches:
            if match.kalshi_ticker not in used_kalshi and match.poly_token_id not in used_poly:
                deduplicated.append(match)
                used_kalshi.add(match.kalshi_ticker)
                used_poly.add(match.poly_token_id)

        return deduplicated

    def get_matched_pairs(self) -> List[MatchedMarketPair]:
        """Get all currently cached matched pairs.

        Note: This requires match_markets to have been called first.
        Returns empty list if no matching has been performed.
        """
        # This is a placeholder for integration with SpreadDetector
        # In practice, you would store matches and return them here
        return []

    def clear_cache(self) -> None:
        """Clear the normalization cache."""
        self._kalshi_cache.clear()
        self._poly_cache.clear()


def match_markets(
    kalshi_markets: List[Dict[str, Any]],
    poly_markets: List[Dict[str, Any]],
    min_confidence: float = 0.75,
) -> List[MatchedMarketPair]:
    """Convenience function for quick market matching.

    Args:
        kalshi_markets: List of Kalshi market dicts
        poly_markets: List of Polymarket market dicts
        min_confidence: Minimum confidence threshold

    Returns:
        List of matched market pairs
    """
    matcher = MarketMatcher()
    return matcher.match_markets(kalshi_markets, poly_markets, min_confidence)


# Integration with SpreadDetector
class MatcherForSpreadDetector:
    """Adapter class that implements MarketMatcher Protocol for SpreadDetector.

    This class bridges the MarketMatcher with the SpreadDetector's expected interface.
    """

    def __init__(
        self,
        matcher: Optional[MarketMatcher] = None,
        kalshi_client: Any = None,
        poly_client: Any = None,
    ):
        """Initialize the adapter.

        Args:
            matcher: MarketMatcher instance (created if not provided)
            kalshi_client: Kalshi API client for fetching quotes
            poly_client: Polymarket API client for fetching quotes
        """
        self.matcher = matcher or MarketMatcher()
        self.kalshi_client = kalshi_client
        self.poly_client = poly_client
        self._matched_pairs: List[MatchedMarketPair] = []

    def refresh_markets(
        self,
        kalshi_markets: List[Dict[str, Any]],
        poly_markets: List[Dict[str, Any]],
    ) -> None:
        """Refresh the matched market pairs.

        Args:
            kalshi_markets: Fresh Kalshi market data
            poly_markets: Fresh Polymarket data
        """
        self._matched_pairs = self.matcher.match_markets(
            kalshi_markets, poly_markets
        )

    def get_matched_pairs(self) -> List[MatchedMarketPair]:
        """Get all currently matched market pairs.

        Returns:
            List of matched pairs (compatible with SpreadDetector interface)
        """
        # Convert to the format expected by SpreadDetector
        # SpreadDetector expects a different MatchedMarketPair structure
        # This bridges the two
        return self._matched_pairs

    def get_quotes(self, pair: MatchedMarketPair) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]:
        """Get current quotes for a matched pair.

        This method fetches live quotes from both platforms.
        Requires kalshi_client and poly_client to be set.

        Args:
            pair: The matched market pair to fetch quotes for

        Returns:
            Tuple of (kalshi_yes, kalshi_no, poly_yes, poly_no) MarketQuote objects
        """
        if not self.kalshi_client or not self.poly_client:
            raise ValueError(
                "Clients not configured. Set kalshi_client and poly_client."
            )

        now = datetime.now()

        # Fetch Kalshi quotes
        kalshi_yes, kalshi_no = self._fetch_kalshi_quotes(
            pair.kalshi_ticker,
            pair.kalshi_market.original_title if pair.kalshi_market else pair.kalshi_ticker,
            now,
        )

        # Fetch Polymarket quotes
        poly_yes, poly_no = self._fetch_poly_quotes(
            pair.poly_token_id,
            pair.poly_market.original_title if pair.poly_market else pair.poly_token_id,
            now,
        )

        return (kalshi_yes, kalshi_no, poly_yes, poly_no)

    def _fetch_kalshi_quotes(
        self,
        ticker: str,
        market_name: str,
        now: datetime,
    ) -> Tuple[MarketQuote, MarketQuote]:
        """Fetch YES and NO quotes from Kalshi.

        Args:
            ticker: Market ticker
            market_name: Market name for the quote
            now: Current timestamp

        Returns:
            Tuple of (yes_quote, no_quote)
        """
        try:
            # Get orderbook - handle both Exchange and raw API client types
            if hasattr(self.kalshi_client, '_get_orderbook'):
                orderbook = self.kalshi_client._get_orderbook(ticker)
            elif hasattr(self.kalshi_client, 'get_orderbook'):
                orderbook = self.kalshi_client.get_orderbook(ticker)
            else:
                raise AttributeError("Kalshi client has no orderbook method")

            # Handle OrderBook object vs dict
            if hasattr(orderbook, 'best_bid'):
                # It's an OrderBook object
                best_bid = orderbook.best_bid
                best_ask = orderbook.best_ask
                bids = orderbook.bids or []
                asks = orderbook.asks or []
            else:
                # It's a dict from raw API
                best_bid = orderbook.get('yes', {}).get('bids', [[None]])[0][0] if orderbook.get('yes', {}).get('bids') else None
                best_ask = orderbook.get('yes', {}).get('asks', [[None]])[0][0] if orderbook.get('yes', {}).get('asks') else None
                bids = [(b[0], b[1]) for b in orderbook.get('yes', {}).get('bids', [])]
                asks = [(a[0], a[1]) for a in orderbook.get('yes', {}).get('asks', [])]

            # YES quote: bid is best bid, ask is best ask
            # Kalshi prices are in cents (0-100), convert to 0-1
            yes_quote = MarketQuote(
                platform=SpreadPlatform.KALSHI,
                market_id=ticker,
                market_name=market_name,
                outcome="yes",
                best_bid=best_bid / 100.0 if best_bid else None,
                best_ask=best_ask / 100.0 if best_ask else None,
                bid_size=bids[0][1] if bids else 0,
                ask_size=asks[0][1] if asks else 0,
                bid_depth_usd=sum(p * s / 100.0 for p, s in bids) if bids else 0.0,
                ask_depth_usd=sum(p * s / 100.0 for p, s in asks) if asks else 0.0,
                timestamp=now,
            )

            # NO quote: inverse of YES
            # NO bid = 100 - YES ask, NO ask = 100 - YES bid
            no_quote = MarketQuote(
                platform=SpreadPlatform.KALSHI,
                market_id=ticker,
                market_name=market_name,
                outcome="no",
                best_bid=(100 - best_ask) / 100.0 if best_ask else None,
                best_ask=(100 - best_bid) / 100.0 if best_bid else None,
                bid_size=asks[0][1] if asks else 0,
                ask_size=bids[0][1] if bids else 0,
                bid_depth_usd=sum((100 - p) * s / 100.0 for p, s in asks) if asks else 0.0,
                ask_depth_usd=sum((100 - p) * s / 100.0 for p, s in bids) if bids else 0.0,
                timestamp=now,
            )

            return (yes_quote, no_quote)

        except Exception as e:
            logger.error("Failed to fetch Kalshi quotes for %s: %s", ticker, e)
            return (
                self._empty_quote(SpreadPlatform.KALSHI, ticker, market_name, "yes", now),
                self._empty_quote(SpreadPlatform.KALSHI, ticker, market_name, "no", now),
            )

    def _fetch_poly_quotes(
        self,
        token_id: str,
        market_name: str,
        now: datetime,
    ) -> Tuple[MarketQuote, MarketQuote]:
        """Fetch YES and NO quotes from Polymarket.

        Args:
            token_id: Token ID
            market_name: Market name for the quote
            now: Current timestamp

        Returns:
            Tuple of (yes_quote, no_quote)
        """
        try:
            # Get orderbook - handle both Exchange and raw client types
            if hasattr(self.poly_client, '_get_orderbook'):
                orderbook = self.poly_client._get_orderbook(token_id)
            elif hasattr(self.poly_client, 'get_orderbook'):
                orderbook = self.poly_client.get_orderbook(token_id)
            else:
                raise AttributeError("Polymarket client has no orderbook method")

            # Handle OrderBook object vs dict
            if hasattr(orderbook, 'best_bid'):
                best_bid = orderbook.best_bid
                best_ask = orderbook.best_ask
                bids = orderbook.bids or []
                asks = orderbook.asks or []
            else:
                # Dict from raw API - Polymarket prices are already 0-1
                bids = orderbook.get('bids', [])
                asks = orderbook.get('asks', [])
                best_bid = bids[0][0] if bids else None
                best_ask = asks[0][0] if asks else None

            # Polymarket prices are already 0-1
            yes_quote = MarketQuote(
                platform=SpreadPlatform.POLYMARKET,
                market_id=token_id,
                market_name=market_name,
                outcome="yes",
                best_bid=best_bid,
                best_ask=best_ask,
                bid_size=int(bids[0][1]) if bids else 0,
                ask_size=int(asks[0][1]) if asks else 0,
                bid_depth_usd=sum(p * s for p, s in bids) if bids else 0.0,
                ask_depth_usd=sum(p * s for p, s in asks) if asks else 0.0,
                timestamp=now,
            )

            # NO quote: inverse
            no_quote = MarketQuote(
                platform=SpreadPlatform.POLYMARKET,
                market_id=token_id,
                market_name=market_name,
                outcome="no",
                best_bid=(1.0 - best_ask) if best_ask else None,
                best_ask=(1.0 - best_bid) if best_bid else None,
                bid_size=int(asks[0][1]) if asks else 0,
                ask_size=int(bids[0][1]) if bids else 0,
                bid_depth_usd=sum((1.0 - p) * s for p, s in asks) if asks else 0.0,
                ask_depth_usd=sum((1.0 - p) * s for p, s in bids) if bids else 0.0,
                timestamp=now,
            )

            return (yes_quote, no_quote)

        except Exception as e:
            logger.error("Failed to fetch Polymarket quotes for %s: %s", token_id, e)
            return (
                self._empty_quote(SpreadPlatform.POLYMARKET, token_id, market_name, "yes", now),
                self._empty_quote(SpreadPlatform.POLYMARKET, token_id, market_name, "no", now),
            )

    def _empty_quote(
        self,
        platform: SpreadPlatform,
        market_id: str,
        market_name: str,
        outcome: str,
        now: datetime,
    ) -> MarketQuote:
        """Create an empty quote for error cases."""
        return MarketQuote(
            platform=platform,
            market_id=market_id,
            market_name=market_name,
            outcome=outcome,
            best_bid=None,
            best_ask=None,
            bid_size=0,
            ask_size=0,
            bid_depth_usd=0.0,
            ask_depth_usd=0.0,
            timestamp=now,
        )
