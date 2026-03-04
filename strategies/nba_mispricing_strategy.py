"""NBA Early Game Mispricing Trading Strategy.

Exploits mispricings between score-implied win probability and Kalshi market prices
during NBA first half (periods 1-2).
"""

import asyncio
import importlib.util
import logging
import os as _os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.exchange_client.i_exchange_client import I_ExchangeClient

# src.core.models does not exist in new layout; wrap in try/except
try:
    from src.core.models import Fill, MarketState
except ImportError:
    Fill = None  # type: ignore[assignment,misc]
    MarketState = None  # type: ignore[assignment,misc]

# Kalshi exceptions: old names don't exist in new module; alias from base classes
try:
    from core.exchange_client.kalshi.kalshi_exceptions import (
        KalshiBadRequestError as InsufficientFundsError,
        KalshiError as KalshiAPIError,
    )
except ImportError:

    class InsufficientFundsError(Exception):  # type: ignore[no-redef]
        pass

    class KalshiAPIError(Exception):  # type: ignore[no-redef]
        pass


from core.order_manager import Action, Side

# Outcome does not exist in new order_manager; keep a local stub
try:
    from core.order_manager import Outcome  # type: ignore[attr-defined]
except ImportError:
    from enum import Enum as _Enum

    class Outcome(_Enum):  # type: ignore[no-redef]
        YES = "yes"
        NO = "no"


# Signal from base.py has the legacy fields (ticker, metadata, etc.) used by this strategy.
# Signal from strategy_types.py is the new minimal type — not compatible with this strategy yet.
try:
    from strategies.base import Signal, TradingStrategy
except ImportError:
    from abc import ABC

    Signal = None  # type: ignore[assignment,misc]
    TradingStrategy = ABC  # type: ignore[assignment,misc]

try:
    from strategies.strategy_types import StrategyConfig  # type: ignore[attr-defined]
except ImportError:
    StrategyConfig = None  # type: ignore[assignment,misc]

# Type hint for OMS (avoid circular import)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.order_manager.i_order_manager import I_OrderManager

# Dashboard integration (deprecated - commented out)
# from dashboard.state import state_aggregator
_DASHBOARD_AVAILABLE = False

# Import from the score feed module
# Note: signal_extraction.py is a directory with .py suffix (unconventional naming)
# We need to use importlib to handle this edge case
_score_feed_path = _os.path.join(
    _os.path.dirname(__file__), "..", "signal_extraction", "data_feeds", "score_feed.py"
)
_spec = importlib.util.spec_from_file_location("score_feed", _score_feed_path)
_score_feed_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_score_feed_module)

GameScore = _score_feed_module.GameScore
NBAScoreFeed = _score_feed_module.NBAScoreFeed
ScoreAnalyzer = _score_feed_module.ScoreAnalyzer
get_nba_game_info_from_ticker = _score_feed_module.get_nba_game_info_from_ticker

logger = logging.getLogger(__name__)


@dataclass
class NBAMispricingConfig:
    """Configuration for NBA mispricing strategy.

    Attributes:
        position_size: Number of contracts per trade
        max_period: Maximum period to trade in (2 = first half only)
        order_timeout_ms: Time to wait for fill before canceling both orders
        poll_interval_ms: How often to check order status
        cooldown_seconds: Minimum time between trade attempts per game
        min_edge_cents: Minimum edge to trade (conservative: 5, aggressive: 1)
        position_scale_factor: Multiply position size (aggressive mode)
        max_position_per_game: Maximum total contracts per game
        use_kelly_sizing: Scale position by edge magnitude
        kelly_fraction: Fraction of Kelly criterion to use
        score_staleness_threshold: Score differential that suggests market is stale
        extend_past_first_half: Allow trading past Q2 if market is stale
        enable_smart_exits: Enable selling positions when signal conflicts
        smart_exit_profit_threshold: Minimum profit % to trigger exit (e.g., 0.05 = 5%)
    """

    position_size: int = 10
    max_period: int = 2
    order_timeout_ms: int = 5000
    poll_interval_ms: int = 500
    cooldown_seconds: float = 3.0

    # Aggressiveness controls
    min_edge_cents: float = 3.0
    position_scale_factor: float = 1.0
    max_position_per_game: int = 100

    # Edge scaling (how much to bet based on edge size)
    use_kelly_sizing: bool = False
    kelly_fraction: float = 0.25

    # Period extension based on market staleness
    score_staleness_threshold: int = 15
    extend_past_first_half: bool = True

    # Smart exit configuration
    # When enabled, sells positions that conflict with current signal direction
    # e.g., if we own YES but signal now says NO, sell YES for profit
    # Testing showed 10% threshold provides best avg improvement (+$3.03 vs hold)
    enable_smart_exits: bool = True
    smart_exit_profit_threshold: float = 0.10  # 10% profit minimum to exit

    # Smart exit behavior controls
    # exit_on_neutral_signal: If False, only exit when signal CONFLICTS (not neutral)
    # This is more conservative - backtesting showed buy & hold often wins when model is correct
    exit_on_neutral_signal: bool = False
    # hold_near_end_when_winning: Don't exit winning positions near game end
    # Rationale: Near end of game, winning positions are more likely to pay out
    hold_near_end_when_winning: bool = True
    near_end_period: int = 4  # Which period is considered "near end"
    near_end_time_threshold: int = (
        300  # Seconds remaining (5 min) to consider "near end"
    )

    @classmethod
    def conservative(cls) -> "NBAMispricingConfig":
        """Conservative preset: Only trade large mispricings."""
        return cls(min_edge_cents=5.0, position_scale_factor=0.5)

    @classmethod
    def moderate(cls) -> "NBAMispricingConfig":
        """Moderate preset: Default balanced approach."""
        return cls(min_edge_cents=3.0, position_scale_factor=1.0)

    @classmethod
    def aggressive(cls) -> "NBAMispricingConfig":
        """Aggressive preset: Trade small edges, scale up on large edges."""
        return cls(min_edge_cents=1.0, position_scale_factor=2.0, use_kelly_sizing=True)


@dataclass
class PositionEntry:
    """Tracks a single position entry with its cost basis.

    Attributes:
        quantity: Number of contracts
        entry_price: Price paid per contract (0-1)
        side: 'YES' or 'NO'
        entry_time: When the position was entered
    """

    quantity: int
    entry_price: float
    side: str
    entry_time: datetime


@dataclass
class DualOrderState:
    """Tracks a pair of orders placed on both sides of a mispricing.

    When a mispricing is detected, we place orders on both sides:
    - order_a: Typically the favorite NO (higher-priced side)
    - order_b: The underdog YES

    We monitor until one fills, then cancel the other.
    """

    order_id_a: str
    order_id_b: str
    ticker_a: str
    ticker_b: str
    placed_at: datetime
    game_id: str
    edge_at_placement: float
    side_a: str = "NO"  # What we're buying on ticker_a
    side_b: str = "YES"  # What we're buying on ticker_b
    filled_order: Optional[str] = None  # Which order filled first


@dataclass
class GameContext:
    """Tracks state for a single NBA game.

    Attributes:
        game_id: NBA game ID
        home_team: Home team tricode (e.g., "LAL")
        away_team: Away team tricode (e.g., "BOS")
        home_ticker: Kalshi ticker for home team win market
        away_ticker: Kalshi ticker for away team win market
        score_feed: NBAScoreFeed instance for this game
        last_trade_at: When we last placed a trade for this game
        yes_positions: List of YES position entries for smart exit tracking
        no_positions: List of NO position entries for smart exit tracking
    """

    game_id: str
    home_team: str
    away_team: str
    home_ticker: str
    away_ticker: str
    score_feed: Optional[NBAScoreFeed] = None
    last_trade_at: Optional[datetime] = None
    yes_positions: List[PositionEntry] = field(default_factory=list)
    no_positions: List[PositionEntry] = field(default_factory=list)


class NBAMispricingStrategy(TradingStrategy):
    """Strategy that exploits score-implied probability vs market price mispricings.

    Algorithm:
    1. Monitor live NBA games via score feeds
    2. Calculate win probability from current score
    3. Compare to Kalshi market prices
    4. When mismatch >= edge_threshold:
       - Place orders on BOTH sides (favorite NO + underdog YES)
       - Monitor which fills first
       - Cancel the other order immediately

    Only trades during first half (periods 1-2) when scores are less predictive
    and mispricings are more likely.
    """

    def __init__(
        self,
        client: I_ExchangeClient,
        config: StrategyConfig,
        mispricing_config: Optional[NBAMispricingConfig] = None,
        kalshi_wrapper: Optional[Any] = None,
        oms: Optional["I_OrderManager"] = None,
        exchange: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Initialize the strategy.

        Args:
            client: API client for order execution
            config: Base strategy configuration
            mispricing_config: NBA-specific configuration
            kalshi_wrapper: KalshiWrapped instance for market discovery
            oms: Optional Order Management System for unified order routing
            exchange: Exchange name (required if OMS is provided)
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(client, config, oms=oms, exchange=exchange, **kwargs)
        self._mispricing_config = mispricing_config or NBAMispricingConfig()
        self._kalshi_wrapper = kalshi_wrapper

        # Game tracking
        self._games: Dict[str, GameContext] = {}
        self._pending_dual_orders: Dict[str, DualOrderState] = {}

        # Score analyzer for win probability calculation
        self._analyzer = ScoreAnalyzer()

    def on_start(self) -> None:
        """Initialize score feeds for live games and fetch market pairs."""
        logger.info("Starting NBA Mispricing Strategy")

        if self._kalshi_wrapper is None:
            logger.warning("No Kalshi wrapper provided, cannot fetch live markets")
            return

        self._refresh_live_games()

    def _refresh_live_games(self) -> None:
        """Fetch live NBA markets and set up score feeds."""
        if self._kalshi_wrapper is None:
            return

        try:
            live_markets = self._kalshi_wrapper.GetLiveNBAMarkets()
            market_pairs = self._kalshi_wrapper.GetMarketPairs(live_markets)

            logger.info(f"Found {len(market_pairs)} live NBA market pairs")

            for market_a, market_b in market_pairs:
                self._setup_game_from_pair(market_a, market_b)

        except Exception as e:
            logger.error(f"Failed to refresh live games: {e}")

    def _setup_game_from_pair(self, market_a: Any, market_b: Any) -> None:
        """Set up game context and score feed from a market pair."""
        data_a = market_a.model_dump()
        data_b = market_b.model_dump()

        ticker_a = data_a["ticker"]
        ticker_b = data_b["ticker"]

        # Try to extract game info from ticker
        game_info = get_nba_game_info_from_ticker(ticker_a)
        if game_info is None:
            game_info = get_nba_game_info_from_ticker(ticker_b)

        if game_info is None:
            logger.warning(f"Could not extract game info from {ticker_a} or {ticker_b}")
            return

        game_id = game_info["game_id"]

        # Skip if already tracking this game
        if game_id in self._games:
            return

        home_team = game_info["home_team"]
        away_team = game_info["away_team"]

        # Determine which ticker is for which team
        home_ticker = ticker_a
        away_ticker = ticker_b

        # Check yes_sub_title to determine team assignment
        if home_team.lower() in data_b.get("yes_sub_title", "").lower():
            home_ticker = ticker_b
            away_ticker = ticker_a

        # Create score feed
        score_feed = NBAScoreFeed(
            game_id=game_id,
            home_team_tricode=home_team,
            away_team_tricode=away_team,
            poll_interval_ms=self._mispricing_config.poll_interval_ms,
        )
        score_feed.start()

        context = GameContext(
            game_id=game_id,
            home_team=home_team,
            away_team=away_team,
            home_ticker=home_ticker,
            away_ticker=away_ticker,
            score_feed=score_feed,
        )

        self._games[game_id] = context
        logger.info(f"Tracking game {game_id}: {away_team} @ {home_team}")

    def evaluate(self, market: MarketState) -> List[Signal]:
        """Evaluate all tracked games for mispricings and exit opportunities.

        Note: This method is called per-market by the base class, but our strategy
        monitors all games together. We use the market update as a trigger to
        check all games.

        Smart Exit Logic:
        - If we own YES positions and current signal says NO, sell YES for profit
        - If we own NO positions and current signal says YES, sell NO for profit
        - Only exit if position is profitable above the threshold
        """
        signals: List[Signal] = []

        for game_id, context in self._games.items():
            # Publish game state to dashboard (even if not trading)
            self._publish_game_to_dashboard(context)

            # Get current market data and signal direction
            edge_info = self._calculate_edge(context)
            current_signal_side = edge_info[2] if edge_info else None  # "YES" or "NO"

            # Check for smart exit opportunities (before cooldown check)
            if self._mispricing_config.enable_smart_exits:
                exit_signals = self._check_smart_exits(context, current_signal_side)
                signals.extend(exit_signals)

            # Check cooldown for new entries
            if context.last_trade_at is not None:
                elapsed = (datetime.now() - context.last_trade_at).total_seconds()
                if elapsed < self._mispricing_config.cooldown_seconds:
                    continue

            # Check for mispricing (entry signal)
            if edge_info is None:
                continue

            edge_cents, ticker_to_buy, side = edge_info

            # Calculate position size based on edge and aggressiveness settings
            position_size = self._calculate_position_size(edge_cents)

            # Determine outcome based on signal side
            outcome = Outcome.YES if side == "YES" else Outcome.NO

            signal = Signal(
                ticker=ticker_to_buy,
                side="BID",
                price=self._get_fair_price(context, ticker_to_buy),
                size=position_size,
                confidence=min(abs(edge_cents) / 10.0, 1.0),
                reason=f"Score-implied mispricing: {edge_cents:.1f}c edge, {side}",
                timestamp=datetime.now(),
                outcome=outcome,
                action=Action.BUY,
                metadata={
                    "game_id": game_id,
                    "edge_cents": edge_cents,
                    "side": side,
                    "action": "ENTRY",
                },
            )
            signals.append(signal)

        return signals

    def _is_near_game_end(self, context: GameContext) -> bool:
        """Check if game is near the end (Q4 with limited time remaining).

        Used to hold winning positions near game end, since they're more likely
        to pay out as the outcome becomes more certain.

        Args:
            context: Game context with score feed

        Returns:
            True if game is in the configured "near end" period with less than
            the threshold time remaining
        """
        if context.score_feed is None:
            return False

        score = context.score_feed.current_score
        if score is None:
            return False

        # Check if we're in the "near end" period (default: Q4)
        if score.period < self._mispricing_config.near_end_period:
            return False

        # Parse time remaining and check threshold
        time_remaining_seconds = self._analyzer.parse_time_remaining(
            score.time_remaining
        )

        return time_remaining_seconds <= self._mispricing_config.near_end_time_threshold

    def _check_smart_exits(
        self, context: GameContext, current_signal_side: Optional[str]
    ) -> List[Signal]:
        """Check for smart exit opportunities on conflicting positions.

        Enhanced smart exit logic:
        - By default, only exits when signal CONFLICTS with position (not on neutral)
        - Optionally holds winning positions near game end (Q4 < 5 min)

        Exit conditions:
        - Signal CONFLICTS with position (YES->NO or NO->YES)
        - OR signal is neutral AND exit_on_neutral_signal is True
        - AND position is profitable above threshold
        - AND NOT (near_end AND position_winning AND hold_near_end_when_winning)

        Args:
            context: Game context with position tracking
            current_signal_side: Current signal direction ("YES", "NO", or None)

        Returns:
            List of exit signals to execute
        """
        signals: List[Signal] = []

        if context.score_feed is None:
            return signals

        score = context.score_feed.current_score
        if score is None:
            return signals

        # Get current market price for profit calculation
        try:
            home_market = asyncio.get_event_loop().run_until_complete(
                self._client.get_market_data_async(context.home_ticker)
            )
            market_mid = (home_market.bid + home_market.ask) / 2
        except Exception:
            return signals

        threshold = self._mispricing_config.smart_exit_profit_threshold

        # Check if we're near game end (for hold logic)
        near_end = self._is_near_game_end(context)

        # Determine if we should consider exiting YES positions
        # Exit YES when: signal is NO (conflicting) OR (signal is neutral AND exit_on_neutral_signal)
        should_check_yes_exit = current_signal_side == "NO" or (
            current_signal_side is None
            and self._mispricing_config.exit_on_neutral_signal
        )

        # Determine if we should consider exiting NO positions
        # Exit NO when: signal is YES (conflicting) OR (signal is neutral AND exit_on_neutral_signal)
        should_check_no_exit = current_signal_side == "YES" or (
            current_signal_side is None
            and self._mispricing_config.exit_on_neutral_signal
        )

        # Check YES positions for exit
        if should_check_yes_exit:
            yes_current_value = market_mid  # YES contracts worth market price

            remaining_yes = []
            for pos in context.yes_positions:
                profit_pct = (
                    (yes_current_value - pos.entry_price) / pos.entry_price
                    if pos.entry_price > 0
                    else 0
                )
                is_winning = profit_pct > 0

                # Skip exit if near end, position is winning, and hold_near_end_when_winning is enabled
                if (
                    near_end
                    and is_winning
                    and self._mispricing_config.hold_near_end_when_winning
                ):
                    remaining_yes.append(pos)
                    logger.debug(
                        f"Holding YES {context.game_id} near game end "
                        f"(+{profit_pct:.1%} profit, Q{score.period} {score.time_remaining})"
                    )
                    continue

                if profit_pct >= threshold:
                    # Generate exit signal
                    profit_cents = (yes_current_value - pos.entry_price) * 100
                    signal = Signal(
                        ticker=context.home_ticker,
                        side="ASK",  # Selling
                        price=home_market.bid,  # Sell at bid
                        size=pos.quantity,
                        confidence=min(profit_pct, 1.0),
                        reason=f"Smart exit: YES position +{profit_pct:.1%} profit, signal now {current_signal_side or 'neutral'}",
                        timestamp=datetime.now(),
                        outcome=Outcome.YES,
                        action=Action.SELL,
                        metadata={
                            "game_id": context.game_id,
                            "action": "SMART_EXIT",
                            "exit_side": "YES",
                            "entry_price": pos.entry_price,
                            "exit_price": yes_current_value,
                            "profit_pct": profit_pct,
                            "profit_cents": profit_cents,
                        },
                    )
                    signals.append(signal)
                    logger.info(
                        f"Smart exit: Selling {pos.quantity} YES {context.game_id} "
                        f"@ {yes_current_value:.3f} (bought @ {pos.entry_price:.3f}, "
                        f"+{profit_pct:.1%} profit)"
                    )
                else:
                    remaining_yes.append(pos)

            context.yes_positions = remaining_yes

        # Check NO positions for exit
        if should_check_no_exit:
            no_current_value = 1 - market_mid  # NO contracts worth 1 - market price

            remaining_no = []
            for pos in context.no_positions:
                profit_pct = (
                    (no_current_value - pos.entry_price) / pos.entry_price
                    if pos.entry_price > 0
                    else 0
                )
                is_winning = profit_pct > 0

                # Skip exit if near end, position is winning, and hold_near_end_when_winning is enabled
                if (
                    near_end
                    and is_winning
                    and self._mispricing_config.hold_near_end_when_winning
                ):
                    remaining_no.append(pos)
                    logger.debug(
                        f"Holding NO {context.game_id} near game end "
                        f"(+{profit_pct:.1%} profit, Q{score.period} {score.time_remaining})"
                    )
                    continue

                if profit_pct >= threshold:
                    # Generate exit signal
                    profit_cents = (no_current_value - pos.entry_price) * 100
                    signal = Signal(
                        ticker=context.home_ticker,
                        side="ASK",  # Selling
                        price=1 - home_market.ask,  # Sell NO at the ask (buy YES)
                        size=pos.quantity,
                        confidence=min(profit_pct, 1.0),
                        reason=f"Smart exit: NO position +{profit_pct:.1%} profit, signal now {current_signal_side or 'neutral'}",
                        timestamp=datetime.now(),
                        outcome=Outcome.NO,
                        action=Action.SELL,
                        metadata={
                            "game_id": context.game_id,
                            "action": "SMART_EXIT",
                            "exit_side": "NO",
                            "entry_price": pos.entry_price,
                            "exit_price": no_current_value,
                            "profit_pct": profit_pct,
                            "profit_cents": profit_cents,
                        },
                    )
                    signals.append(signal)
                    logger.info(
                        f"Smart exit: Selling {pos.quantity} NO {context.game_id} "
                        f"@ {no_current_value:.3f} (bought @ {pos.entry_price:.3f}, "
                        f"+{profit_pct:.1%} profit)"
                    )
                else:
                    remaining_no.append(pos)

            context.no_positions = remaining_no

        return signals

    def _publish_game_to_dashboard(self, context: GameContext) -> None:
        """Publish game state to dashboard."""
        if not _DASHBOARD_AVAILABLE:
            return

        if context.score_feed is None:
            return

        score = context.score_feed.current_score
        if score is None:
            return

        try:
            # Calculate win probability and edge
            time_remaining_seconds = self._analyzer.parse_time_remaining(
                score.time_remaining
            )
            home_win_prob = self._analyzer.calculate_win_probability(
                score.score_differential, score.period, time_remaining_seconds
            )

            # Try to get market price (may fail if not connected)
            market_price = 0.5
            edge_cents = 0.0
            is_trading_allowed = False
            last_signal = None

            try:
                home_market = asyncio.get_event_loop().run_until_complete(
                    self._client.get_market_data_async(context.home_ticker)
                )
                market_price = (home_market.bid + home_market.ask) / 2
                edge_cents = abs(home_win_prob - market_price) * 100
                is_trading_allowed = self._should_trade_this_period(
                    context, market_price
                )

                if edge_cents >= self._mispricing_config.min_edge_cents:
                    if home_win_prob > market_price:
                        last_signal = "BUY YES"
                    else:
                        last_signal = "BUY NO"
            except Exception:
                pass

            # Get current position for this game
            position = self._state.positions.get(context.home_ticker, 0)

            state_aggregator.publish_nba_state(  # noqa: F821
                game_id=context.game_id,
                home_team=context.home_team,
                away_team=context.away_team,
                home_score=score.home_score,
                away_score=score.away_score,
                period=score.period,
                time_remaining=score.time_remaining,
                home_win_prob=home_win_prob,
                market_price=market_price,
                edge_cents=edge_cents,
                is_trading_allowed=is_trading_allowed,
                last_signal=last_signal,
                position=position,
            )
        except Exception:
            pass

    def _calculate_edge(self, context: GameContext) -> Optional[Tuple[float, str, str]]:
        """Calculate edge between score-implied fair value and market price.

        Uses ScoreAnalyzer to get fair value from current score (assuming even skill),
        then compares to market. Returns edge for whichever direction is profitable.

        Returns:
            Tuple of (edge_cents, ticker_to_buy, side) or None if below threshold.
        """
        if context.score_feed is None:
            return None

        score = context.score_feed.current_score
        if score is None:
            return None

        # Only trade live games
        if not score.is_live:
            return None

        # Get market prices first (need for period check with staleness)
        try:
            home_market = asyncio.get_event_loop().run_until_complete(
                self._client.get_market_data_async(context.home_ticker)
            )
        except Exception as e:
            logger.debug(f"Failed to get market data for {context.game_id}: {e}")
            return None

        # Calculate market mid price for staleness check
        market_mid = (home_market.bid + home_market.ask) / 2

        # Check if we should trade this period (with staleness override)
        if not self._should_trade_this_period(context, market_mid):
            return None

        # Get score-implied fair value (assuming even skill)
        time_remaining_seconds = self._analyzer.parse_time_remaining(
            score.time_remaining
        )
        home_win_prob = self._analyzer.calculate_win_probability(
            score.score_differential, score.period, time_remaining_seconds
        )
        fair_value_cents = home_win_prob * 100

        # Market mid price in cents
        market_mid_cents = market_mid * 100

        # Calculate edge as absolute difference between fair value and market
        edge = abs(fair_value_cents - market_mid_cents)

        # Check minimum edge threshold
        if edge < self._mispricing_config.min_edge_cents:
            return None

        # Determine direction: buy YES if underpriced, NO if overpriced
        if fair_value_cents > market_mid_cents:
            # Market underpricing home team - buy YES
            return (edge, context.home_ticker, "YES")
        else:
            # Market overpricing home team - buy NO
            return (edge, context.home_ticker, "NO")

    def _is_market_stale(self, context: GameContext, market_price: float) -> bool:
        """Check if market hasn't adjusted to a lopsided score.

        If score differential is large but market is still near 50/50,
        the market is "stale" and we should continue trading past first half.

        Args:
            context: Game context with score feed
            market_price: Current market YES price (0-1)

        Returns:
            True if market appears stale (hasn't adjusted to score)
        """
        if context.score_feed is None:
            return False

        score = context.score_feed.current_score
        if score is None:
            return False

        score_diff = abs(score.score_differential)
        threshold = self._mispricing_config.score_staleness_threshold

        # Market is stale if:
        # 1. Score differential is large (e.g., 15+ points)
        # 2. Market price is still near 50% (within 10 cents of 0.50)
        is_lopsided_score = score_diff >= threshold
        is_market_near_even = 0.40 <= market_price <= 0.60

        return is_lopsided_score and is_market_near_even

    def _should_trade_this_period(
        self, context: GameContext, market_price: float
    ) -> bool:
        """Determine if we should trade based on period and market staleness.

        Args:
            context: Game context with score feed
            market_price: Current market YES price (0-1)

        Returns:
            True if trading is allowed for current game state
        """
        if context.score_feed is None:
            return True  # No score data, allow trading

        score = context.score_feed.current_score
        if score is None:
            return True

        period = score.period

        # First half: always trade
        if period <= self._mispricing_config.max_period:
            return True

        # Past first half: only trade if market is stale
        if self._mispricing_config.extend_past_first_half:
            return self._is_market_stale(context, market_price)

        return False

    def _calculate_position_size(self, edge_cents: float) -> int:
        """Calculate position size based on edge and aggressiveness settings.

        Args:
            edge_cents: The detected edge in cents

        Returns:
            Number of contracts to trade
        """
        base_size = self._mispricing_config.position_size

        if self._mispricing_config.use_kelly_sizing:
            # Kelly: bet proportional to edge
            # Simplified: edge/100 * kelly_fraction * base_size * 10
            kelly_size = (
                (edge_cents / 100)
                * self._mispricing_config.kelly_fraction
                * base_size
                * 10
            )
            return max(
                1, min(int(kelly_size), self._mispricing_config.max_position_per_game)
            )
        else:
            # Fixed size with optional scaling
            scaled = int(base_size * self._mispricing_config.position_scale_factor)
            return max(1, min(scaled, self._mispricing_config.max_position_per_game))

    def _get_fair_price(self, context: GameContext, ticker: str) -> float:
        """Get fair price to bid based on score-implied probability."""
        if context.score_feed is None:
            return 0.5

        score = context.score_feed.current_score
        if score is None:
            return 0.5

        time_remaining_seconds = self._analyzer.parse_time_remaining(
            score.time_remaining
        )
        home_win_prob = self._analyzer.calculate_win_probability(
            score.score_differential, score.period, time_remaining_seconds
        )

        if ticker == context.home_ticker:
            return home_win_prob
        else:
            return 1 - home_win_prob

    async def _place_dual_orders(
        self, context: GameContext, edge_cents: float
    ) -> Optional[DualOrderState]:
        """Place orders on both sides simultaneously.

        Places:
        - BID on favorite NO market
        - BID on underdog YES market

        Returns DualOrderState for monitoring, or None if orders failed.
        """
        score = context.score_feed.current_score if context.score_feed else None
        if score is None:
            return None

        time_remaining_seconds = self._analyzer.parse_time_remaining(
            score.time_remaining
        )
        home_win_prob = self._analyzer.calculate_win_probability(
            score.score_differential, score.period, time_remaining_seconds
        )

        # Determine favorite and underdog
        if home_win_prob > 0.5:
            favorite_ticker = context.home_ticker
            underdog_ticker = context.away_ticker
            favorite_no_price = 1 - home_win_prob
            underdog_yes_price = 1 - home_win_prob
        else:
            favorite_ticker = context.away_ticker
            underdog_ticker = context.home_ticker
            favorite_no_price = home_win_prob
            underdog_yes_price = home_win_prob

        size = self._mispricing_config.position_size

        try:
            # Place both orders concurrently
            order_a_task = self._client.place_order_async(
                ticker=favorite_ticker,
                side=Side.NO.value,
                price=favorite_no_price,
                size=size,
            )
            order_b_task = self._client.place_order_async(
                ticker=underdog_ticker,
                side=Side.YES.value,
                price=underdog_yes_price,
                size=size,
            )

            order_id_a, order_id_b = await asyncio.gather(order_a_task, order_b_task)

            state = DualOrderState(
                order_id_a=order_id_a,
                order_id_b=order_id_b,
                ticker_a=favorite_ticker,
                ticker_b=underdog_ticker,
                placed_at=datetime.now(),
                game_id=context.game_id,
                edge_at_placement=edge_cents,
                side_a="NO",
                side_b="YES",
            )

            self._pending_dual_orders[order_id_a] = state
            context.last_trade_at = datetime.now()

            logger.info(
                f"Placed dual orders for {context.game_id}: "
                f"NO {favorite_ticker} ({order_id_a}) + YES {underdog_ticker} ({order_id_b})"
            )

            # Start monitoring in background
            asyncio.create_task(self._monitor_dual_orders(state))

            return state

        except InsufficientFundsError as e:
            logger.warning(f"Insufficient funds for dual order: {e}")
            return None
        except KalshiAPIError as e:
            logger.error(f"API error placing dual orders: {e}")
            return None

    async def _monitor_dual_orders(self, state: DualOrderState) -> None:
        """Monitor dual orders until one fills, then cancel the other.

        Polls order status every poll_interval_ms. On first fill, immediately
        cancels the unfilled order. If timeout is reached, cancels both.
        """
        poll_interval = self._mispricing_config.poll_interval_ms / 1000.0
        timeout = self._mispricing_config.order_timeout_ms / 1000.0
        elapsed = 0.0

        while elapsed < timeout:
            try:
                # Check both orders
                status_a = await self._client.get_order_status_async(state.order_id_a)
                status_b = await self._client.get_order_status_async(state.order_id_b)

                # Check for fills
                a_filled = status_a.get("status") == "FILLED"
                b_filled = status_b.get("status") == "FILLED"

                if a_filled and not b_filled:
                    # Order A filled first, cancel B
                    await self._client.cancel_order_async(state.order_id_b)
                    state.filled_order = state.order_id_a
                    logger.info(
                        f"Order {state.order_id_a} filled, canceled {state.order_id_b}"
                    )
                    break

                if b_filled and not a_filled:
                    # Order B filled first, cancel A
                    await self._client.cancel_order_async(state.order_id_a)
                    state.filled_order = state.order_id_b
                    logger.info(
                        f"Order {state.order_id_b} filled, canceled {state.order_id_a}"
                    )
                    break

                if a_filled and b_filled:
                    # Both filled (unlikely but possible)
                    state.filled_order = "both"
                    logger.warning(
                        f"Both orders filled for {state.game_id} - net neutral position"
                    )
                    break

            except Exception as e:
                logger.error(f"Error monitoring orders: {e}")

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        # Timeout - cancel both
        if state.filled_order is None:
            logger.info(f"Timeout reached, canceling both orders for {state.game_id}")
            try:
                await self._client.cancel_order_async(state.order_id_a)
            except Exception:
                pass
            try:
                await self._client.cancel_order_async(state.order_id_b)
            except Exception:
                pass

        # Clean up
        self._pending_dual_orders.pop(state.order_id_a, None)

    def on_fill(self, fill: Fill) -> None:
        """Handle fill event from the exchange.

        Updates positions, tracks entries for smart exits, and triggers
        cancellation of paired order if needed.
        """
        self._state.fills_received += 1

        # Update position
        if fill.side == "BID":
            self._update_position(fill.ticker, fill.size)
        else:
            self._update_position(fill.ticker, -fill.size)

        # Track position entry for smart exits
        if self._mispricing_config.enable_smart_exits and fill.side == "BID":
            self._track_position_entry(fill)

        # Check if this is part of a dual order
        for order_id, state in list(self._pending_dual_orders.items()):
            if fill.order_id == state.order_id_a:
                # Cancel order B
                asyncio.create_task(self._client.cancel_order_async(state.order_id_b))
                state.filled_order = state.order_id_a
                self._pending_dual_orders.pop(order_id, None)
                logger.info(f"Fill triggered cancellation of {state.order_id_b}")
                break
            elif fill.order_id == state.order_id_b:
                # Cancel order A
                asyncio.create_task(self._client.cancel_order_async(state.order_id_a))
                state.filled_order = state.order_id_b
                self._pending_dual_orders.pop(order_id, None)
                logger.info(f"Fill triggered cancellation of {state.order_id_a}")
                break

    def _track_position_entry(self, fill: Fill) -> None:
        """Track a new position entry for smart exit calculations.

        Args:
            fill: The fill event containing entry details
        """
        # Find the game context for this ticker
        context = None
        position_side = None

        for ctx in self._games.values():
            if fill.ticker == ctx.home_ticker:
                context = ctx
                # Buying home ticker = YES position on home team
                position_side = Side.YES.value.upper()
                break
            elif fill.ticker == ctx.away_ticker:
                context = ctx
                # Buying away ticker = NO position on home team (YES on away = NO on home)
                position_side = Side.NO.value.upper()
                break

        if context is None:
            logger.debug(f"Could not find game context for ticker {fill.ticker}")
            return

        # Create position entry
        entry = PositionEntry(
            quantity=fill.size,
            entry_price=fill.price,
            side=position_side,
            entry_time=datetime.now(),
        )

        # Add to appropriate position list
        if position_side == "YES":
            context.yes_positions.append(entry)
            logger.debug(
                f"Tracked YES entry for {context.game_id}: "
                f"{fill.size} contracts @ {fill.price:.3f}"
            )
        else:
            context.no_positions.append(entry)
            logger.debug(
                f"Tracked NO entry for {context.game_id}: "
                f"{fill.size} contracts @ {fill.price:.3f}"
            )

    def on_stop(self) -> None:
        """Clean up score feeds and cancel pending orders."""
        logger.info("Stopping NBA Mispricing Strategy")

        # Stop all score feeds
        for context in self._games.values():
            if context.score_feed is not None:
                context.score_feed.stop()

        # Cancel all pending orders
        for state in list(self._pending_dual_orders.values()):
            try:
                asyncio.get_event_loop().run_until_complete(
                    self._client.cancel_order_async(state.order_id_a)
                )
            except Exception:
                pass
            try:
                asyncio.get_event_loop().run_until_complete(
                    self._client.cancel_order_async(state.order_id_b)
                )
            except Exception:
                pass

        self._pending_dual_orders.clear()
        self._games.clear()

    def should_trade(self, signal: Signal) -> Tuple[bool, str]:
        """Additional trade filtering for NBA mispricing signals."""
        # Check base class filters first
        allowed, reason = super().should_trade(signal)
        if not allowed:
            return allowed, reason

        # Extract game context from signal metadata
        game_id = signal.metadata.get("game_id")
        if game_id is None:
            return False, "No game_id in signal metadata"

        context = self._games.get(game_id)
        if context is None:
            return False, f"Unknown game: {game_id}"

        # Period check is now handled in _calculate_edge with staleness override
        # This is a secondary check for safety
        if context.score_feed is not None:
            score = context.score_feed.current_score
            if score is not None:
                period = score.period
                if period > self._mispricing_config.max_period:
                    # Allow if extend_past_first_half is enabled (staleness checked in _calculate_edge)
                    if not self._mispricing_config.extend_past_first_half:
                        return False, f"Game past first half (period {period})"

        return True, "Trade allowed"

    def get_stats(self) -> Dict[str, Any]:
        """Get strategy statistics including NBA-specific metrics."""
        stats = super().get_stats()

        # Calculate position tracking stats
        total_yes_positions = sum(
            len(ctx.yes_positions) for ctx in self._games.values()
        )
        total_no_positions = sum(len(ctx.no_positions) for ctx in self._games.values())
        total_yes_contracts = sum(
            sum(p.quantity for p in ctx.yes_positions) for ctx in self._games.values()
        )
        total_no_contracts = sum(
            sum(p.quantity for p in ctx.no_positions) for ctx in self._games.values()
        )

        stats.update(
            {
                "tracked_games": len(self._games),
                "pending_dual_orders": len(self._pending_dual_orders),
                "smart_exits_enabled": self._mispricing_config.enable_smart_exits,
                "smart_exit_threshold": self._mispricing_config.smart_exit_profit_threshold,
                "tracked_positions": {
                    "yes_entries": total_yes_positions,
                    "no_entries": total_no_positions,
                    "yes_contracts": total_yes_contracts,
                    "no_contracts": total_no_contracts,
                },
                "games": {
                    game_id: {
                        "home_team": ctx.home_team,
                        "away_team": ctx.away_team,
                        "home_ticker": ctx.home_ticker,
                        "away_ticker": ctx.away_ticker,
                        "last_trade_at": ctx.last_trade_at.isoformat()
                        if ctx.last_trade_at
                        else None,
                        "yes_positions": len(ctx.yes_positions),
                        "no_positions": len(ctx.no_positions),
                        "yes_contracts": sum(p.quantity for p in ctx.yes_positions),
                        "no_contracts": sum(p.quantity for p in ctx.no_positions),
                    }
                    for game_id, ctx in self._games.items()
                },
            }
        )

        return stats
