"""Basketball (NBA/NCAAB) truth source for the latency probe framework.

Polls ESPN scoreboard API for live game scores and converts score differentials
to win probabilities using a normal distribution model.

For NBA: https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard
For NCAAB: https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard
"""

import json
import logging
import math
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional

from core.latency_probe.truth_source import TruthReading, TruthSource
from core.latency_probe.recorder import ProbeRecorder

logger = logging.getLogger(__name__)

# ESPN API endpoints
ESPN_NBA_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_NCAAB_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"


# ------------------------------------------------------------------
# Win Probability Model (Normal Distribution)
# ------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    """Abramowitz & Stegun approximation of N(x)."""
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911

    sgn = 1 if x >= 0 else -1
    x = abs(x)

    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(
        -x * x / 2
    )
    return 0.5 * (1.0 + sgn * y)


def calculate_win_probability(
    score_diff: float,
    time_remaining_sec: float,
    possessions_per_48min: float = 100.0,
) -> float:
    """Calculate P(leading team wins) using normal distribution model.

    Args:
        score_diff: Points ahead (positive = home leading)
        time_remaining_sec: Seconds left in game
        possessions_per_48min: Pace of game (possessions per 48 min)

    Returns:
        Probability that the leading team wins (0-1)
    """
    if time_remaining_sec <= 0:
        return 1.0 if score_diff > 0 else (0.0 if score_diff < 0 else 0.5)

    # Estimate remaining possessions
    game_duration_sec = 48 * 60  # NBA: 48 minutes
    possessions_per_sec = possessions_per_48min / game_duration_sec
    remaining_possessions = time_remaining_sec * possessions_per_sec

    if remaining_possessions <= 0:
        return 1.0 if score_diff > 0 else (0.0 if score_diff < 0 else 0.5)

    # Points per possession (league average ~1.1)
    ppp = 1.1

    # Standard deviation of final margin = sqrt(possessions) * sqrt(var_per_possession)
    # var_per_possession ≈ 1.0 for typical NBA game
    variance_per_poss = 1.0
    stdev = math.sqrt(remaining_possessions * variance_per_poss)

    if stdev <= 0:
        return 1.0 if score_diff > 0 else (0.0 if score_diff < 0 else 0.5)

    # Z-score: how many standard deviations is the current lead?
    z = score_diff / stdev

    # P(leading team wins) = N(z)
    prob = _normal_cdf(z)
    return max(0.001, min(0.999, prob))


# ------------------------------------------------------------------
# Total Points (Over/Under) Model
# ------------------------------------------------------------------

def calculate_over_probability(
    home_score: int,
    away_score: int,
    time_remaining_sec: float,
    strike: float,
    game_duration_sec: float = 48 * 60,
    prior_total: float = 224.0,
    possessions_per_game: float = 200.0,
    variance_per_poss: float = 1.2,
) -> tuple:
    """Calculate P(total points > strike) given live game state.

    Uses a blended pace model: prior league average weighted with observed
    game pace, shifting toward observed as the game progresses.

    Args:
        home_score: Current home score
        away_score: Current away score
        time_remaining_sec: Seconds remaining in regulation
        strike: Over/under line (e.g. 220.5)
        game_duration_sec: Total regulation time (NBA=2880, NCAAB=2400)
        prior_total: Prior expected game total (NBA≈224, NCAAB≈140)
        possessions_per_game: Expected total possessions both teams (NBA≈200)
        variance_per_poss: Scoring variance per possession for totals

    Returns:
        (over_probability, expected_total) tuple
    """
    current_total = home_score + away_score

    if time_remaining_sec <= 0:
        prob = 1.0 if current_total > strike else (
            0.5 if current_total == strike else 0.0
        )
        return (prob, float(current_total))

    elapsed = game_duration_sec - time_remaining_sec

    # --- Expected remaining points ---
    # Prior: league-average pace extrapolation
    prior_remaining = prior_total * (time_remaining_sec / game_duration_sec)

    # Observed: project from scoring rate so far (need >=2 min of data)
    if elapsed > 120:
        observed_rate = current_total / elapsed  # pts/sec
        obs_remaining = observed_rate * time_remaining_sec
    else:
        obs_remaining = prior_remaining

    # Blend: shift from prior → observed as game progresses
    # Fully observed by halftime (game_pct >= 0.5 → weight = 1)
    game_pct = elapsed / game_duration_sec
    weight = min(1.0, game_pct * 2.0)
    expected_remaining = (1.0 - weight) * prior_remaining + weight * obs_remaining

    expected_total = current_total + expected_remaining

    # --- Standard deviation of remaining scoring ---
    remaining_poss = possessions_per_game * (time_remaining_sec / game_duration_sec)
    sigma = math.sqrt(max(remaining_poss * variance_per_poss, 0.01))

    # P(total > strike) = 1 - Phi((strike - expected_total) / sigma)
    z = (strike - expected_total) / sigma
    prob = 1.0 - _normal_cdf(z)

    return (max(0.001, min(0.999, prob)), expected_total)


# ------------------------------------------------------------------
# ESPN API Helpers
# ------------------------------------------------------------------

@dataclass
class GameState:
    """Live game state from ESPN."""
    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int
    clock: str
    status: str  # "in" (live), "pre", "post"
    timestamp: float


def fetch_espn_scoreboard(league: str) -> List[GameState]:
    """Fetch live games from ESPN API.

    Args:
        league: "nba" or "ncaab"

    Returns:
        List of GameState objects for live games
    """
    url = ESPN_NBA_URL if league == "nba" else ESPN_NCAAB_URL

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        logger.error("ESPN API error: %s", e)
        return []

    games = []
    for event in data.get("events", []):
        try:
            competition = event.get("competitions", [{}])[0]
            competitors = competition.get("competitors", [])

            home = away = None
            for c in competitors:
                if c.get("homeAway") == "home":
                    home = c
                else:
                    away = c

            if not home or not away:
                continue

            status = competition.get("status", {})
            state = status.get("type", {}).get("state", "pre")

            # Only include live games
            if state != "in":
                continue

            games.append(GameState(
                game_id=event.get("id", ""),
                home_team=home.get("team", {}).get("abbreviation", ""),
                away_team=away.get("team", {}).get("abbreviation", ""),
                home_score=int(home.get("score", 0)),
                away_score=int(away.get("score", 0)),
                period=status.get("period", 0),
                clock=status.get("displayClock", "12:00"),
                status=state,
                timestamp=time.time(),
            ))
        except Exception as e:
            logger.warning("Failed to parse ESPN event: %s", e)
            continue

    return games


def parse_clock(clock_str: str) -> float:
    """Parse clock string like '4:21' to seconds."""
    try:
        parts = clock_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return float(clock_str)
    except (ValueError, AttributeError):
        return 0.0


def calculate_time_remaining(period: int, clock_str: str, league: str) -> float:
    """Calculate total seconds remaining in game.

    Args:
        period: Current period/quarter
        clock_str: Time remaining in period (e.g., "4:21")
        league: "nba" (4x12min) or "ncaab" (2x20min)

    Returns:
        Total seconds remaining
    """
    period_secs = parse_clock(clock_str)

    if league == "nba":
        # 4 quarters x 12 minutes
        period_duration = 12 * 60
        total_periods = 4
    else:
        # 2 halves x 20 minutes
        period_duration = 20 * 60
        total_periods = 2

    remaining_periods = max(0, total_periods - period)
    return period_secs + (remaining_periods * period_duration)


# ------------------------------------------------------------------
# BasketballTruthSource
# ------------------------------------------------------------------

GAME_STATES_SQL = """
    CREATE TABLE IF NOT EXISTS espn_game_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        game_id TEXT NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_score INTEGER NOT NULL,
        away_score INTEGER NOT NULL,
        period INTEGER NOT NULL,
        clock TEXT NOT NULL,
        time_remaining_sec REAL NOT NULL,
        home_win_prob REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_espn_game_ts ON espn_game_states(ts);
    CREATE INDEX IF NOT EXISTS idx_espn_game_id ON espn_game_states(game_id);
"""

TOTAL_POINTS_STATES_SQL = """
    CREATE TABLE IF NOT EXISTS espn_total_points_states (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        game_id TEXT NOT NULL,
        home_team TEXT NOT NULL,
        away_team TEXT NOT NULL,
        home_score INTEGER NOT NULL,
        away_score INTEGER NOT NULL,
        period INTEGER NOT NULL,
        clock TEXT NOT NULL,
        time_remaining_sec REAL NOT NULL,
        current_total INTEGER NOT NULL,
        expected_total REAL NOT NULL,
        over_prob REAL,
        strike REAL
    );
    CREATE INDEX IF NOT EXISTS idx_espn_tp_ts ON espn_total_points_states(ts);
    CREATE INDEX IF NOT EXISTS idx_espn_tp_game ON espn_total_points_states(game_id);
"""

# Market type constants
MARKET_TYPE_GAME_WINNER = "game_winner"
MARKET_TYPE_TOTAL_POINTS = "total_points"


class BasketballTruthSource(TruthSource):
    """ESPN scoreboard feed → win probability or total points over/under.

    Polls ESPN API for live game scores and converts to probabilities.
    Matches Kalshi tickers to ESPN games by parsing team abbreviations.

    Supports two market types:
      - "game_winner": P(home wins) from score diff + time remaining
      - "total_points": P(total > strike) from pace model + time remaining
    """

    def __init__(
        self,
        league: str,
        recorder: Optional[ProbeRecorder] = None,
        poll_interval: float = 5.0,
        market_type: str = MARKET_TYPE_GAME_WINNER,
    ) -> None:
        """Initialize basketball truth source.

        Args:
            league: "nba" or "ncaab"
            recorder: Optional ProbeRecorder for logging game states
            poll_interval: Seconds between ESPN polls (default: 5s)
            market_type: "game_winner" or "total_points"
        """
        self._league = league.lower()
        self._recorder = recorder
        self._poll_interval = poll_interval
        self._market_type = market_type
        self._games: Dict[str, GameState] = {}  # game_id -> GameState
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False

        # League-specific model parameters
        if self._league == "nba":
            self._game_duration_sec = 48 * 60.0
            self._prior_total = 224.0
            self._possessions_per_game = 200.0
        else:
            # NCAAB: 2 halves x 20 minutes, lower scoring
            self._game_duration_sec = 40 * 60.0
            self._prior_total = 140.0
            self._possessions_per_game = 135.0

        # Register extension tables
        if self._recorder:
            self._recorder.register_tables(GAME_STATES_SQL)
            if self._market_type == MARKET_TYPE_TOTAL_POINTS:
                self._recorder.register_tables(TOTAL_POINTS_STATES_SQL)

    def start(self) -> None:
        """Start the ESPN polling thread."""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("BasketballTruthSource: started (league=%s, poll=%ss)",
                    self._league, self._poll_interval)

    def stop(self) -> None:
        """Stop the ESPN polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def is_connected(self) -> bool:
        """Whether we've successfully fetched data from ESPN."""
        return self._connected

    def get_reading(
        self,
        ticker: str,
        strike: Optional[float],
        seconds_to_close: float,
    ) -> Optional[TruthReading]:
        """Get probability reading for a Kalshi market.

        For game_winner markets: returns P(home wins).
        For total_points markets: returns P(total > strike).

        Args:
            ticker: Kalshi ticker
            strike: Over/under line (used for total_points markets)
            seconds_to_close: Seconds until market expiration

        Returns:
            TruthReading or None if game not found
        """
        teams = self._parse_ticker(ticker)
        if not teams:
            return None

        home_team, away_team = teams

        with self._lock:
            game = self._find_game(home_team, away_team)
            if not game:
                return None

            time_remaining = calculate_time_remaining(
                game.period, game.clock, self._league
            )

            if self._market_type == MARKET_TYPE_TOTAL_POINTS:
                return self._get_total_points_reading(
                    game, time_remaining, strike
                )
            else:
                return self._get_game_winner_reading(game, time_remaining)

    def _get_game_winner_reading(
        self, game: GameState, time_remaining: float
    ) -> TruthReading:
        """Win probability model for game-winner markets."""
        score_diff = game.home_score - game.away_score
        home_win_prob = calculate_win_probability(score_diff, time_remaining)

        if self._recorder:
            self._recorder.execute(
                "INSERT INTO espn_game_states "
                "(ts, game_id, home_team, away_team, home_score, away_score, "
                "period, clock, time_remaining_sec, home_win_prob) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), game.game_id, game.home_team, game.away_team,
                    game.home_score, game.away_score, game.period, game.clock,
                    time_remaining, home_win_prob,
                ),
            )

        return TruthReading(
            timestamp=game.timestamp,
            probability=home_win_prob,
            raw_value=float(score_diff),
            metadata={
                "home_score": game.home_score,
                "away_score": game.away_score,
                "period": game.period,
                "clock": game.clock,
                "time_remaining": time_remaining,
            },
        )

    def _get_total_points_reading(
        self,
        game: GameState,
        time_remaining: float,
        strike: Optional[float],
    ) -> Optional[TruthReading]:
        """Over/under probability model for total points markets."""
        if strike is None or strike <= 0:
            return None

        over_prob, expected_total = calculate_over_probability(
            home_score=game.home_score,
            away_score=game.away_score,
            time_remaining_sec=time_remaining,
            strike=strike,
            game_duration_sec=self._game_duration_sec,
            prior_total=self._prior_total,
            possessions_per_game=self._possessions_per_game,
        )

        current_total = game.home_score + game.away_score

        if self._recorder:
            self._recorder.execute(
                "INSERT INTO espn_total_points_states "
                "(ts, game_id, home_team, away_team, home_score, away_score, "
                "period, clock, time_remaining_sec, current_total, "
                "expected_total, over_prob, strike) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(), game.game_id, game.home_team, game.away_team,
                    game.home_score, game.away_score, game.period, game.clock,
                    time_remaining, current_total, expected_total, over_prob,
                    strike,
                ),
            )

        return TruthReading(
            timestamp=game.timestamp,
            probability=over_prob,
            raw_value=expected_total,
            metadata={
                "home_score": game.home_score,
                "away_score": game.away_score,
                "period": game.period,
                "clock": game.clock,
                "time_remaining": time_remaining,
                "current_total": current_total,
                "expected_total": expected_total,
                "strike": strike,
            },
        )

    def _poll_loop(self) -> None:
        """Background thread: poll ESPN API periodically."""
        while self._running:
            try:
                games = fetch_espn_scoreboard(self._league)

                with self._lock:
                    # Update game cache
                    self._games = {g.game_id: g for g in games}
                    self._connected = len(games) > 0 or self._connected

                if games:
                    logger.debug("ESPN: fetched %d live games", len(games))

            except Exception as e:
                logger.error("ESPN poll error: %s", e)

            time.sleep(self._poll_interval)

    def _parse_ticker(self, ticker: str) -> Optional[tuple]:
        """Parse Kalshi ticker to extract (home_team, away_team).

        Handles both formats:
            KXNBAGAME-26FEB22-LAL-GSW-H    → ("GSW", "LAL")  # dash-separated
            KXNBATOTAL-26FEB03BOSDAL-229    → ("DAL", "BOS")  # concatenated 3-letter codes
            KXNCAAMBGAME-26FEB22-DUKE-UNC-H → ("UNC", "DUKE")
        """
        try:
            # Try concatenated format first: ...YYMMMDD{AWAY3}{HOME3}...
            match = re.search(r"\d{2}[A-Z]{3}\d{2}([A-Z]{3})([A-Z]{3})", ticker)
            if match:
                away = match.group(1)
                home = match.group(2)
                return (home, away)

            # Fallback: dash-separated format SERIES-DATE-AWAY-HOME-H/A
            parts = ticker.split("-")
            if len(parts) >= 5:
                away = parts[-3]
                home = parts[-2]
                return (home, away)

            return None
        except Exception:
            return None

    def _find_game(self, home: str, away: str) -> Optional[GameState]:
        """Find game by team abbreviations (case-insensitive partial match)."""
        home_upper = home.upper()
        away_upper = away.upper()

        for game in self._games.values():
            game_home = game.home_team.upper()
            game_away = game.away_team.upper()

            # Exact match
            if game_home == home_upper and game_away == away_upper:
                return game

            # Partial match (e.g., "LAL" matches "LAKERS")
            if home_upper in game_home and away_upper in game_away:
                return game
            if game_home in home_upper and game_away in away_upper:
                return game

        return None
