"""NCAAB Game Auto-Scheduler - Detects games starting and spawns recorders.

Unlike the NBA scheduler, NCAAB team abbreviations aren't standardized between
ESPN and Kalshi, so we can't generate tickers deterministically. Instead:
1. Fetch all open KXNCAAMBGAME markets from Kalshi
2. Group into pairs by event_ticker
3. Match to ESPN games by comparing team abbreviations
"""

import json
import logging
import signal
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Old path: from src.simulation.ncaab_recorder import NCAABGameRecorder, list_live_ncaab_games
try:
    from src.simulation.ncaab_recorder import NCAABGameRecorder, list_live_ncaab_games
except ImportError:
    NCAABGameRecorder = None  # type: ignore[assignment,misc]
    list_live_ncaab_games = None  # type: ignore[assignment]

# Old path: from src.kalshi.client import KalshiClient
from core.exchange_client.kalshi.kalshi_client import (
    KalshiExchangeClient as KalshiClient,
)

# Old path: from src.kalshi.models import MarketStatus
# MarketStatus is not available in core.exchange_client.kalshi; inline the value instead.
try:
    from src.kalshi.models import MarketStatus
except ImportError:

    class MarketStatus:  # type: ignore[no-redef]
        """Minimal stand-in for src.kalshi.models.MarketStatus."""

        OPEN = "open"
        CLOSED = "closed"
        SETTLED = "settled"


logger = logging.getLogger(__name__)


def _fetch_ncaab_market_pairs(kalshi_client) -> List[Dict[str, Any]]:
    """Fetch open KXNCAAMBGAME markets and group into pairs by event_ticker.

    Each Kalshi NCAAB game event has two markets (one per team). We group
    them by event_ticker and extract the team abbreviation from the ticker
    suffix.

    Args:
        kalshi_client: Connected KalshiClient instance

    Returns:
        List of dicts with keys: event_ticker, markets (list of 2),
        team_abbrevs (set of 2 team strings)
    """
    import asyncio

    async def _fetch():
        all_markets = []
        cursor = None
        for _ in range(10):  # Max 10 pages
            resp = await kalshi_client.get_markets(
                series_ticker="KXNCAAMBGAME",
                status=MarketStatus.OPEN,
                limit=200,
                cursor=cursor,
            )
            markets = resp.get("markets", [])
            all_markets.extend(markets)
            cursor = resp.get("cursor")
            if not cursor or not markets:
                break
        return all_markets

    loop = asyncio.new_event_loop()
    try:
        all_markets = loop.run_until_complete(_fetch())
    finally:
        loop.close()

    # Group by event_ticker
    by_event: Dict[str, List[Dict]] = defaultdict(list)
    for m in all_markets:
        event_ticker = m.get("event_ticker", "")
        if event_ticker:
            by_event[event_ticker].append(m)

    pairs = []
    for event_ticker, markets in by_event.items():
        if len(markets) != 2:
            continue

        # Extract team abbrevs from ticker suffixes
        # Ticker format: KXNCAAMBGAME-26FEB03UNCDUKE-DUKE
        team_abbrevs = set()
        for m in markets:
            ticker = m.get("ticker", "")
            parts = ticker.rsplit("-", 1)
            if len(parts) == 2:
                team_abbrevs.add(parts[1])

        if len(team_abbrevs) == 2:
            pairs.append(
                {
                    "event_ticker": event_ticker,
                    "markets": markets,
                    "team_abbrevs": team_abbrevs,
                }
            )

    return pairs


def _match_espn_to_kalshi(
    game: Dict[str, Any],
    pairs: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Match an ESPN game to a Kalshi market pair.

    Tries exact match first, then substring fallback (e.g., ESPN "DUKE"
    matching Kalshi "DUKE", or ESPN "NCST" matching Kalshi "NCSTATE").

    Args:
        game: ESPN game dict with home_team, away_team
        pairs: List of Kalshi market pairs from _fetch_ncaab_market_pairs()

    Returns:
        Matching pair dict, or None if no match
    """
    espn_home = game["home_team"].upper()
    espn_away = game["away_team"].upper()

    # Exact match: both ESPN teams found in Kalshi pair
    for pair in pairs:
        kalshi_teams = pair["team_abbrevs"]
        if espn_home in kalshi_teams and espn_away in kalshi_teams:
            return pair

    # Substring fallback: ESPN abbrev is substring of Kalshi abbrev or vice versa
    for pair in pairs:
        kalshi_teams = list(pair["team_abbrevs"])
        home_match = any(espn_home in kt or kt in espn_home for kt in kalshi_teams)
        away_match = any(espn_away in kt or kt in espn_away for kt in kalshi_teams)
        if home_match and away_match:
            return pair

    return None


def _get_tickers_from_pair(
    pair: Dict[str, Any],
    home_team: str,
    away_team: str,
) -> Tuple[str, str]:
    """Extract home and away tickers from a matched pair.

    Args:
        pair: Kalshi market pair dict
        home_team: ESPN home team abbreviation
        away_team: ESPN away team abbreviation

    Returns:
        Tuple of (home_ticker, away_ticker)
    """
    markets = pair["markets"]
    home_ticker = None
    away_ticker = None

    for m in markets:
        ticker = m.get("ticker", "")
        suffix = ticker.rsplit("-", 1)[-1].upper()

        # Try exact match first, then substring
        if (
            suffix == home_team.upper()
            or home_team.upper() in suffix
            or suffix in home_team.upper()
        ):
            home_ticker = ticker
        elif (
            suffix == away_team.upper()
            or away_team.upper() in suffix
            or suffix in away_team.upper()
        ):
            away_ticker = ticker

    # Fallback: assign arbitrarily if matching is ambiguous
    if not home_ticker or not away_ticker:
        home_ticker = markets[0]["ticker"]
        away_ticker = markets[1]["ticker"]

    return home_ticker, away_ticker


class NCAABGameScheduler:
    """Scheduler that auto-detects NCAAB games and spawns recorders.

    Key difference from NBAGameScheduler: can't generate tickers
    deterministically, so we fetch Kalshi markets and match by team abbrev.

    Example usage:
        scheduler = NCAABGameScheduler()
        scheduler.poll_once()       # single poll for testing
        scheduler.run_forever()     # run as daemon
    """

    DEFAULT_POLL_INTERVAL = 300
    ACTIVE_POLL_INTERVAL = 120
    IDLE_POLL_INTERVAL = 900
    STATE_FILE = "data/ncaab_scheduler_state.json"
    RECORDINGS_DIR = "data/recordings/ncaab"

    def __init__(
        self,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        state_file: Optional[str] = None,
        recordings_dir: Optional[str] = None,
        demo: bool = False,
        verbose: bool = False,
    ):
        self.poll_interval = poll_interval
        self.state_file = Path(state_file or self.STATE_FILE)
        self.recordings_dir = Path(recordings_dir or self.RECORDINGS_DIR)
        self.demo = demo
        self.verbose = verbose

        self._active: Dict[str, Dict[str, Any]] = {}
        self._completed_today: Set[str] = set()
        self._current_date: str = ""

        self._running = False
        self._lock = threading.Lock()
        self._kalshi_client: Optional[KalshiClient] = None

        self._load_state()
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGINT, self._handle_shutdown)

    def _get_current_poll_interval(self) -> int:
        """Get poll interval based on current time.

        NCAAB games:
        - Weekdays: 4 PM - midnight
        - Weekends: 10 AM - midnight
        """
        now = datetime.now()
        hour = now.hour
        weekday = now.weekday()

        if weekday >= 5:  # Saturday or Sunday
            if 10 <= hour <= 23:
                return self.ACTIVE_POLL_INTERVAL
        else:
            if 16 <= hour <= 23:
                return self.ACTIVE_POLL_INTERVAL

        if self._active:
            return self.ACTIVE_POLL_INTERVAL

        return self.IDLE_POLL_INTERVAL

    def _handle_shutdown(self, signum: int, frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, shutting down gracefully...")
        self.stop()

    def _get_kalshi_client(self) -> KalshiClient:
        if self._kalshi_client is None:
            self._kalshi_client = KalshiClient.from_env(demo=self.demo)
        return self._kalshi_client

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return

        try:
            with open(self.state_file) as f:
                state = json.load(f)

            today = datetime.now().strftime("%Y-%m-%d")
            if state.get("date") == today:
                self._completed_today = set(state.get("completed", []))
                self._current_date = today
                logger.info(
                    f"Loaded state: {len(self._completed_today)} completed games"
                )
            else:
                logger.info("State from previous day, starting fresh")
                self._current_date = today
                self._completed_today = set()
        except Exception as e:
            logger.error(f"Failed to load state: {e}")

    def _save_state(self) -> None:
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
        game_id = game["game_id"]
        status = game["status"]

        if status not in ("pregame", "live"):
            return False
        if game_id in self._active:
            return False
        if game_id in self._completed_today:
            return False

        return True

    def _start_recorder(
        self, game: Dict[str, Any], home_ticker: str, away_ticker: str
    ) -> None:
        game_id = game["game_id"]
        home_team = game["home_team"]
        away_team = game["away_team"]

        logger.info(f"Starting recorder for {away_team} @ {home_team} (ID: {game_id})")
        if self.verbose:
            logger.info(f"  Home ticker: {home_ticker}")
            logger.info(f"  Away ticker: {away_ticker}")

        if NCAABGameRecorder is None:
            logger.error("NCAABGameRecorder not available (import failed)")
            return

        recorder = NCAABGameRecorder(
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
            home_ticker=home_ticker,
            away_ticker=away_ticker,
        )

        def run_recorder():
            try:
                import asyncio

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                client = KalshiClient.from_env(demo=self.demo)

                try:
                    loop.run_until_complete(client.__aenter__())
                    loop.run_until_complete(recorder.start_async(client))
                finally:
                    loop.run_until_complete(client.__aexit__(None, None, None))
                    loop.close()

                if recorder.frames:
                    self._save_recording(recorder, game)

            except Exception as e:
                logger.error(f"Recorder error for {game_id}: {e}")
            finally:
                with self._lock:
                    if game_id in self._active:
                        del self._active[game_id]
                    self._completed_today.add(game_id)
                    self._save_state()

        thread = threading.Thread(
            target=run_recorder,
            name=f"ncaab-recorder-{game_id}",
            daemon=True,
        )
        thread.start()

        with self._lock:
            self._active[game_id] = {
                "thread": thread,
                "recorder": recorder,
                "home_team": home_team,
                "away_team": away_team,
                "started_at": datetime.now().isoformat(),
            }
            self._save_state()

    def _save_recording(
        self, recorder: "NCAABGameRecorder", game: Dict[str, Any]
    ) -> None:
        self.recordings_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{game['away_team']}_vs_{game['home_team']}_{date_str}.json"
        filepath = self.recordings_dir / filename

        recorder.save(str(filepath))
        logger.info(f"Saved recording: {filepath}")

    def _cleanup_finished_threads(self) -> None:
        with self._lock:
            finished = []
            for game_id, info in self._active.items():
                thread = info["thread"]
                if not thread.is_alive():
                    finished.append(game_id)

            for game_id in finished:
                del self._active[game_id]
                self._completed_today.add(game_id)

            if finished:
                self._save_state()

    def poll_once(self) -> int:
        """Poll for games once and start recorders as needed.

        Returns:
            Number of new recorders started
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if self._current_date != today:
            logger.info(f"New day: {today}")
            self._current_date = today
            self._completed_today = set()
            self._save_state()

        self._cleanup_finished_threads()

        # Fetch ESPN games
        logger.info("Polling for NCAAB games...")
        if list_live_ncaab_games is None:
            logger.error("list_live_ncaab_games not available (import failed)")
            return 0

        games = list_live_ncaab_games()

        if not games:
            logger.info("No games found")
            return 0

        eligible = [g for g in games if self._should_record_game(g)]
        if not eligible:
            if self.verbose:
                logger.info(f"Found {len(games)} games, none eligible for recording")
            logger.info(
                f"Active recorders: {len(self._active)}, Completed today: {len(self._completed_today)}"
            )
            return 0

        if self.verbose:
            for game in eligible:
                logger.info(f"  Eligible: {game['matchup']} ({game['status']})")

        # Fetch Kalshi market pairs
        logger.info(
            f"Fetching Kalshi NCAAB markets for {len(eligible)} eligible game(s)..."
        )
        try:
            import asyncio

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client = KalshiClient.from_env(demo=self.demo)
            try:
                loop.run_until_complete(client.__aenter__())
                pairs = _fetch_ncaab_market_pairs(client)
            finally:
                loop.run_until_complete(client.__aexit__(None, None, None))
                loop.close()
        except Exception as e:
            logger.error(f"Failed to fetch Kalshi markets: {e}")
            pairs = []

        if self.verbose:
            logger.info(f"  Found {len(pairs)} Kalshi market pair(s)")

        # Match and start recorders
        started = 0
        for game in eligible:
            pair = _match_espn_to_kalshi(game, pairs)
            if pair:
                home_ticker, away_ticker = _get_tickers_from_pair(
                    pair, game["home_team"], game["away_team"]
                )
                self._start_recorder(game, home_ticker, away_ticker)
                started += 1
            else:
                if self.verbose:
                    logger.info(f"  No Kalshi match for {game['matchup']}")

        logger.info(
            f"Active recorders: {len(self._active)}, Completed today: {len(self._completed_today)}"
        )
        return started

    def run_forever(self) -> None:
        self._running = True
        logger.info("Starting NCAAB scheduler (dynamic polling enabled)")
        logger.info(f"  Active hours poll: {self.ACTIVE_POLL_INTERVAL}s")
        logger.info(f"  Idle hours poll: {self.IDLE_POLL_INTERVAL}s")

        while self._running:
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")

            current_interval = self._get_current_poll_interval()
            for _ in range(current_interval):
                if not self._running:
                    break
                time.sleep(1)

        self._cleanup()
        logger.info("Scheduler stopped")

    def stop(self) -> None:
        logger.info("Stopping scheduler...")
        self._running = False

        with self._lock:
            for game_id, info in self._active.items():
                logger.info(f"Stopping recorder for {game_id}")
                recorder = info["recorder"]
                recorder.stop()

        for game_id, info in list(self._active.items()):
            thread = info["thread"]
            thread.join(timeout=5.0)

        self._save_state()

    def _cleanup(self) -> None:
        self.stop()
        if self._kalshi_client:
            self._kalshi_client = None

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            active_games = []
            for game_id, info in self._active.items():
                active_games.append(
                    {
                        "game_id": game_id,
                        "matchup": f"{info['away_team']} @ {info['home_team']}",
                        "started_at": info["started_at"],
                        "frames": len(info["recorder"].frames),
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
