"""NBA Game Replay - Replays recorded game data through mock infrastructure.

This module takes a recording from NBAGameRecorder and replays it through
the mock Kalshi client, allowing you to test algorithms against historical
game conditions with full dashboard visualization.
"""

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional

from src.core.models import MarketState
from signal_extraction.data_feeds.score_feed import GameScore, ScoreAnalyzer

from .nba_recorder import GameRecordingFrame, NBAGameRecorder


@dataclass
class ReplayState:
    """Current state of the replay."""

    is_playing: bool = False
    is_paused: bool = False
    is_finished: bool = False
    current_frame_idx: int = 0
    total_frames: int = 0
    speed: float = 1.0
    elapsed_game_time: float = 0.0  # Seconds of game time elapsed
    elapsed_real_time: float = 0.0  # Seconds of real time elapsed


class NBAGameReplay:
    """Replays a recorded game through the mock system.

    Example usage:
        recording = NBAGameRecorder.load("data/recordings/LAL_vs_BOS.json")
        replay = NBAGameReplay(recording, speed=10.0)

        # Create mock client
        mock_client = MockKalshiClient()

        # Run replay
        async for frame in replay.run(mock_client):
            print(f"Frame {replay.state.current_frame_idx}: {frame.home_score}-{frame.away_score}")
    """

    def __init__(
        self,
        recording: NBAGameRecorder,
        speed: float = 1.0,
    ):
        """Initialize the replay.

        Args:
            recording: NBAGameRecorder instance with loaded data
            speed: Replay speed multiplier (1.0 = real-time, 10.0 = 10x faster)
        """
        self.recording = recording
        self.speed = speed

        self.state = ReplayState(
            total_frames=len(recording.frames),
            speed=speed,
        )

        self._current_frame: Optional[GameRecordingFrame] = None
        self._analyzer = ScoreAnalyzer()

        # Callbacks
        self._on_frame_callbacks: List[Callable[[GameRecordingFrame], None]] = []
        self._on_score_change_callbacks: List[Callable[[GameScore], None]] = []

    @property
    def current_frame(self) -> Optional[GameRecordingFrame]:
        """Get current frame."""
        return self._current_frame

    def set_speed(self, speed: float) -> None:
        """Change replay speed.

        Args:
            speed: New speed multiplier
        """
        if speed <= 0:
            raise ValueError("Speed must be positive")
        self.speed = speed
        self.state.speed = speed

    def pause(self) -> None:
        """Pause the replay."""
        self.state.is_paused = True

    def resume(self) -> None:
        """Resume the replay."""
        self.state.is_paused = False

    def skip_to_frame(self, frame_idx: int) -> Optional[GameRecordingFrame]:
        """Skip to a specific frame.

        Args:
            frame_idx: Frame index to skip to

        Returns:
            The frame at that index, or None if invalid
        """
        if 0 <= frame_idx < len(self.recording.frames):
            self.state.current_frame_idx = frame_idx
            self._current_frame = self.recording.frames[frame_idx]
            return self._current_frame
        return None

    def skip_to_period(self, period: int) -> Optional[GameRecordingFrame]:
        """Skip to the start of a specific period.

        Args:
            period: Period number (1-4 for regulation, 5+ for OT)

        Returns:
            First frame of that period, or None if not found
        """
        for idx, frame in enumerate(self.recording.frames):
            if frame.period == period:
                return self.skip_to_frame(idx)
        return None

    def skip_to_time(self, period: int, time_remaining: str) -> Optional[GameRecordingFrame]:
        """Skip to a specific game time.

        Args:
            period: Period number
            time_remaining: Time remaining string (e.g., "4:30")

        Returns:
            Closest frame to that time, or None if not found
        """
        target_seconds = self._analyzer.parse_time_remaining(time_remaining)

        best_frame = None
        best_diff = float("inf")

        for idx, frame in enumerate(self.recording.frames):
            if frame.period == period:
                frame_seconds = self._analyzer.parse_time_remaining(frame.time_remaining)
                diff = abs(frame_seconds - target_seconds)
                if diff < best_diff:
                    best_diff = diff
                    best_frame = idx

        if best_frame is not None:
            return self.skip_to_frame(best_frame)
        return None

    def get_next_frame(self) -> Optional[GameRecordingFrame]:
        """Get the next frame without waiting.

        Returns:
            Next frame, or None if at end
        """
        if self.state.current_frame_idx >= len(self.recording.frames):
            self.state.is_finished = True
            return None

        frame = self.recording.frames[self.state.current_frame_idx]
        self._current_frame = frame
        self.state.current_frame_idx += 1

        return frame

    def get_current_market_state(self, ticker: str) -> Optional[MarketState]:
        """Get market state from current frame for a specific ticker.

        Args:
            ticker: Market ticker to get state for

        Returns:
            MarketState if ticker matches, None otherwise
        """
        if not self._current_frame:
            return None

        frame = self._current_frame

        if ticker == self.recording.home_ticker:
            return MarketState(
                ticker=ticker,
                timestamp=datetime.fromtimestamp(frame.timestamp),
                bid=frame.home_bid,
                ask=frame.home_ask,
                last_price=(frame.home_bid + frame.home_ask) / 2,
                volume=frame.volume,
            )
        elif ticker == self.recording.away_ticker:
            return MarketState(
                ticker=ticker,
                timestamp=datetime.fromtimestamp(frame.timestamp),
                bid=frame.away_bid,
                ask=frame.away_ask,
                last_price=(frame.away_bid + frame.away_ask) / 2,
                volume=frame.volume,
            )

        return None

    def get_current_score(self) -> Optional[GameScore]:
        """Get score from current frame.

        Returns:
            GameScore dataclass, or None if no current frame
        """
        if not self._current_frame:
            return None

        frame = self._current_frame
        return GameScore(
            timestamp=frame.timestamp,
            game_id=self.recording.game_id,
            home_team=self.recording.home_team,
            away_team=self.recording.away_team,
            home_score=frame.home_score,
            away_score=frame.away_score,
            period=frame.period,
            time_remaining=frame.time_remaining,
            game_status=frame.game_status,
        )

    def on_frame(self, callback: Callable[[GameRecordingFrame], None]) -> None:
        """Register a callback for each frame.

        Args:
            callback: Function to call with each frame
        """
        self._on_frame_callbacks.append(callback)

    def on_score_change(self, callback: Callable[[GameScore], None]) -> None:
        """Register a callback for score changes.

        Args:
            callback: Function to call when score changes
        """
        self._on_score_change_callbacks.append(callback)

    async def run(self, mock_client=None):
        """Run the replay, yielding frames at the appropriate speed.

        Args:
            mock_client: Optional MockKalshiClient to update with market data

        Yields:
            GameRecordingFrame for each frame
        """
        if not self.recording.frames:
            return

        self.state.is_playing = True
        self.state.is_finished = False
        start_real_time = time.time()

        prev_frame: Optional[GameRecordingFrame] = None
        base_game_time = self.recording.frames[0].timestamp

        while self.state.current_frame_idx < len(self.recording.frames):
            # Handle pause
            while self.state.is_paused and self.state.is_playing:
                await asyncio.sleep(0.1)

            if not self.state.is_playing:
                break

            frame = self.recording.frames[self.state.current_frame_idx]
            self._current_frame = frame

            # Calculate timing
            game_time_elapsed = frame.timestamp - base_game_time
            self.state.elapsed_game_time = game_time_elapsed

            # Calculate how long we should have waited in real time
            target_real_time = game_time_elapsed / self.speed
            actual_real_time = time.time() - start_real_time
            self.state.elapsed_real_time = actual_real_time

            # Wait if needed
            wait_time = target_real_time - actual_real_time
            if wait_time > 0:
                await asyncio.sleep(wait_time)

            # Update mock client if provided
            if mock_client:
                home_state = self.get_current_market_state(self.recording.home_ticker)
                away_state = self.get_current_market_state(self.recording.away_ticker)
                if home_state:
                    mock_client.update_market(self.recording.home_ticker, home_state)
                if away_state:
                    mock_client.update_market(self.recording.away_ticker, away_state)

            # Fire callbacks
            for callback in self._on_frame_callbacks:
                try:
                    callback(frame)
                except Exception as e:
                    print(f"[Replay] Callback error: {e}")

            # Detect score changes
            if prev_frame:
                if (frame.home_score != prev_frame.home_score or
                        frame.away_score != prev_frame.away_score):
                    score = self.get_current_score()
                    if score:
                        for callback in self._on_score_change_callbacks:
                            try:
                                callback(score)
                            except Exception as e:
                                print(f"[Replay] Score callback error: {e}")

            prev_frame = frame
            self.state.current_frame_idx += 1

            yield frame

        self.state.is_playing = False
        self.state.is_finished = True

    def run_sync(self, mock_client=None):
        """Run the replay synchronously (blocking).

        Args:
            mock_client: Optional MockKalshiClient to update
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _run():
                async for _ in self.run(mock_client):
                    pass
            loop.run_until_complete(_run())
        finally:
            loop.close()

    def stop(self) -> None:
        """Stop the replay."""
        self.state.is_playing = False

    def reset(self) -> None:
        """Reset replay to beginning."""
        self.state.current_frame_idx = 0
        self.state.is_playing = False
        self.state.is_paused = False
        self.state.is_finished = False
        self.state.elapsed_game_time = 0.0
        self.state.elapsed_real_time = 0.0
        self._current_frame = None

    def get_progress(self) -> float:
        """Get replay progress as a percentage.

        Returns:
            Progress from 0.0 to 1.0
        """
        if self.state.total_frames == 0:
            return 0.0
        return self.state.current_frame_idx / self.state.total_frames

    def get_summary(self) -> dict:
        """Get a summary of the replay state.

        Returns:
            Dict with replay information
        """
        current_score = self.get_current_score()
        return {
            "game_id": self.recording.game_id,
            "matchup": f"{self.recording.away_team} @ {self.recording.home_team}",
            "is_playing": self.state.is_playing,
            "is_paused": self.state.is_paused,
            "is_finished": self.state.is_finished,
            "speed": self.speed,
            "progress": self.get_progress(),
            "current_frame": self.state.current_frame_idx,
            "total_frames": self.state.total_frames,
            "elapsed_game_time": self.state.elapsed_game_time,
            "elapsed_real_time": self.state.elapsed_real_time,
            "current_score": {
                "home": current_score.home_score if current_score else 0,
                "away": current_score.away_score if current_score else 0,
                "period": current_score.period if current_score else 0,
                "time": current_score.time_remaining if current_score else "",
            } if current_score else None,
        }


class MockScoreFeed:
    """A mock score feed that gets data from replay instead of live API.

    This can be used as a drop-in replacement for NBAScoreFeed during replay.
    """

    def __init__(self, replay: NBAGameReplay):
        """Initialize the mock feed.

        Args:
            replay: NBAGameReplay instance to get data from
        """
        self.replay = replay
        self.game_id = replay.recording.game_id
        self.home_team = replay.recording.home_team
        self.away_team = replay.recording.away_team
        self.analyzer = ScoreAnalyzer()

        self._current_score: Optional[GameScore] = None

        # Register for score changes
        replay.on_score_change(self._on_score_change)

    def _on_score_change(self, score: GameScore) -> None:
        """Handle score change from replay."""
        self._current_score = score

    @property
    def current_score(self) -> Optional[GameScore]:
        """Get current score (matches NBAScoreFeed interface)."""
        return self.replay.get_current_score()

    def start(self) -> None:
        """Start the feed (no-op for mock, replay controls timing)."""
        pass

    def stop(self) -> None:
        """Stop the feed (no-op for mock)."""
        pass

    def get_current_features(self) -> dict:
        """Get current score-derived features (matches NBAScoreFeed interface)."""
        score = self.current_score
        if not score:
            return self._empty_features()

        score_diff = score.score_differential
        time_remaining_seconds = self.analyzer.parse_time_remaining(score.time_remaining)

        win_prob = self.analyzer.calculate_win_probability(
            score_diff,
            score.period,
            time_remaining_seconds,
        )

        # Game completion
        total_time = 2880  # 48 minutes
        if score.period <= 4:
            time_elapsed = (score.period - 1) * 720 + (720 - time_remaining_seconds)
        else:
            time_elapsed = total_time - 60

        game_completion = min(time_elapsed / total_time, 0.99)

        return {
            "score_differential": float(score_diff),
            "win_probability": win_prob,
            "momentum": 0.0,  # Not tracked in replay
            "game_completion": game_completion,
            "total_points": float(score.total_score),
            "seconds_since_score": 0.0,
            "scoring_event_recent": 0.0,
            "home_score": float(score.home_score),
            "away_score": float(score.away_score),
            "period": float(score.period),
            "points_in_last_event": 0.0,
        }

    def _empty_features(self) -> dict:
        """Return default features when no data available."""
        return {
            "score_differential": 0.0,
            "win_probability": 0.5,
            "momentum": 0.0,
            "game_completion": 0.0,
            "total_points": 0.0,
            "seconds_since_score": 0.0,
            "scoring_event_recent": 0.0,
            "home_score": 0.0,
            "away_score": 0.0,
            "period": 1.0,
            "points_in_last_event": 0.0,
        }
