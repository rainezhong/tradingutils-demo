"""Unified realism configuration for backtest fill simulation.

Provides comprehensive models for realistic backtest execution:
- Repricing lag: Delay between signal generation and actual order placement
- Queue priority: Probability of fills based on queue position and depth
- Network latency: Round-trip time to exchange
- Orderbook staleness: Penalty when orderbook is stale
- Market impact: Price worsening from large orders

Each model can be configured independently or use preset profiles.

Usage:
    # Use preset
    config = BacktestRealismConfig.realistic()

    # Or customize
    config = BacktestRealismConfig(
        repricing_lag_sec=3.0,
        queue_priority_factor=5.0,
        network_latency_ms=250.0,
        staleness_penalty_multiplier=1.5,
        market_impact_coefficient=6.0,
    )
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RepricingLagConfig:
    """Configuration for Kalshi repricing lag model.

    Market makers reprice their quotes based on external signals (CEX price moves,
    competitor quotes, etc.). This creates a window where stale quotes persist
    before being updated.

    Attributes:
        enabled: Enable repricing lag simulation
        lag_sec: Average time for market makers to reprice after signal
        std_sec: Standard deviation for lag (adds randomness)
        min_lag_sec: Minimum lag (even fastest MM has latency)
        max_lag_sec: Maximum lag (caps outliers)
    """
    enabled: bool = True
    lag_sec: float = 5.0
    std_sec: float = 1.0
    min_lag_sec: float = 1.0
    max_lag_sec: float = 15.0


@dataclass
class QueuePriorityConfig:
    """Configuration for queue priority fill model.

    When your order rests at a price level, you compete with other orders.
    Fill probability depends on your queue position and total depth.

    Attributes:
        enabled: Enable queue priority simulation
        queue_factor: Multiplier for existing depth ahead of you
            Higher = more competition = lower fill rate
            Example: factor=3.0 means 3x the visible depth is ahead of you
        instant_fill_threshold_cents: If price moves through your level by
            this much, assume instant fill (aggressive taker hit your level)
    """
    enabled: bool = True
    queue_factor: float = 3.0
    instant_fill_threshold_cents: int = 2


@dataclass
class NetworkLatencyConfig:
    """Configuration for network latency model.

    Round-trip time from signal → order submission → exchange processing → fill confirmation.
    During this delay, prices may move adversely.

    Attributes:
        enabled: Enable latency simulation
        latency_ms: Average round-trip latency
        std_ms: Standard deviation for latency
        min_latency_ms: Minimum latency (local exchange minimum)
        max_latency_ms: Maximum latency (network congestion)
        adverse_selection_factor: Fraction of spot move that is adverse (0-1).
            0.0 = no adverse selection, 1.0 = full adverse selection.
            Represents how much of the price movement during latency works against you.
        mode: Latency sampling mode:
            - "sampled": Sample from normal distribution (latency_ms, std_ms)
            - "fixed": Always use latency_ms
            - "optimistic": Use latency_ms - std_ms (faster)
            - "pessimistic": Use latency_ms + std_ms (slower)
    """
    enabled: bool = True
    latency_ms: float = 200.0
    std_ms: float = 50.0
    min_latency_ms: float = 50.0
    max_latency_ms: float = 1000.0
    adverse_selection_factor: float = 0.5
    mode: str = "sampled"


@dataclass
class OrderbookStalenessConfig:
    """Configuration for orderbook staleness penalty.

    Orderbook snapshots age between updates. When stale, the true spread
    may have widened based on spot price velocity during the staleness period.

    Attributes:
        enabled: Enable staleness penalty
        max_staleness_ms: Maximum acceptable staleness in milliseconds.
            Reject fills if snapshot is older than this.
        velocity_penalty_factor: Multiplier for velocity-based penalty.
            penalty = spot_velocity ($/s) * staleness_sec * factor
            Higher values = more conservative (wider spread adjustment)
    """
    enabled: bool = True
    max_staleness_ms: float = 200.0  # 200ms maximum staleness
    velocity_penalty_factor: float = 1.0  # 1:1 scaling of velocity * time


@dataclass
class MarketImpactConfig:
    """Configuration for market impact model.

    Large orders move the market. Your fill price is worse than the quoted price
    based on order size relative to available depth.

    Attributes:
        enabled: Enable market impact simulation
        impact_coefficient: Price worsening factor
            impact_cents = coefficient * (order_size / available_depth)
            Higher coefficient = more slippage on large orders
        min_depth_ratio: Minimum depth ratio to allow fills
            If order_size / depth > this ratio, fill is rejected
    """
    enabled: bool = True
    impact_coefficient: float = 5.0
    min_depth_ratio: float = 2.0


@dataclass
class BacktestRealismConfig:
    """Unified configuration for all backtest realism models.

    Combines multiple execution realism models into a single configuration.
    Use preset factory methods for common profiles:
    - optimistic(): All models disabled (instant fills, no slippage)
    - realistic(): Balanced defaults based on live trading observations
    - pessimistic(): Conservative assumptions (harder fills, more slippage)

    Attributes:
        repricing_lag: Kalshi market maker repricing delay
        queue_priority: Queue position and depth-based fill probability
        network_latency: Round-trip network and exchange processing delay
        orderbook_staleness: Penalty for aged orderbook snapshots
        market_impact: Price worsening from order size vs depth
    """
    repricing_lag: RepricingLagConfig = field(default_factory=RepricingLagConfig)
    queue_priority: QueuePriorityConfig = field(default_factory=QueuePriorityConfig)
    network_latency: NetworkLatencyConfig = field(default_factory=NetworkLatencyConfig)
    orderbook_staleness: OrderbookStalenessConfig = field(default_factory=OrderbookStalenessConfig)
    market_impact: MarketImpactConfig = field(default_factory=MarketImpactConfig)

    @classmethod
    def optimistic(cls) -> "BacktestRealismConfig":
        """Optimistic profile: all models disabled.

        Use this for:
        - Upper bound P&L estimates
        - Strategy logic validation (remove execution noise)
        - Rapid iteration during development

        Characteristics:
        - Instant fills at quoted prices
        - No queue competition
        - No network delays
        - Perfect orderbook data
        - No market impact
        """
        return cls(
            repricing_lag=RepricingLagConfig(enabled=False),
            queue_priority=QueuePriorityConfig(enabled=False),
            network_latency=NetworkLatencyConfig(enabled=False),
            orderbook_staleness=OrderbookStalenessConfig(enabled=False),
            market_impact=MarketImpactConfig(enabled=False),
        )

    @classmethod
    def realistic(cls) -> "BacktestRealismConfig":
        """Realistic profile: balanced assumptions from live trading.

        Use this for:
        - Production P&L forecasting
        - Parameter optimization
        - Risk analysis

        Characteristics (based on live Kalshi crypto markets):
        - Repricing lag: 5s average (MMs react to CEX moves)
        - Queue factor: 3x (moderate competition at each level)
        - Network latency: 200ms average (typical API round-trip)
        - Staleness penalty: 1.0x (linear penalty up to 5s)
        - Market impact: 5.0 coefficient (observable slippage pattern)

        Calibration notes:
        - Repricing lag 5s: observed from Binance→Kalshi update delays
        - Queue factor 3x: backfitted from historical fill rates
        - Latency 200ms: p50 from production logs
        - Impact 5.0: fit to executed price vs quoted price distribution
        """
        return cls(
            repricing_lag=RepricingLagConfig(
                enabled=True,
                lag_sec=5.0,
                std_sec=1.0,
                min_lag_sec=1.0,
                max_lag_sec=15.0,
            ),
            queue_priority=QueuePriorityConfig(
                enabled=True,
                queue_factor=3.0,
                instant_fill_threshold_cents=2,
            ),
            network_latency=NetworkLatencyConfig(
                enabled=True,
                latency_ms=200.0,
                std_ms=50.0,
                min_latency_ms=50.0,
                max_latency_ms=1000.0,
                adverse_selection_factor=0.5,
                mode="sampled",
            ),
            orderbook_staleness=OrderbookStalenessConfig(
                enabled=True,
                max_staleness_ms=200.0,
                velocity_penalty_factor=1.0,
            ),
            market_impact=MarketImpactConfig(
                enabled=True,
                impact_coefficient=5.0,
                min_depth_ratio=2.0,
            ),
        )

    @classmethod
    def pessimistic(cls) -> "BacktestRealismConfig":
        """Pessimistic profile: conservative execution assumptions.

        Use this for:
        - Risk management (worst-case scenarios)
        - Capital allocation (downside protection)
        - Validating strategy robustness

        Characteristics:
        - Repricing lag: 3s average (faster MM response)
        - Queue factor: 5x (heavy competition)
        - Network latency: 300ms average (slower infrastructure)
        - Staleness penalty: 2.0x (aggressive penalty)
        - Market impact: 8.0 coefficient (high slippage)

        This profile assumes:
        - More sophisticated market makers (faster repricing)
        - Deeper hidden liquidity (higher queue factor)
        - Worse infrastructure (higher latency)
        - Lower quality data (more staleness)
        - Thinner true liquidity (more impact)
        """
        return cls(
            repricing_lag=RepricingLagConfig(
                enabled=True,
                lag_sec=3.0,
                std_sec=0.5,
                min_lag_sec=0.5,
                max_lag_sec=10.0,
            ),
            queue_priority=QueuePriorityConfig(
                enabled=True,
                queue_factor=5.0,
                instant_fill_threshold_cents=3,
            ),
            network_latency=NetworkLatencyConfig(
                enabled=True,
                latency_ms=300.0,
                std_ms=100.0,
                min_latency_ms=100.0,
                max_latency_ms=2000.0,
                adverse_selection_factor=0.7,
                mode="sampled",
            ),
            orderbook_staleness=OrderbookStalenessConfig(
                enabled=True,
                max_staleness_ms=150.0,  # More strict for pessimistic
                velocity_penalty_factor=1.5,  # Higher penalty factor
            ),
            market_impact=MarketImpactConfig(
                enabled=True,
                impact_coefficient=8.0,
                min_depth_ratio=1.5,
            ),
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "repricing_lag": {
                "enabled": self.repricing_lag.enabled,
                "lag_sec": self.repricing_lag.lag_sec,
                "std_sec": self.repricing_lag.std_sec,
                "min_lag_sec": self.repricing_lag.min_lag_sec,
                "max_lag_sec": self.repricing_lag.max_lag_sec,
            },
            "queue_priority": {
                "enabled": self.queue_priority.enabled,
                "queue_factor": self.queue_priority.queue_factor,
                "instant_fill_threshold_cents": self.queue_priority.instant_fill_threshold_cents,
            },
            "network_latency": {
                "enabled": self.network_latency.enabled,
                "latency_ms": self.network_latency.latency_ms,
                "std_ms": self.network_latency.std_ms,
                "min_latency_ms": self.network_latency.min_latency_ms,
                "max_latency_ms": self.network_latency.max_latency_ms,
                "adverse_selection_factor": self.network_latency.adverse_selection_factor,
                "mode": self.network_latency.mode,
            },
            "orderbook_staleness": {
                "enabled": self.orderbook_staleness.enabled,
                "max_staleness_ms": self.orderbook_staleness.max_staleness_ms,
                "velocity_penalty_factor": self.orderbook_staleness.velocity_penalty_factor,
            },
            "market_impact": {
                "enabled": self.market_impact.enabled,
                "impact_coefficient": self.market_impact.impact_coefficient,
                "min_depth_ratio": self.market_impact.min_depth_ratio,
            },
        }
