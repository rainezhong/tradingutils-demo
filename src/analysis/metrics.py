"""Market metrics calculator for analyzing historical market data."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.core import MarketDatabase, Snapshot, setup_logger

logger = setup_logger(__name__)


class MarketMetrics:
    """Calculates market metrics from historical snapshot data."""

    def __init__(self, db: Optional[MarketDatabase] = None):
        """
        Initialize the metrics calculator.

        Args:
            db: MarketDatabase instance. Creates new one if not provided.
        """
        self.db = db or MarketDatabase()

    def calculate_metrics(self, ticker: str, days: int = 3) -> Dict[str, Any]:
        """
        Calculate comprehensive metrics for a market over a time period.

        Args:
            ticker: Market ticker symbol
            days: Number of days to analyze (default: 3)

        Returns:
            Dictionary containing calculated metrics.
        """
        snapshots = self._get_snapshots_for_period(ticker, days)

        if not snapshots:
            logger.warning(f"No snapshots found for {ticker} in last {days} days")
            return self._empty_metrics(ticker, days)

        price_position = self._calculate_price_position(snapshots)

        return {
            "ticker": ticker,
            "days_analyzed": days,
            "snapshot_count": len(snapshots),
            "avg_spread_pct": self._calculate_avg_spread(snapshots),
            "spread_volatility": self._calculate_spread_volatility(snapshots),
            "avg_volume": self._calculate_avg_volume(snapshots),
            "volume_trend": self._calculate_volume_trend(snapshots),
            "price_volatility": self._calculate_price_volatility(snapshots),
            "price_range": self._calculate_price_range(snapshots),
            "avg_depth": self._calculate_avg_depth(snapshots),
            "latest_mid": price_position[0],
            "distance_from_extreme": price_position[1],
            "position_label": price_position[2],
        }

    def _get_snapshots_for_period(self, ticker: str, days: int) -> List[Snapshot]:
        """Get snapshots for the specified time period."""
        all_snapshots = self.db.get_snapshots(ticker=ticker, limit=10000)
        if not all_snapshots:
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        filtered = []
        for snap in all_snapshots:
            try:
                ts = snap.timestamp
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                snap_time = datetime.fromisoformat(ts)
                if snap_time.tzinfo is None:
                    snap_time = snap_time.replace(tzinfo=timezone.utc)
                if snap_time >= cutoff:
                    filtered.append(snap)
            except (ValueError, AttributeError):
                continue
        return filtered

    def _empty_metrics(self, ticker: str, days: int) -> Dict[str, Any]:
        """Return empty metrics structure when no data available."""
        return {
            "ticker": ticker,
            "days_analyzed": days,
            "snapshot_count": 0,
            "avg_spread_pct": None,
            "spread_volatility": None,
            "avg_volume": None,
            "volume_trend": None,
            "price_volatility": None,
            "price_range": (None, None),
            "avg_depth": None,
            "latest_mid": None,
            "distance_from_extreme": None,
            "position_label": None,
        }

    def _calculate_avg_spread(self, snapshots: List[Snapshot]) -> Optional[float]:
        """Calculate average spread percentage."""
        spreads = [s.spread_pct for s in snapshots if s.spread_pct is not None]
        if not spreads:
            return None
        return float(np.mean(spreads))

    def _calculate_spread_volatility(self, snapshots: List[Snapshot]) -> Optional[float]:
        """Calculate standard deviation of spread percentage."""
        spreads = [s.spread_pct for s in snapshots if s.spread_pct is not None]
        if len(spreads) < 2:
            return None
        return float(np.std(spreads, ddof=1))

    def _calculate_avg_volume(self, snapshots: List[Snapshot]) -> Optional[float]:
        """Calculate average 24h volume."""
        volumes = [s.volume_24h for s in snapshots if s.volume_24h is not None]
        if not volumes:
            return None
        return float(np.mean(volumes))

    def _calculate_volume_trend(self, snapshots: List[Snapshot]) -> Optional[float]:
        """Calculate volume trend using linear regression slope."""
        data_points = [
            (s.timestamp, s.volume_24h)
            for s in snapshots
            if s.volume_24h is not None
        ]
        if len(data_points) < 2:
            return None
        data_points.sort(key=lambda x: x[0])
        y = np.array([v for _, v in data_points])
        x = np.arange(len(y))
        if len(x) < 2:
            return None
        slope, _ = np.polyfit(x, y, 1)
        return float(slope)

    def _calculate_price_volatility(self, snapshots: List[Snapshot]) -> Optional[float]:
        """Calculate standard deviation of mid-price."""
        prices = [s.mid_price for s in snapshots if s.mid_price is not None]
        if len(prices) < 2:
            return None
        return float(np.std(prices, ddof=1))

    def _calculate_price_range(self, snapshots: List[Snapshot]) -> tuple[Optional[float], Optional[float]]:
        """Calculate min and max mid-price."""
        prices = [s.mid_price for s in snapshots if s.mid_price is not None]
        if not prices:
            return (None, None)
        return (float(min(prices)), float(max(prices)))

    def _calculate_avg_depth(self, snapshots: List[Snapshot]) -> Optional[float]:
        """Calculate average total orderbook depth (bid + ask)."""
        depths = []
        for s in snapshots:
            bid_depth = s.orderbook_bid_depth or 0
            ask_depth = s.orderbook_ask_depth or 0
            if bid_depth > 0 or ask_depth > 0:
                depths.append(bid_depth + ask_depth)
        if not depths:
            return None
        return float(np.mean(depths))

    def _calculate_price_position(
        self, snapshots: List[Snapshot]
    ) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """
        Calculate price position metrics for the market.

        Analyzes where the current price sits in the 0-100 range and how much
        room there is to move (distance from extremes).

        Args:
            snapshots: List of market snapshots (should be sorted by time)

        Returns:
            Tuple of:
            - latest_mid: Current mid price (0-100)
            - distance_from_extreme: Minimum distance from 0 or 100 (higher = more room)
            - position_label: 'extreme_low', 'low', 'mid', 'high', 'extreme_high'
        """
        prices = [s.mid_price for s in snapshots if s.mid_price is not None]
        if not prices:
            return (None, None, None)

        # Get the most recent price (snapshots should be time-ordered)
        # Find the latest timestamp to get the most recent price
        latest_snapshot = None
        for s in snapshots:
            if s.mid_price is not None:
                if latest_snapshot is None:
                    latest_snapshot = s
                else:
                    try:
                        current_ts = s.timestamp
                        latest_ts = latest_snapshot.timestamp
                        if current_ts > latest_ts:
                            latest_snapshot = s
                    except (ValueError, AttributeError):
                        continue

        if latest_snapshot is None or latest_snapshot.mid_price is None:
            return (None, None, None)

        latest_mid = float(latest_snapshot.mid_price)

        # Calculate distance from nearest extreme (0 or 100)
        distance_from_low = latest_mid
        distance_from_high = 100 - latest_mid
        distance_from_extreme = min(distance_from_low, distance_from_high)

        # Determine position label
        if latest_mid <= 10:
            position_label = "extreme_low"
        elif latest_mid <= 30:
            position_label = "low"
        elif latest_mid <= 70:
            position_label = "mid"
        elif latest_mid <= 90:
            position_label = "high"
        else:
            position_label = "extreme_high"

        return (latest_mid, float(distance_from_extreme), position_label)
