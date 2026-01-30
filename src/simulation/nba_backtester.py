"""NBA Strategy Backtester - Runs strategy against recorded game data.

This module provides a complete backtesting framework that:
1. Wires NBAMispricingStrategy to MockKalshiClient + MockScoreFeed
2. Tracks all signals, orders, and fills
3. Calculates performance metrics
4. Generates detailed reports
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.core.models import Fill, MarketState
from src.kalshi.mock_client import MockKalshiClient
from src.strategies.late_game_blowout import (
    LateGameBlowoutStrategy,
    BlowoutStrategyConfig,
    BlowoutSignal,
    Side,
)
from signal_extraction.data_feeds.score_feed import ScoreAnalyzer

from .nba_recorder import GameRecordingFrame, NBAGameRecorder
from .nba_replay import NBAGameReplay, MockScoreFeed


@dataclass
class SignalRecord:
    """Record of a signal generated during backtest."""

    timestamp: float
    frame_idx: int
    period: int
    time_remaining: str
    home_score: int
    away_score: int

    # Signal details
    direction: str  # "BUY YES" or "BUY NO"
    ticker: str
    edge_cents: float
    home_win_prob: float
    market_mid: float

    # Outcome (filled in later)
    order_placed: bool = False
    order_id: Optional[str] = None
    filled: bool = False
    fill_price: Optional[float] = None

    # Was signal correct? (filled in at game end)
    signal_correct: Optional[bool] = None
    theoretical_pnl: Optional[float] = None


@dataclass
class BacktestMetrics:
    """Performance metrics from a backtest."""

    # Basic counts
    total_frames: int = 0
    total_signals: int = 0
    signals_traded: int = 0
    orders_filled: int = 0
    orders_canceled: int = 0

    # Edge analysis
    total_edge_cents: float = 0.0
    avg_edge_cents: float = 0.0
    max_edge_cents: float = 0.0

    # Accuracy
    correct_signals: int = 0
    incorrect_signals: int = 0
    accuracy_pct: float = 0.0

    # P&L
    gross_pnl: float = 0.0  # Before fees
    fees: float = 0.0
    net_pnl: float = 0.0

    # Position
    max_position: int = 0
    final_position: int = 0

    # By period
    signals_by_period: Dict[int, int] = field(default_factory=dict)
    accuracy_by_period: Dict[int, float] = field(default_factory=dict)


@dataclass
class BacktestResult:
    """Complete result of a backtest run."""

    recording_path: str
    game_id: str
    home_team: str
    away_team: str
    final_home_score: int
    final_away_score: int
    winner: str

    # Config used
    min_edge_cents: float
    max_period: int
    position_size: int

    # Results
    metrics: BacktestMetrics
    signals: List[SignalRecord]
    fills: List[Fill]

    # Timing
    started_at: datetime
    completed_at: datetime
    replay_speed: float
    real_duration_seconds: float


class NBAStrategyBacktester:
    """Runs NBA mispricing strategy against recorded game data.

    Example usage:
        recording = NBAGameRecorder.load("data/recordings/game.json")
        backtester = NBAStrategyBacktester(
            recording=recording,
            min_edge_cents=3.0,
            position_size=10,
        )
        result = await backtester.run()
        print(result.metrics)
    """

    def __init__(
        self,
        recording: NBAGameRecorder,
        min_edge_cents: float = 3.0,
        max_period: int = 2,
        position_size: int = 10,
        initial_balance: int = 100000,
        fill_probability: float = 0.8,  # Realistic: not all orders fill
    ):
        """Initialize the backtester.

        Args:
            recording: Loaded game recording
            min_edge_cents: Minimum edge to generate signal
            max_period: Maximum period to trade in (2 = first half, recommended)
                        NOTE: Model only works reliably before halftime.
                        Late game is too volatile and markets are more efficient.
            position_size: Contracts per trade
            initial_balance: Starting balance in cents
            fill_probability: Probability orders fill when price crosses
        """
        # Enforce max_period limit - model doesn't work well after halftime
        if max_period > 2:
            print(f"[Backtester] Warning: max_period={max_period} capped to 2 (first half only)")
            max_period = 2
        self.recording = recording
        self.min_edge_cents = min_edge_cents
        self.max_period = max_period
        self.position_size = position_size
        self.initial_balance = initial_balance
        self.fill_probability = fill_probability

        # Will be initialized in run()
        self.mock_client: Optional[MockKalshiClient] = None
        self.replay: Optional[NBAGameReplay] = None
        self.analyzer = ScoreAnalyzer()

        # Tracking
        self.signals: List[SignalRecord] = []
        self.fills: List[Fill] = []

        # State
        self._last_signal_time: float = 0.0
        self._cooldown_seconds: float = 3.0

    async def run(self, speed: float = 100.0, verbose: bool = False) -> BacktestResult:
        """Run the backtest.

        Args:
            speed: Replay speed (100 = 100x faster than real-time)
            verbose: Print progress during backtest

        Returns:
            BacktestResult with all metrics and data
        """
        started_at = datetime.now()

        # Initialize mock client
        self.mock_client = MockKalshiClient(
            initial_balance=self.initial_balance,
            fill_probability=self.fill_probability,
            auto_fill=True,
        )

        # Track fills
        self.mock_client.on_fill(self._on_fill)

        # Initialize replay
        self.replay = NBAGameReplay(self.recording, speed=speed)

        # Reset tracking
        self.signals = []
        self.fills = []
        self._last_signal_time = 0.0

        frame_count = 0

        # Run replay
        async for frame in self.replay.run(self.mock_client):
            frame_count += 1

            # Evaluate for signals
            signal = self._evaluate_frame(frame, frame_count - 1)

            if signal:
                self.signals.append(signal)

                # Try to place order
                if self._can_trade(frame.timestamp):
                    await self._place_order(signal)

                if verbose:
                    print(f"[Q{frame.period} {frame.time_remaining}] "
                          f"SIGNAL: {signal.direction} | Edge: {signal.edge_cents:.1f}¢")

            # Periodic progress
            if verbose and frame_count % 100 == 0:
                print(f"Processed {frame_count} frames, {len(self.signals)} signals")

        completed_at = datetime.now()
        real_duration = (completed_at - started_at).total_seconds()

        # Determine winner
        final_frame = self.recording.frames[-1] if self.recording.frames else None
        if final_frame:
            if final_frame.home_score > final_frame.away_score:
                winner = self.recording.home_team
            elif final_frame.away_score > final_frame.home_score:
                winner = self.recording.away_team
            else:
                winner = "TIE"
            final_home = final_frame.home_score
            final_away = final_frame.away_score
        else:
            winner = "UNKNOWN"
            final_home = 0
            final_away = 0

        # Calculate signal correctness
        self._evaluate_signal_correctness(winner)

        # Calculate metrics
        metrics = self._calculate_metrics(frame_count)

        return BacktestResult(
            recording_path="",  # Filled by caller
            game_id=self.recording.game_id,
            home_team=self.recording.home_team,
            away_team=self.recording.away_team,
            final_home_score=final_home,
            final_away_score=final_away,
            winner=winner,
            min_edge_cents=self.min_edge_cents,
            max_period=self.max_period,
            position_size=self.position_size,
            metrics=metrics,
            signals=self.signals,
            fills=self.fills,
            started_at=started_at,
            completed_at=completed_at,
            replay_speed=speed,
            real_duration_seconds=real_duration,
        )

    def _evaluate_frame(
        self,
        frame: GameRecordingFrame,
        frame_idx: int
    ) -> Optional[SignalRecord]:
        """Evaluate a frame for trading signals."""

        # Period check
        if frame.period > self.max_period:
            return None

        # Need live game
        if frame.game_status != "live":
            return None

        # Calculate win probability from score
        score_diff = frame.home_score - frame.away_score
        time_remaining_seconds = self.analyzer.parse_time_remaining(frame.time_remaining)

        home_win_prob = self.analyzer.calculate_win_probability(
            score_diff,
            frame.period,
            time_remaining_seconds,
        )

        # Calculate market mid
        market_mid = (frame.home_bid + frame.home_ask) / 2

        # Calculate edge
        edge_cents = abs(home_win_prob - market_mid) * 100

        # Check threshold
        if edge_cents < self.min_edge_cents:
            return None

        # Determine direction
        if home_win_prob > market_mid:
            direction = "BUY YES"
            ticker = self.recording.home_ticker
        else:
            direction = "BUY NO"
            ticker = self.recording.away_ticker

        return SignalRecord(
            timestamp=frame.timestamp,
            frame_idx=frame_idx,
            period=frame.period,
            time_remaining=frame.time_remaining,
            home_score=frame.home_score,
            away_score=frame.away_score,
            direction=direction,
            ticker=ticker,
            edge_cents=edge_cents,
            home_win_prob=home_win_prob,
            market_mid=market_mid,
        )

    def _can_trade(self, timestamp: float) -> bool:
        """Check if we can trade (cooldown check)."""
        if timestamp - self._last_signal_time < self._cooldown_seconds:
            return False
        return True

    async def _place_order(self, signal: SignalRecord) -> None:
        """Attempt to place an order for a signal."""
        if self.mock_client is None:
            return

        try:
            # Determine price based on direction
            if signal.direction == "BUY YES":
                # Buy YES at the ask
                frame = self.recording.frames[signal.frame_idx]
                price = frame.home_ask
            else:
                # Buy NO means sell YES at the bid (or buy NO at 1-bid)
                frame = self.recording.frames[signal.frame_idx]
                price = frame.away_ask

            order_id = await self.mock_client.place_order_async(
                ticker=signal.ticker,
                side="buy" if "YES" in signal.direction else "buy",
                price=price,
                size=self.position_size,
            )

            signal.order_placed = True
            signal.order_id = order_id
            self._last_signal_time = signal.timestamp

            # Check if filled immediately
            status = await self.mock_client.get_order_status_async(order_id)
            if status["status"] == "FILLED":
                signal.filled = True
                signal.fill_price = price

        except Exception as e:
            signal.order_placed = False

    def _on_fill(self, fill: Fill) -> None:
        """Handle fill callback from mock client."""
        self.fills.append(fill)

        # Update corresponding signal
        for signal in reversed(self.signals):
            if signal.order_id == fill.order_id:
                signal.filled = True
                signal.fill_price = fill.price
                break

    def _evaluate_signal_correctness(self, winner: str) -> None:
        """Evaluate whether signals were correct based on game outcome."""
        home_won = winner == self.recording.home_team

        for signal in self.signals:
            if signal.direction == "BUY YES":
                # Betting on home team
                signal.signal_correct = home_won
            else:
                # Betting against home team (on away)
                signal.signal_correct = not home_won

            # Calculate theoretical P&L (if filled)
            if signal.filled and signal.fill_price is not None:
                if signal.signal_correct:
                    # Win: paid fill_price, receive $1
                    signal.theoretical_pnl = (1.0 - signal.fill_price) * self.position_size
                else:
                    # Lose: paid fill_price, receive $0
                    signal.theoretical_pnl = -signal.fill_price * self.position_size

    def _calculate_metrics(self, total_frames: int) -> BacktestMetrics:
        """Calculate performance metrics from signals and fills."""
        metrics = BacktestMetrics()

        metrics.total_frames = total_frames
        metrics.total_signals = len(self.signals)

        # Count traded signals
        metrics.signals_traded = sum(1 for s in self.signals if s.order_placed)
        metrics.orders_filled = sum(1 for s in self.signals if s.filled)
        metrics.orders_canceled = metrics.signals_traded - metrics.orders_filled

        # Edge analysis
        if self.signals:
            edges = [s.edge_cents for s in self.signals]
            metrics.total_edge_cents = sum(edges)
            metrics.avg_edge_cents = sum(edges) / len(edges)
            metrics.max_edge_cents = max(edges)

        # Accuracy
        filled_signals = [s for s in self.signals if s.filled and s.signal_correct is not None]
        if filled_signals:
            metrics.correct_signals = sum(1 for s in filled_signals if s.signal_correct)
            metrics.incorrect_signals = sum(1 for s in filled_signals if not s.signal_correct)
            total_judged = metrics.correct_signals + metrics.incorrect_signals
            metrics.accuracy_pct = (metrics.correct_signals / total_judged * 100) if total_judged > 0 else 0.0

        # P&L
        for signal in self.signals:
            if signal.theoretical_pnl is not None:
                metrics.gross_pnl += signal.theoretical_pnl

        # Approximate fees (Kalshi charges ~2% on winnings)
        if metrics.gross_pnl > 0:
            metrics.fees = metrics.gross_pnl * 0.02
        metrics.net_pnl = metrics.gross_pnl - metrics.fees

        # Position tracking
        if self.mock_client:
            for ticker in [self.recording.home_ticker, self.recording.away_ticker]:
                pos = abs(self.mock_client.get_position(ticker))
                if pos > metrics.max_position:
                    metrics.max_position = pos
            metrics.final_position = sum(
                abs(self.mock_client.get_position(t))
                for t in [self.recording.home_ticker, self.recording.away_ticker]
            )

        # By period
        for signal in self.signals:
            period = signal.period
            metrics.signals_by_period[period] = metrics.signals_by_period.get(period, 0) + 1

        for period in metrics.signals_by_period:
            period_signals = [s for s in self.signals if s.period == period and s.filled and s.signal_correct is not None]
            if period_signals:
                correct = sum(1 for s in period_signals if s.signal_correct)
                metrics.accuracy_by_period[period] = correct / len(period_signals) * 100

        return metrics


def format_backtest_report(result: BacktestResult) -> str:
    """Format a backtest result as a readable report."""

    m = result.metrics

    lines = [
        "=" * 60,
        "NBA STRATEGY BACKTEST REPORT",
        "=" * 60,
        "",
        f"Game: {result.away_team} @ {result.home_team}",
        f"Final: {result.away_team} {result.final_away_score} - {result.final_home_score} {result.home_team}",
        f"Winner: {result.winner}",
        "",
        "--- Configuration ---",
        f"Min Edge: {result.min_edge_cents}¢",
        f"Max Period: Q{result.max_period}",
        f"Position Size: {result.position_size}",
        "",
        "--- Signal Analysis ---",
        f"Total Frames: {m.total_frames}",
        f"Signals Generated: {m.total_signals}",
        f"Signals Traded: {m.signals_traded}",
        f"Orders Filled: {m.orders_filled} ({m.orders_filled/max(m.signals_traded,1)*100:.1f}% fill rate)",
        "",
        f"Avg Edge at Signal: {m.avg_edge_cents:.1f}¢",
        f"Max Edge Seen: {m.max_edge_cents:.1f}¢",
        "",
        "--- Accuracy ---",
        f"Correct Signals: {m.correct_signals}",
        f"Incorrect Signals: {m.incorrect_signals}",
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
        acc = m.accuracy_by_period.get(period, 0)
        lines.append(f"  Q{period}: {count} signals, {acc:.1f}% accuracy")

    lines.extend([
        "",
        "--- Timing ---",
        f"Replay Speed: {result.replay_speed}x",
        f"Real Duration: {result.real_duration_seconds:.1f}s",
        "",
        "=" * 60,
    ])

    return "\n".join(lines)


async def run_backtest(
    recording_path: str,
    min_edge_cents: float = 3.0,
    max_period: int = 2,
    position_size: int = 10,
    speed: float = 100.0,
    verbose: bool = False,
) -> BacktestResult:
    """Convenience function to run a backtest.

    Args:
        recording_path: Path to game recording JSON
        min_edge_cents: Minimum edge to trade
        max_period: Maximum period to trade
        position_size: Contracts per trade
        speed: Replay speed
        verbose: Print progress

    Returns:
        BacktestResult
    """
    recording = NBAGameRecorder.load(recording_path)

    backtester = NBAStrategyBacktester(
        recording=recording,
        min_edge_cents=min_edge_cents,
        max_period=max_period,
        position_size=position_size,
    )

    result = await backtester.run(speed=speed, verbose=verbose)
    result.recording_path = recording_path

    return result


# ============================================================================
# LATE GAME BLOWOUT BACKTESTER
# ============================================================================


@dataclass
class BlowoutSignalRecord:
    """Record of a blowout signal during backtest."""

    timestamp: float
    frame_idx: int
    period: int
    time_remaining: str
    time_remaining_seconds: int
    home_score: int
    away_score: int
    score_differential: int

    # Signal details
    leading_team: str  # "home" or "away"
    ticker: str
    confidence: str  # "medium", "high", "very_high"
    win_probability: float
    market_price: float  # Price we're buying at

    # Trade details
    position_size: float
    order_placed: bool = False
    filled: bool = False

    # Outcome
    signal_correct: Optional[bool] = None
    pnl: Optional[float] = None


@dataclass
class BlowoutBacktestResult:
    """Result of a blowout strategy backtest."""

    recording_path: str
    game_id: str
    home_team: str
    away_team: str
    final_home_score: int
    final_away_score: int
    winner: str

    # Config
    min_point_differential: int
    max_time_remaining_seconds: int

    # Results
    total_frames: int
    total_signals: int
    signals_traded: int
    correct_signals: int
    incorrect_signals: int
    accuracy_pct: float
    gross_pnl: float
    net_pnl: float

    signals: List[BlowoutSignalRecord]

    # Timing
    started_at: datetime
    completed_at: datetime


class BlowoutStrategyBacktester:
    """Backtester for Late Game Blowout strategy.

    This strategy activates in the last 10 minutes when one team has
    a commanding lead (10+ points).

    Example usage:
        recording = NBAGameRecorder.load("data/recordings/game.json")
        backtester = BlowoutStrategyBacktester(recording=recording)
        result = await backtester.run()
    """

    def __init__(
        self,
        recording: NBAGameRecorder,
        min_point_differential: int = 10,
        max_time_remaining_seconds: int = 600,  # 10 minutes
        base_position_size: float = 5.0,
    ):
        """Initialize the blowout backtester.

        Args:
            recording: Loaded game recording
            min_point_differential: Minimum point lead to trigger (default 10)
            max_time_remaining_seconds: Max time remaining to trigger (default 600 = 10 min)
            base_position_size: Base position size in dollars
        """
        self.recording = recording

        config = BlowoutStrategyConfig(
            min_point_differential=min_point_differential,
            max_time_remaining_seconds=max_time_remaining_seconds,
            base_position_size=base_position_size,
        )
        self.strategy = LateGameBlowoutStrategy(config)
        self.config = config

        # Tracking
        self.signals: List[BlowoutSignalRecord] = []
        self._traded_this_game = False  # Only trade once per game

    async def run(self, speed: float = 100.0, verbose: bool = False) -> BlowoutBacktestResult:
        """Run the backtest.

        Args:
            speed: Replay speed (not used, we iterate directly)
            verbose: Print progress

        Returns:
            BlowoutBacktestResult
        """
        started_at = datetime.now()
        self.signals = []
        self._traded_this_game = False

        # Process each frame
        for frame_idx, frame in enumerate(self.recording.frames):
            signal = self._evaluate_frame(frame, frame_idx)

            if signal and not self._traded_this_game:
                self.signals.append(signal)
                self._traded_this_game = True  # Only one trade per game

                if verbose:
                    print(
                        f"[Q{frame.period} {frame.time_remaining}] "
                        f"BLOWOUT SIGNAL: {signal.leading_team.upper()} leads by {signal.score_differential} | "
                        f"Confidence: {signal.confidence}"
                    )

        completed_at = datetime.now()

        # Determine winner
        final_frame = self.recording.frames[-1] if self.recording.frames else None
        if final_frame:
            if final_frame.home_score > final_frame.away_score:
                winner = "home"
            elif final_frame.away_score > final_frame.home_score:
                winner = "away"
            else:
                winner = "tie"
            final_home = final_frame.home_score
            final_away = final_frame.away_score
        else:
            winner = "unknown"
            final_home = 0
            final_away = 0

        # Evaluate correctness and P&L
        self._evaluate_outcomes(winner)

        # Calculate summary metrics
        correct = sum(1 for s in self.signals if s.signal_correct)
        incorrect = sum(1 for s in self.signals if s.signal_correct is False)
        total = correct + incorrect
        accuracy = (correct / total * 100) if total > 0 else 0.0

        gross_pnl = sum(s.pnl for s in self.signals if s.pnl is not None)
        fees = gross_pnl * 0.02 if gross_pnl > 0 else 0
        net_pnl = gross_pnl - fees

        return BlowoutBacktestResult(
            recording_path="",
            game_id=self.recording.game_id,
            home_team=self.recording.home_team,
            away_team=self.recording.away_team,
            final_home_score=final_home,
            final_away_score=final_away,
            winner=winner,
            min_point_differential=self.config.min_point_differential,
            max_time_remaining_seconds=self.config.max_time_remaining_seconds,
            total_frames=len(self.recording.frames),
            total_signals=len(self.signals),
            signals_traded=len(self.signals),
            correct_signals=correct,
            incorrect_signals=incorrect,
            accuracy_pct=accuracy,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            signals=self.signals,
            started_at=started_at,
            completed_at=completed_at,
        )

    def _evaluate_frame(
        self,
        frame: GameRecordingFrame,
        frame_idx: int,
    ) -> Optional[BlowoutSignalRecord]:
        """Evaluate a frame for blowout signals."""

        # Must be live game
        if frame.game_status != "live":
            return None

        # Get market prices
        home_price = (frame.home_bid + frame.home_ask) / 2
        away_price = (frame.away_bid + frame.away_ask) / 2

        # Check entry conditions using strategy
        signal = self.strategy.check_entry(
            home_score=frame.home_score,
            away_score=frame.away_score,
            period=frame.period,
            time_remaining=frame.time_remaining,
            timestamp=frame.timestamp,
            game_id=self.recording.game_id,
            home_price=home_price,
            away_price=away_price,
        )

        if signal is None:
            return None

        # Determine which ticker to buy
        if signal.leading_team == Side.HOME:
            ticker = self.recording.home_ticker
            market_price = home_price
        else:
            ticker = self.recording.away_ticker
            market_price = away_price

        position_size = self.strategy.get_position_size(signal.confidence)

        return BlowoutSignalRecord(
            timestamp=frame.timestamp,
            frame_idx=frame_idx,
            period=frame.period,
            time_remaining=frame.time_remaining,
            time_remaining_seconds=signal.time_remaining_seconds,
            home_score=frame.home_score,
            away_score=frame.away_score,
            score_differential=signal.score_differential,
            leading_team=signal.leading_team.value,
            ticker=ticker,
            confidence=signal.confidence,
            win_probability=signal.win_probability,
            market_price=market_price,
            position_size=position_size,
            order_placed=True,
            filled=True,
        )

    def _evaluate_outcomes(self, winner: str) -> None:
        """Evaluate whether signals were correct."""
        for signal in self.signals:
            # Did the leading team win?
            signal.signal_correct = signal.leading_team == winner

            # Calculate P&L
            if signal.signal_correct:
                # Won: profit = (1 - buy_price) * size
                signal.pnl = (1.0 - signal.market_price) * signal.position_size
            else:
                # Lost: lose entire position
                signal.pnl = -signal.market_price * signal.position_size


def format_blowout_report(result: BlowoutBacktestResult) -> str:
    """Format a blowout backtest result as a readable report."""

    lines = [
        "=" * 60,
        "LATE GAME BLOWOUT STRATEGY BACKTEST",
        "=" * 60,
        "",
        f"Game: {result.away_team} @ {result.home_team}",
        f"Final: {result.away_team} {result.final_away_score} - {result.final_home_score} {result.home_team}",
        f"Winner: {result.winner.upper()}",
        "",
        "--- Configuration ---",
        f"Min Point Differential: {result.min_point_differential}",
        f"Max Time Remaining: {result.max_time_remaining_seconds // 60} minutes",
        "",
        "--- Results ---",
        f"Total Frames: {result.total_frames}",
        f"Signals Generated: {result.total_signals}",
        f"Correct: {result.correct_signals}",
        f"Incorrect: {result.incorrect_signals}",
        f"Accuracy: {result.accuracy_pct:.1f}%",
        "",
        f"Gross P&L: ${result.gross_pnl:.2f}",
        f"Net P&L: ${result.net_pnl:.2f}",
        "",
    ]

    if result.signals:
        lines.append("--- Signals ---")
        for s in result.signals:
            status = "✓" if s.signal_correct else "✗"
            lines.append(
                f"  [{status}] Q{s.period} {s.time_remaining} | "
                f"{s.leading_team.upper()} +{s.score_differential} | "
                f"Conf: {s.confidence} | Price: {s.market_price:.2f} | "
                f"P&L: ${s.pnl:.2f}"
            )

    lines.extend(["", "=" * 60])

    return "\n".join(lines)


async def run_blowout_backtest(
    recording_path: str,
    min_point_differential: int = 10,
    max_time_remaining_seconds: int = 600,
    base_position_size: float = 5.0,
    verbose: bool = False,
) -> BlowoutBacktestResult:
    """Convenience function to run a blowout strategy backtest.

    Args:
        recording_path: Path to game recording JSON
        min_point_differential: Min lead to trigger (default 10)
        max_time_remaining_seconds: Max time left (default 600 = 10 min)
        base_position_size: Position size in dollars
        verbose: Print progress

    Returns:
        BlowoutBacktestResult
    """
    recording = NBAGameRecorder.load(recording_path)

    backtester = BlowoutStrategyBacktester(
        recording=recording,
        min_point_differential=min_point_differential,
        max_time_remaining_seconds=max_time_remaining_seconds,
        base_position_size=base_position_size,
    )

    result = await backtester.run(verbose=verbose)
    result.recording_path = recording_path

    return result
