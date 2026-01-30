"""Market ranking system for identifying top trading opportunities."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from src.core import MarketDatabase, setup_logger

from .metrics import MarketMetrics
from .scorer import MarketScorer
from .strategy import StrategyLabeler, TradingStrategy

logger = setup_logger(__name__)


@dataclass
class TradabilityFilter:
    """Configuration for filtering untradeable markets.

    Attributes:
        exclude_closed: Exclude markets with status != 'open'
        exclude_expired: Exclude markets with close_time in the past
        exclude_stale: Exclude markets with no snapshots in stale_hours
        stale_hours: Hours without snapshots to consider a market stale
    """

    exclude_closed: bool = True
    exclude_expired: bool = True
    exclude_stale: bool = True
    stale_hours: int = 24


class MarketRanker:
    """
    Ranks all markets in the database by their scores.

    Combines MarketMetrics and MarketScorer to analyze and rank
    all available markets.
    """

    def __init__(
        self,
        db: Optional[MarketDatabase] = None,
        metrics_calculator: Optional[MarketMetrics] = None,
        scorer: Optional[MarketScorer] = None,
        strategy_labeler: Optional[StrategyLabeler] = None,
    ):
        """
        Initialize the market ranker.

        Args:
            db: MarketDatabase instance
            metrics_calculator: MarketMetrics instance
            scorer: MarketScorer instance
            strategy_labeler: StrategyLabeler instance for strategy classification
        """
        self.db = db or MarketDatabase()
        self.metrics = metrics_calculator or MarketMetrics(db=self.db)
        self.scorer = scorer or MarketScorer()
        self.strategy_labeler = strategy_labeler or StrategyLabeler(db=self.db)

    def _is_tradeable(
        self,
        market: Any,
        metrics: Dict[str, Any],
        filter_config: TradabilityFilter,
    ) -> bool:
        """
        Check if a market is tradeable based on filter criteria.

        Args:
            market: Market object with status and close_time attributes
            metrics: Calculated metrics including snapshot_count
            filter_config: TradabilityFilter configuration

        Returns:
            True if market passes all tradability checks, False otherwise
        """
        # Check status (closed/settled markets)
        if filter_config.exclude_closed:
            if market.status and market.status.lower() != "open":
                return False

        # Check expiration (close_time in past)
        if filter_config.exclude_expired and market.close_time:
            try:
                close_time_str = market.close_time
                if close_time_str.endswith("Z"):
                    close_time_str = close_time_str[:-1] + "+00:00"
                close_time = datetime.fromisoformat(close_time_str)
                if close_time.tzinfo is None:
                    close_time = close_time.replace(tzinfo=timezone.utc)
                if close_time < datetime.now(timezone.utc):
                    return False
            except (ValueError, AttributeError):
                pass  # If we can't parse, don't filter

        # Check staleness (no recent snapshots)
        if filter_config.exclude_stale:
            snapshot_count = metrics.get("snapshot_count", 0)
            if snapshot_count == 0:
                return False

        return True

    def get_top_markets(
        self,
        n: int = 10,
        min_score: float = 12.0,
        days: int = 3,
        include_strategies: bool = False,
        filter_untradeable: bool = False,
        tradability_filter: Optional[TradabilityFilter] = None,
    ) -> pd.DataFrame:
        """
        Get top N markets meeting minimum score threshold.

        Args:
            n: Maximum number of markets to return
            min_score: Minimum score threshold (default: 12)
            days: Number of days for metrics calculation
            include_strategies: Whether to include strategy labels
            filter_untradeable: Whether to filter out untradeable markets
            tradability_filter: Custom TradabilityFilter config (uses defaults if None)

        Returns:
            DataFrame sorted by score (descending) with columns:
            - ticker, title, score, avg_spread_pct, avg_volume,
              spread_volatility, avg_depth, snapshot_count
            - If include_strategies: best_strategy, strategy_score, all_strategies
        """
        all_rankings = self._rank_all_markets(
            days=days,
            include_strategies=include_strategies,
            filter_untradeable=filter_untradeable,
            tradability_filter=tradability_filter,
        )

        if all_rankings.empty:
            return all_rankings

        # Filter by minimum score
        filtered = all_rankings[all_rankings["score"] >= min_score]

        # Return top N
        return filtered.head(n).reset_index(drop=True)

    def get_all_rankings(
        self,
        days: int = 3,
        include_strategies: bool = False,
        filter_untradeable: bool = False,
        tradability_filter: Optional[TradabilityFilter] = None,
    ) -> pd.DataFrame:
        """
        Get rankings for all markets without filtering.

        Args:
            days: Number of days for metrics calculation
            include_strategies: Whether to include strategy labels
            filter_untradeable: Whether to filter out untradeable markets
            tradability_filter: Custom TradabilityFilter config (uses defaults if None)

        Returns:
            DataFrame sorted by score (descending)
        """
        return self._rank_all_markets(
            days=days,
            include_strategies=include_strategies,
            filter_untradeable=filter_untradeable,
            tradability_filter=tradability_filter,
        ).reset_index(drop=True)

    def get_market_summary(self, ticker: str, days: int = 3) -> Dict[str, Any]:
        """
        Get detailed summary for a specific market.

        Args:
            ticker: Market ticker symbol
            days: Number of days for metrics calculation

        Returns:
            Dictionary containing:
            - Market info (ticker, title)
            - All calculated metrics
            - Score breakdown
            - Strategy labels
        """
        market = self.db.get_market(ticker)
        if not market:
            logger.warning(f"Market {ticker} not found in database")
            return {"ticker": ticker, "error": "Market not found"}

        metrics = self.metrics.calculate_metrics(ticker, days=days)
        score_breakdown = self.scorer.score_market_detailed(metrics)
        strategy_labels = self.strategy_labeler.label_market(ticker, metrics)

        return {
            "ticker": ticker,
            "title": market.title,
            "category": market.category,
            "status": market.status,
            "metrics": metrics,
            "scores": score_breakdown,
            "strategies": [
                {
                    "strategy": label.strategy.value,
                    "suitability_score": label.suitability_score,
                    "reasons": label.reasons,
                }
                for label in strategy_labels
            ],
        }

    def export_to_csv(
        self,
        filename: str,
        days: int = 3,
        min_score: Optional[float] = None,
        include_strategies: bool = False,
    ) -> Path:
        """
        Export rankings to CSV file.

        Args:
            filename: Output filename (will be created in current directory)
            days: Number of days for metrics calculation
            min_score: Optional minimum score filter
            include_strategies: Whether to include strategy labels

        Returns:
            Path to the created CSV file
        """
        rankings = self._rank_all_markets(
            days=days, include_strategies=include_strategies
        )

        if min_score is not None:
            rankings = rankings[rankings["score"] >= min_score]

        output_path = Path(filename)
        rankings.to_csv(output_path, index=False)

        logger.info(f"Exported {len(rankings)} markets to {output_path}")
        return output_path

    def get_markets_by_strategy(
        self,
        strategy: str,
        min_suitability: float = 0.0,
        days: int = 3,
        filter_untradeable: bool = False,
        tradability_filter: Optional[TradabilityFilter] = None,
    ) -> pd.DataFrame:
        """
        Get markets suitable for a specific trading strategy.

        Args:
            strategy: Strategy name (e.g., "market_making", "scalping")
            min_suitability: Minimum suitability score threshold
            days: Number of days for metrics calculation
            filter_untradeable: Whether to filter out untradeable markets
            tradability_filter: Custom TradabilityFilter config (uses defaults if None)

        Returns:
            DataFrame of markets with the specified strategy, sorted by suitability
        """
        # Validate strategy name
        try:
            TradingStrategy(strategy)
        except ValueError:
            valid = [s.value for s in TradingStrategy]
            logger.warning(f"Invalid strategy '{strategy}'. Valid: {valid}")
            return pd.DataFrame()

        all_rankings = self._rank_all_markets(
            days=days,
            include_strategies=True,
            filter_untradeable=filter_untradeable,
            tradability_filter=tradability_filter,
        )

        if all_rankings.empty:
            return all_rankings

        # Filter to markets that have this strategy
        filtered = all_rankings[
            all_rankings["all_strategies"].apply(
                lambda x: strategy in x if isinstance(x, dict) else False
            )
        ].copy()

        if filtered.empty:
            return filtered

        # Add strategy-specific suitability score column
        filtered["strategy_suitability"] = filtered["all_strategies"].apply(
            lambda x: x.get(strategy, {}).get("score", 0) if isinstance(x, dict) else 0
        )

        # Filter by minimum suitability
        filtered = filtered[filtered["strategy_suitability"] >= min_suitability]

        # Sort by strategy suitability descending
        filtered = filtered.sort_values("strategy_suitability", ascending=False)

        return filtered.reset_index(drop=True)

    def get_strategy_matrix(
        self,
        days: int = 3,
        filter_untradeable: bool = False,
        tradability_filter: Optional[TradabilityFilter] = None,
    ) -> pd.DataFrame:
        """
        Get a clean matrix view of strategy suitability for all markets.

        Returns a DataFrame with one row per market and columns for each
        strategy's suitability score (0-10), making it easy to compare
        which markets are best for which strategies.

        Args:
            days: Number of days for metrics calculation
            filter_untradeable: Whether to filter out untradeable markets
            tradability_filter: Custom TradabilityFilter config (uses defaults if None)

        Returns:
            DataFrame with columns:
            - ticker: Market ticker
            - title: Market title
            - status: Market status
            - mm_score: Market-making score (0-20)
            - market_making: Market making suitability (0-10)
            - spread_trading: Spread trading suitability (0-10)
            - momentum: Momentum trading suitability (0-10)
            - scalping: Scalping suitability (0-10)
            - arbitrage: Arbitrage suitability (0-10)
            - event_trading: Event trading suitability (0-10)
            - best_strategy: Name of the best strategy for this market
        """
        all_rankings = self._rank_all_markets(
            days=days,
            include_strategies=True,
            filter_untradeable=filter_untradeable,
            tradability_filter=tradability_filter,
        )

        if all_rankings.empty:
            return pd.DataFrame()

        # Build the matrix
        rows = []
        for _, row in all_rankings.iterrows():
            all_strategies = row.get("all_strategies", {})

            matrix_row = {
                "ticker": row["ticker"],
                "title": row["title"],
                "status": row["status"],
                "mm_score": row["score"],
                "market_making": all_strategies.get("market_making", {}).get("score", 0),
                "spread_trading": all_strategies.get("spread_trading", {}).get("score", 0),
                "momentum": all_strategies.get("momentum", {}).get("score", 0),
                "scalping": all_strategies.get("scalping", {}).get("score", 0),
                "arbitrage": all_strategies.get("arbitrage", {}).get("score", 0),
                "event_trading": all_strategies.get("event_trading", {}).get("score", 0),
                "best_strategy": row.get("best_strategy"),
            }
            rows.append(matrix_row)

        return pd.DataFrame(rows)

    def _rank_all_markets(
        self,
        days: int = 3,
        include_strategies: bool = False,
        filter_untradeable: bool = False,
        tradability_filter: Optional[TradabilityFilter] = None,
    ) -> pd.DataFrame:
        """
        Calculate metrics and scores for all markets.

        Args:
            days: Number of days for metrics calculation
            include_strategies: Whether to include strategy labels in output
            filter_untradeable: Whether to filter out untradeable markets
            tradability_filter: Custom TradabilityFilter config (uses defaults if None)

        Returns:
            DataFrame sorted by score (descending)
        """
        markets = self.db.get_all_markets()

        if not markets:
            logger.warning("No markets found in database")
            return pd.DataFrame()

        # Use default filter config if not provided
        filter_config = tradability_filter or TradabilityFilter()

        results = []
        for market in markets:
            try:
                metrics = self.metrics.calculate_metrics(market.ticker, days=days)

                # Apply tradability filter if enabled
                if filter_untradeable:
                    if not self._is_tradeable(market, metrics, filter_config):
                        continue

                score = self.scorer.score_market(metrics)

                row = {
                    "ticker": market.ticker,
                    "title": market.title,
                    "category": market.category,
                    "status": market.status,
                    "score": score,
                    "avg_spread_pct": metrics.get("avg_spread_pct"),
                    "avg_volume": metrics.get("avg_volume"),
                    "spread_volatility": metrics.get("spread_volatility"),
                    "avg_depth": metrics.get("avg_depth"),
                    "price_volatility": metrics.get("price_volatility"),
                    "volume_trend": metrics.get("volume_trend"),
                    "snapshot_count": metrics.get("snapshot_count"),
                }

                if include_strategies:
                    labels = self.strategy_labeler.label_market(market.ticker, metrics)
                    if labels:
                        best = labels[0]
                        row["best_strategy"] = best.strategy.value
                        row["strategy_score"] = best.suitability_score
                        row["all_strategies"] = {
                            label.strategy.value: {
                                "score": label.suitability_score,
                                "reasons": label.reasons,
                            }
                            for label in labels
                        }
                    else:
                        row["best_strategy"] = None
                        row["strategy_score"] = None
                        row["all_strategies"] = {}

                results.append(row)
            except Exception as e:
                logger.error(f"Error analyzing {market.ticker}: {e}")
                continue

        df = pd.DataFrame(results)

        if not df.empty:
            df = df.sort_values("score", ascending=False)

        return df
