"""
Soccer Score Feed - Real-time soccer game score tracking.
Uses ESPN API for UEFA Champions League and other leagues.
"""

import requests
import time
import numpy as np
from typing import Optional, Dict

from .base_score_feed import BaseScoreFeed, GameScore, SportType


class SoccerScoreFeed(BaseScoreFeed):
    """
    Real-time soccer score feed using ESPN API.
    
    Soccer-specific features:
        - 90 minutes (two 45-minute halves) + stoppage time
        - Low scoring (typically 1-3 goals per team)
        - Away goals can matter in some competitions
        - No overtime in regular league matches
    """
    
    # ESPN API endpoints for different competitions
    LEAGUE_ENDPOINTS = {
        'ucl': 'http://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard',
        'epl': 'http://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
        'laliga': 'http://site.api.espn.com/apis/site/v2/sports/soccer/esp.1/scoreboard',
        'mls': 'http://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard',
    }
    
    def __init__(
        self,
        game_id: str,
        home_team_code: str,
        away_team_code: str,
        league: str = 'ucl',
        poll_interval_ms: int = 5000,
        history_size: int = 500
    ):
        super().__init__(
            game_id=game_id,
            home_team=home_team_code,
            away_team=away_team_code,
            poll_interval_ms=poll_interval_ms,
            history_size=history_size
        )
        self.league = league
        self.api_url = self.LEAGUE_ENDPOINTS.get(league, self.LEAGUE_ENDPOINTS['ucl'])
        print(f"[SoccerScoreFeed] Initialized for {away_team_code} @ {home_team_code} ({league.upper()})")
    
    @property
    def sport_type(self) -> SportType:
        return SportType.SOCCER
    
    def _fetch_score(self) -> Optional[GameScore]:
        """Fetch current score from ESPN API."""
        try:
            response = requests.get(self.api_url, timeout=5)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"[SoccerScoreFeed] API error: {e}")
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
        
        # Period (1 = first half, 2 = second half, 3+ = extra time)
        period = status.get('period', 1)
        
        # Clock (e.g., "45+2" or "72")
        display_clock = status.get('displayClock', '0')
        
        # Extras for soccer-specific data
        extras = {
            'is_extra_time': period > 2,
            'is_penalty_shootout': period > 4
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
            sport=SportType.SOCCER,
            extras=extras
        )
    
    def calculate_win_probability(
        self,
        score_diff: int,
        period: int,
        time_remaining_seconds: int
    ) -> float:
        """
        Calculate win probability for soccer.
        
        Soccer-specific model:
            - Goals are rare and very impactful
            - Draws are common (affects probability model)
            - Late goals (80+ min) are less likely to be equalized
        """
        # Total time: 90 minutes = 5400 seconds (ignoring stoppage)
        total_time = 5400
        
        # Estimate current minute from period and clock
        # Period 1 = first half (0-45), Period 2 = second half (45-90)
        if period == 1:
            current_minute = 45 - (time_remaining_seconds / 60)
        else:
            current_minute = 45 + (45 - time_remaining_seconds / 60)
        
        current_minute = max(0, min(90, current_minute))
        game_completion = current_minute / 90.0
        
        # Soccer has 3 outcomes: win, draw, lose
        # We model P(home win) based on goal difference and time
        
        # Logistic model with soccer-specific k
        # Goals matter enormously in soccer (each goal ~25% swing)
        k = 0.6 + (1.2 * game_completion)
        
        if score_diff == 0:
            # Draw - 50% for each side
            base_prob = 0.5
        else:
            base_prob = 1.0 / (1.0 + np.exp(-k * score_diff))
        
        # Late game adjustment (harder to come back late)
        if current_minute > 75:
            if score_diff > 0:
                base_prob = min(0.95, base_prob * 1.1)
            elif score_diff < 0:
                base_prob = max(0.05, base_prob * 0.9)
        
        return float(np.clip(base_prob, 0.02, 0.98))
    
    def get_game_completion(self, period: int, time_remaining_seconds: int) -> float:
        """Calculate soccer game completion percentage."""
        if period == 1:
            # First half: 0-50%
            minutes_played = 45 - (time_remaining_seconds / 60)
            return min(minutes_played / 90, 0.5)
        else:
            # Second half: 50-100%
            minutes_played = 45 + (45 - time_remaining_seconds / 60)
            return min(minutes_played / 90, 0.99)


def get_soccer_game_info_from_ticker(ticker: str, league: str = 'ucl') -> Optional[Dict]:
    """
    Extract game info from Kalshi ticker.
    
    Args:
        ticker: Kalshi ticker
        league: Which league to search
        
    Returns:
        Dict with game_id, home_team, away_team, or None
    """
    try:
        url = SoccerScoreFeed.LEAGUE_ENDPOINTS.get(league, SoccerScoreFeed.LEAGUE_ENDPOINTS['ucl'])
        response = requests.get(url, timeout=5)
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
            
            if matchup in ticker or home_code in ticker or away_code in ticker:
                return {
                    'game_id': str(event.get('id')),
                    'home_team': home_code,
                    'away_team': away_code,
                    'matchup': matchup
                }
        
        return None
        
    except Exception as e:
        print(f"[get_soccer_game_info] Error: {e}")
        return None
