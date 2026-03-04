"""Strategy module.

Provides strategy interface and implementations.
"""

try:
    from .i_strategy import I_Strategy
    from .strategy_types import (
        StrategyConfig,
        ScalpConfig,
        MarketMakingConfig,
        Position,
        Quote,
        Signal,
        SignalStrength,
        CONFIG_DIR,
    )
    from .scalp_strategy import ScalpStrategy
    from .market_making_strategy import MarketMakingStrategy
    from .correlation_arb_strategy import CorrelationArbStrategy, CorrelationArbConfig
    from .depth_strategy_base import DepthStrategyBase
    from .spread_capture_strategy import SpreadCaptureStrategy
    from .edge_capture_strategy import EdgeCaptureStrategy
    from .depth_scalper_strategy import DepthScalper
    from .liquidity_provider_strategy import LiquidityProvider
    from .late_game_blowout_strategy import (
        LateGameBlowoutStrategy,
        BlowoutStrategyConfig,
        LateGameBlowoutConfig,
        BlowoutSide,
        BlowoutSignal,
    )
    from .sim_clock import SimulatedClock, sim_sleep, make_sim_wait_for_event
    from .base import TradingStrategy
    from .nba_mispricing_strategy import NBAMispricingStrategy
    from .nba_points_arb_strategy import NbaPointsArbStrategy
    from .tied_game_spread_strategy import TiedGameSpreadStrategy
    from .total_points_strategy import TotalPointsStrategy
except ImportError:
    pass

__all__ = [
    # Interface + types
    "I_Strategy",
    "StrategyConfig",
    "ScalpConfig",
    "MarketMakingConfig",
    "CorrelationArbConfig",
    "Position",
    "Quote",
    "Signal",
    "SignalStrength",
    "CONFIG_DIR",
    # MrClean strategies
    "ScalpStrategy",
    "MarketMakingStrategy",
    "CorrelationArbStrategy",
    # Depth-based strategies
    "DepthStrategyBase",
    "SpreadCaptureStrategy",
    "EdgeCaptureStrategy",
    "DepthScalper",
    "LiquidityProvider",
    # Late game + blowout
    "LateGameBlowoutStrategy",
    "BlowoutStrategyConfig",
    "LateGameBlowoutConfig",
    "BlowoutSide",
    "BlowoutSignal",
    # Simulation clock
    "SimulatedClock",
    "sim_sleep",
    "make_sim_wait_for_event",
    # Base strategy
    "TradingStrategy",
    # NBA strategies
    "NBAMispricingStrategy",
    "NbaPointsArbStrategy",
    "TiedGameSpreadStrategy",
    "TotalPointsStrategy",
]
