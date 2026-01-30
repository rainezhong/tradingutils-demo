"""
Abstract base class for all sport score feeds.
Provides common interface for score tracking and win probability calculation.
"""

import time
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, List
from collections import deque
from enum import Enum


class SportType(Enum):
    """Supported sports."""
    NBA = "nba"
    NHL = "nhl"
    SOCCER = "soccer"
    TENNIS = "tennis"
    NCAAMB = "ncaamb"
    NFL = "nfl"


@dataclass
class GameScore:
    """Universal game score representation."""
    timestamp: float
    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int  # Quarter, period, half, set, etc.
    time_remaining: str
    game_status: str  # 'pregame', 'live', 'final'
    sport: SportType
    
    # Sport-specific extras (optional)
    extras: Dict = None
    
    @property
    def score_differential(self) -> int:
        """Home - Away."""
        return self.home_score - self.away_score
    
    @property
    def total_score(self) -> int:
        return self.home_score + self.away_score
    
    @property
    def is_live(self) -> bool:
        return self.game_status == 'live'


class BaseScoreFeed(ABC):
    """
    Abstract base class for sport-specific score feeds.
    
    Subclasses must implement:
        - _fetch_score(): Fetch current score from API
        - calculate_win_probability(): Sport-specific win prob model
        - get_game_completion(): % of game completed
    """
    
    def __init__(
        self,
        game_id: str,
        home_team: str,
        away_team: str,
        poll_interval_ms: int = 3000,
        history_size: int = 500
    ):
        self.game_id = game_id
        self.home_team = home_team
        self.away_team = away_team
        self.poll_interval = poll_interval_ms / 1000.0
        
        # Thread safety
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        
        # Data storage
        self.score_history: deque = deque(maxlen=history_size)
        self.current_score: Optional[GameScore] = None
        
        # Event detection
        self.last_scoring_event: Optional[str] = None
        self.time_since_last_score: float = 0.0
        self.points_in_last_event: int = 0
    
    @property
    @abstractmethod
    def sport_type(self) -> SportType:
        """Return the sport type for this feed."""
        pass
    
    def start(self):
        """Start the feed thread."""
        if self.thread is None or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
            print(f"[{self.sport_type.value.upper()}ScoreFeed] Started for {self.game_id}")
    
    def stop(self):
        """Stop the feed thread."""
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2.0)
        print(f"[{self.sport_type.value.upper()}ScoreFeed] Stopped for {self.game_id}")
    
    def _run(self):
        """Main polling loop."""
        while not self.stop_event.is_set():
            try:
                score = self._fetch_score()
                
                if score:
                    with self.lock:
                        # Detect scoring events
                        if self.current_score and score.is_live:
                            event = self._detect_scoring_event(score, self.current_score)
                            if event:
                                self._handle_scoring_event(event, score)
                        
                        self.current_score = score
                        self.score_history.append(score)
                        self.time_since_last_score += self.poll_interval
                
            except Exception as e:
                print(f"[{self.sport_type.value.upper()}ScoreFeed Error] {e}")
            
            time.sleep(self.poll_interval)
    
    @abstractmethod
    def _fetch_score(self) -> Optional[GameScore]:
        """
        Fetch current score from the sport's API.
        Must be implemented by subclasses.
        """
        pass
    
    @abstractmethod
    def calculate_win_probability(
        self,
        score_diff: int,
        period: int,
        time_remaining_seconds: int
    ) -> float:
        """
        Calculate win probability based on sport-specific model.
        
        Args:
            score_diff: Home - Away score
            period: Current period/quarter/half/set
            time_remaining_seconds: Time left in current period
            
        Returns:
            Probability of home team winning [0, 1]
        """
        pass
    
    @abstractmethod
    def get_game_completion(self, period: int, time_remaining_seconds: int) -> float:
        """
        Calculate what % of the game has been completed.
        
        Returns:
            Completion percentage [0, 1]
        """
        pass
    
    def _detect_scoring_event(
        self,
        current: GameScore,
        previous: GameScore
    ) -> Optional[str]:
        """Detect if scoring just happened."""
        home_diff = current.home_score - previous.home_score
        away_diff = current.away_score - previous.away_score
        
        if home_diff > 0 and away_diff > 0:
            return 'home_scored' if home_diff > away_diff else 'away_scored'
        elif home_diff > 0:
            return 'home_scored'
        elif away_diff > 0:
            return 'away_scored'
        return None
    
    def _handle_scoring_event(self, event: str, score: GameScore):
        """Handle a detected scoring event."""
        if event == 'home_scored':
            points = score.home_score - self.current_score.home_score
        else:
            points = score.away_score - self.current_score.away_score
        
        self.last_scoring_event = event
        self.time_since_last_score = 0.0
        self.points_in_last_event = points
        
        print(f"[{self.sport_type.value.upper()}ScoreFeed] {event.upper()}! +{points} | "
              f"{score.away_team} {score.away_score} - {score.home_score} {score.home_team}")
    
    def get_current_features(self) -> Dict[str, float]:
        """Get current score-derived features for strategy."""
        with self.lock:
            if not self.current_score:
                return self._empty_features()
            
            score = self.current_score
            time_remaining_seconds = self._parse_time_remaining(score.time_remaining)
            
            win_prob = self.calculate_win_probability(
                score.score_differential,
                score.period,
                time_remaining_seconds
            )
            
            game_completion = self.get_game_completion(score.period, time_remaining_seconds)
            momentum = self._calculate_momentum()
            
            return {
                'score_differential': float(score.score_differential),
                'win_probability': win_prob,
                'momentum': momentum,
                'game_completion': game_completion,
                'total_points': float(score.total_score),
                'seconds_since_score': self.time_since_last_score,
                'scoring_event_recent': 1.0 if self.time_since_last_score < 30.0 else 0.0,
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
    
    def _calculate_momentum(self, window: int = 5) -> float:
        """Calculate scoring momentum from recent history."""
        if len(self.score_history) < 2:
            return 0.0
        
        recent = list(self.score_history)[-min(window, len(self.score_history)):]
        if len(recent) < 2:
            return 0.0
        
        home_points = recent[-1].home_score - recent[0].home_score
        away_points = recent[-1].away_score - recent[0].away_score
        
        time_span = recent[-1].timestamp - recent[0].timestamp
        if time_span == 0:
            return 0.0
        
        time_span_minutes = time_span / 60.0
        return (home_points - away_points) / time_span_minutes
    
    @staticmethod
    def _parse_time_remaining(time_str: str) -> int:
        """Parse time string to seconds."""
        try:
            time_str = time_str.split()[-1]
            parts = time_str.split(':')
            if len(parts) == 2:
                minutes = int(parts[0])
                seconds = float(parts[1])
                return int(minutes * 60 + seconds)
            elif len(parts) == 1:
                return int(float(parts[0]))
        except:
            pass
        return 0
    
    def was_recent_score(self, within_seconds: float = 30.0) -> bool:
        """Check if scoring happened recently."""
        return self.time_since_last_score < within_seconds
