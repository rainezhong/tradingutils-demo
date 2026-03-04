"""Tests for the backtest validation suite."""

import math
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional

import pytest

from src.core.models import Fill, MarketState
from src.backtesting.data_feed import BacktestFrame, DataFeed
from src.backtesting.engine import BacktestAdapter, BacktestConfig, BacktestEngine
from src.backtesting.metrics import BacktestResult
from strategies.base import Signal

from src.backtesting.validation.trade_analysis import (
    TradePnL,
    compute_trade_pnls,
    compute_trade_distribution,
)
from src.backtesting.validation.extended_metrics import ExtendedMetrics
from src.backtesting.validation.monte_carlo import (
    MonteCarloConfig,
    MonteCarloMode,
    MonteCarloSimulator,
)
from src.backtesting.validation.bootstrap import BootstrapAnalyzer, BootstrapConfig
from src.backtesting.validation.permutation_test import PermutationConfig, PermutationTester
from src.backtesting.validation.walk_forward import (
    SlicedDataFeed,
    WalkForwardConfig,
    WalkForwardRunner,
)
from src.backtesting.validation.report import ValidationSuite, run_validation_suite


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


def _make_fill(ticker: str, side: str, price: float, size: int = 1, fee: float = 0.0,
               ts: Optional[datetime] = None) -> Fill:
    return Fill(
        ticker=ticker,
        side=side,
        price=price,
        size=size,
        order_id="test",
        fee=fee,
        timestamp=ts,
    )


def _make_settlements() -> Dict[str, Optional[float]]:
    return {
        "TICKER-A": 1.0,  # YES won
        "TICKER-B": 0.0,  # NO won
        "TICKER-C": 1.0,
    }


def _make_fills() -> List[Fill]:
    """Create a mix of winning and losing fills."""
    t0 = datetime(2025, 1, 1)
    return [
        _make_fill("TICKER-A", "BID", 0.40, 10, fee=0.01, ts=t0),  # win: (1.0-0.4)*10 - 0.01
        _make_fill("TICKER-A", "BID", 0.60, 5, fee=0.01, ts=t0 + timedelta(hours=1)),  # win
        _make_fill("TICKER-B", "BID", 0.70, 10, fee=0.01, ts=t0 + timedelta(hours=2)),  # lose: (0.0-0.7)*10
        _make_fill("TICKER-C", "ASK", 0.30, 5, fee=0.01, ts=t0 + timedelta(hours=3)),  # lose: (0.3-1.0)*5
        _make_fill("TICKER-C", "BID", 0.20, 10, fee=0.01, ts=t0 + timedelta(hours=4)),  # win
    ]


def _make_bankroll_curve() -> list:
    t0 = datetime(2025, 1, 1)
    return [
        (t0, 100.0),
        (t0 + timedelta(hours=1), 106.0),
        (t0 + timedelta(hours=2), 108.0),
        (t0 + timedelta(hours=3), 101.0),
        (t0 + timedelta(hours=4), 99.0),
        (t0 + timedelta(hours=5), 107.0),
    ]


def _make_result() -> BacktestResult:
    fills = _make_fills()
    settlements = _make_settlements()
    curve = _make_bankroll_curve()
    from src.backtesting.metrics import BacktestMetrics
    return BacktestResult(
        adapter_name="test",
        metrics=BacktestMetrics(
            total_fills=len(fills),
            initial_bankroll=100.0,
            final_bankroll=107.0,
            net_pnl=7.0,
            return_pct=7.0,
        ),
        signals=[],
        fills=fills,
        settlements=settlements,
        bankroll_curve=curve,
    )


# ---------------------------------------------------------------------------
# Trade analysis tests
# ---------------------------------------------------------------------------


class TestTradeAnalysis:
    def test_compute_trade_pnls_basic(self):
        fills = [_make_fill("A", "BID", 0.40, 10)]
        settlements = {"A": 1.0}
        trades = compute_trade_pnls(fills, settlements)
        assert len(trades) == 1
        assert trades[0].gross_pnl == pytest.approx(6.0)  # (1.0 - 0.4) * 10
        assert trades[0].is_winner is True

    def test_compute_trade_pnls_losing_bid(self):
        fills = [_make_fill("A", "BID", 0.70, 10)]
        settlements = {"A": 0.0}
        trades = compute_trade_pnls(fills, settlements)
        assert len(trades) == 1
        assert trades[0].gross_pnl == pytest.approx(-7.0)
        assert trades[0].is_winner is False

    def test_compute_trade_pnls_ask_side(self):
        fills = [_make_fill("A", "ASK", 0.80, 5)]
        settlements = {"A": 0.0}
        trades = compute_trade_pnls(fills, settlements)
        assert len(trades) == 1
        # ASK side: (price - settle) * size = (0.8 - 0.0) * 5 = 4.0
        assert trades[0].gross_pnl == pytest.approx(4.0)
        assert trades[0].is_winner is True

    def test_compute_trade_pnls_skips_unknown_settlement(self):
        fills = [_make_fill("UNKNOWN", "BID", 0.50, 1)]
        settlements = {"A": 1.0}
        trades = compute_trade_pnls(fills, settlements)
        assert len(trades) == 0

    def test_compute_trade_pnls_with_fees(self):
        fills = [_make_fill("A", "BID", 0.40, 10, fee=0.50)]
        settlements = {"A": 1.0}
        trades = compute_trade_pnls(fills, settlements)
        assert trades[0].gross_pnl == pytest.approx(6.0)
        assert trades[0].net_pnl == pytest.approx(5.5)

    def test_distribution_basic(self):
        fills = _make_fills()
        settlements = _make_settlements()
        trades = compute_trade_pnls(fills, settlements)
        dist = compute_trade_distribution(trades)
        assert dist is not None
        assert dist.count == len(trades)
        assert dist.win_count + dist.loss_count == dist.count
        assert dist.total_pnl == pytest.approx(sum(t.net_pnl for t in trades))

    def test_distribution_empty(self):
        assert compute_trade_distribution([]) is None

    def test_distribution_single_trade(self):
        fills = [_make_fill("A", "BID", 0.40, 10)]
        settlements = {"A": 1.0}
        trades = compute_trade_pnls(fills, settlements)
        dist = compute_trade_distribution(trades)
        assert dist is not None
        assert dist.count == 1
        assert dist.std == 0.0

    def test_distribution_consecutive(self):
        """Test max consecutive wins/losses counting."""
        trades = [
            TradePnL("A", "BID", 0.5, 1, 0, 1.0, 0.5, 0.5, 1.0, True),
            TradePnL("A", "BID", 0.5, 1, 0, 1.0, 0.5, 0.5, 1.0, True),
            TradePnL("A", "BID", 0.5, 1, 0, 1.0, 0.5, 0.5, 1.0, True),
            TradePnL("A", "BID", 0.7, 1, 0, 0.0, -0.7, -0.7, -1.0, False),
            TradePnL("A", "BID", 0.7, 1, 0, 0.0, -0.7, -0.7, -1.0, False),
        ]
        dist = compute_trade_distribution(trades)
        assert dist.max_consecutive_wins == 3
        assert dist.max_consecutive_losses == 2


# ---------------------------------------------------------------------------
# Extended metrics tests
# ---------------------------------------------------------------------------


class TestExtendedMetrics:
    def test_compute_basic(self):
        fills = _make_fills()
        settlements = _make_settlements()
        curve = _make_bankroll_curve()
        ext = ExtendedMetrics.compute(fills, settlements, curve)
        assert ext.expected_value != 0
        assert ext.distribution is not None
        assert len(ext.trades) > 0

    def test_profit_factor(self):
        fills = _make_fills()
        settlements = _make_settlements()
        curve = _make_bankroll_curve()
        ext = ExtendedMetrics.compute(fills, settlements, curve)
        # Should have both winners and losers
        if ext.profit_factor is not None:
            assert ext.profit_factor > 0

    def test_report_format(self):
        fills = _make_fills()
        settlements = _make_settlements()
        curve = _make_bankroll_curve()
        ext = ExtendedMetrics.compute(fills, settlements, curve)
        report = ext.report()
        assert "Sharpe" in report
        assert "Sortino" in report
        assert "Profit factor" in report

    def test_empty_fills(self):
        ext = ExtendedMetrics.compute([], {}, [])
        assert ext.expected_value == 0.0
        assert ext.distribution is None


# ---------------------------------------------------------------------------
# Monte Carlo tests
# ---------------------------------------------------------------------------


class TestMonteCarlo:
    def _make_trades(self):
        fills = _make_fills()
        return compute_trade_pnls(fills, _make_settlements())

    def test_sequence_mode(self):
        trades = self._make_trades()
        config = MonteCarloConfig(n_simulations=100, mode=MonteCarloMode.SEQUENCE, seed=42)
        result = MonteCarloSimulator(config).run(trades)
        assert result is not None
        assert result.n_simulations == 100
        assert result.mode == MonteCarloMode.SEQUENCE

    def test_resample_mode(self):
        trades = self._make_trades()
        config = MonteCarloConfig(n_simulations=100, mode=MonteCarloMode.RESAMPLE, seed=42)
        result = MonteCarloSimulator(config).run(trades)
        assert result is not None

    def test_null_mode(self):
        trades = self._make_trades()
        config = MonteCarloConfig(n_simulations=100, mode=MonteCarloMode.NULL, seed=42)
        result = MonteCarloSimulator(config).run(trades)
        assert result is not None
        # Null mode flips signs — mean should be ~0
        assert abs(result.pnl_mean) < abs(result.observed_pnl) * 3

    def test_reproducibility(self):
        trades = self._make_trades()
        config = MonteCarloConfig(n_simulations=100, seed=42)
        r1 = MonteCarloSimulator(config).run(trades)
        r2 = MonteCarloSimulator(config).run(trades)
        assert r1.pnl_mean == r2.pnl_mean
        assert r1.pnl_std == r2.pnl_std

    def test_insufficient_trades(self):
        trades = compute_trade_pnls([_make_fill("A", "BID", 0.5, 1)], {"A": 1.0})
        config = MonteCarloConfig(n_simulations=100, seed=42)
        result = MonteCarloSimulator(config).run(trades)
        assert result is None

    def test_report_format(self):
        trades = self._make_trades()
        config = MonteCarloConfig(n_simulations=100, seed=42)
        result = MonteCarloSimulator(config).run(trades)
        report = result.report()
        assert "Monte Carlo" in report
        assert "P(negative)" in report


# ---------------------------------------------------------------------------
# Bootstrap tests
# ---------------------------------------------------------------------------


class TestBootstrap:
    def _make_trades(self):
        return compute_trade_pnls(_make_fills(), _make_settlements())

    def test_basic(self):
        trades = self._make_trades()
        config = BootstrapConfig(n_samples=100, seed=42)
        result = BootstrapAnalyzer(config).run(trades)
        assert result is not None
        assert result.n_samples == 100
        # CI should contain the point estimate
        assert result.net_pnl.lower <= result.net_pnl.point_estimate <= result.net_pnl.upper

    def test_reproducibility(self):
        trades = self._make_trades()
        config = BootstrapConfig(n_samples=100, seed=42)
        r1 = BootstrapAnalyzer(config).run(trades)
        r2 = BootstrapAnalyzer(config).run(trades)
        assert r1.net_pnl.lower == r2.net_pnl.lower
        assert r1.net_pnl.upper == r2.net_pnl.upper

    def test_insufficient_trades(self):
        trades = compute_trade_pnls([_make_fill("A", "BID", 0.5, 1)], {"A": 1.0})
        result = BootstrapAnalyzer(BootstrapConfig(n_samples=100, seed=42)).run(trades)
        assert result is None

    def test_report_format(self):
        trades = self._make_trades()
        result = BootstrapAnalyzer(BootstrapConfig(n_samples=100, seed=42)).run(trades)
        report = result.report()
        assert "Bootstrap" in report
        assert "Net PnL" in report


# ---------------------------------------------------------------------------
# Permutation tests
# ---------------------------------------------------------------------------


class TestPermutation:
    def _make_trades(self):
        return compute_trade_pnls(_make_fills(), _make_settlements())

    def test_basic(self):
        trades = self._make_trades()
        config = PermutationConfig(n_permutations=100, seed=42)
        result = PermutationTester(config).run(trades)
        assert result is not None
        assert 0.0 <= result.pnl_p_value <= 1.0
        assert 0.0 <= result.win_rate_p_value <= 1.0

    def test_reproducibility(self):
        trades = self._make_trades()
        config = PermutationConfig(n_permutations=100, seed=42)
        r1 = PermutationTester(config).run(trades)
        r2 = PermutationTester(config).run(trades)
        assert r1.pnl_p_value == r2.pnl_p_value

    def test_insufficient_trades(self):
        trades = compute_trade_pnls([_make_fill("A", "BID", 0.5, 1)], {"A": 1.0})
        result = PermutationTester(PermutationConfig(n_permutations=100, seed=42)).run(trades)
        assert result is None

    def test_report_format(self):
        trades = self._make_trades()
        result = PermutationTester(PermutationConfig(n_permutations=100, seed=42)).run(trades)
        report = result.report()
        assert "Permutation" in report
        assert "p-value" in report


# ---------------------------------------------------------------------------
# Walk-forward tests
# ---------------------------------------------------------------------------


class SimpleFeed(DataFeed):
    """Minimal DataFeed for testing walk-forward."""

    def __init__(self, n_frames: int = 100, start_ts: float = 1000000.0):
        self._n = n_frames
        self._start = start_ts
        self._step = 60.0  # 1 min between frames

    def __iter__(self) -> Iterator[BacktestFrame]:
        for i in range(self._n):
            ts = datetime.fromtimestamp(self._start + i * self._step)
            yield BacktestFrame(
                timestamp=ts,
                frame_idx=i,
                markets={
                    "TEST": MarketState(
                        ticker="TEST",
                        timestamp=ts,
                        bid=0.40,
                        ask=0.60,
                    )
                },
            )

    def get_settlement(self) -> Dict[str, Optional[float]]:
        return {"TEST": 1.0}

    @property
    def tickers(self) -> List[str]:
        return ["TEST"]


class SimpleAdapter(BacktestAdapter):
    """Adapter that generates a BID signal every 10 frames."""

    @property
    def name(self) -> str:
        return "simple-test"

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        if frame.frame_idx % 10 == 0:
            return [Signal(
                ticker="TEST",
                side="BID",
                price=0.45,
                size=1,
                confidence=1.0,
                reason="test",
                timestamp=frame.timestamp,
            )]
        return []


class TestSlicedDataFeed:
    def test_slice_filters_frames(self):
        feed = SimpleFeed(n_frames=100)
        start = 1000000.0 + 20 * 60  # frame 20
        end = 1000000.0 + 50 * 60    # frame 50
        sliced = SlicedDataFeed(feed, start, end)
        frames = list(sliced)
        assert len(frames) > 0
        assert len(frames) < 100
        for f in frames:
            ts = f.timestamp.timestamp()
            assert ts >= start
            assert ts < end

    def test_slice_reindexes(self):
        feed = SimpleFeed(n_frames=100)
        sliced = SlicedDataFeed(feed, 1000000.0 + 20 * 60, 1000000.0 + 50 * 60)
        frames = list(sliced)
        assert frames[0].frame_idx == 0

    def test_datafeed_slice_method(self):
        feed = SimpleFeed(n_frames=100)
        sliced = feed.slice(1000000.0 + 20 * 60, 1000000.0 + 50 * 60)
        assert isinstance(sliced, SlicedDataFeed)
        frames = list(sliced)
        assert len(frames) > 0


class TestWalkForward:
    def test_basic_walk_forward(self):
        feed = SimpleFeed(n_frames=100)
        config = WalkForwardConfig(n_splits=3, train_pct=0.70)
        engine = BacktestEngine(BacktestConfig(initial_bankroll=100.0))
        runner = WalkForwardRunner(config)
        result = runner.run(feed, SimpleAdapter, engine)
        assert len(result.folds) == 3
        for fold in result.folds:
            assert fold.train_result is not None
            assert fold.test_result is not None

    def test_expanding_window(self):
        feed = SimpleFeed(n_frames=100)
        config = WalkForwardConfig(n_splits=3, train_pct=0.70, expanding_window=True)
        engine = BacktestEngine(BacktestConfig(initial_bankroll=100.0))
        runner = WalkForwardRunner(config)
        result = runner.run(feed, SimpleAdapter, engine)
        assert len(result.folds) == 3
        # Expanding: all folds start at the same time
        starts = [f.train_start for f in result.folds]
        assert starts[0] == starts[1] == starts[2]

    def test_report_format(self):
        feed = SimpleFeed(n_frames=100)
        config = WalkForwardConfig(n_splits=2, train_pct=0.70)
        engine = BacktestEngine(BacktestConfig(initial_bankroll=100.0))
        result = WalkForwardRunner(config).run(feed, SimpleAdapter, engine)
        report = result.report()
        assert "Walk-Forward" in report
        assert "Fold" in report


# ---------------------------------------------------------------------------
# Validation suite tests
# ---------------------------------------------------------------------------


class TestValidationSuite:
    def test_run_all(self):
        result = _make_result()
        suite = run_validation_suite(
            result,
            run_extended=True,
            run_monte_carlo=True,
            run_bootstrap=True,
            run_permutation=True,
            mc_config=MonteCarloConfig(n_simulations=50, seed=42),
            bs_config=BootstrapConfig(n_samples=50, seed=42),
            perm_config=PermutationConfig(n_permutations=50, seed=42),
        )
        assert suite.extended is not None
        assert suite.monte_carlo is not None
        assert suite.bootstrap is not None
        assert suite.permutation is not None

    def test_run_extended_only(self):
        result = _make_result()
        suite = run_validation_suite(result, run_extended=True)
        assert suite.extended is not None
        assert suite.monte_carlo is None

    def test_report(self):
        result = _make_result()
        suite = run_validation_suite(
            result,
            run_extended=True,
            run_monte_carlo=True,
            mc_config=MonteCarloConfig(n_simulations=50, seed=42),
        )
        report = suite.report()
        assert "Sharpe" in report
        assert "Monte Carlo" in report

    def test_settlements_field_on_result(self):
        """Verify BacktestResult now has settlements field."""
        result = _make_result()
        assert hasattr(result, "settlements")
        assert result.settlements["TICKER-A"] == 1.0


# ---------------------------------------------------------------------------
# Reproducibility test
# ---------------------------------------------------------------------------


class TestReproducibility:
    def test_full_suite_deterministic(self):
        """Two runs with the same seed produce identical results."""
        result = _make_result()

        def _run():
            return run_validation_suite(
                result,
                run_extended=True,
                run_monte_carlo=True,
                run_bootstrap=True,
                run_permutation=True,
                mc_config=MonteCarloConfig(n_simulations=100, seed=42),
                bs_config=BootstrapConfig(n_samples=100, seed=42),
                perm_config=PermutationConfig(n_permutations=100, seed=42),
            )

        s1 = _run()
        s2 = _run()

        assert s1.monte_carlo.pnl_mean == s2.monte_carlo.pnl_mean
        assert s1.bootstrap.net_pnl.lower == s2.bootstrap.net_pnl.lower
        assert s1.permutation.pnl_p_value == s2.permutation.pnl_p_value
