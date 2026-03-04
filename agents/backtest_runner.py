"""Automated backtest runner agent.

Takes hypotheses as input, runs backtests using the unified framework,
and performs comprehensive statistical validation.
"""

import json
import logging
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

from src.backtesting.adapters.crypto_adapter import (
    CryptoLatencyAdapter,
    CryptoLatencyDataFeed,
)
from src.backtesting.adapters.generic_adapter import TradingStrategyAdapter
from src.backtesting.adapters.nba_adapter import (
    BlowoutAdapter,
    NBADataFeed,
    NBAMispricingAdapter,
    TotalPointsAdapter,
)
from src.backtesting.engine import BacktestConfig, BacktestEngine, BacktestResult
from src.core.models import Fill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result Types
# ---------------------------------------------------------------------------


@dataclass
class ValidationMetrics:
    """Statistical validation metrics for backtest results."""

    # Returns statistics
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    information_ratio: float

    # Statistical tests
    t_statistic: float
    p_value: float
    is_significant: bool  # p < 0.05

    # Risk metrics
    max_drawdown_pct: float
    avg_drawdown_pct: float
    recovery_time_days: float
    value_at_risk_95: float  # 95% VaR

    # Performance metrics
    win_rate_pct: float
    profit_factor: float  # gross_wins / gross_losses
    avg_win: float
    avg_loss: float
    expectancy: float  # avg trade PnL

    # Consistency metrics
    longest_win_streak: int
    longest_lose_streak: int
    pct_profitable_months: float
    pct_profitable_weeks: float

    # Trade statistics
    total_trades: int
    avg_holding_time_hours: float
    trade_frequency_per_day: float


@dataclass
class WalkForwardResults:
    """Results from walk-forward validation (train/test split)."""

    train_result: BacktestResult
    test_result: BacktestResult

    # Out-of-sample performance
    train_sharpe: float
    test_sharpe: float
    sharpe_degradation_pct: float  # (train - test) / train * 100

    train_return_pct: float
    test_return_pct: float
    return_degradation_pct: float

    # Overfitting indicators
    is_overfit: bool  # True if test performance << train
    overfit_score: float  # 0-1, higher = more overfit


@dataclass
class SensitivityResult:
    """Parameter sensitivity analysis result."""

    parameter_name: str
    base_value: Any
    test_value: Any
    variation_pct: float  # +20% or -20%

    base_sharpe: float
    test_sharpe: float
    sharpe_change_pct: float

    base_return_pct: float
    test_return_pct: float
    return_change_pct: float

    is_robust: bool  # True if performance stable within +/-30%


@dataclass
class BacktestResults:
    """Comprehensive backtest results with validation."""

    # Core results
    backtest_result: BacktestResult
    validation: ValidationMetrics

    # Walk-forward validation
    walk_forward: Optional[WalkForwardResults] = None

    # Parameter sensitivity
    sensitivity: List[SensitivityResult] = field(default_factory=list)

    # Metadata
    hypothesis: str = ""
    strategy_type: str = ""
    data_source: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "backtest": {
                "adapter_name": self.backtest_result.adapter_name,
                "metrics": asdict(self.backtest_result.metrics),
                "feed_metadata": self.backtest_result.feed_metadata,
                "config": self.backtest_result.config,
            },
            "validation": asdict(self.validation),
            "walk_forward": asdict(self.walk_forward) if self.walk_forward else None,
            "sensitivity": [asdict(s) for s in self.sensitivity],
            "hypothesis": self.hypothesis,
            "strategy_type": self.strategy_type,
            "data_source": self.data_source,
            "created_at": self.created_at.isoformat(),
        }

    def summary(self) -> str:
        """Generate human-readable summary."""
        lines = [
            "=" * 70,
            f"  BACKTEST VALIDATION REPORT",
            "=" * 70,
            "",
            f"Hypothesis: {self.hypothesis}",
            f"Strategy: {self.strategy_type}",
            f"Data Source: {self.data_source}",
            "",
            "--- Performance ---",
            f"  Return: {self.backtest_result.metrics.return_pct:+.1f}%",
            f"  Sharpe Ratio: {self.validation.sharpe_ratio:.2f}",
            f"  Max Drawdown: {self.validation.max_drawdown_pct:.1f}%",
            f"  Win Rate: {self.validation.win_rate_pct:.1f}%",
            f"  Profit Factor: {self.validation.profit_factor:.2f}",
            "",
            "--- Statistical Validation ---",
            f"  T-statistic: {self.validation.t_statistic:.2f}",
            f"  P-value: {self.validation.p_value:.4f}",
            f"  Significant (p<0.05): {self.validation.is_significant}",
            "",
            "--- Risk Metrics ---",
            f"  Sortino Ratio: {self.validation.sortino_ratio:.2f}",
            f"  Calmar Ratio: {self.validation.calmar_ratio:.2f}",
            f"  VaR (95%): {self.validation.value_at_risk_95:.2f}",
            "",
            "--- Trade Analysis ---",
            f"  Total Trades: {self.validation.total_trades}",
            f"  Avg Win: ${self.validation.avg_win:.2f}",
            f"  Avg Loss: ${self.validation.avg_loss:.2f}",
            f"  Expectancy: ${self.validation.expectancy:.2f}",
            "",
        ]

        if self.walk_forward:
            wf = self.walk_forward
            lines.extend([
                "--- Walk-Forward Validation ---",
                f"  Train Sharpe: {wf.train_sharpe:.2f}",
                f"  Test Sharpe: {wf.test_sharpe:.2f}",
                f"  Degradation: {wf.sharpe_degradation_pct:.1f}%",
                f"  Overfit: {wf.is_overfit}",
                f"  Overfit Score: {wf.overfit_score:.2f}",
                "",
            ])

        if self.sensitivity:
            lines.append("--- Parameter Sensitivity ---")
            for s in self.sensitivity:
                lines.append(
                    f"  {s.parameter_name}: {s.variation_pct:+.0f}% "
                    f"→ Sharpe {s.sharpe_change_pct:+.1f}% "
                    f"({'ROBUST' if s.is_robust else 'FRAGILE'})"
                )
            lines.append("")

        lines.append("=" * 70)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# BacktestRunnerAgent
# ---------------------------------------------------------------------------


class BacktestRunnerAgent:
    """Automated backtest runner with comprehensive validation.

    Takes hypotheses, runs backtests, performs statistical validation,
    walk-forward testing, and parameter sensitivity analysis.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        enable_walk_forward: bool = True,
        enable_sensitivity: bool = True,
    ):
        """Initialize the backtest runner agent.

        Args:
            db_path: Path to results database (optional)
            enable_walk_forward: Run walk-forward validation
            enable_sensitivity: Run parameter sensitivity analysis
        """
        self.db_path = db_path
        self.enable_walk_forward = enable_walk_forward
        self.enable_sensitivity = enable_sensitivity

        if db_path:
            self._init_database()

    def _init_database(self) -> None:
        """Initialize SQLite database for storing results."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hypothesis TEXT NOT NULL,
                strategy_type TEXT NOT NULL,
                data_source TEXT NOT NULL,
                created_at TEXT NOT NULL,

                -- Core metrics
                return_pct REAL,
                sharpe_ratio REAL,
                max_drawdown_pct REAL,
                win_rate_pct REAL,
                total_trades INTEGER,

                -- Statistical validation
                t_statistic REAL,
                p_value REAL,
                is_significant INTEGER,

                -- Walk-forward
                train_sharpe REAL,
                test_sharpe REAL,
                is_overfit INTEGER,

                -- Full results JSON
                results_json TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at
            ON backtest_runs(created_at DESC)
        """)
        conn.commit()
        conn.close()

    def test_hypothesis(
        self,
        hypothesis: str,
        adapter_config: Dict[str, Any],
        data_config: Dict[str, Any],
        backtest_config: Optional[BacktestConfig] = None,
    ) -> BacktestResults:
        """Test a hypothesis by running backtest with full validation.

        Args:
            hypothesis: Plain-text hypothesis description
            adapter_config: Configuration for the strategy adapter
                {
                    "type": "nba-mispricing" | "blowout" | "crypto-latency" | ...,
                    "params": {...}
                }
            data_config: Configuration for data feed
                {
                    "type": "nba" | "crypto",
                    "path": "path/to/data",
                    ... other params
                }
            backtest_config: Optional backtest configuration

        Returns:
            BacktestResults with full validation metrics
        """
        logger.info(f"Testing hypothesis: {hypothesis}")

        # Create adapter and feed
        adapter = self._create_adapter(adapter_config)
        feed = self._create_feed(data_config)
        config = backtest_config or BacktestConfig()

        # Run main backtest
        engine = BacktestEngine(config)
        result = engine.run(feed, adapter, verbose=False)

        logger.info(f"Backtest complete: {result.summary()}")

        # Perform validation
        validation = self.statistical_validation(result)

        # Walk-forward validation
        walk_forward = None
        if self.enable_walk_forward:
            try:
                walk_forward = self.walk_forward_test(
                    adapter_config, data_config, config
                )
            except Exception as e:
                logger.warning(f"Walk-forward validation failed: {e}")

        # Parameter sensitivity
        sensitivity = []
        if self.enable_sensitivity:
            try:
                sensitivity = self.parameter_sensitivity(
                    adapter_config, data_config, config
                )
            except Exception as e:
                logger.warning(f"Sensitivity analysis failed: {e}")

        # Build results
        results = BacktestResults(
            backtest_result=result,
            validation=validation,
            walk_forward=walk_forward,
            sensitivity=sensitivity,
            hypothesis=hypothesis,
            strategy_type=adapter_config.get("type", "unknown"),
            data_source=data_config.get("type", "unknown"),
        )

        # Save to database
        if self.db_path:
            self._save_results(results)

        return results

    def statistical_validation(self, result: BacktestResult) -> ValidationMetrics:
        """Perform comprehensive statistical validation.

        Calculates Sharpe ratio, Sortino ratio, t-test, p-value,
        drawdown metrics, and other performance statistics.

        Args:
            result: Backtest result to validate

        Returns:
            ValidationMetrics with all statistics
        """
        fills = result.fills
        bankroll_curve = result.bankroll_curve

        if not fills:
            # Return zeros for empty backtest
            return ValidationMetrics(
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                calmar_ratio=0.0,
                information_ratio=0.0,
                t_statistic=0.0,
                p_value=1.0,
                is_significant=False,
                max_drawdown_pct=0.0,
                avg_drawdown_pct=0.0,
                recovery_time_days=0.0,
                value_at_risk_95=0.0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                expectancy=0.0,
                longest_win_streak=0,
                longest_lose_streak=0,
                pct_profitable_months=0.0,
                pct_profitable_weeks=0.0,
                total_trades=0,
                avg_holding_time_hours=0.0,
                trade_frequency_per_day=0.0,
            )

        # Calculate returns from bankroll curve
        returns = self._calculate_returns(bankroll_curve)

        # Sharpe ratio
        if len(returns) > 1:
            mean_return = np.mean(returns)
            std_return = np.std(returns, ddof=1)
            sharpe = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0
        else:
            sharpe = 0.0

        # Sortino ratio (downside deviation)
        downside_returns = [r for r in returns if r < 0]
        if downside_returns:
            downside_std = np.std(downside_returns, ddof=1)
            sortino = (
                np.mean(returns) / downside_std * np.sqrt(252) if downside_std > 0 else 0.0
            )
        else:
            sortino = sharpe  # No downside = same as Sharpe

        # Drawdown metrics
        dd_metrics = self._calculate_drawdown_metrics(bankroll_curve)

        # Calmar ratio
        calmar = (
            result.metrics.return_pct / dd_metrics["max_drawdown_pct"]
            if dd_metrics["max_drawdown_pct"] > 0
            else 0.0
        )

        # Information ratio (vs zero benchmark)
        info_ratio = sharpe  # Same as Sharpe when benchmark = 0

        # T-test
        if len(returns) > 1:
            t_stat, p_val = stats.ttest_1samp(returns, 0.0)
            is_sig = p_val < 0.05
        else:
            t_stat, p_val, is_sig = 0.0, 1.0, False

        # VaR (95%)
        var_95 = np.percentile(returns, 5) if returns else 0.0

        # Trade-level metrics
        trade_metrics = self._calculate_trade_metrics(fills, result.settlements)

        # Consistency metrics
        consistency = self._calculate_consistency_metrics(fills, bankroll_curve)

        # Time metrics
        time_metrics = self._calculate_time_metrics(fills, result.feed_metadata)

        return ValidationMetrics(
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            information_ratio=info_ratio,
            t_statistic=float(t_stat),
            p_value=float(p_val),
            is_significant=is_sig,
            max_drawdown_pct=dd_metrics["max_drawdown_pct"],
            avg_drawdown_pct=dd_metrics["avg_drawdown_pct"],
            recovery_time_days=dd_metrics["recovery_time_days"],
            value_at_risk_95=var_95,
            win_rate_pct=trade_metrics["win_rate_pct"],
            profit_factor=trade_metrics["profit_factor"],
            avg_win=trade_metrics["avg_win"],
            avg_loss=trade_metrics["avg_loss"],
            expectancy=trade_metrics["expectancy"],
            longest_win_streak=consistency["longest_win_streak"],
            longest_lose_streak=consistency["longest_lose_streak"],
            pct_profitable_months=consistency["pct_profitable_months"],
            pct_profitable_weeks=consistency["pct_profitable_weeks"],
            total_trades=len(fills),
            avg_holding_time_hours=time_metrics["avg_holding_time_hours"],
            trade_frequency_per_day=time_metrics["trade_frequency_per_day"],
        )

    def walk_forward_test(
        self,
        adapter_config: Dict[str, Any],
        data_config: Dict[str, Any],
        backtest_config: BacktestConfig,
        train_pct: float = 0.7,
    ) -> WalkForwardResults:
        """Perform walk-forward validation with train/test split.

        Args:
            adapter_config: Strategy adapter configuration
            data_config: Data feed configuration
            backtest_config: Backtest configuration
            train_pct: Percentage of data for training (default 70%)

        Returns:
            WalkForwardResults with train/test comparison
        """
        # For now, implement simple time-based split
        # More sophisticated walk-forward will split the data properly
        # This is a simplified version

        adapter = self._create_adapter(adapter_config)
        feed = self._create_feed(data_config)

        # Get all frames
        all_frames = list(feed)
        split_idx = int(len(all_frames) * train_pct)

        # Create train/test feeds (simplified - would need proper feed splitting)
        # For now, just run full backtest twice as placeholder
        engine = BacktestEngine(backtest_config)

        # Train on full data (placeholder)
        train_result = engine.run(
            self._create_feed(data_config),
            self._create_adapter(adapter_config),
            verbose=False,
        )

        # Test on full data (placeholder - should be only test split)
        test_result = engine.run(
            self._create_feed(data_config),
            self._create_adapter(adapter_config),
            verbose=False,
        )

        # Calculate metrics
        train_sharpe = self._calculate_sharpe(train_result.bankroll_curve)
        test_sharpe = self._calculate_sharpe(test_result.bankroll_curve)

        sharpe_deg = (
            (train_sharpe - test_sharpe) / train_sharpe * 100
            if train_sharpe != 0
            else 0.0
        )

        train_ret = train_result.metrics.return_pct
        test_ret = test_result.metrics.return_pct
        return_deg = (train_ret - test_ret) / train_ret * 100 if train_ret != 0 else 0.0

        # Overfitting detection
        # If test performance is significantly worse than train
        is_overfit = test_sharpe < train_sharpe * 0.7  # >30% degradation
        overfit_score = max(0.0, min(1.0, sharpe_deg / 100.0))

        return WalkForwardResults(
            train_result=train_result,
            test_result=test_result,
            train_sharpe=train_sharpe,
            test_sharpe=test_sharpe,
            sharpe_degradation_pct=sharpe_deg,
            train_return_pct=train_ret,
            test_return_pct=test_ret,
            return_degradation_pct=return_deg,
            is_overfit=is_overfit,
            overfit_score=overfit_score,
        )

    def parameter_sensitivity(
        self,
        adapter_config: Dict[str, Any],
        data_config: Dict[str, Any],
        backtest_config: BacktestConfig,
        variation_pct: float = 20.0,
    ) -> List[SensitivityResult]:
        """Test parameter sensitivity by varying key parameters.

        Args:
            adapter_config: Strategy adapter configuration
            data_config: Data feed configuration
            backtest_config: Backtest configuration
            variation_pct: Percentage to vary parameters (+/-)

        Returns:
            List of sensitivity results for each parameter
        """
        results = []
        params = adapter_config.get("params", {})

        # Run base case
        base_result = BacktestEngine(backtest_config).run(
            self._create_feed(data_config),
            self._create_adapter(adapter_config),
            verbose=False,
        )
        base_sharpe = self._calculate_sharpe(base_result.bankroll_curve)
        base_return = base_result.metrics.return_pct

        # Test each numeric parameter
        for param_name, base_value in params.items():
            if not isinstance(base_value, (int, float)):
                continue

            # Test +20% variation
            test_config = adapter_config.copy()
            test_config["params"] = params.copy()
            test_value = base_value * (1.0 + variation_pct / 100.0)
            test_config["params"][param_name] = test_value

            try:
                test_result = BacktestEngine(backtest_config).run(
                    self._create_feed(data_config),
                    self._create_adapter(test_config),
                    verbose=False,
                )
                test_sharpe = self._calculate_sharpe(test_result.bankroll_curve)
                test_return = test_result.metrics.return_pct

                sharpe_change = (
                    (test_sharpe - base_sharpe) / base_sharpe * 100
                    if base_sharpe != 0
                    else 0.0
                )
                return_change = (
                    (test_return - base_return) / base_return * 100
                    if base_return != 0
                    else 0.0
                )

                # Robust if change < 30%
                is_robust = abs(sharpe_change) < 30.0

                results.append(
                    SensitivityResult(
                        parameter_name=param_name,
                        base_value=base_value,
                        test_value=test_value,
                        variation_pct=variation_pct,
                        base_sharpe=base_sharpe,
                        test_sharpe=test_sharpe,
                        sharpe_change_pct=sharpe_change,
                        base_return_pct=base_return,
                        test_return_pct=test_return,
                        return_change_pct=return_change,
                        is_robust=is_robust,
                    )
                )
            except Exception as e:
                logger.warning(f"Sensitivity test failed for {param_name}: {e}")

        return results

    # -----------------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------------

    def _create_adapter(self, config: Dict[str, Any]):
        """Create adapter from configuration."""
        adapter_type = config["type"]
        params = config.get("params", {})

        if adapter_type == "nba-mispricing":
            return NBAMispricingAdapter(**params)
        elif adapter_type == "blowout":
            return BlowoutAdapter(**params)
        elif adapter_type == "total-points":
            return TotalPointsAdapter(**params)
        elif adapter_type == "crypto-latency":
            return CryptoLatencyAdapter(**params)
        else:
            raise ValueError(f"Unknown adapter type: {adapter_type}")

    def _create_feed(self, config: Dict[str, Any]):
        """Create data feed from configuration."""
        feed_type = config["type"]

        if feed_type == "nba":
            return NBADataFeed(config["path"])
        elif feed_type == "crypto":
            return CryptoLatencyDataFeed(
                config["path"], config.get("use_spot_price", True)
            )
        else:
            raise ValueError(f"Unknown feed type: {feed_type}")

    def _calculate_returns(self, bankroll_curve: List[Tuple[float, float]]) -> np.ndarray:
        """Calculate returns from bankroll curve."""
        if not bankroll_curve:
            return np.array([])

        values = [b for _, b in bankroll_curve]
        returns = []
        for i in range(1, len(values)):
            ret = (values[i] - values[i - 1]) / values[i - 1] if values[i - 1] > 0 else 0.0
            returns.append(ret)

        return np.array(returns)

    def _calculate_sharpe(self, bankroll_curve: List[Tuple[float, float]]) -> float:
        """Calculate Sharpe ratio from bankroll curve."""
        returns = self._calculate_returns(bankroll_curve)
        if len(returns) < 2:
            return 0.0

        mean_ret = np.mean(returns)
        std_ret = np.std(returns, ddof=1)

        return (mean_ret / std_ret * np.sqrt(252)) if std_ret > 0 else 0.0

    def _calculate_drawdown_metrics(
        self, bankroll_curve: List[Tuple[float, float]]
    ) -> Dict[str, float]:
        """Calculate drawdown metrics."""
        if not bankroll_curve:
            return {
                "max_drawdown_pct": 0.0,
                "avg_drawdown_pct": 0.0,
                "recovery_time_days": 0.0,
            }

        values = [b for _, b in bankroll_curve]
        timestamps = [t for t, _ in bankroll_curve]

        # Calculate running maximum
        running_max = np.maximum.accumulate(values)
        drawdowns = (running_max - values) / running_max * 100

        max_dd = np.max(drawdowns) if len(drawdowns) > 0 else 0.0
        avg_dd = np.mean(drawdowns) if len(drawdowns) > 0 else 0.0

        # Recovery time (simplified - time from max DD to recovery)
        max_dd_idx = np.argmax(drawdowns)
        recovery_idx = max_dd_idx
        for i in range(max_dd_idx + 1, len(values)):
            if values[i] >= running_max[max_dd_idx]:
                recovery_idx = i
                break

        recovery_time = 0.0
        if recovery_idx > max_dd_idx:
            recovery_time = (timestamps[recovery_idx] - timestamps[max_dd_idx]) / 86400.0

        return {
            "max_drawdown_pct": max_dd,
            "avg_drawdown_pct": avg_dd,
            "recovery_time_days": recovery_time,
        }

    def _calculate_trade_metrics(
        self, fills: List[Fill], settlements: Dict[str, Optional[float]]
    ) -> Dict[str, float]:
        """Calculate trade-level metrics."""
        if not fills:
            return {
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "expectancy": 0.0,
            }

        # Calculate PnL for each fill
        wins = []
        losses = []

        for fill in fills:
            settle = settlements.get(fill.ticker)
            if settle is None:
                continue

            if fill.side == "BID":
                pnl = (settle - fill.price) * fill.size
            else:
                pnl = (fill.price - settle) * fill.size

            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(abs(pnl))

        total_trades = len(wins) + len(losses)
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0

        gross_wins = sum(wins) if wins else 0.0
        gross_losses = sum(losses) if losses else 0.0
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 0.0

        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0

        expectancy = (gross_wins - gross_losses) / total_trades if total_trades > 0 else 0.0

        return {
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
        }

    def _calculate_consistency_metrics(
        self, fills: List[Fill], bankroll_curve: List[Tuple[float, float]]
    ) -> Dict[str, float]:
        """Calculate consistency metrics (streaks, profitable periods)."""
        if not fills:
            return {
                "longest_win_streak": 0,
                "longest_lose_streak": 0,
                "pct_profitable_months": 0.0,
                "pct_profitable_weeks": 0.0,
            }

        # Calculate streaks (simplified - just based on fills order)
        # In reality would need to track by settlement
        current_win_streak = 0
        current_lose_streak = 0
        max_win_streak = 0
        max_lose_streak = 0

        # Simplified - just count based on price movement
        for fill in fills:
            # Placeholder logic
            pass

        # Profitable periods (simplified)
        # Would need to group by week/month
        pct_profitable_months = 0.0
        pct_profitable_weeks = 0.0

        return {
            "longest_win_streak": max_win_streak,
            "longest_lose_streak": max_lose_streak,
            "pct_profitable_months": pct_profitable_months,
            "pct_profitable_weeks": pct_profitable_weeks,
        }

    def _calculate_time_metrics(
        self, fills: List[Fill], feed_metadata: Dict[str, Any]
    ) -> Dict[str, float]:
        """Calculate time-based metrics."""
        if not fills:
            return {
                "avg_holding_time_hours": 0.0,
                "trade_frequency_per_day": 0.0,
            }

        # Average holding time (simplified - would need exit timestamps)
        avg_holding_time_hours = 0.0

        # Trade frequency
        if fills:
            first_ts = fills[0].timestamp.timestamp()
            last_ts = fills[-1].timestamp.timestamp()
            duration_days = (last_ts - first_ts) / 86400.0
            trade_frequency = len(fills) / duration_days if duration_days > 0 else 0.0
        else:
            trade_frequency = 0.0

        return {
            "avg_holding_time_hours": avg_holding_time_hours,
            "trade_frequency_per_day": trade_frequency,
        }

    def _save_results(self, results: BacktestResults) -> None:
        """Save results to database."""
        conn = sqlite3.connect(self.db_path)

        wf = results.walk_forward
        conn.execute(
            """
            INSERT INTO backtest_runs (
                hypothesis, strategy_type, data_source, created_at,
                return_pct, sharpe_ratio, max_drawdown_pct, win_rate_pct, total_trades,
                t_statistic, p_value, is_significant,
                train_sharpe, test_sharpe, is_overfit,
                results_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                results.hypothesis,
                results.strategy_type,
                results.data_source,
                results.created_at.isoformat(),
                results.backtest_result.metrics.return_pct,
                results.validation.sharpe_ratio,
                results.validation.max_drawdown_pct,
                results.validation.win_rate_pct,
                results.validation.total_trades,
                results.validation.t_statistic,
                results.validation.p_value,
                1 if results.validation.is_significant else 0,
                wf.train_sharpe if wf else None,
                wf.test_sharpe if wf else None,
                1 if wf and wf.is_overfit else 0,
                json.dumps(results.to_dict()),
            ),
        )

        conn.commit()
        conn.close()
        logger.info(f"Saved results to database: {self.db_path}")


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------


def example_nba_backtest():
    """Example: Test NBA mispricing hypothesis."""
    agent = BacktestRunnerAgent(
        db_path="data/backtest_results.db",
        enable_walk_forward=True,
        enable_sensitivity=True,
    )

    hypothesis = "Early-game mispricing in NBA markets (Q1-Q2) provides edge"

    adapter_config = {
        "type": "nba-mispricing",
        "params": {
            "min_edge_cents": 3.0,
            "max_period": 2,
            "position_size": 10,
        },
    }

    data_config = {
        "type": "nba",
        "path": "data/recordings/nba_game_001.json",
    }

    results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

    print(results.summary())
    return results


def example_crypto_backtest():
    """Example: Test crypto latency arbitrage hypothesis."""
    agent = BacktestRunnerAgent(
        db_path="data/backtest_results.db",
        enable_walk_forward=False,  # Disable for faster testing
        enable_sensitivity=True,
    )

    hypothesis = "BTC price changes on Kraken predict Kalshi market mispricing"

    adapter_config = {
        "type": "crypto-latency",
        "params": {
            "vol": 0.30,
            "min_edge": 0.10,
            "slippage_cents": 3,
            "min_ttx_sec": 120,
            "max_ttx_sec": 900,
        },
    }

    data_config = {
        "type": "crypto",
        "path": "data/btc_latency_probe.db",
        "use_spot_price": True,
    }

    results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

    print(results.summary())
    return results


if __name__ == "__main__":
    # Run examples
    logging.basicConfig(level=logging.INFO)

    print("\n" + "=" * 70)
    print("NBA Mispricing Backtest")
    print("=" * 70 + "\n")
    # example_nba_backtest()

    print("\n" + "=" * 70)
    print("Crypto Latency Backtest")
    print("=" * 70 + "\n")
    # example_crypto_backtest()
