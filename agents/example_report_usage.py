#!/usr/bin/env python3
"""Example usage of ReportGeneratorAgent.

Demonstrates how to generate a research report from backtest results.
"""

from datetime import datetime
from pathlib import Path

from src.backtesting.metrics import BacktestMetrics, BacktestResult
from src.core.models import Fill

from agents.report_generator import HypothesisInfo, ReportGeneratorAgent


def create_example_backtest_result() -> BacktestResult:
    """Create a sample backtest result for demonstration."""

    # Create sample metrics
    metrics = BacktestMetrics(
        total_frames=1000,
        total_signals=150,
        total_fills=120,
        initial_bankroll=10000.0,
        final_bankroll=12500.0,
        net_pnl=2500.0,
        return_pct=25.0,
        total_fees=120.0,
        max_drawdown_pct=8.5,
        peak_bankroll=13000.0,
        winning_fills=75,
        losing_fills=45,
        win_rate_pct=62.5,
        portfolio={
            "initial_bankroll": 10000.0,
            "final_bankroll": 12500.0,
            "net_pnl": 2500.0,
            "return_pct": 25.0,
            "total_fees": 120.0,
            "max_drawdown_pct": 8.5,
            "peak_bankroll": 13000.0,
        },
    )

    # Create sample fills
    fills = []
    for i in range(120):
        fill = Fill(
            ticker=f"TEST-MARKET-{i % 20}",
            side="BID" if i % 2 == 0 else "ASK",
            price=50.0 + (i % 10) * 2.5,  # Price between 50-72.5 cents
            size=10 + (i % 5) * 5,  # Size between 10-35 contracts
            order_id=f"order-{i}",
            timestamp=datetime.now(),
        )
        fills.append(fill)

    # Create sample bankroll curve
    bankroll_curve = []
    current_bankroll = 10000.0
    for i in range(120):
        # Simulate winning ~62.5% of trades
        if i % 8 < 5:  # 5 out of 8 = 62.5%
            current_bankroll += 30.0  # Average win
        else:
            current_bankroll -= 15.0  # Average loss

        bankroll_curve.append((datetime.now().timestamp() + i * 3600, current_bankroll))

    # Create settlements (62.5% win rate)
    settlements = {}
    for i, fill in enumerate(fills):
        if i % 8 < 5:  # Winner
            if fill.side == "BID":
                settlements[fill.ticker] = 100.0  # Full payout
            else:
                settlements[fill.ticker] = 0.0  # No payout (sold no wins)
        else:  # Loser
            if fill.side == "BID":
                settlements[fill.ticker] = 0.0  # No payout
            else:
                settlements[fill.ticker] = 100.0  # Full payout (sold yes loses)

    # Create result
    result = BacktestResult(
        adapter_name="Example Strategy",
        metrics=metrics,
        signals=[],  # Not needed for report
        fills=fills,
        feed_metadata={
            "data_source": "example_data.db",
            "start_date": "2026-01-01",
            "end_date": "2026-02-27",
        },
        config={
            "initial_bankroll": 10000.0,
            "fill_probability": 1.0,
            "slippage": 0.0,
        },
        bankroll_curve=bankroll_curve,
        settlements=settlements,
        started_at=datetime.now(),
        completed_at=datetime.now(),
    )

    return result


def main():
    """Run example report generation."""
    print("Creating example backtest result...")
    result = create_example_backtest_result()

    print("Creating hypothesis info...")
    hypothesis = HypothesisInfo(
        name="example_momentum_strategy",
        description="Test momentum strategy that buys undervalued contracts in trending markets",
        market_type="NBA",
        strategy_family="momentum",
        parameters={
            "lookback_period": 10,
            "momentum_threshold": 0.15,
            "max_position_size": 100,
            "entry_edge_bps": 200,
        },
        data_source="nba_historical.db",
        time_period="2026-01-01 to 2026-02-27",
    )

    print("Initializing ReportGeneratorAgent...")
    agent = ReportGeneratorAgent()

    print("Generating research report...")
    report_path = agent.generate(hypothesis, result)

    print(f"\n✅ Report generated successfully!")
    print(f"📁 Location: {report_path}")
    print(f"\nTo view the report:")
    print(f"  jupyter notebook {report_path}")


if __name__ == "__main__":
    main()
