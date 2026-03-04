"""Opportunity Detector - Bridge between quotes and execution.

Scans matched market pairs for profitable arbitrage opportunities,
filters by minimum profitability thresholds, and ranks by ROI.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Protocol, Tuple

from arb.spread_detector import (
    MarketQuote,
    MatchedMarketPair,
    SpreadOpportunity,
    SpreadDetector,
)

from .config import ArbitrageConfig
from .fee_calculator import FeeCalculator, SpreadAnalysis


logger = logging.getLogger(__name__)


class QuoteSource(Protocol):
    """Protocol for quote providers."""

    def get_matched_pairs(self) -> List[MatchedMarketPair]:
        """Get all currently matched market pairs."""
        ...

    def get_quotes(
        self, pair: MatchedMarketPair
    ) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]:
        """Get current quotes for a matched pair."""
        ...


@dataclass
class RankedOpportunity:
    """A spread opportunity ranked by profitability.

    Attributes:
        opportunity: The underlying SpreadOpportunity
        analysis: Fee analysis for the spread
        rank_score: Combined score for ranking (higher = better)
        detected_at: When the opportunity was detected
    """

    opportunity: SpreadOpportunity
    analysis: SpreadAnalysis
    rank_score: float
    detected_at: datetime = field(default_factory=datetime.now)

    @property
    def roi(self) -> float:
        """Return on investment."""
        return self.analysis.roi

    @property
    def net_edge(self) -> float:
        """Net edge per contract after fees."""
        return self.analysis.net_spread

    @property
    def estimated_profit(self) -> float:
        """Estimated profit in USD."""
        return self.analysis.estimated_profit


class OpportunityDetector:
    """Detects and ranks profitable arbitrage opportunities.

    Uses the SpreadDetector from arb/spread_detector.py for core detection,
    then applies additional filtering and ranking based on:
    - Minimum ROI threshold
    - Minimum net edge after fees
    - Available liquidity
    - Risk-adjusted scoring

    Example:
        detector = OpportunityDetector(quote_source, fee_calculator, config)

        # Scan for opportunities
        opportunities = detector.scan_all_pairs()

        for opp in opportunities:
            print(f"ROI: {opp.roi:.2%}, Profit: ${opp.estimated_profit:.2f}")
    """

    def __init__(
        self,
        quote_source: QuoteSource,
        fee_calculator: Optional[FeeCalculator] = None,
        config: Optional[ArbitrageConfig] = None,
    ):
        """Initialize the opportunity detector.

        Args:
            quote_source: Source for market quotes (implements QuoteSource protocol)
            fee_calculator: Optional fee calculator (creates one if not provided)
            config: Optional configuration (uses defaults if not provided)
        """
        self._quote_source = quote_source
        self._config = config or ArbitrageConfig()
        self._fee_calc = fee_calculator or FeeCalculator(self._config)

        # Use existing SpreadDetector for core detection
        self._spread_detector = SpreadDetector(
            market_matcher=quote_source,
            min_edge_cents=self._config.min_edge_cents,
            min_liquidity_usd=self._config.min_liquidity_usd,
            max_quote_age_ms=self._config.max_quote_age_ms,
        )

        # Stats tracking
        self._total_scans = 0
        self._opportunities_found = 0

    def scan_all_pairs(self) -> List[RankedOpportunity]:
        """Scan all matched pairs for profitable opportunities.

        Returns:
            List of RankedOpportunity objects sorted by rank score (best first)
        """
        self._total_scans += 1

        # Use spread detector to find raw opportunities
        raw_opportunities = self._spread_detector.check_once()

        if not raw_opportunities:
            logger.debug("No raw opportunities detected in scan %d", self._total_scans)
            return []

        # Filter and rank
        ranked = self._filter_and_rank(raw_opportunities)

        if ranked:
            self._opportunities_found += len(ranked)
            logger.info(
                "Scan %d: Found %d viable opportunities (total: %d)",
                self._total_scans,
                len(ranked),
                self._opportunities_found,
            )

        return ranked

    def scan_single_pair(self, pair: MatchedMarketPair) -> List[RankedOpportunity]:
        """Scan a single pair for opportunities.

        Args:
            pair: The matched market pair to scan

        Returns:
            List of opportunities for this pair (may be empty)
        """
        try:
            p1_yes, p1_no, p2_yes, p2_no = self._quote_source.get_quotes(pair)
        except Exception as e:
            logger.warning("Failed to get quotes for pair %s: %s", pair.pair_id, e)
            return []

        opportunities = []

        # Check cross-platform arb opportunities
        opportunities.extend(self._check_cross_platform(pair, p1_yes, p2_yes, "yes"))
        opportunities.extend(self._check_cross_platform(pair, p2_yes, p1_yes, "yes"))
        opportunities.extend(self._check_cross_platform(pair, p1_no, p2_no, "no"))
        opportunities.extend(self._check_cross_platform(pair, p2_no, p1_no, "no"))

        # Check dutch book opportunities
        opportunities.extend(self._check_dutch_book(pair, p1_yes, p2_no))
        opportunities.extend(self._check_dutch_book(pair, p1_no, p2_yes))

        return self._filter_and_rank_list(opportunities)

    def get_stats(self) -> dict:
        """Get detection statistics.

        Returns:
            Dictionary with scan count and opportunities found
        """
        return {
            "total_scans": self._total_scans,
            "opportunities_found": self._opportunities_found,
            "hit_rate": (
                self._opportunities_found / self._total_scans
                if self._total_scans > 0
                else 0
            ),
        }

    def _filter_and_rank(
        self, opportunities: List[SpreadOpportunity]
    ) -> List[RankedOpportunity]:
        """Filter opportunities by thresholds and rank by profitability.

        Applies the following filters:
        1. Explicit negative edge rejection (net_spread <= 0)
        2. Minimum profitable size check (filters insufficient liquidity)
        3. ROI threshold check (using conservative fees)
        4. Net edge threshold check (using conservative fees)
        5. Depth-limited sizing to reduce partial fills

        Args:
            opportunities: Raw opportunities from SpreadDetector

        Returns:
            Filtered and ranked opportunities
        """
        ranked = []
        use_maker = self._config.prefer_maker_orders
        use_conservative = self._config.use_conservative_fees_for_filtering
        max_depth_pct = self._config.max_depth_usage_pct

        for opp in opportunities:
            # Apply depth limiting to reduce partial fills
            # Only use a fraction of available liquidity
            effective_size = max(1, int(opp.max_contracts * max_depth_pct))

            # Calculate analysis for FILTERING using conservative fees
            # This ensures we only take trades that are profitable in worst case
            if opp.opportunity_type == "dutch_book":
                filter_analysis = self._fee_calc.calculate_dutch_book_spread(
                    platform_a=opp.buy_platform,
                    price_a=opp.buy_price,
                    platform_b=opp.sell_platform,
                    price_b=opp.sell_price,
                    size=effective_size,
                )
            elif use_conservative:
                filter_analysis = self._fee_calc.calculate_net_spread_conservative(
                    buy_platform=opp.buy_platform,
                    buy_price=opp.buy_price,
                    sell_platform=opp.sell_platform,
                    sell_price=opp.sell_price,
                    size=effective_size,
                )
            else:
                filter_analysis = self._fee_calc.calculate_net_spread(
                    buy_platform=opp.buy_platform,
                    buy_price=opp.buy_price,
                    sell_platform=opp.sell_platform,
                    sell_price=opp.sell_price,
                    size=effective_size,
                    buy_maker=use_maker,
                    sell_maker=use_maker,
                )

            # FILTER 1: Explicit negative edge rejection
            # This catches any opportunities that have negative net spread
            # which indicates an upstream bug or stale quotes
            if filter_analysis.net_spread <= 0:
                logger.warning(
                    "Rejected negative edge opportunity: %s net_spread=%.4f "
                    "(buy=%.2f@%s, sell=%.2f@%s)",
                    opp.opportunity_id if hasattr(opp, "opportunity_id") else "unknown",
                    filter_analysis.net_spread,
                    opp.buy_price,
                    opp.buy_platform.value,
                    opp.sell_price,
                    opp.sell_platform.value,
                )
                continue

            # FILTER 2: Check minimum profitable size
            # Skip opportunities where available liquidity is insufficient
            if opp.opportunity_type != "dutch_book":
                min_size = self._fee_calc.calculate_min_profitable_size(
                    buy_platform=opp.buy_platform,
                    buy_price=opp.buy_price,
                    sell_platform=opp.sell_platform,
                    sell_price=opp.sell_price,
                    buy_maker=False if use_conservative else use_maker,
                    sell_maker=False if use_conservative else use_maker,
                )
                if min_size == float("inf"):
                    logger.debug(
                        "Filtered: never profitable at current spread %.4f",
                        filter_analysis.net_spread,
                    )
                    continue
                if effective_size < min_size:
                    logger.debug(
                        "Filtered: insufficient size %d < min profitable %d",
                        effective_size,
                        min_size,
                    )
                    continue

            # FILTER 3: Filter by ROI threshold (using conservative analysis)
            if filter_analysis.roi < self._config.min_roi_pct:
                logger.debug(
                    "Filtered opportunity: ROI %.2f%% below threshold %.2f%%",
                    filter_analysis.roi * 100,
                    self._config.min_roi_pct * 100,
                )
                continue

            # FILTER 4: Filter by net edge (using conservative analysis)
            min_edge = self._config.min_edge_cents / 100
            if filter_analysis.net_spread < min_edge:
                logger.debug(
                    "Filtered opportunity: net edge %.4f below threshold %.4f",
                    filter_analysis.net_spread,
                    min_edge,
                )
                continue

            # Now calculate the ACTUAL expected analysis for ranking and display
            # This uses the preferred maker/taker setting without safety margin
            if opp.opportunity_type == "dutch_book":
                actual_analysis = filter_analysis  # Same for dutch book
            else:
                actual_analysis = self._fee_calc.calculate_net_spread(
                    buy_platform=opp.buy_platform,
                    buy_price=opp.buy_price,
                    sell_platform=opp.sell_platform,
                    sell_price=opp.sell_price,
                    size=effective_size,
                    buy_maker=use_maker,
                    sell_maker=use_maker,
                )

            # Calculate rank score using actual (optimistic) analysis
            rank_score = self._calculate_rank_score(opp, actual_analysis)

            # Create a modified opportunity with the depth-limited size
            limited_opp = SpreadOpportunity(
                pair=opp.pair,
                opportunity_type=opp.opportunity_type,
                buy_platform=opp.buy_platform,
                buy_market_id=opp.buy_market_id,
                buy_outcome=opp.buy_outcome,
                buy_price=opp.buy_price,
                sell_platform=opp.sell_platform,
                sell_market_id=opp.sell_market_id,
                sell_outcome=opp.sell_outcome,
                sell_price=opp.sell_price,
                gross_edge_per_contract=opp.gross_edge_per_contract,
                net_edge_per_contract=actual_analysis.net_spread,
                total_fees_per_contract=actual_analysis.total_fees / effective_size
                if effective_size > 0
                else 0,
                max_contracts=effective_size,  # Use depth-limited size
                available_liquidity_usd=opp.available_liquidity_usd * max_depth_pct,
                estimated_profit_usd=actual_analysis.estimated_profit,
            )

            ranked.append(
                RankedOpportunity(
                    opportunity=limited_opp,
                    analysis=actual_analysis,
                    rank_score=rank_score,
                )
            )

        # Sort by rank score (highest first)
        ranked.sort(key=lambda r: r.rank_score, reverse=True)

        # Limit to top opportunities
        max_opportunities = self._config.max_concurrent_spreads * 2
        return ranked[:max_opportunities]

    def _filter_and_rank_list(
        self, opportunities: List[Tuple[SpreadOpportunity, SpreadAnalysis]]
    ) -> List[RankedOpportunity]:
        """Filter and rank a list of (opportunity, analysis) tuples."""
        ranked = []
        use_maker = self._config.prefer_maker_orders
        use_conservative = self._config.use_conservative_fees_for_filtering
        max_depth_pct = self._config.max_depth_usage_pct

        for opp, analysis in opportunities:
            # Apply depth limiting
            effective_size = max(1, int(opp.max_contracts * max_depth_pct))

            # Recalculate with conservative fees if configured
            if use_conservative and opp.opportunity_type != "dutch_book":
                filter_analysis = self._fee_calc.calculate_net_spread_conservative(
                    buy_platform=opp.buy_platform,
                    buy_price=opp.buy_price,
                    sell_platform=opp.sell_platform,
                    sell_price=opp.sell_price,
                    size=effective_size,
                )
            else:
                filter_analysis = analysis

            # Explicit negative edge rejection
            if filter_analysis.net_spread <= 0:
                logger.warning(
                    "Rejected negative edge opportunity: net_spread=%.4f",
                    filter_analysis.net_spread,
                )
                continue

            # Check minimum profitable size (skip for dutch book)
            if opp.opportunity_type != "dutch_book":
                min_size = self._fee_calc.calculate_min_profitable_size(
                    buy_platform=opp.buy_platform,
                    buy_price=opp.buy_price,
                    sell_platform=opp.sell_platform,
                    sell_price=opp.sell_price,
                    buy_maker=False if use_conservative else use_maker,
                    sell_maker=False if use_conservative else use_maker,
                )
                if min_size == float("inf") or effective_size < min_size:
                    continue

            if filter_analysis.roi < self._config.min_roi_pct:
                continue

            min_edge = self._config.min_edge_cents / 100
            if filter_analysis.net_spread < min_edge:
                continue

            rank_score = self._calculate_rank_score(opp, analysis)
            ranked.append(
                RankedOpportunity(
                    opportunity=opp,
                    analysis=analysis,
                    rank_score=rank_score,
                )
            )

        ranked.sort(key=lambda r: r.rank_score, reverse=True)
        return ranked

    def _calculate_rank_score(
        self, opp: SpreadOpportunity, analysis: SpreadAnalysis
    ) -> float:
        """Calculate a combined ranking score for an opportunity.

        Score components:
        - ROI contribution (40%): Higher ROI = higher score
        - Profit contribution (35%): Higher absolute profit = higher score
        - Liquidity contribution (25%): More liquidity = higher confidence

        Args:
            opp: The spread opportunity
            analysis: Fee analysis results

        Returns:
            Combined score (0-100 range)
        """
        # ROI score (0-40 points)
        # 2% ROI = 16 points, 5% ROI = 40 points
        roi_score = min(40, analysis.roi * 100 * 8)

        # Profit score (0-35 points)
        # $10 profit = 7 points, $50+ profit = 35 points
        profit_score = min(35, analysis.estimated_profit * 0.7)

        # Liquidity score (0-25 points)
        # $500 liquidity = 6.25 points, $2000+ = 25 points
        liq_score = min(25, opp.available_liquidity_usd / 80)

        return roi_score + profit_score + liq_score

    def _check_cross_platform(
        self,
        pair: MatchedMarketPair,
        buy_quote: MarketQuote,
        sell_quote: MarketQuote,
        outcome: str,
    ) -> List[Tuple[SpreadOpportunity, SpreadAnalysis]]:
        """Check for cross-platform arbitrage opportunity."""
        opportunities = []

        if buy_quote.best_ask is None or sell_quote.best_bid is None:
            return opportunities

        # Quick check: is there a gross spread?
        if sell_quote.best_bid <= buy_quote.best_ask:
            return opportunities

        # Apply depth limiting to reduce partial fills
        max_depth_pct = self._config.max_depth_usage_pct
        raw_available = min(buy_quote.ask_size, sell_quote.bid_size)
        available_contracts = max(1, int(raw_available * max_depth_pct))

        # Calculate with fees, using maker preference from config
        use_maker = self._config.prefer_maker_orders
        analysis = self._fee_calc.calculate_net_spread(
            buy_platform=buy_quote.platform,
            buy_price=buy_quote.best_ask,
            sell_platform=sell_quote.platform,
            sell_price=sell_quote.best_bid,
            size=available_contracts,
            buy_maker=use_maker,
            sell_maker=use_maker,
        )

        if not analysis.is_profitable:
            return opportunities

        available_usd = (
            min(buy_quote.ask_depth_usd, sell_quote.bid_depth_usd) * max_depth_pct
        )

        opp = SpreadOpportunity(
            pair=pair,
            opportunity_type="cross_platform_arb",
            buy_platform=buy_quote.platform,
            buy_market_id=buy_quote.market_id,
            buy_outcome=outcome,
            buy_price=buy_quote.best_ask,
            sell_platform=sell_quote.platform,
            sell_market_id=sell_quote.market_id,
            sell_outcome=outcome,
            sell_price=sell_quote.best_bid,
            gross_edge_per_contract=sell_quote.best_bid - buy_quote.best_ask,
            net_edge_per_contract=analysis.net_spread,
            total_fees_per_contract=analysis.total_fees / available_contracts
            if available_contracts > 0
            else 0,
            max_contracts=available_contracts,
            available_liquidity_usd=available_usd,
            estimated_profit_usd=analysis.estimated_profit,
        )

        opportunities.append((opp, analysis))
        return opportunities

    def _check_dutch_book(
        self,
        pair: MatchedMarketPair,
        quote_a: MarketQuote,
        quote_b: MarketQuote,
    ) -> List[Tuple[SpreadOpportunity, SpreadAnalysis]]:
        """Check for dutch book opportunity."""
        opportunities = []

        if quote_a.best_ask is None or quote_b.best_ask is None:
            return opportunities

        # Quick check: do prices sum to less than 1?
        if quote_a.best_ask + quote_b.best_ask >= 1.0:
            return opportunities

        # Apply depth limiting to reduce partial fills
        max_depth_pct = self._config.max_depth_usage_pct
        raw_available = min(quote_a.ask_size, quote_b.ask_size)
        available_contracts = max(1, int(raw_available * max_depth_pct))

        # Calculate with fees
        analysis = self._fee_calc.calculate_dutch_book_spread(
            platform_a=quote_a.platform,
            price_a=quote_a.best_ask,
            platform_b=quote_b.platform,
            price_b=quote_b.best_ask,
            size=available_contracts,
        )

        if not analysis.is_profitable:
            return opportunities

        available_usd = (
            min(quote_a.ask_depth_usd, quote_b.ask_depth_usd) * max_depth_pct
        )

        opp = SpreadOpportunity(
            pair=pair,
            opportunity_type="dutch_book",
            buy_platform=quote_a.platform,
            buy_market_id=quote_a.market_id,
            buy_outcome=quote_a.outcome,
            buy_price=quote_a.best_ask,
            sell_platform=quote_b.platform,
            sell_market_id=quote_b.market_id,
            sell_outcome=quote_b.outcome,
            sell_price=quote_b.best_ask,
            gross_edge_per_contract=1.0 - (quote_a.best_ask + quote_b.best_ask),
            net_edge_per_contract=analysis.net_spread,
            total_fees_per_contract=analysis.total_fees / available_contracts
            if available_contracts > 0
            else 0,
            max_contracts=available_contracts,
            available_liquidity_usd=available_usd,
            estimated_profit_usd=analysis.estimated_profit,
        )

        opportunities.append((opp, analysis))
        return opportunities
