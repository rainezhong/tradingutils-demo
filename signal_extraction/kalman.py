import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from kalshi_utils.client_wrapper import *
from get_series import *
from kalshi_utils.plotter import KalshiLivePlotter

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from pykalman import KalmanFilter

class MarketKalmanFilter:
    def __init__(self, process_noise=1e-5, measurement_noise=1e-3):
        """
        Initialize a Local Level Kalman Filter (Random Walk).
        
        State Equation: x_t = x_{t-1} + process_noise
        Measure Equation: z_t = x_t + measurement_noise
        """
        self.kf = KalmanFilter(
            transition_matrices=[1],
            observation_matrices=[1],
            initial_state_mean=0.5,
            initial_state_covariance=1,
            observation_covariance=measurement_noise,
            transition_covariance=process_noise
        )
        self.current_state_mean = None
        self.current_state_cov = None

    def process_dataframe(self, df, team_name, window_minutes=None):
        """
        Prepares the dataframe by calculating Mid-Price for a specific team.
        Optionally slices data to the last 'window_minutes'.
        """
        if window_minutes is not None:
            last_time = df.index[-1]
            cutoff_time = last_time - pd.Timedelta(minutes=window_minutes)
            df = df[df.index >= cutoff_time].copy()

        bid_col = f"{team_name}_YesBid"
        ask_col = f"{team_name}_YesAsk"
        
        if bid_col not in df.columns or ask_col not in df.columns:
            raise ValueError(f"Columns for {team_name} not found in DataFrame.")

        analysis_df = df[[bid_col, ask_col]].copy()
        analysis_df = analysis_df.ffill().bfill()
        analysis_df['MidPrice'] = (analysis_df[bid_col] + analysis_df[ask_col]) / 2.0
        
        return analysis_df

    def run_filter(self, df, team_name, window_minutes=60):
        """
        Runs the Kalman Smoother on the historical data.
        
        Args:
            window_minutes: Only analyze the last N minutes of data. 
                            Set to None to use full history.
        """
        data = self.process_dataframe(df, team_name, window_minutes)
        
        if data.empty:
            print(f"Warning: No data found in the last {window_minutes} minutes.")
            return data

        measurements = data['MidPrice'].values
        state_means, state_covs = self.kf.filter(measurements)
        
        self.current_state_mean = state_means[-1]
        self.current_state_cov = state_covs[-1]
        
        data['KalmanPrice'] = state_means
        data['Residual'] = data['MidPrice'] - data['KalmanPrice']
        
        return data

    def predict_next(self, steps=1):
        """
        Predicts the price 'steps' ahead based on current state.
        For a Random Walk model, the mean prediction is constant, 
        but the uncertainty (covariance) grows.
        """
        if self.current_state_mean is None:
            return None, None
            
        pred_mean = self.current_state_mean
        pred_cov = self.current_state_cov + (self.kf.transition_covariance * steps)
        
        return pred_mean, pred_cov

    def plot_results(self, data, team_name):
        """Visualizes the Raw MidPrice vs Kalman Smoothed Price."""
        plt.figure(figsize=(12, 6))
        plt.plot(data.index, data['MidPrice'], 'k.', markersize=2, alpha=0.3, label='Raw Mid-Price')
        plt.plot(data.index, data['KalmanPrice'], 'b-', linewidth=2, label='Kalman Trend')
        plt.title(f"Kalman Filter Analysis: {team_name}")
        plt.ylabel("Price ($)")
        plt.xlabel("Time")
        plt.legend()
        plt.grid(True, alpha=0.3)
        plt.tight_layout()


if __name__ == "__main__":
    # 1. Setup Client and Data
    print("Initializing Kalshi client...")
    kalshi = KalshiWrapped()
    client = kalshi.GetClient()

    '''    # 2. Get Market Data
    print("Fetching live NHL markets...")
    games = kalshi.GetLiveNBAMarkets()
    pairs = kalshi.GetMarketPairs(games)
    
    if not pairs:
        print("No market pairs found!")
        sys.exit(1)
    
    pair_one = pairs[0]'''
    target_matchup = "torsac".upper() 
    series_ticker = "kxnbagame".upper()

    # 1. Fetch the series (returns a GetMarketResponse)
    response = kalshi.GetSeries(series_ticker, status='open')

    search = []

    # 2. Iterate over the .markets list inside the response
    for market in response.markets:
        # 3. Check if your target matchup is in this market's ticker
        if target_matchup in market.ticker:
            search.append(market)

    from kalshi_utils.plotter import KalshiLivePlotter
    pair_one = (search[0], search[1])
    print(f"Selected pair: {pair_one[0].ticker} vs {pair_one[1].ticker}")

    # 3. Fetch Historical Data for Analysis
    print("Fetching time series data...")
    df = get_time_series(client, pair_one)

    # 4. Find team name from columns
    bid_col = next((col for col in df.columns if col.endswith('_YesBid')), None)
    
    if not bid_col:
        print("Error: Could not find bid column in dataframe")
        print(f"Available columns: {df.columns.tolist()}")
        sys.exit(1)
    
    team_name = bid_col.replace('_YesBid', '')
    print(f"\n{'='*60}")
    print(f"Analyzing Market for: {team_name}")
    print(f"{'='*60}\n")

    # 5. Initialize and Run Kalman Filter
    kf_model = MarketKalmanFilter(process_noise=1e-5, measurement_noise=1e-3)
    analyzed_df = kf_model.run_filter(df, team_name, window_minutes=60)

    # 6. Display Results
    if not analyzed_df.empty:
        next_price, uncertainty = kf_model.predict_next()
        
        print(f"Analysis Window: Last {len(analyzed_df)} data points")
        print(f"Current Filtered Price: ${next_price[0]:.4f}")
        print(f"Prediction Uncertainty: {np.sqrt(uncertainty[0][0]):.4f}")
        print(f"Current Raw Mid-Price: ${analyzed_df['MidPrice'].iloc[-1]:.4f}")
        print(f"Latest Residual: ${analyzed_df['Residual'].iloc[-1]:.4f}")
        print(f"\n{'='*60}\n")
        
        # 7. Plot Historical Analysis
        print("Displaying historical Kalman filter analysis...")
        kf_model.plot_results(analyzed_df, team_name)
        
        # 8. Start Live Plotters
        print("\nStarting live market plotters...")
        print(f"Window 1: {pair_one[0].yes_sub_title}")
        print(f"Window 2: {pair_one[1].yes_sub_title}")
        print("\nNote: The live plotters use their own online Kalman filter.")
        print("Close the windows to exit.\n")
        
        # Plot both markets in the pair
        KalshiLivePlotter.plot_pair(client, pair_one)
        
    else:
        print("Error: Not enough recent data to run filter.")
        print("Try increasing the window_minutes parameter or check if markets are active.")