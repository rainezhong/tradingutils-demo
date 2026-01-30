"""
Enhanced multi-dimensional Kalman Filter.
Incorporates orderbook imbalance, score data, and price into state estimation.
"""

import numpy as np
from typing import Dict, Tuple, Optional
from scipy.linalg import block_diag


class EnhancedKalmanFilter:
    """
    Multi-dimensional Kalman Filter for market prediction.
    
    State Vector:
        x = [price, price_velocity, fair_value_adjustment]
        
    Observations:
        z = [observed_price, orderbook_imbalance, score_probability]
    
    The filter combines:
    1. Price dynamics (trend + noise)
    2. Orderbook pressure (short-term predictor)
    3. Score-based fair value (fundamental anchor)
    """
    
    def __init__(
        self,
        process_noise: float = 1e-6,        # Reduced: filter changes slowly
        measurement_noise: float = 1e-2,    # Increased: trust price less
        imbalance_weight: float = 0.1,      # Reduced: orderbook often zero
        score_weight: float = 0.9           # Increased: trust fundamentals more
    ):
        """
        Initialize enhanced Kalman filter.
        
        Args:
            process_noise: Base process variance for price changes
            measurement_noise: Measurement noise in price observations
            imbalance_weight: How much to trust orderbook imbalance
            score_weight: How much to trust score-based probabilities
        """
        # Dimensionality
        self.n_states = 3  # [price, velocity, fair_value_adj]
        self.n_obs = 3     # [price, imbalance, score_prob]
        
        # State transition matrix (F)
        # x_t = F @ x_{t-1} + w_t
        self.F = np.array([
            [1.0, 1.0, 0.0],  # price_{t} = price_{t-1} + velocity_{t-1}
            [0.0, 0.9, 0.0],  # velocity decays (mean reversion)
            [0.0, 0.0, 0.95]  # fair value adjustment persists
        ])
        
        # Observation matrix (H)
        # z_t = H @ x_t + v_t
        self.H = np.array([
            [1.0, 0.0, 1.0],     # observed_price = price + fair_value_adj
            [0.0, imbalance_weight, 0.0],  # imbalance predicts velocity
            [0.0, 0.0, score_weight]       # score probability = fair value
        ])
        
        # Process noise covariance (Q)
        self.Q = np.diag([
            process_noise,      # price noise
            process_noise * 10, # velocity noise (more uncertain)
            process_noise * 5   # fair value adjustment noise
        ])
        
        # Measurement noise covariance (R)
        self.R = np.diag([
            measurement_noise,     # price measurement noise
            0.1,                   # imbalance measurement noise
            0.05                   # score probability noise
        ])
        
        # State estimate (x) and covariance (P)
        self.x = np.array([0.5, 0.0, 0.0])  # [price, velocity, fair_value_adj]
        self.P = np.eye(self.n_states) * 1.0
        
        # Weights for feature importance
        self.imbalance_weight = imbalance_weight
        self.score_weight = score_weight
        
    def reset(self, initial_price: float = 0.5):
        """Reset filter state."""
        self.x = np.array([initial_price, 0.0, 0.0])
        self.P = np.eye(self.n_states) * 1.0
    
    def predict(self) -> Tuple[np.ndarray, np.ndarray]:
        """
        Prediction step.
        
        Returns:
            (predicted_state, predicted_covariance)
        """
        # State prediction
        x_pred = self.F @ self.x
        
        # Covariance prediction
        P_pred = self.F @ self.P @ self.F.T + self.Q
        
        return x_pred, P_pred
    
    def update(
        self,
        observed_price: float,
        orderbook_imbalance: float,
        score_probability: float
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Update step with multi-dimensional observations.
        
        Args:
            observed_price: Current market mid-price
            orderbook_imbalance: Bid-ask imbalance [-1, 1]
            score_probability: Win probability from scores [0, 1]
            
        Returns:
            (updated_state, updated_covariance)
        """
        # Predict
        x_pred, P_pred = self.predict()
        
        # Measurement vector
        z = np.array([
            observed_price,
            orderbook_imbalance,
            score_probability
        ])
        
        # Innovation (measurement residual)
        y = z - self.H @ x_pred
        
        # Innovation covariance
        S = self.H @ P_pred @ self.H.T + self.R
        
        # Kalman gain
        K = P_pred @ self.H.T @ np.linalg.inv(S)
        
        # Updated state estimate
        self.x = x_pred + K @ y
        
        # Updated covariance
        I = np.eye(self.n_states)
        self.P = (I - K @ self.H) @ P_pred
        
        return self.x, self.P
    
    def get_price_estimate(self) -> float:
        """Get current price estimate."""
        return float(self.x[0] + self.x[2])  # price + fair_value_adjustment
    
    def get_velocity_estimate(self) -> float:
        """Get estimated price velocity (rate of change)."""
        return float(self.x[1])
    
    def get_fair_value_adjustment(self) -> float:
        """Get estimated fair value adjustment."""
        return float(self.x[2])
    
    def predict_ahead(self, steps: int = 1) -> Tuple[float, float]:
        """
        Multi-step ahead prediction.
        
        Args:
            steps: Number of time steps ahead
            
        Returns:
            (predicted_price, prediction_std)
        """
        x_pred = self.x.copy()
        P_pred = self.P.copy()
        
        # Forward propagate
        for _ in range(steps):
            x_pred = self.F @ x_pred
            P_pred = self.F @ P_pred @ self.F.T + self.Q
        
        # Extract price prediction
        pred_price = x_pred[0] + x_pred[2]  # price + adjustment
        pred_var = P_pred[0, 0] + P_pred[2, 2] + 2 * P_pred[0, 2]
        pred_std = np.sqrt(max(pred_var, 0))
        
        return float(pred_price), float(pred_std)
    
    def get_confidence_interval(
        self,
        steps: int = 1,
        confidence: float = 0.95
    ) -> Tuple[float, float]:
        """
        Get confidence interval for prediction.
        
        Args:
            steps: Steps ahead to predict
            confidence: Confidence level
            
        Returns:
            (lower_bound, upper_bound)
        """
        pred_price, pred_std = self.predict_ahead(steps)
        
        z_scores = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}
        z = z_scores.get(confidence, 1.96)
        
        lower = pred_price - z * pred_std
        upper = pred_price + z * pred_std
        
        # Clip to valid probability range
        lower = max(0.0, min(1.0, lower))
        upper = max(0.0, min(1.0, upper))
        
        return lower, upper
    
    def get_trading_signal(self, current_price: float) -> Dict[str, float]:
        """
        Generate trading signal based on filter state.
        
        Args:
            current_price: Current market price
            
        Returns:
            Dictionary with signal information
        """
        fair_value = self.get_price_estimate()
        velocity = self.get_velocity_estimate()
        
        # Mispricing
        mispricing = fair_value - current_price
        
        # Z-score (how many standard deviations away)
        price_std = np.sqrt(self.P[0, 0])
        z_score = mispricing / price_std if price_std > 0 else 0
        
        # Signal strength based on multiple factors
        signal_strength = 0.0
        
        # Factor 1: Mispricing magnitude
        signal_strength += np.tanh(z_score * 2)  # Bounded to [-1, 1]
        
        # Factor 2: Velocity alignment (momentum)
        if mispricing > 0 and velocity > 0:
            signal_strength += 0.3  # Underpriced and rising
        elif mispricing < 0 and velocity < 0:
            signal_strength -= 0.3  # Overpriced and falling
        
        # Factor 3: Confidence (lower uncertainty = stronger signal)
        confidence_factor = 1.0 / (1.0 + price_std * 10)
        signal_strength *= confidence_factor
        
        # Clip to [-1, 1]
        signal_strength = np.clip(signal_strength, -1, 1)
        
        return {
            'signal_strength': signal_strength,
            'fair_value': fair_value,
            'current_price': current_price,
            'mispricing': mispricing,
            'z_score': z_score,
            'velocity': velocity,
            'confidence': confidence_factor
        }


class FeatureIntegrator:
    """
    Combines features from multiple sources for Kalman filter input.
    """
    
    @staticmethod
    def prepare_observation(
        price: float,
        orderbook_features: Dict[str, float],
        score_features: Dict[str, float]
    ) -> Tuple[float, float, float]:
        """
        Prepare observation vector for Kalman filter.
        
        Args:
            price: Current market price
            orderbook_features: Dict from OrderbookFeed
            score_features: Dict from ScoreFeed
            
        Returns:
            (observed_price, orderbook_imbalance, score_probability)
        """
        # Price observation
        # Use microprice if available (more accurate than mid)
        observed_price = orderbook_features.get('microprice', price)
        
        # Orderbook imbalance
        # Combine raw imbalance with EMA for stability
        raw_imbalance = orderbook_features.get('imbalance', 0.0)
        ema_imbalance = orderbook_features.get('imbalance_ema', 0.0)
        orderbook_imbalance = 0.7 * ema_imbalance + 0.3 * raw_imbalance
        
        # Score probability
        # This is the fundamental "fair value" anchor
        score_probability = score_features.get('win_probability', 0.5)
        
        return observed_price, orderbook_imbalance, score_probability
    
    @staticmethod
    def calculate_feature_importance(
        orderbook_features: Dict[str, float],
        score_features: Dict[str, float]
    ) -> Dict[str, float]:
        """
        Calculate relative importance of each feature source.
        
        Returns weights that should sum to 1.0
        """
        # Orderbook importance increases with:
        # - High liquidity (can actually trade)
        # - Low spread (accurate pricing)
        liquidity = orderbook_features.get('liquidity_score', 0.0)
        spread_bps = orderbook_features.get('spread_bps', 100.0)
        orderbook_quality = liquidity / (1.0 + spread_bps / 10.0)
        
        # Score importance increases with:
        # - Game being live (scores matter)
        # - Later in game (more certainty)
        game_completion = score_features.get('game_completion', 0.0)
        score_importance = game_completion * 1.5
        
        # Normalize
        total = orderbook_quality + score_importance + 1.0  # +1 for price
        
        return {
            'price': 1.0 / total,
            'orderbook': orderbook_quality / total,
            'score': score_importance / total
        }