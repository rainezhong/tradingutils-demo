"""NBA Utilities - Game progress and state tracking.

Uses nba_api to fetch live game data for filtering markets.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# nba_api is optional
try:
    from nba_api.live.nba.endpoints import scoreboard, boxscore
    from nba_api.stats.endpoints import leaguegamefinder

    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False
    scoreboard = None
    boxscore = None
    leaguegamefinder = None


class GamePeriod(Enum):
    """Game period/quarter."""

    PREGAME = "pregame"
    Q1 = "Q1"
    Q2 = "Q2"
    HALFTIME = "halftime"
    Q3 = "Q3"
    Q4 = "Q4"
    OT1 = "OT1"
    OT2 = "OT2"
    OT3 = "OT3"
    FINAL = "final"


@dataclass
class GameProgress:
    """Current state of an NBA game."""

    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int  # 1-4 for regulation, 5+ for OT
    period_name: str  # "Q1", "Q2", "Halftime", etc.
    time_remaining: str  # "12:00", "5:32", etc.
    game_status: str  # "pregame", "live", "halftime", "final"

    @property
    def is_first_half(self) -> bool:
        """True if game is in first half (Q1 or Q2) or halftime."""
        return self.period <= 2 or self.game_status == "halftime"

    @property
    def first_half_complete(self) -> bool:
        """True if first half has ended (Q3 or later, or halftime/final)."""
        return self.period >= 3 or self.game_status in ("halftime", "final")

    @property
    def is_live(self) -> bool:
        """True if game is currently in progress."""
        return self.game_status == "live"

    @property
    def is_final(self) -> bool:
        """True if game has ended."""
        return self.game_status == "final"

    @property
    def period_enum(self) -> GamePeriod:
        """Get period as enum."""
        if self.game_status == "pregame":
            return GamePeriod.PREGAME
        elif self.game_status == "halftime":
            return GamePeriod.HALFTIME
        elif self.game_status == "final":
            return GamePeriod.FINAL
        elif self.period == 1:
            return GamePeriod.Q1
        elif self.period == 2:
            return GamePeriod.Q2
        elif self.period == 3:
            return GamePeriod.Q3
        elif self.period == 4:
            return GamePeriod.Q4
        elif self.period == 5:
            return GamePeriod.OT1
        elif self.period == 6:
            return GamePeriod.OT2
        else:
            return GamePeriod.OT3


def get_todays_games() -> List[Dict[str, Any]]:
    """Get all games scheduled for today.

    Returns:
        List of game dicts with keys: game_id, home_team, away_team,
        home_score, away_score, status, start_time
    """
    if not NBA_API_AVAILABLE:
        logger.warning("nba_api not installed. Install with: pip install nba_api")
        return []

    try:
        board = scoreboard.ScoreBoard()
        games_data = board.get_dict()["scoreboard"]["games"]

        games = []
        for game in games_data:
            status_code = game["gameStatus"]
            if status_code == 1:
                status = "pregame"
            elif status_code == 2:
                status = "live"
            else:
                status = "final"

            games.append(
                {
                    "game_id": game["gameId"],
                    "home_team": game["homeTeam"]["teamTricode"],
                    "away_team": game["awayTeam"]["teamTricode"],
                    "home_score": int(game["homeTeam"]["score"])
                    if game["homeTeam"]["score"]
                    else 0,
                    "away_score": int(game["awayTeam"]["score"])
                    if game["awayTeam"]["score"]
                    else 0,
                    "status": status,
                    "period": game.get("period", 0),
                    "time_remaining": game.get("gameStatusText", ""),
                }
            )

        return games

    except Exception as e:
        logger.error(f"Error fetching today's games: {e}")
        return []


def get_live_games() -> List[Dict[str, Any]]:
    """Get only games that are currently in progress (status == 'live').

    Returns:
        List of live game dicts with keys: game_id, home_team, away_team,
        home_score, away_score, status, period, time_remaining
    """
    return [g for g in get_todays_games() if g["status"] == "live"]


def find_game(team1: str, team2: str) -> Optional[Dict[str, Any]]:
    """Find a game involving two teams from today's schedule.

    Args:
        team1: First team tricode (e.g., "SAS")
        team2: Second team tricode (e.g., "LAL")

    Returns:
        Game dict or None if not found
    """
    games = get_todays_games()
    team1, team2 = team1.upper(), team2.upper()

    for game in games:
        teams = {game["home_team"], game["away_team"]}
        if team1 in teams and team2 in teams:
            return game

    return None


def get_game_progress(team1: str, team2: str) -> Optional[GameProgress]:
    """Get current progress of a game between two teams.

    Args:
        team1: First team tricode (e.g., "SAS")
        team2: Second team tricode (e.g., "LAL")

    Returns:
        GameProgress object or None if game not found
    """
    game = find_game(team1, team2)
    if not game:
        return None

    # Determine period name
    period = game["period"]
    status = game["status"]

    if status == "pregame":
        period_name = "Pregame"
    elif status == "final":
        period_name = "Final"
    elif period == 1:
        period_name = "Q1"
    elif period == 2:
        period_name = "Q2"
    elif period == 3:
        period_name = "Q3"
    elif period == 4:
        period_name = "Q4"
    else:
        period_name = f"OT{period - 4}"

    # Check for halftime (Q2 ended, Q3 not started)
    if period == 2 and "half" in game["time_remaining"].lower():
        status = "halftime"
        period_name = "Halftime"

    return GameProgress(
        game_id=game["game_id"],
        home_team=game["home_team"],
        away_team=game["away_team"],
        home_score=game["home_score"],
        away_score=game["away_score"],
        period=period,
        period_name=period_name,
        time_remaining=game["time_remaining"],
        game_status=status,
    )


def get_game_progress_by_id(game_id: str) -> Optional[GameProgress]:
    """Get game progress by NBA game ID.

    Args:
        game_id: NBA game ID (e.g., "0022400123")

    Returns:
        GameProgress object or None if not found
    """
    if not NBA_API_AVAILABLE:
        return None

    try:
        board = scoreboard.ScoreBoard()
        games = board.get_dict()["scoreboard"]["games"]

        for game in games:
            if game["gameId"] == game_id:
                return _parse_game_to_progress(game)

        return None

    except Exception as e:
        logger.error(f"Error fetching game {game_id}: {e}")
        return None


def _parse_game_to_progress(game: Dict[str, Any]) -> GameProgress:
    """Parse NBA API game dict to GameProgress."""
    status_code = game["gameStatus"]
    period = game.get("period", 0)
    time_text = game.get("gameStatusText", "")

    if status_code == 1:
        status = "pregame"
        period_name = "Pregame"
    elif status_code == 3:
        status = "final"
        period_name = "Final"
    else:
        status = "live"
        if period == 1:
            period_name = "Q1"
        elif period == 2:
            period_name = "Q2"
            if "half" in time_text.lower():
                status = "halftime"
                period_name = "Halftime"
        elif period == 3:
            period_name = "Q3"
        elif period == 4:
            period_name = "Q4"
        else:
            period_name = f"OT{period - 4}"

    return GameProgress(
        game_id=game["gameId"],
        home_team=game["homeTeam"]["teamTricode"],
        away_team=game["awayTeam"]["teamTricode"],
        home_score=int(game["homeTeam"]["score"]) if game["homeTeam"]["score"] else 0,
        away_score=int(game["awayTeam"]["score"]) if game["awayTeam"]["score"] else 0,
        period=period,
        period_name=period_name,
        time_remaining=time_text,
        game_status=status,
    )


def should_include_1h_markets(team1: str, team2: str) -> bool:
    """Check if first-half markets are still relevant for this game.

    Returns True if:
    - Game hasn't started
    - Game is in first half (Q1, Q2)
    - Game not found (assume pregame)

    Returns False if:
    - Game is in second half (Q3+)
    - Game is at halftime or later
    - Game is final
    """
    progress = get_game_progress(team1, team2)

    if progress is None:
        # Game not found - assume pregame, include 1H markets
        logger.info(
            f"Game {team1} vs {team2} not found in today's schedule - assuming pregame"
        )
        return True

    # Include 1H markets only if first half hasn't completed
    include = not progress.first_half_complete

    if not include:
        logger.info(
            f"First half complete for {team1} vs {team2} "
            f"({progress.period_name}) - excluding 1H markets"
        )

    return include
