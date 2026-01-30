"""Configuration for the arbitrage system."""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

import yaml


@dataclass
class ArbitrageConfig:
    """Configuration for the arbitrage system.

    Attributes:
        min_edge_cents: Minimum net edge in cents to execute (default 5).
            With 7% Kalshi taker fees on a $0.50 contract, you lose ~3.5 cents
            per leg. Need at least 5-7 cents edge to be profitable after
            round-trip fees.
        min_edge_cents_conservative: Higher threshold for high-fee scenarios (default 7)
        min_roi_pct: Minimum ROI percentage to execute (default 0.03 = 3%)
        prefer_maker_orders: Calculate assuming maker fees for more realistic
            profitability estimates (default True). Maker fees are significantly
            lower: Kalshi 1.75% vs 7% taker, Polymarket 0% vs 2% taker.
        min_liquidity_usd: Minimum available liquidity in USD (default 500)
        max_position_per_market: Maximum contracts per market (default 100)
        max_concurrent_spreads: Maximum concurrent spread executions (default 3)
        scan_interval_seconds: How often to scan for opportunities (default 1.0)
        reconciliation_interval_seconds: How often to reconcile positions (default 60)
        paper_mode: Whether to run in paper trading mode (default True)
        max_daily_loss: Maximum daily loss before circuit breaker trips (default 500)
        max_error_rate: Maximum error rate before circuit breaker trips (default 0.10)
        max_api_latency_seconds: Maximum API latency before alert (default 2.0)
        min_fill_rate: Minimum fill rate before circuit breaker trips (default 0.70)
        kalshi_fee_rate: Kalshi taker fee rate on profit (default 0.07)
        kalshi_maker_fee_rate: Kalshi maker fee rate on profit (default 0.0175)
        polymarket_taker_fee: Polymarket taker fee rate (default 0.02)
        polymarket_maker_fee: Polymarket maker fee rate (default 0.0)
        polymarket_gas_estimate: Estimated gas cost per trade in USD (default 0.05)
    """

    # Detection thresholds - increased for fee coverage
    min_edge_cents: float = 5.0  # Increased from 2.0 to cover fees
    min_edge_cents_conservative: float = 7.0  # For high-fee scenarios
    min_roi_pct: float = 0.03  # Increased from 0.02 for safety margin
    prefer_maker_orders: bool = True  # Calculate assuming maker fees
    min_liquidity_usd: float = 500.0
    max_quote_age_ms: float = 2000.0

    # Fee safety margin - inflate expected fees by this factor to account for variance
    # A 15% margin means if we calculate $1.00 in fees, we assume $1.15 for filtering
    fee_safety_margin: float = 0.15

    # Use conservative (taker) fees for profitability filtering, even if prefer_maker_orders is True
    # This ensures we only take trades profitable even in worst-case fee scenario
    use_conservative_fees_for_filtering: bool = True

    # Partial fill prevention - limit order size to this fraction of available depth
    # 0.60 means we only try to fill 60% of visible liquidity to reduce partial fills
    max_depth_usage_pct: float = 0.60

    # Use fill-or-kill orders where supported to avoid partial fills
    use_fill_or_kill: bool = True

    # Position limits
    max_position_per_market: int = 100
    max_concurrent_spreads: int = 3

    # Timing
    scan_interval_seconds: float = 1.0
    reconciliation_interval_seconds: float = 60.0

    # Operation mode
    paper_mode: bool = True

    # Circuit breaker settings
    max_daily_loss: float = 500.0
    max_error_rate: float = 0.10
    max_api_latency_seconds: float = 2.0
    min_fill_rate: float = 0.70

    # Fee configuration
    kalshi_fee_rate: float = 0.07  # Taker fee
    kalshi_maker_fee_rate: float = 0.0175  # Maker fee (1.75%)
    polymarket_taker_fee: float = 0.02
    polymarket_maker_fee: float = 0.0  # Maker fee is 0%
    polymarket_gas_estimate: float = 0.05

    # Metrics
    metrics_port: int = 9090

    def __post_init__(self) -> None:
        """Validate configuration."""
        self.validate()

    def validate(self) -> None:
        """Validate configuration values."""
        errors = []

        if self.min_edge_cents <= 0:
            errors.append(f"min_edge_cents must be positive, got {self.min_edge_cents}")

        if not 0 < self.min_roi_pct < 1:
            errors.append(f"min_roi_pct must be between 0 and 1, got {self.min_roi_pct}")

        if self.min_liquidity_usd <= 0:
            errors.append(f"min_liquidity_usd must be positive, got {self.min_liquidity_usd}")

        if self.max_position_per_market <= 0:
            errors.append(f"max_position_per_market must be positive, got {self.max_position_per_market}")

        if self.max_concurrent_spreads <= 0:
            errors.append(f"max_concurrent_spreads must be positive, got {self.max_concurrent_spreads}")

        if self.scan_interval_seconds <= 0:
            errors.append(f"scan_interval_seconds must be positive, got {self.scan_interval_seconds}")

        if self.max_daily_loss <= 0:
            errors.append(f"max_daily_loss must be positive, got {self.max_daily_loss}")

        if not 0 < self.max_error_rate < 1:
            errors.append(f"max_error_rate must be between 0 and 1, got {self.max_error_rate}")

        if not 0 < self.min_fill_rate <= 1:
            errors.append(f"min_fill_rate must be between 0 and 1, got {self.min_fill_rate}")

        if errors:
            raise ValueError("Invalid ArbitrageConfig: " + "; ".join(errors))

    @classmethod
    def from_yaml(cls, path: str) -> "ArbitrageConfig":
        """Load configuration from YAML file.

        Args:
            path: Path to YAML configuration file

        Returns:
            ArbitrageConfig instance
        """
        config_path = Path(path)
        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        return cls.from_dict(data.get("arbitrage", data))

    @classmethod
    def from_dict(cls, data: Dict) -> "ArbitrageConfig":
        """Create config from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            ArbitrageConfig instance
        """
        return cls(
            min_edge_cents=float(data.get("min_edge_cents", 5.0)),
            min_edge_cents_conservative=float(data.get("min_edge_cents_conservative", 7.0)),
            min_roi_pct=float(data.get("min_roi_pct", 0.03)),
            prefer_maker_orders=bool(data.get("prefer_maker_orders", True)),
            min_liquidity_usd=float(data.get("min_liquidity_usd", 500.0)),
            max_quote_age_ms=float(data.get("max_quote_age_ms", 2000.0)),
            fee_safety_margin=float(data.get("fee_safety_margin", 0.15)),
            use_conservative_fees_for_filtering=bool(data.get("use_conservative_fees_for_filtering", True)),
            max_depth_usage_pct=float(data.get("max_depth_usage_pct", 0.60)),
            use_fill_or_kill=bool(data.get("use_fill_or_kill", True)),
            max_position_per_market=int(data.get("max_position_per_market", 100)),
            max_concurrent_spreads=int(data.get("max_concurrent_spreads", 3)),
            scan_interval_seconds=float(data.get("scan_interval_seconds", 1.0)),
            reconciliation_interval_seconds=float(data.get("reconciliation_interval_seconds", 60.0)),
            paper_mode=bool(data.get("paper_mode", True)),
            max_daily_loss=float(data.get("max_daily_loss", 500.0)),
            max_error_rate=float(data.get("max_error_rate", 0.10)),
            max_api_latency_seconds=float(data.get("max_api_latency_seconds", 2.0)),
            min_fill_rate=float(data.get("min_fill_rate", 0.70)),
            kalshi_fee_rate=float(data.get("kalshi_fee_rate", 0.07)),
            kalshi_maker_fee_rate=float(data.get("kalshi_maker_fee_rate", 0.0175)),
            polymarket_taker_fee=float(data.get("polymarket_taker_fee", 0.02)),
            polymarket_maker_fee=float(data.get("polymarket_maker_fee", 0.0)),
            polymarket_gas_estimate=float(data.get("polymarket_gas_estimate", 0.05)),
            metrics_port=int(data.get("metrics_port", 9090)),
        )

    def to_dict(self) -> Dict:
        """Convert config to dictionary.

        Returns:
            Configuration as dictionary
        """
        return {
            "min_edge_cents": self.min_edge_cents,
            "min_edge_cents_conservative": self.min_edge_cents_conservative,
            "min_roi_pct": self.min_roi_pct,
            "prefer_maker_orders": self.prefer_maker_orders,
            "min_liquidity_usd": self.min_liquidity_usd,
            "max_quote_age_ms": self.max_quote_age_ms,
            "fee_safety_margin": self.fee_safety_margin,
            "use_conservative_fees_for_filtering": self.use_conservative_fees_for_filtering,
            "max_depth_usage_pct": self.max_depth_usage_pct,
            "use_fill_or_kill": self.use_fill_or_kill,
            "max_position_per_market": self.max_position_per_market,
            "max_concurrent_spreads": self.max_concurrent_spreads,
            "scan_interval_seconds": self.scan_interval_seconds,
            "reconciliation_interval_seconds": self.reconciliation_interval_seconds,
            "paper_mode": self.paper_mode,
            "max_daily_loss": self.max_daily_loss,
            "max_error_rate": self.max_error_rate,
            "max_api_latency_seconds": self.max_api_latency_seconds,
            "min_fill_rate": self.min_fill_rate,
            "kalshi_fee_rate": self.kalshi_fee_rate,
            "kalshi_maker_fee_rate": self.kalshi_maker_fee_rate,
            "polymarket_taker_fee": self.polymarket_taker_fee,
            "polymarket_maker_fee": self.polymarket_maker_fee,
            "polymarket_gas_estimate": self.polymarket_gas_estimate,
            "metrics_port": self.metrics_port,
        }
