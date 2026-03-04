"""Unified backtest engine for the trading framework.

BacktestEngine drives the main loop: iterate frames from a DataFeed,
call the BacktestAdapter for signals, simulate fills, track positions,
and return a BacktestResult.

Strategies plug in via BacktestAdapter (one per strategy family) and
DataFeed (one per data source).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from src.core.models import Fill
from strategies.base import Signal

from .data_feed import BacktestFrame, DataFeed
from .fill_model import FillModel, ImmediateFillModel
from .metrics import BacktestMetadata, BacktestMetrics, BacktestResult
from .portfolio import PositionTracker
from .realism_config import BacktestRealismConfig


# ---------------------------------------------------------------------------
# BacktestConfig
# ---------------------------------------------------------------------------


@dataclass
class BacktestConfig:
    """Configuration knobs shared across all backtest runs."""

    initial_bankroll: float = 10000.0
    fill_probability: float = 1.0
    slippage: float = 0.0
    fee_model: Optional[Callable[[float], float]] = None
    max_position_per_ticker: int = 100
    max_total_position: int = 500
    cooldown_seconds: float = 0.0
    market_impact: Optional[Any] = None  # MarketImpactConfig from fill_model (DEPRECATED)
    realism: Optional[BacktestRealismConfig] = None  # Unified realism models
    repricing_lag: Optional[Any] = None  # KalshiRepricingConfig from fill_model (velocity-based staleness check)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "initial_bankroll": self.initial_bankroll,
            "fill_probability": self.fill_probability,
            "slippage": self.slippage,
            "max_position_per_ticker": self.max_position_per_ticker,
            "max_total_position": self.max_total_position,
            "cooldown_seconds": self.cooldown_seconds,
        }
        if self.realism is not None:
            result["realism"] = self.realism.to_dict()
        if self.market_impact is not None:
            result["market_impact"] = {
                "enable_impact": self.market_impact.enable_impact,
                "impact_coeff": self.market_impact.impact_coeff,
                "permanent_fraction": self.market_impact.permanent_fraction,
                "min_depth": self.market_impact.min_depth,
            }
        if self.repricing_lag is not None:
            result["repricing_lag"] = {
                "enable_repricing_lag": self.repricing_lag.enable_repricing_lag,
                "max_staleness_sec": self.repricing_lag.max_staleness_sec,
                "min_spot_velocity_threshold": self.repricing_lag.min_spot_velocity_threshold,
            }
        return result


# ---------------------------------------------------------------------------
# BacktestAdapter ABC
# ---------------------------------------------------------------------------


class BacktestAdapter(ABC):
    """Translates BacktestFrames into Signals for the engine.

    Each strategy family provides its own adapter subclass that wraps the
    real strategy logic and converts frame context into Signal objects.
    """

    @abstractmethod
    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        """Evaluate a frame and return zero or more Signals."""
        ...

    def on_fill(self, fill: Fill) -> None:
        """Called after a signal is filled.  Override for bookkeeping."""
        pass

    def on_start(self) -> None:
        """Called before the first frame is processed."""
        pass

    def on_end(self) -> None:
        """Called after all frames have been processed."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name for reports."""
        ...


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """Runs a backtest by iterating a DataFeed through a BacktestAdapter.

    Usage::

        engine = BacktestEngine(config)
        result = engine.run(feed, adapter, verbose=True)
        print(result.report())
    """

    def __init__(
        self,
        config: Optional[BacktestConfig] = None,
        fill_model: Optional[FillModel] = None,
    ):
        self._config = config or BacktestConfig()
        if fill_model is not None:
            self._fill_model = fill_model
        else:
            # Extract realism configs if provided
            impact_cfg = None
            queue_cfg = None
            latency_cfg = None
            staleness_cfg = None
            if self._config.realism is not None:
                impact_cfg = self._config.realism.market_impact
                queue_cfg = self._config.realism.queue_priority
                latency_cfg = self._config.realism.network_latency
                staleness_cfg = self._config.realism.orderbook_staleness

            # Legacy market_impact field (DEPRECATED, prefer realism.market_impact)
            if impact_cfg is None and self._config.market_impact is not None:
                impact_cfg = self._config.market_impact

            self._fill_model = ImmediateFillModel(
                fill_probability=self._config.fill_probability,
                slippage=self._config.slippage,
                fee_fn=self._config.fee_model,
                repricing_config=self._config.repricing_lag,
                impact_config=impact_cfg,
                queue_config=queue_cfg,
                latency_config=latency_cfg,
                staleness_config=staleness_cfg,
            )

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        feed: DataFeed,
        adapter: BacktestAdapter,
        verbose: bool = False,
    ) -> BacktestResult:
        """Run a single backtest.

        Args:
            feed: Source of BacktestFrame objects.
            adapter: Strategy adapter that produces Signals.
            verbose: Print per-signal progress to stdout.

        Returns:
            BacktestResult with metrics, signals, fills, etc.
        """
        started_at = datetime.now()
        tracker = PositionTracker(self._config.initial_bankroll)
        all_signals: List[Signal] = []
        all_fills: List[Fill] = []
        frame_count = 0
        last_signal_ts: Dict[str, float] = {}  # ticker -> epoch

        # Data completeness tracking
        signals_with_depth = 0
        signals_with_spread = 0
        signals_with_estimated_depth = 0
        signals_with_default_spread = 0

        adapter.on_start()

        for frame in feed:
            frame_count += 1
            signals = adapter.evaluate(frame)

            for signal in signals:
                # Position limits
                if not self._passes_filters(signal, tracker):
                    continue

                # Cooldown
                if self._config.cooldown_seconds > 0:
                    prev_ts = last_signal_ts.get(signal.ticker, 0.0)
                    cur_ts = signal.timestamp.timestamp() if signal.timestamp else 0.0
                    if cur_ts - prev_ts < self._config.cooldown_seconds:
                        continue

                # Attempt fill
                market = frame.markets.get(signal.ticker)
                if market is None:
                    continue

                # Track data completeness for this signal
                has_depth = False
                has_spread = False
                if signal.side == "BID":
                    has_depth = market.ask_depth is not None and market.ask_depth > 0
                else:
                    has_depth = market.bid_depth is not None and market.bid_depth > 0

                has_spread = market.spread is not None and market.spread > 0

                if has_depth:
                    signals_with_depth += 1
                else:
                    # Will need to estimate depth
                    signals_with_estimated_depth += 1

                if has_spread:
                    signals_with_spread += 1
                else:
                    # Will use default spread
                    signals_with_default_spread += 1

                fill = self._fill_model.simulate_fill(signal, market, frame.context)
                if fill is not None:
                    tracker.process_fill(fill)
                    adapter.on_fill(fill)
                    all_fills.append(fill)
                    last_signal_ts[signal.ticker] = (
                        signal.timestamp.timestamp() if signal.timestamp else 0.0
                    )

                    if verbose:
                        print(
                            f"  [{frame.frame_idx}] FILL {fill.side} "
                            f"{fill.ticker} {fill.size}@{fill.price:.2f}"
                        )

                all_signals.append(signal)

        # Settle open positions
        tracker.settle(feed.get_settlement())
        adapter.on_end()

        completed_at = datetime.now()

        # Build metrics
        portfolio = tracker.compute_metrics()
        metrics = self._build_metrics(
            portfolio,
            frame_count,
            all_signals,
            all_fills,
            feed.get_settlement(),
        )

        # Build data quality metadata
        data_metadata = self._build_data_metadata(
            len(all_signals),
            signals_with_depth,
            signals_with_spread,
            signals_with_estimated_depth,
            signals_with_default_spread,
        )

        result = BacktestResult(
            adapter_name=adapter.name,
            metrics=metrics,
            signals=all_signals,
            fills=all_fills,
            feed_metadata=feed.metadata,
            config=self._config.to_dict(),
            bankroll_curve=tracker.bankroll_curve,
            settlements=feed.get_settlement(),
            started_at=started_at,
            completed_at=completed_at,
            data_metadata=data_metadata,
        )

        # Portfolio optimizer integration: record fills to trade database
        self._record_portfolio_fills(adapter.name, all_fills, feed.get_settlement())

        return result

    # ------------------------------------------------------------------
    # Walk-forward convenience
    # ------------------------------------------------------------------

    def run_walk_forward(
        self,
        feeds: List[DataFeed],
        adapter: BacktestAdapter,
        verbose: bool = False,
    ) -> List[BacktestResult]:
        """Run the adapter against multiple feeds sequentially.

        Useful for per-game or per-day backtests where each feed
        is independent.

        Returns:
            One BacktestResult per feed.
        """
        results = []
        for feed in feeds:
            result = self.run(feed, adapter, verbose=verbose)
            results.append(result)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _passes_filters(self, signal: Signal, tracker: PositionTracker) -> bool:
        """Check position limits before filling."""
        current_pos = tracker.get_position(signal.ticker)
        if signal.side == "BID":
            new_pos = current_pos + signal.size
        else:
            new_pos = current_pos - signal.size

        if abs(new_pos) > self._config.max_position_per_ticker:
            return False

        total = tracker.get_total_position()
        delta = abs(new_pos) - abs(current_pos)
        if total + delta > self._config.max_total_position:
            return False

        return True

    def _record_portfolio_fills(
        self,
        strategy_name: str,
        fills: List[Fill],
        settlements: Dict[str, Any],
    ) -> None:
        """Record backtest fills to portfolio trade database.

        Only records if portfolio optimization is enabled.

        Args:
            strategy_name: Name of strategy (from adapter)
            fills: List of fills from backtest
            settlements: Settlement prices for calculating realized PnL
        """
        try:
            import os
            import logging

            if os.getenv("ENABLE_PORTFOLIO_OPT") != "true":
                return

            from core.portfolio import PerformanceTracker

            logger = logging.getLogger(__name__)

            # Convert fills to trade records
            trades = []
            for fill in fills:
                # Calculate realized PnL if settled
                pnl = None
                settled_at = None

                if fill.ticker in settlements:
                    settlement_price = settlements[fill.ticker]
                    if fill.side == "BID":
                        # Bought yes, settled at settlement_price
                        pnl = (settlement_price - fill.price) * fill.size
                    else:
                        # Sold yes (bought no), settled at (100 - settlement_price)
                        pnl = (fill.price - settlement_price) * fill.size

                    # Settlement happens at end of backtest
                    settled_at = datetime.now()

                trades.append({
                    "ticker": fill.ticker,
                    "timestamp": fill.timestamp,
                    "side": "buy" if fill.side == "BID" else "sell",
                    "price": fill.price / 100.0,  # Convert cents to dollars
                    "size": fill.size,
                    "pnl": pnl / 100.0 if pnl is not None else None,  # Convert to dollars
                    "settled_at": settled_at,
                })

            # Record to database
            if trades:
                tracker = PerformanceTracker()
                tracker.record_backtest_fills(strategy_name, trades)
                logger.info(
                    f"Recorded {len(trades)} fills for {strategy_name} to portfolio database"
                )

        except Exception as e:
            # Don't fail backtest if portfolio tracking fails
            logger.warning(f"Failed to record portfolio fills: {e}")

    @staticmethod
    def _build_metrics(
        portfolio: Dict[str, Any],
        frame_count: int,
        signals: List[Signal],
        fills: List[Fill],
        settlements: Dict[str, Optional[float]],
    ) -> BacktestMetrics:
        """Assemble BacktestMetrics from raw data."""
        # Count winners / losers based on settlement
        winning = 0
        losing = 0
        for f in fills:
            settle = settlements.get(f.ticker)
            if settle is None:
                continue
            if f.side == "BID":
                pnl = (settle - f.price) * f.size
            else:
                pnl = (f.price - settle) * f.size
            if pnl > 0:
                winning += 1
            elif pnl < 0:
                losing += 1

        total_judged = winning + losing
        win_rate = (winning / total_judged * 100) if total_judged > 0 else 0.0

        return BacktestMetrics(
            total_frames=frame_count,
            total_signals=len(signals),
            total_fills=len(fills),
            initial_bankroll=portfolio["initial_bankroll"],
            final_bankroll=portfolio["final_bankroll"],
            net_pnl=portfolio["net_pnl"],
            return_pct=portfolio["return_pct"],
            total_fees=portfolio["total_fees"],
            max_drawdown_pct=portfolio["max_drawdown_pct"],
            peak_bankroll=portfolio["peak_bankroll"],
            winning_fills=winning,
            losing_fills=losing,
            win_rate_pct=win_rate,
            portfolio=portfolio,
        )

    @staticmethod
    def _build_data_metadata(
        total_signals: int,
        signals_with_depth: int,
        signals_with_spread: int,
        signals_with_estimated_depth: int,
        signals_with_default_spread: int,
    ) -> BacktestMetadata:
        """Calculate data quality metadata from completeness tracking.

        Assigns a confidence level based on data completeness:
        - HIGH: >80% signals have both depth and spread data
        - MEDIUM: 50-80% signals have both depth and spread data
        - LOW: <50% signals have both depth and spread data

        Args:
            total_signals: Total number of signals generated
            signals_with_depth: Count of signals with real depth data
            signals_with_spread: Count of signals with real spread data
            signals_with_estimated_depth: Count using estimated depth
            signals_with_default_spread: Count using default spread

        Returns:
            BacktestMetadata with confidence score and completeness stats
        """
        if total_signals == 0:
            return BacktestMetadata(
                data_confidence="UNKNOWN",
                signals_with_full_data_pct=0.0,
                signals_with_depth_data_pct=0.0,
                signals_with_spread_data_pct=0.0,
                signals_with_estimated_depth=0,
                signals_with_default_spread=0,
                total_signals=0,
            )

        # Calculate percentages
        depth_pct = (signals_with_depth / total_signals) * 100
        spread_pct = (signals_with_spread / total_signals) * 100

        # Full data = both depth and spread available
        # Use minimum of the two as a conservative estimate
        full_data_pct = min(depth_pct, spread_pct)

        # Assign confidence level
        if full_data_pct >= 80.0:
            confidence = "HIGH"
        elif full_data_pct >= 50.0:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return BacktestMetadata(
            data_confidence=confidence,
            signals_with_full_data_pct=full_data_pct,
            signals_with_depth_data_pct=depth_pct,
            signals_with_spread_data_pct=spread_pct,
            signals_with_estimated_depth=signals_with_estimated_depth,
            signals_with_default_spread=signals_with_default_spread,
            total_signals=total_signals,
        )
