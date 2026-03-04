"""Crypto scalp chop strategy adapter for the unified backtest framework.

Extends the crypto_scalp adapter with pattern-based exit timing instead of fixed delays.

Usage:
    from src.backtesting.adapters.chop_adapter import ChopDataFeed, ChopAdapter
    feed = ChopDataFeed("data/btc_ob_48h.db", lookback_sec=5.0)
    adapter = ChopAdapter(config)
    engine = BacktestEngine(config)
    result = engine.run(feed, adapter, verbose=True)
"""

import time
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional

from src.core.models import MarketState
from strategies.base import Signal
from strategies.crypto_scalp_chop.config import ChopConfig

from ..data_feed import BacktestFrame
from .scalp_adapter import (
    CryptoScalpDataFeed,
    CryptoScalpAdapter,
    _compute_delta,
)


@dataclass
class ChopPosition:
    """Extended position tracking with predicted peak timing.

    Additional fields:
        predicted_peak_time: When we expect Kalshi to peak (frame index)
        predicted_overshoot_cents: Expected overshoot magnitude
        move_magnitude_usd: Magnitude of the spot move that triggered entry
    """

    ticker: str
    side: str  # "yes" or "no"
    entry_price_cents: int
    entry_time: float  # frame timestamp
    entry_frame_idx: int  # frame index
    size: int
    predicted_peak_time: float  # frame timestamp
    predicted_overshoot_cents: float
    move_magnitude_usd: float


class ChopDataFeed(CryptoScalpDataFeed):
    """Data feed for chop strategy - same as scalp adapter."""

    pass  # No changes needed from base class


class ChopAdapter(CryptoScalpAdapter):
    """Backtest adapter for crypto scalp chop strategy.

    Extends CryptoScalpAdapter with:
    - Pattern-based exit timing (not fixed 20s delay)
    - Early exit if profit target hit
    - Timing accuracy metrics (RMSE)
    """

    def __init__(self, config: ChopConfig) -> None:
        """Initialize chop adapter.

        Args:
            config: Chop strategy configuration
        """
        # Initialize base adapter with individual parameters
        super().__init__(
            signal_feed=config.signal_feed,
            min_spot_move_usd=config.min_spot_move_usd,
            min_ttx_sec=config.min_ttx_sec,
            max_ttx_sec=config.max_ttx_sec,
            min_entry_price_cents=config.min_entry_price_cents,
            max_entry_price_cents=config.max_entry_price_cents,
            contracts_per_trade=config.contracts_per_trade,
            exit_delay_sec=20.0,  # Not used - we override exit logic
            max_hold_sec=config.max_hold_sec,
            cooldown_sec=config.cooldown_sec,
            min_window_volume=config.min_window_volume,
            min_volume_concentration=0.0,
            require_multi_exchange_confirm=config.require_multi_exchange_confirm,
            regime_osc_threshold=0.0,
            slippage_cents=config.slippage_buffer_cents,
            min_entry_bid_depth=config.min_entry_bid_depth,
            enable_entry_liquidity_check=config.enable_entry_liquidity_check,
            stop_loss_cents=config.stop_loss_cents,
            stop_loss_delay_sec=0.0,  # No delay for chop
            enable_stop_loss=config.enable_stop_loss,
            enable_fill_simulation=True,
            base_fill_rate=0.65,
            adverse_selection_factor=0.3,
            fill_latency_ms=(200.0, 500.0),
            slippage_probability=0.2,
            max_slippage_cents=3,
        )
        self._chop_config = config

        # Load patterns
        self._patterns = config.load_patterns()

        # Tracking for timing accuracy
        self._timing_errors: List[float] = []  # List of (predicted - actual) in ms
        self._early_exits = 0

    def process_frame(
        self, frame: BacktestFrame, context: Dict[str, Any]
    ) -> Iterator[Signal]:
        """Process a single backtest frame.

        Overrides base method to implement pattern-based exits.

        Args:
            frame: Current frame with market data
            context: Shared context across frames

        Yields:
            Signal objects for entry/exit
        """
        # Check for entries (use base logic)
        for signal in super().process_frame(frame, context):
            # If it's an entry signal, add timing prediction
            if signal.action == "BUY":
                # Get the spot delta that triggered this entry
                ticker = signal.ticker
                market = frame.data.get("markets", {}).get(ticker)
                if market:
                    # Compute spot delta at this frame
                    spot_prices = context.get("spot_prices", {}).get("binance", [])
                    delta, _ = _compute_delta(
                        spot_prices, frame.ts, self._chop_config.spot_lookback_sec
                    )

                    if delta is not None:
                        # Get pattern-based timing prediction
                        move_magnitude = abs(delta)
                        predicted_lag_ms = self._chop_config.get_predicted_peak_time_ms(
                            move_magnitude
                        )
                        if predicted_lag_ms is None:
                            predicted_lag_ms = 5000.0  # Default 5s

                        predicted_overshoot = (
                            self._chop_config.get_predicted_overshoot_cents(move_magnitude)
                        )
                        if predicted_overshoot is None:
                            predicted_overshoot = 0.0

                        # Store position with timing info
                        position = ChopPosition(
                            ticker=ticker,
                            side=signal.side,
                            entry_price_cents=signal.target_price_cents,
                            entry_time=frame.ts,
                            entry_frame_idx=context.get("frame_idx", 0),
                            size=signal.size,
                            predicted_peak_time=frame.ts + (predicted_lag_ms / 1000.0),
                            predicted_overshoot_cents=predicted_overshoot,
                            move_magnitude_usd=move_magnitude,
                        )

                        # Store in context
                        if "chop_positions" not in context:
                            context["chop_positions"] = {}
                        context["chop_positions"][ticker] = position

            yield signal

        # Check for exits (pattern-based timing)
        if "chop_positions" not in context:
            return

        positions = context["chop_positions"]
        to_remove = []

        for ticker, position in positions.items():
            market = frame.data.get("markets", {}).get(ticker)
            if not market:
                continue

            # Get current bid price
            if position.side == "yes":
                current_bid = market.get("yes_bid", 0)
            else:
                current_bid = market.get("no_bid", 0)

            if current_bid == 0:
                continue

            # Check early exit (profit target)
            if self._chop_config.enable_early_exit:
                current_profit = current_bid - position.entry_price_cents
                predicted_profit = position.predicted_overshoot_cents

                if predicted_profit > 0:
                    profit_pct = current_profit / predicted_profit
                    if profit_pct >= self._chop_config.early_exit_threshold_pct:
                        # Early exit
                        yield Signal(
                            ticker=ticker,
                            action="SELL",
                            side=position.side,
                            target_price_cents=current_bid,
                            size=position.size,
                        )
                        self._early_exits += 1

                        # Record timing error
                        actual_lag_ms = (frame.ts - position.entry_time) * 1000
                        predicted_lag_ms = (
                            position.predicted_peak_time - position.entry_time
                        ) * 1000
                        self._timing_errors.append(predicted_lag_ms - actual_lag_ms)

                        to_remove.append(ticker)
                        continue

            # Exit at predicted peak time
            if frame.ts >= position.predicted_peak_time:
                yield Signal(
                    ticker=ticker,
                    action="SELL",
                    side=position.side,
                    target_price_cents=current_bid,
                    size=position.size,
                )

                # Record timing error
                actual_lag_ms = (frame.ts - position.entry_time) * 1000
                predicted_lag_ms = (
                    position.predicted_peak_time - position.entry_time
                ) * 1000
                self._timing_errors.append(predicted_lag_ms - actual_lag_ms)

                to_remove.append(ticker)
                continue

            # Safety: max hold time
            hold_time = frame.ts - position.entry_time
            if hold_time >= self._chop_config.max_hold_sec:
                yield Signal(
                    ticker=ticker,
                    action="SELL",
                    side=position.side,
                    target_price_cents=current_bid,
                    size=position.size,
                )

                # Record timing error (late exit)
                actual_lag_ms = (frame.ts - position.entry_time) * 1000
                predicted_lag_ms = (
                    position.predicted_peak_time - position.entry_time
                ) * 1000
                self._timing_errors.append(predicted_lag_ms - actual_lag_ms)

                to_remove.append(ticker)

        # Remove exited positions
        for ticker in to_remove:
            del positions[ticker]

    def get_metrics(self) -> Dict[str, Any]:
        """Get backtest metrics including timing accuracy.

        Returns:
            Dict with timing RMSE and early exit rate
        """
        metrics = {}

        # Add timing metrics
        if self._timing_errors:
            # Calculate RMSE
            sq_errors = [e ** 2 for e in self._timing_errors]
            rmse = (sum(sq_errors) / len(sq_errors)) ** 0.5

            metrics["timing_rmse_ms"] = rmse
            metrics["timing_predictions"] = len(self._timing_errors)
            metrics["early_exits"] = self._early_exits
            metrics["early_exit_rate"] = (
                self._early_exits / len(self._timing_errors)
                if self._timing_errors
                else 0.0
            )

        return metrics
