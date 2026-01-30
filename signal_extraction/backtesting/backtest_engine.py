"""
Walk-forward backtesting engine for live game prediction.
Tests model performance as if it were making real-time predictions.
"""

import numpy as np
import pandas as pd
from typing import List, Tuple, Optional, Callable
from dataclasses import dataclass
import sys
import os

# Add parent directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from models.kalman_filter import KalmanPriceFilter
from backtesting.metrics import calculate_prediction_metrics, PredictionMetrics


@dataclass
class BacktestResult:
    """Container for backtest results."""
    predictions: pd.DataFrame
    metrics: PredictionMetrics
    filter_state_history: List[Tuple[float, float]]  # (mean, variance) over time
    
    def summary(self) -> str:
        return f"""
Backtest Summary:
  Total Timesteps: {len(self.predictions)}
  {str(self.metrics)}
"""


class WalkForwardBacktest:
    """
    Walk-forward backtesting engine.
    
    Simulates live trading by:
    1. Starting with minimal/no data
    2. Making prediction for next timestep
    3. Observing actual price
    4. Updating model
    5. Repeat
    
    This mimics real-time trading where you can only use past data.
    """
    
    def __init__(
        self,
        process_noise: float = 1e-5,
        measurement_noise: float = 1e-3,
        warmup_periods: int = 10,
        prediction_horizon: int = 1
    ):
        """
        Initialize backtester.
        
        Args:
            process_noise: Kalman filter Q parameter
            measurement_noise: Kalman filter R parameter
            warmup_periods: Number of initial observations before making predictions
            prediction_horizon: Steps ahead to predict (1 = next observation)
        """
        self.Q = process_noise
        self.R = measurement_noise
        self.warmup = warmup_periods
        self.horizon = prediction_horizon
        
        self.kf = KalmanPriceFilter(process_noise, measurement_noise)
    
    def run(
        self,
        prices: pd.Series,
        verbose: bool = True
    ) -> BacktestResult:
        """
        Run walk-forward backtest on price series.
        
        Args:
            prices: Time series of observed prices
            verbose: Whether to print progress
            
        Returns:
            BacktestResult with predictions and metrics
        """
        if len(prices) < self.warmup + self.horizon:
            raise ValueError(f"Need at least {self.warmup + self.horizon} data points")
        
        # Initialize results storage
        results = []
        state_history = []
        
        # Reset filter with first observation
        self.kf.reset(price=prices.iloc[0])
        
        # Warmup phase
        for i in range(self.warmup):
            obs = prices.iloc[i]
            self.kf.update(obs)
            if verbose and i % 50 == 0:
                print(f"Warmup: {i}/{self.warmup}")
        
        # Testing phase - walk forward
        total_steps = len(prices) - self.warmup - self.horizon
        
        for i in range(self.warmup, len(prices) - self.horizon):
            # Current timestep index
            idx = prices.index[i]
            
            # Make prediction BEFORE seeing current observation
            pred_price, pred_var = self.kf.predict_ahead(self.horizon)
            pred_std = np.sqrt(pred_var)
            
            # Calculate confidence interval
            lower_95 = pred_price - 1.96 * pred_std
            upper_95 = pred_price + 1.96 * pred_std
            
            # The actual price we're trying to predict
            # (horizon steps ahead from current position)
            actual_future_idx = i + self.horizon
            actual_price = prices.iloc[actual_future_idx]
            
            # Current observed price (what we update the filter with)
            current_obs = prices.iloc[i]
            
            # Record results
            results.append({
                'timestamp': idx,
                'current_price': current_obs,
                'predicted_price': pred_price,
                'actual_future_price': actual_price,
                'prediction_error': actual_price - pred_price,
                'prediction_std': pred_std,
                'ci_lower': lower_95,
                'ci_upper': upper_95,
                'within_ci': lower_95 <= actual_price <= upper_95
            })
            
            # Update filter with current observation
            self.kf.update(current_obs)
            state_history.append((self.kf.x, self.kf.P))
            
            # Progress
            if verbose and (i - self.warmup) % 100 == 0:
                progress = ((i - self.warmup) / total_steps) * 100
                print(f"Progress: {progress:.1f}% ({i - self.warmup}/{total_steps})")
        
        # Convert to DataFrame
        df_results = pd.DataFrame(results)
        df_results.set_index('timestamp', inplace=True)
        
        # Calculate metrics
        predictions = df_results['predicted_price'].values
        actuals = df_results['actual_future_price'].values
        confidence_intervals = list(zip(
            df_results['ci_lower'].values,
            df_results['ci_upper'].values
        ))
        
        metrics = calculate_prediction_metrics(
            predictions, 
            actuals, 
            confidence_intervals
        )
        
        if verbose:
            print("\n" + "="*70)
            print("BACKTEST COMPLETE")
            print("="*70)
        
        return BacktestResult(
            predictions=df_results,
            metrics=metrics,
            filter_state_history=state_history
        )
    
    def run_with_refit(
        self,
        prices: pd.Series,
        refit_window: int = 500,
        verbose: bool = True
    ) -> BacktestResult:
        """
        Run backtest with periodic filter reset using rolling window.
        
        This prevents filter from becoming overconfident with old data.
        
        Args:
            prices: Time series of prices
            refit_window: How many recent observations to use when resetting
            verbose: Print progress
            
        Returns:
            BacktestResult
        """
        results = []
        state_history = []
        
        total_steps = len(prices) - self.warmup - self.horizon
        
        for i in range(self.warmup, len(prices) - self.horizon):
            # Refit filter periodically
            if (i - self.warmup) % refit_window == 0:
                if verbose:
                    print(f"\nRefitting at step {i - self.warmup}...")
                
                # Get recent window of data
                start_idx = max(0, i - refit_window)
                recent_prices = prices.iloc[start_idx:i]
                
                # Reset and warmup with recent data
                self.kf.reset(price=recent_prices.iloc[0])
                for price in recent_prices.iloc[1:]:
                    self.kf.update(price)
            
            # Make prediction
            idx = prices.index[i]
            pred_price, pred_var = self.kf.predict_ahead(self.horizon)
            pred_std = np.sqrt(pred_var)
            
            lower_95 = pred_price - 1.96 * pred_std
            upper_95 = pred_price + 1.96 * pred_std
            
            actual_future_idx = i + self.horizon
            actual_price = prices.iloc[actual_future_idx]
            current_obs = prices.iloc[i]
            
            results.append({
                'timestamp': idx,
                'current_price': current_obs,
                'predicted_price': pred_price,
                'actual_future_price': actual_price,
                'prediction_error': actual_price - pred_price,
                'prediction_std': pred_std,
                'ci_lower': lower_95,
                'ci_upper': upper_95,
                'within_ci': lower_95 <= actual_price <= upper_95
            })
            
            self.kf.update(current_obs)
            state_history.append((self.kf.x, self.kf.P))
            
            if verbose and (i - self.warmup) % 100 == 0:
                progress = ((i - self.warmup) / total_steps) * 100
                print(f"Progress: {progress:.1f}% ({i - self.warmup}/{total_steps})")
        
        df_results = pd.DataFrame(results)
        df_results.set_index('timestamp', inplace=True)
        
        predictions = df_results['predicted_price'].values
        actuals = df_results['actual_future_price'].values
        confidence_intervals = list(zip(
            df_results['ci_lower'].values,
            df_results['ci_upper'].values
        ))
        
        metrics = calculate_prediction_metrics(predictions, actuals, confidence_intervals)
        
        if verbose:
            print("\n" + "="*70)
            print("BACKTEST WITH REFIT COMPLETE")
            print("="*70)
        
        return BacktestResult(
            predictions=df_results,
            metrics=metrics,
            filter_state_history=state_history
        )


def compare_parameter_settings(
    prices: pd.Series,
    param_grid: List[Tuple[float, float]],
    warmup: int = 10,
    horizon: int = 1
) -> pd.DataFrame:
    """
    Compare different Kalman filter parameter settings.
    
    Args:
        prices: Price series to test on
        param_grid: List of (process_noise, measurement_noise) tuples
        warmup: Warmup periods
        horizon: Prediction horizon
        
    Returns:
        DataFrame comparing all parameter combinations
    """
    results = []
    
    print(f"\nTesting {len(param_grid)} parameter combinations...")
    
    for i, (Q, R) in enumerate(param_grid):
        print(f"\n[{i+1}/{len(param_grid)}] Testing Q={Q:.2e}, R={R:.2e}")
        
        backtester = WalkForwardBacktest(
            process_noise=Q,
            measurement_noise=R,
            warmup_periods=warmup,
            prediction_horizon=horizon
        )
        
        result = backtester.run(prices, verbose=False)
        
        results.append({
            'Q': Q,
            'R': R,
            'Q/R': Q/R,
            'MAE': result.metrics.mae,
            'RMSE': result.metrics.rmse,
            'Direction_Accuracy': result.metrics.direction_accuracy,
            'CI_Coverage': result.metrics.coverage_95,
            'Mean_Residual': result.metrics.mean_residual
        })
    
    df_comparison = pd.DataFrame(results)
    df_comparison = df_comparison.sort_values('MAE')
    
    print("\n" + "="*70)
    print("PARAMETER COMPARISON RESULTS")
    print("="*70)
    print(df_comparison.to_string(index=False))
    
    return df_comparison