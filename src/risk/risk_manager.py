"""Risk management module for enforcing trading limits and safety checks.

This is a safety-critical component that protects against:
- Excessive position sizes
- Daily loss limits
- Per-position loss limits
- Total portfolio exposure limits
"""

import logging
import math
from typing import Optional

from src.core.config import RiskConfig
from src.core.models import Position


logger = logging.getLogger(__name__)


class RiskManager:
    """Manages trading risk by enforcing position and loss limits.

    This class tracks all open positions and enforces configurable risk limits
    including per-position size limits, total position limits, and loss thresholds.

    Attributes:
        config: RiskConfig instance with limit settings
        positions: Dictionary mapping ticker to Position
        daily_pnl: Running total of realized P&L for the day
    """

    def __init__(self, config: RiskConfig) -> None:
        """Initialize risk manager with configuration.

        Args:
            config: RiskConfig instance defining risk limits

        Raises:
            TypeError: If config is not a RiskConfig instance
        """
        if not isinstance(config, RiskConfig):
            raise TypeError(f"config must be RiskConfig, got {type(config).__name__}")

        self.config = config
        self.positions: dict[str, Position] = {}
        self.daily_pnl: float = 0.0
        self._trading_halted: bool = False

        logger.info(
            "RiskManager initialized: max_position=%d, max_total=%d, "
            "max_loss_per_position=$%.2f, max_daily_loss=$%.2f",
            config.max_position_size,
            config.max_total_position,
            config.max_loss_per_position,
            config.max_daily_loss,
        )

    def can_trade(
        self,
        ticker: str,
        side: str,
        size: int,
        current_position: Optional[Position] = None,
    ) -> tuple[bool, str]:
        """Check if a trade is allowed under risk limits.

        Args:
            ticker: Market ticker symbol
            side: Trade side ("buy" or "sell")
            size: Number of contracts to trade (must be positive)
            current_position: Current position in this market (optional)

        Returns:
            Tuple of (allowed: bool, reason: str)
            - If allowed, reason is "Trade allowed"
            - If blocked, reason describes which limit was hit
        """
        # Input validation
        if size <= 0:
            return False, f"Trade size must be positive, got {size}"

        if side not in ("buy", "sell"):
            return False, f"Invalid side '{side}', must be 'buy' or 'sell'"

        # Check if trading is halted
        if self._trading_halted:
            logger.warning("Trade blocked for %s: trading halted", ticker)
            return False, "Trading halted due to risk limit breach"

        # Check daily loss limit
        if self.daily_pnl <= -self.config.max_daily_loss:
            logger.warning(
                "Trade blocked for %s: daily loss limit reached ($%.2f)",
                ticker,
                self.daily_pnl,
            )
            return False, f"Daily loss limit reached: ${-self.daily_pnl:.2f} lost"

        # Get current position size for this ticker
        existing_position = current_position or self.positions.get(ticker)
        current_size = existing_position.size if existing_position else 0

        # Calculate new position size after trade
        if side == "buy":
            new_size = current_size + size
        else:  # sell
            new_size = current_size - size

        # Check position limit for this market
        if abs(new_size) > self.config.max_position_size:
            logger.warning(
                "Trade blocked for %s: position limit exceeded (new_size=%d, limit=%d)",
                ticker,
                new_size,
                self.config.max_position_size,
            )
            return (
                False,
                f"Position limit exceeded: {abs(new_size)} > {self.config.max_position_size}",
            )

        # Calculate total position across all markets after trade
        total_position = self._calculate_total_position()
        # Adjust for change in this ticker's position
        position_change = abs(new_size) - abs(current_size)
        new_total = total_position + position_change

        if new_total > self.config.max_total_position:
            logger.warning(
                "Trade blocked for %s: total position limit exceeded (new_total=%d, limit=%d)",
                ticker,
                new_total,
                self.config.max_total_position,
            )
            return (
                False,
                f"Total position limit exceeded: {new_total} > {self.config.max_total_position}",
            )

        # Check if existing position loss exceeds threshold
        if existing_position and existing_position.unrealized_pnl < 0:
            loss = -existing_position.unrealized_pnl
            if loss >= self.config.max_loss_per_position:
                # Only block if adding to a losing position
                is_adding_to_position = (
                    (side == "buy" and current_size > 0)
                    or (side == "sell" and current_size < 0)
                )
                if is_adding_to_position:
                    logger.warning(
                        "Trade blocked for %s: adding to losing position ($%.2f loss)",
                        ticker,
                        loss,
                    )
                    return (
                        False,
                        f"Cannot add to losing position: ${loss:.2f} unrealized loss",
                    )

        # Check limit utilization and log warnings
        self._check_limit_warnings(ticker, abs(new_size), new_total)

        logger.debug(
            "Trade allowed for %s: %s %d contracts (new_size=%d, total=%d)",
            ticker,
            side,
            size,
            new_size,
            new_total,
        )
        return True, "Trade allowed"

    def should_force_close(self, ticker: str, position: Position) -> bool:
        """Check if a position should be force closed due to risk limits.

        Args:
            ticker: Market ticker symbol
            position: Current position to check

        Returns:
            True if position should be force closed, False otherwise
        """
        if position.is_flat:
            return False

        # Check unrealized loss threshold
        if position.unrealized_pnl < 0:
            loss = -position.unrealized_pnl
            if loss >= self.config.max_loss_per_position:
                logger.critical(
                    "FORCE CLOSE triggered for %s: unrealized loss $%.2f >= limit $%.2f",
                    ticker,
                    loss,
                    self.config.max_loss_per_position,
                )
                return True

        # Check daily loss including unrealized
        total_daily_loss = self._calculate_total_daily_loss()
        if total_daily_loss >= self.config.max_daily_loss:
            logger.critical(
                "FORCE CLOSE triggered for %s: daily loss $%.2f >= limit $%.2f",
                ticker,
                total_daily_loss,
                self.config.max_daily_loss,
            )
            return True

        return False

    def register_position(self, ticker: str, position: Position) -> None:
        """Register or update a position in tracking.

        Args:
            ticker: Market ticker symbol
            position: Position to register

        Raises:
            ValueError: If position ticker doesn't match provided ticker
        """
        if position.ticker != ticker:
            raise ValueError(
                f"Position ticker '{position.ticker}' doesn't match provided ticker '{ticker}'"
            )

        old_position = self.positions.get(ticker)
        self.positions[ticker] = position

        # Calculate exposure change
        old_exposure = abs(old_position.size) if old_position else 0
        new_exposure = abs(position.size)

        logger.info(
            "Position registered for %s: size=%d (was %d), unrealized_pnl=$%.2f",
            ticker,
            position.size,
            old_exposure if old_position else 0,
            position.unrealized_pnl,
        )

        # Remove flat positions
        if position.is_flat:
            del self.positions[ticker]
            logger.info("Position for %s closed and removed from tracking", ticker)

        # Check if any limits are breached
        self._check_all_limits()

    def update_daily_pnl(self, realized_pnl: float) -> None:
        """Update daily P&L with realized gains/losses.

        Args:
            realized_pnl: Amount to add to daily P&L (positive = profit, negative = loss)

        Raises:
            ValueError: If realized_pnl is not a valid number
        """
        if not isinstance(realized_pnl, (int, float)) or math.isnan(realized_pnl):
            raise ValueError(f"Invalid realized_pnl: {realized_pnl}")

        old_pnl = self.daily_pnl
        self.daily_pnl += realized_pnl

        logger.info(
            "Daily P&L updated: $%.2f -> $%.2f (change: $%.2f)",
            old_pnl,
            self.daily_pnl,
            realized_pnl,
        )

        # Check daily loss limit
        if self.daily_pnl <= -self.config.max_daily_loss:
            logger.critical(
                "DAILY LOSS LIMIT BREACHED: $%.2f lost (limit: $%.2f)",
                -self.daily_pnl,
                self.config.max_daily_loss,
            )
            self._trading_halted = True

        # Check warning thresholds
        loss_ratio = -self.daily_pnl / self.config.max_daily_loss if self.daily_pnl < 0 else 0
        self._check_threshold_alert("daily_loss", loss_ratio)

    def reset_daily(self) -> None:
        """Reset daily P&L tracking for a new trading day."""
        old_pnl = self.daily_pnl
        self.daily_pnl = 0.0
        self._trading_halted = False

        logger.info(
            "Daily reset: P&L reset from $%.2f to $0.00, trading resumed",
            old_pnl,
        )

    def get_risk_metrics(self) -> dict:
        """Get a summary of current risk state.

        Returns:
            Dictionary containing:
            - total_position: Total contracts across all markets
            - daily_pnl: Daily realized P&L
            - total_unrealized_pnl: Sum of unrealized P&L across positions
            - total_daily_loss: Combined realized + unrealized loss
            - position_limit_utilization: Ratio of max single position to limit
            - total_limit_utilization: Ratio of total position to limit
            - daily_loss_utilization: Ratio of daily loss to limit
            - trading_halted: Whether trading is currently halted
            - positions: Dictionary of positions by ticker
        """
        total_position = self._calculate_total_position()
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        total_daily_loss = self._calculate_total_daily_loss()

        # Calculate max position size across all tickers
        max_single_position = max(
            (abs(p.size) for p in self.positions.values()),
            default=0,
        )

        return {
            "total_position": total_position,
            "daily_pnl": self.daily_pnl,
            "total_unrealized_pnl": total_unrealized,
            "total_daily_loss": total_daily_loss,
            "position_limit_utilization": max_single_position / self.config.max_position_size,
            "total_limit_utilization": total_position / self.config.max_total_position,
            "daily_loss_utilization": (
                total_daily_loss / self.config.max_daily_loss if total_daily_loss > 0 else 0
            ),
            "trading_halted": self._trading_halted,
            "positions": {
                ticker: {
                    "size": p.size,
                    "unrealized_pnl": p.unrealized_pnl,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                }
                for ticker, p in self.positions.items()
            },
        }

    def is_trading_allowed(self) -> bool:
        """Check if trading is currently allowed.

        Returns:
            True if trading is allowed, False if halted due to limit breach
        """
        if self._trading_halted:
            logger.debug("Trading not allowed: halted flag set")
            return False

        # Check daily loss
        if self.daily_pnl <= -self.config.max_daily_loss:
            logger.debug("Trading not allowed: daily loss limit breached")
            return False

        # Check total daily loss including unrealized
        total_daily_loss = self._calculate_total_daily_loss()
        if total_daily_loss >= self.config.max_daily_loss:
            logger.debug("Trading not allowed: total daily loss limit breached")
            return False

        return True

    def _calculate_total_position(self) -> int:
        """Calculate total absolute position across all markets."""
        return sum(abs(p.size) for p in self.positions.values())

    def _calculate_total_daily_loss(self) -> float:
        """Calculate total daily loss including unrealized P&L."""
        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        total_pnl = self.daily_pnl + total_unrealized

        # Return loss as positive number, or 0 if in profit
        return max(0, -total_pnl)

    def _check_limit_warnings(
        self,
        ticker: str,
        new_position_size: int,
        new_total_position: int,
    ) -> None:
        """Check and log warnings for approaching limits."""
        # Position limit utilization
        position_ratio = new_position_size / self.config.max_position_size
        self._check_threshold_alert(f"position_{ticker}", position_ratio)

        # Total position limit utilization
        total_ratio = new_total_position / self.config.max_total_position
        self._check_threshold_alert("total_position", total_ratio)

    def _check_threshold_alert(self, limit_name: str, utilization: float) -> None:
        """Check utilization ratio and log appropriate alert level."""
        if utilization >= self.config.critical_threshold_pct:
            logger.critical(
                "CRITICAL: %s at %.1f%% of limit",
                limit_name,
                utilization * 100,
            )
        elif utilization >= self.config.warning_threshold_pct:
            logger.warning(
                "WARNING: %s at %.1f%% of limit",
                limit_name,
                utilization * 100,
            )

    def _check_all_limits(self) -> None:
        """Check all limits and update trading halted status if needed."""
        # Check total position
        total_position = self._calculate_total_position()
        if total_position > self.config.max_total_position:
            logger.critical(
                "TOTAL POSITION LIMIT BREACHED: %d > %d",
                total_position,
                self.config.max_total_position,
            )
            self._trading_halted = True

        # Check daily loss
        total_daily_loss = self._calculate_total_daily_loss()
        if total_daily_loss >= self.config.max_daily_loss:
            logger.critical(
                "DAILY LOSS LIMIT BREACHED: $%.2f >= $%.2f",
                total_daily_loss,
                self.config.max_daily_loss,
            )
            self._trading_halted = True
