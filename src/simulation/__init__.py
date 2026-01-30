"""Simulation framework for testing trading algorithms.

Provides market simulators and simulated API clients for backtesting
and strategy development without requiring live market data.
"""

from .market_simulator import (
    MarketSimulator,
    MeanRevertingSimulator,
    TrendingSimulator,
)
from .paired_simulator import MispricingConfig, PairedMarketSimulator
from .scenarios import (
    SCENARIOS,
    ScenarioConfig,
    SimulationResult,
    create_api_client,
    create_simulator,
    get_scenario,
    list_scenarios,
    mean_reverting,
    run_simulation,
    stable_market,
    trending_down,
    trending_up,
    volatile_market,
)
from .simulated_api_client import SimulatedAPIClient
from .paper_trading import (
    PaperFill,
    PaperOrder,
    PaperPosition,
    PaperTradingClient,
    calculate_fee,
)
from .spread_scenarios import (
    SPREAD_SCENARIOS,
    SpreadScenarioConfig,
    create_spread_simulator,
    get_spread_scenario,
    list_spread_scenarios,
)
from .nba_recorder import (
    GameRecordingFrame,
    GameRecordingMetadata,
    NBAGameRecorder,
    list_live_games,
)
from .nba_replay import (
    MockScoreFeed,
    NBAGameReplay,
    ReplayState,
)
from .nba_backtester import (
    BacktestMetrics,
    BacktestResult,
    NBAStrategyBacktester,
    SignalRecord,
    format_backtest_report,
    run_backtest,
)

__all__ = [
    # Simulators
    "MarketSimulator",
    "TrendingSimulator",
    "MeanRevertingSimulator",
    # Paired Market Simulator
    "PairedMarketSimulator",
    "MispricingConfig",
    # API Clients
    "SimulatedAPIClient",
    # Paper Trading
    "PaperTradingClient",
    "PaperOrder",
    "PaperFill",
    "PaperPosition",
    "calculate_fee",
    # Scenarios
    "ScenarioConfig",
    "SimulationResult",
    "SCENARIOS",
    "create_simulator",
    "create_api_client",
    "get_scenario",
    "list_scenarios",
    "run_simulation",
    # Spread Scenarios
    "SpreadScenarioConfig",
    "SPREAD_SCENARIOS",
    "create_spread_simulator",
    "get_spread_scenario",
    "list_spread_scenarios",
    # Convenience functions
    "stable_market",
    "volatile_market",
    "trending_up",
    "trending_down",
    "mean_reverting",
    # NBA Recording & Replay
    "NBAGameRecorder",
    "GameRecordingFrame",
    "GameRecordingMetadata",
    "list_live_games",
    "NBAGameReplay",
    "MockScoreFeed",
    "ReplayState",
    # NBA Backtesting
    "NBAStrategyBacktester",
    "BacktestResult",
    "BacktestMetrics",
    "SignalRecord",
    "format_backtest_report",
    "run_backtest",
]
