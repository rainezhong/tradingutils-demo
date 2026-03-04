"""Tests for the backtest runner agent."""

import sys
import tempfile
from pathlib import Path

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agents.backtest_runner import BacktestRunnerAgent, BacktestResults


class TestBacktestRunnerAgent:
    """Test suite for BacktestRunnerAgent."""

    def test_agent_initialization(self):
        """Test agent can be initialized."""
        agent = BacktestRunnerAgent(enable_walk_forward=False, enable_sensitivity=False)
        assert agent is not None
        assert agent.enable_walk_forward is False
        assert agent.enable_sensitivity is False

    def test_agent_with_database(self):
        """Test agent with database initialization."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        agent = BacktestRunnerAgent(
            db_path=db_path, enable_walk_forward=False, enable_sensitivity=False
        )

        # Database should be created
        assert Path(db_path).exists()

        # Clean up
        Path(db_path).unlink()

    @pytest.mark.skipif(
        not Path("data/btc_latency_probe.db").exists(),
        reason="Crypto latency data not available",
    )
    def test_crypto_backtest(self):
        """Test crypto latency backtest (if data available)."""
        agent = BacktestRunnerAgent(enable_walk_forward=False, enable_sensitivity=False)

        hypothesis = "Test crypto latency arbitrage"
        adapter_config = {
            "type": "crypto-latency",
            "params": {
                "vol": 0.30,
                "min_edge": 0.10,
                "slippage_cents": 3,
                "min_ttx_sec": 120,
                "max_ttx_sec": 900,
                "kelly_fraction": 0.5,
                "max_bet_dollars": 50.0,
            },
        }

        data_config = {
            "type": "crypto",
            "path": "data/btc_latency_probe.db",
            "use_spot_price": True,
        }

        results = agent.test_hypothesis(hypothesis, adapter_config, data_config)

        # Verify results structure
        assert isinstance(results, BacktestResults)
        assert results.hypothesis == hypothesis
        assert results.strategy_type == "crypto-latency"
        assert results.validation is not None

        # Basic sanity checks
        assert results.validation.total_trades >= 0
        assert results.validation.sharpe_ratio != 0 or results.validation.total_trades == 0

    def test_validation_metrics_empty_backtest(self):
        """Test validation metrics calculation with no trades."""
        from src.backtesting.engine import BacktestConfig, BacktestEngine
        from src.backtesting.adapters.crypto_adapter import (
            CryptoLatencyAdapter,
            CryptoLatencyDataFeed,
        )

        # Create agent
        agent = BacktestRunnerAgent(enable_walk_forward=False, enable_sensitivity=False)

        # Create a backtest result with no trades (very high min_edge)
        if Path("data/btc_latency_probe.db").exists():
            adapter = CryptoLatencyAdapter(min_edge=10.0)  # Impossibly high edge
            feed = CryptoLatencyDataFeed("data/btc_latency_probe.db")
            config = BacktestConfig()
            engine = BacktestEngine(config)
            result = engine.run(feed, adapter, verbose=False)

            # Validate
            validation = agent.statistical_validation(result)

            # Should handle zero trades gracefully
            assert validation.total_trades >= 0
            assert validation.sharpe_ratio == 0.0
            assert validation.p_value == 1.0
            assert validation.is_significant is False

    def test_results_serialization(self):
        """Test BacktestResults can be serialized to dict."""
        from src.backtesting.metrics import BacktestMetrics, BacktestResult
        from agents.backtest_runner import ValidationMetrics

        # Create minimal result
        metrics = BacktestMetrics(
            total_frames=100,
            total_signals=10,
            total_fills=5,
            initial_bankroll=1000.0,
            final_bankroll=1050.0,
            net_pnl=50.0,
            return_pct=5.0,
        )

        validation = ValidationMetrics(
            sharpe_ratio=1.5,
            sortino_ratio=1.8,
            calmar_ratio=0.5,
            information_ratio=1.5,
            t_statistic=2.0,
            p_value=0.04,
            is_significant=True,
            max_drawdown_pct=10.0,
            avg_drawdown_pct=3.0,
            recovery_time_days=5.0,
            value_at_risk_95=-2.0,
            win_rate_pct=60.0,
            profit_factor=1.5,
            avg_win=20.0,
            avg_loss=-10.0,
            expectancy=5.0,
            longest_win_streak=3,
            longest_lose_streak=2,
            pct_profitable_months=70.0,
            pct_profitable_weeks=65.0,
            total_trades=5,
            avg_holding_time_hours=24.0,
            trade_frequency_per_day=0.5,
        )

        backtest_result = BacktestResult(
            adapter_name="test",
            metrics=metrics,
            signals=[],
            fills=[],
        )

        results = BacktestResults(
            backtest_result=backtest_result,
            validation=validation,
            hypothesis="Test hypothesis",
            strategy_type="test",
            data_source="test",
        )

        # Should serialize without error
        data = results.to_dict()
        assert isinstance(data, dict)
        assert data["hypothesis"] == "Test hypothesis"
        assert data["validation"]["sharpe_ratio"] == 1.5

        # Should generate summary
        summary = results.summary()
        assert isinstance(summary, str)
        assert "Test hypothesis" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
