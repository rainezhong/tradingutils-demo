"""Stub for score_feed module — original lives in a separate repo.
Provides minimal definitions so src.strategies.__init__ can import nba_mispricing."""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class GameScore:
    game_id: str = ""
    home_team: str = ""
    away_team: str = ""
    home_score: int = 0
    away_score: int = 0
    period: int = 0
    clock: str = ""
    status: str = ""


class NBAScoreFeed:
    pass


class ScoreAnalyzer:
    pass


def get_nba_game_info_from_ticker(ticker: str) -> Optional[Dict]:
    return None
