"""Quote Provider - Bridge between MarketMatcher and SpreadDetector.

Fetches live quotes from both exchanges for matched market pairs
and returns them in the format expected by SpreadDetector.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from arb.spread_detector import MarketQuote, Platform, MatchedMarketPair as SpreadMatchedPair
from src.exchanges.kalshi import KalshiExchange
from src.exchanges.polymarket import PolymarketExchange
from .models import MatchedMarketPair
from .matcher import MarketMatcher


logger = logging.getLogger(__name__)


@dataclass
class QuoteProviderConfig:
    """Configuration for quote provider."""
    cache_ttl_seconds: float = 1.0  # How long to cache quotes
    fetch_depth: bool = True  # Whether to fetch full orderbook depth
    max_retries: int = 2  # Retries on fetch failure


class QuoteProvider:
    """Provides live quotes for matched market pairs.

    Bridges the MarketMatcher (which does static matching) with the
    SpreadDetector (which needs live quotes).

    Usage:
        provider = QuoteProvider(kalshi_exchange, poly_exchange, matcher)

        # Refresh matched pairs
        provider.refresh_pairs(kalshi_markets, poly_markets)

        # Get quotes for a pair (called by SpreadDetector)
        quotes = provider.get_quotes(pair)
    """

    def __init__(
        self,
        kalshi_exchange: KalshiExchange,
        poly_exchange: PolymarketExchange,
        matcher: Optional[MarketMatcher] = None,
        config: Optional[QuoteProviderConfig] = None,
    ):
        """Initialize the quote provider.

        Args:
            kalshi_exchange: Kalshi exchange client
            poly_exchange: Polymarket exchange client
            matcher: Optional MarketMatcher (creates one if not provided)
            config: Optional configuration
        """
        self._kalshi = kalshi_exchange
        self._poly = poly_exchange
        self._matcher = matcher or MarketMatcher()
        self._config = config or QuoteProviderConfig()

        # Matched pairs cache
        self._matched_pairs: List[MatchedMarketPair] = []
        self._spread_pairs: List[SpreadMatchedPair] = []

        # Quote cache: {market_id: (quote, timestamp)}
        self._quote_cache: Dict[str, Tuple[MarketQuote, datetime]] = {}

    def refresh_pairs(
        self,
        kalshi_markets: List[Dict[str, Any]],
        poly_markets: List[Dict[str, Any]],
        min_confidence: float = 0.75,
    ) -> int:
        """Refresh matched market pairs from fresh market data.

        Args:
            kalshi_markets: Kalshi market data from API
            poly_markets: Polymarket data from API
            min_confidence: Minimum confidence threshold

        Returns:
            Number of matched pairs found
        """
        self._matched_pairs = self._matcher.match_markets(
            kalshi_markets,
            poly_markets,
            min_confidence=min_confidence,
        )

        # Convert to SpreadDetector format
        self._spread_pairs = [
            self._convert_to_spread_pair(pair)
            for pair in self._matched_pairs
        ]

        logger.info("Refreshed %d matched pairs", len(self._matched_pairs))
        return len(self._matched_pairs)

    def get_matched_pairs(self) -> List[SpreadMatchedPair]:
        """Get all matched pairs in SpreadDetector format.

        Returns:
            List of MatchedMarketPair objects for SpreadDetector
        """
        return self._spread_pairs

    def get_quotes(
        self,
        pair: SpreadMatchedPair,
    ) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]:
        """Get live quotes for a matched pair.

        Args:
            pair: The matched market pair

        Returns:
            Tuple of (kalshi_yes, kalshi_no, poly_yes, poly_no) MarketQuote objects
        """
        # Fetch Kalshi quotes
        kalshi_yes, kalshi_no = self._fetch_kalshi_quotes(
            pair.market_1_id,
            pair.market_1_name,
        )

        # Fetch Polymarket quotes
        poly_yes, poly_no = self._fetch_poly_quotes(
            pair.market_2_id,
            pair.market_2_name,
        )

        return (kalshi_yes, kalshi_no, poly_yes, poly_no)

    def _fetch_kalshi_quotes(
        self,
        ticker: str,
        market_name: str,
    ) -> Tuple[MarketQuote, MarketQuote]:
        """Fetch YES and NO quotes from Kalshi.

        Args:
            ticker: Market ticker
            market_name: Market name for the quote

        Returns:
            Tuple of (yes_quote, no_quote)
        """
        now = datetime.now()

        # Check cache
        cache_key_yes = f"kalshi:{ticker}:yes"
        cache_key_no = f"kalshi:{ticker}:no"

        cached_yes = self._get_cached_quote(cache_key_yes, now)
        cached_no = self._get_cached_quote(cache_key_no, now)

        if cached_yes and cached_no:
            return (cached_yes, cached_no)

        # Fetch fresh orderbook
        try:
            orderbook = self._kalshi._get_orderbook(ticker)

            # YES quote: bid is best bid, ask is best ask
            yes_quote = MarketQuote(
                platform=Platform.KALSHI,
                market_id=ticker,
                market_name=market_name,
                outcome="yes",
                best_bid=orderbook.best_bid / 100.0 if orderbook.best_bid else None,
                best_ask=orderbook.best_ask / 100.0 if orderbook.best_ask else None,
                bid_size=orderbook.bids[0][1] if orderbook.bids else 0,
                ask_size=orderbook.asks[0][1] if orderbook.asks else 0,
                bid_depth_usd=sum(p * s / 100.0 for p, s in orderbook.bids),
                ask_depth_usd=sum(p * s / 100.0 for p, s in orderbook.asks),
                timestamp=now,
            )

            # NO quote: inverse of YES
            # NO bid = 100 - YES ask, NO ask = 100 - YES bid
            no_quote = MarketQuote(
                platform=Platform.KALSHI,
                market_id=ticker,
                market_name=market_name,
                outcome="no",
                best_bid=(100 - orderbook.best_ask) / 100.0 if orderbook.best_ask else None,
                best_ask=(100 - orderbook.best_bid) / 100.0 if orderbook.best_bid else None,
                bid_size=orderbook.asks[0][1] if orderbook.asks else 0,
                ask_size=orderbook.bids[0][1] if orderbook.bids else 0,
                bid_depth_usd=sum((100 - p) * s / 100.0 for p, s in orderbook.asks),
                ask_depth_usd=sum((100 - p) * s / 100.0 for p, s in orderbook.bids),
                timestamp=now,
            )

            # Cache
            self._quote_cache[cache_key_yes] = (yes_quote, now)
            self._quote_cache[cache_key_no] = (no_quote, now)

            return (yes_quote, no_quote)

        except Exception as e:
            logger.error("Failed to fetch Kalshi quotes for %s: %s", ticker, e)
            # Return empty quotes
            return (
                self._empty_quote(Platform.KALSHI, ticker, market_name, "yes"),
                self._empty_quote(Platform.KALSHI, ticker, market_name, "no"),
            )

    def _fetch_poly_quotes(
        self,
        token_id: str,
        market_name: str,
    ) -> Tuple[MarketQuote, MarketQuote]:
        """Fetch YES and NO quotes from Polymarket.

        Args:
            token_id: Token ID
            market_name: Market name for the quote

        Returns:
            Tuple of (yes_quote, no_quote)
        """
        now = datetime.now()

        # Check cache
        cache_key_yes = f"poly:{token_id}:yes"
        cache_key_no = f"poly:{token_id}:no"

        cached_yes = self._get_cached_quote(cache_key_yes, now)
        cached_no = self._get_cached_quote(cache_key_no, now)

        if cached_yes and cached_no:
            return (cached_yes, cached_no)

        # Fetch fresh orderbook
        try:
            orderbook = self._poly._get_orderbook(token_id)

            # Polymarket prices are already 0-1
            yes_quote = MarketQuote(
                platform=Platform.POLYMARKET,
                market_id=token_id,
                market_name=market_name,
                outcome="yes",
                best_bid=orderbook.best_bid if orderbook.best_bid else None,
                best_ask=orderbook.best_ask if orderbook.best_ask else None,
                bid_size=orderbook.bids[0][1] if orderbook.bids else 0,
                ask_size=orderbook.asks[0][1] if orderbook.asks else 0,
                bid_depth_usd=sum(p * s for p, s in orderbook.bids),
                ask_depth_usd=sum(p * s for p, s in orderbook.asks),
                timestamp=now,
            )

            # NO quote: inverse
            no_quote = MarketQuote(
                platform=Platform.POLYMARKET,
                market_id=token_id,
                market_name=market_name,
                outcome="no",
                best_bid=(1.0 - orderbook.best_ask) if orderbook.best_ask else None,
                best_ask=(1.0 - orderbook.best_bid) if orderbook.best_bid else None,
                bid_size=orderbook.asks[0][1] if orderbook.asks else 0,
                ask_size=orderbook.bids[0][1] if orderbook.bids else 0,
                bid_depth_usd=sum((1.0 - p) * s for p, s in orderbook.asks),
                ask_depth_usd=sum((1.0 - p) * s for p, s in orderbook.bids),
                timestamp=now,
            )

            # Cache
            self._quote_cache[cache_key_yes] = (yes_quote, now)
            self._quote_cache[cache_key_no] = (no_quote, now)

            return (yes_quote, no_quote)

        except Exception as e:
            logger.error("Failed to fetch Polymarket quotes for %s: %s", token_id, e)
            return (
                self._empty_quote(Platform.POLYMARKET, token_id, market_name, "yes"),
                self._empty_quote(Platform.POLYMARKET, token_id, market_name, "no"),
            )

    def _get_cached_quote(
        self,
        cache_key: str,
        now: datetime,
    ) -> Optional[MarketQuote]:
        """Get a quote from cache if still valid."""
        if cache_key not in self._quote_cache:
            return None

        quote, cached_at = self._quote_cache[cache_key]
        age = (now - cached_at).total_seconds()

        if age <= self._config.cache_ttl_seconds:
            return quote

        return None

    def _empty_quote(
        self,
        platform: Platform,
        market_id: str,
        market_name: str,
        outcome: str,
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
            timestamp=datetime.now(),
        )

    def _convert_to_spread_pair(
        self,
        pair: MatchedMarketPair,
    ) -> SpreadMatchedPair:
        """Convert internal MatchedMarketPair to SpreadDetector format.

        Args:
            pair: Internal matched pair

        Returns:
            SpreadDetector-compatible MatchedMarketPair
        """
        return SpreadMatchedPair(
            pair_id=f"{pair.kalshi_ticker}:{pair.poly_token_id}",
            event_description=pair.event_description,
            platform_1=Platform.KALSHI,
            market_1_id=pair.kalshi_ticker,
            market_1_name=pair.kalshi_title,
            platform_2=Platform.POLYMARKET,
            market_2_id=pair.poly_token_id,
            market_2_name=pair.poly_title,
            match_confidence=pair.confidence,
            category=pair.category,
            close_time=pair.close_time,
        )

    def clear_cache(self) -> None:
        """Clear the quote cache."""
        self._quote_cache.clear()


class LiveQuoteMarketMatcher:
    """Market matcher that implements the SpreadDetector protocol with live quotes.

    This is the class to pass to SpreadDetector. It implements:
    - get_matched_pairs() -> List[MatchedMarketPair]
    - get_quotes(pair) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]
    """

    def __init__(
        self,
        kalshi_exchange: KalshiExchange,
        poly_exchange: PolymarketExchange,
        matcher: Optional[MarketMatcher] = None,
    ):
        """Initialize the live quote market matcher.

        Args:
            kalshi_exchange: Kalshi exchange client
            poly_exchange: Polymarket exchange client
            matcher: Optional MarketMatcher instance
        """
        self._provider = QuoteProvider(
            kalshi_exchange,
            poly_exchange,
            matcher,
        )

    def refresh(
        self,
        kalshi_markets: List[Dict[str, Any]],
        poly_markets: List[Dict[str, Any]],
        min_confidence: float = 0.75,
    ) -> int:
        """Refresh matched pairs from market data.

        Args:
            kalshi_markets: Kalshi markets from API
            poly_markets: Polymarket data from API
            min_confidence: Minimum match confidence

        Returns:
            Number of pairs matched
        """
        return self._provider.refresh_pairs(
            kalshi_markets,
            poly_markets,
            min_confidence,
        )

    def get_matched_pairs(self) -> List[SpreadMatchedPair]:
        """Get all matched pairs (SpreadDetector interface)."""
        return self._provider.get_matched_pairs()

    def get_quotes(
        self,
        pair: SpreadMatchedPair,
    ) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]:
        """Get quotes for a pair (SpreadDetector interface)."""
        return self._provider.get_quotes(pair)
