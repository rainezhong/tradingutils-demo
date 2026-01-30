"""Market scoring system for evaluating trading opportunities."""

from typing import Any, Dict, Optional

from src.core import setup_logger

logger = setup_logger(__name__)


class MarketScorer:
    """
    Scores markets based on spread, volume, stability, and depth metrics.

    Scoring criteria (max 20 points):
    - Spread: >5%=5pts, 4-5%=4pts, 3-4%=2pts, <3%=0pts
    - Volume: >5k=5pts, 2-5k=3pts, 1-2k=1pt, <1k=0pts
    - Stability (spread std): <1.5%=5pts, <3%=3pts, <5%=1pt, else=0pts
    - Depth: >100=5pts, >50=3pts, >20=1pt, else=0pts
    """

    MAX_SCORE = 20

    # Spread scoring thresholds (percentage)
    SPREAD_THRESHOLDS = [
        (5.0, 5),   # >5% = 5 points
        (4.0, 4),   # 4-5% = 4 points
        (3.0, 2),   # 3-4% = 2 points
    ]

    # Volume scoring thresholds
    VOLUME_THRESHOLDS = [
        (5000, 5),  # >5k = 5 points
        (2000, 3),  # 2-5k = 3 points
        (1000, 1),  # 1-2k = 1 point
    ]

    # Stability scoring thresholds (lower is better)
    STABILITY_THRESHOLDS = [
        (1.5, 5),   # <1.5% std = 5 points
        (3.0, 3),   # <3% std = 3 points
        (5.0, 1),   # <5% std = 1 point
    ]

    # Depth scoring thresholds
    DEPTH_THRESHOLDS = [
        (100, 5),   # >100 = 5 points
        (50, 3),    # >50 = 3 points
        (20, 1),    # >20 = 1 point
    ]

    def score_market(self, metrics: Dict[str, Any]) -> float:
        """
        Calculate total score for a market based on its metrics.

        Args:
            metrics: Dictionary from MarketMetrics.calculate_metrics()

        Returns:
            Total score (0-20 points)
        """
        spread_score = self._score_spread(metrics.get("avg_spread_pct"))
        volume_score = self._score_volume(metrics.get("avg_volume"))
        stability_score = self._score_stability(metrics.get("spread_volatility"))
        depth_score = self._score_depth(metrics.get("avg_depth"))

        total = spread_score + volume_score + stability_score + depth_score

        logger.debug(
            f"Scored {metrics.get('ticker', 'unknown')}: "
            f"spread={spread_score}, volume={volume_score}, "
            f"stability={stability_score}, depth={depth_score}, total={total}"
        )

        return total

    def score_market_detailed(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate detailed score breakdown for a market.

        Args:
            metrics: Dictionary from MarketMetrics.calculate_metrics()

        Returns:
            Dictionary with individual scores and total
        """
        spread_score = self._score_spread(metrics.get("avg_spread_pct"))
        volume_score = self._score_volume(metrics.get("avg_volume"))
        stability_score = self._score_stability(metrics.get("spread_volatility"))
        depth_score = self._score_depth(metrics.get("avg_depth"))

        return {
            "ticker": metrics.get("ticker"),
            "spread_score": spread_score,
            "volume_score": volume_score,
            "stability_score": stability_score,
            "depth_score": depth_score,
            "total_score": spread_score + volume_score + stability_score + depth_score,
            "max_score": self.MAX_SCORE,
        }

    def _score_spread(self, avg_spread_pct: Optional[float]) -> int:
        """
        Score based on average spread percentage.

        Higher spread = more profit potential = higher score.
        """
        if avg_spread_pct is None:
            return 0

        for threshold, points in self.SPREAD_THRESHOLDS:
            if avg_spread_pct > threshold:
                return points
        return 0

    def _score_volume(self, avg_volume: Optional[float]) -> int:
        """
        Score based on average 24h volume.

        Higher volume = more liquidity = higher score.
        """
        if avg_volume is None:
            return 0

        for threshold, points in self.VOLUME_THRESHOLDS:
            if avg_volume > threshold:
                return points
        return 0

    def _score_stability(self, spread_volatility: Optional[float]) -> int:
        """
        Score based on spread volatility (standard deviation).

        Lower volatility = more predictable = higher score.
        """
        if spread_volatility is None:
            return 0

        for threshold, points in self.STABILITY_THRESHOLDS:
            if spread_volatility < threshold:
                return points
        return 0

    def _score_depth(self, avg_depth: Optional[float]) -> int:
        """
        Score based on average orderbook depth.

        Higher depth = more liquidity = higher score.
        """
        if avg_depth is None:
            return 0

        for threshold, points in self.DEPTH_THRESHOLDS:
            if avg_depth > threshold:
                return points
        return 0
