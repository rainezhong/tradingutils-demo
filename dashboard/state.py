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
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set
from collections import deque
import threading


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


class StateAggregator:
    """Central state aggregator for dashboard updates.

    Thread-safe state management with WebSocket broadcast support.
    """

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
        self._metrics = DashboardMetrics()

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
            self._opportunity_history.append({
                "timestamp": now,
                "count": len([o for o in self._opportunities.values() if o.is_active]),
            })

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
            completed_at = now if status in ("completed", "failed", "rolled_back") else None

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
                self._profit_history.append({
                    "timestamp": now,
                    "profit": actual_profit,
                    "cumulative": self._metrics.total_profit,
                })

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
            self._position_history.append({
                "timestamp": now,
                "ticker": ticker,
                "position": position,
            })

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

        self._broadcast("activity", entry)

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
        progress_pct = (frames_processed / total_frames * 100) if total_frames > 0 else 0

        with self._lock:
            if id in self._backtest_states:
                state = self._backtest_states[id]
                state.progress_pct = progress_pct
                state.frames_processed = frames_processed
                state.total_frames = total_frames
                state.signals_generated = signals_generated
                state.trades_executed = trades_executed
                state.current_pnl = current_pnl

        self._broadcast("backtest_progress", {
            "id": id,
            "progress_pct": progress_pct,
            "frames_processed": frames_processed,
            "total_frames": total_frames,
            "signals_generated": signals_generated,
            "trades_executed": trades_executed,
            "current_pnl": current_pnl,
            "equity_curve": equity_curve or [],
        })

    def remove_backtest(self, id: str) -> None:
        """Remove a backtest from state."""
        with self._lock:
            if id in self._backtest_states:
                del self._backtest_states[id]
                self._update_metrics()
                self._broadcast("backtest_removed", {"id": id})

    # ==================== Query Methods ====================

    def get_snapshot(self) -> Dict[str, Any]:
        """Get current state snapshot for initial load."""
        with self._lock:
            self._metrics.uptime_seconds = time.time() - self._start_time

            return {
                "metrics": asdict(self._metrics),
                "opportunities": [asdict(o) for o in self._opportunities.values()],
                "executions": [asdict(e) for e in self._executions.values()],
                "mm_states": [asdict(s) for s in self._mm_states.values()],
                "nba_states": [asdict(s) for s in self._nba_states.values()],
                "backtest_states": [asdict(s) for s in self._backtest_states.values()],
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
        message = {"type": event_type, "data": data, "timestamp": datetime.now().isoformat()}

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
