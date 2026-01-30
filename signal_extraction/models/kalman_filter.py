"""
Unified Kalman Filter implementation for market prediction.
Supports both batch (historical) and online (streaming) modes.
"""

import numpy as np
import pandas as pd
from typing import Tuple, Optional, Union


class KalmanPriceFilter:
    """
    Local Level Kalman Filter for market price prediction.
    
    Model:
        State: x_t = x_{t-1} + w_t,  w_t ~ N(0, Q)  (process noise)
        Obs:   z_t = x_t + v_t,      v_t ~ N(0, R)  (measurement noise)
    
    Where:
        x_t = True underlying price at time t
        z_t = Observed mid-price at time t
        Q = Process variance (how fast true price changes)
        R = Measurement variance (bid-ask bounce, noise)
    """
    
    def __init__(
        self, 
        process_noise: float = 1e-5,
        measurement_noise: float = 1e-3,
        initial_price: float = 0.5,
        initial_uncertainty: float = 1.0
    ):
        """
        Initialize Kalman Filter.
        
        Args:
            process_noise: Q - How much we expect true price to move per step
            measurement_noise: R - How noisy/unreliable are observations
            initial_price: Starting estimate for price
            initial_uncertainty: Starting estimate for our uncertainty
        """
        self.Q = process_noise
        self.R = measurement_noise
        
        # State estimate (posterior)
        self.x = initial_price           # Current price estimate
        self.P = initial_uncertainty     # Current uncertainty (variance)
        
        # For tracking filter history
        self.history = []
    
    def predict(self) -> Tuple[float, float]:
        """
        Prediction step: Project current state forward.
        
        For random walk: E[x_{t+1}] = x_t
        But uncertainty grows: P_{t+1} = P_t + Q
        
        Returns:
            (predicted_price, predicted_variance)
        """
        x_pred = self.x
        P_pred = self.P + self.Q
        return x_pred, P_pred
    
    def update(self, measurement: Optional[float]) -> Tuple[float, float]:
        """
        Update step: Incorporate new observation.
        
        Args:
            measurement: Observed mid-price (or None to skip update)
        
        Returns:
            (filtered_price, filtered_variance)
        """
        if measurement is None:
            return self.x, self.P
        
        # Predict
        x_pred, P_pred = self.predict()
        
        # Update (Kalman gain and correction)
        K = P_pred / (P_pred + self.R)              # Kalman gain
        self.x = x_pred + K * (measurement - x_pred) # Corrected estimate
        self.P = (1 - K) * P_pred                    # Corrected uncertainty
        
        return self.x, self.P
    
    def predict_ahead(self, steps: int = 1) -> Tuple[float, float]:
        """
        Multi-step ahead prediction.
        
        For random walk: mean stays constant, but uncertainty grows linearly.
        
        Args:
            steps: Number of time steps ahead
            
        Returns:
            (predicted_price, predicted_variance)
        """
        pred_mean = self.x
        pred_var = self.P + (self.Q * steps)
        return pred_mean, pred_var
    
    def get_confidence_interval(self, steps: int = 1, confidence: float = 0.95) -> Tuple[float, float]:
        """
        Calculate confidence interval for prediction.
        
        Args:
            steps: Steps ahead to predict
            confidence: Confidence level (e.g., 0.95 for 95%)
            
        Returns:
            (lower_bound, upper_bound)
        """
        pred_mean, pred_var = self.predict_ahead(steps)
        std = np.sqrt(pred_var)
        
        # For 95% CI: ±1.96 standard deviations
        z_score = {0.90: 1.645, 0.95: 1.96, 0.99: 2.576}.get(confidence, 1.96)
        
        lower = pred_mean - z_score * std
        upper = pred_mean + z_score * std
        return lower, upper
    
    def reset(self, price: Optional[float] = None, uncertainty: Optional[float] = None):
        """Reset filter state."""
        if price is not None:
            self.x = price
        if uncertainty is not None:
            self.P = uncertainty
        self.history = []


class BatchKalmanFilter:
    """
    Batch processor for historical data.
    Useful for backtesting and analysis.
    """
    
    def __init__(self, process_noise: float = 1e-5, measurement_noise: float = 1e-3):
        self.Q = process_noise
        self.R = measurement_noise
        self.filter = KalmanPriceFilter(process_noise, measurement_noise)
    
    def fit(self, prices: Union[np.ndarray, pd.Series]) -> pd.DataFrame:
        """
        Run Kalman filter over entire price series.
        
        Args:
            prices: Array or Series of observed mid-prices
            
        Returns:
            DataFrame with columns: ['observation', 'filtered', 'predicted', 
                                     'residual', 'uncertainty']
        """
        if isinstance(prices, pd.Series):
            index = prices.index
            prices = prices.values
        else:
            index = range(len(prices))
        
        # Reset filter
        self.filter.reset(price=prices[0] if len(prices) > 0 else 0.5)
        
        results = []
        
        for i, obs in enumerate(prices):
            # One-step ahead prediction (before seeing observation)
            pred_price, pred_var = self.filter.predict()
            
            # Update with observation
            filt_price, filt_var = self.filter.update(obs)
            
            results.append({
                'observation': obs,
                'filtered': filt_price,
                'predicted': pred_price,  # What we predicted BEFORE seeing this point
                'residual': obs - pred_price,  # Prediction error
                'uncertainty': np.sqrt(filt_var)
            })
        
        df = pd.DataFrame(results, index=index)
        return df
    
    def fit_predict(self, train_prices: np.ndarray, test_steps: int = 1) -> Tuple[float, float]:
        """
        Fit on training data, then predict ahead.
        
        Args:
            train_prices: Historical prices to learn from
            test_steps: How many steps ahead to predict
            
        Returns:
            (predicted_price, prediction_std)
        """
        # Fit filter on training data
        self.fit(train_prices)
        
        # Generate prediction
        pred_mean, pred_var = self.filter.predict_ahead(test_steps)
        return pred_mean, np.sqrt(pred_var)


def calculate_mid_price(bid: float, ask: float) -> float:
    """Helper to calculate mid-price from bid-ask."""
    return (bid + ask) / 2.0


def prepare_market_data(df: pd.DataFrame, team_name: str) -> pd.Series:
    """
    Extract and prepare mid-price series from market dataframe.
    
    Args:
        df: DataFrame with columns like 'TeamName_YesBid', 'TeamName_YesAsk'
        team_name: Name prefix for columns
        
    Returns:
        Series of mid-prices with datetime index
    """
    bid_col = f"{team_name}_YesBid"
    ask_col = f"{team_name}_YesAsk"
    
    if bid_col not in df.columns or ask_col not in df.columns:
        raise ValueError(f"Required columns not found for {team_name}")
    
    # Calculate mid-price
    mid_prices = (df[bid_col] + df[ask_col]) / 2.0
    
    # Fill any gaps
    mid_prices = mid_prices.ffill().bfill()
    
    return mid_prices