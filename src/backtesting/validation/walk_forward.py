"""Walk-forward analysis for backtest validation.

Splits a DataFeed into train/test folds and runs the backtest engine
on each fold to detect overfitting and estimate out-of-sample performance.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..data_feed import BacktestFrame, DataFeed
from ..metrics import BacktestResult


# ---------------------------------------------------------------------------
# SlicedDataFeed
# ---------------------------------------------------------------------------


class SlicedDataFeed(DataFeed):
    """Wraps a DataFeed to only yield frames within [start_ts, end_ts).

    Iterates the parent feed and filters by timestamp boundaries.
    """

    def __init__(
        self,
        parent: DataFeed,
        start_ts: float,
        end_ts: float,
    ):
        self._parent = parent
        self._start_ts = start_ts
        self._end_ts = end_ts

    def __iter__(self) -> Iterator[BacktestFrame]:
        idx = 0
        for frame in self._parent:
            ts = frame.timestamp.timestamp()
            if ts < self._start_ts:
                continue
            if ts >= self._end_ts:
                continue
            # Re-index frames within this slice
            yield BacktestFrame(
                timestamp=frame.timestamp,
                frame_idx=idx,
                markets=frame.markets,
                context=frame.context,
            )
            idx += 1

    def get_settlement(self) -> Dict[str, Optional[float]]:
        return self._parent.get_settlement()

    @property
    def tickers(self) -> List[str]:
        return self._parent.tickers

    @property
    def metadata(self) -> Dict[str, Any]:
        meta = dict(self._parent.metadata)
        meta["slice_start"] = self._start_ts
        meta["slice_end"] = self._end_ts
        return meta


# ---------------------------------------------------------------------------
# Walk-forward config and result
# ---------------------------------------------------------------------------


@dataclass
class WalkForwardConfig:
    n_splits: int = 5
    train_pct: float = 0.70
    expanding_window: bool = False  # False = sliding, True = expanding


@dataclass
class WalkForwardFold:
    """Results for a single walk-forward fold."""

    fold_idx: int
    train_start: float
    train_end: float
    test_start: float
    test_end: float
    train_result: BacktestResult
    test_result: BacktestResult


@dataclass
class WalkForwardResult:
    """Aggregate walk-forward results."""

    config: WalkForwardConfig
    folds: List[WalkForwardFold]

    # Aggregate OOS metrics
    oos_total_pnl: float = 0.0
    oos_total_fills: int = 0
    oos_avg_return_pct: float = 0.0
    oos_win_rate_pct: float = 0.0

    def report(self) -> str:
        lines = [
            f"--- Walk-Forward Analysis ({self.config.n_splits} folds, "
            f"{'expanding' if self.config.expanding_window else 'sliding'}) ---",
            "",
            f"{'Fold':<6} {'Train PnL':>12} {'Train WR':>10} "
            f"{'Test PnL':>12} {'Test WR':>10} {'Test Fills':>10}",
            "-" * 62,
        ]

        for fold in self.folds:
            tm = fold.train_result.metrics
            em = fold.test_result.metrics
            lines.append(
                f"  {fold.fold_idx:<4d} "
                f"${tm.net_pnl:>+10.2f} {tm.win_rate_pct:>9.0f}% "
                f"${em.net_pnl:>+10.2f} {em.win_rate_pct:>9.0f}% "
                f"{em.total_fills:>9d}"
            )

        lines.append("-" * 62)
        lines.append(
            f"  OOS aggregate: PnL ${self.oos_total_pnl:+.2f}, "
            f"{self.oos_total_fills} fills, "
            f"WR {self.oos_win_rate_pct:.0f}%, "
            f"Avg return {self.oos_avg_return_pct:+.1f}%"
        )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Walk-forward runner
# ---------------------------------------------------------------------------


class WalkForwardRunner:
    """Runs walk-forward analysis by splitting a feed into time-based folds."""

    def __init__(self, config: Optional[WalkForwardConfig] = None):
        self._config = config or WalkForwardConfig()

    def run(
        self,
        feed: DataFeed,
        adapter_factory,
        engine,
        verbose: bool = False,
    ) -> WalkForwardResult:
        """Run walk-forward analysis.

        Args:
            feed: The full DataFeed to split.
            adapter_factory: Callable that returns a fresh BacktestAdapter
                             (needed per fold to reset internal state).
            engine: BacktestEngine instance.
            verbose: Print progress.

        Returns:
            WalkForwardResult with per-fold and aggregate metrics.
        """
        # Collect all timestamps by iterating the feed once
        timestamps = []
        for frame in feed:
            timestamps.append(frame.timestamp.timestamp())

        if len(timestamps) < 2:
            return WalkForwardResult(
                config=self._config,
                folds=[],
            )

        t_min = min(timestamps)
        t_max = max(timestamps)
        total_span = t_max - t_min

        if total_span <= 0:
            return WalkForwardResult(
                config=self._config,
                folds=[],
            )

        # Compute fold boundaries
        n_splits = self._config.n_splits
        train_pct = self._config.train_pct
        expanding = self._config.expanding_window

        # Each fold has a test window. We step through time so test windows
        # tile the data from left to right.
        # For sliding: train window is fixed size, slides with test.
        # For expanding: train always starts at t_min, grows.

        # Test windows are non-overlapping and cover the latter portion of data.
        # First fold's test starts at train_pct of total span.
        test_span = total_span * (1 - train_pct) / n_splits
        train_span = total_span * train_pct

        folds = []
        for i in range(n_splits):
            test_start = t_min + train_span + i * test_span
            test_end = test_start + test_span

            if expanding:
                fold_train_start = t_min
            else:
                fold_train_start = test_start - train_span

            fold_train_end = test_start

            # Slice feeds
            train_feed = feed.slice(fold_train_start, fold_train_end)
            test_feed = feed.slice(test_start, test_end)

            # Fresh adapters per fold
            train_adapter = adapter_factory()
            test_adapter = adapter_factory()

            if verbose:
                print(f"  Fold {i}: train [{fold_train_start:.0f}, {fold_train_end:.0f}), "
                      f"test [{test_start:.0f}, {test_end:.0f})")

            train_result = engine.run(train_feed, train_adapter)
            test_result = engine.run(test_feed, test_adapter)

            folds.append(WalkForwardFold(
                fold_idx=i,
                train_start=fold_train_start,
                train_end=fold_train_end,
                test_start=test_start,
                test_end=test_end,
                train_result=train_result,
                test_result=test_result,
            ))

        # Aggregate OOS metrics
        oos_pnl = sum(f.test_result.metrics.net_pnl for f in folds)
        oos_fills = sum(f.test_result.metrics.total_fills for f in folds)
        oos_wins = sum(f.test_result.metrics.winning_fills for f in folds)
        oos_losses = sum(f.test_result.metrics.losing_fills for f in folds)
        oos_judged = oos_wins + oos_losses
        oos_wr = (oos_wins / oos_judged * 100) if oos_judged > 0 else 0.0

        returns = [f.test_result.metrics.return_pct for f in folds]
        avg_ret = sum(returns) / len(returns) if returns else 0.0

        result = WalkForwardResult(
            config=self._config,
            folds=folds,
            oos_total_pnl=oos_pnl,
            oos_total_fills=oos_fills,
            oos_avg_return_pct=avg_ret,
            oos_win_rate_pct=oos_wr,
        )
        return result
