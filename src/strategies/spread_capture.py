"""Spread Capture Strategy - Buy at bid, sell at ask on wide-spread markets.

This strategy targets low-volume Kalshi prediction markets with wide spreads:
1. Posts a passive buy at the best bid (maker order)
2. Once filled, immediately posts a sell at the best ask (maker order)
3. Captures the spread minus fees
4. Manages stuck inventory if exit doesn't fill

Best conditions:
- Wide spreads (5-30 cents)
- Low volume markets (less competition)
- Mid prices away from extremes (15-85 cents) for lower fees
- Sufficient time to event (avoids volatility near expiry)

Extends DepthStrategyBase for orderbook feeds, order management, and logging.
"""

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from src.oms.capital_manager import CapitalManager
    from src.risk.correlation_limits import CorrelatedExposureTracker
    from src.risk.risk_manager import RiskManager

from src.core.orderbook_manager import OrderBookState, Side
from src.fill_time import (
    DepthSnapshotCollector,
    FillTimeConfig,
    FillTimeEstimator,
    SnapshotStore,
    VelocityEstimator,
)

from .depth_base import (
    DepthMetrics,
    DepthOpportunity,
    DepthStrategyBase,
    PositionSizer,
)

logger = logging.getLogger(__name__)


# =============================================================================
# State Machine
# =============================================================================


class SpreadCaptureState(Enum):
    """States for a spread capture round-trip."""

    PENDING_ENTRY = "pending_entry"
    OPEN = "open"
    PENDING_EXIT = "pending_exit"
    STUCK = "stuck"
    CLOSED = "closed"
    CANCELLED = "cancelled"


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class SpreadCaptureConfig:
    """Configuration for spread capture strategy."""

    # --- Market Selection ---
    # Only trade markets matching these prefixes (backtest-validated).
    # Default: NBA + NCAAB game winners, totals, and wins-by-X spreads.
    # Set to None to allow all markets.
    allowed_ticker_prefixes: Optional[List[str]] = field(
        default_factory=lambda: [
            "KXNBAGAME",
            "KXNBATOTAL",
            "KXNBASPREAD",
            "KXNCAAMBGAME",
            "KXNCAAMBTOTAL",
            "KXNCAAMBSPREAD",
        ]
    )
    min_spread_cents: int = 14  # Wide spreads needed for mean reversion profits
    max_spread_cents: int = 30
    min_depth_at_best: int = 3
    min_mid_price_cents: float = 15.0
    max_mid_price_cents: float = 85.0
    prefer_high_prob: bool = False  # Only trade high-prob markets (mid > 65c)
    high_prob_min_mid_cents: float = 65.0  # Min mid price when prefer_high_prob=True
    live_games_only: bool = (
        True  # Only trade games that have started (based on ticker date)
    )
    min_time_to_close_minutes: int = (
        1  # Don't trade markets closing within this many minutes
    )
    min_volume_24h: int = 1  # Skip zero-volume markets in live mode
    max_volume_24h: Optional[int] = (
        None  # Target low-liquidity markets (None = no limit)
    )
    min_movement_score: float = 0.0  # Minimum movement likelihood score (0-1)
    # Combined activity filter (volume + movement score)
    use_combined_filter: bool = False  # Enable combined vol/movement scoring
    min_activity_score: float = 0.5  # Minimum combined score (0-1)
    volume_weight: float = 0.6  # Weight for volume in combined score
    movement_weight: float = 0.4  # Weight for orderbook movement signals
    volume_reference: int = 10000  # Volume that gets max score (log scale used)
    min_time_to_event_hours: float = 2.0
    max_event_exposure_contracts: int = 100

    # --- Price Movement Filter ---
    require_price_movement: bool = True  # Only enter if price moved recently
    price_movement_window_seconds: float = 60.0  # Look back window for movement
    min_price_change_cents: int = 1  # Minimum bid/ask change to count as movement

    # --- Live Activity Filter (for active markets like live sports) ---
    require_live_activity: bool = (
        True  # Only trade on "hot" markets with recent activity
    )
    live_activity_window_seconds: float = 30.0  # Window to measure activity
    min_price_changes: int = 2  # Minimum number of price changes in window
    min_total_movement_cents: int = (
        3  # Minimum total price movement (bid + ask) in window
    )

    # --- Mean Reversion Entry (profitable on backtests) ---
    # Only enter after price dropped - betting on bounce back
    use_mean_reversion_entry: bool = True  # Require price drop before entry
    mean_reversion_window_frames: int = 5  # Look back N poll cycles for drop
    min_price_drop_cents: int = 6  # Minimum drop to trigger entry

    # --- Undercut Exit Strategy (for volatile markets) ---
    use_undercut_exit: bool = True  # Exit at entry + fixed profit instead of at ask
    undercut_profit_cents: int = 8  # Target profit in cents (exit = entry + this)
    undercut_min_movement_cents: int = (
        5  # Only use undercut when recent movement >= this
    )

    # --- Entry ---
    bid_improvement_cents: int = 0
    depth_utilization_pct: float = 0.2
    max_entry_size: int = 15
    min_entry_size: int = 1
    entry_timeout_seconds: float = 300.0

    # --- Exit ---
    ask_discount_cents: int = 2
    exit_timeout_seconds: float = 120.0

    # --- Taker Exit ---
    use_taker_exit: bool = True  # Use taker order to exit when profitable
    taker_exit_min_profit_cents: int = 5  # Min profit to trigger taker exit
    taker_exit_check_interval_seconds: float = 10.0  # How often to check for taker exit

    # --- Alerts ---
    enable_alerts: bool = True  # Enable desktop/log alerts
    alert_on_trade_complete: bool = True  # Alert on every completed trade
    alert_on_circuit_breaker: bool = True  # Alert when circuit breaker triggers
    alert_on_daily_limit: bool = True  # Alert when daily loss limit hit
    alerts_log_file: Optional[str] = "logs/alerts.log"  # Separate alerts log

    # --- Stuck Management ---
    stuck_improvement_interval_seconds: float = 15.0
    stuck_improvement_cents: int = 2
    stuck_max_improvements: int = 5
    stuck_min_remaining_spread_cents: int = 4
    critical_time_to_event_hours: float = 0.5

    # --- Loss Cutting ---
    max_hold_time_seconds: float = 60.0  # Force exit after 1 minute (backtest optimal)
    max_loss_per_position_cents: int = (
        6  # Cut loss at 6c (backtest optimal with 8c profit target)
    )
    cut_loss_on_adverse_move: bool = True  # Exit if bid drops significantly below entry

    # --- Hedging ---
    enable_hedging: bool = False  # Enable hedging for stuck positions
    hedge_after_stuck_seconds: float = 60.0  # Hedge if stuck for this long
    hedge_min_spread_reduction_cents: int = (
        3  # Only hedge if it reduces exposure by this much
    )

    # --- Multi-Leg Trading ---
    enable_multi_leg: bool = False  # Enable multi-leg spread trading
    multi_leg_pair_types: List[str] = field(
        default_factory=lambda: ["complement"]
    )  # Types: complement, over_under
    multi_leg_max_leg_imbalance: int = 1  # Max contracts difference between legs
    multi_leg_combined_min_edge_cents: int = 5  # Min combined edge for pair entry

    # --- Risk ---
    max_concurrent_positions: int = 10
    max_positions_per_ticker: int = 1
    max_daily_loss_dollars: float = 50.0
    max_loss_per_trade_dollars: float = 10.0
    circuit_breaker_consecutive_losses: int = 5
    circuit_breaker_cooldown_seconds: float = 300.0

    # --- Timing ---
    scan_interval_seconds: float = 5.0
    cooldown_between_trades_seconds: float = 10.0

    # --- Simulation ---
    adverse_selection_cents: Optional[int] = None  # None = auto: max(1, spread // 3)

    # --- Fees ---
    kalshi_maker_rate: float = 0.0175
    kalshi_taker_rate: float = 0.07
    assume_maker_entry: bool = True
    assume_maker_exit: bool = True

    # --- Kelly Sizing ---
    use_kelly_sizing: bool = False
    kelly_fraction: float = 0.5  # Half-Kelly for safety (reduces variance)
    kelly_win_prob: float = 0.70  # Estimated prob of profitable round-trip
    kelly_max_bankroll_pct: float = 0.02  # Max 2% of bankroll per trade
    bankroll_override: Optional[float] = None  # Manual bankroll (None = fetch from API)

    # --- Dynamic Pricing ---
    use_dynamic_pricing: bool = True
    imbalance_weight: float = 0.5  # How much imbalance affects price
    microprice_weight: float = 0.3  # Weight for microprice signal
    max_bid_improvement_cents: int = 3  # Max bid improvement
    max_ask_discount_cents: int = 3  # Max ask discount
    min_expected_edge_cents: int = 3  # Minimum profit floor
    reprice_interval_seconds: float = 5.0  # How often to recalculate
    reprice_threshold_cents: int = 1  # Min change to trigger update
    queue_join_threshold: int = 20  # Depth to join vs improve
    poll_interval_active_ms: int = 500  # Fast polling when orders open

    def validate(self) -> None:
        """Validate configuration parameters."""
        if self.min_spread_cents < 1:
            raise ValueError("min_spread_cents must be >= 1")
        if self.max_spread_cents < self.min_spread_cents:
            raise ValueError("max_spread_cents must be >= min_spread_cents")
        if self.min_entry_size < 1:
            raise ValueError("min_entry_size must be >= 1")
        if self.max_entry_size < self.min_entry_size:
            raise ValueError("max_entry_size must be >= min_entry_size")
        if self.min_mid_price_cents < 1 or self.max_mid_price_cents > 99:
            raise ValueError("mid_price_cents must be within 1-99")
        if self.max_daily_loss_dollars <= 0:
            raise ValueError("max_daily_loss_dollars must be positive")
        if self.max_concurrent_positions < 1:
            raise ValueError("max_concurrent_positions must be >= 1")
        if self.circuit_breaker_consecutive_losses < 1:
            raise ValueError("circuit_breaker_consecutive_losses must be >= 1")
        if self.depth_utilization_pct <= 0 or self.depth_utilization_pct > 1.0:
            raise ValueError("depth_utilization_pct must be in (0, 1.0]")


# =============================================================================
# Fee Calculation
# =============================================================================


def kalshi_fee(rate: float, contracts: int, price_cents: int) -> float:
    """Calculate Kalshi fee.

    Formula: ceil(rate * C * P * (1-P) * 100) / 100
    where P = price_cents / 100.

    Args:
        rate: Fee rate (0.0175 for maker, 0.07 for taker)
        contracts: Number of contracts
        price_cents: Price in cents (1-99)

    Returns:
        Fee in dollars, rounded up to nearest cent.
    """
    p = price_cents / 100.0
    raw_fee = rate * contracts * p * (1.0 - p)
    return math.ceil(raw_fee * 100.0) / 100.0


# =============================================================================
# Trade Tracking
# =============================================================================


@dataclass
class SpreadCaptureTrade:
    """Tracks a single spread capture round-trip."""

    # Identity
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    ticker: str = ""

    # State
    state: SpreadCaptureState = SpreadCaptureState.PENDING_ENTRY

    # Entry fields
    entry_order_id: Optional[str] = None
    entry_price: int = 0
    entry_fill_price: Optional[int] = None
    entry_fill_size: int = 0
    entry_time: Optional[float] = None
    entry_fill_time: Optional[float] = None

    # Exit fields
    exit_order_id: Optional[str] = None
    exit_price: int = 0
    exit_fill_price: Optional[int] = None
    exit_fill_size: int = 0
    current_exit_price: int = 0

    # Stuck management
    stuck_since: Optional[float] = None
    improvement_count: int = 0

    # P&L
    gross_pnl: float = 0.0
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    net_pnl: float = 0.0

    # Exit flags
    was_taker_exit: bool = False

    # Capital management
    capital_reservation_id: Optional[str] = None

    # Snapshot at entry
    spread_at_entry: int = 0
    mid_at_entry: float = 0.0
    depth_at_entry_bid: int = 0
    depth_at_entry_ask: int = 0

    # Pluggable clock (defaults to time.time)
    _clock: object = field(default=None, repr=False)

    def compute_fees(self, maker_rate: float) -> None:
        """Compute entry and exit fees based on fill prices."""
        if self.entry_fill_price and self.entry_fill_size > 0:
            self.entry_fee = kalshi_fee(
                maker_rate, self.entry_fill_size, self.entry_fill_price
            )
        if self.exit_fill_price and self.exit_fill_size > 0:
            self.exit_fee = kalshi_fee(
                maker_rate, self.exit_fill_size, self.exit_fill_price
            )

    def compute_pnl(self) -> None:
        """Compute gross and net P&L."""
        if (
            self.entry_fill_price is not None
            and self.exit_fill_price is not None
            and self.entry_fill_size > 0
        ):
            size = min(self.entry_fill_size, self.exit_fill_size)
            # Bought at entry, sold at exit
            self.gross_pnl = (
                (self.exit_fill_price - self.entry_fill_price) * size / 100.0
            )
            self.net_pnl = self.gross_pnl - self.entry_fee - self.exit_fee

    def is_active(self) -> bool:
        """Whether the trade is still in progress."""
        return self.state in (
            SpreadCaptureState.PENDING_ENTRY,
            SpreadCaptureState.OPEN,
            SpreadCaptureState.PENDING_EXIT,
            SpreadCaptureState.STUCK,
        )

    def hold_time(self) -> float:
        """Seconds held since entry fill."""
        if self.entry_fill_time is None:
            return 0.0
        clock = self._clock or time.time
        return clock() - self.entry_fill_time

    def to_dict(self) -> dict:
        return {
            "trade_id": self.trade_id,
            "ticker": self.ticker,
            "state": self.state.value,
            "entry_price": self.entry_price,
            "entry_fill_price": self.entry_fill_price,
            "entry_fill_size": self.entry_fill_size,
            "exit_price": self.exit_price,
            "exit_fill_price": self.exit_fill_price,
            "exit_fill_size": self.exit_fill_size,
            "current_exit_price": self.current_exit_price,
            "improvement_count": self.improvement_count,
            "gross_pnl": self.gross_pnl,
            "entry_fee": self.entry_fee,
            "exit_fee": self.exit_fee,
            "net_pnl": self.net_pnl,
            "spread_at_entry": self.spread_at_entry,
            "hold_time": self.hold_time(),
        }


# =============================================================================
# Strategy
# =============================================================================


class SpreadCaptureStrategy(DepthStrategyBase):
    """Spread capture strategy: buy at bid, sell at ask on wide-spread markets.

    Lifecycle per trade:
        1. Detect opportunity (wide spread, good depth, positive net edge)
        2. Place passive buy at best bid (+ optional improvement)
        3. Wait for entry fill or timeout
        4. Place passive sell at best ask (- optional discount)
        5. Wait for exit fill or timeout
        6. If exit times out, enter stuck management loop
        7. Stuck: periodically improve exit price toward mid
        8. Force exit if improvements exhausted or time running out
    """

    def __init__(
        self,
        config: SpreadCaptureConfig,
        dry_run: bool = True,
        log_dir: str = "data/spread_capture",
        use_polling: bool = False,
        poll_interval: float = 2.0,
        fill_probability: float = 0.85,
        passive_fill_rate: float = 0.025,
        # Exit fill simulation parameters
        exit_passive_fill_rate: float = 0.008,
        exit_spread_penalty_per_cent: float = 0.04,
        exit_distance_penalty_per_cent: float = 0.02,
        # Optional infrastructure modules (backward-compatible: None = use inline logic)
        risk_manager: Optional["RiskManager"] = None,
        capital_manager: Optional["CapitalManager"] = None,
        correlation_tracker: Optional["CorrelatedExposureTracker"] = None,
        # Pluggable time primitives (forwarded to base class)
        clock=None,
        sleep=None,
        wait_for_event=None,
    ):
        config.validate()
        self.config = config

        position_sizer = PositionSizer(
            max_risk_dollars=config.max_loss_per_trade_dollars,
            max_depth_usage_pct=config.depth_utilization_pct,
            min_size=config.min_entry_size,
            max_size=config.max_entry_size,
        )

        super().__init__(
            dry_run=dry_run,
            log_dir=log_dir,
            position_sizer=position_sizer,
            use_polling=use_polling,
            poll_interval=poll_interval,
            fill_probability=fill_probability,
            passive_fill_rate=passive_fill_rate,
            exit_passive_fill_rate=exit_passive_fill_rate,
            exit_spread_penalty_per_cent=exit_spread_penalty_per_cent,
            exit_distance_penalty_per_cent=exit_distance_penalty_per_cent,
            clock=clock,
            sleep=sleep,
            wait_for_event=wait_for_event,
        )

        # Active trades by trade_id
        self._trades: Dict[str, SpreadCaptureTrade] = {}

        # Map order_id -> trade_id for fill routing
        self._order_to_trade: Dict[str, str] = {}

        # Stuck management tasks
        self._stuck_tasks: Dict[str, asyncio.Task] = {}

        # Cooldown tracking: ticker -> last trade completion time
        self._last_trade_time: Dict[str, float] = {}

        # Risk state
        self._daily_pnl: float = 0.0
        self._daily_loss_limit_hit: bool = False
        self._consecutive_losses: int = 0
        self._circuit_breaker_active: bool = False
        self._circuit_breaker_until: float = 0.0

        # Session metrics
        self._session_trades: int = 0
        self._session_wins: int = 0
        self._session_losses: int = 0

        # Kelly sizing state
        self._cached_bankroll: Optional[float] = None
        self._bankroll_last_fetched: float = 0.0
        self._bankroll_cache_seconds: float = 60.0  # Refresh every 60s

        # Price movement tracking: ticker -> list of (timestamp, bid, ask)
        self._price_history: Dict[str, List[Tuple[float, int, int]]] = {}

        # Fill time estimation system
        self._fill_time_config = FillTimeConfig()
        self._snapshot_store = SnapshotStore(self._fill_time_config)
        self._velocity_estimator = VelocityEstimator(self._fill_time_config)
        self._fill_estimator = FillTimeEstimator(
            self._fill_time_config, self._velocity_estimator
        )
        self._snapshot_collector = DepthSnapshotCollector(
            self._fill_time_config, self._snapshot_store
        )
        # In WebSocket mode, also attach listener for between-poll snapshots
        self._snapshot_collector.attach(self._orderbook_mgr)

        # Optional infrastructure modules
        self._risk_manager = risk_manager
        self._capital_manager = capital_manager
        self._correlation_tracker = correlation_tracker

    def get_strategy_name(self) -> str:
        return "spread_capture"

    # =========================================================================
    # Alerts & Notifications
    # =========================================================================

    def _send_alert(self, title: str, message: str, urgent: bool = False) -> None:
        """Send alert via desktop notification and log file."""
        if not self.config.enable_alerts:
            return

        # Log to alerts file
        if self.config.alerts_log_file:
            try:
                import os

                os.makedirs(os.path.dirname(self.config.alerts_log_file), exist_ok=True)
                with open(self.config.alerts_log_file, "a") as f:
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    urgency = "URGENT" if urgent else "INFO"
                    f.write(f"[{timestamp}] [{urgency}] {title}: {message}\n")
            except Exception as e:
                logger.debug(f"Failed to write alert to log: {e}")

        # Send macOS desktop notification
        try:
            import subprocess

            sound = "Basso" if urgent else "Pop"
            script = f'display notification "{message}" with title "{title}" sound name "{sound}"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        except Exception as e:
            logger.debug(f"Failed to send desktop notification: {e}")

    def _alert_trade_complete(self, trade: "SpreadCaptureTrade") -> None:
        """Send alert for completed trade."""
        if not self.config.alert_on_trade_complete:
            return

        result = "WIN" if trade.net_pnl > 0 else "LOSS"
        ticker_short = trade.ticker.split("-")[-1] if trade.ticker else "?"
        title = f"Trade {result}: {ticker_short}"
        message = f"${trade.net_pnl:+.2f} | {trade.entry_fill_price}c→{trade.exit_fill_price}c | Daily: ${self._daily_pnl:+.2f}"
        self._send_alert(title, message, urgent=(trade.net_pnl < -0.50))

    def _alert_circuit_breaker(self) -> None:
        """Send alert when circuit breaker triggers."""
        if not self.config.alert_on_circuit_breaker:
            return

        title = "Circuit Breaker Triggered"
        message = f"{self._consecutive_losses} consecutive losses. Pausing {self.config.circuit_breaker_cooldown_seconds}s"
        self._send_alert(title, message, urgent=True)

    def _alert_daily_limit(self) -> None:
        """Send alert when daily loss limit hit."""
        if not self.config.alert_on_daily_limit:
            return

        title = "Daily Loss Limit Hit"
        message = f"Daily P&L: ${self._daily_pnl:.2f}. Trading halted."
        self._send_alert(title, message, urgent=True)

    # =========================================================================
    # Hedging Helpers
    # =========================================================================

    def _find_complement_ticker(self, ticker: str) -> Optional[str]:
        """Find the complement ticker for a game market.

        For game winner markets like KXNCAAMBGAME-26FEB06BELUIC-BEL,
        the complement is KXNCAAMBGAME-26FEB06BELUIC-UIC (other team).

        Returns:
            Complement ticker if found, None otherwise.
        """
        import re

        # Parse ticker: PREFIX-DATEGGAMEID-TEAM
        # e.g., KXNCAAMBGAME-26FEB06BELUIC-BEL
        parts = ticker.rsplit("-", 1)
        if len(parts) != 2:
            return None

        base, team = parts

        # Extract game ID to find both teams
        # Game ID is like "BELUIC" where teams are "BEL" and "UIC"
        game_match = re.search(r"(\d{2}[A-Z]{3}\d{2})([A-Z]+)$", base)
        if not game_match:
            return None

        game_match.group(1)
        game_id = game_match.group(2)

        # Try to find the other team in the game ID
        # Common patterns: BELUIC = BEL vs UIC, LAKBOS = LAK vs BOS
        if len(game_id) >= 6:
            # Assume 3-letter team codes
            team1 = game_id[:3]
            team2 = game_id[3:6] if len(game_id) >= 6 else game_id[3:]

            if team == team1:
                complement_team = team2
            elif team == team2:
                complement_team = team1
            else:
                return None

            return f"{base}-{complement_team}"

        return None

    def _should_hedge(self, trade: "SpreadCaptureTrade") -> bool:
        """Check if we should hedge a stuck position."""
        if not self.config.enable_hedging:
            return False

        if trade.state != SpreadCaptureState.STUCK:
            return False

        # Check if stuck long enough
        stuck_duration = self._clock() - (trade.stuck_since or self._clock())
        if stuck_duration < self.config.hedge_after_stuck_seconds:
            return False

        # Check if complement exists
        complement = self._find_complement_ticker(trade.ticker)
        if not complement:
            return False

        # Check if complement has good spread
        complement_book = self.get_orderbook(complement)
        if (
            not complement_book
            or not complement_book.best_bid
            or not complement_book.best_ask
        ):
            return False

        return True

    # =========================================================================
    # Multi-Leg Trading Helpers
    # =========================================================================

    def _find_multi_leg_pairs(self, tickers: List[str]) -> List[Tuple[str, str, str]]:
        """Find tradeable pairs from a list of tickers.

        Returns:
            List of (ticker1, ticker2, pair_type) tuples.
        """
        pairs = []

        if "complement" in self.config.multi_leg_pair_types:
            # Find complement pairs (same game, different teams)
            seen = set()
            for ticker in tickers:
                if ticker in seen:
                    continue
                complement = self._find_complement_ticker(ticker)
                if complement and complement in tickers and complement not in seen:
                    pairs.append((ticker, complement, "complement"))
                    seen.add(ticker)
                    seen.add(complement)

        return pairs

    def _analyze_multi_leg_opportunity(
        self,
        ticker1: str,
        ticker2: str,
        pair_type: str,
    ) -> Optional[Dict]:
        """Analyze a multi-leg pair for trading opportunity.

        For complement pairs (Team A vs Team B):
        - Combined probability should be ~100%
        - Look for mispricings where P(A) + P(B) != 100

        Returns:
            Opportunity dict if found, None otherwise.
        """
        book1 = self.get_orderbook(ticker1)
        book2 = self.get_orderbook(ticker2)

        if not book1 or not book2:
            return None
        if not book1.best_bid or not book1.best_ask:
            return None
        if not book2.best_bid or not book2.best_ask:
            return None

        if pair_type == "complement":
            # For complements, check if bid1 + ask2 > 100 (arb: buy1 sell2)
            # or ask1 + bid2 < 100 (arb: sell1 buy2)
            mid1 = book1.mid_price or 50
            mid2 = book2.mid_price or 50

            # Check for mispricing
            combined = mid1 + mid2
            edge_cents = abs(combined - 100)

            if edge_cents >= self.config.multi_leg_combined_min_edge_cents:
                # There's an opportunity
                if combined > 100:
                    # Markets overpriced - could sell both
                    return {
                        "pair_type": pair_type,
                        "ticker1": ticker1,
                        "ticker2": ticker2,
                        "action": "sell_both",
                        "edge_cents": edge_cents,
                        "combined_prob": combined,
                        "price1": book1.best_bid.price,
                        "price2": book2.best_bid.price,
                    }
                else:
                    # Markets underpriced - could buy both
                    return {
                        "pair_type": pair_type,
                        "ticker1": ticker1,
                        "ticker2": ticker2,
                        "action": "buy_both",
                        "edge_cents": edge_cents,
                        "combined_prob": combined,
                        "price1": book1.best_ask.price,
                        "price2": book2.best_ask.price,
                    }

        return None

    # =========================================================================
    # Dynamic Pricing Helpers
    # =========================================================================

    def _calculate_imbalance(self, book: OrderBookState, levels: int = 3) -> float:
        """Calculate bid-ask imbalance from orderbook.

        Args:
            book: Current orderbook state
            levels: Number of levels to consider

        Returns:
            Imbalance in range [-1, 1], positive = buy pressure
        """
        if not book.bids or not book.asks:
            return 0.0

        bid_depth = sum(level.size for level in book.bids[:levels])
        ask_depth = sum(level.size for level in book.asks[:levels])

        total = bid_depth + ask_depth
        if total == 0:
            return 0.0

        return (bid_depth - ask_depth) / total

    def _calculate_microprice(self, book: OrderBookState) -> float:
        """Calculate microprice (volume-weighted fair value).

        Microprice adjusts mid-price based on orderbook imbalance at best levels.
        If there's more size at the bid, fair value is closer to the ask (and vice versa).

        Args:
            book: Current orderbook state

        Returns:
            Microprice in cents
        """
        if not book.best_bid or not book.best_ask:
            return book.mid_price or 50.0

        bid_price = book.best_bid.price
        ask_price = book.best_ask.price
        bid_size = book.best_bid.size
        ask_size = book.best_ask.size

        total_size = bid_size + ask_size
        if total_size == 0:
            return (bid_price + ask_price) / 2.0

        # Weight prices by opposite side quantity
        microprice = (bid_price * ask_size + ask_price * bid_size) / total_size
        return microprice

    def _calculate_movement_score(self, book: OrderBookState) -> float:
        """Calculate likelihood of market movement based on orderbook state.

        Higher score = more likely to see fills/movement soon.

        Factors:
        - Volume-to-depth ratio: High volume vs thin book = active
        - Thin depth at best: Small queue = easy to move
        - Imbalance strength: Strong imbalance = directional pressure
        - Depth asymmetry: One side much thinner = likely to get hit

        Returns:
            Score in range [0, 1], higher = more likely movement
        """
        if not book.best_bid or not book.best_ask:
            return 0.0

        score = 0.0

        # 1. Volume-to-depth ratio (0-0.4 points)
        # High volume relative to depth at best = active market
        best_depth = min(book.best_bid.size, book.best_ask.size)
        if best_depth > 0 and book.volume_24h > 0:
            vol_depth_ratio = book.volume_24h / best_depth
            # Normalize: ratio of 10+ = max score
            score += min(0.4, vol_depth_ratio / 25.0)

        # 2. Thin depth at best (0-0.3 points)
        # Thin book = easy to move/fill
        # <10 contracts = very thin, <50 = thin, <200 = moderate
        if best_depth < 10:
            score += 0.3
        elif best_depth < 50:
            score += 0.2
        elif best_depth < 200:
            score += 0.1

        # 3. Imbalance strength (0-0.2 points)
        # Strong imbalance = directional pressure
        imbalance = abs(self._calculate_imbalance(book, levels=3))
        score += imbalance * 0.2

        # 4. Depth asymmetry (0-0.1 points)
        # One side much thinner = likely to get hit
        bid_depth = book.best_bid.size
        ask_depth = book.best_ask.size
        if bid_depth > 0 and ask_depth > 0:
            asymmetry = abs(bid_depth - ask_depth) / max(bid_depth, ask_depth)
            score += asymmetry * 0.1

        return min(1.0, score)

    def _calculate_activity_score(self, book: OrderBookState) -> float:
        """Calculate combined activity score from volume and movement signals.

        Combines:
        - Volume (log-normalized): High volume = active market with fills
        - Movement score: Orderbook structure signals (depth, imbalance, etc.)

        Returns:
            Score in range [0, 1], higher = more likely to get fills
        """
        import math

        # 1. Volume component (log-normalized)
        # Use log scale since volume ranges from 0 to 100k+
        # log10(1) = 0, log10(10000) = 4
        volume = max(1, book.volume_24h)
        ref_vol = max(1, self.config.volume_reference)
        vol_score = min(1.0, math.log10(volume) / math.log10(ref_vol))

        # 2. Movement score component (already 0-1)
        movement_score = self._calculate_movement_score(book)

        # Weighted combination
        combined = (
            self.config.volume_weight * vol_score
            + self.config.movement_weight * movement_score
        )

        return min(1.0, combined)

    def _record_price(self, ticker: str, book: OrderBookState) -> None:
        """Record current bid/ask for price movement tracking."""
        if not book.best_bid or not book.best_ask:
            return

        now = self._clock()
        bid = book.best_bid.price
        ask = book.best_ask.price

        if ticker not in self._price_history:
            self._price_history[ticker] = []

        history = self._price_history[ticker]

        # Only record if price changed or first entry
        if not history or history[-1][1] != bid or history[-1][2] != ask:
            history.append((now, bid, ask))

        # Prune old entries (keep 2x the larger window for safety)
        max_window = max(
            self.config.price_movement_window_seconds,
            self.config.live_activity_window_seconds,
        )
        cutoff = now - (max_window * 2)
        self._price_history[ticker] = [(t, b, a) for t, b, a in history if t >= cutoff]

    def _has_recent_price_movement(self, ticker: str, book: OrderBookState) -> bool:
        """Check if price has moved within the configured window.

        Returns True if either bid or ask has changed by at least
        min_price_change_cents within price_movement_window_seconds.
        """
        if not self.config.require_price_movement:
            return True

        # Record current price first
        self._record_price(ticker, book)

        history = self._price_history.get(ticker, [])
        if len(history) < 2:
            # Not enough history yet - no movement detected
            return False

        now = self._clock()
        window_start = now - self.config.price_movement_window_seconds

        # Get prices within window
        prices_in_window = [(t, b, a) for t, b, a in history if t >= window_start]
        if len(prices_in_window) < 2:
            return False

        # Check for movement
        min_bid = min(p[1] for p in prices_in_window)
        max_bid = max(p[1] for p in prices_in_window)
        min_ask = min(p[2] for p in prices_in_window)
        max_ask = max(p[2] for p in prices_in_window)

        bid_moved = (max_bid - min_bid) >= self.config.min_price_change_cents
        ask_moved = (max_ask - min_ask) >= self.config.min_price_change_cents

        return bid_moved or ask_moved

    def _has_live_activity(self, ticker: str, book: OrderBookState) -> bool:
        """Check if market has high real-time activity (for live sports, etc).

        Returns True if the market is "hot" - multiple price changes and
        significant total movement within a short window. This filters for
        markets where exits are more likely to fill.

        Criteria:
        - At least min_price_changes distinct price levels in window
        - At least min_total_movement_cents total movement (bid range + ask range)
        """
        if not self.config.require_live_activity:
            return True

        # Record current price first
        self._record_price(ticker, book)

        history = self._price_history.get(ticker, [])
        if len(history) < 2:
            logger.debug(f"[ACTIVITY SKIP] {ticker}: not enough price history yet")
            return False

        now = self._clock()
        window_start = now - self.config.live_activity_window_seconds

        # Get prices within window
        prices_in_window = [(t, b, a) for t, b, a in history if t >= window_start]

        # Count distinct price changes (number of entries = number of changes + 1)
        num_changes = len(prices_in_window) - 1 if prices_in_window else 0

        # Calculate total movement
        if len(prices_in_window) >= 2:
            min_bid = min(p[1] for p in prices_in_window)
            max_bid = max(p[1] for p in prices_in_window)
            min_ask = min(p[2] for p in prices_in_window)
            max_ask = max(p[2] for p in prices_in_window)
            bid_range = max_bid - min_bid
            ask_range = max_ask - min_ask
            total_movement = bid_range + ask_range
        else:
            total_movement = 0

        # Check if activity meets thresholds
        changes_ok = num_changes >= self.config.min_price_changes
        movement_ok = total_movement >= self.config.min_total_movement_cents

        if not changes_ok or not movement_ok:
            logger.debug(
                f"[ACTIVITY SKIP] {ticker}: {num_changes} changes "
                f"(need {self.config.min_price_changes}), "
                f"{total_movement}c moved (need {self.config.min_total_movement_cents}c) "
                f"in {self.config.live_activity_window_seconds}s"
            )
            return False

        # Market is active!
        logger.info(
            f"[ACTIVITY PASS] {ticker}: {num_changes} changes, "
            f"{total_movement}c movement in {self.config.live_activity_window_seconds}s - TRADING"
        )

        return True

    def _has_price_dropped(self, ticker: str, book: OrderBookState) -> bool:
        """Check if bid price dropped recently (mean reversion opportunity).

        Returns True if the bid dropped by at least min_price_drop_cents
        within the lookback window. This creates a mean reversion entry
        opportunity - buying after a drop, betting on a bounce.
        """
        if not self.config.use_mean_reversion_entry:
            return True

        history = self._price_history.get(ticker, [])
        lookback = self.config.mean_reversion_window_frames

        if len(history) < lookback + 1:
            return False

        # Get bid from N frames ago vs now
        old_bid = history[-(lookback + 1)][1]  # (timestamp, bid, ask)
        current_bid = book.best_bid.price if book.best_bid else 0

        drop = old_bid - current_bid

        if drop >= self.config.min_price_drop_cents:
            logger.info(
                f"[MEAN REVERSION] {ticker}: Bid dropped {drop}c "
                f"({old_bid}c → {current_bid}c) - ENTRY SIGNAL"
            )
            return True

        return False

    def _calculate_taker_exit_profit(
        self,
        trade: "SpreadCaptureTrade",
        book: OrderBookState,
    ) -> Optional[float]:
        """Calculate net profit if we taker exit at current bid.

        Returns:
            Net profit in dollars if we sell at bid, or None if not viable.
        """
        if not book.best_bid or not trade.entry_fill_price:
            return None

        exit_price = book.best_bid.price
        entry_price = trade.entry_fill_price
        size = trade.entry_fill_size

        # Gross P&L
        gross_pnl = (exit_price - entry_price) * size / 100.0

        # Calculate fees (taker for exit)
        entry_fee = kalshi_fee(
            self.config.kalshi_maker_rate
            if self.config.assume_maker_entry
            else self.config.kalshi_taker_rate,
            size,
            entry_price,
        )
        exit_fee = kalshi_fee(
            self.config.kalshi_taker_rate,  # Taker fee for market order
            size,
            exit_price,
        )

        net_pnl = gross_pnl - entry_fee - exit_fee
        return net_pnl

    def _should_taker_exit(
        self,
        trade: "SpreadCaptureTrade",
        book: OrderBookState,
    ) -> bool:
        """Check if we should take liquidity to exit profitably.

        Returns True if:
        - Taker exit is enabled
        - Current bid gives us profit >= taker_exit_min_profit_cents
        """
        if not self.config.use_taker_exit:
            return False

        net_pnl = self._calculate_taker_exit_profit(trade, book)
        if net_pnl is None:
            return False

        min_profit = (
            self.config.taker_exit_min_profit_cents * trade.entry_fill_size / 100.0
        )
        return net_pnl >= min_profit

    def _calculate_optimal_bid(
        self,
        book: OrderBookState,
        time_elapsed: float = 0.0,
    ) -> int:
        """Calculate optimal bid price using dynamic pricing factors.

        Factors considered:
        - Imbalance: Buy pressure → more aggressive bid
        - Microprice: Fair value above mid → improve bid
        - Queue position: Thin queue at best → improve to get priority
        - Time urgency: Near timeout → increase aggression

        Args:
            book: Current orderbook state
            time_elapsed: Seconds since entry order placed (for urgency)

        Returns:
            Optimal bid price in cents
        """
        if not book.best_bid or not book.best_ask:
            return book.best_bid.price if book.best_bid else 50

        best_bid = book.best_bid.price
        best_ask = book.best_ask.price
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2.0

        if spread <= 0:
            return best_bid

        improvement = 0.0

        # Factor 1: Imbalance signal
        # Buy pressure (positive imbalance) → more aggressive bid
        imbalance = self._calculate_imbalance(book)
        imbalance_contrib = imbalance * self.config.imbalance_weight * (spread / 2.0)
        improvement += imbalance_contrib

        # Factor 2: Microprice signal
        # Microprice above mid → fair value favors buyers → improve bid
        microprice = self._calculate_microprice(book)
        microprice_delta = microprice - mid
        microprice_contrib = (
            (microprice_delta / spread) * self.config.microprice_weight * spread
        )
        improvement += microprice_contrib

        # Factor 3: Queue position
        # If queue at best bid is thin, improve to get priority
        depth_at_best = book.best_bid.size
        if depth_at_best < self.config.queue_join_threshold:
            # Thin queue - improve by up to 1 cent
            queue_factor = 1.0 - (depth_at_best / self.config.queue_join_threshold)
            improvement += queue_factor * 1.0

        # Factor 4: Time urgency
        # Near timeout → increase aggression by up to 50%
        if time_elapsed > 0 and self.config.entry_timeout_seconds > 0:
            urgency = min(1.0, time_elapsed / self.config.entry_timeout_seconds)
            improvement *= 1.0 + urgency * 0.5

        # Clamp improvement
        improvement = max(0.0, min(improvement, self.config.max_bid_improvement_cents))

        # Calculate candidate bid
        optimal_bid = best_bid + int(round(improvement))

        # Edge check: ensure minimum expected edge
        expected_edge = best_ask - optimal_bid
        if expected_edge < self.config.min_expected_edge_cents:
            optimal_bid = best_ask - self.config.min_expected_edge_cents

        # Never bid at or above the ask
        if optimal_bid >= best_ask:
            optimal_bid = best_ask - 1

        # Never go below the current best bid
        optimal_bid = max(optimal_bid, best_bid)

        logger.debug(
            f"[DYNAMIC BID] imb={imbalance:.2f} microprice={microprice:.1f} mid={mid:.1f} "
            f"depth={depth_at_best} improvement={improvement:.2f} → bid={optimal_bid}c"
        )

        return optimal_bid

    def _calculate_optimal_ask(
        self,
        book: OrderBookState,
        entry_price: int,
        hold_time: float = 0.0,
        position_size: int = 1,
    ) -> int:
        """Calculate optimal ask price using dynamic pricing factors.

        Factors considered:
        - Imbalance: Sell pressure → lower ask to exit faster
        - Hold time risk: Longer hold → more aggressive exit
        - Position size: Larger inventory → more urgency
        - Minimum profit: Never price below entry + min_edge

        Args:
            book: Current orderbook state
            entry_price: Price we bought at (cents)
            hold_time: Seconds since entry fill
            position_size: Current position size

        Returns:
            Optimal ask price in cents
        """
        if not book.best_bid or not book.best_ask:
            return (
                book.best_ask.price
                if book.best_ask
                else entry_price + self.config.min_expected_edge_cents
            )

        best_bid = book.best_bid.price
        best_ask = book.best_ask.price
        spread = best_ask - best_bid

        if spread <= 0:
            return best_ask

        discount = 0.0

        # Factor 1: Imbalance signal
        # Sell pressure (negative imbalance) → lower ask to exit
        imbalance = self._calculate_imbalance(book)
        # Negative imbalance = more sell pressure = positive discount
        imbalance_contrib = -imbalance * self.config.imbalance_weight * (spread / 2.0)
        discount += max(0.0, imbalance_contrib)

        # Factor 2: Hold time risk
        # Longer hold → more aggressive exit
        if hold_time > 0 and self.config.exit_timeout_seconds > 0:
            hold_urgency = min(1.0, hold_time / self.config.exit_timeout_seconds)
            discount += hold_urgency * 1.0  # Up to 1 cent for hold time

        # Factor 3: Position size
        # Larger position → more urgency to exit
        if position_size > self.config.max_entry_size // 2:
            size_factor = (position_size - self.config.max_entry_size // 2) / (
                self.config.max_entry_size // 2
            )
            discount += size_factor * 0.5  # Up to 0.5 cents for large positions

        # Clamp discount
        discount = max(0.0, min(discount, self.config.max_ask_discount_cents))

        # Calculate candidate ask
        optimal_ask = best_ask - int(round(discount))

        # Minimum profit floor: never price below entry + min_edge
        min_ask = entry_price + self.config.min_expected_edge_cents
        optimal_ask = max(optimal_ask, min_ask)

        # Never go below the bid (would cross the book)
        if optimal_ask <= best_bid:
            optimal_ask = best_bid + 1

        logger.debug(
            f"[DYNAMIC ASK] imb={imbalance:.2f} hold={hold_time:.1f}s size={position_size} "
            f"discount={discount:.2f} min_ask={min_ask} → ask={optimal_ask}c"
        )

        return optimal_ask

    # =========================================================================
    # Kelly Sizing
    # =========================================================================

    def _fetch_bankroll(self) -> Optional[float]:
        """Fetch available balance from Kalshi API."""
        if self.config.bankroll_override is not None:
            return self.config.bankroll_override

        # Use CapitalManager if available
        if self._capital_manager:
            state = self._capital_manager.get_capital_state("kalshi")
            return state.total_balance if state else None

        # Check cache
        now = self._clock()
        if (
            self._cached_bankroll is not None
            and now - self._bankroll_last_fetched < self._bankroll_cache_seconds
        ):
            return self._cached_bankroll

        try:
            import requests
            from src.kalshi.auth import KalshiAuth

            auth = KalshiAuth.from_env()
            host = "https://api.elections.kalshi.com"
            path = "/trade-api/v2/portfolio/balance"

            headers = auth.sign_request("GET", path, "")
            headers["Content-Type"] = "application/json"

            resp = requests.get(f"{host}{path}", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                # Balance is in cents, convert to dollars
                balance_cents = data.get("balance", 0)
                self._cached_bankroll = balance_cents / 100.0
                self._bankroll_last_fetched = now
                logger.debug(f"Fetched bankroll: ${self._cached_bankroll:.2f}")
                return self._cached_bankroll
            else:
                logger.warning(f"Failed to fetch balance: HTTP {resp.status_code}")
                return self._cached_bankroll  # Return stale cache if available

        except Exception as e:
            logger.warning(f"Error fetching bankroll: {e}")
            return self._cached_bankroll

    def _calculate_kelly_size(
        self,
        entry_price: int,
        exit_price: int,
        entry_fee_per: float,
        exit_fee_per: float,
    ) -> int:
        """Calculate position size using Kelly Criterion.

        For spread capture, we use a simplified Kelly approach:
            f* = edge / variance ≈ (p * win - q * loss) / (win * loss)

        Where:
            - win = net profit if trade succeeds (exit - entry - fees)
            - loss = typical loss if trade fails (half the spread due to adverse selection)
            - p = probability of profitable round-trip
            - q = 1 - p

        We apply fractional Kelly and cap at max_bankroll_pct for safety.

        Returns:
            Optimal number of contracts
        """
        bankroll = self._fetch_bankroll()
        if bankroll is None or bankroll <= 0:
            logger.warning("No bankroll available for Kelly sizing")
            return self.config.min_entry_size

        # Calculate win/loss amounts per contract
        gross_profit_cents = exit_price - entry_price
        total_fees_dollars = entry_fee_per + exit_fee_per
        win_dollars = (
            gross_profit_cents / 100.0 - total_fees_dollars
        )  # Net profit on success

        # On failure (adverse selection / stuck), typical loss scenarios:
        # - Mild: spread compresses, we exit at smaller profit or breakeven
        # - Moderate: we improve price a few times, lose 1-3 cents
        # - Severe: force exit at bid, lose most of spread
        # Weighted average loss is typically 20-30% of the spread
        spread_cents = exit_price - entry_price
        loss_dollars = (
            spread_cents * 0.25
        ) / 100.0 + total_fees_dollars  # ~25% of spread + fees

        if win_dollars <= 0 or loss_dollars <= 0:
            return self.config.min_entry_size

        # Kelly calculation using expected value formula
        p = self.config.kelly_win_prob
        q = 1.0 - p

        # Expected value per dollar risked
        # EV = p * win - q * loss
        ev = p * win_dollars - q * loss_dollars

        if ev <= 0:
            # Negative edge, don't trade (shouldn't happen if we passed edge check)
            return self.config.min_entry_size

        # Kelly fraction = EV / (win * loss) for unequal payoffs
        # Simplified: f* = (p * win - q * loss) / (win if loss == win else max variance approx)
        # For spread capture: f* ≈ edge / cost_per_contract
        cost_per_contract = entry_price / 100.0
        kelly_full = ev / cost_per_contract

        # Apply fractional Kelly for safety (typically 0.25-0.5)
        kelly_adj = kelly_full * self.config.kelly_fraction

        # Cap at max bankroll percentage per trade
        kelly_adj = min(kelly_adj, self.config.kelly_max_bankroll_pct)
        kelly_adj = max(kelly_adj, 0)  # Ensure non-negative

        # Calculate size: how many contracts can we buy with kelly_adj of bankroll
        dollars_to_risk = bankroll * kelly_adj
        size = int(dollars_to_risk / cost_per_contract) if cost_per_contract > 0 else 0

        # Apply min/max bounds
        size = max(self.config.min_entry_size, min(size, self.config.max_entry_size))

        logger.debug(
            f"Kelly: bankroll=${bankroll:.2f}, win=${win_dollars:.4f}, loss=${loss_dollars:.4f}, "
            f"EV=${ev:.4f}, kelly={kelly_adj:.4f}, size={size}"
        )

        return size

    # =========================================================================
    # Opportunity Analysis
    # =========================================================================

    def analyze_opportunity(
        self, ticker: str, book: OrderBookState
    ) -> Optional[DepthOpportunity]:
        """Analyze orderbook for spread capture opportunity."""
        # Feed snapshot to velocity estimator (works in both polling and WS modes)
        from src.fill_time.models import SnapshotRecord

        snap = SnapshotRecord.from_orderbook_state(book)
        self._velocity_estimator.process_snapshot(snap)
        self._snapshot_collector._on_update(ticker, book)

        # --- Guard checks ---

        # Ticker prefix filter (only trade backtest-validated markets)
        if self.config.allowed_ticker_prefixes is not None:
            if not any(
                ticker.startswith(p) for p in self.config.allowed_ticker_prefixes
            ):
                return None

        # Daily loss limit
        if self._risk_manager:
            if not self._risk_manager.is_trading_allowed():
                return None
        elif self._daily_loss_limit_hit:
            return None

        # Balance check (avoid spam when account is depleted)
        if self._capital_manager:
            if self._capital_manager.get_deployable_capital("kalshi") <= 0:
                return None
        elif hasattr(self, "_insufficient_balance_until"):
            if self._clock() < self._insufficient_balance_until:
                return None
            else:
                delattr(self, "_insufficient_balance_until")

        # Circuit breaker
        if self._circuit_breaker_active:
            if self._clock() < self._circuit_breaker_until:
                return None
            # Cooldown expired
            self._circuit_breaker_active = False
            self._consecutive_losses = 0
            logger.info("Circuit breaker cooldown expired, resuming trading")

        # Max concurrent positions
        active_count = sum(1 for t in self._trades.values() if t.is_active())
        if active_count >= self.config.max_concurrent_positions:
            return None

        # Per-ticker limit
        ticker_active = sum(
            1 for t in self._trades.values() if t.ticker == ticker and t.is_active()
        )
        if ticker_active >= self.config.max_positions_per_ticker:
            return None

        # Cooldown between trades on same ticker
        last_time = self._last_trade_time.get(ticker, 0)
        if self._clock() - last_time < self.config.cooldown_between_trades_seconds:
            return None

        # --- Book checks ---

        if not book.best_bid or not book.best_ask:
            return None

        # Always record price for movement tracking (even if we don't trade)
        self._record_price(ticker, book)

        spread = book.spread
        if spread is None:
            return None

        # Spread in range
        if spread < self.config.min_spread_cents:
            return None
        if spread > self.config.max_spread_cents:
            return None

        # Price movement filter - only enter if market is active
        if not self._has_recent_price_movement(ticker, book):
            return None

        # Live activity filter - only trade on "hot" markets with lots of action
        if not self._has_live_activity(ticker, book):
            return None

        # Mean reversion filter - only enter after price dropped (betting on bounce)
        if self.config.use_mean_reversion_entry:
            if not self._has_price_dropped(ticker, book):
                return None

        # Mid price in range
        mid = book.mid_price
        if mid is None:
            return None
        if (
            mid < self.config.min_mid_price_cents
            or mid > self.config.max_mid_price_cents
        ):
            return None
        # High probability filter - only trade when mid > threshold (safer if stuck)
        if self.config.prefer_high_prob and mid < self.config.high_prob_min_mid_cents:
            return None

        # Combined activity filter (volume + movement score together)
        if self.config.use_combined_filter:
            activity_score = self._calculate_activity_score(book)
            if activity_score < self.config.min_activity_score:
                return None
            movement_score = self._calculate_movement_score(book)
            logger.debug(
                f"[ACTIVITY] {ticker}: combined={activity_score:.2f} "
                f"(vol={book.volume_24h}, movement={movement_score:.2f}, "
                f"depth={book.best_bid.size}/{book.best_ask.size})"
            )
        else:
            # Separate volume and movement filters (legacy)
            # Volume filter (skip dead markets in live mode)
            if book.volume_24h < self.config.min_volume_24h:
                return None
            # Max volume filter (target low-liquidity markets)
            if (
                self.config.max_volume_24h is not None
                and book.volume_24h > self.config.max_volume_24h
            ):
                return None

            # Movement likelihood filter
            if self.config.min_movement_score > 0:
                movement_score = self._calculate_movement_score(book)
                if movement_score < self.config.min_movement_score:
                    return None
                logger.debug(
                    f"[MOVEMENT] {ticker}: score={movement_score:.2f} "
                    f"(vol={book.volume_24h}, depth={book.best_bid.size}/{book.best_ask.size})"
                )

        # Depth at best on both sides
        if book.best_bid.size < self.config.min_depth_at_best:
            return None
        if book.best_ask.size < self.config.min_depth_at_best:
            return None

        # --- Fee check: compute net edge per contract ---
        if self.config.use_dynamic_pricing:
            entry_price = self._calculate_optimal_bid(book)
            exit_price = self._calculate_optimal_ask(book, entry_price)
        else:
            entry_price = book.best_bid.price + self.config.bid_improvement_cents
            exit_price = book.best_ask.price - self.config.ask_discount_cents

        # Ensure entry < exit
        if entry_price >= exit_price:
            return None

        entry_rate = (
            self.config.kalshi_maker_rate
            if self.config.assume_maker_entry
            else self.config.kalshi_taker_rate
        )
        exit_rate = (
            self.config.kalshi_maker_rate
            if self.config.assume_maker_exit
            else self.config.kalshi_taker_rate
        )

        # Fee per contract (use 1 contract for per-contract check)
        entry_fee_per = kalshi_fee(entry_rate, 1, entry_price)
        exit_fee_per = kalshi_fee(exit_rate, 1, exit_price)

        gross_edge_per = (exit_price - entry_price) / 100.0
        net_edge_per = gross_edge_per - entry_fee_per - exit_fee_per

        if net_edge_per <= 0:
            return None

        # --- Size ---
        if self.config.use_kelly_sizing:
            # Kelly Criterion sizing based on bankroll and edge
            size = self._calculate_kelly_size(
                entry_price=entry_price,
                exit_price=exit_price,
                entry_fee_per=entry_fee_per,
                exit_fee_per=exit_fee_per,
            )
        else:
            # Legacy fixed sizing based on depth and risk constraints
            size = self._position_sizer.calculate_size(
                book=book,
                side=Side.BID,  # Buying at the bid
                current_position=self.get_position(ticker),
                max_position=self.config.max_entry_size,
            )

        size = min(size, self.config.max_entry_size)
        if size < self.config.min_entry_size:
            return None

        # Check per-trade loss limit
        # Worst case: buy at entry, sell at 1 cent
        worst_case_loss = (entry_price - 1) * size / 100.0
        if worst_case_loss > self.config.max_loss_per_trade_dollars:
            size = int(
                self.config.max_loss_per_trade_dollars / ((entry_price - 1) / 100.0)
            )
            if size < self.config.min_entry_size:
                return None

        # Risk manager portfolio-wide gate (includes correlation if wired)
        if self._risk_manager:
            allowed, reason = self._risk_manager.can_trade(ticker, "buy", size)
            if not allowed:
                logger.debug(f"[RISK] {ticker}: blocked by RiskManager - {reason}")
                return None

        # Standalone correlation check (when tracker present but no risk manager)
        if self._correlation_tracker and not self._risk_manager:
            from src.core.models import Position as _Position

            active_positions = {
                t.ticker: _Position(
                    ticker=t.ticker,
                    size=t.entry_fill_size,
                    entry_price=float(t.entry_fill_price or 0),
                    current_price=float(t.entry_fill_price or 0),
                )
                for t in self._trades.values()
                if t.is_active() and t.entry_fill_size > 0
            }
            allowed, reason = self._correlation_tracker.check_exposure(
                positions=active_positions,
                proposed_ticker=ticker,
                proposed_size=size,
                max_total_position=self.config.max_concurrent_positions
                * self.config.max_entry_size,
            )
            if not allowed:
                logger.debug(f"[CORRELATION] {ticker}: blocked - {reason}")
                return None

        # Expected net profit (EV-adjusted for fill probability)
        rt = self._fill_estimator.estimate_round_trip_time(
            book,
            entry_price,
            exit_price,
            size,
            entry_fee_rate=entry_rate,
            exit_fee_rate=exit_rate,
        )
        expected_net_profit = net_edge_per * size * rt.p_round_trip_completes

        # Skip if entry or exit unlikely to fill within timeout
        if rt.entry.p_fill_60s < self._fill_time_config.min_entry_fill_prob_60s:
            return None
        if rt.exit.p_fill_60s < self._fill_time_config.min_exit_fill_prob_60s:
            return None

        metrics = DepthMetrics.from_orderbook(book)

        reasons = [
            f"spread={spread}c",
            f"mid={mid:.1f}c",
            f"bid_depth={book.best_bid.size}",
            f"ask_depth={book.best_ask.size}",
            f"net_edge={net_edge_per * 100:.2f}c/contract",
            f"size={size}",
            f"p_rt={rt.p_round_trip_completes:.2f}",
            f"p_entry_60s={rt.entry.p_fill_60s:.2f}",
            f"expected_profit=${expected_net_profit:.4f}",
        ]

        return DepthOpportunity(
            ticker=ticker,
            timestamp=self._clock(),
            metrics=metrics,
            opportunity_type="spread_capture",
            entry_side="buy",
            entry_price=entry_price,
            entry_size=size,
            target_price=exit_price,
            score=expected_net_profit,
            reasons=reasons,
        )

    # =========================================================================
    # Trade Execution
    # =========================================================================

    def _reserve_opportunity(self, opportunity: DepthOpportunity) -> None:
        """Create a placeholder trade synchronously so concurrency guards
        work within a single poll cycle."""
        trade = SpreadCaptureTrade(
            ticker=opportunity.ticker,
            state=SpreadCaptureState.PENDING_ENTRY,
            entry_price=opportunity.entry_price,
            entry_time=self._clock(),
            spread_at_entry=opportunity.metrics.spread_cents or 0,
            mid_at_entry=opportunity.metrics.mid_price or 0,
            depth_at_entry_bid=opportunity.metrics.bid_depth_at_best,
            depth_at_entry_ask=opportunity.metrics.ask_depth_at_best,
            _clock=self._clock,
        )
        self._trades[trade.trade_id] = trade
        # Stash trade_id on the opportunity so execute_opportunity can find it
        opportunity._reserved_trade_id = trade.trade_id

    async def execute_opportunity(self, opportunity: DepthOpportunity) -> None:
        """Execute a spread capture round-trip."""
        ticker = opportunity.ticker
        book = self.get_orderbook(ticker)

        # Pick up the trade created by _reserve_opportunity, or create one
        trade_id = getattr(opportunity, "_reserved_trade_id", None)
        if trade_id and trade_id in self._trades:
            trade = self._trades[trade_id]
        else:
            trade = SpreadCaptureTrade(
                ticker=ticker,
                state=SpreadCaptureState.PENDING_ENTRY,
                entry_price=opportunity.entry_price,
                entry_time=self._clock(),
                spread_at_entry=opportunity.metrics.spread_cents or 0,
                mid_at_entry=opportunity.metrics.mid_price or 0,
                depth_at_entry_bid=opportunity.metrics.bid_depth_at_best,
                depth_at_entry_ask=opportunity.metrics.ask_depth_at_best,
                _clock=self._clock,
            )
            self._trades[trade.trade_id] = trade

        logger.info(
            f"[SPREAD] {ticker}: Entry buy {opportunity.entry_size}x @ {opportunity.entry_price}c "
            f"(spread={trade.spread_at_entry}c, target_exit={opportunity.target_price}c)"
        )

        self._log_event(
            "spread_entry_attempt",
            {
                "trade_id": trade.trade_id,
                "ticker": ticker,
                "entry_price": opportunity.entry_price,
                "entry_size": opportunity.entry_size,
                "target_exit": opportunity.target_price,
                "spread_at_entry": trade.spread_at_entry,
                "score": opportunity.score,
                "reasons": opportunity.reasons,
            },
        )

        try:
            # --- Capital Reservation ---
            if self._capital_manager:
                cost = opportunity.entry_price * opportunity.entry_size / 100.0
                res_id = f"sc_{trade.trade_id}"
                reserved = self._capital_manager.reserve(
                    reservation_id=res_id,
                    exchange="kalshi",
                    amount=cost,
                    purpose=f"spread_capture entry {ticker}",
                    ttl_seconds=self.config.entry_timeout_seconds
                    + self.config.exit_timeout_seconds
                    + 60,
                )
                if not reserved:
                    logger.warning(
                        f"[SPREAD] {ticker}: Capital reservation failed (need ${cost:.2f})"
                    )
                    trade.state = SpreadCaptureState.CANCELLED
                    return
                trade.capital_reservation_id = res_id

            # --- Entry Phase ---
            entry_id = await self.place_order(
                ticker=ticker,
                side="buy",
                price=opportunity.entry_price,
                size=opportunity.entry_size,
                order_type="entry",
            )

            if not entry_id:
                logger.warning(f"[SPREAD] {ticker}: Entry order failed to place")
                trade.state = SpreadCaptureState.CANCELLED
                # Set cooldown to prevent immediate retry spam
                self._last_trade_time[ticker] = self._clock()
                # Release capital reservation on failure
                if self._capital_manager and trade.capital_reservation_id:
                    self._capital_manager.release(trade.capital_reservation_id)
                # If insufficient balance, pause all trading for 60 seconds
                if (
                    not self._capital_manager
                    and getattr(self, "_last_order_error", "") == "insufficient_balance"
                ):
                    logger.warning(
                        "[SPREAD] Insufficient balance - pausing new entries for 60s"
                    )
                    self._insufficient_balance_until = self._clock() + 60.0
                return

            trade.entry_order_id = entry_id
            # Register mapping BEFORE waiting — guarantees fill routes correctly
            self._order_to_trade[entry_id] = trade.trade_id

            # Entry with optional repricing loop
            if self.config.use_dynamic_pricing:
                entry_filled = await self._wait_for_fill_with_repricing(
                    ticker=ticker,
                    order_id=entry_id,
                    trade=trade,
                    is_entry=True,
                    size=opportunity.entry_size,
                )
            else:
                entry_filled = await self.wait_for_fill(
                    entry_id, timeout=self.config.entry_timeout_seconds
                )

            if not entry_filled:
                logger.info(f"[SPREAD] {ticker}: Entry timeout, cancelling")
                await self.cancel_order(entry_id)
                trade.state = SpreadCaptureState.CANCELLED
                if (
                    self._capital_manager
                    and hasattr(trade, "capital_reservation_id")
                    and trade.capital_reservation_id
                ):
                    self._capital_manager.release(trade.capital_reservation_id)
                self._log_event("spread_entry_timeout", trade.to_dict())
                return

            # Entry filled
            trade.state = SpreadCaptureState.OPEN
            trade.entry_fill_price = opportunity.entry_price  # Assume limit price
            trade.entry_fill_size = opportunity.entry_size
            trade.entry_fill_time = self._clock()

            logger.info(
                f"[SPREAD] {ticker}: Entry FILLED {trade.entry_fill_size}x @ "
                f"{trade.entry_fill_price}c"
            )

            # Register open position with risk manager
            if self._risk_manager:
                from src.core.models import Position as _Position

                self._risk_manager.register_position(
                    ticker,
                    _Position(
                        ticker=ticker,
                        size=trade.entry_fill_size,
                        entry_price=float(trade.entry_fill_price),
                        current_price=float(trade.entry_fill_price),
                    ),
                )

            # --- Exit Phase ---
            # Re-read book for current best ask
            current_book = self.get_orderbook(ticker)

            # Undercut exit strategy: place ask at entry + small profit
            # This undercuts everyone else and fills faster on volatile markets
            if self.config.use_undercut_exit:
                exit_price = trade.entry_fill_price + self.config.undercut_profit_cents
                # Make sure we're still below the current ask (undercutting)
                if current_book and current_book.best_ask:
                    if exit_price >= current_book.best_ask.price:
                        # We're not undercutting, use ask - 1
                        exit_price = current_book.best_ask.price - 1
                logger.info(
                    f"[UNDERCUT] {ticker}: Exit at {exit_price}c "
                    f"(entry {trade.entry_fill_price}c + {self.config.undercut_profit_cents}c profit)"
                )
            elif self.config.use_dynamic_pricing and current_book:
                exit_price = self._calculate_optimal_ask(
                    current_book,
                    entry_price=trade.entry_fill_price,
                    hold_time=0.0,
                    position_size=trade.entry_fill_size,
                )
            elif current_book and current_book.best_ask:
                exit_price = (
                    current_book.best_ask.price - self.config.ask_discount_cents
                )
            else:
                exit_price = opportunity.target_price

            # Adverse selection: in dry run, shift exit price down to simulate
            # the book moving against us between entry and exit
            if self.dry_run:
                spread = trade.spread_at_entry
                max_adverse = self.config.adverse_selection_cents
                if max_adverse is None:
                    max_adverse = max(1, spread // 3)
                if max_adverse > 0:
                    adverse_shift = self._fill_rng.randint(0, max_adverse)
                    exit_price -= adverse_shift

            # Don't post exit below entry (would lock in a loss)
            if exit_price <= trade.entry_fill_price:
                exit_price = trade.entry_fill_price + 1

            trade.exit_price = exit_price
            trade.current_exit_price = exit_price
            trade.state = SpreadCaptureState.PENDING_EXIT

            exit_id = await self.place_order(
                ticker=ticker,
                side="sell",
                price=exit_price,
                size=trade.entry_fill_size,
                order_type="exit",
            )

            if not exit_id:
                logger.warning(
                    f"[SPREAD] {ticker}: Exit order failed, entering stuck management"
                )
                trade.state = SpreadCaptureState.STUCK
                trade.stuck_since = self._clock()
                self._launch_stuck_task(trade)
                return

            trade.exit_order_id = exit_id
            # Register mapping BEFORE waiting
            self._order_to_trade[exit_id] = trade.trade_id

            logger.info(
                f"[SPREAD] {ticker}: Exit posted sell {trade.entry_fill_size}x @ {exit_price}c"
            )

            # Exit with optional repricing loop
            if self.config.use_dynamic_pricing:
                exit_filled = await self._wait_for_fill_with_repricing(
                    ticker=ticker,
                    order_id=exit_id,
                    trade=trade,
                    is_entry=False,
                    size=trade.entry_fill_size,
                )
            else:
                exit_filled = await self._wait_for_exit_with_stoploss(
                    ticker=ticker,
                    order_id=exit_id,
                    trade=trade,
                )

            if exit_filled:
                # Success!
                trade.exit_fill_price = trade.current_exit_price
                trade.exit_fill_size = trade.entry_fill_size
                self._complete_trade(trade)
            else:
                # Check if stop-loss conditions are already met — skip stuck management
                should_force_exit = False
                if trade.entry_fill_price and self.config.cut_loss_on_adverse_move:
                    book = self.get_orderbook(ticker)
                    if book and book.best_bid:
                        loss_cents = trade.entry_fill_price - book.best_bid.price
                        if loss_cents > self.config.max_loss_per_position_cents:
                            should_force_exit = True
                if trade.hold_time() > self.config.max_hold_time_seconds:
                    should_force_exit = True

                if should_force_exit:
                    logger.warning(
                        f"[LOSS CUT] {ticker}: Immediate force exit "
                        f"(hold={trade.hold_time():.0f}s)"
                    )
                    await self._force_exit_stuck(trade)
                else:
                    # Normal timeout - enter stuck management
                    logger.info(
                        f"[SPREAD] {ticker}: Exit timeout after "
                        f"{self.config.exit_timeout_seconds}s, entering stuck management"
                    )
                    trade.state = SpreadCaptureState.STUCK
                    trade.stuck_since = self._clock()
                    self._launch_stuck_task(trade)

        except Exception as e:
            logger.error(
                f"[SPREAD] {ticker}: Error during execution: {e}", exc_info=True
            )
            # Try to clean up
            if trade.state in (
                SpreadCaptureState.OPEN,
                SpreadCaptureState.PENDING_EXIT,
            ):
                await self._force_exit_stuck(trade)
            else:
                trade.state = SpreadCaptureState.CANCELLED

    async def _wait_for_exit_with_stoploss(
        self,
        ticker: str,
        order_id: str,
        trade: SpreadCaptureTrade,
    ) -> bool:
        """Wait for exit fill, checking stop-loss every 5 seconds.

        Used when dynamic pricing is disabled. Replaces the simple
        wait_for_fill with periodic stop-loss and max-hold-time checks.

        Returns:
            True if filled, False if timeout or stop-loss triggered.
        """
        check_interval = 5.0
        start_time = self._clock()
        timeout = self.config.exit_timeout_seconds
        event = self._fill_events.get(order_id)
        if event is None:
            event = asyncio.Event()
            self._fill_events[order_id] = event

        try:
            while self._clock() - start_time < timeout:
                result = await self._wait_for_event(event, check_interval)
                if result:
                    return True  # Filled

                # Stop-loss check
                if self.config.cut_loss_on_adverse_move and trade.entry_fill_price:
                    book = self.get_orderbook(ticker)
                    if book and book.best_bid:
                        loss_cents = trade.entry_fill_price - book.best_bid.price
                        if loss_cents > self.config.max_loss_per_position_cents:
                            logger.warning(
                                f"[LOSS CUT] {ticker}: Stop-loss during exit wait "
                                f"(loss={loss_cents}c > {self.config.max_loss_per_position_cents}c)"
                            )
                            await self.cancel_order(order_id)
                            self._order_to_trade.pop(order_id, None)
                            return False

                # Max hold time check
                if trade.hold_time() > self.config.max_hold_time_seconds:
                    logger.warning(
                        f"[LOSS CUT] {ticker}: Max hold time during exit wait "
                        f"({trade.hold_time():.0f}s > {self.config.max_hold_time_seconds}s)"
                    )
                    await self.cancel_order(order_id)
                    self._order_to_trade.pop(order_id, None)
                    return False

            return False  # Normal timeout
        finally:
            self._fill_events.pop(order_id, None)

    async def _wait_for_fill_with_repricing(
        self,
        ticker: str,
        order_id: str,
        trade: SpreadCaptureTrade,
        is_entry: bool,
        size: int,
    ) -> bool:
        """Wait for fill with periodic repricing based on orderbook state.

        This implements the repricing loop for dynamic pricing:
        - Check fill every second
        - Recalculate optimal price every reprice_interval_seconds
        - Cancel and replace if price moved >= reprice_threshold_cents
        - Preserve minimum expected edge

        Args:
            ticker: Market ticker
            order_id: Current order ID
            trade: Trade being executed
            is_entry: True for entry orders, False for exit orders
            size: Order size

        Returns:
            True if filled, False if timeout
        """
        timeout = (
            self.config.entry_timeout_seconds
            if is_entry
            else self.config.exit_timeout_seconds
        )
        start_time = self._clock()
        last_reprice_time = start_time
        current_order_id = order_id
        current_price = trade.entry_price if is_entry else trade.current_exit_price

        while self._clock() - start_time < timeout:
            # Check if filled
            if current_order_id in self._fill_events:
                event = self._fill_events[current_order_id]
                result = await self._wait_for_event(event, 1.0)
                if result:
                    return True

            time_elapsed = self._clock() - start_time
            time_since_reprice = self._clock() - last_reprice_time

            # Check for taker exit opportunity (exit orders only)
            if (
                not is_entry
                and time_since_reprice >= self.config.taker_exit_check_interval_seconds
            ):
                book = self.get_orderbook(ticker)
                if book and self._should_taker_exit(trade, book):
                    profit = self._calculate_taker_exit_profit(trade, book)
                    logger.info(
                        f"[TAKER EXIT] {ticker}: Taking liquidity at bid {book.best_bid.price}c "
                        f"(profit=${profit:.2f})"
                    )
                    # Cancel passive order and place taker order
                    await self.cancel_order(current_order_id)
                    self._order_to_trade.pop(current_order_id, None)
                    self._fill_events.pop(current_order_id, None)

                    taker_order_id = await self.place_order(
                        ticker=ticker,
                        side="sell",
                        price=book.best_bid.price,  # Hit the bid
                        size=size,
                        order_type="exit",
                    )
                    if taker_order_id:
                        trade.exit_order_id = taker_order_id
                        trade.current_exit_price = book.best_bid.price
                        self._order_to_trade[taker_order_id] = trade.trade_id
                        self._fill_events[taker_order_id] = asyncio.Event()
                        self._log_event(
                            "taker_exit",
                            {
                                "trade_id": trade.trade_id,
                                "ticker": ticker,
                                "price": book.best_bid.price,
                                "size": size,
                                "expected_profit": profit,
                            },
                        )
                        # Wait briefly for fill (taker should be instant)
                        result = await self._wait_for_event(
                            self._fill_events[taker_order_id], 5.0
                        )
                        if result:
                            return True
                        logger.warning(
                            f"[TAKER EXIT] {ticker}: Taker order not filled immediately"
                        )
                        current_order_id = taker_order_id
                        current_price = book.best_bid.price

            # Stop-loss check during exit wait (don't wait for STUCK)
            if (
                not is_entry
                and self.config.cut_loss_on_adverse_move
                and trade.entry_fill_price
            ):
                book = self.get_orderbook(ticker)
                if book and book.best_bid:
                    loss_cents = trade.entry_fill_price - book.best_bid.price
                    if loss_cents > self.config.max_loss_per_position_cents:
                        logger.warning(
                            f"[LOSS CUT] {ticker}: Stop-loss triggered during exit wait "
                            f"(entry={trade.entry_fill_price}c, bid={book.best_bid.price}c, "
                            f"loss={loss_cents}c > {self.config.max_loss_per_position_cents}c)"
                        )
                        # Cancel passive exit and force exit at bid
                        await self.cancel_order(current_order_id)
                        self._order_to_trade.pop(current_order_id, None)
                        self._fill_events.pop(current_order_id, None)
                        return False  # Caller will enter stuck management -> force exit

            # Max hold time check during exit wait
            if not is_entry and trade.hold_time() > self.config.max_hold_time_seconds:
                logger.warning(
                    f"[LOSS CUT] {ticker}: Max hold time exceeded during exit wait "
                    f"({trade.hold_time():.0f}s > {self.config.max_hold_time_seconds}s)"
                )
                await self.cancel_order(current_order_id)
                self._order_to_trade.pop(current_order_id, None)
                self._fill_events.pop(current_order_id, None)
                return False

            # Check if we should reprice
            if time_since_reprice >= self.config.reprice_interval_seconds:
                book = self.get_orderbook(ticker)
                if book and book.best_bid and book.best_ask:
                    # Calculate new optimal price
                    if is_entry:
                        new_price = self._calculate_optimal_bid(book, time_elapsed)
                    else:
                        new_price = self._calculate_optimal_ask(
                            book,
                            entry_price=trade.entry_fill_price or trade.entry_price,
                            hold_time=trade.hold_time(),
                            position_size=size,
                        )

                    # Check if price change exceeds threshold
                    price_change = abs(new_price - current_price)
                    if price_change >= self.config.reprice_threshold_cents:
                        logger.info(
                            f"[REPRICE] {ticker}: {'entry' if is_entry else 'exit'} "
                            f"{current_price}c → {new_price}c (Δ{price_change}c)"
                        )

                        # Cancel current order
                        await self.cancel_order(current_order_id)
                        self._order_to_trade.pop(current_order_id, None)
                        self._fill_events.pop(current_order_id, None)

                        # Place new order at new price
                        side = "buy" if is_entry else "sell"
                        new_order_id = await self.place_order(
                            ticker=ticker,
                            side=side,
                            price=new_price,
                            size=size,
                            order_type="entry" if is_entry else "exit",
                        )

                        if new_order_id:
                            current_order_id = new_order_id
                            current_price = new_price
                            self._order_to_trade[new_order_id] = trade.trade_id
                            self._fill_events[new_order_id] = asyncio.Event()

                            # Update trade tracking
                            if is_entry:
                                trade.entry_order_id = new_order_id
                                trade.entry_price = new_price
                            else:
                                trade.exit_order_id = new_order_id
                                trade.current_exit_price = new_price

                            self._log_event(
                                "reprice",
                                {
                                    "trade_id": trade.trade_id,
                                    "ticker": ticker,
                                    "is_entry": is_entry,
                                    "old_price": current_price - price_change
                                    if new_price > current_price
                                    else current_price + price_change,
                                    "new_price": new_price,
                                    "time_elapsed": time_elapsed,
                                },
                            )
                        else:
                            logger.warning(
                                f"[REPRICE] {ticker}: Failed to place new order"
                            )
                            # Keep using old order (which was cancelled)
                            return False

                last_reprice_time = self._clock()

            await self._sleep(1.0)

        return False

    # =========================================================================
    # Stuck Management
    # =========================================================================

    def _launch_stuck_task(self, trade: SpreadCaptureTrade) -> None:
        """Launch background task to manage a stuck trade."""
        task = asyncio.create_task(self._manage_stuck_trade(trade))
        self._stuck_tasks[trade.trade_id] = task

    async def _manage_stuck_trade(self, trade: SpreadCaptureTrade) -> None:
        """Background loop to improve exit price on a stuck trade."""
        ticker = trade.ticker

        logger.info(
            f"[STUCK] {ticker}: Starting stuck management for trade {trade.trade_id}"
        )

        try:
            while (
                trade.state == SpreadCaptureState.STUCK
                and self._running
                and trade.improvement_count < self.config.stuck_max_improvements
            ):
                await self._sleep(self.config.stuck_improvement_interval_seconds)

                if trade.state != SpreadCaptureState.STUCK:
                    break

                book = self.get_orderbook(ticker)
                if not book or not book.best_bid or not book.best_ask:
                    continue

                # Check for taker exit opportunity first
                if self._should_taker_exit(trade, book):
                    profit = self._calculate_taker_exit_profit(trade, book)
                    logger.info(
                        f"[STUCK TAKER EXIT] {ticker}: Taking liquidity at bid {book.best_bid.price}c "
                        f"(profit=${profit:.2f})"
                    )
                    # Cancel current order and place taker
                    if trade.exit_order_id:
                        await self.cancel_order(trade.exit_order_id)
                        self._order_to_trade.pop(trade.exit_order_id, None)

                    taker_order_id = await self.place_order(
                        ticker=ticker,
                        side="sell",
                        price=book.best_bid.price,
                        size=trade.entry_fill_size,
                        order_type="exit",
                    )
                    if taker_order_id:
                        trade.exit_order_id = taker_order_id
                        trade.current_exit_price = book.best_bid.price
                        self._order_to_trade[taker_order_id] = trade.trade_id
                        self._log_event(
                            "stuck_taker_exit",
                            {
                                "trade_id": trade.trade_id,
                                "ticker": ticker,
                                "price": book.best_bid.price,
                                "expected_profit": profit,
                            },
                        )
                        # Wait for fill
                        await self._sleep(2.0)
                        if trade.state == SpreadCaptureState.CLOSED:
                            return
                    continue

                # Check if we should hedge
                if self._should_hedge(trade):
                    complement = self._find_complement_ticker(ticker)
                    if complement:
                        complement_book = self.get_orderbook(complement)
                        if complement_book and complement_book.best_bid:
                            logger.info(
                                f"[HEDGE] {ticker}: Hedging with complement {complement} "
                                f"at bid {complement_book.best_bid.price}c"
                            )
                            # Place hedge order at complement bid
                            hedge_id = await self.place_order(
                                ticker=complement,
                                side="buy",
                                price=complement_book.best_bid.price,
                                size=trade.entry_fill_size,
                                order_type="entry",  # Hedge is effectively a new entry
                            )
                            if hedge_id:
                                self._log_event(
                                    "hedge_placed",
                                    {
                                        "trade_id": trade.trade_id,
                                        "original_ticker": ticker,
                                        "hedge_ticker": complement,
                                        "hedge_price": complement_book.best_bid.price,
                                        "hedge_size": trade.entry_fill_size,
                                    },
                                )

                # Check max hold time - cut loss if held too long
                hold_time = trade.hold_time()
                if hold_time > self.config.max_hold_time_seconds:
                    logger.warning(
                        f"[LOSS CUT] {ticker}: Max hold time exceeded ({hold_time:.0f}s > "
                        f"{self.config.max_hold_time_seconds}s), force exiting"
                    )
                    await self._force_exit_stuck(trade)
                    return

                # Check max loss per position
                if self.config.cut_loss_on_adverse_move and trade.entry_fill_price:
                    potential_loss_cents = trade.entry_fill_price - book.best_bid.price
                    if potential_loss_cents > self.config.max_loss_per_position_cents:
                        logger.warning(
                            f"[LOSS CUT] {ticker}: Max loss exceeded "
                            f"(entry={trade.entry_fill_price}c, bid={book.best_bid.price}c, "
                            f"loss={potential_loss_cents}c > {self.config.max_loss_per_position_cents}c), "
                            f"force exiting"
                        )
                        await self._force_exit_stuck(trade)
                        return

                # Check if spread compressed below minimum remaining
                current_spread = book.best_ask.price - trade.entry_fill_price
                if current_spread < self.config.stuck_min_remaining_spread_cents:
                    logger.info(
                        f"[STUCK] {ticker}: Spread compressed to {current_spread}c, force exiting"
                    )
                    await self._force_exit_stuck(trade)
                    return

                # Cancel current exit order
                if trade.exit_order_id:
                    await self.cancel_order(trade.exit_order_id)
                    self._order_to_trade.pop(trade.exit_order_id, None)

                # Improve price: move N cents closer to mid
                new_exit_price = (
                    trade.current_exit_price - self.config.stuck_improvement_cents
                )

                # Don't go below entry + minimum remaining spread
                min_exit = (
                    trade.entry_fill_price
                    + self.config.stuck_min_remaining_spread_cents
                )
                if new_exit_price < min_exit:
                    new_exit_price = min_exit

                # Don't go below best bid (that would cross)
                if new_exit_price <= book.best_bid.price:
                    logger.info(
                        f"[STUCK] {ticker}: Can't improve further (would cross bid), force exiting"
                    )
                    await self._force_exit_stuck(trade)
                    return

                trade.current_exit_price = new_exit_price
                trade.improvement_count += 1

                logger.info(
                    f"[STUCK] {ticker}: Improvement #{trade.improvement_count}: "
                    f"new exit @ {new_exit_price}c"
                )

                # Place improved exit order
                exit_id = await self.place_order(
                    ticker=ticker,
                    side="sell",
                    price=new_exit_price,
                    size=trade.entry_fill_size,
                    order_type="exit",
                )

                if not exit_id:
                    continue

                trade.exit_order_id = exit_id
                self._order_to_trade[exit_id] = trade.trade_id

                filled = await self.wait_for_fill(
                    exit_id, timeout=self.config.stuck_improvement_interval_seconds
                )

                if filled:
                    trade.exit_fill_price = new_exit_price
                    trade.exit_fill_size = trade.entry_fill_size
                    self._complete_trade(trade)
                    return

            # Max improvements exhausted
            if trade.state == SpreadCaptureState.STUCK:
                logger.warning(
                    f"[STUCK] {ticker}: Max improvements ({self.config.stuck_max_improvements}) "
                    f"exhausted, force exiting"
                )
                await self._force_exit_stuck(trade)

        except asyncio.CancelledError:
            logger.info(f"[STUCK] {ticker}: Stuck task cancelled")
            if trade.state == SpreadCaptureState.STUCK:
                await self._force_exit_stuck(trade)
        except Exception as e:
            logger.error(
                f"[STUCK] {ticker}: Error in stuck management: {e}", exc_info=True
            )
            if trade.state == SpreadCaptureState.STUCK:
                await self._force_exit_stuck(trade)
        finally:
            self._stuck_tasks.pop(trade.trade_id, None)

    async def _force_exit_stuck(self, trade: SpreadCaptureTrade) -> None:
        """Force exit a stuck trade by selling at best bid."""
        ticker = trade.ticker
        trade.was_taker_exit = True

        # Cancel current exit if any
        if trade.exit_order_id:
            await self.cancel_order(trade.exit_order_id)
            self._order_to_trade.pop(trade.exit_order_id, None)

        book = self.get_orderbook(ticker)
        if not book or not book.best_bid:
            # Absolute worst case: sell at 1 cent
            force_price = 1
        else:
            force_price = book.best_bid.price

        remaining = trade.entry_fill_size - trade.exit_fill_size
        if remaining <= 0:
            self._complete_trade(trade)
            return

        logger.warning(
            f"[FORCE EXIT] {ticker}: Selling {remaining}x @ {force_price}c (trade {trade.trade_id})"
        )

        exit_id = await self.place_order(
            ticker=ticker,
            side="sell",
            price=force_price,
            size=remaining,
            order_type="exit",
        )

        if exit_id:
            trade.exit_order_id = exit_id
            self._order_to_trade[exit_id] = trade.trade_id

            filled = await self.wait_for_fill(exit_id, timeout=10.0)
            if filled:
                trade.exit_fill_price = force_price
                trade.exit_fill_size = trade.entry_fill_size

        self._complete_trade(trade)

    # =========================================================================
    # Trade Completion
    # =========================================================================

    def _complete_trade(self, trade: SpreadCaptureTrade) -> None:
        """Finalize a trade: compute fees/P&L, update risk state."""
        trade.state = SpreadCaptureState.CLOSED

        # Compute fees and P&L
        entry_rate = (
            self.config.kalshi_maker_rate
            if self.config.assume_maker_entry
            else self.config.kalshi_taker_rate
        )
        if trade.was_taker_exit:
            exit_rate = self.config.kalshi_taker_rate
        elif self.config.assume_maker_exit:
            exit_rate = self.config.kalshi_maker_rate
        else:
            exit_rate = self.config.kalshi_taker_rate

        if trade.entry_fill_price:
            trade.entry_fee = kalshi_fee(
                entry_rate, trade.entry_fill_size, trade.entry_fill_price
            )
        if trade.exit_fill_price:
            trade.exit_fee = kalshi_fee(
                exit_rate, trade.exit_fill_size, trade.exit_fill_price
            )

        trade.compute_pnl()

        # Update daily P&L
        self._daily_pnl += trade.net_pnl
        self._session_trades += 1

        if trade.net_pnl >= 0:
            self._session_wins += 1
            self._consecutive_losses = 0
        else:
            self._session_losses += 1
            self._consecutive_losses += 1

        # Check circuit breaker
        if self._consecutive_losses >= self.config.circuit_breaker_consecutive_losses:
            self._circuit_breaker_active = True
            self._circuit_breaker_until = (
                self._clock() + self.config.circuit_breaker_cooldown_seconds
            )
            logger.warning(
                f"[CIRCUIT BREAKER] {self._consecutive_losses} consecutive losses, "
                f"pausing for {self.config.circuit_breaker_cooldown_seconds}s"
            )
            self._alert_circuit_breaker()

        # Check daily loss limit
        if self._risk_manager:
            self._risk_manager.update_daily_pnl(trade.net_pnl)
            from src.core.models import Position as _Position

            self._risk_manager.register_position(
                trade.ticker,
                _Position(
                    ticker=trade.ticker,
                    size=0,
                    entry_price=float(trade.entry_fill_price or 0),
                    current_price=float(trade.exit_fill_price or 0),
                    realized_pnl=trade.net_pnl,
                ),
            )
        else:
            if self._daily_pnl < -self.config.max_daily_loss_dollars:
                self._daily_loss_limit_hit = True
                logger.warning(
                    f"[DAILY LIMIT] Daily loss ${self._daily_pnl:.2f} exceeds "
                    f"limit -${self.config.max_daily_loss_dollars:.2f}, halting"
                )
                self._alert_daily_limit()

        # Release capital reservation
        if (
            self._capital_manager
            and hasattr(trade, "capital_reservation_id")
            and trade.capital_reservation_id
        ):
            self._capital_manager.release(trade.capital_reservation_id)

        # Record cooldown
        self._last_trade_time[trade.ticker] = self._clock()

        # Log
        logger.info(
            f"[SPREAD COMPLETE] {trade.ticker}: "
            f"gross=${trade.gross_pnl:.4f} fees=${trade.entry_fee + trade.exit_fee:.4f} "
            f"net=${trade.net_pnl:.4f} "
            f"(entry={trade.entry_fill_price}c exit={trade.exit_fill_price}c "
            f"size={trade.entry_fill_size} hold={trade.hold_time():.1f}s)"
        )

        self._log_event("spread_complete", trade.to_dict())

        # Send trade completion alert
        self._alert_trade_complete(trade)

        # Update base class stats
        self._stats["total_pnl"] = self._daily_pnl

    # =========================================================================
    # Fill Override
    # =========================================================================

    def _on_fill(self, ticker: str, data: dict) -> None:
        """Override to route fills to the correct SpreadCaptureTrade."""
        # Call parent handler (updates position tracking, signals fill events)
        super()._on_fill(ticker, data)

        # Route fill to trade
        order_id = data.get("order_id", "")
        trade_id = self._order_to_trade.get(order_id)
        if not trade_id:
            return

        trade = self._trades.get(trade_id)
        if not trade:
            return

        fill_count = data.get("count", 0)
        price = data.get("yes_price") or data.get("no_price", 0)

        if order_id == trade.entry_order_id:
            trade.entry_fill_price = price
            trade.entry_fill_size = fill_count
            trade.entry_fill_time = self._clock()
            logger.debug(
                f"[FILL ROUTED] {ticker}: entry fill {fill_count}x @ {price}c "
                f"for trade {trade_id}"
            )
        elif order_id == trade.exit_order_id:
            trade.exit_fill_price = price
            trade.exit_fill_size = fill_count
            logger.debug(
                f"[FILL ROUTED] {ticker}: exit fill {fill_count}x @ {price}c "
                f"for trade {trade_id}"
            )

    # =========================================================================
    # Main Loop
    # =========================================================================

    async def _run_loop(self) -> None:
        """Main loop for WebSocket mode."""
        while self._running:
            if int(self._clock()) % 30 == 0:
                self._log_status()
            await self._sleep(1.0)

    async def _run_loop_iteration(self) -> None:
        """Called each poll cycle in polling mode."""
        for ticker in list(self._subscribed_tickers):
            book = self.get_orderbook(ticker)
            if book:
                self._check_opportunity(ticker, book)

        if int(self._clock()) % 30 == 0:
            self._log_status()

    def _log_status(self) -> None:
        """Log current strategy status."""
        active = sum(1 for t in self._trades.values() if t.is_active())
        stuck = sum(
            1 for t in self._trades.values() if t.state == SpreadCaptureState.STUCK
        )
        logger.info(
            f"[STATUS] Active: {active} (stuck: {stuck}), "
            f"Trades: {self._session_trades} "
            f"(W:{self._session_wins}/L:{self._session_losses}), "
            f"Daily P&L: ${self._daily_pnl:.4f}"
        )

    # =========================================================================
    # Stop
    # =========================================================================

    async def stop(self) -> None:
        """Stop strategy: cancel stuck tasks, exit all active trades."""
        logger.info("Stopping spread capture strategy...")

        # Cancel stuck management tasks
        for task_id, task in list(self._stuck_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Force exit any active trades
        for trade in list(self._trades.values()):
            if trade.is_active() and trade.entry_fill_size > 0:
                await self._force_exit_stuck(trade)

        await super().stop()

    def print_status(self) -> None:
        """Print detailed status."""
        super().print_status()

        print("Spread Capture Status:")
        print("-" * 60)

        for trade_id, trade in self._trades.items():
            if trade.is_active() or trade.net_pnl != 0:
                print(
                    f"  {trade.ticker}: state={trade.state.value} "
                    f"entry={trade.entry_fill_price}c "
                    f"exit={trade.current_exit_price}c "
                    f"size={trade.entry_fill_size} "
                    f"pnl=${trade.net_pnl:.4f} "
                    f"improvements={trade.improvement_count}"
                )

        print(
            f"\nSession: {self._session_trades} trades "
            f"(W:{self._session_wins}/L:{self._session_losses})"
        )
        print(f"Daily P&L: ${self._daily_pnl:.4f}")
        if self._circuit_breaker_active:
            remaining = max(0, self._circuit_breaker_until - self._clock())
            print(f"CIRCUIT BREAKER ACTIVE ({remaining:.0f}s remaining)")
        if self._daily_loss_limit_hit:
            print("DAILY LOSS LIMIT HIT - HALTED")
        print("-" * 60)
