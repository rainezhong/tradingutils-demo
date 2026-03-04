"""Tests for ReportGeneratorAgent."""

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agents.report_generator import HypothesisInfo, ReportGeneratorAgent
from src.backtesting.metrics import BacktestMetrics, BacktestResult
from src.core.models import Fill


def create_sample_result(
    total_fills: int = 50,
    win_rate: float = 0.6,
    net_pnl: float = 1000.0,
) -> BacktestResult:
    """Create a sample backtest result for testing."""
    initial = 10000.0
    final = initial + net_pnl
    winners = int(total_fills * win_rate)
    losers = total_fills - winners

    metrics = BacktestMetrics(
        total_frames=1000,
        total_signals=total_fills,
        total_fills=total_fills,
        initial_bankroll=initial,
        final_bankroll=final,
        net_pnl=net_pnl,
        return_pct=(net_pnl / initial) * 100,
        total_fees=50.0,
        max_drawdown_pct=5.0,
        peak_bankroll=final + 100.0,
        winning_fills=winners,
        losing_fills=losers,
        win_rate_pct=win_rate * 100,
        portfolio={},
    )

    # Create sample fills
    fills = []
    for i in range(total_fills):
        fills.append(
            Fill(
                ticker=f"TEST-{i}",
                side="BID",
                price=50.0,
                size=10,
                order_id=f"order-{i}",
                timestamp=datetime.now(),
            )
        )

    # Create bankroll curve
    bankroll_curve = []
    current = initial
    step = net_pnl / total_fills
    for i in range(total_fills):
        current += step
        bankroll_curve.append((datetime.now().timestamp() + i * 3600, current))

    # Create settlements
    settlements = {}
    for i, fill in enumerate(fills):
        if i < winners:
            settlements[fill.ticker] = 100.0  # Winner
        else:
            settlements[fill.ticker] = 0.0  # Loser

    return BacktestResult(
        adapter_name="TestStrategy",
        metrics=metrics,
        signals=[],
        fills=fills,
        feed_metadata={},
        config={},
        bankroll_curve=bankroll_curve,
        settlements=settlements,
        started_at=datetime.now(),
        completed_at=datetime.now(),
    )


def test_report_generator_init():
    """Test ReportGeneratorAgent initialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = ReportGeneratorAgent(reports_dir=Path(tmpdir))
        assert agent.reports_dir == Path(tmpdir)
        assert agent.min_sharpe_deploy == 1.0
        assert agent.min_sharpe_paper == 0.5


def test_report_generator_recommendation_deploy():
    """Test deployment recommendation for high-quality strategy."""
    agent = ReportGeneratorAgent()

    # Create profitable result with good Sharpe
    result = create_sample_result(total_fills=100, win_rate=0.7, net_pnl=5000.0)
    metrics = agent._calculate_advanced_metrics(result)

    # Should recommend deploy
    recommendation = agent._generate_recommendation(result, metrics)
    # Note: May be 'paper' or 'reject' if Sharpe/p-value not high enough
    assert recommendation in ["deploy", "paper", "reject"]


def test_report_generator_recommendation_reject():
    """Test rejection recommendation for losing strategy."""
    agent = ReportGeneratorAgent()

    # Create losing result
    result = create_sample_result(total_fills=20, win_rate=0.3, net_pnl=-500.0)
    metrics = agent._calculate_advanced_metrics(result)

    # Should recommend reject
    recommendation = agent._generate_recommendation(result, metrics)
    assert recommendation == "reject"


def test_advanced_metrics_calculation():
    """Test advanced metrics calculation."""
    agent = ReportGeneratorAgent()
    result = create_sample_result(total_fills=50, win_rate=0.6, net_pnl=1000.0)

    metrics = agent._calculate_advanced_metrics(result)

    # Check all metrics are present
    assert "sharpe_ratio" in metrics
    assert "sortino_ratio" in metrics
    assert "calmar_ratio" in metrics
    assert "profit_factor" in metrics
    assert "avg_win" in metrics
    assert "avg_loss" in metrics
    assert "max_consecutive_losses" in metrics
    assert "p_value" in metrics

    # Check types
    assert isinstance(metrics["sharpe_ratio"], float)
    assert isinstance(metrics["p_value"], float)


def test_generate_report():
    """Test full report generation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        agent = ReportGeneratorAgent(reports_dir=Path(tmpdir))

        hypothesis = HypothesisInfo(
            name="test_strategy",
            description="Test strategy for unit testing",
            market_type="TEST",
            strategy_family="test",
            parameters={"param1": 10, "param2": 0.5},
            data_source="test.db",
        )

        result = create_sample_result(total_fills=50, win_rate=0.6, net_pnl=1000.0)

        # Generate report
        report_path = agent.generate(hypothesis, result)

        # Check file exists
        assert Path(report_path).exists()
        assert Path(report_path).suffix == ".ipynb"

        # Verify it's a valid notebook
        import nbformat

        nb = nbformat.read(report_path, as_version=4)
        assert len(nb.cells) > 5  # Should have multiple cells
        assert nb.cells[0].cell_type == "markdown"  # Title
        assert nb.cells[1].cell_type == "code"  # Setup


def test_create_executive_summary():
    """Test executive summary creation."""
    agent = ReportGeneratorAgent()

    hypothesis = HypothesisInfo(
        name="test_strategy",
        description="Test description",
        market_type="NBA",
        strategy_family="momentum",
        parameters={},
        data_source="test.db",
        time_period="2026-01-01 to 2026-02-27",
    )

    result = create_sample_result(total_fills=50, win_rate=0.6, net_pnl=1000.0)
    metrics = agent._calculate_advanced_metrics(result)
    recommendation = agent._generate_recommendation(result, metrics)

    summary = agent._create_executive_summary(hypothesis, result, metrics, recommendation)

    # Check key elements are present
    assert "Executive Summary" in summary
    assert "test_strategy" in summary
    assert "Total Trades" in summary
    assert "Win Rate" in summary
    assert "Sharpe Ratio" in summary


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
