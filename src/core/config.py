"""Configuration management for the trading utilities."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import yaml


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    requests_per_second: int = 10
    requests_per_minute: int = 100


@dataclass
class RiskConfig:
    """Risk management configuration with validation.

    All monetary values are in dollars unless otherwise specified.

    Attributes:
        max_position_size: Maximum contracts per single market
        max_total_position: Maximum total contracts across all markets
        max_loss_per_position: Maximum unrealized loss before force close (dollars)
        max_daily_loss: Maximum daily realized + unrealized loss (dollars)
        warning_threshold_pct: Percentage of limit to trigger warning (0-1)
        critical_threshold_pct: Percentage of limit to trigger critical alert (0-1)
    """

    max_position_size: int = 100
    max_total_position: int = 500
    max_loss_per_position: float = 50.0
    max_daily_loss: float = 200.0
    warning_threshold_pct: float = 0.80
    critical_threshold_pct: float = 0.95

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate that risk limits are sensible."""
        errors = []

        if self.max_position_size <= 0:
            errors.append(f"max_position_size must be positive, got {self.max_position_size}")

        if self.max_total_position <= 0:
            errors.append(f"max_total_position must be positive, got {self.max_total_position}")

        if self.max_total_position < self.max_position_size:
            errors.append(
                f"max_total_position ({self.max_total_position}) must be >= "
                f"max_position_size ({self.max_position_size})"
            )

        if self.max_loss_per_position <= 0:
            errors.append(f"max_loss_per_position must be positive, got {self.max_loss_per_position}")

        if self.max_daily_loss <= 0:
            errors.append(f"max_daily_loss must be positive, got {self.max_daily_loss}")

        if self.max_daily_loss < self.max_loss_per_position:
            errors.append(
                f"max_daily_loss ({self.max_daily_loss}) must be >= "
                f"max_loss_per_position ({self.max_loss_per_position})"
            )

        if not 0 < self.warning_threshold_pct < 1:
            errors.append(f"warning_threshold_pct must be between 0 and 1, got {self.warning_threshold_pct}")

        if not 0 < self.critical_threshold_pct <= 1:
            errors.append(f"critical_threshold_pct must be between 0 and 1, got {self.critical_threshold_pct}")

        if self.warning_threshold_pct >= self.critical_threshold_pct:
            errors.append(
                f"warning_threshold_pct ({self.warning_threshold_pct}) must be < "
                f"critical_threshold_pct ({self.critical_threshold_pct})"
            )

        if errors:
            raise ValueError("Invalid RiskConfig: " + "; ".join(errors))

    @classmethod
    def from_dict(cls, data: dict) -> "RiskConfig":
        """Create RiskConfig from dictionary."""
        return cls(
            max_position_size=data.get("max_position_size", 100),
            max_total_position=data.get("max_total_position", 500),
            max_loss_per_position=float(data.get("max_loss_per_position", 50.0)),
            max_daily_loss=float(data.get("max_daily_loss", 200.0)),
            warning_threshold_pct=float(data.get("warning_threshold_pct", 0.80)),
            critical_threshold_pct=float(data.get("critical_threshold_pct", 0.95)),
        )


@dataclass
class CapitalConfig:
    """Capital management configuration.

    Controls how capital is allocated and reserved across exchanges.

    Attributes:
        max_capital_per_trade_pct: Maximum fraction of capital for a single trade (0-1)
        emergency_reserve_pct: Fraction to keep as emergency reserve (0-1)
        rebalance_interval_days: How often to rebalance between exchanges
        target_allocation: Target capital allocation by exchange (must sum to 1.0)
    """
    max_capital_per_trade_pct: float = 0.5
    emergency_reserve_pct: float = 0.25
    rebalance_interval_days: int = 7
    target_allocation: Dict[str, float] = field(default_factory=lambda: {
        "kalshi": 0.5,
        "polymarket": 0.5,
    })

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate that capital config is sensible."""
        errors = []

        if not 0 < self.max_capital_per_trade_pct <= 1:
            errors.append(
                f"max_capital_per_trade_pct must be in (0, 1], got {self.max_capital_per_trade_pct}"
            )

        if not 0 <= self.emergency_reserve_pct < 1:
            errors.append(
                f"emergency_reserve_pct must be in [0, 1), got {self.emergency_reserve_pct}"
            )

        if self.rebalance_interval_days < 1:
            errors.append(
                f"rebalance_interval_days must be >= 1, got {self.rebalance_interval_days}"
            )

        if self.target_allocation:
            total_allocation = sum(self.target_allocation.values())
            if not 0.99 <= total_allocation <= 1.01:  # Allow small float tolerance
                errors.append(
                    f"target_allocation must sum to 1.0, got {total_allocation}"
                )
            for exchange, alloc in self.target_allocation.items():
                if not 0 <= alloc <= 1:
                    errors.append(
                        f"target_allocation[{exchange}] must be in [0, 1], got {alloc}"
                    )

        if errors:
            raise ValueError("Invalid CapitalConfig: " + "; ".join(errors))

    @classmethod
    def from_dict(cls, data: dict) -> "CapitalConfig":
        """Create CapitalConfig from dictionary."""
        return cls(
            max_capital_per_trade_pct=float(data.get("max_capital_per_trade_pct", 0.5)),
            emergency_reserve_pct=float(data.get("emergency_reserve_pct", 0.25)),
            rebalance_interval_days=int(data.get("rebalance_interval_days", 7)),
            target_allocation=data.get("target_allocation", {
                "kalshi": 0.5,
                "polymarket": 0.5,
            }),
        )


@dataclass
class Config:
    """Application configuration loaded from YAML or environment variables."""

    # Database settings
    db_path: str = "data/markets.db"

    # API settings
    api_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    api_timeout: int = 30
    api_max_retries: int = 3

    # Authentication (optional)
    api_key_id: str = ""
    api_private_key_path: str = ""

    # Rate limiting
    rate_limits: RateLimitConfig = field(default_factory=RateLimitConfig)

    # Data collection settings
    min_volume: int = 1000
    snapshot_interval_seconds: int = 60

    # Logging
    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        """Load configuration from a YAML file."""
        config_path = Path(path)
        if not config_path.exists():
            return cls()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        return cls._from_dict(data)

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        rate_limits = RateLimitConfig(
            requests_per_second=int(os.getenv("RATE_LIMIT_PER_SEC", "10")),
            requests_per_minute=int(os.getenv("RATE_LIMIT_PER_MIN", "100")),
        )

        return cls(
            db_path=os.getenv("DB_PATH", "data/markets.db"),
            api_base_url=os.getenv("API_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"),
            api_timeout=int(os.getenv("API_TIMEOUT", "30")),
            api_max_retries=int(os.getenv("API_MAX_RETRIES", "3")),
            api_key_id=os.getenv("KALSHI_API_KEY_ID", ""),
            api_private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", ""),
            rate_limits=rate_limits,
            min_volume=int(os.getenv("MIN_VOLUME", "1000")),
            snapshot_interval_seconds=int(os.getenv("SNAPSHOT_INTERVAL", "60")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

    @classmethod
    def _from_dict(cls, data: dict) -> "Config":
        """Create Config from dictionary."""
        rate_limit_data = data.get("rate_limits", {})
        rate_limits = RateLimitConfig(
            requests_per_second=rate_limit_data.get("requests_per_second", 10),
            requests_per_minute=rate_limit_data.get("requests_per_minute", 100),
        )

        return cls(
            db_path=data.get("db_path", "data/markets.db"),
            api_base_url=data.get("api_base_url", "https://api.elections.kalshi.com/trade-api/v2"),
            api_timeout=data.get("api_timeout", 30),
            api_max_retries=data.get("api_max_retries", 3),
            api_key_id=data.get("api_key_id", ""),
            api_private_key_path=data.get("api_private_key_path", ""),
            rate_limits=rate_limits,
            min_volume=data.get("min_volume", 1000),
            snapshot_interval_seconds=data.get("snapshot_interval_seconds", 60),
            log_level=data.get("log_level", "INFO"),
            log_format=data.get("log_format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s"),
        )

    @classmethod
    def load(cls, config_path: Optional[str] = None) -> "Config":
        """Load config from YAML file if provided, otherwise from environment."""
        if config_path:
            return cls.from_yaml(config_path)

        # Check for default config file
        default_path = Path("config.yaml")
        if default_path.exists():
            return cls.from_yaml(str(default_path))

        return cls.from_env()


# Global config instance
_config: Optional[Config] = None


def get_config(config_path: Optional[str] = None) -> Config:
    """Get or create the global configuration instance."""
    global _config
    if _config is None:
        _config = Config.load(config_path)
    return _config


def set_config(config: Config) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config
