"""FastAPI application for the trading dashboard.

Provides:
- REST API endpoints for state queries
- WebSocket endpoint for real-time updates
- Static file serving for frontend
"""

import asyncio
from pathlib import Path
from typing import Any, Dict

from fastapi import (
    FastAPI,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    BackgroundTasks,
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .state import (
    state_aggregator,
    StrategySession,
    StrategyType,
)
from .backtest_manager import (
    backtest_manager,
    BacktestConfig,
    BacktestStatus,
    BacktestJob,
    BacktestProgress,
)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""

    app = FastAPI(
        title="Trading Dashboard",
        description="Real-time monitoring dashboard for trading algorithms",
        version="1.0.0",
    )

    # Static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ==================== REST Endpoints ====================

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the dashboard HTML."""
        template_path = Path(__file__).parent / "templates" / "index.html"
        if template_path.exists():
            return HTMLResponse(content=template_path.read_text())
        return HTMLResponse(content="<h1>Dashboard not found</h1>", status_code=404)

    @app.get("/api/snapshot")
    async def get_snapshot() -> Dict[str, Any]:
        """Get current state snapshot."""
        return state_aggregator.get_snapshot()

    @app.get("/api/metrics")
    async def get_metrics() -> Dict[str, Any]:
        """Get current metrics."""
        from dataclasses import asdict

        return asdict(state_aggregator.get_metrics())

    @app.get("/api/opportunities")
    async def get_opportunities() -> Dict[str, Any]:
        """Get active opportunities."""
        snapshot = state_aggregator.get_snapshot()
        return {"opportunities": snapshot["opportunities"]}

    @app.get("/api/executions")
    async def get_executions() -> Dict[str, Any]:
        """Get execution records."""
        snapshot = state_aggregator.get_snapshot()
        return {"executions": snapshot["executions"]}

    @app.get("/api/mm")
    async def get_mm_states() -> Dict[str, Any]:
        """Get market maker states."""
        snapshot = state_aggregator.get_snapshot()
        return {"mm_states": snapshot["mm_states"]}

    @app.get("/api/nba")
    async def get_nba_states() -> Dict[str, Any]:
        """Get NBA game states."""
        snapshot = state_aggregator.get_snapshot()
        return {"nba_states": snapshot["nba_states"]}

    @app.get("/api/scanner")
    async def get_scanner_state() -> Dict[str, Any]:
        """Get scanner opportunities."""
        scanner_state = state_aggregator.get_scanner_state()
        if scanner_state is None:
            return {"scanner": None, "opportunities": []}
        return {
            "scanner": {
                "last_scan": scanner_state.last_scan,
                "total_scanned": scanner_state.total_scanned,
                "opportunities_found": scanner_state.opportunities_found,
                "top_spread": scanner_state.top_spread,
            },
            "opportunities": [
                {
                    "ticker": o.ticker,
                    "spread_cents": o.spread_cents,
                    "volume": o.volume,
                    "yes_bid": o.yes_bid,
                    "yes_ask": o.yes_ask,
                    "volatility_score": o.volatility_score,
                    "category": o.category,
                }
                for o in scanner_state.opportunities[:50]
            ],
        }

    @app.get("/api/oms/metrics")
    async def get_oms_metrics() -> Dict[str, Any]:
        """Get OMS metrics."""
        oms_metrics = state_aggregator.get_oms_metrics()
        if oms_metrics is None:
            return {"oms_metrics": None}
        from dataclasses import asdict

        return {"oms_metrics": asdict(oms_metrics)}

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok"}

    # ==================== Strategy Control Endpoints ====================

    # Track the active strategy runner
    _active_runner: Dict[str, Any] = {"runner": None, "task": None}

    class StartStrategyRequest(BaseModel):
        """Request body for starting a strategy."""

        strategy: str  # "underdog_scalp", "late_game_blowout", "nba_mispricing"
        mode: str  # "paper" or "live"
        config: Dict[str, Any] = {}

    @app.post("/api/strategy/start")
    async def start_strategy(
        request: StartStrategyRequest,
        background_tasks: BackgroundTasks,
    ) -> Dict[str, Any]:
        """Start a strategy session."""
        from datetime import datetime

        # Validate strategy type
        valid_strategies = [s.value for s in StrategyType]
        if request.strategy not in valid_strategies:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid strategy: {request.strategy}. Must be one of {valid_strategies}",
            )

        # Validate mode
        if request.mode not in ("paper", "live"):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid mode: {request.mode}. Must be 'paper' or 'live'",
            )

        # Check if a strategy is already running
        if _active_runner["runner"] is not None:
            raise HTTPException(
                status_code=400,
                detail="A strategy is already running. Stop it first.",
            )

        # Create initial session
        session = StrategySession(
            strategy_type=request.strategy,
            mode=request.mode,
            status="running",
            started_at=datetime.now().isoformat(),
            positions=[],
            trades=[],
            game=None,
            market=None,
            total_pnl=0.0,
            realized_pnl=0.0,
            unrealized_pnl=0.0,
            trade_count=0,
            win_count=0,
            loss_count=0,
        )

        state_aggregator.set_strategy_session(session)

        # Log activity
        state_aggregator.log_activity(
            strategy="strategy",
            event_type="signal",
            message=f"Started {request.strategy} in {request.mode} mode",
            details={"strategy": request.strategy, "mode": request.mode},
        )

        # Import and start the strategy runner in background
        try:
            from .strategy_runner import StrategyRunner

            runner = StrategyRunner(
                strategy_type=request.strategy,
                mode=request.mode,
                config=request.config,
            )
            _active_runner["runner"] = runner

            async def run_strategy():
                try:
                    await runner.run()
                except Exception as e:
                    state_aggregator.log_activity(
                        strategy="strategy",
                        event_type="error",
                        message=f"Strategy error: {str(e)}",
                    )
                finally:
                    _active_runner["runner"] = None
                    _active_runner["task"] = None

            task = asyncio.create_task(run_strategy())
            _active_runner["task"] = task

        except ImportError:
            # Strategy runner not yet implemented - session still created for UI testing
            pass

        return {
            "status": "started",
            "strategy": request.strategy,
            "mode": request.mode,
            "started_at": session.started_at,
        }

    @app.post("/api/strategy/stop")
    async def stop_strategy() -> Dict[str, Any]:
        """Stop the running strategy."""
        if _active_runner["runner"] is None:
            # Even if no runner, clear any session
            state_aggregator.clear_strategy_session()
            return {"status": "stopped", "message": "No strategy was running"}

        try:
            await _active_runner["runner"].stop()
        except Exception as e:
            state_aggregator.log_activity(
                strategy="strategy",
                event_type="error",
                message=f"Error stopping strategy: {str(e)}",
            )

        state_aggregator.clear_strategy_session()
        _active_runner["runner"] = None
        _active_runner["task"] = None

        state_aggregator.log_activity(
            strategy="strategy",
            event_type="signal",
            message="Strategy stopped",
        )

        return {"status": "stopped"}

    @app.get("/api/strategy/status")
    async def get_strategy_status() -> Dict[str, Any]:
        """Get current strategy session state."""
        session = state_aggregator.get_strategy_session()
        if session is None:
            return {"status": "no_session", "session": None}

        from dataclasses import asdict

        return {"status": "ok", "session": asdict(session)}

    @app.get("/api/games/live")
    async def get_live_games() -> Dict[str, Any]:
        """Get list of live NBA games with their Kalshi markets."""
        try:
            from kalshi_utils.client_wrapper import KalshiWrapped
            from nba_utils.fetch import get_nbalive_games

            # Get live games from NBA API
            nba_games = get_nbalive_games()

            # Get Kalshi markets
            client = KalshiWrapped()
            markets = client.GetLiveNBAMarkets()

            # Match games to markets
            games = []
            for game in nba_games:
                matchup = game.get("matchup", "")
                # Find matching markets (home team win, away team win)
                game_markets = [m for m in markets if matchup in m.ticker]

                if game_markets:
                    # Parse team codes from matchup (e.g., "BOSLAL" -> BOS @ LAL)
                    away_code = matchup[:3]
                    home_code = matchup[3:]

                    games.append(
                        {
                            "game_id": game.get("id"),
                            "matchup": matchup,
                            "display": f"{away_code} @ {home_code}",
                            "score": game.get("score"),
                            "clock": game.get("clock"),
                            "markets": [
                                {
                                    "ticker": m.ticker,
                                    "team": m.yes_sub_title
                                    if hasattr(m, "yes_sub_title")
                                    else "",
                                    "yes_bid": getattr(m, "yes_bid", 0),
                                    "yes_ask": getattr(m, "yes_ask", 0),
                                }
                                for m in game_markets
                            ],
                        }
                    )

            return {"status": "ok", "games": games}

        except Exception as e:
            # Return empty list with error info
            return {"status": "error", "error": str(e), "games": []}

    class SelectGameRequest(BaseModel):
        """Request to select a game for monitoring."""

        ticker: str  # The Kalshi market ticker to monitor

    @app.post("/api/strategy/select-game")
    async def select_game(request: SelectGameRequest) -> Dict[str, Any]:
        """Select a game/market to monitor with WebSocket updates."""
        try:
            if _active_runner.get("runner"):
                runner = _active_runner["runner"]
                await runner.switch_market(request.ticker)
                return {"status": "ok", "ticker": request.ticker}
            else:
                return {"status": "error", "error": "No strategy running"}

        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ==================== Backtest Endpoints ====================

    class BacktestRequest(BaseModel):
        """Request body for starting a backtest."""

        strategy: str  # "nba_mispricing" or "crypto_latency"
        data_source: str  # Path to recording file

        # Common params
        min_edge_cents: float = 3.0
        position_size: int = 10

        # NBA-specific
        max_period: int = 2
        fill_probability: float = 0.8

        # Crypto-specific
        signal_stability_sec: float = 2.0
        kelly_fraction: float = 0.5
        bankroll: float = 100.0
        slippage_adjusted: bool = True
        market_cooldown: bool = True

    def _on_backtest_progress(job_id: str, progress: BacktestProgress) -> None:
        """Callback when backtest progress updates."""
        job = backtest_manager.get_job(job_id)
        if job:
            state_aggregator.publish_backtest_progress(
                id=job_id,
                frames_processed=progress.frames_processed,
                total_frames=progress.total_frames,
                signals_generated=progress.signals_generated,
                trades_executed=progress.trades_executed,
                current_pnl=progress.current_pnl,
                equity_curve=progress.current_equity,
            )

    def _on_backtest_status_change(job: BacktestJob) -> None:
        """Callback when backtest status changes."""
        progress_pct = 0.0
        if job.progress.total_frames > 0:
            progress_pct = (
                job.progress.frames_processed / job.progress.total_frames * 100
            )

        state_aggregator.publish_backtest_state(
            id=job.id,
            strategy=job.config.strategy.value,
            data_source=job.config.data_source,
            status=job.status.value,
            progress_pct=progress_pct,
            frames_processed=job.progress.frames_processed,
            total_frames=job.progress.total_frames,
            signals_generated=job.progress.signals_generated,
            trades_executed=job.progress.trades_executed,
            current_pnl=job.progress.current_pnl,
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            result=job.result,
        )

        # Log activity
        if job.status == BacktestStatus.RUNNING:
            state_aggregator.log_activity(
                strategy="backtest",
                event_type="signal",
                message=f"Started {job.config.strategy.value} backtest on {Path(job.config.data_source).name}",
                details={"job_id": job.id},
            )
        elif job.status == BacktestStatus.COMPLETED:
            pnl = job.result.get("metrics", {}).get("net_pnl") if job.result else None
            if pnl is None:
                pnl = (
                    job.result.get("metrics", {}).get("total_pnl") if job.result else 0
                )
            state_aggregator.log_activity(
                strategy="backtest",
                event_type="fill",
                message=f"Backtest completed: ${pnl:.2f} P&L",
                details={"job_id": job.id, "pnl": pnl},
            )
        elif job.status == BacktestStatus.FAILED:
            state_aggregator.log_activity(
                strategy="backtest",
                event_type="error",
                message=f"Backtest failed: {job.error}",
                details={"job_id": job.id},
            )

    # Set up callbacks
    backtest_manager.set_callbacks(
        on_progress=_on_backtest_progress,
        on_status_change=_on_backtest_status_change,
    )

    @app.get("/api/recordings")
    async def get_recordings() -> Dict[str, Any]:
        """Get available recording files for backtesting."""
        return backtest_manager.get_available_recordings()

    @app.get("/api/backtests")
    async def list_backtests() -> Dict[str, Any]:
        """List all backtest jobs."""
        jobs = backtest_manager.list_jobs()
        return {
            "backtests": [
                {
                    "id": j.id,
                    "strategy": j.config.strategy.value,
                    "data_source": j.config.data_source,
                    "status": j.status.value,
                    "progress_pct": (
                        j.progress.frames_processed / j.progress.total_frames * 100
                    )
                    if j.progress.total_frames > 0
                    else 0,
                    "current_pnl": j.progress.current_pnl,
                    "created_at": j.created_at,
                    "completed_at": j.completed_at,
                }
                for j in jobs
            ]
        }

    @app.post("/api/backtest/run")
    async def run_backtest(
        request: BacktestRequest,
        background_tasks: BackgroundTasks,
    ) -> Dict[str, Any]:
        """Start a new backtest job."""
        # Validate strategy
        try:
            strategy = StrategyType(request.strategy)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid strategy: {request.strategy}. Must be 'nba_mispricing' or 'crypto_latency'",
            )

        # Validate data source exists
        data_path = Path(request.data_source)
        if not data_path.exists():
            raise HTTPException(
                status_code=400,
                detail=f"Recording file not found: {request.data_source}",
            )

        # Create config
        config = BacktestConfig(
            strategy=strategy,
            data_source=request.data_source,
            min_edge_cents=request.min_edge_cents,
            position_size=request.position_size,
            max_period=request.max_period,
            fill_probability=request.fill_probability,
            signal_stability_sec=request.signal_stability_sec,
            kelly_fraction=request.kelly_fraction,
            bankroll=request.bankroll,
            slippage_adjusted=request.slippage_adjusted,
            market_cooldown=request.market_cooldown,
        )

        # Create job
        job_id = backtest_manager.create_job(config)

        # Run in background
        async def run_backtest_task():
            await backtest_manager.run_job(job_id)

        background_tasks.add_task(run_backtest_task)

        return {
            "job_id": job_id,
            "status": "pending",
            "message": "Backtest job created and queued",
        }

    @app.get("/api/backtest/{job_id}")
    async def get_backtest(job_id: str) -> Dict[str, Any]:
        """Get backtest job status and results."""
        job = backtest_manager.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=404, detail=f"Backtest job not found: {job_id}"
            )

        progress_pct = 0.0
        if job.progress.total_frames > 0:
            progress_pct = (
                job.progress.frames_processed / job.progress.total_frames * 100
            )

        return {
            "id": job.id,
            "strategy": job.config.strategy.value,
            "data_source": job.config.data_source,
            "config": {
                "min_edge_cents": job.config.min_edge_cents,
                "position_size": job.config.position_size,
                "max_period": job.config.max_period,
                "fill_probability": job.config.fill_probability,
                "signal_stability_sec": job.config.signal_stability_sec,
                "kelly_fraction": job.config.kelly_fraction,
                "bankroll": job.config.bankroll,
            },
            "status": job.status.value,
            "progress": {
                "pct": progress_pct,
                "frames_processed": job.progress.frames_processed,
                "total_frames": job.progress.total_frames,
                "signals_generated": job.progress.signals_generated,
                "trades_executed": job.progress.trades_executed,
                "current_pnl": job.progress.current_pnl,
            },
            "result": job.result,
            "error": job.error,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        }

    @app.post("/api/backtest/{job_id}/cancel")
    async def cancel_backtest(job_id: str) -> Dict[str, Any]:
        """Cancel a running backtest job."""
        success = backtest_manager.cancel_job(job_id)
        if not success:
            job = backtest_manager.get_job(job_id)
            if not job:
                raise HTTPException(
                    status_code=404, detail=f"Backtest job not found: {job_id}"
                )
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel job in status: {job.status.value}",
            )

        return {"status": "cancelled", "job_id": job_id}

    @app.delete("/api/backtest/{job_id}")
    async def delete_backtest(job_id: str) -> Dict[str, Any]:
        """Delete a completed/cancelled/failed backtest job."""
        success = backtest_manager.delete_job(job_id)
        if not success:
            job = backtest_manager.get_job(job_id)
            if not job:
                raise HTTPException(
                    status_code=404, detail=f"Backtest job not found: {job_id}"
                )
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete job in status: {job.status.value}. Must be completed, cancelled, or failed.",
            )

        # Also remove from state aggregator
        state_aggregator.remove_backtest(job_id)

        return {"status": "deleted", "job_id": job_id}

    # ==================== WebSocket Endpoint ====================

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time updates."""
        await websocket.accept()

        # Subscribe to state updates
        queue = state_aggregator.subscribe()

        try:
            # Send initial snapshot
            snapshot = state_aggregator.get_snapshot()
            await websocket.send_json({"type": "snapshot", "data": snapshot})

            # Listen for updates
            while True:
                try:
                    # Wait for updates with timeout to allow heartbeat
                    message = await asyncio.wait_for(queue.get(), timeout=30.0)
                    await websocket.send_json(message)
                except asyncio.TimeoutError:
                    # Send heartbeat
                    await websocket.send_json({"type": "heartbeat"})

        except WebSocketDisconnect:
            pass
        finally:
            state_aggregator.unsubscribe(queue)

    # ==================== Demo Data Endpoint ====================

    # ==================== Spread Scanner Endpoints ====================

    from .spread_scanner_api import (
        SpreadScanConfig,
        TradeRequest,
        get_scanner_state,
        refresh_current_spreads,
        discover_new_opportunities,
        create_trade_log,
    )

    @app.get("/api/spreads/config")
    async def get_spread_config() -> Dict[str, Any]:
        """Get current spread scanner configuration."""
        state = get_scanner_state()
        return state.config.dict()

    @app.post("/api/spreads/config")
    async def update_spread_config(config: SpreadScanConfig) -> Dict[str, Any]:
        """Update spread scanner configuration."""
        state = get_scanner_state()
        state.update_config(config)
        return {"status": "ok", "config": config.dict()}

    @app.post("/api/spreads/refresh")
    async def api_refresh_spreads() -> Dict[str, Any]:
        """Fast refresh: Update quotes for current display spreads."""
        opportunities = await refresh_current_spreads()
        state = get_scanner_state()
        return {
            "opportunities": [opp.dict() for opp in opportunities],
            "last_updated": state.last_display_refresh,
            "has_new_data": state.has_new_opportunities(),
        }

    @app.post("/api/spreads/discover")
    async def api_discover_spreads(background_tasks: BackgroundTasks) -> Dict[str, Any]:
        """Slow scan: Full discovery of new opportunities."""

        # Run in background to avoid blocking
        async def discover_task():
            await discover_new_opportunities()

        background_tasks.add_task(discover_task)

        return {
            "status": "scanning",
            "message": "Background scan started",
        }

    @app.get("/api/spreads/display")
    async def get_current_display() -> Dict[str, Any]:
        """Get currently displayed spreads."""
        state = get_scanner_state()
        return {
            "opportunities": [opp.dict() for opp in state.current_display],
            "last_updated": state.last_display_refresh,
            "has_new_data": state.has_new_opportunities(),
        }

    @app.get("/api/spreads/background")
    async def get_background_cache() -> Dict[str, Any]:
        """Get background cached spreads."""
        state = get_scanner_state()
        return {
            "opportunities": [opp.dict() for opp in state.background_cache],
            "last_scanned": state.last_background_scan,
        }

    @app.post("/api/spreads/sync")
    async def sync_display() -> Dict[str, Any]:
        """Sync background cache to display."""
        state = get_scanner_state()
        state.sync_background_to_display()
        return {
            "status": "ok",
            "opportunities": [opp.dict() for opp in state.current_display],
            "last_updated": state.last_display_refresh,
        }

    @app.post("/api/spreads/trade")
    async def mock_trade(request: TradeRequest) -> Dict[str, Any]:
        """Execute a mock trade (logs only, no actual execution)."""
        state = get_scanner_state()

        # Find the opportunity
        opp = next(
            (o for o in state.current_display if o.spread_id == request.spread_id), None
        )
        if not opp:
            raise HTTPException(
                status_code=404, detail="Spread not found in current display"
            )

        # Create trade log
        trade_log = create_trade_log(opp, request.amount)
        state.add_trade_log(trade_log)

        return {
            "status": "MOCK_TRADE",
            "trade_log": trade_log.dict(),
        }

    @app.get("/api/spreads/logs")
    async def get_trade_logs() -> Dict[str, Any]:
        """Get all trade logs."""
        state = get_scanner_state()
        return {
            "logs": [log.dict() for log in state.trade_logs],
            "count": len(state.trade_logs),
        }

    @app.delete("/api/spreads/logs")
    async def clear_trade_logs() -> Dict[str, Any]:
        """Clear all trade logs."""
        state = get_scanner_state()
        state.clear_trade_logs()
        return {"status": "ok", "message": "Logs cleared"}

    @app.post("/api/demo/start")
    async def start_demo():
        """Start demo mode with simulated data."""
        import random
        import threading
        import time

        def generate_demo_data():
            """Generate demo data in background."""
            opportunity_id = 0

            while True:
                # Random opportunity
                opportunity_id += 1
                state_aggregator.publish_opportunity(
                    id=f"OPP-{opportunity_id:04d}",
                    pair_id=f"pair_{random.randint(1, 10)}",
                    event_description=f"Demo Event {random.randint(100, 999)}",
                    opportunity_type=random.choice(
                        ["cross_platform_arb", "dutch_book"]
                    ),
                    buy_platform=random.choice(["kalshi", "polymarket"]),
                    buy_price=random.uniform(0.3, 0.5),
                    sell_platform=random.choice(["kalshi", "polymarket"]),
                    sell_price=random.uniform(0.5, 0.7),
                    gross_edge=random.uniform(0.05, 0.15),
                    net_edge=random.uniform(0.02, 0.10),
                    max_contracts=random.randint(50, 200),
                    estimated_profit=random.uniform(5, 50),
                )

                # Random MM state
                state_aggregator.publish_mm_state(
                    ticker="DEMO-MARKET",
                    position=random.randint(-10, 10),
                    cash=random.uniform(-100, 100),
                    total_fees=random.uniform(0, 10),
                    mtm_pnl=random.uniform(-50, 50),
                    gross_pnl=random.uniform(-40, 60),
                    sigma=random.uniform(0.01, 0.1),
                    last_ref=random.uniform(0.4, 0.6),
                    active_bid=random.uniform(0.4, 0.5),
                    active_ask=random.uniform(0.5, 0.6),
                    reservation_price=0.5,
                    half_spread=0.05,
                    total_volume=random.randint(0, 100),
                    fills_received=random.randint(0, 50),
                    running=True,
                    dry_run=True,
                )

                # Random NBA state
                state_aggregator.publish_nba_state(
                    game_id="demo_game",
                    home_team="LAL",
                    away_team="BOS",
                    home_score=random.randint(80, 120),
                    away_score=random.randint(80, 120),
                    period=random.randint(1, 4),
                    time_remaining=f"{random.randint(0, 12)}:{random.randint(0, 59):02d}",
                    home_win_prob=random.uniform(0.3, 0.7),
                    market_price=random.uniform(0.4, 0.6),
                    edge_cents=random.uniform(-5, 5),
                    is_trading_allowed=random.choice([True, False]),
                    last_signal=random.choice(["BUY YES", "BUY NO", None]),
                    position=random.randint(-10, 10),
                )

                time.sleep(2)

        # Start background thread
        thread = threading.Thread(target=generate_demo_data, daemon=True)
        thread.start()

        return {"status": "demo started"}

    return app
