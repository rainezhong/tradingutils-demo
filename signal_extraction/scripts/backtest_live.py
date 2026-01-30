"""
Backtest Kalman filter on live game data.
This script runs a walk-forward backtest to evaluate prediction accuracy.
"""

import sys
import os
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np

# Add parent directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from get_series import get_time_series
from models.kalman_filter import prepare_market_data
from backtesting.backtest_engine import WalkForwardBacktest, compare_parameter_settings
from backtesting.metrics import analyze_prediction_by_horizon, calculate_rolling_accuracy

double_parent_dir = os.path.dirname(parent_dir)
sys.path.append(double_parent_dir)

from kalshi_utils.client_wrapper import KalshiWrapped


def plot_backtest_results(result, title="Kalman Filter Backtest"):
    """
    Create comprehensive visualization of backtest results.
    """
    df = result.predictions
    
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.suptitle(title, fontsize=14, fontweight='bold')
    
    # Plot 1: Prices and Predictions
    ax1 = axes[0]
    ax1.plot(df.index, df['current_price'], 'k.', markersize=1, alpha=0.3, label='Observed Price')
    ax1.plot(df.index, df['predicted_price'], 'b-', linewidth=1.5, label='Predicted Price', alpha=0.8)
    ax1.plot(df.index, df['actual_future_price'], 'r.', markersize=1, alpha=0.4, label='Actual Future Price')
    
    # Add confidence bands
    ax1.fill_between(
        df.index, 
        df['ci_lower'], 
        df['ci_upper'],
        alpha=0.2, 
        color='blue',
        label='95% Confidence Interval'
    )
    
    ax1.set_ylabel('Price ($)')
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.set_title('Predictions vs Actuals')
    
    # Plot 2: Prediction Errors
    ax2 = axes[1]
    ax2.plot(df.index, df['prediction_error'], 'g-', linewidth=0.8, alpha=0.6)
    ax2.axhline(y=0, color='black', linestyle='--', linewidth=1)
    ax2.fill_between(df.index, 0, df['prediction_error'], alpha=0.3, color='green')
    ax2.set_ylabel('Prediction Error ($)')
    ax2.set_title('Prediction Error Over Time')
    ax2.grid(True, alpha=0.3)
    
    # Add rolling MAE
    rolling_mae = df['prediction_error'].abs().rolling(window=100).mean()
    ax2_twin = ax2.twinx()
    ax2_twin.plot(df.index, rolling_mae, 'r-', linewidth=1.5, alpha=0.7, label='Rolling MAE (100)')
    ax2_twin.set_ylabel('Rolling MAE ($)', color='r')
    ax2_twin.tick_params(axis='y', labelcolor='r')
    ax2_twin.legend(loc='upper right', fontsize=9)
    
    # Plot 3: Uncertainty
    ax3 = axes[2]
    ax3.plot(df.index, df['prediction_std'], 'purple', linewidth=1)
    ax3.fill_between(df.index, 0, df['prediction_std'], alpha=0.3, color='purple')
    ax3.set_ylabel('Prediction Std Dev ($)')
    ax3.set_xlabel('Time')
    ax3.set_title('Model Uncertainty Over Time')
    ax3.grid(True, alpha=0.3)
    
    plt.tight_layout()
    return fig


def plot_error_distribution(result):
    """Plot distribution of prediction errors."""
    df = result.predictions
    errors = df['prediction_error']
    
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    
    # Histogram
    ax1 = axes[0]
    ax1.hist(errors, bins=50, alpha=0.7, edgecolor='black')
    ax1.axvline(x=0, color='red', linestyle='--', linewidth=2)
    ax1.axvline(x=errors.mean(), color='blue', linestyle='--', linewidth=2, 
                label=f'Mean: ${errors.mean():.4f}')
    ax1.set_xlabel('Prediction Error ($)')
    ax1.set_ylabel('Frequency')
    ax1.set_title('Distribution of Prediction Errors')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # Q-Q plot
    ax2 = axes[1]
    from scipy import stats
    stats.probplot(errors, dist="norm", plot=ax2)
    ax2.set_title('Q-Q Plot (Normal Distribution)')
    ax2.grid(True, alpha=0.3)
    
    plt.tight_layout()
    return fig


def main():
    """Main backtesting workflow."""
    
    print("="*70)
    print("KALMAN FILTER LIVE GAME BACKTEST")
    print("="*70)
    
    # 1. Setup
    print("\n[1/6] Initializing Kalshi client...")
    kalshi = KalshiWrapped()
    client = kalshi.GetClient()
    
    # 2. Get market data
    print("[2/6] Fetching live NHL markets...")
    games = kalshi.GetLiveNHLMarkets()
    pairs = kalshi.GetMarketPairs(games)
    
    if not pairs:
        print("ERROR: No market pairs found!")
        return
    
    pair = pairs[0]
    print(f"Selected: {pair[0].ticker} vs {pair[1].ticker}")
    
    # 3. Fetch time series
    print("[3/6] Fetching historical data...")
    df = get_time_series(client, pair)
    
    # Get team name
    bid_col = next((col for col in df.columns if col.endswith('_YesBid')), None)
    if not bid_col:
        print("ERROR: Could not find bid column!")
        return
    
    team_name = bid_col.replace('_YesBid', '')
    print(f"Team: {team_name}")
    print(f"Data points: {len(df)}")
    print(f"Time range: {df.index[0]} to {df.index[-1]}")
    
    # 4. Prepare data
    print("[4/6] Preparing mid-price series...")
    mid_prices = prepare_market_data(df, team_name)
    print(f"Mid-price range: ${mid_prices.min():.4f} to ${mid_prices.max():.4f}")
    
    # 5. Run backtest
    print("\n[5/6] Running walk-forward backtest...")
    print("-" * 70)
    
    backtester = WalkForwardBacktest(
        process_noise=1e-5,      # Q: How fast true price changes
        measurement_noise=1e-3,  # R: How noisy observations are
        warmup_periods=10,       # Initial learning period
        prediction_horizon=1     # Predict 1 step ahead
    )
    
    result = backtester.run(mid_prices, verbose=True)
    
    # 6. Display results
    print("\n[6/6] Results:")
    print(result.summary())
    
    # Additional analysis
    print("\n" + "="*70)
    print("ADDITIONAL ANALYSIS")
    print("="*70)
    
    # Rolling accuracy
    print("\nCalculating rolling metrics...")
    rolling = calculate_rolling_accuracy(
        result.predictions['predicted_price'],
        result.predictions['actual_future_price'],
        window=100
    )
    
    print(f"\nLast 100 predictions:")
    print(f"  MAE: ${rolling['rolling_mae'].iloc[-1]:.4f}")
    print(f"  Bias: ${rolling['rolling_bias'].iloc[-1]:.4f}")
    print(f"  Std: ${rolling['rolling_std'].iloc[-1]:.4f}")
    
    # Horizon analysis
    print("\nTesting different prediction horizons...")
    horizon_results = analyze_prediction_by_horizon(
        result.predictions,
        prediction_col='predicted_price',
        actual_col='current_price',
        horizons=[1, 5, 10, 30, 60]
    )
    print("\nPrediction accuracy by horizon:")
    print(horizon_results.to_string(index=False))
    
    # 7. Visualizations
    print("\n[7/7] Generating visualizations...")
    
    fig1 = plot_backtest_results(result, title=f"Backtest: {team_name}")
    fig2 = plot_error_distribution(result)
    
    # Save results
    output_dir = os.path.join(parent_dir, 'backtest_results')
    os.makedirs(output_dir, exist_ok=True)
    
    csv_path = os.path.join(output_dir, f'backtest_{team_name}.csv')
    result.predictions.to_csv(csv_path)
    print(f"\nResults saved to: {csv_path}")
    
    plt.show()
    
    print("\n" + "="*70)
    print("BACKTEST COMPLETE")
    print("="*70)


def test_parameter_grid():
    """
    Optional: Test different parameter combinations to find optimal settings.
    """
    print("\n" + "="*70)
    print("PARAMETER OPTIMIZATION")
    print("="*70)
    
    kalshi = KalshiWrapped()
    client = kalshi.GetClient()
    
    games = kalshi.GetLiveNHLMarkets()
    pairs = kalshi.GetMarketPairs(games)
    pair = pairs[0]
    
    df = get_time_series(client, pair)
    bid_col = next((col for col in df.columns if col.endswith('_YesBid')), None)
    team_name = bid_col.replace('_YesBid', '')
    
    mid_prices = prepare_market_data(df, team_name)
    
    # Define parameter grid
    param_grid = [
        (1e-6, 1e-3),   # Very smooth
        (1e-5, 1e-3),   # Default
        (1e-4, 1e-3),   # More responsive
        (1e-5, 1e-2),   # Trust observations less
        (1e-5, 1e-4),   # Trust observations more
    ]
    
    results_df = compare_parameter_settings(
        mid_prices,
        param_grid,
        warmup=10,
        horizon=1
    )
    
    print("\nBest parameters by MAE:")
    print(results_df.head(3))
    
    return results_df


if __name__ == "__main__":
    # Run standard backtest
    main()
    
    # Uncomment to run parameter optimization
    # test_parameter_grid()