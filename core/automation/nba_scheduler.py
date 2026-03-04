"""NBA Game Auto-Scheduler - Detects games starting and spawns recorders.

This module provides automatic NBA game recording that:
- Polls for live/pregame games every 5 minutes
- Spawns NBAGameRecorder tasks for each game
- Tracks state to avoid duplicate recordings
- Persists state to JSON for crash recovery
"""

import asyncio
import json
import logging
import signal
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Set

try:
    from core.recorder.record_kalshiNBA import NBAGameRecorder
except ImportError:
    from src.simulation.nba_recorder import NBAGameRecorder  # type: ignore[assignment]

from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient
from core.nba_utils import get_todays_games

logger = logging.getLogger(__name__)


def _generate_kalshi_tickers(
    home_team: str, away_team: str, game_date: Optional[datetime] = None
) -> tuple:
    """Generate Kalshi tickers for an NBA game.

    The ticker format is: KXNBAGAME-{YY}{MON}{DD}{AWAY}{HOME}-{TEAM}
    Example: KXNBAGAME-26JAN28CHIIND-IND for Indiana win market

    Args:
        home_team: Home team tricode (e.g., "LAL")
        away_team: Away team tricode (e.g., "BOS")
        game_date: Date of the game (defaults to today)

    Returns:
        Tuple of (home_ticker, away_ticker)
    """
    if game_date is None:
        game_date = datetime.now()

    month_abbrev = game_date.strftime("%b").upper()
    date_str = f"{game_date.year % 100}{month_abbrev}{game_date.day:02d}"

    matchup = f"{away_team}{home_team}"
    prefix = f"KXNBAGAME-{date_str}{matchup}"

    home_ticker = f"{prefix}-{home_team}"
    away_ticker = f"{prefix}-{away_team}"

    return home_ticker, away_ticker


class NBAGameScheduler:
    """Scheduler that auto-detects NBA games and spawns recorders.

    Example usage:
        scheduler = NBAGameScheduler()

        # Single poll for testing
        await scheduler.poll_once()

        # Run as daemon
        await scheduler.run_forever()

    The scheduler polls at different intervals based on time of day:
    - During NBA game hours (6 PM - 1 AM ET): every 2 minutes
    - Outside game hours: every 15 minutes

    This ensures we catch games right when they start for complete recordings.
    """

    DEFAULT_POLL_INTERVAL = 300  # 5 minutes (used as fallback)
    ACTIVE_POLL_INTERVAL = 120  # 2 minutes during game hours
    IDLE_POLL_INTERVAL = 900  # 15 minutes outside game hours
    STATE_FILE = "data/nba_scheduler_state.json"
    RECORDINGS_DIR = "data/recordings"

    def __init__(
        self,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        state_file: Optional[str] = None,
        recordings_dir: Optional[str] = None,
        demo: bool = False,
        verbose: bool = False,
    ):
        """Initialize the scheduler.

        Args:
            poll_interval: Seconds between polls (default 300 = 5 min)
            state_file: Path to state persistence file
            recordings_dir: Directory to save recordings
            demo: Use Kalshi demo API
            verbose: Enable verbose logging
        """
        self.poll_interval = poll_interval
        self.state_file = Path(state_file or self.STATE_FILE)
        self.recordings_dir = Path(recordings_dir or self.RECORDINGS_DIR)
        self.demo = demo
        self.verbose = verbose

        # In-memory state
        self._active: Dict[str, Dict[str, Any]] = {}  # game_id -> {task, recorder, ...}
        self._completed_today: Set[str] = set()
        self._current_date: str = ""

        # Control
        self._running = False
        self._lock = asyncio.Lock()

        # Kalshi client (created on first use)
        self._kalshi_client: Optional[KalshiExchangeClient] = None

        # Load persisted state
        self._load_state()

        # Setup signal handlers
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _get_current_poll_interval(self) -> int:
        """Get poll interval based on current time.

        NBA games typically run:
        - Weekdays: 7 PM - 11 PM ET (some at 10 PM)
        - Weekends: 12 PM - 11 PM ET

        We poll more frequently during these hours to catch games at start.

        Returns:
            Poll interval in seconds
        """
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()  # 0=Monday, 6=Sunday

        # Weekend: games can start from noon
        if weekday >= 5:  # Saturday or Sunday
            if 11 <= hour <= 23 or hour == 0:
                return self.ACTIVE_POLL_INTERVAL
        else:
            # Weekday: games typically 7 PM - midnight
            if 18 <= hour <= 23 or hour == 0:
                return self.ACTIVE_POLL_INTERVAL

        # Also poll actively if we have active recordings
        if self._active:
            return self.ACTIVE_POLL_INTERVAL

        return self.IDLE_POLL_INTERVAL

    def _handle_shutdown(self, signum: int, frame) -> None:
        """Handle shutdown signals gracefully."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        self.stop()

    def _get_kalshi_client(self) -> KalshiExchangeClient:
        """Get or create Kalshi client."""
        if self._kalshi_client is None:
            self._kalshi_client = KalshiExchangeClient.from_env(demo=self.demo)
        return self._kalshi_client

    def _load_state(self) -> None:
        """Load persisted state from JSON file."""
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file) as f:
                state = json.load(f)

            # Check if state is from today
            today = datetime.now().strftime("%Y-%m-%d")
            if state.get("date") == today:
                self._completed_today = set(state.get("completed", []))
                self._current_date = today
                logger.info(
                    f"Loaded state: {len(self._completed_today)} completed games"
                )
            else:
                # Reset for new day
                logger.info("State from previous day, starting fresh")
                self._current_date = today
                self._completed_today = set()
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _save_state(self) -> None:
        """Save state to JSON file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

            state = {
                "date": self._current_date or datetime.now().strftime("%Y-%m-%d"),
                "completed": list(self._completed_today),
                "active": list(self._active.keys()),
            }

            with open(self.state_file, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def _should_record_game(self, game: Dict[str, Any]) -> bool:
        """Determine if a game should be recorded.

        Args:
            game: Game info dict from get_todays_games()

        Returns:
            True if game should be recorded
        """
        game_id = game["game_id"]
        status = game["status"]

        # Only record pregame or live games
        if status not in ("pregame", "live"):
            return False

        # Skip if already tracking
        if game_id in self._active:
            return False

        # Skip if already completed today
        if game_id in self._completed_today:
            return False

        return True

    async def _start_recorder(self, game: Dict[str, Any]) -> None:
        """Start a recorder for a game as a background async task.

        Args:
            game: Game info dict from get_todays_games()
        """
        game_id = game["game_id"]
        home_team = game["home_team"]
        away_team = game["away_team"]

        # Generate tickers (kept for logging / external reference)
        home_ticker, away_ticker = _generate_kalshi_tickers(home_team, away_team)

        logger.info(f"Starting recorder for {away_team} @ {home_team} (ID: {game_id})")
        if self.verbose:
            logger.info(f"  Home ticker: {home_ticker}")
            logger.info(f"  Away ticker: {away_ticker}")

        # Build Kalshi date string from current date (e.g., "26FEB10")
        now = datetime.now()
        kalshi_date = f"{now.year % 100}{now.strftime('%b').upper()}{now.day:02d}"

        # Create recorder (new interface: home_team, away_team, date)
        recorder = NBAGameRecorder(
            home_team=home_team,
            away_team=away_team,
            date=kalshi_date,
        )

        async def run_recorder():
            try:
                # Create a new client for this task
                client = KalshiExchangeClient.from_env(demo=self.demo)
                try:
                    await client.__aenter__()
                    recording = await recorder.start_async(client)
                finally:
                    await client.__aexit__(None, None, None)

                # Save recording when done
                if len(recording) > 0:
                    self._save_recording(recorder, game)

            except Exception as e:
                logger.error(f"Recorder error for {game_id}: {e}")
            finally:
                # Mark as completed
                async with self._lock:
                    if game_id in self._active:
                        del self._active[game_id]
                    self._completed_today.add(game_id)
                    self._save_state()

        task = asyncio.create_task(run_recorder())

        # Track
        async with self._lock:
            self._active[game_id] = {
                "task": task,
                "recorder": recorder,
                "home_team": home_team,
                "away_team": away_team,
                "started_at": datetime.now().isoformat(),
            }
            self._save_state()

    def _save_recording(self, recorder: NBAGameRecorder, game: Dict[str, Any]) -> None:
        """Save a completed recording to disk.

        Args:
            recorder: The NBAGameRecorder instance
            game: Game info dict
        """
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{game['away_team']}_vs_{game['home_team']}_{date_str}.json"
        filepath = self.recordings_dir / filename

        recorder.save(str(filepath))
        logger.info(f"Saved recording: {filepath}")

    async def _cleanup_finished_tasks(self) -> None:
        """Clean up tasks that have finished."""
        async with self._lock:
            finished = []
            for game_id, info in self._active.items():
                task = info["task"]
                if task.done():
                    finished.append(game_id)

            for game_id in finished:
                del self._active[game_id]
                self._completed_today.add(game_id)

            if finished:
                self._save_state()

    async def poll_once(self) -> int:
        """Poll for games once and start recorders as needed.

        Returns:
            Number of new recorders started
        """
        # Reset state if new day
        today = datetime.now().strftime("%Y-%m-%d")
        if self._current_date != today:
            logger.info(f"New day: {today}")
            self._current_date = today
            self._completed_today = set()
            self._save_state()

        # Cleanup finished tasks
        await self._cleanup_finished_tasks()

        # Fetch games
        logger.info("Polling for NBA games...")
        games = get_todays_games()

        if not games:
            logger.info("No games found")
            return 0

        if self.verbose:
            for game in games:
                matchup = f"{game['away_team']} @ {game['home_team']}"
                clock = game.get("time_remaining", "")
                logger.info(f"  {matchup}: {game['status']} ({clock})")

        # Start recorders for eligible games
        started = 0
        for game in games:
            if self._should_record_game(game):
                await self._start_recorder(game)
                started += 1

        logger.info(
            f"Active recorders: {len(self._active)}, Completed today: {len(self._completed_today)}"
        )

        return started

    async def run_forever(self) -> None:
        """Run the scheduler loop indefinitely.

        Uses dynamic poll intervals - more frequent during NBA game hours.
        """
        self._running = True
        logger.info("Starting NBA scheduler (dynamic polling enabled)")
        logger.info(f"  Active hours poll: {self.ACTIVE_POLL_INTERVAL}s")
        logger.info(f"  Idle hours poll: {self.IDLE_POLL_INTERVAL}s")

        while self._running:
            try:
                await self.poll_once()
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")

            # Get current poll interval based on time of day
            current_interval = self._get_current_poll_interval()

            # Sleep in small increments to allow for quick shutdown
            for _ in range(current_interval):
                if not self._running:
                    break
                await asyncio.sleep(1)

        # Cleanup
        await self._cleanup()
        logger.info("Scheduler stopped")

    def stop(self) -> None:
        """Stop the scheduler and all recorders."""
        logger.info("Stopping scheduler...")
        self._running = False

        # Stop all active recorders
        # Note: _lock is an asyncio.Lock so we iterate without acquiring here;
        # stop() is called from signal handlers which are synchronous.
        for game_id, info in self._active.items():
            logger.info(f"Stopping recorder for {game_id}")
            recorder = info["recorder"]
            recorder.stop()

        # Cancel all active tasks
        for game_id, info in list(self._active.items()):
            task = info["task"]
            if not task.done():
                task.cancel()

        self._save_state()

    async def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        self.stop()

        # Wait briefly for tasks to finish
        tasks = [
            info["task"] for info in self._active.values() if not info["task"].done()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        if self._kalshi_client:
            self._kalshi_client = None

    async def get_status(self) -> Dict[str, Any]:
        """Get current scheduler status.

        Returns:
            Status dict with active/completed counts and details
        """
        async with self._lock:
            active_games = []
            for game_id, info in self._active.items():
                recording = info["recorder"].get_recording()
                active_games.append(
                    {
                        "game_id": game_id,
                        "matchup": f"{info['away_team']} @ {info['home_team']}",
                        "started_at": info["started_at"],
                        "snapshots": len(recording),
                    }
                )

            return {
                "running": self._running,
                "date": self._current_date,
                "poll_interval": self.poll_interval,
                "active_count": len(self._active),
                "completed_count": len(self._completed_today),
                "active_games": active_games,
                "completed_game_ids": list(self._completed_today),
            }
