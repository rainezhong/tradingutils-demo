"""Tied Game Spread Strategy Backtester - Runs strategy against recorded game data.

This module provides a backtesting framework for the tied game narrow margin strategy.
It evaluates recordings with spread data to identify opportunities when teams are tied
and markets overprice narrow margin finishes.

Expected recording format with spread data:
{
    "metadata": {...},
    "frames": [{
        "timestamp": 1234567890.0,
        "home_score": 50,
        "away_score": 50,
        "period": 2,
        "time_remaining": "6:30",
        "game_status": "live",
        "spreads": {
            "home": {
                "1": {"bid": 0.45, "ask": 0.48},  // > 1.5 pts
                "4": {"bid": 0.35, "ask": 0.38}   // > 4.5 pts (used for > 3.5 calc)
            },
            "away": {
                "1": {"bid": 0.42, "ask": 0.45},
                "4": {"bid": 0.33, "ask": 0.36}
            }
        },
        "spread_tickers": {
            "home": "KXNBASPREAD-HOME-1",
            "away": "KXNBASPREAD-AWAY-1"
        }
    }]
}
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from strategies.tied_game_spread_strategy import (
    TiedGameSpreadConfig,
    TiedGameSpreadStrategy,
)


@dataclass
class SpreadFrame:
    """Single frame from a spread recording."""

    timestamp: float
    home_score: int
    away_score: int
    period: int
    time_remaining: str
    game_status: str

    # Spread data
    home_spreads: Dict[str, Dict[str, float]]  # {"1": {"bid": 0.45, "ask": 0.48}, ...}
    away_spreads: Dict[str, Dict[str, float]]

    # Tickers
    home_spread_ticker: str = ""
    away_spread_ticker: str = ""


@dataclass
class SpreadRecording:
    """Recording with spread data."""

    game_id: str
    home_team: str
    away_team: str
    date: str
    frames: List[SpreadFrame]

    # Final outcome
    final_home_score: Optional[int] = None
    final_away_score: Optional[int] = None
    final_margin: Optional[int] = None  # |home - away|

    @classmethod
    def load(cls, filepath: str) -> "SpreadRecording":
        """Load a spread recording from JSON file.

        Args:
            filepath: Path to the recording file

        Returns:
            SpreadRecording instance
        """
        with open(filepath, "r") as f:
            data = json.load(f)

        metadata = data.get("metadata", {})
        frames_data = data.get("frames", [])

        frames = []
        for fd in frames_data:
            # Extract spread data
            spreads = fd.get("spreads", {})
            home_spreads = spreads.get("home", spreads.get("HOME", {}))
            away_spreads = spreads.get("away", spreads.get("AWAY", {}))

            # Extract tickers
            tickers = fd.get("spread_tickers", {})
            home_ticker = tickers.get("home", tickers.get("HOME", ""))
            away_ticker = tickers.get("away", tickers.get("AWAY", ""))

            frame = SpreadFrame(
                timestamp=fd.get("timestamp", 0.0),
                home_score=fd.get("home_score", 0),
                away_score=fd.get("away_score", 0),
                period=fd.get("period", 1),
                time_remaining=fd.get("time_remaining", "12:00"),
                game_status=fd.get("game_status", "live"),
                home_spreads=home_spreads,
                away_spreads=away_spreads,
                home_spread_ticker=home_ticker,
                away_spread_ticker=away_ticker,
            )
            frames.append(frame)

        # Get final outcome from last frame or metadata
        final_home = metadata.get("final_home_score")
        final_away = metadata.get("final_away_score")

        if final_home is None and frames:
            final_home = frames[-1].home_score
        if final_away is None and frames:
            final_away = frames[-1].away_score

        final_margin = None
        if final_home is not None and final_away is not None:
            final_margin = abs(final_home - final_away)

        return cls(
            game_id=metadata.get("game_id", "unknown"),
            home_team=metadata.get("home_team", "HOME"),
            away_team=metadata.get("away_team", "AWAY"),
            date=metadata.get("date", ""),
            frames=frames,
            final_home_score=final_home,
            final_away_score=final_away,
            final_margin=final_margin,
        )


@dataclass
class TiedSpreadSignalRecord:
    """Record of a signal generated during backtest."""

    timestamp: float
    frame_idx: int
    period: int
    time_remaining: str
    time_remaining_seconds: int
    home_score: int
    away_score: int
    score_differential: int

    # Signal details
    team: str  # "home" or "away"
    ticker: str
    theoretical_narrow_prob: float
    market_narrow_prob: float
    edge_cents: float

    # Market data
    p_over_low: float
    p_over_high: float

    # Outcome
    signal_correct: Optional[bool] = None  # Did margin NOT fall in 2-3 range?
    pnl: Optional[float] = None


@dataclass
class TiedSpreadBacktestMetrics:
    """Performance metrics from a tied spread backtest."""

    # Basic counts
    total_frames: int = 0
    tied_frames: int = 0  # Frames where teams were tied/close
    total_signals: int = 0

    # Edge analysis
    total_edge_cents: float = 0.0
    avg_edge_cents: float = 0.0
    max_edge_cents: float = 0.0

    # Accuracy (for NO bets - did narrow margin NOT occur?)
    correct_signals: int = 0
    incorrect_signals: int = 0
    accuracy_pct: float = 0.0

    # P&L
    gross_pnl: float = 0.0
    fees: float = 0.0
    net_pnl: float = 0.0

    # By period
    signals_by_period: Dict[int, int] = field(default_factory=dict)


@dataclass
class TiedSpreadBacktestResult:
    """Complete result of a tied spread backtest."""

    recording_path: str
    game_id: str
    home_team: str
    away_team: str
    final_home_score: int
    final_away_score: int
    final_margin: int

    # Config used
    min_edge_cents: float
    max_period: int
    max_score_differential: int
    position_size: int

    # Results
    metrics: TiedSpreadBacktestMetrics
    signals: List[TiedSpreadSignalRecord]

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
            "final_home_score": self.final_home_score,
            "final_away_score": self.final_away_score,
            "final_margin": self.final_margin,
            "config": {
                "min_edge_cents": self.min_edge_cents,
                "max_period": self.max_period,
                "max_score_differential": self.max_score_differential,
                "position_size": self.position_size,
            },
            "metrics": {
                "total_frames": self.metrics.total_frames,
                "tied_frames": self.metrics.tied_frames,
                "total_signals": self.metrics.total_signals,
                "avg_edge_cents": self.metrics.avg_edge_cents,
                "max_edge_cents": self.metrics.max_edge_cents,
                "correct_signals": self.metrics.correct_signals,
                "incorrect_signals": self.metrics.incorrect_signals,
                "accuracy_pct": self.metrics.accuracy_pct,
                "gross_pnl": self.metrics.gross_pnl,
                "net_pnl": self.metrics.net_pnl,
                "signals_by_period": self.metrics.signals_by_period,
            },
            "signals": [
                {
                    "timestamp": s.timestamp,
                    "frame_idx": s.frame_idx,
                    "period": s.period,
                    "time_remaining": s.time_remaining,
                    "score": f"{s.away_score}-{s.home_score}",
                    "team": s.team,
                    "theoretical_prob": s.theoretical_narrow_prob,
                    "market_prob": s.market_narrow_prob,
                    "edge_cents": s.edge_cents,
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


class TiedSpreadBacktester:
    """Backtester for Tied Game Narrow Margin strategy.

    This backtester evaluates the tied game spread strategy against
    recorded game data that includes spread contract prices.

    Example usage:
        recording = SpreadRecording.load("data/recordings/game_with_spreads.json")
        backtester = TiedSpreadBacktester(
            recording=recording,
            config=TiedGameSpreadConfig(min_edge_cents=2.0)
        )
        result = await backtester.run()
        print(f"Accuracy: {result.metrics.accuracy_pct:.1f}%")
    """

    def __init__(
        self,
        recording: SpreadRecording,
        config: Optional[TiedGameSpreadConfig] = None,
    ):
        """Initialize the backtester.

        Args:
            recording: Loaded spread recording
            config: Strategy configuration
        """
        self.recording = recording
        self.config = config or TiedGameSpreadConfig()
        self.strategy = TiedGameSpreadStrategy(self.config)

        # Tracking
        self.signals: List[TiedSpreadSignalRecord] = []

    async def run(
        self, speed: float = 100.0, verbose: bool = False
    ) -> TiedSpreadBacktestResult:
        """Run the backtest.

        Args:
            speed: Replay speed (not used, we iterate directly)
            verbose: Print progress during backtest

        Returns:
            TiedSpreadBacktestResult with all metrics and data
        """
        started_at = datetime.now()
        self.signals = []
        self.strategy.reset()

        tied_frame_count = 0

        # Process each frame
        for frame_idx, frame in enumerate(self.recording.frames):
            # Skip non-live frames
            if frame.game_status != "live":
                continue

            # Track tied/close frames
            if (
                abs(frame.home_score - frame.away_score)
                <= self.config.max_score_differential
            ):
                tied_frame_count += 1

            # Evaluate for signals
            signal = self._evaluate_frame(frame, frame_idx)

            if signal:
                self.signals.append(signal)

                if verbose:
                    print(
                        f"[Q{frame.period} {frame.time_remaining}] "
                        f"SIGNAL: {signal.team} | Score: {frame.away_score}-{frame.home_score} | "
                        f"Edge: {signal.edge_cents:.1f}c | "
                        f"Theo: {signal.theoretical_narrow_prob:.1%} vs Mkt: {signal.market_narrow_prob:.1%}"
                    )

        completed_at = datetime.now()

        # Evaluate signal correctness based on final margin
        self._evaluate_outcomes()

        # Calculate metrics
        metrics = self._calculate_metrics(
            total_frames=len(self.recording.frames),
            tied_frames=tied_frame_count,
        )

        return TiedSpreadBacktestResult(
            recording_path="",  # Filled by caller
            game_id=self.recording.game_id,
            home_team=self.recording.home_team,
            away_team=self.recording.away_team,
            final_home_score=self.recording.final_home_score or 0,
            final_away_score=self.recording.final_away_score or 0,
            final_margin=self.recording.final_margin or 0,
            min_edge_cents=self.config.min_edge_cents,
            max_period=self.config.max_period,
            max_score_differential=self.config.max_score_differential,
            position_size=self.config.position_size,
            metrics=metrics,
            signals=self.signals,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _evaluate_frame(
        self, frame: SpreadFrame, frame_idx: int
    ) -> Optional[TiedSpreadSignalRecord]:
        """Evaluate a frame for trading signals.

        Args:
            frame: Current frame data
            frame_idx: Frame index

        Returns:
            Signal record if conditions met, None otherwise
        """
        # Use strategy's check_entry method
        signal = self.strategy.check_entry(
            home_score=frame.home_score,
            away_score=frame.away_score,
            period=frame.period,
            time_remaining=frame.time_remaining,
            timestamp=frame.timestamp,
            game_id=self.recording.game_id,
            home_spreads=frame.home_spreads,
            away_spreads=frame.away_spreads,
            home_spread_ticker=frame.home_spread_ticker,
            away_spread_ticker=frame.away_spread_ticker,
        )

        if signal is None:
            return None

        return TiedSpreadSignalRecord(
            timestamp=frame.timestamp,
            frame_idx=frame_idx,
            period=frame.period,
            time_remaining=frame.time_remaining,
            time_remaining_seconds=signal.time_remaining_seconds,
            home_score=frame.home_score,
            away_score=frame.away_score,
            score_differential=signal.score_differential,
            team=signal.team,
            ticker=signal.ticker,
            theoretical_narrow_prob=signal.theoretical_narrow_prob,
            market_narrow_prob=signal.market_narrow_prob,
            edge_cents=signal.edge_cents,
            p_over_low=signal.p_over_low,
            p_over_high=signal.p_over_high,
        )

    def _evaluate_outcomes(self) -> None:
        """Evaluate whether signals were correct based on final margin.

        For BUY_NO on narrow margin (2-3 pts):
        - Correct if final margin is NOT 2-3 points
        - Incorrect if final margin IS 2-3 points
        """
        final_margin = self.recording.final_margin

        if final_margin is None:
            return

        # Narrow margin is 2-3 points (margin > 1.5 and margin <= 3.5)
        is_narrow_margin = 1.5 < final_margin <= 3.5

        for signal in self.signals:
            # For BUY_NO: we win if margin is NOT narrow
            signal.signal_correct = not is_narrow_margin

            # Calculate P&L
            # Assuming we buy NO at (1 - market_narrow_prob) price
            # Actually, NO price = 1 - YES price, but we're betting on "NOT narrow"
            # If market prices narrow margin YES at market_narrow_prob,
            # then NO is priced at (1 - market_narrow_prob)
            no_price = 1.0 - signal.market_narrow_prob

            if signal.signal_correct:
                # Won: paid no_price, receive $1
                signal.pnl = (1.0 - no_price) * self.config.position_size
            else:
                # Lost: paid no_price, receive $0
                signal.pnl = -no_price * self.config.position_size

    def _calculate_metrics(
        self, total_frames: int, tied_frames: int
    ) -> TiedSpreadBacktestMetrics:
        """Calculate performance metrics from signals.

        Args:
            total_frames: Total frames processed
            tied_frames: Frames where teams were tied/close

        Returns:
            TiedSpreadBacktestMetrics with calculated values
        """
        metrics = TiedSpreadBacktestMetrics()
        metrics.total_frames = total_frames
        metrics.tied_frames = tied_frames
        metrics.total_signals = len(self.signals)

        if not self.signals:
            return metrics

        # Edge analysis
        edges = [s.edge_cents for s in self.signals]
        metrics.total_edge_cents = sum(edges)
        metrics.avg_edge_cents = sum(edges) / len(edges)
        metrics.max_edge_cents = max(edges)

        # Accuracy
        judged_signals = [s for s in self.signals if s.signal_correct is not None]
        if judged_signals:
            metrics.correct_signals = sum(1 for s in judged_signals if s.signal_correct)
            metrics.incorrect_signals = sum(
                1 for s in judged_signals if not s.signal_correct
            )
            total = metrics.correct_signals + metrics.incorrect_signals
            metrics.accuracy_pct = (
                (metrics.correct_signals / total * 100) if total > 0 else 0.0
            )

        # P&L
        for signal in self.signals:
            if signal.pnl is not None:
                metrics.gross_pnl += signal.pnl

        # Fees (~2% on winnings)
        if metrics.gross_pnl > 0:
            metrics.fees = metrics.gross_pnl * 0.02
        metrics.net_pnl = metrics.gross_pnl - metrics.fees

        # By period
        for signal in self.signals:
            period = signal.period
            metrics.signals_by_period[period] = (
                metrics.signals_by_period.get(period, 0) + 1
            )

        return metrics


def format_tied_spread_report(result: TiedSpreadBacktestResult) -> str:
    """Format a tied spread backtest result as a readable report.

    Args:
        result: Backtest result to format

    Returns:
        Formatted string report
    """
    m = result.metrics

    lines = [
        "=" * 60,
        "TIED GAME NARROW MARGIN STRATEGY BACKTEST",
        "=" * 60,
        "",
        f"Game: {result.away_team} @ {result.home_team}",
        f"Final: {result.away_team} {result.final_away_score} - {result.final_home_score} {result.home_team}",
        f"Final Margin: {result.final_margin} points",
        f"Narrow Margin (2-3 pts): {'YES' if 1.5 < result.final_margin <= 3.5 else 'NO'}",
        "",
        "--- Configuration ---",
        f"Min Edge: {result.min_edge_cents}c",
        f"Max Period: Q{result.max_period}",
        f"Max Score Differential: {result.max_score_differential}",
        f"Position Size: {result.position_size}",
        "",
        "--- Frame Analysis ---",
        f"Total Frames: {m.total_frames}",
        f"Tied/Close Frames: {m.tied_frames}",
        f"Tied Frame %: {m.tied_frames / max(m.total_frames, 1) * 100:.1f}%",
        "",
        "--- Signal Analysis ---",
        f"Signals Generated: {m.total_signals}",
        f"Avg Edge at Signal: {m.avg_edge_cents:.1f}c",
        f"Max Edge Seen: {m.max_edge_cents:.1f}c",
        "",
        "--- Accuracy ---",
        f"Correct (NO won): {m.correct_signals}",
        f"Incorrect (NO lost): {m.incorrect_signals}",
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

    if result.signals:
        lines.extend(["", "--- Signals ---"])
        for s in result.signals:
            status = (
                "WIN"
                if s.signal_correct
                else "LOSS"
                if s.signal_correct is False
                else "?"
            )
            lines.append(
                f"  [{status}] Q{s.period} {s.time_remaining} | "
                f"Score: {s.away_score}-{s.home_score} | "
                f"{s.team.upper()} | "
                f"Edge: {s.edge_cents:.1f}c | "
                f"P&L: ${s.pnl:.2f}"
                if s.pnl
                else ""
            )

    lines.extend(["", "=" * 60])

    return "\n".join(lines)


async def run_tied_spread_backtest(
    recording_path: str,
    min_edge_cents: float = 2.0,
    max_period: int = 3,
    max_score_differential: int = 2,
    position_size: int = 10,
    verbose: bool = False,
) -> TiedSpreadBacktestResult:
    """Convenience function to run a tied spread strategy backtest.

    Args:
        recording_path: Path to spread recording JSON
        min_edge_cents: Minimum edge to trade
        max_period: Maximum period to trade (skip Q4)
        max_score_differential: Max score diff to consider "tied"
        position_size: Contracts per trade
        verbose: Print progress

    Returns:
        TiedSpreadBacktestResult
    """
    recording = SpreadRecording.load(recording_path)

    config = TiedGameSpreadConfig(
        min_edge_cents=min_edge_cents,
        max_period=max_period,
        max_score_differential=max_score_differential,
        position_size=position_size,
    )

    backtester = TiedSpreadBacktester(recording=recording, config=config)
    result = await backtester.run(verbose=verbose)
    result.recording_path = recording_path

    return result
