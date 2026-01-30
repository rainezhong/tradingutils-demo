"""
Tennis Score Feed - Real-time tennis match score tracking.
Uses ESPN API for ATP/WTA live scores.
"""

import requests
import time
import numpy as np
from typing import Optional, Dict

from .base_score_feed import BaseScoreFeed, GameScore, SportType


class TennisScoreFeed(BaseScoreFeed):
    """
    Real-time tennis score feed using ESPN API.
    
    Tennis-specific features:
        - Best of 3 or 5 sets
        - Games within sets, points within games
        - Serve advantage matters
        - Tiebreakers at 6-6
    """
    
    # ESPN API endpoints for different tours
    TOUR_ENDPOINTS = {
        'atp': 'http://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard',
        'wta': 'http://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard',
    }
    
    def __init__(
        self,
        match_id: str,
        player1_name: str,
        player2_name: str,
        tour: str = 'atp',
        best_of: int = 3,
        poll_interval_ms: int = 5000,
        history_size: int = 500
    ):
        super().__init__(
            game_id=match_id,
            home_team=player1_name,  # In tennis, we use player names
            away_team=player2_name,
            poll_interval_ms=poll_interval_ms,
            history_size=history_size
        )
        self.tour = tour
        self.best_of = best_of
        self.api_url = self.TOUR_ENDPOINTS.get(tour, self.TOUR_ENDPOINTS['atp'])
        print(f"[TennisScoreFeed] Initialized for {player1_name} vs {player2_name} ({tour.upper()}, Bo{best_of})")
    
    @property
    def sport_type(self) -> SportType:
        return SportType.TENNIS
    
    def _fetch_score(self) -> Optional[GameScore]:
        """Fetch current score from ESPN API."""
        try:
            response = requests.get(self.api_url, timeout=5)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as e:
            print(f"[TennisScoreFeed] API error: {e}")
            return None
        
        # Find our match
        for event in data.get('events', []):
            if str(event.get('id')) == str(self.game_id):
                return self._parse_event(event)
        
        return None
    
    def _parse_event(self, event: dict) -> GameScore:
        """Parse ESPN event data into GameScore."""
        competition = event['competitions'][0]
        status = event.get('status', {})
        
        # Find competitors
        competitors = competition['competitors']
        # In tennis, no home/away - use order
        player1 = competitors[0] if len(competitors) > 0 else None
        player2 = competitors[1] if len(competitors) > 1 else None
        
        # Game status
        state = status.get('type', {}).get('state', 'pre')
        if state == 'in':
            game_status = 'live'
        elif state == 'post':
            game_status = 'final'
        else:
            game_status = 'pregame'
        
        # Current set
        current_set = status.get('period', 1)
        
        # Get set scores
        p1_sets = 0
        p2_sets = 0
        
        if player1 and player2:
            # Parse linescores for sets won
            p1_linescores = player1.get('linescores', [])
            p2_linescores = player2.get('linescores', [])
            
            for i, (p1_ls, p2_ls) in enumerate(zip(p1_linescores, p2_linescores)):
                p1_games = p1_ls.get('value', 0)
                p2_games = p2_ls.get('value', 0)
                
                # Set complete?
                if p1_games >= 6 or p2_games >= 6:
                    if p1_games > p2_games:
                        p1_sets += 1
                    elif p2_games > p1_games:
                        p2_sets += 1
        
        # Clock (display current game score)
        display_clock = status.get('displayClock', '0-0')
        
        # Extras for tennis-specific data
        extras = {
            'best_of': self.best_of,
            'current_set': current_set,
            'p1_sets': p1_sets,
            'p2_sets': p2_sets,
            'is_serving_p1': True  # Would need to parse from API
        }
        
        return GameScore(
            timestamp=time.time(),
            game_id=str(event.get('id')),
            home_team=player1['athlete']['displayName'] if player1 else '',
            away_team=player2['athlete']['displayName'] if player2 else '',
            home_score=p1_sets,  # For tennis, score = sets won
            away_score=p2_sets,
            period=current_set,
            time_remaining=display_clock,
            game_status=game_status,
            sport=SportType.TENNIS,
            extras=extras
        )
    
    def calculate_win_probability(
        self,
        score_diff: int,
        period: int,  # Current set number
        time_remaining_seconds: int  # Not really used in tennis
    ) -> float:
        """
        Calculate win probability for tennis.
        
        Tennis-specific model:
            - Based on sets won vs sets to win
            - Each set is roughly independent
            - Player serving has advantage
        """
        if not self.current_score:
            return 0.5
        
        extras = self.current_score.extras or {}
        p1_sets = extras.get('p1_sets', 0)
        p2_sets = extras.get('p2_sets', 0)
        sets_to_win = (self.best_of + 1) // 2  # 2 for Bo3, 3 for Bo5
        
        # Win probability based on sets won
        # Use binomial-like model
        
        p1_remaining = sets_to_win - p1_sets
        p2_remaining = sets_to_win - p2_sets
        
        if p1_remaining <= 0:
            return 0.98
        if p2_remaining <= 0:
            return 0.02
        
        # Simple model: odds based on remaining sets
        # Player with fewer needed is favored
        total_remaining = p1_remaining + p2_remaining
        base_prob = p2_remaining / total_remaining  # P1 favored if p2 needs more
        
        # Adjust for current set progress (period)
        # Early in match: closer to 50%
        # Late in match: closer to extreme
        match_progress = (p1_sets + p2_sets) / (self.best_of - 1)
        confidence = 0.5 + 0.5 * match_progress
        
        win_prob = 0.5 + (base_prob - 0.5) * confidence
        
        return float(np.clip(win_prob, 0.02, 0.98))
    
    def get_game_completion(self, period: int, time_remaining_seconds: int) -> float:
        """Calculate tennis match completion percentage."""
        if not self.current_score:
            return 0.0
        
        extras = self.current_score.extras or {}
        p1_sets = extras.get('p1_sets', 0)
        p2_sets = extras.get('p2_sets', 0)
        
        sets_played = p1_sets + p2_sets
        max_sets = self.best_of
        
        return min(sets_played / max_sets, 0.99)


def get_tennis_match_info_from_ticker(ticker: str, tour: str = 'atp') -> Optional[Dict]:
    """
    Extract match info from Kalshi ticker.
    
    Args:
        ticker: Kalshi ticker
        tour: ATP or WTA
        
    Returns:
        Dict with match_id, player1, player2, or None
    """
    try:
        url = TennisScoreFeed.TOUR_ENDPOINTS.get(tour, TennisScoreFeed.TOUR_ENDPOINTS['atp'])
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        for event in data.get('events', []):
            status = event.get('status', {})
            if status.get('type', {}).get('state') != 'in':
                continue
            
            competition = event['competitions'][0]
            competitors = competition['competitors']
            
            if len(competitors) < 2:
                continue
            
            player1 = competitors[0]['athlete']['displayName']
            player2 = competitors[1]['athlete']['displayName']
            
            # Try to match player names to ticker
            p1_last = player1.split()[-1].upper()
            p2_last = player2.split()[-1].upper()
            
            if p1_last in ticker.upper() or p2_last in ticker.upper():
                return {
                    'match_id': str(event.get('id')),
                    'player1': player1,
                    'player2': player2,
                    'matchup': f"{p1_last}{p2_last}"
                }
        
        return None
        
    except Exception as e:
        print(f"[get_tennis_match_info] Error: {e}")
        return None
