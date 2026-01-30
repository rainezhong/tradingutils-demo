"""
Real-time NBA game score feed using nba_api.
Fetches live scores and calculates momentum features.
"""

import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, List
import numpy as np

# NBA API imports - optional, only needed for live feeds
try:
    from nba_api.live.nba.endpoints import scoreboard
    NBA_API_AVAILABLE = True
except ImportError:
    NBA_API_AVAILABLE = False
    scoreboard = None


@dataclass
class GameScore:
    """Current game state."""
    timestamp: float
    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int  # 1, 2, 3, 4 for regulation, 5+ for OT
    time_remaining: str  # e.g., "4:21"
    game_status: str  # 'pregame', 'live', 'final'
    
    @property
    def score_differential(self) -> int:
        """Home - Away"""
        return self.home_score - self.away_score
    
    @property
    def total_score(self) -> int:
        return self.home_score + self.away_score
    
    @property
    def is_live(self) -> bool:
        return self.game_status == 'live'


class ScoreAnalyzer:
    """
    Analyzes score history to extract predictive features.
    """
    
    @staticmethod
    def calculate_win_probability(
        score_diff: int,
        period: int,
        time_remaining_seconds: int
    ) -> float:
        """
        Estimate win probability based on score and time remaining.

        Uses Central Limit Theorem / Normal distribution model:
        - Each team's remaining points ~ N(μ * t, σ * sqrt(t))
        - Where t = time remaining as fraction of game
        - μ = 110 points (typical NBA team average)
        - σ = 10 points (empirical NBA std dev)

        The margin (A - B) at end of game follows:
        - Margin ~ N(current_diff, 10 * sqrt(2t))

        P(Team A wins) = Φ(score_diff / (10 * sqrt(2t)))
        where Φ is the standard normal CDF.

        NOTE: This model works best in Q1-Q2 (first half). Late game is too
        volatile - the model becomes overconfident and markets are more
        efficient. Limit trading to before halftime for best results.
        """
        from scipy.stats import norm

        # Total regulation time in NBA: 48 minutes = 2880 seconds
        total_time = 2880

        # Calculate time remaining as fraction of game
        # Each quarter is 12 minutes = 720 seconds
        if period <= 4:
            time_elapsed = (period - 1) * 720 + (720 - time_remaining_seconds)
        else:
            # Overtime - each OT is 5 minutes = 300 seconds
            # Treat as additional time beyond regulation
            ot_period = period - 4
            time_elapsed = total_time + (ot_period - 1) * 300 + (300 - time_remaining_seconds)
            total_time = total_time + ot_period * 300

        time_remaining_fraction = max(0.001, (total_time - time_elapsed) / total_time)

        # Standard deviation of the margin at game end
        # σ_diff = 10 * sqrt(2t) where t is time remaining fraction
        # Using σ = 10 points per team (empirical NBA data)
        sigma_diff = 10.0 * np.sqrt(2.0 * time_remaining_fraction)

        # Edge case: if game is essentially over, return based on who's ahead
        if sigma_diff < 0.1:
            if score_diff > 0:
                return 0.99
            elif score_diff < 0:
                return 0.01
            else:
                return 0.50

        # P(home wins) = P(margin > 0) = Φ(score_diff / sigma_diff)
        # score_diff = home_score - away_score
        z_score = score_diff / sigma_diff
        win_prob = norm.cdf(z_score)

        # Clip to [0.01, 0.99] - never absolutely certain
        return np.clip(win_prob, 0.01, 0.99)
    
    @staticmethod
    def calculate_momentum(
        score_history: List[GameScore],
        window: int = 5
    ) -> float:
        """
        Calculate scoring momentum (points per minute recently).
        
        Positive = home team momentum
        Negative = away team momentum
        """
        if len(score_history) < 2:
            return 0.0
        
        recent = list(score_history)[-min(window, len(score_history)):]
        
        if len(recent) < 2:
            return 0.0
        
        # Calculate score changes
        home_points = recent[-1].home_score - recent[0].home_score
        away_points = recent[-1].away_score - recent[0].away_score
        
        time_span = recent[-1].timestamp - recent[0].timestamp
        if time_span == 0:
            return 0.0
        
        # Points per minute
        time_span_minutes = time_span / 60.0
        home_rate = home_points / time_span_minutes
        away_rate = away_points / time_span_minutes
        
        momentum = home_rate - away_rate
        return momentum
    
    @staticmethod
    def detect_scoring_event(
        current: GameScore,
        previous: GameScore
    ) -> Optional[str]:
        """
        Detect if scoring just happened.
        
        Returns 'home_scored', 'away_scored', or None
        """
        home_diff = current.home_score - previous.home_score
        away_diff = current.away_score - previous.away_score
        
        if home_diff > 0 and away_diff > 0:
            # Both scored (rare, but possible in quick succession)
            if home_diff > away_diff:
                return 'home_scored'
            else:
                return 'away_scored'
        elif home_diff > 0:
            return 'home_scored'
        elif away_diff > 0:
            return 'away_scored'
        
        return None
    
    @staticmethod
    def parse_time_remaining(time_str: str) -> int:
        """
        Parse NBA time string to seconds.
        
        Examples:
            "Q4 4:21" -> parse quarter info separately
            "4:21" -> 261 seconds
            "0:45.3" -> 45 seconds
        """
        try:
            # Remove any quarter info
            time_str = time_str.split()[-1]
            
            # Split on colon
            parts = time_str.split(':')
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return int(minutes * 60 + seconds)
            elif len(parts) == 1:
                # Just seconds
                return int(float(parts[0]))
        except:
            pass
        
        # Default to 0 if can't parse
        return 0


class NBAScoreFeed:
    """
    Real-time NBA score feed using nba_api.
    """
    
    def __init__(
        self,
        game_id: str,
        home_team_tricode: str,
        away_team_tricode: str,
        poll_interval_ms: int = 3000,  # NBA scores update frequently
        history_size: int = 500
    ):
        """
        Initialize NBA score feed.
        
        Args:
            game_id: NBA game ID (e.g., "0022300001")
            home_team_tricode: Home team 3-letter code (e.g., "LAL")
            away_team_tricode: Away team 3-letter code (e.g., "BOS")
            poll_interval_ms: How often to poll for scores
            history_size: Number of snapshots to keep
        """
        self.game_id = game_id
        self.home_team = home_team_tricode
        self.away_team = away_team_tricode
        self.poll_interval = poll_interval_ms / 1000.0
        
        # Thread safety
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        
        # Data storage
        self.score_history = deque(maxlen=history_size)
        self.current_score: Optional[GameScore] = None
        
        # Event detection
        self.last_scoring_event: Optional[str] = None
        self.time_since_last_score: float = 0.0
        self.points_in_last_event: int = 0
        
        self.analyzer = ScoreAnalyzer()
        
        print(f"[NBAScoreFeed] Initialized for game {game_id}: {away_team_tricode} @ {home_team_tricode}")
    
    def start(self):
        """Start the feed thread."""
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            print(f"[NBAScoreFeed] Started for {self.game_id}")
    
    def stop(self):
        """Stop the feed thread."""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        print(f"[NBAScoreFeed] Stopped for {self.game_id}")
    
    def _run(self):
        """Main polling loop."""
        while not self.stop_event.is_set():
            try:
                score = self._fetch_score()
                
                if score:
                    with self.lock:
                        # Detect scoring events
                        if self.current_score and score.is_live:
                            event = self.analyzer.detect_scoring_event(score, self.current_score)
                            if event:
                                points_scored = 0
                                if event == 'home_scored':
                                    points_scored = score.home_score - self.current_score.home_score
                                else:
                                    points_scored = score.away_score - self.current_score.away_score
                                
                                self.last_scoring_event = event
                                self.time_since_last_score = 0.0
                                self.points_in_last_event = points_scored
                                
                                print(f"[NBAScoreFeed] {event.upper()}! +{points_scored} pts | "
                                      f"{self.away_team} {score.away_score} - {score.home_score} {self.home_team} | "
                                      f"Q{score.period} {score.time_remaining}")
                        
                        self.current_score = score
                        self.score_history.append(score)
                        self.time_since_last_score += self.poll_interval
                
            except Exception as e:
                print(f"[NBAScoreFeed Error] {e}")
            
            time.sleep(self.poll_interval)
    
    def _fetch_score(self) -> Optional[GameScore]:
        """
        Fetch current score from NBA API.
        """
        if not NBA_API_AVAILABLE:
            print("[NBAScoreFeed] nba_api not installed")
            return None

        try:
            # Get live scoreboard
            board = scoreboard.ScoreBoard()
            games = board.get_dict()['scoreboard']['games']
            
            # Find our game
            game_data = None
            for game in games:
                if game['gameId'] == self.game_id:
                    game_data = game
                    break
            
            if not game_data:
                # Game not found in live games
                return None
            
            # Extract score data
            home_team = game_data['homeTeam']
            away_team = game_data['awayTeam']
            
            # Game status: 1 = Not Started, 2 = In Progress, 3 = Final
            game_status_code = game_data['gameStatus']
            if game_status_code == 1:
                game_status = 'pregame'
            elif game_status_code == 2:
                game_status = 'live'
            else:
                game_status = 'final'
            
            # Get period (quarter)
            period = game_data.get('period', 1)
            
            # Get time remaining
            time_remaining = game_data.get('gameStatusText', '12:00')
            
            # Parse time to seconds for calculations
            time_remaining_seconds = self.analyzer.parse_time_remaining(time_remaining)
            
            score = GameScore(
                timestamp=time.time(),
                game_id=self.game_id,
                home_team=home_team['teamTricode'],
                away_team=away_team['teamTricode'],
                home_score=int(home_team['score']),
                away_score=int(away_team['score']),
                period=period,
                time_remaining=time_remaining,
                game_status=game_status
            )
            
            return score
            
        except Exception as e:
            print(f"[NBAScoreFeed Fetch Error] {e}")
            return None
    
    def get_current_features(self) -> Dict[str, float]:
        """
        Get current score-derived features.
        
        Returns dict with keys:
            - score_differential: Home - Away
            - win_probability: Estimated P(home wins)
            - momentum: Recent scoring rate differential
            - game_completion: % of game completed
            - total_points: Total points in game
            - seconds_since_score: Time since last scoring event
            - scoring_event_recent: 1.0 if scored in last 30s, else 0.0
        """
        with self.lock:
            if not self.current_score:
                return self._empty_features()
            
            score = self.current_score
            
            # Basic features
            score_diff = score.score_differential
            
            # Time parsing
            time_remaining_seconds = self.analyzer.parse_time_remaining(score.time_remaining)
            
            # Win probability
            win_prob = self.analyzer.calculate_win_probability(
                score_diff,
                score.period,
                time_remaining_seconds
            )
            
            # Momentum
            momentum = self.analyzer.calculate_momentum(
                list(self.score_history),
                window=5
            )
            
            # Game completion
            total_time = 2880  # 48 minutes
            if score.period <= 4:
                time_elapsed = (score.period - 1) * 720 + (720 - time_remaining_seconds)
            else:
                time_elapsed = total_time - 60  # OT
            
            game_completion = min(time_elapsed / total_time, 0.99)
            
            # Scoring event recency
            scoring_event_recent = 1.0 if self.time_since_last_score < 30.0 else 0.0
            
            return {
                'score_differential': float(score_diff),
                'win_probability': win_prob,
                'momentum': momentum,
                'game_completion': game_completion,
                'total_points': float(score.total_score),
                'seconds_since_score': self.time_since_last_score,
                'scoring_event_recent': scoring_event_recent,
                'home_score': float(score.home_score),
                'away_score': float(score.away_score),
                'period': float(score.period),
                'points_in_last_event': float(self.points_in_last_event)
            }
    
    def _empty_features(self) -> Dict[str, float]:
        """Return default features when no data available."""
        return {
            'score_differential': 0.0,
            'win_probability': 0.5,
            'momentum': 0.0,
            'game_completion': 0.0,
            'total_points': 0.0,
            'seconds_since_score': 0.0,
            'scoring_event_recent': 0.0,
            'home_score': 0.0,
            'away_score': 0.0,
            'period': 1.0,
            'points_in_last_event': 0.0
        }
    
    def was_recent_score(self, within_seconds: float = 30.0) -> bool:
        """Check if scoring happened recently."""
        return self.time_since_last_score < within_seconds


def get_nba_game_info_from_ticker(ticker: str) -> Optional[Dict]:
    """
    Extract game info from Kalshi ticker.
    
    Args:
        ticker: Kalshi ticker (e.g., "NBAGSW-LALLAKERS-24JAN21")
        
    Returns:
        Dict with game_id, home_team, away_team, or None
    """
    try:
        # Get live games
        live_games = get_nbalive_games()
        
        # Parse ticker to extract team codes
        # Ticker format: NBAGSW-TEAMNAME-DATE or similar
        # The matchup in live_games is like "LACLAL" (away+home)
        
        for game in live_games:
            matchup = game['matchup']  # e.g., "LACLAL"
            
            # Check if this matchup appears in the ticker
            if matchup in ticker:
                # Extract team codes (3 letters each)
                away_team = matchup[:3]
                home_team = matchup[3:6]
                
                return {
                    'game_id': game['id'],
                    'home_team': home_team,
                    'away_team': away_team,
                    'matchup': matchup
                }
        
        return None
        
    except Exception as e:
        print(f"[get_nba_game_info] Error: {e}")
        return None


def get_nbalive_games():
    """
    Fetch live NBA games.

    Returns:
        List of dicts with game info
    """
    if not NBA_API_AVAILABLE:
        print("[get_nbalive_games] nba_api not installed")
        return []

    try:
        board = scoreboard.ScoreBoard()
        games = board.get_dict()['scoreboard']['games']
        
        live_games = []
        
        for game in games:
            # gameStatus: 1 = Not Started, 2 = In Progress, 3 = Final
            if game['gameStatus'] == 2:
                live_games.append({
                    'id': game['gameId'],
                    'matchup': f"{game['awayTeam']['teamTricode']}{game['homeTeam']['teamTricode']}",
                    'score': f"{game['awayTeam']['score']} - {game['homeTeam']['score']}",
                    'clock': game['gameStatusText']
                })
        
        return live_games
        
    except Exception as e:
        print(f"[get_nbalive_games] Error: {e}")
        return []