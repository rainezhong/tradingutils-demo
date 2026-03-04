"""Total Points Over/Under Strategy Backtester.

Runs the total points strategy against recorded game data to evaluate
performance and optimize parameters.

Expected recording format:
{
    "metadata": {
        "game_id": "0022500001",
        "home_team": "LAL",
        "away_team": "BOS",
        "final_home_score": 112,
        "final_away_score": 108,
        ...
    },
    "frames": [{
        "timestamp": 1234567890.0,
        "home_score": 50,
        "away_score": 48,
        "period": 2,
        "time_remaining": "6:30",
        "game_status": "live",
        ...
    }]
}

For total points backtesting, we need to either:
1. Use recordings that include total points market data
2. Simulate market prices from final outcomes (retrospective analysis)
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from strategies.total_points_strategy import (
    TotalPointsConfig,
    TotalPointsStrategy,
)


@dataclass
class TotalPointsFrame:
    """Single frame from a game recording."""

    timestamp: float
    home_score: int
    away_score: int
    period: int
    time_remaining: str
    game_status: str

    @property
    def total_score(self) -> int:
        return self.home_score + self.away_score


@dataclass
class TotalPointsRecording:
    """Recording for total points backtesting."""

    game_id: str
    home_team: str
    away_team: str
    date: str
    frames: List[TotalPointsFrame]

    # Final outcome
    final_home_score: Optional[int] = None
    final_away_score: Optional[int] = None

    @property
    def final_total(self) -> Optional[int]:
        if self.final_home_score is not None and self.final_away_score is not None:
            return self.final_home_score + self.final_away_score
        return None

    @classmethod
    def load(cls, filepath: str) -> "TotalPointsRecording":
        """Load a recording from JSON file.

        Args:
            filepath: Path to the recording file

        Returns:
            TotalPointsRecording instance
        """
        with open(filepath, "r") as f:
            data = json.load(f)

        metadata = data.get("metadata", {})
        frames_data = data.get("frames", [])

        frames = []
        for fd in frames_data:
            frame = TotalPointsFrame(
                timestamp=fd.get("timestamp", 0.0),
                home_score=fd.get("home_score", 0),
                away_score=fd.get("away_score", 0),
                period=fd.get("period", 1),
                time_remaining=fd.get("time_remaining", "12:00"),
                game_status=fd.get("game_status", "live"),
            )
            frames.append(frame)

        # Get final outcome from metadata
        final_home = metadata.get("final_home_score")
        final_away = metadata.get("final_away_score")

        if final_home is None and frames:
            final_home = frames[-1].home_score
        if final_away is None and frames:
            final_away = frames[-1].away_score

        return cls(
            game_id=metadata.get("game_id", "unknown"),
            home_team=metadata.get("home_team", "HOME"),
            away_team=metadata.get("away_team", "AWAY"),
            date=metadata.get("date", ""),
            frames=frames,
            final_home_score=final_home,
            final_away_score=final_away,
        )


@dataclass
class ProjectionSnapshot:
    """Snapshot of projection at a point in time."""

    timestamp: float
    period: int
    time_remaining: str
    current_total: int
    projected_total: float
    blended_pace: float
    boost_applied: float
    sigma: float
    time_remaining_frac: float


@dataclass
class TotalPointsSignalRecord:
    """Record of a signal generated during backtest."""

    timestamp: float
    frame_idx: int
    period: int
    time_remaining: str
    current_total: int

    # Signal details
    line: float
    projected_total: float
    theoretical_over_prob: float
    market_over_prob: float
    edge_cents: float
    direction: str
    blended_pace: float
    boost_applied: float

    # Outcome
    final_total: Optional[int] = None
    signal_correct: Optional[bool] = None
    pnl: Optional[float] = None


@dataclass
class TotalPointsBacktestMetrics:
    """Performance metrics from a total points backtest."""

    # Basic counts
    total_frames: int = 0
    live_frames: int = 0
    total_signals: int = 0

    # Projection accuracy
    projection_errors: List[float] = field(default_factory=list)
    avg_projection_error: float = 0.0
    rmse: float = 0.0

    # Edge analysis
    total_edge_cents: float = 0.0
    avg_edge_cents: float = 0.0
    max_edge_cents: float = 0.0

    # Accuracy
    correct_signals: int = 0
    incorrect_signals: int = 0
    accuracy_pct: float = 0.0

    # P&L
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0

    # By period
    signals_by_period: Dict[int, int] = field(default_factory=dict)
    signals_by_direction: Dict[str, int] = field(default_factory=dict)


@dataclass
class TotalPointsBacktestResult:
    """Complete result of a total points backtest."""

    recording_path: str
    game_id: str
    home_team: str
    away_team: str
    final_total: int

    # Config used
    config: TotalPointsConfig

    # Results
    metrics: TotalPointsBacktestMetrics
    signals: List[TotalPointsSignalRecord]
    projections: List[ProjectionSnapshot]

    # Timing
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "recording_path": self.recording_path,
            "game_id": self.game_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "final_total": self.final_total,
            "config": {
                "min_edge_cents": self.config.min_edge_cents,
                "max_period": self.config.max_period,
                "position_size": self.config.position_size,
                "nba_avg_pace": self.config.nba_avg_pace,
                "pace_blend_start": self.config.pace_blend_start,
                "pace_blend_end": self.config.pace_blend_end,
                "second_half_boost": self.config.second_half_boost,
                "slow_game_threshold": self.config.slow_game_threshold,
                "slow_game_extra_boost": self.config.slow_game_extra_boost,
            },
            "metrics": {
                "total_frames": self.metrics.total_frames,
                "live_frames": self.metrics.live_frames,
                "total_signals": self.metrics.total_signals,
                "avg_projection_error": self.metrics.avg_projection_error,
                "rmse": self.metrics.rmse,
                "avg_edge_cents": self.metrics.avg_edge_cents,
                "max_edge_cents": self.metrics.max_edge_cents,
                "correct_signals": self.metrics.correct_signals,
                "incorrect_signals": self.metrics.incorrect_signals,
                "accuracy_pct": self.metrics.accuracy_pct,
                "gross_pnl": self.metrics.gross_pnl,
                "net_pnl": self.metrics.net_pnl,
                "signals_by_period": self.metrics.signals_by_period,
                "signals_by_direction": self.metrics.signals_by_direction,
            },
            "signals": [
                {
                    "timestamp": s.timestamp,
                    "frame_idx": s.frame_idx,
                    "period": s.period,
                    "time_remaining": s.time_remaining,
                    "current_total": s.current_total,
                    "line": s.line,
                    "projected_total": s.projected_total,
                    "theoretical_prob": s.theoretical_over_prob,
                    "market_prob": s.market_over_prob,
                    "edge_cents": s.edge_cents,
                    "direction": s.direction,
                    "correct": s.signal_correct,
                    "pnl": s.pnl,
                }
                for s in self.signals
            ],
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat(),
        }

    def save(self, filepath: str) -> None:
        """Save result to JSON file."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


class TotalPointsBacktester:
    """Backtester for Total Points Over/Under strategy.

    This backtester evaluates the total points strategy against recorded
    game data. Since recordings may not include actual market prices,
    it can simulate market prices or just evaluate projection accuracy.

    Example usage:
        recording = TotalPointsRecording.load("data/recordings/game.json")
        backtester = TotalPointsBacktester(
            recording=recording,
            config=TotalPointsConfig(second_half_boost=13.5)
        )
        result = await backtester.run()
        print(f"RMSE: {result.metrics.rmse:.1f} points")
    """

    def __init__(
        self,
        recording: TotalPointsRecording,
        config: Optional[TotalPointsConfig] = None,
        test_line: Optional[float] = None,
    ):
        """Initialize the backtester.

        Args:
            recording: Loaded game recording
            config: Strategy configuration
            test_line: Line to test against. If None, uses final_total - 0.5
        """
        self.recording = recording
        self.config = config or TotalPointsConfig()
        self.strategy = TotalPointsStrategy(self.config)

        # Line to test - defaults to a line just below final total
        # This simulates a "fair" market for evaluation
        if test_line is not None:
            self.test_line = test_line
        elif recording.final_total is not None:
            # Use a line near the final total for testing
            self.test_line = recording.final_total - 0.5
        else:
            self.test_line = 220.0  # Default NBA total

        # Tracking
        self.signals: List[TotalPointsSignalRecord] = []
        self.projections: List[ProjectionSnapshot] = []

    def _parse_time_remaining(self, time_str: str) -> int:
        """Parse time string to seconds."""
        try:
            time_str = time_str.split()[-1]
            parts = time_str.split(":")
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return int(minutes * 60 + seconds)
            elif len(parts) == 1:
                return int(float(parts[0]))
        except (ValueError, IndexError):
            pass
        return 0

    def _simulate_market_price(
        self, theoretical_prob: float, noise_std: float = 0.02
    ) -> float:
        """Simulate a market price with some noise around theoretical.

        For backtesting without real market data, we simulate prices that
        are slightly off from theoretical to test edge detection.

        Args:
            theoretical_prob: Our model's probability
            noise_std: Standard deviation of market noise

        Returns:
            Simulated market probability
        """
        # Add random noise to simulate market inefficiency
        noise = np.random.normal(0, noise_std)
        market_prob = theoretical_prob + noise
        # Clamp to valid probability range
        return max(0.01, min(0.99, market_prob))

    async def run(
        self,
        simulate_market: bool = True,
        market_noise: float = 0.03,
        verbose: bool = False,
    ) -> TotalPointsBacktestResult:
        """Run the backtest.

        Args:
            simulate_market: If True, simulate market prices. Otherwise skip signals.
            market_noise: Std dev of noise to add to simulated market prices
            verbose: Print progress during backtest

        Returns:
            TotalPointsBacktestResult with all metrics and data
        """
        started_at = datetime.now()
        self.signals = []
        self.projections = []
        self.strategy.reset()

        live_frame_count = 0
        halftime_recorded = False

        final_total = self.recording.final_total or 0

        # Process each frame
        for frame_idx, frame in enumerate(self.recording.frames):
            # Skip non-live frames
            if frame.game_status != "live":
                continue

            live_frame_count += 1

            # Parse time
            time_remaining_seconds = self._parse_time_remaining(frame.time_remaining)
            time_frac = self.strategy.calculate_time_remaining_fraction(
                frame.period, time_remaining_seconds
            )

            # Record halftime total
            if (
                frame.period == 2
                and time_remaining_seconds < 30
                and not halftime_recorded
            ):
                self.strategy.set_halftime_total(frame.total_score)
                halftime_recorded = True

            # Calculate projection
            projected, pace, boost = self.strategy.calculate_projected_total(
                frame.total_score, time_frac, self.strategy._halftime_total
            )
            sigma = self.strategy.calculate_sigma(time_frac)

            # Record projection snapshot
            snapshot = ProjectionSnapshot(
                timestamp=frame.timestamp,
                period=frame.period,
                time_remaining=frame.time_remaining,
                current_total=frame.total_score,
                projected_total=projected,
                blended_pace=pace,
                boost_applied=boost,
                sigma=sigma,
                time_remaining_frac=time_frac,
            )
            self.projections.append(snapshot)

            # Simulate market and check for signals if requested
            if simulate_market:
                # Calculate theoretical probability
                over_prob, _, _, _ = self.strategy.calculate_over_probability(
                    frame.total_score,
                    self.test_line,
                    frame.period,
                    time_remaining_seconds,
                    self.strategy._halftime_total,
                )

                # Simulate market price with noise
                market_over = self._simulate_market_price(over_prob, market_noise)

                # Check for signal
                signal = self.strategy.check_entry(
                    home_score=frame.home_score,
                    away_score=frame.away_score,
                    period=frame.period,
                    time_remaining=frame.time_remaining,
                    timestamp=frame.timestamp,
                    game_id=self.recording.game_id,
                    line=self.test_line,
                    market_over_bid=market_over - 0.01,
                    market_over_ask=market_over + 0.01,
                    ticker=f"TEST-{self.recording.game_id}-{int(self.test_line)}",
                )

                if signal:
                    record = TotalPointsSignalRecord(
                        timestamp=frame.timestamp,
                        frame_idx=frame_idx,
                        period=frame.period,
                        time_remaining=frame.time_remaining,
                        current_total=frame.total_score,
                        line=signal.line,
                        projected_total=signal.projected_total,
                        theoretical_over_prob=signal.theoretical_over_prob,
                        market_over_prob=signal.market_over_prob,
                        edge_cents=signal.edge_cents,
                        direction=signal.direction,
                        blended_pace=signal.blended_pace,
                        boost_applied=signal.boost_applied,
                        final_total=final_total,
                    )
                    self.signals.append(record)

                    if verbose:
                        print(
                            f"[Q{frame.period} {frame.time_remaining}] "
                            f"SIGNAL: {signal.direction} | "
                            f"Current: {frame.total_score}, Projected: {projected:.1f} | "
                            f"Line: {self.test_line} | "
                            f"Edge: {signal.edge_cents:.1f}c"
                        )

        completed_at = datetime.now()

        # Evaluate signal correctness based on final total
        self._evaluate_outcomes()

        # Calculate metrics
        metrics = self._calculate_metrics(
            total_frames=len(self.recording.frames),
            live_frames=live_frame_count,
            final_total=final_total,
        )

        return TotalPointsBacktestResult(
            recording_path="",  # Filled by caller
            game_id=self.recording.game_id,
            home_team=self.recording.home_team,
            away_team=self.recording.away_team,
            final_total=final_total,
            config=self.config,
            metrics=metrics,
            signals=self.signals,
            projections=self.projections,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _evaluate_outcomes(self) -> None:
        """Evaluate whether signals were correct based on final total."""
        for signal in self.signals:
            if signal.final_total is None:
                continue

            if signal.direction == "BUY_OVER":
                # Correct if final total > line
                signal.signal_correct = signal.final_total > signal.line
            else:  # BUY_UNDER
                # Correct if final total <= line
                signal.signal_correct = signal.final_total <= signal.line

            # Calculate P&L (simplified)
            if signal.signal_correct:
                # Won: receive $1 per contract, paid market price
                if signal.direction == "BUY_OVER":
                    signal.pnl = (
                        1.0 - signal.market_over_prob
                    ) * self.config.position_size
                else:
                    signal.pnl = signal.market_over_prob * self.config.position_size
            else:
                # Lost: paid market price, receive $0
                if signal.direction == "BUY_OVER":
                    signal.pnl = -signal.market_over_prob * self.config.position_size
                else:
                    signal.pnl = (
                        -(1.0 - signal.market_over_prob) * self.config.position_size
                    )

    def _calculate_metrics(
        self, total_frames: int, live_frames: int, final_total: int
    ) -> TotalPointsBacktestMetrics:
        """Calculate performance metrics."""
        metrics = TotalPointsBacktestMetrics()
        metrics.total_frames = total_frames
        metrics.live_frames = live_frames
        metrics.total_signals = len(self.signals)

        # Projection accuracy
        if self.projections:
            errors = [p.projected_total - final_total for p in self.projections]
            metrics.projection_errors = errors
            metrics.avg_projection_error = sum(errors) / len(errors)
            metrics.rmse = np.sqrt(sum(e**2 for e in errors) / len(errors))

        if not self.signals:
            return metrics

        # Edge analysis
        edges = [s.edge_cents for s in self.signals]
        metrics.total_edge_cents = sum(edges)
        metrics.avg_edge_cents = sum(edges) / len(edges)
        metrics.max_edge_cents = max(edges)

        # Accuracy
        judged = [s for s in self.signals if s.signal_correct is not None]
        if judged:
            metrics.correct_signals = sum(1 for s in judged if s.signal_correct)
            metrics.incorrect_signals = sum(1 for s in judged if not s.signal_correct)
            total = metrics.correct_signals + metrics.incorrect_signals
            metrics.accuracy_pct = (
                (metrics.correct_signals / total * 100) if total > 0 else 0.0
            )

        # P&L
        for signal in self.signals:
            if signal.pnl is not None:
                metrics.gross_pnl += signal.pnl

        if metrics.gross_pnl > 0:
            metrics.fees = metrics.gross_pnl * 0.02
        metrics.net_pnl = metrics.gross_pnl - metrics.fees

        # By period and direction
        for signal in self.signals:
            period = signal.period
            metrics.signals_by_period[period] = (
                metrics.signals_by_period.get(period, 0) + 1
            )
            direction = signal.direction
            metrics.signals_by_direction[direction] = (
                metrics.signals_by_direction.get(direction, 0) + 1
            )

        return metrics


def sweep_parameters(
    recordings: List[str],
    second_half_boosts: List[float] = [0, 5, 10, 13.5, 15, 20],
    pace_blend_ends: List[float] = [0.5, 0.6, 0.7, 0.8, 0.9],
    slow_game_boosts: List[float] = [0, 3, 6, 10],
    verbose: bool = False,
) -> pd.DataFrame:
    """Sweep tunable parameters to find optimal settings.

    Args:
        recordings: List of recording file paths
        second_half_boosts: Values to test for second half boost
        pace_blend_ends: Values to test for pace blending weight
        slow_game_boosts: Values to test for slow game boost
        verbose: Print progress

    Returns:
        DataFrame with results for each parameter combination
    """
    import asyncio

    results = []

    total_combos = (
        len(second_half_boosts) * len(pace_blend_ends) * len(slow_game_boosts)
    )
    combo_idx = 0

    for second_half_boost in second_half_boosts:
        for pace_blend_end in pace_blend_ends:
            for slow_game_boost in slow_game_boosts:
                combo_idx += 1
                if verbose:
                    print(
                        f"[{combo_idx}/{total_combos}] "
                        f"2H={second_half_boost}, pace={pace_blend_end}, slow={slow_game_boost}"
                    )

                config = TotalPointsConfig(
                    second_half_boost=second_half_boost,
                    pace_blend_end=pace_blend_end,
                    slow_game_extra_boost=slow_game_boost,
                )

                for rec_path in recordings:
                    try:
                        recording = TotalPointsRecording.load(rec_path)
                        backtester = TotalPointsBacktester(
                            recording=recording, config=config
                        )
                        result = asyncio.run(
                            backtester.run(simulate_market=False, verbose=False)
                        )

                        results.append(
                            {
                                "second_half_boost": second_half_boost,
                                "pace_blend_end": pace_blend_end,
                                "slow_game_boost": slow_game_boost,
                                "game_id": result.game_id,
                                "final_total": result.final_total,
                                "avg_projection_error": result.metrics.avg_projection_error,
                                "rmse": result.metrics.rmse,
                            }
                        )
                    except Exception as e:
                        if verbose:
                            print(f"  Error on {rec_path}: {e}")

    return pd.DataFrame(results)


def format_total_points_report(result: TotalPointsBacktestResult) -> str:
    """Format a total points backtest result as a readable report.

    Args:
        result: Backtest result to format

    Returns:
        Formatted string report
    """
    m = result.metrics

    lines = [
        "=" * 60,
        "TOTAL POINTS OVER/UNDER STRATEGY BACKTEST",
        "=" * 60,
        "",
        f"Game: {result.away_team} @ {result.home_team}",
        f"Final Total: {result.final_total} points",
        f"Test Line: {result.config.min_edge_cents}",
        "",
        "--- Configuration ---",
        f"Min Edge: {result.config.min_edge_cents}c",
        f"Max Period: Q{result.config.max_period}",
        f"Position Size: {result.config.position_size}",
        f"NBA Avg Pace: {result.config.nba_avg_pace:.1f} pts/min",
        f"Pace Blend: {result.config.pace_blend_start:.1f} -> {result.config.pace_blend_end:.1f}",
        f"Second Half Boost: {result.config.second_half_boost:.1f} pts",
        f"Slow Game Threshold: {result.config.slow_game_threshold:.0f}",
        f"Slow Game Boost: {result.config.slow_game_extra_boost:.1f} pts",
        "",
        "--- Frame Analysis ---",
        f"Total Frames: {m.total_frames}",
        f"Live Frames: {m.live_frames}",
        "",
        "--- Projection Accuracy ---",
        f"Avg Projection Error: {m.avg_projection_error:+.1f} pts",
        f"RMSE: {m.rmse:.1f} pts",
        "",
        "--- Signal Analysis ---",
        f"Signals Generated: {m.total_signals}",
        f"Avg Edge at Signal: {m.avg_edge_cents:.1f}c",
        f"Max Edge Seen: {m.max_edge_cents:.1f}c",
        "",
        "--- Accuracy ---",
        f"Correct: {m.correct_signals}",
        f"Incorrect: {m.incorrect_signals}",
        f"Accuracy: {m.accuracy_pct:.1f}%",
        "",
        "--- P&L ---",
        f"Gross P&L: ${m.gross_pnl:.2f}",
        f"Fees: ${m.fees:.2f}",
        f"Net P&L: ${m.net_pnl:.2f}",
        "",
        "--- By Period ---",
    ]

    for period in sorted(m.signals_by_period.keys()):
        count = m.signals_by_period[period]
        lines.append(f"  Q{period}: {count} signals")

    lines.append("")
    lines.append("--- By Direction ---")
    for direction, count in m.signals_by_direction.items():
        lines.append(f"  {direction}: {count}")

    if result.signals:
        lines.extend(["", "--- Signals (first 10) ---"])
        for s in result.signals[:10]:
            status = (
                "WIN"
                if s.signal_correct
                else "LOSS"
                if s.signal_correct is False
                else "?"
            )
            lines.append(
                f"  [{status}] Q{s.period} {s.time_remaining} | "
                f"Current: {s.current_total}, Line: {s.line} | "
                f"{s.direction} | "
                f"Edge: {s.edge_cents:.1f}c | "
                f"P&L: ${s.pnl:.2f}"
                if s.pnl
                else ""
            )

    lines.extend(["", "=" * 60])

    return "\n".join(lines)


async def run_total_points_backtest(
    recording_path: str,
    config: Optional[TotalPointsConfig] = None,
    test_line: Optional[float] = None,
    simulate_market: bool = True,
    verbose: bool = False,
) -> TotalPointsBacktestResult:
    """Convenience function to run a total points strategy backtest.

    Args:
        recording_path: Path to game recording JSON
        config: Strategy configuration
        test_line: Line to test (defaults to final_total - 0.5)
        simulate_market: Whether to simulate market prices
        verbose: Print progress

    Returns:
        TotalPointsBacktestResult
    """
    recording = TotalPointsRecording.load(recording_path)
    backtester = TotalPointsBacktester(
        recording=recording, config=config, test_line=test_line
    )
    result = await backtester.run(simulate_market=simulate_market, verbose=verbose)
    result.recording_path = recording_path
    return result


if __name__ == "__main__":
    import argparse
    import asyncio
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Total Points Over/Under Backtester")
    parser.add_argument("recording", nargs="?", help="Path to recording file")
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Run parameter sweep on all recordings",
    )
    parser.add_argument(
        "--recordings-dir",
        default="data/recordings",
        help="Directory containing recordings",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")

    args = parser.parse_args()

    if args.sweep:
        # Find all recordings
        rec_dir = Path(args.recordings_dir)
        recordings = list(rec_dir.glob("*.json"))

        if not recordings:
            print(f"No recordings found in {rec_dir}")
            exit(1)

        print(f"Found {len(recordings)} recordings")
        print("Running parameter sweep...")

        df = sweep_parameters(
            [str(r) for r in recordings],
            verbose=args.verbose,
        )

        # Aggregate results
        agg = df.groupby(
            ["second_half_boost", "pace_blend_end", "slow_game_boost"]
        ).agg(
            {
                "rmse": "mean",
                "avg_projection_error": "mean",
                "game_id": "count",
            }
        )
        agg = agg.rename(columns={"game_id": "games"})
        agg = agg.sort_values("rmse")

        print("\n=== Parameter Sweep Results (sorted by RMSE) ===")
        print(agg.head(20))

        # Best params
        best = agg.iloc[0]
        print("\nBest parameters:")
        print(f"  Second Half Boost: {best.name[0]}")
        print(f"  Pace Blend End: {best.name[1]}")
        print(f"  Slow Game Boost: {best.name[2]}")
        print(f"  RMSE: {best['rmse']:.2f}")

    elif args.recording:
        # Run single backtest
        result = asyncio.run(
            run_total_points_backtest(
                args.recording,
                verbose=args.verbose,
            )
        )
        print(format_total_points_report(result))

    else:
        parser.print_help()
