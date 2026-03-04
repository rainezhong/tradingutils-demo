"""Correlation-aware position limits for risk management.

This module provides tools for tracking and limiting exposure to correlated
positions, such as multiple positions in the same category or event.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CorrelationLimitConfig:
    """Configuration for correlation-based exposure limits.

    Attributes:
        max_category_exposure_pct: Maximum exposure in one category as fraction of total (0-1)
        max_event_exposure_pct: Maximum exposure in same event as fraction of total (0-1)
        correlated_categories: List of category prefixes that are considered correlated
    """

    max_category_exposure_pct: float = 0.40  # 40% in one category
    max_event_exposure_pct: float = 0.30  # 30% in same event
    correlated_categories: List[str] = field(
        default_factory=lambda: [
            "POLITICS",
            "FED",
            "ECON",
            "CRYPTO",
            "SPORTS",
            "WEATHER",
        ]
    )

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        self.validate()

    def validate(self) -> None:
        """Validate that correlation limits are sensible."""
        errors = []

        if not 0 < self.max_category_exposure_pct <= 1:
            errors.append(
                f"max_category_exposure_pct must be in (0, 1], got {self.max_category_exposure_pct}"
            )

        if not 0 < self.max_event_exposure_pct <= 1:
            errors.append(
                f"max_event_exposure_pct must be in (0, 1], got {self.max_event_exposure_pct}"
            )

        if errors:
            raise ValueError("Invalid CorrelationLimitConfig: " + "; ".join(errors))


@dataclass
class ExposureGroup:
    """A group of correlated positions.

    Attributes:
        name: Group identifier (category or event name)
        group_type: Type of group ("category" or "event")
        tickers: List of tickers in this group
        total_exposure: Sum of absolute position sizes
        exposure_pct: Exposure as fraction of max total position
    """

    name: str
    group_type: str
    tickers: List[str]
    total_exposure: int
    exposure_pct: float


class CorrelatedExposureTracker:
    """Tracks and limits exposure to correlated positions.

    Groups positions by category and event to enforce concentration limits.
    Categories are extracted from ticker prefixes (e.g., "POLITICS-TRUMP" -> "POLITICS").
    Events are extracted from the full ticker prefix before the last segment
    (e.g., "POLITICS-TRUMP-WIN" -> "POLITICS-TRUMP").

    Attributes:
        config: CorrelationLimitConfig with limit settings
    """

    def __init__(self, config: Optional[CorrelationLimitConfig] = None) -> None:
        """Initialize correlation tracker.

        Args:
            config: CorrelationLimitConfig instance (uses defaults if None)
        """
        self.config = config or CorrelationLimitConfig()

        logger.info(
            "CorrelatedExposureTracker initialized: "
            "category_limit=%.1f%%, event_limit=%.1f%%",
            self.config.max_category_exposure_pct * 100,
            self.config.max_event_exposure_pct * 100,
        )

    def check_exposure(
        self,
        positions: Dict[str, Any],
        proposed_ticker: str,
        proposed_size: int,
        max_total_position: int,
    ) -> Tuple[bool, str]:
        """Check if a proposed trade would exceed correlation limits.

        Args:
            positions: Current positions by ticker (each position must have a .size attribute)
            proposed_ticker: Ticker for proposed trade
            proposed_size: Size of proposed trade (positive = long, negative = short)
            max_total_position: Maximum total position for exposure calculations

        Returns:
            Tuple of (allowed: bool, reason: str)
            - If allowed, reason is "Trade allowed"
            - If blocked, reason describes which limit was hit
        """
        if max_total_position <= 0:
            return False, "max_total_position must be positive"

        # Extract category and event from proposed ticker
        proposed_category = self._extract_category(proposed_ticker)
        proposed_event = self._extract_event(proposed_ticker)

        # Calculate current exposures by category and event
        category_exposures = self._calculate_category_exposures(positions)
        event_exposures = self._calculate_event_exposures(positions)

        # Calculate new exposures after trade
        proposed_abs_size = abs(proposed_size)

        # Check category exposure
        if proposed_category:
            current_category_exposure = category_exposures.get(proposed_category, 0)
            # Get existing position size in this ticker to calculate net change
            existing_size = (
                abs(positions[proposed_ticker].size)
                if proposed_ticker in positions
                else 0
            )
            new_category_exposure = (
                current_category_exposure - existing_size + proposed_abs_size
            )

            max_category_exposure = int(
                max_total_position * self.config.max_category_exposure_pct
            )

            if new_category_exposure > max_category_exposure:
                logger.debug(
                    "Category limit check failed: %s exposure %d > limit %d",
                    proposed_category,
                    new_category_exposure,
                    max_category_exposure,
                )
                return (
                    False,
                    f"Category '{proposed_category}' exposure {new_category_exposure} "
                    f"would exceed limit {max_category_exposure} "
                    f"({self.config.max_category_exposure_pct * 100:.0f}% of {max_total_position})",
                )

        # Check event exposure
        if proposed_event:
            current_event_exposure = event_exposures.get(proposed_event, 0)
            existing_size = (
                abs(positions[proposed_ticker].size)
                if proposed_ticker in positions
                else 0
            )
            new_event_exposure = (
                current_event_exposure - existing_size + proposed_abs_size
            )

            max_event_exposure = int(
                max_total_position * self.config.max_event_exposure_pct
            )

            if new_event_exposure > max_event_exposure:
                logger.debug(
                    "Event limit check failed: %s exposure %d > limit %d",
                    proposed_event,
                    new_event_exposure,
                    max_event_exposure,
                )
                return (
                    False,
                    f"Event '{proposed_event}' exposure {new_event_exposure} "
                    f"would exceed limit {max_event_exposure} "
                    f"({self.config.max_event_exposure_pct * 100:.0f}% of {max_total_position})",
                )

        return True, "Trade allowed"

    def get_exposure_groups(
        self,
        positions: Dict[str, Any],
        max_total_position: int,
    ) -> List[ExposureGroup]:
        """Get all exposure groups with their current exposures.

        Args:
            positions: Current positions by ticker (each position must have a .size attribute)
            max_total_position: Maximum total position for percentage calculations

        Returns:
            List of ExposureGroup objects
        """
        groups = []

        # Get category groups
        category_exposures = self._calculate_category_exposures(positions)
        for category, exposure in category_exposures.items():
            tickers = [
                t
                for t in positions
                if self._extract_category(t) == category and positions[t].size != 0
            ]
            if tickers:
                groups.append(
                    ExposureGroup(
                        name=category,
                        group_type="category",
                        tickers=tickers,
                        total_exposure=exposure,
                        exposure_pct=exposure / max_total_position
                        if max_total_position > 0
                        else 0,
                    )
                )

        # Get event groups
        event_exposures = self._calculate_event_exposures(positions)
        for event, exposure in event_exposures.items():
            tickers = [
                t
                for t in positions
                if self._extract_event(t) == event and positions[t].size != 0
            ]
            if tickers:
                groups.append(
                    ExposureGroup(
                        name=event,
                        group_type="event",
                        tickers=tickers,
                        total_exposure=exposure,
                        exposure_pct=exposure / max_total_position
                        if max_total_position > 0
                        else 0,
                    )
                )

        # Sort by exposure (highest first)
        groups.sort(key=lambda g: g.total_exposure, reverse=True)

        return groups

    def get_metrics(
        self,
        positions: Dict[str, Any],
        max_total_position: int,
    ) -> dict:
        """Get correlation exposure metrics.

        Args:
            positions: Current positions by ticker (each position must have a .size attribute)
            max_total_position: Maximum total position for calculations

        Returns:
            Dictionary with correlation metrics
        """
        category_exposures = self._calculate_category_exposures(positions)
        event_exposures = self._calculate_event_exposures(positions)

        max_category_limit = int(
            max_total_position * self.config.max_category_exposure_pct
        )
        max_event_limit = int(max_total_position * self.config.max_event_exposure_pct)

        # Find highest utilization
        max_category_util = 0.0
        max_category_name = None
        for category, exposure in category_exposures.items():
            util = exposure / max_category_limit if max_category_limit > 0 else 0
            if util > max_category_util:
                max_category_util = util
                max_category_name = category

        max_event_util = 0.0
        max_event_name = None
        for event, exposure in event_exposures.items():
            util = exposure / max_event_limit if max_event_limit > 0 else 0
            if util > max_event_util:
                max_event_util = util
                max_event_name = event

        return {
            "category_exposures": category_exposures,
            "event_exposures": event_exposures,
            "max_category_utilization": max_category_util,
            "max_category_name": max_category_name,
            "max_event_utilization": max_event_util,
            "max_event_name": max_event_name,
            "category_limit": max_category_limit,
            "event_limit": max_event_limit,
        }

    def _extract_category(self, ticker: str) -> Optional[str]:
        """Extract category from ticker.

        Categories are the first segment of hyphen-separated tickers.
        Only returns category if it matches a known correlated category.

        Args:
            ticker: Market ticker

        Returns:
            Category string or None if not a known category
        """
        if not ticker:
            return None

        parts = ticker.upper().split("-")
        if not parts:
            return None

        category = parts[0]

        # Check if it matches a known category
        for known_category in self.config.correlated_categories:
            if category.startswith(known_category.upper()):
                return known_category.upper()

        return category

    def _extract_event(self, ticker: str) -> Optional[str]:
        """Extract event from ticker.

        Events are all segments except the last (which is typically the outcome).
        E.g., "POLITICS-TRUMP-WIN" -> "POLITICS-TRUMP"

        Args:
            ticker: Market ticker

        Returns:
            Event string or None if ticker has less than 2 segments
        """
        if not ticker:
            return None

        parts = ticker.upper().split("-")
        if len(parts) < 2:
            return None

        # Event is everything except the last segment
        return "-".join(parts[:-1])

    def _calculate_category_exposures(
        self,
        positions: Dict[str, Any],
    ) -> Dict[str, int]:
        """Calculate total exposure by category.

        Args:
            positions: Current positions by ticker (each position must have a .size attribute)

        Returns:
            Dictionary mapping category to total absolute exposure
        """
        exposures: Dict[str, int] = {}

        for ticker, position in positions.items():
            if position.size == 0:
                continue

            category = self._extract_category(ticker)
            if category:
                exposures[category] = exposures.get(category, 0) + abs(position.size)

        return exposures

    def _calculate_event_exposures(
        self,
        positions: Dict[str, Any],
    ) -> Dict[str, int]:
        """Calculate total exposure by event.

        Args:
            positions: Current positions by ticker (each position must have a .size attribute)

        Returns:
            Dictionary mapping event to total absolute exposure
        """
        exposures: Dict[str, int] = {}

        for ticker, position in positions.items():
            if position.size == 0:
                continue

            event = self._extract_event(ticker)
            if event:
                exposures[event] = exposures.get(event, 0) + abs(position.size)

        return exposures
