"""
Data Scout Agent - Pattern Detection for Trading Databases

This agent scans trading databases for statistically significant patterns,
including spread anomalies, price movements, mean reversion opportunities,
and momentum signals.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import statistics
import math


@dataclass
class Hypothesis:
    """Structured finding from pattern detection."""
    pattern_type: str  # 'spread_anomaly', 'price_movement', 'mean_reversion', 'momentum'
    ticker: str
    description: str
    confidence: float  # 0-1 scale
    statistical_significance: float  # p-value or z-score
    data_points: int
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    metadata: Dict = field(default_factory=dict)

    def __str__(self):
        return (
            f"[{self.pattern_type.upper()}] {self.ticker}\n"
            f"  {self.description}\n"
            f"  Confidence: {self.confidence:.2%} | Significance: {self.statistical_significance:.4f} | "
            f"  N={self.data_points}"
        )


class DataScoutAgent:
    """
    Agent that scans trading databases for patterns and anomalies.

    Uses statistical methods to detect:
    - Spread anomalies (spreads > 2x average)
    - Price movements (jumps > 2 std dev)
    - Mean reversion (prices far from moving average)
    - Momentum (persistent directional moves)
    """

    def __init__(self, db_path: str = "data/btc_latency_probe.db"):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        """Open database connection."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row

    def disconnect(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()

    def scan_for_patterns(self, min_snapshots: int = 100) -> List[Hypothesis]:
        """
        Scan all pattern types and return findings.

        Args:
            min_snapshots: Minimum number of snapshots required for a ticker to be analyzed

        Returns:
            List of hypotheses sorted by confidence
        """
        if not self.conn:
            self.connect()

        all_hypotheses = []

        # Get tickers with sufficient data
        tickers = self._get_active_tickers(min_snapshots)
        print(f"Scanning {len(tickers)} tickers with >={min_snapshots} snapshots...")

        for ticker in tickers:
            # Spread anomalies
            spread_hyps = self.find_spread_anomalies(ticker)
            all_hypotheses.extend(spread_hyps)

            # Price movements
            price_hyps = self.find_price_movements(ticker)
            all_hypotheses.extend(price_hyps)

            # Mean reversion
            reversion_hyps = self.find_mean_reversion(ticker)
            all_hypotheses.extend(reversion_hyps)

            # Momentum
            momentum_hyps = self.find_momentum(ticker)
            all_hypotheses.extend(momentum_hyps)

        # Sort by confidence descending
        all_hypotheses.sort(key=lambda h: h.confidence, reverse=True)

        return all_hypotheses

    def _get_active_tickers(self, min_snapshots: int) -> List[str]:
        """Get tickers with sufficient data for analysis."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT ticker, COUNT(*) as cnt
            FROM kalshi_snapshots
            WHERE yes_bid IS NOT NULL AND yes_ask IS NOT NULL
            GROUP BY ticker
            HAVING cnt >= ?
            ORDER BY cnt DESC
        """, (min_snapshots,))

        return [row['ticker'] for row in cursor.fetchall()]

    def find_spread_anomalies(self, ticker: str) -> List[Hypothesis]:
        """
        Find spread anomalies (spreads > 2x average).

        Args:
            ticker: Market ticker to analyze

        Returns:
            List of hypotheses for detected anomalies
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                ts,
                yes_bid,
                yes_ask,
                yes_mid,
                (yes_ask - yes_bid) as spread
            FROM kalshi_snapshots
            WHERE ticker = ?
              AND yes_bid IS NOT NULL
              AND yes_ask IS NOT NULL
            ORDER BY ts
        """, (ticker,))

        rows = cursor.fetchall()
        if len(rows) < 10:
            return []

        spreads = [row['spread'] for row in rows]
        avg_spread = statistics.mean(spreads)

        # Find anomalies (spread > 2x average)
        anomalies = [
            (i, row, spread)
            for i, (row, spread) in enumerate(zip(rows, spreads))
            if spread > 2 * avg_spread
        ]

        if not anomalies:
            return []

        # Calculate z-score for the anomaly magnitude
        try:
            std_spread = statistics.stdev(spreads)
            if std_spread == 0:
                return []
        except statistics.StatisticsError:
            return []

        hypotheses = []
        for idx, row, spread in anomalies:
            z_score = (spread - avg_spread) / std_spread if std_spread > 0 else 0

            # Confidence based on how extreme the anomaly is
            # z > 2 -> 95%, z > 3 -> 99.7%
            confidence = min(0.99, 1 - (1 / (1 + abs(z_score))))

            hypothesis = Hypothesis(
                pattern_type='spread_anomaly',
                ticker=ticker,
                description=(
                    f"Spread widened to {spread} cents ({spread/avg_spread:.1f}x average). "
                    f"Avg spread: {avg_spread:.2f} cents"
                ),
                confidence=confidence,
                statistical_significance=z_score,
                data_points=len(rows),
                metadata={
                    'timestamp': row['ts'],
                    'spread': spread,
                    'avg_spread': avg_spread,
                    'std_spread': std_spread,
                    'yes_bid': row['yes_bid'],
                    'yes_ask': row['yes_ask'],
                    'yes_mid': row['yes_mid']
                }
            )
            hypotheses.append(hypothesis)

        return hypotheses

    def find_price_movements(self, ticker: str, window_size: int = 10) -> List[Hypothesis]:
        """
        Find significant price jumps (> 2 std dev from rolling mean).

        Args:
            ticker: Market ticker to analyze
            window_size: Size of rolling window for calculating local mean/std

        Returns:
            List of hypotheses for detected price jumps
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                ts,
                yes_mid
            FROM kalshi_snapshots
            WHERE ticker = ?
              AND yes_mid IS NOT NULL
            ORDER BY ts
        """, (ticker,))

        rows = cursor.fetchall()
        if len(rows) < window_size * 2:
            return []

        prices = [row['yes_mid'] for row in rows]
        hypotheses = []

        # Scan for jumps using rolling window
        for i in range(window_size, len(prices) - 1):
            window = prices[i-window_size:i]
            mean_price = statistics.mean(window)

            try:
                std_price = statistics.stdev(window)
                if std_price == 0:
                    continue
            except statistics.StatisticsError:
                continue

            price_change = prices[i] - mean_price
            z_score = price_change / std_price

            # Detect jumps > 2 std dev
            if abs(z_score) > 2:
                confidence = min(0.99, 1 - (1 / (1 + abs(z_score) / 2)))
                direction = "up" if price_change > 0 else "down"

                hypothesis = Hypothesis(
                    pattern_type='price_movement',
                    ticker=ticker,
                    description=(
                        f"Price jumped {direction} by {abs(price_change):.1f} cents "
                        f"({abs(z_score):.1f} std devs). "
                        f"From {mean_price:.1f} to {prices[i]:.1f}"
                    ),
                    confidence=confidence,
                    statistical_significance=abs(z_score),
                    data_points=len(rows),
                    metadata={
                        'timestamp': rows[i]['ts'],
                        'price_before': mean_price,
                        'price_after': prices[i],
                        'change': price_change,
                        'std_dev': std_price,
                        'direction': direction
                    }
                )
                hypotheses.append(hypothesis)

        return hypotheses

    def find_mean_reversion(self, ticker: str, lookback: int = 50) -> List[Hypothesis]:
        """
        Find mean reversion opportunities (price far from MA).

        Args:
            ticker: Market ticker to analyze
            lookback: Periods to use for moving average calculation

        Returns:
            List of hypotheses for detected reversion opportunities
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                ts,
                yes_mid
            FROM kalshi_snapshots
            WHERE ticker = ?
              AND yes_mid IS NOT NULL
            ORDER BY ts
        """, (ticker,))

        rows = cursor.fetchall()
        if len(rows) < lookback + 10:
            return []

        prices = [row['yes_mid'] for row in rows]
        hypotheses = []

        # Calculate moving average and standard deviation
        for i in range(lookback, len(prices)):
            window = prices[i-lookback:i]
            ma = statistics.mean(window)

            try:
                std = statistics.stdev(window)
                if std == 0:
                    continue
            except statistics.StatisticsError:
                continue

            current_price = prices[i]
            deviation = current_price - ma
            z_score = deviation / std

            # Detect prices > 2 std dev from MA (potential reversion)
            if abs(z_score) > 2:
                confidence = min(0.95, 1 - (1 / (1 + abs(z_score) / 2)))
                direction = "overbought" if deviation > 0 else "oversold"

                hypothesis = Hypothesis(
                    pattern_type='mean_reversion',
                    ticker=ticker,
                    description=(
                        f"Price {direction} at {current_price:.1f} cents "
                        f"({abs(z_score):.1f} std devs from MA of {ma:.1f}). "
                        f"Potential reversion opportunity"
                    ),
                    confidence=confidence,
                    statistical_significance=abs(z_score),
                    data_points=len(rows),
                    metadata={
                        'timestamp': rows[i]['ts'],
                        'current_price': current_price,
                        'moving_average': ma,
                        'deviation': deviation,
                        'std_dev': std,
                        'direction': direction,
                        'lookback': lookback
                    }
                )
                hypotheses.append(hypothesis)

        return hypotheses

    def find_momentum(self, ticker: str, min_streak: int = 5) -> List[Hypothesis]:
        """
        Find momentum patterns (persistent directional moves).

        Args:
            ticker: Market ticker to analyze
            min_streak: Minimum consecutive moves in same direction

        Returns:
            List of hypotheses for detected momentum patterns
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT
                ts,
                yes_mid
            FROM kalshi_snapshots
            WHERE ticker = ?
              AND yes_mid IS NOT NULL
            ORDER BY ts
        """, (ticker,))

        rows = cursor.fetchall()
        if len(rows) < min_streak * 2:
            return []

        prices = [row['yes_mid'] for row in rows]
        hypotheses = []

        # Track consecutive moves
        current_streak = 0
        current_direction = None
        streak_start_idx = 0

        for i in range(1, len(prices)):
            change = prices[i] - prices[i-1]

            if change > 0:
                direction = 'up'
            elif change < 0:
                direction = 'down'
            else:
                direction = None

            if direction == current_direction and direction is not None:
                current_streak += 1
            else:
                # Check if previous streak was significant
                if current_streak >= min_streak:
                    hypotheses.append(self._create_momentum_hypothesis(
                        ticker, rows, prices, streak_start_idx, i-1,
                        current_direction, current_streak
                    ))

                # Start new streak
                current_direction = direction
                current_streak = 1
                streak_start_idx = i

        # Check final streak
        if current_streak >= min_streak:
            hypotheses.append(self._create_momentum_hypothesis(
                ticker, rows, prices, streak_start_idx, len(prices)-1,
                current_direction, current_streak
            ))

        return hypotheses

    def _create_momentum_hypothesis(
        self, ticker: str, rows: List, prices: List[float],
        start_idx: int, end_idx: int, direction: str, streak: int
    ) -> Hypothesis:
        """Helper to create momentum hypothesis."""
        start_price = prices[start_idx]
        end_price = prices[end_idx]
        total_move = end_price - start_price
        avg_move = total_move / streak

        # Calculate statistical significance using t-test approximation
        # Confidence increases with streak length and consistency
        confidence = min(0.95, 0.5 + (streak / 20))

        # Use streak length as a measure of significance
        significance = streak / 5.0  # Normalized to typical streaks

        hypothesis = Hypothesis(
            pattern_type='momentum',
            ticker=ticker,
            description=(
                f"Strong {direction} momentum: {streak} consecutive moves, "
                f"total change {abs(total_move):.1f} cents "
                f"(avg {abs(avg_move):.2f} per move)"
            ),
            confidence=confidence,
            statistical_significance=significance,
            data_points=streak,
            metadata={
                'start_ts': rows[start_idx]['ts'],
                'end_ts': rows[end_idx]['ts'],
                'start_price': start_price,
                'end_price': end_price,
                'total_move': total_move,
                'avg_move': avg_move,
                'direction': direction,
                'streak_length': streak
            }
        )

        return hypothesis

    def calculate_t_statistic(self, sample_mean: float, pop_mean: float,
                            sample_std: float, n: int) -> float:
        """
        Calculate t-statistic for hypothesis testing.

        Args:
            sample_mean: Sample mean
            pop_mean: Population mean (or hypothesized mean)
            sample_std: Sample standard deviation
            n: Sample size

        Returns:
            t-statistic value
        """
        if sample_std == 0 or n == 0:
            return 0.0
        return (sample_mean - pop_mean) / (sample_std / math.sqrt(n))

    def calculate_z_score(self, value: float, mean: float, std: float) -> float:
        """
        Calculate z-score for a value.

        Args:
            value: Value to test
            mean: Mean of distribution
            std: Standard deviation of distribution

        Returns:
            z-score
        """
        if std == 0:
            return 0.0
        return (value - mean) / std


def main():
    """Example usage of DataScoutAgent."""
    import sys

    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/btc_latency_probe.db"

    print(f"Data Scout Agent - Scanning {db_path}")
    print("=" * 80)

    with DataScoutAgent(db_path) as agent:
        hypotheses = agent.scan_for_patterns(min_snapshots=100)

        print(f"\nFound {len(hypotheses)} hypotheses:\n")

        # Group by pattern type
        by_type = {}
        for h in hypotheses:
            by_type.setdefault(h.pattern_type, []).append(h)

        for pattern_type, hyps in sorted(by_type.items()):
            print(f"\n{pattern_type.upper().replace('_', ' ')} ({len(hyps)} findings)")
            print("-" * 80)

            # Show top 3 per type
            for h in hyps[:3]:
                print(h)
                print()

        # Summary statistics
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("-" * 80)
        print(f"Total patterns detected: {len(hypotheses)}")
        for pattern_type, hyps in sorted(by_type.items()):
            avg_conf = statistics.mean([h.confidence for h in hyps])
            print(f"  {pattern_type}: {len(hyps)} (avg confidence: {avg_conf:.2%})")


if __name__ == "__main__":
    main()
