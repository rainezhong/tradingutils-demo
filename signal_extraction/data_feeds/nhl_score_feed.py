"""
NHL Score Feed - Real-time NHL game score tracking.
Uses the official NHL Edge API for live scores.
"""

import requests
import time
import numpy as np
from typing import Optional, Dict

from .base_score_feed import BaseScoreFeed, GameScore, SportType


class NHLScoreFeed(BaseScoreFeed):
    """
    Real-time NHL score feed using NHL Edge API.
    
    NHL-specific features:
        - 3 periods of 20 minutes (60 min regulation)
        - Overtime (5 min) and Shootout
        - Lower scoring than basketball (typically 2-5 goals per team)
    """
    
    # NHL team code mapping (API -> Kalshi)
    TEAM_MAP = {
        "NJD": "NJ", "LAK": "LA", "TBL": "TB", "SJS": "SJ",
        "ANA": "ANA", "BOS": "BOS", "BUF": "BUF", "CGY": "CGY",
        "CAR": "CAR", "CHI": "CHI", "COL": "COL", "CBJ": "CBJ",
        "DAL": "DAL", "DET": "DET", "EDM": "EDM", "FLA": "FLA",
        "MIN": "MIN", "MTL": "MTL", "NSH": "NSH", "NYI": "NYI",
        "NYR": "NYR", "OTT": "OTT", "PHI": "PHI", "PIT": "PIT",
        "SEA": "SEA", "STL": "STL", "TOR": "TOR", "UTA": "UTA",
        "VAN": "VAN", "VGK": "VGK", "WSH": "WSH", "WPG": "WPG"
    }
    
    API_URL = "https://api-web.nhle.com/v1/score/now"
    
    def __init__(
        self,
        game_id: str,
        home_team_code: str,
        away_team_code: str,
        poll_interval_ms: int = 3000,
        history_size: int = 500
    ):
        super().__init__(
            game_id=game_id,
            home_team=home_team_code,
            away_team=away_team_code,
            poll_interval_ms=poll_interval_ms,
            history_size=history_size
        )
        print(f"[NHLScoreFeed] Initialized for {away_team_code} @ {home_team_code}")
    
    @property
    def sport_type(self) -> SportType:
        return SportType.NHL
    
    def _fetch_score(self) -> Optional[GameScore]:
        """Fetch current score from NHL Edge API."""
        try:
            response = requests.get(self.API_URL, timeout=5)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"[NHLScoreFeed] API error: {e}")
            return None
        
        # Find our game
        for game in data.get('games', []):
            if str(game.get('id')) == str(self.game_id):
                return self._parse_game(game)
        
        return None
    
    def _parse_game(self, game: dict) -> GameScore:
        """Parse NHL API game data into GameScore."""
        away = game.get('awayTeam', {})
        home = game.get('homeTeam', {})
        clock = game.get('clock', {})
        period_desc = game.get('periodDescriptor', {})
        
        # Game status
        game_state = game.get('gameState', '')
        if game_state in ['LIVE', 'CRIT']:
            game_status = 'live'
        elif game_state == 'FINAL':
            game_status = 'final'
        else:
            game_status = 'pregame'
        
        # Period info
        period_num = period_desc.get('number', 1)
        period_type = period_desc.get('periodType', 'REG')
        
        # Time remaining
        time_remaining = clock.get('timeRemaining', '20:00')
        
        # Extras for NHL-specific data
        extras = {
            'period_type': period_type,  # REG, OT, SO
            'in_intermission': clock.get('inIntermission', False)
        }
        
        return GameScore(
            timestamp=time.time(),
            game_id=str(game.get('id')),
            home_team=self.TEAM_MAP.get(home.get('abbrev'), home.get('abbrev', '')),
            away_team=self.TEAM_MAP.get(away.get('abbrev'), away.get('abbrev', '')),
            home_score=int(home.get('score', 0)),
            away_score=int(away.get('score', 0)),
            period=period_num,
            time_remaining=time_remaining,
            game_status=game_status,
            sport=SportType.NHL,
            extras=extras
        )
    
    def calculate_win_probability(
        self,
        score_diff: int,
        period: int,
        time_remaining_seconds: int
    ) -> float:
        """
        Calculate win probability for NHL.
        
        NHL-specific model:
            - Goals matter more than in basketball (each goal is ~15% swing)
            - Lead protection increases dramatically in 3rd period
            - Empty net situations in final minutes
        """
        # Total regulation time: 60 minutes = 3600 seconds
        total_time = 3600
        
        # Estimate time elapsed
        time_per_period = 1200  # 20 minutes
        if period <= 3:
            time_elapsed = (period - 1) * time_per_period + (time_per_period - time_remaining_seconds)
        else:
            # Overtime
            time_elapsed = total_time - 60  # Near end of game
        
        game_completion = min(time_elapsed / total_time, 0.99)
        
        # NHL logistic model
        # Each goal is worth more than in basketball
        # k scales with game completion (goals matter more late)
        k = 0.4 + (1.8 * game_completion)
        
        base_prob = 1.0 / (1.0 + np.exp(-k * score_diff))
        
        # Period adjustment
        if period >= 3:
            period_weight = 1.0
        else:
            period_weight = 0.6 + (0.4 * period / 3.0)
        
        win_prob = base_prob * (0.5 + 0.5 * period_weight)
        
        return float(np.clip(win_prob, 0.02, 0.98))
    
    def get_game_completion(self, period: int, time_remaining_seconds: int) -> float:
        """Calculate NHL game completion percentage."""
        total_time = 3600  # 60 minutes
        time_per_period = 1200  # 20 minutes
        
        if period <= 3:
            time_elapsed = (period - 1) * time_per_period + (time_per_period - time_remaining_seconds)
        else:
            # Overtime - treat as 95%+ complete
            time_elapsed = total_time + 300  # Add 5 min OT
        
        return min(time_elapsed / total_time, 0.99)


def get_nhl_game_info_from_ticker(ticker: str) -> Optional[Dict]:
    """
    Extract game info from Kalshi ticker.
    
    Args:
        ticker: Kalshi ticker (e.g., "KXNHLGAME-26JAN27-EDMCGY")
        
    Returns:
        Dict with game_id, home_team, away_team, or None
    """
    try:
        # Fetch live games
        response = requests.get(NHLScoreFeed.API_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        for game in data.get('games', []):
            if game.get('gameState') not in ['LIVE', 'CRIT']:
                continue
                
            away = game.get('awayTeam', {})
            home = game.get('homeTeam', {})
            
            away_code = NHLScoreFeed.TEAM_MAP.get(away.get('abbrev'), away.get('abbrev', ''))
            home_code = NHLScoreFeed.TEAM_MAP.get(home.get('abbrev'), home.get('abbrev', ''))
            
            matchup = f"{away_code}{home_code}"
            
            if matchup in ticker:
                return {
                    'game_id': str(game.get('id')),
                    'home_team': home_code,
                    'away_team': away_code,
                    'matchup': matchup
                }
        
        return None
        
    except Exception as e:
        print(f"[get_nhl_game_info] Error: {e}")
        return None
