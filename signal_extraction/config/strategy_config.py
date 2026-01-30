"""
Trading strategy configurations.
Switch between different strategies easily.
"""

class StrategyConfig:
    """Configuration for trading strategies."""
    
    # =====================================
    # STRATEGY SELECTION
    # =====================================
    # Options: 'momentum', 'mean_reversion', 'hybrid'
    ACTIVE_STRATEGY = 'momentum'
    
    # =====================================
    # KALMAN FILTER SETTINGS
    # =====================================
    
    # Momentum Strategy (Recommended for trending markets)
    KALMAN_MOMENTUM = {
        'process_noise': 1e-6,        # Very sticky (slow to change)
        'measurement_noise': 1e-2,    # Don't trust price much
        'imbalance_weight': 0.1,      # Low weight (often zero)
        'score_weight': 0.9           # High weight on fundamentals
    }
    
    # Mean Reversion Strategy (For range-bound markets)
    KALMAN_MEAN_REVERSION = {
        'process_noise': 1e-5,
        'measurement_noise': 1e-3,
        'imbalance_weight': 0.3,
        'score_weight': 0.5
    }
    
    # Hybrid Strategy (Balanced)
    KALMAN_HYBRID = {
        'process_noise': 5e-6,
        'measurement_noise': 5e-3,
        'imbalance_weight': 0.2,
        'score_weight': 0.7
    }
    
    # =====================================
    # MOMENTUM STRATEGY PARAMETERS
    # =====================================
    MOMENTUM_PARAMS = {
        'momentum_threshold': 0.003,      # 0.3% velocity to trigger
        'trend_confirmation_bars': 3,     # Confirm over 3 updates
        'score_weight': 0.7,              # 70% weight on scores
        'max_holding_time': 600.0,        # 10 minutes max
        'trailing_stop_pct': 0.08         # 8% trailing stop
    }
    
    # =====================================
    # MEAN REVERSION STRATEGY PARAMETERS
    # =====================================
    MEAN_REVERSION_PARAMS = {
        'entry_threshold': 0.01,          # $0.01 mispricing
        'exit_threshold': 0.005,          # $0.005 to exit
        'min_signal_strength': 0.2,       # 20% confidence
        'min_imbalance': 0.0              # Disabled (often zero)
    }
    
    # =====================================
    # RISK MANAGEMENT
    # =====================================
    RISK_PARAMS = {
        'max_position_size': 0.05,        # 5% per position
        'max_total_exposure': 0.20,       # 20% total
        'max_loss_per_trade': 0.10,       # 10% stop loss
        'max_daily_loss': 0.15            # 15% daily limit
    }
    
    # =====================================
    # MARKET FILTERING (When to trade)
    # =====================================
    MARKET_FILTER = {
        'min_game_completion': 0.25,      # Don't trade Q1
        'max_game_completion': 0.95,      # Don't trade final minutes
        'min_spread': 0.0,                # Min spread (disabled)
        'max_spread': 0.05,               # Max 5% spread
        'min_volume': 1000.0              # Min $1000 volume
    }
    
    # =====================================
    # PRESETS FOR DIFFERENT MARKET CONDITIONS
    # =====================================
    
    @classmethod
    def get_config(cls, preset='aggressive'):
        """
        Get configuration preset.
        
        Args:
            preset: 'conservative', 'moderate', 'aggressive'
        """
        if preset == 'conservative':
            return {
                'strategy': 'momentum',
                'kalman': cls.KALMAN_MOMENTUM,
                'momentum': {
                    'momentum_threshold': 0.005,      # Higher threshold
                    'trend_confirmation_bars': 5,     # More confirmation
                    'score_weight': 0.8,
                    'max_holding_time': 300.0,        # 5 min max
                    'trailing_stop_pct': 0.05         # Tight 5% stop
                },
                'risk': {
                    'max_position_size': 0.03,        # 3% only
                    'max_total_exposure': 0.10,
                    'max_loss_per_trade': 0.08,
                    'max_daily_loss': 0.12
                }
            }
        
        elif preset == 'moderate':
            return {
                'strategy': 'momentum',
                'kalman': cls.KALMAN_MOMENTUM,
                'momentum': cls.MOMENTUM_PARAMS,
                'risk': cls.RISK_PARAMS
            }
        
        elif preset == 'aggressive':
            return {
                'strategy': 'momentum',
                'kalman': {
                    'process_noise': 5e-6,            # Slightly more responsive
                    'measurement_noise': 5e-3,
                    'imbalance_weight': 0.2,
                    'score_weight': 0.8
                },
                'momentum': {
                    'momentum_threshold': 0.003,      # 0.3% velocity (catch more moves)
                    'trend_confirmation_bars': 2,     # 2 bars (enter earlier)
                    'score_weight': 0.5,
                    'max_holding_time': 600.0,        # 10 min max
                    'trailing_stop_pct': 0.08         # 8% stop (room for noise)
                },
                'risk': {
                    'max_position_size': 0.08,        # 8% per position
                    'max_total_exposure': 1,
                    'max_loss_per_trade': 0.06,       # 6% stop (more room)
                    'max_daily_loss': 1
                }
            }
        
        else:
            raise ValueError(f"Unknown preset: {preset}")
    
    # =====================================
    # HELPER METHODS
    # =====================================
    
    @classmethod
    def print_config(cls, config=None):
        """Print current configuration."""
        if config is None:
            config = cls.get_config('moderate')
        
        print("\n" + "="*70)
        print("STRATEGY CONFIGURATION")
        print("="*70)
        print(f"Strategy: {config['strategy'].upper()}")
        print("\nKalman Filter:")
        for key, val in config['kalman'].items():
            print(f"  {key}: {val}")
        
        if config['strategy'] == 'momentum':
            print("\nMomentum Parameters:")
            for key, val in config['momentum'].items():
                print(f"  {key}: {val}")
        
        print("\nRisk Management:")
        for key, val in config['risk'].items():
            print(f"  {key}: {val}")
        print("="*70 + "\n")


# Quick access to presets
CONSERVATIVE = StrategyConfig.get_config('conservative')
MODERATE = StrategyConfig.get_config('moderate')
AGGRESSIVE = StrategyConfig.get_config('aggressive')