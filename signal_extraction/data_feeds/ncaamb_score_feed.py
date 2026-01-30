"""
NCAA Men's Basketball Score Feed - Real-time college basketball score tracking.
Uses ESPN API for live scores.
"""

import requests
import time
import numpy as np
from typing import Optional, Dict

from .base_score_feed import BaseScoreFeed, GameScore, SportType


class NCAAMBScoreFeed(BaseScoreFeed):
    """
    Real-time NCAA Men's Basketball score feed using ESPN API.
    
    NCAAMB-specific features:
        - 2 halves of 20 minutes each (40 min total)
        - Higher variance than NBA (more upsets)
        - March Madness dynamics differ from regular season
    """
    
    API_URL = "http://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
    
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
        print(f"[NCAAMBScoreFeed] Initialized for {away_team_code} @ {home_team_code}")
    
    @property
    def sport_type(self) -> SportType:
        return SportType.NCAAMB
    
    def _fetch_score(self) -> Optional[GameScore]:
        """Fetch current score from ESPN API."""
        try:
            response = requests.get(self.API_URL, timeout=5)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"[NCAAMBScoreFeed] API error: {e}")
            return None
        
        # Find our game
        for event in data.get('events', []):
            if str(event.get('id')) == str(self.game_id):
                return self._parse_event(event)
        
        return None
    
    def _parse_event(self, event: dict) -> GameScore:
        """Parse ESPN event data into GameScore."""
        competition = event['competitions'][0]
        status = event.get('status', {})
        
        # Find home and away teams
        competitors = competition['competitors']
        home = next(filter(lambda x: x['homeAway'] == 'home', competitors), None)
        away = next(filter(lambda x: x['homeAway'] == 'away', competitors), None)
        
        # Game status
        state = status.get('type', {}).get('state', 'pre')
        if state == 'in':
            game_status = 'live'
        elif state == 'post':
            game_status = 'final'
        else:
            game_status = 'pregame'
        
        # Period (1 = first half, 2 = second half, 3+ = overtime)
        period = status.get('period', 1)
        
        # Clock
        display_clock = status.get('displayClock', '20:00')
        
        # Extras for NCAAMB-specific data
        extras = {
            'is_overtime': period > 2,
            'home_rank': home.get('curatedRank', {}).get('current', 0) if home else 0,
            'away_rank': away.get('curatedRank', {}).get('current', 0) if away else 0
        }
        
        return GameScore(
            timestamp=time.time(),
            game_id=str(event.get('id')),
            home_team=home['team']['abbreviation'] if home else '',
            away_team=away['team']['abbreviation'] if away else '',
            home_score=int(home.get('score', 0)) if home else 0,
            away_score=int(away.get('score', 0)) if away else 0,
            period=period,
            time_remaining=display_clock,
            game_status=game_status,
            sport=SportType.NCAAMB,
            extras=extras
        )
    
    def calculate_win_probability(
        self,
        score_diff: int,
        period: int,
        time_remaining_seconds: int
    ) -> float:
        """
        Calculate win probability for college basketball.
        
        NCAAMB-specific model:
            - Similar to NBA but with 40 min game time
            - Higher variance (upsets more common)
            - Tournament games have different dynamics
        """
        # Total regulation time: 40 minutes = 2400 seconds
        total_time = 2400
        
        # Estimate time elapsed (2 halves of 20 min each)
        time_per_half = 1200
        if period <= 2:
            time_elapsed = (period - 1) * time_per_half + (time_per_half - time_remaining_seconds)
        else:
            # Overtime - near end of game
            time_elapsed = total_time - 60
        
        game_completion = min(time_elapsed / total_time, 0.99)
        
        # Logistic model (similar to NBA but slightly lower k for more variance)
        k = 0.30 + (2.2 * game_completion)
        
        base_prob = 1.0 / (1.0 + np.exp(-k * score_diff))
        
        # Half adjustment
        if period >= 2:
            period_weight = 1.0
        else:
            period_weight = 0.6 + (0.4 * period / 2.0)
        
        win_prob = base_prob * (0.5 + 0.5 * period_weight)
        
        return float(np.clip(win_prob, 0.02, 0.98))
    
    def get_game_completion(self, period: int, time_remaining_seconds: int) -> float:
        """Calculate NCAAMB game completion percentage."""
        total_time = 2400  # 40 minutes
        time_per_half = 1200  # 20 minutes
        
        if period <= 2:
            time_elapsed = (period - 1) * time_per_half + (time_per_half - time_remaining_seconds)
        else:
            # Overtime
            time_elapsed = total_time + 300  # Add 5 min OT
        
        return min(time_elapsed / total_time, 0.99)


def get_ncaamb_game_info_from_ticker(ticker: str) -> Optional[Dict]:
    """
    Extract game info from Kalshi ticker.
    
    Args:
        ticker: Kalshi ticker
        
    Returns:
        Dict with game_id, home_team, away_team, or None
    """
    try:
        response = requests.get(NCAAMBScoreFeed.API_URL, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        for event in data.get('events', []):
            status = event.get('status', {})
            if status.get('type', {}).get('state') != 'in':
                continue
            
            competition = event['competitions'][0]
            competitors = competition['competitors']
            home = next(filter(lambda x: x['homeAway'] == 'home', competitors), None)
            away = next(filter(lambda x: x['homeAway'] == 'away', competitors), None)
            
            if not home or not away:
                continue
            
            home_code = home['team']['abbreviation']
            away_code = away['team']['abbreviation']
            matchup = f"{away_code}{home_code}"
            
            # Check various matching patterns
            if matchup in ticker or home_code in ticker or away_code in ticker:
                return {
                    'game_id': str(event.get('id')),
                    'home_team': home_code,
                    'away_team': away_code,
                    'matchup': matchup
                }
        
        return None
        
    except Exception as e:
        print(f"[get_ncaamb_game_info] Error: {e}")
        return None
