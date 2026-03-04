"""Feature management system for institutional quant features.

Manages enabling/disabling/validating 4 core features:
1. Empirical Kelly (portfolio allocation)
2. VPIN Kill Switch (market making)
3. Sequence Gap Detection (infrastructure)
4. Avellaneda-Stoikov Reservation Price (market making)
"""

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List
from enum import Enum


class Feature(str, Enum):
    """Available features."""
    EMPIRICAL_KELLY = "empirical-kelly"
    VPIN_KILL_SWITCH = "vpin-kill-switch"
    SEQUENCE_GAP_DETECTION = "sequence-gap-detection"
    AS_RESERVATION_PRICE = "as-reservation-price"


@dataclass
class FeatureStatus:
    """Status of a single feature."""
    name: str
    enabled: bool
    config_file: str
    config_key: str
    description: str
    scope: str  # "portfolio", "infrastructure", "market-making"
    applicable_strategies: List[str]


class FeatureManager:
    """Manages institutional quant feature configuration."""

    # Feature metadata
    FEATURES = {
        Feature.EMPIRICAL_KELLY: {
            "config_file": "config/portfolio_config.yaml",
            "config_key": "use_empirical_kelly",
            "description": "Monte Carlo uncertainty-adjusted position sizing",
            "scope": "portfolio",
            "applicable_strategies": ["ALL"],
            "related_keys": {
                "empirical_kelly_simulations": 500,
                "kelly_fraction": 0.5,
            }
        },
        Feature.VPIN_KILL_SWITCH: {
            "config_file": "strategies/configs/prediction_mm_strategy.yaml",
            "config_key": "vpin_kill_switch.enabled",
            "description": "Automatic quote cancellation on toxic order flow",
            "scope": "market-making",
            "applicable_strategies": ["prediction-mm"],
            "related_keys": {
                "vpin_kill_switch.toxic_threshold": 0.75,
                "vpin_kill_switch.warning_threshold": 0.55,
                "vpin_kill_switch.check_interval_sec": 5,
                "vpin_kill_switch.toxic_cooldown_sec": 120,
            }
        },
        Feature.SEQUENCE_GAP_DETECTION: {
            "config_file": "config/websocket_config.yaml",
            "config_key": "enable_sequence_validation",
            "description": "WebSocket message gap detection and reconnection",
            "scope": "infrastructure",
            "applicable_strategies": ["crypto-scalp", "crypto-latency", "prediction-mm"],
            "related_keys": {
                "gap_tolerance": 1,
                "reconnect_delay_ms": 1000,
            }
        },
        Feature.AS_RESERVATION_PRICE: {
            "config_file": "strategies/configs/prediction_mm_strategy.yaml",
            "config_key": "use_reservation_price",
            "description": "Avellaneda-Stoikov time-aware inventory unwinding",
            "scope": "market-making",
            "applicable_strategies": ["prediction-mm"],
            "related_keys": {
                "risk_aversion": 0.05,
                "reservation_use_log_odds": False,
            }
        },
    }

    def __init__(self, project_root: Optional[Path] = None):
        """Initialize feature manager.

        Args:
            project_root: Path to project root (defaults to current directory)
        """
        self.root = project_root or Path.cwd()

    def get_status(self, feature: Feature) -> FeatureStatus:
        """Get current status of a feature.

        Args:
            feature: Feature to check

        Returns:
            FeatureStatus object
        """
        meta = self.FEATURES[feature]
        config_path = self.root / meta["config_file"]

        # Check if enabled
        enabled = False
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}
            enabled = self._get_nested(config, meta["config_key"], False)

        return FeatureStatus(
            name=feature.value,
            enabled=enabled,
            config_file=meta["config_file"],
            config_key=meta["config_key"],
            description=meta["description"],
            scope=meta["scope"],
            applicable_strategies=meta["applicable_strategies"],
        )

    def get_all_statuses(self) -> Dict[Feature, FeatureStatus]:
        """Get status of all features."""
        return {f: self.get_status(f) for f in Feature}

    def enable(self, feature: Feature, dry_run: bool = False) -> bool:
        """Enable a feature.

        Args:
            feature: Feature to enable
            dry_run: If True, show what would be done without modifying files

        Returns:
            True if successful
        """
        meta = self.FEATURES[feature]
        config_path = self.root / meta["config_file"]

        # Create parent directories if needed
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Load or create config
        if config_path.exists():
            with open(config_path, 'r') as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}

        # Set main config key
        self._set_nested(config, meta["config_key"], True)

        # Set related keys if they don't exist
        for key, default_value in meta.get("related_keys", {}).items():
            if not self._get_nested(config, key):
                self._set_nested(config, key, default_value)

        if dry_run:
            print(f"[DRY RUN] Would write to {config_path}:")
            print(yaml.dump(config, default_flow_style=False))
            return True

        # Write config
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return True

    def disable(self, feature: Feature, dry_run: bool = False) -> bool:
        """Disable a feature.

        Args:
            feature: Feature to disable
            dry_run: If True, show what would be done without modifying files

        Returns:
            True if successful
        """
        meta = self.FEATURES[feature]
        config_path = self.root / meta["config_file"]

        if not config_path.exists():
            return True  # Already disabled (no config file)

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}

        # Set to False
        self._set_nested(config, meta["config_key"], False)

        if dry_run:
            print(f"[DRY RUN] Would write to {config_path}:")
            print(yaml.dump(config, default_flow_style=False))
            return True

        # Write config
        with open(config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return True

    def validate(self, feature: Feature) -> Dict[str, any]:
        """Validate a feature's configuration.

        Args:
            feature: Feature to validate

        Returns:
            Dict with validation results
        """
        status = self.get_status(feature)

        if not status.enabled:
            return {
                "valid": False,
                "reason": f"{feature.value} is not enabled"
            }

        # Feature-specific validation
        if feature == Feature.EMPIRICAL_KELLY:
            return self._validate_empirical_kelly()
        elif feature == Feature.VPIN_KILL_SWITCH:
            return self._validate_vpin()
        elif feature == Feature.SEQUENCE_GAP_DETECTION:
            return self._validate_sequence_gap()
        elif feature == Feature.AS_RESERVATION_PRICE:
            return self._validate_as_reservation()

        return {"valid": True}

    # Private helpers

    @staticmethod
    def _get_nested(d: dict, key: str, default=None):
        """Get nested dict value using dot notation."""
        keys = key.split('.')
        val = d
        for k in keys:
            if isinstance(val, dict) and k in val:
                val = val[k]
            else:
                return default
        return val

    @staticmethod
    def _set_nested(d: dict, key: str, value):
        """Set nested dict value using dot notation."""
        keys = key.split('.')
        for k in keys[:-1]:
            if k not in d:
                d[k] = {}
            d = d[k]
        d[keys[-1]] = value

    def _validate_empirical_kelly(self) -> Dict[str, any]:
        """Validate empirical Kelly configuration."""
        config_path = self.root / "config/portfolio_config.yaml"

        if not config_path.exists():
            return {"valid": False, "reason": "Config file not found"}

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}

        issues = []

        # Check simulations
        sims = config.get('empirical_kelly_simulations', 0)
        if sims < 100:
            issues.append(f"empirical_kelly_simulations too low ({sims}, recommend 500+)")

        # Check kelly fraction
        kf = config.get('kelly_fraction', 0)
        if kf <= 0 or kf > 1:
            issues.append(f"kelly_fraction invalid ({kf}, must be in (0, 1])")

        if issues:
            return {"valid": False, "issues": issues}

        return {"valid": True, "simulations": sims, "kelly_fraction": kf}

    def _validate_vpin(self) -> Dict[str, any]:
        """Validate VPIN kill switch configuration."""
        config_path = self.root / "strategies/configs/prediction_mm_strategy.yaml"

        if not config_path.exists():
            return {"valid": False, "reason": "Config file not found"}

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}

        vpin_config = config.get('vpin_kill_switch', {})

        issues = []

        # Check thresholds
        toxic = vpin_config.get('toxic_threshold', 0)
        warning = vpin_config.get('warning_threshold', 0)

        if toxic <= warning:
            issues.append(f"toxic_threshold ({toxic}) must be > warning_threshold ({warning})")

        if toxic > 1 or toxic < 0.5:
            issues.append(f"toxic_threshold ({toxic}) should be in [0.5, 1.0]")

        if issues:
            return {"valid": False, "issues": issues}

        return {"valid": True, "toxic_threshold": toxic, "warning_threshold": warning}

    def _validate_sequence_gap(self) -> Dict[str, any]:
        """Validate sequence gap detection configuration."""
        config_path = self.root / "config/websocket_config.yaml"

        if not config_path.exists():
            # Not critical - can use defaults
            return {
                "valid": True,
                "note": "Config file not found, using defaults (gap_tolerance=1)"
            }

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}

        gap_tol = config.get('gap_tolerance', 1)

        return {"valid": True, "gap_tolerance": gap_tol}

    def _validate_as_reservation(self) -> Dict[str, any]:
        """Validate A-S reservation price configuration."""
        config_path = self.root / "strategies/configs/prediction_mm_strategy.yaml"

        if not config_path.exists():
            return {"valid": False, "reason": "Config file not found"}

        with open(config_path, 'r') as f:
            config = yaml.safe_load(f) or {}

        issues = []

        # Check risk aversion
        gamma = config.get('risk_aversion', 0)
        if gamma <= 0:
            issues.append(f"risk_aversion must be > 0 (got {gamma})")

        if gamma > 0.2:
            issues.append(f"risk_aversion very high ({gamma}), recommend 0.01-0.10")

        if issues:
            return {"valid": False, "issues": issues}

        return {"valid": True, "risk_aversion": gamma}
