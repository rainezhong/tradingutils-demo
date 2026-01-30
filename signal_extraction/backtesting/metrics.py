"""
Performance metrics for prediction accuracy and trading performance.
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple
from dataclasses import dataclass


@dataclass
class PredictionMetrics:
    """Container for prediction accuracy metrics."""
    mae: float              # Mean Absolute Error
    rmse: float             # Root Mean Squared Error
    mape: float             # Mean Absolute Percentage Error
    direction_accuracy: float  # % of correct up/down predictions
    coverage_95: float      # % of actual prices within 95% CI
    mean_residual: float    # Average prediction error (bias)
    std_residual: float     # Std dev of prediction errors
    total_predictions: int
    
    def __str__(self) -> str:
        return f"""
Prediction Metrics (n={self.total_predictions}):
  MAE:  ${self.mae:.4f}
  RMSE: ${self.rmse:.4f}
  MAPE: {self.mape:.2f}%
  Direction Accuracy: {self.direction_accuracy:.2f}%
  95% CI Coverage: {self.coverage_95:.2f}%
  Mean Residual: ${self.mean_residual:.4f}
  Std Residual:  ${self.std_residual:.4f}
"""


def calculate_prediction_metrics(
    predictions: np.ndarray,
    actuals: np.ndarray,
    confidence_intervals: List[Tuple[float, float]] = None
) -> PredictionMetrics:
    """
    Calculate comprehensive prediction accuracy metrics.
    
    Args:
        predictions: Predicted prices
        actuals: Actual observed prices
        confidence_intervals: List of (lower, upper) bounds for each prediction
        
    Returns:
        PredictionMetrics object
    """
    # Remove any NaN values
    mask = ~(np.isnan(predictions) | np.isnan(actuals))
    predictions = predictions[mask]
    actuals = actuals[mask]
    
    if len(predictions) == 0:
        return PredictionMetrics(0, 0, 0, 0, 0, 0, 0, 0)
    
    # Calculate errors
    errors = actuals - predictions
    abs_errors = np.abs(errors)
    squared_errors = errors ** 2
    
    # Core metrics
    mae = np.mean(abs_errors)
    rmse = np.sqrt(np.mean(squared_errors))
    mape = np.mean(np.abs(errors / actuals)) * 100  # As percentage
    mean_residual = np.mean(errors)
    std_residual = np.std(errors)
    
    # Direction accuracy (did we predict up/down correctly?)
    if len(predictions) > 1:
        pred_direction = np.diff(predictions) > 0
        actual_direction = np.diff(actuals) > 0
        direction_accuracy = np.mean(pred_direction == actual_direction) * 100
    else:
        direction_accuracy = 0.0
    
    # Confidence interval coverage
    if confidence_intervals is not None and len(confidence_intervals) == len(actuals):
        coverage_count = sum(
            lower <= actual <= upper 
            for (lower, upper), actual in zip(confidence_intervals, actuals)
        )
        coverage_95 = (coverage_count / len(actuals)) * 100
    else:
        coverage_95 = 0.0
    
    return PredictionMetrics(
        mae=mae,
        rmse=rmse,
        mape=mape,
        direction_accuracy=direction_accuracy,
        coverage_95=coverage_95,
        mean_residual=mean_residual,
        std_residual=std_residual,
        total_predictions=len(predictions)
    )


def calculate_rolling_accuracy(
    predictions: pd.Series,
    actuals: pd.Series,
    window: int = 100
) -> pd.DataFrame:
    """
    Calculate rolling prediction accuracy over time.
    
    Args:
        predictions: Time series of predictions
        actuals: Time series of actual values
        window: Rolling window size
        
    Returns:
        DataFrame with rolling metrics
    """
    errors = actuals - predictions
    abs_errors = errors.abs()
    
    results = pd.DataFrame({
        'rolling_mae': abs_errors.rolling(window).mean(),
        'rolling_bias': errors.rolling(window).mean(),
        'rolling_std': errors.rolling(window).std(),
    })
    
    return results


def analyze_prediction_by_horizon(
    df: pd.DataFrame,
    prediction_col: str,
    actual_col: str,
    horizons: List[int] = [1, 5, 10, 30]
) -> pd.DataFrame:
    """
    Analyze how prediction accuracy degrades with prediction horizon.
    
    Args:
        df: DataFrame with predictions and actuals
        prediction_col: Name of prediction column
        actual_col: Name of actual price column  
        horizons: List of forward-looking periods to test
        
    Returns:
        DataFrame with metrics for each horizon
    """
    results = []
    
    for h in horizons:
        # Shift actuals forward by h periods
        future_actuals = df[actual_col].shift(-h)
        
        # Calculate metrics
        mask = ~(df[prediction_col].isna() | future_actuals.isna())
        preds = df.loc[mask, prediction_col].values
        acts = future_actuals[mask].values
        
        if len(preds) > 0:
            mae = np.mean(np.abs(acts - preds))
            rmse = np.sqrt(np.mean((acts - preds) ** 2))
        else:
            mae = rmse = np.nan
        
        results.append({
            'horizon': h,
            'mae': mae,
            'rmse': rmse,
            'samples': len(preds)
        })
    
    return pd.DataFrame(results)


def calculate_sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """
    Calculate Sharpe ratio for a return series.
    
    Args:
        returns: Series of returns
        risk_free_rate: Annual risk-free rate (default 0)
        
    Returns:
        Annualized Sharpe ratio
    """
    excess_returns = returns - risk_free_rate
    if excess_returns.std() == 0:
        return 0.0
    
    # Annualize (assuming returns are per-minute)
    # There are ~525,600 minutes per year, but markets aren't always open
    # For simplicity, use sqrt(525600) for scaling
    sharpe = excess_returns.mean() / excess_returns.std()
    sharpe_annualized = sharpe * np.sqrt(525600)
    
    return sharpe_annualized


def print_metrics_comparison(
    metrics_dict: Dict[str, PredictionMetrics],
    title: str = "Model Comparison"
):
    """
    Print a formatted comparison table of multiple models.
    
    Args:
        metrics_dict: Dictionary of {model_name: PredictionMetrics}
        title: Title for the comparison
    """
    print(f"\n{'='*70}")
    print(f"{title:^70}")
    print(f"{'='*70}\n")
    
    # Header
    print(f"{'Model':<20} {'MAE':>10} {'RMSE':>10} {'Dir%':>10} {'CI95%':>10}")
    print(f"{'-'*70}")
    
    # Each model
    for name, metrics in metrics_dict.items():
        print(f"{name:<20} "
              f"${metrics.mae:>9.4f} "
              f"${metrics.rmse:>9.4f} "
              f"{metrics.direction_accuracy:>9.1f}% "
              f"{metrics.coverage_95:>9.1f}%")
    
    print(f"{'='*70}\n")