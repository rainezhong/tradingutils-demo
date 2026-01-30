"""NBA Game Recorder - Records live game data + Kalshi market prices for replay.

This module captures synchronized snapshots of:
- NBA scores (from nba_api)
- Kalshi market prices (bid/ask for home & away win markets)

Recordings can be replayed through the mock infrastructure for algorithm testing.
"""

import json
import time
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# nba_api is optional - only needed for live recording, not replay/backtest
try:
    from nba_api.live.nba.endpoints import scoreboard
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False
    scoreboard = None


@dataclass
class GameRecordingFrame:
    """Single point-in-time snapshot during a game."""

    timestamp: float

    # NBA Score Data
    home_score: int
    away_score: int
    period: int
    time_remaining: str
    game_status: str  # "pregame", "live", "final"

    # Kalshi Market Data
    home_ticker: str
    away_ticker: str
    home_bid: float  # 0-1 probability
    home_ask: float
    away_bid: float
    away_ask: float
    volume: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameRecordingFrame":
        """Create from dictionary."""
        return cls(**data)


@dataclass
class GameRecordingMetadata:
    """Metadata about the recorded game."""

    game_id: str
    home_team: str
    away_team: str
    home_ticker: str
    away_ticker: str
    date: str  # YYYY-MM-DD
    recorded_at: str  # ISO timestamp
    poll_interval_ms: int = 2000
    total_frames: int = 0
    final_home_score: Optional[int] = None
    final_away_score: Optional[int] = None
    final_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameRecordingMetadata":
        """Create from dictionary."""
        return cls(**data)


class NBAGameRecorder:
    """Records live game + market data for later replay.

    Example usage:
        recorder = NBAGameRecorder(
            game_id="0022400123",
            home_team="LAL",
            away_team="BOS",
            home_ticker="NBALALBOS-LALWIN",
            away_ticker="NBALALBOS-BOSWIN"
        )

        # Record with a Kalshi client
        async with KalshiClient.from_env() as client:
            await recorder.start_async(client, poll_interval_ms=2000)

        # Save recording
        recorder.save("data/recordings/LAL_vs_BOS_2025-01-28.json")
    """

    def __init__(
        self,
        game_id: str,
        home_team: str,
        away_team: str,
        home_ticker: str,
        away_ticker: str,
    ):
        """Initialize the recorder.

        Args:
            game_id: NBA game ID (e.g., "0022400123")
            home_team: Home team tricode (e.g., "LAL")
            away_team: Away team tricode (e.g., "BOS")
            home_ticker: Kalshi ticker for home team win
            away_ticker: Kalshi ticker for away team win
        """
        self.game_id = game_id
        self.home_team = home_team
        self.away_team = away_team
        self.home_ticker = home_ticker
        self.away_ticker = away_ticker

        self.frames: List[GameRecordingFrame] = []
        self.metadata = GameRecordingMetadata(
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
            home_ticker=home_ticker,
            away_ticker=away_ticker,
            date=datetime.now().strftime("%Y-%m-%d"),
            recorded_at=datetime.now().isoformat(),
        )

        self._stop_event = threading.Event()
        self._recording_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    async def start_async(
        self,
        kalshi_client,
        poll_interval_ms: int = 2000,
        max_duration_seconds: Optional[int] = None,
    ) -> None:
        """Start recording asynchronously (runs until game ends or stopped).

        Args:
            kalshi_client: KalshiClient instance for fetching market data
            poll_interval_ms: How often to capture frames (default 2s)
            max_duration_seconds: Optional max recording duration
        """
        import asyncio

        self.metadata.poll_interval_ms = poll_interval_ms
        poll_interval = poll_interval_ms / 1000.0
        start_time = time.time()

        print(f"[Recorder] Starting recording for {self.away_team} @ {self.home_team}")
        print(f"[Recorder] Game ID: {self.game_id}")
        print(f"[Recorder] Home ticker: {self.home_ticker}")
        print(f"[Recorder] Away ticker: {self.away_ticker}")
        print(f"[Recorder] Poll interval: {poll_interval_ms}ms")

        self._stop_event.clear()

        while not self._stop_event.is_set():
            try:
                frame = await self._capture_frame_async(kalshi_client)

                if frame:
                    with self._lock:
                        self.frames.append(frame)
                        self.metadata.total_frames = len(self.frames)

                    # Log progress every 30 frames (~1 min at 2s intervals)
                    if len(self.frames) % 30 == 0:
                        print(f"[Recorder] Captured {len(self.frames)} frames | "
                              f"{self.away_team} {frame.away_score} - {frame.home_score} {self.home_team} | "
                              f"Q{frame.period} {frame.time_remaining}")

                    # Check if game ended
                    if frame.game_status == "final":
                        print(f"[Recorder] Game ended! Final: {self.away_team} {frame.away_score} - "
                              f"{frame.home_score} {self.home_team}")
                        self.metadata.final_home_score = frame.home_score
                        self.metadata.final_away_score = frame.away_score
                        self.metadata.final_status = "final"
                        break

                # Check max duration
                if max_duration_seconds and (time.time() - start_time) > max_duration_seconds:
                    print(f"[Recorder] Max duration reached ({max_duration_seconds}s)")
                    break

            except Exception as e:
                print(f"[Recorder] Error capturing frame: {e}")

            await asyncio.sleep(poll_interval)

        print(f"[Recorder] Recording complete. Total frames: {len(self.frames)}")

    def start(
        self,
        kalshi_client,
        poll_interval_ms: int = 2000,
    ) -> None:
        """Start recording synchronously (blocking, runs in current thread).

        For async usage, use start_async() instead.
        """
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.start_async(kalshi_client, poll_interval_ms))
        finally:
            loop.close()

    def start_background(
        self,
        kalshi_client,
        poll_interval_ms: int = 2000,
    ) -> None:
        """Start recording in a background thread."""
        if self._recording_thread and self._recording_thread.is_alive():
            raise RuntimeError("Recording already in progress")

        self._stop_event.clear()
        self._recording_thread = threading.Thread(
            target=self.start,
            args=(kalshi_client, poll_interval_ms),
            daemon=True,
        )
        self._recording_thread.start()

    def stop(self) -> None:
        """Stop recording."""
        self._stop_event.set()
        if self._recording_thread:
            self._recording_thread.join(timeout=5.0)

    async def _capture_frame_async(self, kalshi_client) -> Optional[GameRecordingFrame]:
        """Capture a single frame of game + market data."""
        timestamp = time.time()

        # Fetch NBA score
        score_data = self._fetch_nba_score()
        if not score_data:
            return None

        # Fetch Kalshi market data
        try:
            home_market = await kalshi_client.get_market_data_async(self.home_ticker)
            away_market = await kalshi_client.get_market_data_async(self.away_ticker)

            home_bid = home_market.bid
            home_ask = home_market.ask
            away_bid = away_market.bid
            away_ask = away_market.ask
            volume = home_market.volume + away_market.volume
        except Exception as e:
            print(f"[Recorder] Error fetching market data: {e}")
            # Use default values if market data unavailable
            home_bid = 0.0
            home_ask = 1.0
            away_bid = 0.0
            away_ask = 1.0
            volume = 0

        return GameRecordingFrame(
            timestamp=timestamp,
            home_score=score_data["home_score"],
            away_score=score_data["away_score"],
            period=score_data["period"],
            time_remaining=score_data["time_remaining"],
            game_status=score_data["game_status"],
            home_ticker=self.home_ticker,
            away_ticker=self.away_ticker,
            home_bid=home_bid,
            home_ask=home_ask,
            away_bid=away_bid,
            away_ask=away_ask,
            volume=volume,
        )

    def _fetch_nba_score(self) -> Optional[Dict[str, Any]]:
        """Fetch current score from NBA API."""
        if not NBA_API_AVAILABLE:
            print("[Recorder] nba_api not installed - cannot fetch live scores")
            return None

        try:
            board = scoreboard.ScoreBoard()
            games = board.get_dict()["scoreboard"]["games"]

            for game in games:
                if game["gameId"] == self.game_id:
                    home_team_data = game["homeTeam"]
                    away_team_data = game["awayTeam"]

                    # Game status: 1 = Not Started, 2 = In Progress, 3 = Final
                    status_code = game["gameStatus"]
                    if status_code == 1:
                        game_status = "pregame"
                    elif status_code == 2:
                        game_status = "live"
                    else:
                        game_status = "final"

                    return {
                        "home_score": int(home_team_data["score"]),
                        "away_score": int(away_team_data["score"]),
                        "period": game.get("period", 1),
                        "time_remaining": game.get("gameStatusText", "12:00"),
                        "game_status": game_status,
                    }

            return None

        except Exception as e:
            print(f"[Recorder] Error fetching NBA score: {e}")
            return None

    def save(self, filepath: str) -> None:
        """Save recording to JSON file.

        Args:
            filepath: Path to save the recording
        """
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            data = {
                "metadata": self.metadata.to_dict(),
                "frames": [f.to_dict() for f in self.frames],
            }

        with open(path, "w") as f:
            json.dump(data, f, indent=2)

        print(f"[Recorder] Saved recording to {filepath}")
        print(f"[Recorder] Total frames: {len(self.frames)}")

    @classmethod
    def load(cls, filepath: str) -> "NBAGameRecorder":
        """Load recording from file.

        Args:
            filepath: Path to the recording file

        Returns:
            NBAGameRecorder instance with loaded data
        """
        with open(filepath, "r") as f:
            data = json.load(f)

        metadata = GameRecordingMetadata.from_dict(data["metadata"])
        frames = [GameRecordingFrame.from_dict(f) for f in data["frames"]]

        recorder = cls(
            game_id=metadata.game_id,
            home_team=metadata.home_team,
            away_team=metadata.away_team,
            home_ticker=metadata.home_ticker,
            away_ticker=metadata.away_ticker,
        )
        recorder.metadata = metadata
        recorder.frames = frames

        print(f"[Recorder] Loaded recording from {filepath}")
        print(f"[Recorder] Game: {metadata.away_team} @ {metadata.home_team}")
        print(f"[Recorder] Date: {metadata.date}")
        print(f"[Recorder] Total frames: {len(frames)}")

        return recorder

    def get_frame_at_time(self, timestamp: float) -> Optional[GameRecordingFrame]:
        """Get the frame closest to a given timestamp.

        Args:
            timestamp: Unix timestamp

        Returns:
            Closest frame, or None if no frames
        """
        if not self.frames:
            return None

        # Binary search for closest frame
        left, right = 0, len(self.frames) - 1

        while left < right:
            mid = (left + right) // 2
            if self.frames[mid].timestamp < timestamp:
                left = mid + 1
            else:
                right = mid

        # Return the closest frame
        if left > 0 and abs(self.frames[left - 1].timestamp - timestamp) < abs(self.frames[left].timestamp - timestamp):
            return self.frames[left - 1]
        return self.frames[left]

    def get_frames_in_range(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> List[GameRecordingFrame]:
        """Get all frames within a time range.

        Args:
            start_time: Start timestamp (inclusive)
            end_time: End timestamp (inclusive)

        Returns:
            List of frames in the range
        """
        result = []
        for frame in self.frames:
            if start_time and frame.timestamp < start_time:
                continue
            if end_time and frame.timestamp > end_time:
                break
            result.append(frame)
        return result


def list_live_games() -> List[Dict[str, Any]]:
    """List currently live NBA games.

    Returns:
        List of dicts with game info (id, matchup, score, clock, etc.)
    """
    if not NBA_API_AVAILABLE:
        print("Error: nba_api not installed. Install with: pip install nba_api")
        return []

    try:
        board = scoreboard.ScoreBoard()
        games = board.get_dict()["scoreboard"]["games"]

        result = []
        for game in games:
            home_team = game["homeTeam"]
            away_team = game["awayTeam"]

            # Game status: 1 = Not Started, 2 = In Progress, 3 = Final
            status_code = game["gameStatus"]
            if status_code == 1:
                status = "pregame"
            elif status_code == 2:
                status = "live"
            else:
                status = "final"

            result.append({
                "game_id": game["gameId"],
                "home_team": home_team["teamTricode"],
                "away_team": away_team["teamTricode"],
                "home_score": int(home_team["score"]),
                "away_score": int(away_team["score"]),
                "period": game.get("period", 0),
                "clock": game.get("gameStatusText", ""),
                "status": status,
                "matchup": f"{away_team['teamTricode']} @ {home_team['teamTricode']}",
            })

        return result

    except Exception as e:
        print(f"Error fetching live games: {e}")
        return []
