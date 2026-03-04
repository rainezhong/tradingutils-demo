"""State Aggregator for the trading dashboard.

Collects state from all running algorithms and makes it available
for the web dashboard via WebSocket updates.

Usage:
    from dashboard.state import state_aggregator

    # From spread detector:
    state_aggregator.publish_opportunity(opportunity)

    # From market maker:
    state_aggregator.publish_mm_state(bot_state)

    # From NBA strategy:
    state_aggregator.publish_nba_state(game_state)
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from collections import deque
import threading

logger = logging.getLogger(__name__)


# ==================== Strategy-Centric Dataclasses ====================


class StrategyType(str, Enum):
    """Available strategy types."""

    # NBA strategies
    NBA_MISPRICING = "nba_mispricing"
    LATE_GAME_BLOWOUT = "late_game_blowout"
    UNDERDOG_SCALP = "underdog_scalp"
    SPREAD_CAPTURE = "spread_capture"
    LIQUIDITY_PROVIDER = "liquidity_provider"
    DEPTH_SCALPER = "depth_scalper"
    TIED_GAME_SPREAD = "tied_game_spread"
    TOTAL_POINTS = "total_points"
    # Crypto / cross-exchange
    CRYPTO_LATENCY = "crypto_latency"
    ARBITRAGE = "arbitrage"


@dataclass
class StrategyPosition:
    """Open position in running strategy."""

    ticker: str
    side: str  # "YES" or "NO"
    entry_price: float  # in cents
    current_price: float
    size: int
    unrealized_pnl: float
    entry_time: str


@dataclass
class StrategyTrade:
    """Executed trade record."""

    trade_id: str
    ticker: str
    side: str
    action: str  # "BUY" or "SELL"
    price: float
    size: int
    timestamp: str
    pnl: Optional[float] = None  # For exits


@dataclass
class GameState:
    """Live NBA game state."""

    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    quarter: int
    clock: str
    model_prob: float  # Win probability from model


@dataclass
class OrderBookLevel:
    """Single level in the order book."""

    price: float  # in cents
    size: int


@dataclass
class MarketState:
    """Kalshi market pricing with full order book."""

    ticker: str
    yes_bid: float
    yes_ask: float
    spread: float
    volume: int
    last_trade: Optional[float] = None
    # Full order book depth
    bids: List[OrderBookLevel] = None  # Sorted by price descending (best first)
    asks: List[OrderBookLevel] = None  # Sorted by price ascending (best first)

    def __post_init__(self):
        if self.bids is None:
            self.bids = []
        if self.asks is None:
            self.asks = []


@dataclass
class ScannerOpportunity:
    """A market opportunity from the scanner."""

    ticker: str
    event_ticker: str
    spread_cents: int
    volume: int
    yes_bid: int
    yes_ask: int
    mid_price: float
    category: str  # 'nba_totals', 'ncaab', etc.
    volatility_score: float  # spread / mid_price
    scanned_at: str


@dataclass
class ScannerState:
    """State of the opportunity scanner."""

    last_scan: str
    total_scanned: int
    opportunities_found: int
    opportunities: List[ScannerOpportunity]
    top_spread: int
    scan_duration_ms: float


@dataclass
class StrategySession:
    """Complete state for a running strategy session."""

    strategy_type: str
    mode: str  # "paper" or "live"
    status: str  # "running", "stopped", "error"
    started_at: str

    # Core data
    positions: List[StrategyPosition]
    trades: List[StrategyTrade]
    game: Optional[GameState]
    market: Optional[MarketState]

    # Metrics
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    trade_count: int
    win_count: int
    loss_count: int


@dataclass
class Opportunity:
    """An arbitrage opportunity."""

    id: str
    pair_id: str
    event_description: str
    opportunity_type: str
    buy_platform: str
    buy_price: float
    sell_platform: str
    sell_price: float
    gross_edge: float
    net_edge: float
    max_contracts: int
    estimated_profit: float
    first_seen: str
    last_seen: str
    is_active: bool = True


@dataclass
class Execution:
    """An execution record."""

    id: str
    opportunity_id: str
    status: str  # pending, leg1_filled, completed, failed, rolled_back
    leg1_exchange: str
    leg1_ticker: str
    leg1_side: str
    leg1_price: float
    leg1_filled: bool
    leg2_exchange: str
    leg2_ticker: str
    leg2_side: str
    leg2_price: float
    leg2_filled: bool
    expected_profit: float
    actual_profit: Optional[float]
    started_at: str
    completed_at: Optional[str]
    error: Optional[str] = None


@dataclass
class MarketMakerState:
    """State of a market maker bot."""

    ticker: str
    position: int
    cash: float
    total_fees: float
    mtm_pnl: float
    gross_pnl: float
    sigma: float
    last_ref: float
    active_bid: Optional[float]
    active_ask: Optional[float]
    reservation_price: Optional[float]
    half_spread: Optional[float]
    total_volume: int
    fills_received: int
    running: bool
    dry_run: bool
    updated_at: str


@dataclass
class NBAGameState:
    """State of an NBA game being tracked."""

    game_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    period: int
    time_remaining: str
    home_win_prob: float
    market_price: float
    edge_cents: float
    is_trading_allowed: bool
    last_signal: Optional[str]
    position: int
    updated_at: str


@dataclass
class ActivityLogEntry:
    """A log entry for algorithm activity."""

    timestamp: str
    strategy: str  # "nba", "arb", "mm"
    event_type: str  # "signal", "order", "fill", "decision", "error"
    message: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class DashboardMetrics:
    """Aggregate dashboard metrics."""

    active_opportunities: int = 0
    running_detectors: int = 0
    running_bots: int = 0
    pending_executions: int = 0
    completed_executions: int = 0
    failed_executions: int = 0
    total_profit: float = 0.0
    tracked_games: int = 0
    uptime_seconds: float = 0.0
    running_backtests: int = 0
    completed_backtests: int = 0


@dataclass
class OMSMetricsState:
    """Snapshot of OMS metrics for dashboard display."""

    pending_orders: int = 0
    active_orders: int = 0
    total_tracked_orders: int = 0
    orders_by_status: Dict[str, int] = field(default_factory=dict)
    orders_by_exchange: Dict[str, Dict[str, int]] = field(default_factory=dict)
    # Fill stats
    total_filled_contracts: int = 0
    total_filled_value: float = 0.0
    avg_fill_price: Optional[float] = None
    avg_fill_time_seconds: Optional[float] = None
    fill_count: int = 0
    # Failures
    failed_orders: int = 0
    failure_reasons: Dict[str, int] = field(default_factory=dict)
    # Constraints
    constraint_violations: int = 0
    constrained_tickers: int = 0
    # Infrastructure
    exchanges_registered: int = 0
    timeout_registered: int = 0
    # Positions
    positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    total_exposure: float = 0.0
    # Capital
    capital: Optional[Dict[str, Any]] = None
    updated_at: str = ""


@dataclass
class BacktestState:
    """State of a backtest job."""

    id: str
    strategy: str
    data_source: str
    status: str  # pending, running, completed, failed, cancelled
    progress_pct: float = 0.0
    frames_processed: int = 0
    total_frames: int = 0
    signals_generated: int = 0
    trades_executed: int = 0
    current_pnl: float = 0.0
    error: Optional[str] = None
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


@dataclass
class LivePositionState:
    """State of a live position from the OMS."""

    exchange: str
    ticker: str
    size: int
    entry_price: float
    current_price: float
    unrealized_pnl: float
    realized_pnl: float
    exposure: float
    opened_at: Optional[str]
    updated_at: str


@dataclass
class PaperOrderState:
    """State of a paper trading order."""

    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    filled_size: int
    status: str
    created_at: str
    updated_at: str


@dataclass
class PaperPositionState:
    """State of a paper trading position."""

    ticker: str
    size: int
    avg_entry_price: float
    unrealized_pnl: float
    realized_pnl: float
    total_fees: float
    updated_at: str


@dataclass
class PaperFillState:
    """State of a paper trading fill."""

    fill_id: str
    order_id: str
    ticker: str
    side: str
    price: float
    size: int
    fee: float
    timestamp: str


@dataclass
class PaperTradingSummary:
    """Summary of paper trading session."""

    initial_balance: float
    current_balance: float
    total_realized_pnl: float
    total_unrealized_pnl: float
    total_pnl: float
    total_fees: float
    updated_at: str


class StateAggregator:
    """Central state aggregator for dashboard updates.

    Thread-safe state management with WebSocket broadcast support.
    Persists activity log and trade history across restarts.
    """

    PERSIST_DIR = Path("data/dashboard_state")
    ACTIVITY_LOG_FILE = "activity_log.json"
    TRADE_HISTORY_FILE = "trade_history.json"

    def __init__(self, max_history: int = 100):
        """Initialize state aggregator.

        Args:
            max_history: Maximum number of historical records to keep
        """
        self._lock = threading.RLock()
        self._max_history = max_history

        # Current state
        self._opportunities: Dict[str, Opportunity] = {}
        self._executions: Dict[str, Execution] = {}
        self._mm_states: Dict[str, MarketMakerState] = {}
        self._nba_states: Dict[str, NBAGameState] = {}
        self._backtest_states: Dict[str, BacktestState] = {}
        self._live_positions: Dict[str, LivePositionState] = {}
        self._paper_orders: Dict[str, PaperOrderState] = {}
        self._paper_positions: Dict[str, PaperPositionState] = {}
        self._paper_fills: deque = deque(maxlen=200)
        self._paper_summary: Optional[PaperTradingSummary] = None
        self._scanner_state: Optional[ScannerState] = None
        self._metrics = DashboardMetrics()

        # Strategy session state
        self._strategy_session: Optional[StrategySession] = None

        # OMS metrics
        self._oms_metrics: Optional[OMSMetricsState] = None

        # History for charts
        self._opportunity_history: deque = deque(maxlen=max_history)
        self._profit_history: deque = deque(maxlen=max_history)
        self._position_history: deque = deque(maxlen=max_history)

        # Activity log
        self._activity_log: deque = deque(maxlen=200)

        # WebSocket subscribers
        self._subscribers: Set[asyncio.Queue] = set()

        # Start time
        self._start_time = time.time()

        # Load persisted state
        self._load_persisted_state()

    # ==================== Persistence ====================

    def _load_persisted_state(self) -> None:
        """Load persisted activity log and trade history from disk."""
        # Activity log
        activity_path = self.PERSIST_DIR / self.ACTIVITY_LOG_FILE
        if activity_path.exists():
            try:
                entries = json.loads(activity_path.read_text())
                for entry in entries[-200:]:  # Respect max size
                    self._activity_log.append(entry)
                logger.info("Loaded %d activity log entries", len(self._activity_log))
            except Exception as e:
                logger.warning("Failed to load activity log: %s", e)

        # Trade history (profit history)
        trade_path = self.PERSIST_DIR / self.TRADE_HISTORY_FILE
        if trade_path.exists():
            try:
                entries = json.loads(trade_path.read_text())
                for entry in entries[-100:]:
                    self._profit_history.append(entry)
                logger.info(
                    "Loaded %d trade history entries", len(self._profit_history)
                )
            except Exception as e:
                logger.warning("Failed to load trade history: %s", e)

    def persist_state(self) -> None:
        """Save activity log and trade history to disk."""
        try:
            self.PERSIST_DIR.mkdir(parents=True, exist_ok=True)

            with self._lock:
                # Activity log
                activity_path = self.PERSIST_DIR / self.ACTIVITY_LOG_FILE
                activity_path.write_text(
                    json.dumps(list(self._activity_log), indent=2, default=str)
                )

                # Trade history
                trade_path = self.PERSIST_DIR / self.TRADE_HISTORY_FILE
                trade_path.write_text(
                    json.dumps(list(self._profit_history), indent=2, default=str)
                )
        except Exception as e:
            logger.warning("Failed to persist dashboard state: %s", e)

    # ==================== Publishing Methods ====================

    def publish_opportunity(
        self,
        id: str,
        pair_id: str,
        event_description: str,
        opportunity_type: str,
        buy_platform: str,
        buy_price: float,
        sell_platform: str,
        sell_price: float,
        gross_edge: float,
        net_edge: float,
        max_contracts: int,
        estimated_profit: float,
        is_active: bool = True,
    ) -> None:
        """Publish an arbitrage opportunity."""
        now = datetime.now().isoformat()

        with self._lock:
            existing = self._opportunities.get(id)
            first_seen = existing.first_seen if existing else now

            opp = Opportunity(
                id=id,
                pair_id=pair_id,
                event_description=event_description,
                opportunity_type=opportunity_type,
                buy_platform=buy_platform,
                buy_price=buy_price,
                sell_platform=sell_platform,
                sell_price=sell_price,
                gross_edge=gross_edge,
                net_edge=net_edge,
                max_contracts=max_contracts,
                estimated_profit=estimated_profit,
                first_seen=first_seen,
                last_seen=now,
                is_active=is_active,
            )
            self._opportunities[id] = opp
            self._update_metrics()

            # Add to history
            self._opportunity_history.append(
                {
                    "timestamp": now,
                    "count": len(
                        [o for o in self._opportunities.values() if o.is_active]
                    ),
                }
            )

        self._broadcast("opportunity", asdict(opp))

    def remove_opportunity(self, id: str) -> None:
        """Remove an opportunity (mark as inactive)."""
        with self._lock:
            if id in self._opportunities:
                self._opportunities[id].is_active = False
                self._update_metrics()
                self._broadcast("opportunity_removed", {"id": id})

    def publish_execution(
        self,
        id: str,
        opportunity_id: str,
        status: str,
        leg1_exchange: str,
        leg1_ticker: str,
        leg1_side: str,
        leg1_price: float,
        leg1_filled: bool,
        leg2_exchange: str,
        leg2_ticker: str,
        leg2_side: str,
        leg2_price: float,
        leg2_filled: bool,
        expected_profit: float,
        actual_profit: Optional[float] = None,
        error: Optional[str] = None,
    ) -> None:
        """Publish an execution update."""
        now = datetime.now().isoformat()

        with self._lock:
            existing = self._executions.get(id)
            started_at = existing.started_at if existing else now
            completed_at = (
                now if status in ("completed", "failed", "rolled_back") else None
            )

            execution = Execution(
                id=id,
                opportunity_id=opportunity_id,
                status=status,
                leg1_exchange=leg1_exchange,
                leg1_ticker=leg1_ticker,
                leg1_side=leg1_side,
                leg1_price=leg1_price,
                leg1_filled=leg1_filled,
                leg2_exchange=leg2_exchange,
                leg2_ticker=leg2_ticker,
                leg2_side=leg2_side,
                leg2_price=leg2_price,
                leg2_filled=leg2_filled,
                expected_profit=expected_profit,
                actual_profit=actual_profit,
                started_at=started_at,
                completed_at=completed_at,
                error=error,
            )
            self._executions[id] = execution
            self._update_metrics()

            # Add profit to history if completed
            if status == "completed" and actual_profit is not None:
                self._profit_history.append(
                    {
                        "timestamp": now,
                        "profit": actual_profit,
                        "cumulative": self._metrics.total_profit,
                    }
                )

        self._broadcast("execution", asdict(execution))

    def publish_mm_state(
        self,
        ticker: str,
        position: int,
        cash: float,
        total_fees: float,
        mtm_pnl: float,
        gross_pnl: float,
        sigma: float,
        last_ref: float,
        active_bid: Optional[float],
        active_ask: Optional[float],
        reservation_price: Optional[float] = None,
        half_spread: Optional[float] = None,
        total_volume: int = 0,
        fills_received: int = 0,
        running: bool = True,
        dry_run: bool = True,
    ) -> None:
        """Publish market maker bot state."""
        now = datetime.now().isoformat()

        with self._lock:
            state = MarketMakerState(
                ticker=ticker,
                position=position,
                cash=cash,
                total_fees=total_fees,
                mtm_pnl=mtm_pnl,
                gross_pnl=gross_pnl,
                sigma=sigma,
                last_ref=last_ref,
                active_bid=active_bid,
                active_ask=active_ask,
                reservation_price=reservation_price,
                half_spread=half_spread,
                total_volume=total_volume,
                fills_received=fills_received,
                running=running,
                dry_run=dry_run,
                updated_at=now,
            )
            self._mm_states[ticker] = state
            self._update_metrics()

            # Add position to history
            self._position_history.append(
                {
                    "timestamp": now,
                    "ticker": ticker,
                    "position": position,
                }
            )

        self._broadcast("mm_state", asdict(state))

    def publish_nba_state(
        self,
        game_id: str,
        home_team: str,
        away_team: str,
        home_score: int,
        away_score: int,
        period: int,
        time_remaining: str,
        home_win_prob: float,
        market_price: float,
        edge_cents: float,
        is_trading_allowed: bool,
        last_signal: Optional[str] = None,
        position: int = 0,
    ) -> None:
        """Publish NBA game state."""
        now = datetime.now().isoformat()

        with self._lock:
            state = NBAGameState(
                game_id=game_id,
                home_team=home_team,
                away_team=away_team,
                home_score=home_score,
                away_score=away_score,
                period=period,
                time_remaining=time_remaining,
                home_win_prob=home_win_prob,
                market_price=market_price,
                edge_cents=edge_cents,
                is_trading_allowed=is_trading_allowed,
                last_signal=last_signal,
                position=position,
                updated_at=now,
            )
            self._nba_states[game_id] = state
            self._update_metrics()

        self._broadcast("nba_state", asdict(state))

    def publish_scanner_update(self, data: Dict[str, Any]) -> None:
        """Publish scanner results to dashboard.

        Args:
            data: Scanner results dict with keys:
                - timestamp: ISO timestamp
                - opportunities: List of opportunity dicts
                - summary: Dict with total_scanned, found, top_spread
        """
        now = datetime.now().isoformat()

        with self._lock:
            opportunities = []
            for opp in data.get("opportunities", []):
                opportunities.append(
                    ScannerOpportunity(
                        ticker=opp.get("ticker", ""),
                        event_ticker=opp.get("event_ticker", ""),
                        spread_cents=opp.get("spread_cents", 0),
                        volume=opp.get("volume", 0),
                        yes_bid=opp.get("yes_bid", 0),
                        yes_ask=opp.get("yes_ask", 0),
                        mid_price=opp.get("mid_price", 0),
                        category=opp.get("category", ""),
                        volatility_score=opp.get("volatility_score", 0),
                        scanned_at=opp.get("scanned_at", now),
                    )
                )

            summary = data.get("summary", {})
            self._scanner_state = ScannerState(
                last_scan=data.get("timestamp", now),
                total_scanned=summary.get("total_scanned", 0),
                opportunities_found=summary.get("found", len(opportunities)),
                opportunities=opportunities,
                top_spread=summary.get("top_spread", 0),
                scan_duration_ms=data.get("scan_duration_ms", 0),
            )

        self._broadcast(
            "scanner_update",
            {
                "last_scan": self._scanner_state.last_scan,
                "total_scanned": self._scanner_state.total_scanned,
                "opportunities_found": self._scanner_state.opportunities_found,
                "top_spread": self._scanner_state.top_spread,
                "opportunities": [
                    asdict(o) for o in self._scanner_state.opportunities[:50]
                ],
            },
        )

    def get_scanner_state(self) -> Optional[ScannerState]:
        """Get current scanner state."""
        with self._lock:
            return self._scanner_state

    def log_activity(
        self,
        strategy: str,
        event_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log algorithm activity for display on dashboard.

        Args:
            strategy: Which strategy ("nba", "arb", "mm")
            event_type: Type of event ("signal", "order", "fill", "decision", "error")
            message: Human-readable message
            details: Optional dict with additional details
        """
        now = datetime.now().isoformat()

        entry = {
            "timestamp": now,
            "strategy": strategy,
            "event_type": event_type,
            "message": message,
            "details": details or {},
        }

        with self._lock:
            self._activity_log.append(entry)
            should_persist = len(self._activity_log) % 50 == 0

        self._broadcast("activity", entry)

        if should_persist:
            self.persist_state()

    def publish_backtest_state(
        self,
        id: str,
        strategy: str,
        data_source: str,
        status: str,
        progress_pct: float = 0.0,
        frames_processed: int = 0,
        total_frames: int = 0,
        signals_generated: int = 0,
        trades_executed: int = 0,
        current_pnl: float = 0.0,
        error: Optional[str] = None,
        created_at: str = "",
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Publish backtest state update."""
        with self._lock:
            state = BacktestState(
                id=id,
                strategy=strategy,
                data_source=data_source,
                status=status,
                progress_pct=progress_pct,
                frames_processed=frames_processed,
                total_frames=total_frames,
                signals_generated=signals_generated,
                trades_executed=trades_executed,
                current_pnl=current_pnl,
                error=error,
                created_at=created_at,
                started_at=started_at,
                completed_at=completed_at,
                result=result,
            )
            self._backtest_states[id] = state
            self._update_metrics()

        self._broadcast("backtest_state", asdict(state))

    def publish_backtest_progress(
        self,
        id: str,
        frames_processed: int,
        total_frames: int,
        signals_generated: int,
        trades_executed: int,
        current_pnl: float,
        equity_curve: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Publish backtest progress update (lightweight, frequent)."""
        progress_pct = (
            (frames_processed / total_frames * 100) if total_frames > 0 else 0
        )

        with self._lock:
            if id in self._backtest_states:
                state = self._backtest_states[id]
                state.progress_pct = progress_pct
                state.frames_processed = frames_processed
                state.total_frames = total_frames
                state.signals_generated = signals_generated
                state.trades_executed = trades_executed
                state.current_pnl = current_pnl

        self._broadcast(
            "backtest_progress",
            {
                "id": id,
                "progress_pct": progress_pct,
                "frames_processed": frames_processed,
                "total_frames": total_frames,
                "signals_generated": signals_generated,
                "trades_executed": trades_executed,
                "current_pnl": current_pnl,
                "equity_curve": equity_curve or [],
            },
        )

    def publish_oms_metrics(self, metrics: Dict[str, Any]) -> None:
        """Publish OMS metrics snapshot from OrderManagementSystem.get_metrics()."""
        now = datetime.now().isoformat()
        fill_stats = metrics.get("fill_stats", {})

        with self._lock:
            self._oms_metrics = OMSMetricsState(
                pending_orders=metrics.get("pending_orders", 0),
                active_orders=metrics.get("active_orders", 0),
                total_tracked_orders=metrics.get("total_tracked_orders", 0),
                orders_by_status=metrics.get("orders_by_status", {}),
                orders_by_exchange=metrics.get("orders_by_exchange", {}),
                total_filled_contracts=fill_stats.get("total_filled_contracts", 0),
                total_filled_value=fill_stats.get("total_filled_value", 0.0),
                avg_fill_price=fill_stats.get("avg_fill_price"),
                avg_fill_time_seconds=fill_stats.get("avg_fill_time_seconds"),
                fill_count=fill_stats.get("fill_count", 0),
                failed_orders=metrics.get("failed_orders", 0),
                failure_reasons=metrics.get("failure_reasons", {}),
                constraint_violations=metrics.get("constraint_violations", 0),
                constrained_tickers=metrics.get("constrained_tickers", 0),
                exchanges_registered=metrics.get("exchanges_registered", 0),
                timeout_registered=metrics.get("timeout_registered", 0),
                positions=metrics.get("positions", {}),
                total_exposure=metrics.get("total_exposure", 0.0),
                capital=metrics.get("capital"),
                updated_at=now,
            )

        self._broadcast("oms_metrics", asdict(self._oms_metrics))

    def get_oms_metrics(self) -> Optional[OMSMetricsState]:
        """Get current OMS metrics state."""
        with self._lock:
            return self._oms_metrics

    def remove_backtest(self, id: str) -> None:
        """Remove a backtest from state."""
        with self._lock:
            if id in self._backtest_states:
                del self._backtest_states[id]
                self._update_metrics()
                self._broadcast("backtest_removed", {"id": id})

    # ==================== Live Position Methods ====================

    def publish_live_position(
        self,
        exchange: str,
        ticker: str,
        size: int,
        entry_price: float,
        current_price: float,
        unrealized_pnl: float,
        realized_pnl: float,
        exposure: float,
        opened_at: Optional[str] = None,
    ) -> None:
        """Publish a live position update from the OMS."""
        now = datetime.now().isoformat()
        key = f"{exchange}:{ticker}"

        with self._lock:
            state = LivePositionState(
                exchange=exchange,
                ticker=ticker,
                size=size,
                entry_price=entry_price,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
                exposure=exposure,
                opened_at=opened_at,
                updated_at=now,
            )
            self._live_positions[key] = state

        self._broadcast("live_position", asdict(state))

    def remove_live_position(self, exchange: str, ticker: str) -> None:
        """Remove a live position (when closed)."""
        key = f"{exchange}:{ticker}"
        with self._lock:
            if key in self._live_positions:
                del self._live_positions[key]
                self._broadcast(
                    "live_position_removed", {"exchange": exchange, "ticker": ticker}
                )

    # ==================== Paper Trading Methods ====================

    def publish_paper_order(
        self,
        order_id: str,
        ticker: str,
        side: str,
        price: float,
        size: int,
        filled_size: int,
        status: str,
        created_at: str,
    ) -> None:
        """Publish a paper order update."""
        now = datetime.now().isoformat()

        with self._lock:
            state = PaperOrderState(
                order_id=order_id,
                ticker=ticker,
                side=side,
                price=price,
                size=size,
                filled_size=filled_size,
                status=status,
                created_at=created_at,
                updated_at=now,
            )
            self._paper_orders[order_id] = state

        self._broadcast("paper_order", asdict(state))

    def publish_paper_position(
        self,
        ticker: str,
        size: int,
        avg_entry_price: float,
        unrealized_pnl: float,
        realized_pnl: float,
        total_fees: float,
    ) -> None:
        """Publish a paper position update."""
        now = datetime.now().isoformat()

        with self._lock:
            state = PaperPositionState(
                ticker=ticker,
                size=size,
                avg_entry_price=avg_entry_price,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
                total_fees=total_fees,
                updated_at=now,
            )
            self._paper_positions[ticker] = state

        self._broadcast("paper_position", asdict(state))

    def publish_paper_fill(
        self,
        fill_id: str,
        order_id: str,
        ticker: str,
        side: str,
        price: float,
        size: int,
        fee: float,
        timestamp: str,
    ) -> None:
        """Publish a paper fill."""
        state = PaperFillState(
            fill_id=fill_id,
            order_id=order_id,
            ticker=ticker,
            side=side,
            price=price,
            size=size,
            fee=fee,
            timestamp=timestamp,
        )

        with self._lock:
            self._paper_fills.append(asdict(state))

        self._broadcast("paper_fill", asdict(state))

    def publish_paper_summary(
        self,
        initial_balance: float,
        current_balance: float,
        total_realized_pnl: float,
        total_unrealized_pnl: float,
        total_pnl: float,
        total_fees: float,
    ) -> None:
        """Publish paper trading summary."""
        now = datetime.now().isoformat()

        with self._lock:
            self._paper_summary = PaperTradingSummary(
                initial_balance=initial_balance,
                current_balance=current_balance,
                total_realized_pnl=total_realized_pnl,
                total_unrealized_pnl=total_unrealized_pnl,
                total_pnl=total_pnl,
                total_fees=total_fees,
                updated_at=now,
            )

        self._broadcast("paper_summary", asdict(self._paper_summary))

    # ==================== Strategy Session Methods ====================

    def set_strategy_session(self, session: StrategySession) -> None:
        """Set the active strategy session."""
        with self._lock:
            self._strategy_session = session
        self._broadcast("strategy_session", asdict(session))

    def get_strategy_session(self) -> Optional[StrategySession]:
        """Get the current strategy session."""
        with self._lock:
            return self._strategy_session

    def update_strategy_position(self, position: StrategyPosition) -> None:
        """Update a position in the current session."""
        with self._lock:
            if self._strategy_session is None:
                return
            # Find and update or add position
            found = False
            for i, pos in enumerate(self._strategy_session.positions):
                if pos.ticker == position.ticker and pos.side == position.side:
                    self._strategy_session.positions[i] = position
                    found = True
                    break
            if not found:
                self._strategy_session.positions.append(position)
            # Recalculate unrealized P&L
            self._strategy_session.unrealized_pnl = sum(
                p.unrealized_pnl for p in self._strategy_session.positions
            )
            self._strategy_session.total_pnl = (
                self._strategy_session.realized_pnl
                + self._strategy_session.unrealized_pnl
            )
        self._broadcast("strategy_position", asdict(position))

    def remove_strategy_position(self, ticker: str, side: str) -> None:
        """Remove a position from the current session."""
        with self._lock:
            if self._strategy_session is None:
                return
            self._strategy_session.positions = [
                p
                for p in self._strategy_session.positions
                if not (p.ticker == ticker and p.side == side)
            ]
            # Recalculate unrealized P&L
            self._strategy_session.unrealized_pnl = sum(
                p.unrealized_pnl for p in self._strategy_session.positions
            )
            self._strategy_session.total_pnl = (
                self._strategy_session.realized_pnl
                + self._strategy_session.unrealized_pnl
            )
        self._broadcast("strategy_position_removed", {"ticker": ticker, "side": side})

    def add_strategy_trade(self, trade: StrategyTrade) -> None:
        """Add a trade to the current session."""
        with self._lock:
            if self._strategy_session is None:
                return
            self._strategy_session.trades.append(trade)
            self._strategy_session.trade_count += 1
            if trade.pnl is not None:
                self._strategy_session.realized_pnl += trade.pnl
                if trade.pnl > 0:
                    self._strategy_session.win_count += 1
                else:
                    self._strategy_session.loss_count += 1
            self._strategy_session.total_pnl = (
                self._strategy_session.realized_pnl
                + self._strategy_session.unrealized_pnl
            )
        self._broadcast("strategy_trade", asdict(trade))

    def update_strategy_game(self, game: GameState) -> None:
        """Update game state in current session."""
        with self._lock:
            if self._strategy_session is None:
                return
            self._strategy_session.game = game
        self._broadcast("game_state", asdict(game))

    def update_strategy_market(self, market: MarketState) -> None:
        """Update market state in current session."""
        with self._lock:
            if self._strategy_session is None:
                return
            self._strategy_session.market = market
        self._broadcast("market_state", asdict(market))

    def clear_strategy_session(self) -> None:
        """Clear the current strategy session."""
        with self._lock:
            self._strategy_session = None
        self._broadcast("strategy_stopped", {})
        self.persist_state()

    # ==================== Query Methods ====================

    def get_snapshot(self) -> Dict[str, Any]:
        """Get current state snapshot for initial load."""
        with self._lock:
            self._metrics.uptime_seconds = time.time() - self._start_time

            # Convert strategy session with nested dataclasses
            strategy_session_data = None
            if self._strategy_session:
                session_dict = asdict(self._strategy_session)
                strategy_session_data = session_dict

            return {
                "metrics": asdict(self._metrics),
                "opportunities": [asdict(o) for o in self._opportunities.values()],
                "executions": [asdict(e) for e in self._executions.values()],
                "mm_states": [asdict(s) for s in self._mm_states.values()],
                "nba_states": [asdict(s) for s in self._nba_states.values()],
                "backtest_states": [asdict(s) for s in self._backtest_states.values()],
                "live_positions": [asdict(p) for p in self._live_positions.values()],
                "paper_orders": [asdict(o) for o in self._paper_orders.values()],
                "paper_positions": [asdict(p) for p in self._paper_positions.values()],
                "paper_fills": list(self._paper_fills)[-50:],
                "paper_summary": asdict(self._paper_summary)
                if self._paper_summary
                else None,
                "strategy_session": strategy_session_data,
                "oms_metrics": asdict(self._oms_metrics) if self._oms_metrics else None,
                "activity_log": list(self._activity_log),
                "history": {
                    "opportunities": list(self._opportunity_history),
                    "profit": list(self._profit_history),
                    "position": list(self._position_history),
                },
            }

    def get_metrics(self) -> DashboardMetrics:
        """Get current metrics."""
        with self._lock:
            self._metrics.uptime_seconds = time.time() - self._start_time
            return self._metrics

    # ==================== WebSocket Support ====================

    def subscribe(self) -> asyncio.Queue:
        """Subscribe to state updates. Returns a queue for receiving updates."""
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        """Unsubscribe from state updates."""
        with self._lock:
            self._subscribers.discard(queue)

    def _broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast update to all subscribers."""
        message = {
            "type": event_type,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

        with self._lock:
            for queue in list(self._subscribers):
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    # Drop message if queue is full
                    pass

    def set_total_profit(self, profit: float) -> None:
        """Set the total profit directly (for simulations)."""
        with self._lock:
            self._metrics.total_profit = profit
        self._broadcast("metrics", {"total_profit": profit})

    def _update_metrics(self) -> None:
        """Update aggregate metrics."""
        self._metrics.active_opportunities = len(
            [o for o in self._opportunities.values() if o.is_active]
        )
        self._metrics.running_bots = len(
            [s for s in self._mm_states.values() if s.running]
        )
        self._metrics.tracked_games = len(self._nba_states)

        pending = 0
        completed = 0
        failed = 0

        for e in self._executions.values():
            if e.status in ("pending", "leg1_filled"):
                pending += 1
            elif e.status == "completed":
                completed += 1
            elif e.status in ("failed", "rolled_back"):
                failed += 1

        self._metrics.pending_executions = pending
        self._metrics.completed_executions = completed
        self._metrics.failed_executions = failed
        # Note: total_profit is managed separately via set_total_profit()

        # Backtest counts
        self._metrics.running_backtests = len(
            [s for s in self._backtest_states.values() if s.status == "running"]
        )
        self._metrics.completed_backtests = len(
            [s for s in self._backtest_states.values() if s.status == "completed"]
        )


# Global singleton instance
state_aggregator = StateAggregator()
