"""Trading strategy labeling system for market classification."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.core import MarketDatabase, setup_logger

from .correlation import CorrelationDetector

logger = setup_logger(__name__)


class TradingStrategy(Enum):
    """Trading strategy types."""

    MARKET_MAKING = "market_making"
    SPREAD_TRADING = "spread_trading"
    MOMENTUM = "momentum"
    SCALPING = "scalping"
    ARBITRAGE = "arbitrage"
    EVENT_TRADING = "event_trading"


@dataclass
class StrategyLabel:
    """Label indicating strategy suitability for a market."""

    strategy: TradingStrategy
    suitability_score: float  # 0-10
    reasons: List[str] = field(default_factory=list)


class StrategyLabeler:
    """
    Labels markets with applicable trading strategies and suitability scores.

    Evaluates markets against criteria for various trading strategies
    including market making, spread trading, momentum, scalping,
    arbitrage, and event trading.
    """

    def __init__(
        self,
        db: Optional[MarketDatabase] = None,
        correlation_detector: Optional[CorrelationDetector] = None,
    ):
        """
        Initialize the strategy labeler.

        Args:
            db: MarketDatabase instance (needed for arbitrage/event trading)
            correlation_detector: CorrelationDetector instance for arbitrage detection
        """
        self.db = db or MarketDatabase()
        self.correlation_detector = correlation_detector or CorrelationDetector(db=self.db)

    def label_market(self, ticker: str, metrics: Dict[str, Any]) -> List[StrategyLabel]:
        """
        Label a market with all applicable trading strategies.

        Args:
            ticker: Market ticker symbol
            metrics: Dictionary of calculated market metrics

        Returns:
            List of StrategyLabel objects, sorted by suitability score descending
        """
        labels = []

        # Evaluate each strategy
        market_making = self._evaluate_market_making(metrics)
        if market_making:
            labels.append(market_making)

        spread_trading = self._evaluate_spread_trading(metrics)
        if spread_trading:
            labels.append(spread_trading)

        momentum = self._evaluate_momentum(metrics)
        if momentum:
            labels.append(momentum)

        scalping = self._evaluate_scalping(metrics)
        if scalping:
            labels.append(scalping)

        arbitrage = self._evaluate_arbitrage(ticker, metrics)
        if arbitrage:
            labels.append(arbitrage)

        event_trading = self._evaluate_event_trading(ticker, metrics)
        if event_trading:
            labels.append(event_trading)

        # Sort by suitability score descending
        labels.sort(key=lambda x: x.suitability_score, reverse=True)

        return labels

    def label_market_as_tuples(
        self, ticker: str, metrics: Dict[str, Any]
    ) -> List[Tuple[str, float, str]]:
        """
        Label a market and return as list of tuples for easier display.

        Args:
            ticker: Market ticker symbol
            metrics: Dictionary of calculated market metrics

        Returns:
            List of (strategy_name, suitability_score, reasons_string) tuples
        """
        labels = self.label_market(ticker, metrics)
        return [
            (label.strategy.value, label.suitability_score, "; ".join(label.reasons))
            for label in labels
        ]

    def get_best_strategy(
        self, ticker: str, metrics: Dict[str, Any]
    ) -> Optional[StrategyLabel]:
        """
        Get the best strategy for a market.

        Args:
            ticker: Market ticker symbol
            metrics: Dictionary of calculated market metrics

        Returns:
            The StrategyLabel with highest suitability score, or None
        """
        labels = self.label_market(ticker, metrics)
        return labels[0] if labels else None

    def evaluate_all_strategies(
        self, ticker: str, metrics: Dict[str, Any]
    ) -> Dict[str, StrategyLabel]:
        """
        Evaluate all strategies for a market.

        Args:
            ticker: Market ticker symbol
            metrics: Dictionary of calculated market metrics

        Returns:
            Dictionary mapping strategy names to their StrategyLabel objects
        """
        labels = self.label_market(ticker, metrics)
        return {label.strategy.value: label for label in labels}

    def _evaluate_market_making(self, metrics: Dict[str, Any]) -> Optional[StrategyLabel]:
        """
        Evaluate market making strategy suitability.

        Market Making criteria (max 10 points):
        - avg_spread_pct: >=5% (+3), 3-5% (+2) - 30%
        - spread_volatility: <1.5% (+3), 1.5-3% (+2) - 30%
        - avg_volume: >=2000 (+2), 1000-2000 (+1) - 20%
        - avg_depth: >=50 (+2), 25-50 (+1) - 20%
        """
        score = 0.0
        reasons = []

        avg_spread = metrics.get("avg_spread_pct")
        spread_vol = metrics.get("spread_volatility")
        avg_volume = metrics.get("avg_volume")
        avg_depth = metrics.get("avg_depth")

        # Spread scoring (30% weight)
        if avg_spread is not None:
            if avg_spread >= 5:
                score += 3
                reasons.append(f"Wide spread ({avg_spread:.1f}%)")
            elif avg_spread >= 3:
                score += 2
                reasons.append(f"Moderate spread ({avg_spread:.1f}%)")

        # Spread stability scoring (30% weight)
        if spread_vol is not None:
            if spread_vol < 1.5:
                score += 3
                reasons.append("Stable spreads")
            elif spread_vol < 3:
                score += 2
                reasons.append("Moderately stable spreads")

        # Volume scoring (20% weight)
        if avg_volume is not None:
            if avg_volume >= 2000:
                score += 2
                reasons.append("Good volume")
            elif avg_volume >= 1000:
                score += 1
                reasons.append("Moderate volume")

        # Depth scoring (20% weight)
        if avg_depth is not None:
            if avg_depth >= 50:
                score += 2
                reasons.append("Good depth")
            elif avg_depth >= 25:
                score += 1
                reasons.append("Moderate depth")

        if score > 0:
            return StrategyLabel(
                strategy=TradingStrategy.MARKET_MAKING,
                suitability_score=score,
                reasons=reasons,
            )
        return None

    def _evaluate_spread_trading(self, metrics: Dict[str, Any]) -> Optional[StrategyLabel]:
        """
        Evaluate spread trading strategy suitability.

        Spread Trading criteria (max 10 points):
        - spread_volatility: >=4% (+4), 2-4% (+2.5) - 40%
        - avg_volume: >=2000 (+3), 1000-2000 (+2) - 30%
        - avg_spread_pct: >=2% (+3), 1-2% (+2) - 30%
        """
        score = 0.0
        reasons = []

        spread_vol = metrics.get("spread_volatility")
        avg_volume = metrics.get("avg_volume")
        avg_spread = metrics.get("avg_spread_pct")

        # Spread volatility scoring (40% weight)
        if spread_vol is not None:
            if spread_vol >= 4:
                score += 4
                reasons.append(f"High spread volatility ({spread_vol:.1f}%)")
            elif spread_vol >= 2:
                score += 2.5
                reasons.append(f"Moderate spread volatility ({spread_vol:.1f}%)")

        # Volume scoring (30% weight)
        if avg_volume is not None:
            if avg_volume >= 2000:
                score += 3
                reasons.append("Good volume")
            elif avg_volume >= 1000:
                score += 2
                reasons.append("Moderate volume")

        # Spread size scoring (30% weight)
        if avg_spread is not None:
            if avg_spread >= 2:
                score += 3
                reasons.append(f"Tradeable spread ({avg_spread:.1f}%)")
            elif avg_spread >= 1:
                score += 2
                reasons.append(f"Narrow spread ({avg_spread:.1f}%)")

        if score > 0:
            return StrategyLabel(
                strategy=TradingStrategy.SPREAD_TRADING,
                suitability_score=score,
                reasons=reasons,
            )
        return None

    def _evaluate_momentum(self, metrics: Dict[str, Any]) -> Optional[StrategyLabel]:
        """
        Evaluate momentum trading strategy suitability.

        Momentum criteria (max 10 points):
        - price_volatility: >=8 cents (+4), 4-8 cents (+2.5) - 40%
        - volume_trend: >+50 (+3), 0 to +50 (+1.5) - 30%
        - avg_depth: <50 (thin) (+2), 50-100 (+1) - 20%
        """
        score = 0.0
        reasons = []

        price_vol = metrics.get("price_volatility")
        volume_trend = metrics.get("volume_trend")
        avg_depth = metrics.get("avg_depth")

        # Price volatility scoring (40% weight)
        if price_vol is not None:
            if price_vol >= 8:
                score += 4
                reasons.append(f"High price volatility ({price_vol:.1f}c)")
            elif price_vol >= 4:
                score += 2.5
                reasons.append(f"Moderate price volatility ({price_vol:.1f}c)")

        # Volume trend scoring (30% weight)
        if volume_trend is not None:
            if volume_trend > 50:
                score += 3
                reasons.append(f"Rising volume trend (+{volume_trend:.0f})")
            elif volume_trend > 0:
                score += 1.5
                reasons.append(f"Slight volume uptick (+{volume_trend:.0f})")

        # Thin book scoring (20% weight) - thin books favor momentum
        if avg_depth is not None:
            if avg_depth < 50:
                score += 2
                reasons.append("Thin order book")
            elif avg_depth < 100:
                score += 1
                reasons.append("Moderate order book depth")

        if score > 0:
            return StrategyLabel(
                strategy=TradingStrategy.MOMENTUM,
                suitability_score=score,
                reasons=reasons,
            )
        return None

    def _evaluate_scalping(self, metrics: Dict[str, Any]) -> Optional[StrategyLabel]:
        """
        Evaluate scalping strategy suitability.

        Scalping criteria (max 10 points):
        - avg_spread_pct: <=2% (+3), 2-3% (+2) - 30%
        - avg_volume: >=5000 (+3), 3000-5000 (+2) - 30%
        - price_volatility: <=3 (+2), 3-6 (+1) - 20%
        - avg_depth: >=100 (+2), 50-100 (+1) - 20%
        """
        score = 0.0
        reasons = []

        avg_spread = metrics.get("avg_spread_pct")
        avg_volume = metrics.get("avg_volume")
        price_vol = metrics.get("price_volatility")
        avg_depth = metrics.get("avg_depth")

        # Tight spread scoring (30% weight)
        if avg_spread is not None:
            if avg_spread <= 2:
                score += 3
                reasons.append(f"Tight spread ({avg_spread:.1f}%)")
            elif avg_spread <= 3:
                score += 2
                reasons.append(f"Acceptable spread ({avg_spread:.1f}%)")

        # High volume scoring (30% weight)
        if avg_volume is not None:
            if avg_volume >= 5000:
                score += 3
                reasons.append("High volume")
            elif avg_volume >= 3000:
                score += 2
                reasons.append("Good volume")

        # Stable price scoring (20% weight)
        if price_vol is not None:
            if price_vol <= 3:
                score += 2
                reasons.append("Stable prices")
            elif price_vol <= 6:
                score += 1
                reasons.append("Moderately stable prices")

        # Deep book scoring (20% weight)
        if avg_depth is not None:
            if avg_depth >= 100:
                score += 2
                reasons.append("Deep order book")
            elif avg_depth >= 50:
                score += 1
                reasons.append("Moderate depth")

        if score > 0:
            return StrategyLabel(
                strategy=TradingStrategy.SCALPING,
                suitability_score=score,
                reasons=reasons,
            )
        return None

    def _evaluate_arbitrage(
        self, ticker: str, metrics: Dict[str, Any]
    ) -> Optional[StrategyLabel]:
        """
        Evaluate arbitrage strategy suitability.

        Arbitrage criteria (max 10 points):
        - has_correlated_markets: yes (+4) - 40%
        - correlation_strength: >=0.8 (+3), 0.6-0.8 (+2) - 30%
        - avg_volume (both): >=2000 (+2), 1000-2000 (+1) - 20%
        - avg_spread_pct: <=3% (+1), 3-5% (+0.5) - 10%
        """
        score = 0.0
        reasons = []

        # Find correlated markets
        try:
            correlated = self.correlation_detector.get_correlated_markets(ticker)
        except Exception as e:
            logger.debug(f"Error checking correlations for {ticker}: {e}")
            correlated = []

        if not correlated:
            return None

        # Has correlated markets (40% weight)
        score += 4
        best_match = correlated[0]  # First match is typically strongest
        reasons.append(f"Correlated with {best_match['ticker']}")

        # Correlation strength proxy - more shared categories = stronger correlation (30% weight)
        shared_categories = len(best_match.get("shared_categories", []))
        if shared_categories >= 2:
            score += 3
            reasons.append(f"Strong correlation ({shared_categories} categories)")
        elif shared_categories >= 1:
            score += 2
            reasons.append(f"Moderate correlation")

        # Volume scoring (20% weight)
        avg_volume = metrics.get("avg_volume")
        if avg_volume is not None:
            if avg_volume >= 2000:
                score += 2
                reasons.append("Good volume")
            elif avg_volume >= 1000:
                score += 1
                reasons.append("Moderate volume")

        # Spread scoring (10% weight)
        avg_spread = metrics.get("avg_spread_pct")
        if avg_spread is not None:
            if avg_spread <= 3:
                score += 1
                reasons.append("Low spread cost")
            elif avg_spread <= 5:
                score += 0.5
                reasons.append("Acceptable spread cost")

        return StrategyLabel(
            strategy=TradingStrategy.ARBITRAGE,
            suitability_score=score,
            reasons=reasons,
        )

    def _evaluate_event_trading(
        self, ticker: str, metrics: Dict[str, Any]
    ) -> Optional[StrategyLabel]:
        """
        Evaluate event trading strategy suitability.

        Event Trading criteria (max 10 points):
        - days_until_close: <=3 (+4), 3-7 (+2) - 40%
        - volume_trend: >+100 (+3), +50 to +100 (+2) - 30%
        - price_near_extreme: <=10 or >=90 (+2), <=20 or >=80 (+1) - 20%
        - avg_volume: >=1000 (+1), 500-1000 (+0.5) - 10%
        """
        score = 0.0
        reasons = []

        # Get market close time from database
        try:
            market = self.db.get_market(ticker)
        except Exception as e:
            logger.debug(f"Error getting market {ticker}: {e}")
            market = None

        days_until_close = None
        if market and market.close_time:
            try:
                close_time_str = market.close_time
                if close_time_str.endswith("Z"):
                    close_time_str = close_time_str[:-1] + "+00:00"
                close_time = datetime.fromisoformat(close_time_str)
                if close_time.tzinfo is None:
                    close_time = close_time.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                days_until_close = (close_time - now).days
            except (ValueError, AttributeError) as e:
                logger.debug(f"Error parsing close time for {ticker}: {e}")

        # Days until close scoring (40% weight)
        if days_until_close is not None:
            if days_until_close <= 3:
                score += 4
                reasons.append(f"Closes in {days_until_close} days")
            elif days_until_close <= 7:
                score += 2
                reasons.append(f"Closes in {days_until_close} days")

        # Volume trend scoring (30% weight)
        volume_trend = metrics.get("volume_trend")
        if volume_trend is not None:
            if volume_trend > 100:
                score += 3
                reasons.append(f"Strong volume surge (+{volume_trend:.0f})")
            elif volume_trend > 50:
                score += 2
                reasons.append(f"Rising volume trend (+{volume_trend:.0f})")

        # Price near extreme scoring (20% weight)
        price_range = metrics.get("price_range", (None, None))
        if price_range and price_range[0] is not None and price_range[1] is not None:
            min_price, max_price = price_range
            # Use latest price (approximate with midpoint of range for now)
            mid_price = (min_price + max_price) / 2
            if mid_price <= 10 or mid_price >= 90:
                score += 2
                reasons.append(f"Price near extreme ({mid_price:.0f}c)")
            elif mid_price <= 20 or mid_price >= 80:
                score += 1
                reasons.append(f"Price trending to extreme ({mid_price:.0f}c)")

        # Volume scoring (10% weight)
        avg_volume = metrics.get("avg_volume")
        if avg_volume is not None:
            if avg_volume >= 1000:
                score += 1
                reasons.append("Active trading")
            elif avg_volume >= 500:
                score += 0.5
                reasons.append("Some trading activity")

        if score > 0:
            return StrategyLabel(
                strategy=TradingStrategy.EVENT_TRADING,
                suitability_score=score,
                reasons=reasons,
            )
        return None
