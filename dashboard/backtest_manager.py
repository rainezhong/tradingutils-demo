"""Backtest Manager - Runs and manages backtests from the dashboard.

Provides:
- Job queue for backtest requests
- Support for multiple strategies (NBA, crypto)
- Real-time progress updates via WebSocket
- Async execution with cancellation support
"""

import asyncio
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import threading


class BacktestStatus(str, Enum):
    """Backtest job status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StrategyType(str, Enum):
    """Supported backtest strategies."""
    NBA_MISPRICING = "nba_mispricing"
    NBA_BLOWOUT = "nba_blowout"
    CRYPTO_LATENCY = "crypto_latency"


@dataclass
class BacktestConfig:
    """Configuration for a backtest run."""
    strategy: StrategyType
    data_source: str  # Path to recording file or "all" for all recordings

    # Common params
    min_edge_cents: float = 3.0
    position_size: int = 10

    # NBA Mispricing-specific
    # NOTE: Model only works reliably before halftime (Q1-Q2)
    # Late game is too volatile and markets are more efficient
    max_period: int = 2  # 2 = first half only (recommended)
    fill_probability: float = 0.8

    # NBA Blowout-specific (late game strategy)
    min_point_differential: int = 12  # Minimum lead to trigger
    max_time_remaining_seconds: int = 600  # 10 minutes = 600 seconds
    blowout_position_size: float = 5.0  # Base position size in $

    # Crypto-specific
    signal_stability_sec: float = 2.0
    kelly_fraction: float = 0.5
    bankroll: float = 100.0
    slippage_adjusted: bool = True
    market_cooldown: bool = True


@dataclass
class BacktestProgress:
    """Progress update during backtest."""
    frames_processed: int = 0
    total_frames: int = 0
    signals_generated: int = 0
    trades_executed: int = 0
    current_pnl: float = 0.0
    current_equity: List[Dict[str, Any]] = field(default_factory=list)
    current_trades: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class BacktestJob:
    """A backtest job with status tracking."""
    id: str
    config: BacktestConfig
    status: BacktestStatus = BacktestStatus.PENDING
    progress: BacktestProgress = field(default_factory=BacktestProgress)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()


class BacktestManager:
    """Manages backtest job execution and progress tracking.

    Usage:
        from dashboard.backtest_manager import backtest_manager

        # Create a backtest job
        job_id = backtest_manager.create_job(config)

        # Start running (call from async context)
        await backtest_manager.run_job(job_id)

        # Get job status
        job = backtest_manager.get_job(job_id)
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: Dict[str, BacktestJob] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._cancelled: set = set()

        # Callback for progress updates (set by app.py)
        self._on_progress: Optional[Callable[[str, BacktestProgress], None]] = None
        self._on_status_change: Optional[Callable[[BacktestJob], None]] = None

    def set_callbacks(
        self,
        on_progress: Callable[[str, BacktestProgress], None],
        on_status_change: Callable[[BacktestJob], None],
    ) -> None:
        """Set callbacks for progress and status updates."""
        self._on_progress = on_progress
        self._on_status_change = on_status_change

    def create_job(self, config: BacktestConfig) -> str:
        """Create a new backtest job.

        Returns:
            Job ID
        """
        job_id = f"bt_{uuid.uuid4().hex[:8]}"
        job = BacktestJob(id=job_id, config=config)

        with self._lock:
            self._jobs[job_id] = job

        if self._on_status_change:
            self._on_status_change(job)

        return job_id

    def get_job(self, job_id: str) -> Optional[BacktestJob]:
        """Get a job by ID."""
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> List[BacktestJob]:
        """List all jobs, most recent first."""
        with self._lock:
            return sorted(
                self._jobs.values(),
                key=lambda j: j.created_at,
                reverse=True
            )

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.status == BacktestStatus.RUNNING:
                self._cancelled.add(job_id)
                task = self._running_tasks.get(job_id)
                if task:
                    task.cancel()
                return True
            elif job.status == BacktestStatus.PENDING:
                job.status = BacktestStatus.CANCELLED
                job.completed_at = datetime.now().isoformat()
                if self._on_status_change:
                    self._on_status_change(job)
                return True

            return False

    def delete_job(self, job_id: str) -> bool:
        """Delete a completed/cancelled/failed job."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.status in (BacktestStatus.COMPLETED, BacktestStatus.CANCELLED, BacktestStatus.FAILED):
                del self._jobs[job_id]
                return True

            return False

    async def run_job(self, job_id: str) -> None:
        """Run a backtest job asynchronously."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.status != BacktestStatus.PENDING:
                return

            job.status = BacktestStatus.RUNNING
            job.started_at = datetime.now().isoformat()

        if self._on_status_change:
            self._on_status_change(job)

        try:
            if job.config.strategy == StrategyType.NBA_MISPRICING:
                await self._run_nba_backtest(job)
            elif job.config.strategy == StrategyType.NBA_BLOWOUT:
                await self._run_nba_blowout_backtest(job)
            elif job.config.strategy == StrategyType.CRYPTO_LATENCY:
                await self._run_crypto_backtest(job)
            else:
                raise ValueError(f"Unknown strategy: {job.config.strategy}")

            # Check if cancelled
            if job_id in self._cancelled:
                job.status = BacktestStatus.CANCELLED
            else:
                job.status = BacktestStatus.COMPLETED

        except asyncio.CancelledError:
            job.status = BacktestStatus.CANCELLED
        except Exception as e:
            job.status = BacktestStatus.FAILED
            job.error = str(e)
        finally:
            job.completed_at = datetime.now().isoformat()
            self._cancelled.discard(job_id)
            self._running_tasks.pop(job_id, None)

            if self._on_status_change:
                self._on_status_change(job)

    async def _run_nba_backtest(self, job: BacktestJob) -> None:
        """Run NBA mispricing backtest."""
        from src.simulation.nba_backtester import NBAStrategyBacktester
        from src.simulation.nba_recorder import NBAGameRecorder

        # Load recording
        recording_path = Path(job.config.data_source)
        if not recording_path.exists():
            raise FileNotFoundError(f"Recording not found: {recording_path}")

        recording = NBAGameRecorder.load(str(recording_path))

        # Initialize backtester
        backtester = NBAStrategyBacktester(
            recording=recording,
            min_edge_cents=job.config.min_edge_cents,
            max_period=job.config.max_period,
            position_size=job.config.position_size,
            fill_probability=job.config.fill_probability,
        )

        # Set total frames for progress
        job.progress.total_frames = len(recording.frames)

        # Run with progress tracking via custom run
        await self._run_nba_backtest_with_progress(job, backtester, recording)

    async def _run_nba_backtest_with_progress(
        self,
        job: BacktestJob,
        backtester,
        recording,
    ) -> None:
        """Run NBA backtest with real-time progress updates."""
        from src.kalshi.mock_client import MockKalshiClient
        from src.simulation.nba_replay import NBAGameReplay

        # Initialize mock client
        backtester.mock_client = MockKalshiClient(
            initial_balance=100000,
            fill_probability=job.config.fill_probability,
            auto_fill=True,
        )

        # Track fills
        backtester.mock_client.on_fill(backtester._on_fill)

        # Initialize replay
        backtester.replay = NBAGameReplay(recording, speed=1000.0)  # Fast replay

        # Reset tracking
        backtester.signals = []
        backtester.fills = []
        backtester._last_signal_time = 0.0

        frame_count = 0
        equity_curve = []
        price_history = []  # Track market prices for charting

        # Run replay with progress updates
        async for frame in backtester.replay.run(backtester.mock_client):
            # Check cancellation
            if job.id in self._cancelled:
                break

            frame_count += 1

            # Capture price data for charting (sample every 5 frames to reduce data size)
            if frame_count % 5 == 1 or frame_count == 1:
                home_mid = (frame.home_bid + frame.home_ask) / 2 if frame.home_bid and frame.home_ask else 0
                price_history.append({
                    "frame": frame_count,
                    "period": frame.period,
                    "time_remaining": frame.time_remaining,
                    "home_price": home_mid * 100,  # Convert to cents
                    "home_bid": frame.home_bid * 100 if frame.home_bid else 0,
                    "home_ask": frame.home_ask * 100 if frame.home_ask else 0,
                    "home_score": frame.home_score,
                    "away_score": frame.away_score,
                })

            # Evaluate for signals
            signal = backtester._evaluate_frame(frame, frame_count - 1)

            if signal:
                backtester.signals.append(signal)

                # Try to place order
                if backtester._can_trade(frame.timestamp):
                    await backtester._place_order(signal)

            # Update progress every 50 frames
            if frame_count % 50 == 0 or frame_count == len(recording.frames):
                # Calculate current P&L
                current_pnl = sum(
                    s.theoretical_pnl or 0
                    for s in backtester.signals
                    if s.filled
                )

                equity_curve.append({
                    "frame": frame_count,
                    "pnl": current_pnl,
                    "timestamp": frame.timestamp,
                })

                job.progress.frames_processed = frame_count
                job.progress.signals_generated = len(backtester.signals)
                job.progress.trades_executed = sum(1 for s in backtester.signals if s.filled)
                job.progress.current_pnl = current_pnl
                job.progress.current_equity = equity_curve[-20:]  # Last 20 points

                if self._on_progress:
                    self._on_progress(job.id, job.progress)

                # Yield to allow other tasks
                await asyncio.sleep(0)

        # Determine winner
        final_frame = recording.frames[-1] if recording.frames else None
        if final_frame:
            if final_frame.home_score > final_frame.away_score:
                winner = recording.home_team
            elif final_frame.away_score > final_frame.home_score:
                winner = recording.away_team
            else:
                winner = "TIE"
        else:
            winner = "UNKNOWN"

        # Calculate signal correctness
        backtester._evaluate_signal_correctness(winner)

        # Calculate final metrics
        metrics = backtester._calculate_metrics(frame_count)

        # Store result
        job.result = {
            "game_id": recording.game_id,
            "home_team": recording.home_team,
            "away_team": recording.away_team,
            "final_home_score": final_frame.home_score if final_frame else 0,
            "final_away_score": final_frame.away_score if final_frame else 0,
            "winner": winner,
            "metrics": {
                "total_frames": metrics.total_frames,
                "total_signals": metrics.total_signals,
                "signals_traded": metrics.signals_traded,
                "orders_filled": metrics.orders_filled,
                "avg_edge_cents": metrics.avg_edge_cents,
                "max_edge_cents": metrics.max_edge_cents,
                "correct_signals": metrics.correct_signals,
                "incorrect_signals": metrics.incorrect_signals,
                "accuracy_pct": metrics.accuracy_pct,
                "gross_pnl": metrics.gross_pnl,
                "fees": metrics.fees,
                "net_pnl": metrics.net_pnl,
                "signals_by_period": metrics.signals_by_period,
                "accuracy_by_period": metrics.accuracy_by_period,
            },
            "equity_curve": equity_curve,
            "price_history": price_history,
            "trades": [
                {
                    "frame_idx": s.frame_idx,
                    "timestamp": s.timestamp,
                    "period": s.period,
                    "time_remaining": s.time_remaining,
                    "direction": s.direction,
                    "edge_cents": s.edge_cents,
                    "home_win_prob": s.home_win_prob,
                    "market_mid": s.market_mid * 100,  # Convert to cents
                    "filled": s.filled,
                    "fill_price": s.fill_price * 100 if s.fill_price else None,  # Convert to cents
                    "correct": s.signal_correct,
                    "pnl": s.theoretical_pnl,
                }
                for s in backtester.signals
                if s.order_placed
            ],
        }

        # Final progress update
        job.progress.frames_processed = frame_count
        job.progress.current_trades = job.result["trades"][-50:]  # Last 50 trades

    async def _run_nba_blowout_backtest(self, job: BacktestJob) -> None:
        """Run NBA late-game blowout backtest."""
        from src.simulation.nba_backtester import BlowoutStrategyBacktester
        from src.simulation.nba_recorder import NBAGameRecorder

        # Load recording
        recording_path = Path(job.config.data_source)
        if not recording_path.exists():
            raise FileNotFoundError(f"Recording not found: {recording_path}")

        recording = NBAGameRecorder.load(str(recording_path))

        # Initialize backtester
        backtester = BlowoutStrategyBacktester(
            recording=recording,
            min_point_differential=job.config.min_point_differential,
            max_time_remaining_seconds=job.config.max_time_remaining_seconds,
            base_position_size=job.config.blowout_position_size,
        )

        # Set total frames for progress
        job.progress.total_frames = len(recording.frames)

        # Run backtest
        result = await backtester.run(verbose=False)

        # Build price history for charting (sample frames)
        price_history = []
        for i, frame in enumerate(recording.frames):
            if i % 10 == 0:  # Sample every 10 frames
                home_mid = (frame.home_bid + frame.home_ask) / 2 if frame.home_bid and frame.home_ask else 0
                price_history.append({
                    "frame": i,
                    "period": frame.period,
                    "time_remaining": frame.time_remaining,
                    "home_price": home_mid * 100,
                    "home_score": frame.home_score,
                    "away_score": frame.away_score,
                })

        # Update progress
        job.progress.frames_processed = len(recording.frames)
        job.progress.signals_generated = result.total_signals
        job.progress.trades_executed = result.signals_traded
        job.progress.current_pnl = result.net_pnl

        # Store result
        job.result = {
            "game_id": result.game_id,
            "home_team": result.home_team,
            "away_team": result.away_team,
            "final_home_score": result.final_home_score,
            "final_away_score": result.final_away_score,
            "winner": result.winner,
            "metrics": {
                "total_frames": result.total_frames,
                "total_signals": result.total_signals,
                "signals_traded": result.signals_traded,
                "correct_signals": result.correct_signals,
                "incorrect_signals": result.incorrect_signals,
                "accuracy_pct": result.accuracy_pct,
                "gross_pnl": result.gross_pnl,
                "net_pnl": result.net_pnl,
                "min_point_differential": result.min_point_differential,
                "max_time_remaining_seconds": result.max_time_remaining_seconds,
            },
            "equity_curve": [
                {"trade": 0, "pnl": 0.0},
                {"trade": 1, "pnl": result.net_pnl},
            ] if result.signals else [],
            "price_history": price_history,
            "trades": [
                {
                    "frame_idx": s.frame_idx,
                    "timestamp": s.timestamp,
                    "period": s.period,
                    "time_remaining": s.time_remaining,
                    "direction": f"BUY YES ({s.leading_team.upper()})",
                    "score_differential": s.score_differential,
                    "confidence": s.confidence,
                    "win_probability": s.win_probability,
                    "market_price": s.market_price * 100,  # Convert to cents
                    "filled": s.filled,
                    "fill_price": s.market_price * 100,
                    "correct": s.signal_correct,
                    "pnl": s.pnl,
                }
                for s in result.signals
            ],
        }

        if self._on_progress:
            self._on_progress(job.id, job.progress)

    async def _run_crypto_backtest(self, job: BacktestJob) -> None:
        """Run crypto latency backtest."""
        import json
        from pathlib import Path
        from scripts.backtest_recorded_crypto import (
            run_backtest as run_crypto_backtest,
            BacktestConfig as CryptoConfig,
        )

        # Load recording
        recording_path = Path(job.config.data_source)
        if not recording_path.exists():
            raise FileNotFoundError(f"Recording not found: {recording_path}")

        with open(recording_path) as f:
            data = json.load(f)

        snapshots = data.get("snapshots", [])
        settlements = {s["ticker"]: s["result"] for s in data.get("settlements", [])}
        markets = data.get("markets", {})

        job.progress.total_frames = len(snapshots)

        # Convert edge from cents to percentage
        min_edge_pct = job.config.min_edge_cents / 100.0

        config = CryptoConfig(
            min_edge_pct=min_edge_pct,
            signal_stability_duration_sec=job.config.signal_stability_sec,
            slippage_adjusted_edge=job.config.slippage_adjusted,
            market_cooldown_enabled=job.config.market_cooldown,
            kelly_fraction=job.config.kelly_fraction,
            bankroll=job.config.bankroll,
        )

        # Run backtest (synchronous, but fast)
        trades, final_bankroll = run_crypto_backtest(
            snapshots,
            settlements,
            markets,
            config,
        )

        # Calculate results
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = sum(1 for t in trades if t.pnl <= 0)
        total_pnl = sum(t.pnl for t in trades)
        settled = sum(1 for t in trades if t.result)

        job.progress.frames_processed = len(snapshots)
        job.progress.trades_executed = len(trades)
        job.progress.current_pnl = total_pnl

        # Store result
        job.result = {
            "total_snapshots": len(snapshots),
            "total_settlements": len(settlements),
            "metrics": {
                "total_trades": len(trades),
                "settled": settled,
                "wins": wins,
                "losses": losses,
                "win_rate": (wins / settled * 100) if settled else 0,
                "starting_bankroll": job.config.bankroll,
                "final_bankroll": final_bankroll,
                "total_pnl": total_pnl,
                "return_pct": (final_bankroll / job.config.bankroll - 1) * 100,
            },
            "trades": [
                {
                    "ticker": t.ticker,
                    "asset": t.asset,
                    "side": t.side,
                    "contracts": t.contracts,
                    "entry_price_cents": t.entry_price_cents,
                    "entry_time": t.entry_time,
                    "edge": t.edge,
                    "result": t.result,
                    "pnl": t.pnl,
                }
                for t in trades
            ],
            "equity_curve": [
                {"trade": i, "pnl": sum(t.pnl for t in trades[:i+1])}
                for i in range(len(trades))
            ],
        }

    def get_available_recordings(self) -> Dict[str, List[Dict[str, str]]]:
        """Get list of available recordings by strategy type."""
        recordings_dir = Path("data/recordings")

        result = {
            "nba": [],
            "crypto": [],
        }

        if not recordings_dir.exists():
            return result

        for path in recordings_dir.glob("*.json"):
            name = path.stem
            info = {
                "path": str(path),
                "name": name,
                "size_kb": path.stat().st_size // 1024,
            }

            if name.startswith("crypto_"):
                result["crypto"].append(info)
            elif "_vs_" in name or "game" in name.lower():
                result["nba"].append(info)

        return result


# Global singleton
backtest_manager = BacktestManager()
