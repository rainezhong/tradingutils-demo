#!/usr/bin/env python3
"""
Test script for the research tracking database.
Demonstrates the full lifecycle: hypothesis -> backtest -> report -> deployment.
"""

from research_db import ResearchDB, Hypothesis, BacktestResult
from datetime import datetime


def main():
    """Run a complete test of the research database."""
    print("Testing ResearchDB...")

    with ResearchDB() as db:
        # 1. Create a hypothesis
        print("\n1. Creating hypothesis...")
        hypothesis = Hypothesis(
            id=None,
            name="NBA Home Underdog Fade",
            description="Fade home underdogs in the 4th quarter when trailing by 10-15 points",
            source="mcp_research",
            created_at=datetime.now(),
            status="pending",
            metadata={
                "market_type": "moneyline",
                "sport": "nba",
                "trigger": "4th_quarter_deficit",
                "min_deficit": 10,
                "max_deficit": 15
            }
        )

        h_id = db.save_hypothesis(hypothesis)
        print(f"   Created hypothesis ID: {h_id}")

        # 2. Get pending hypotheses
        print("\n2. Getting pending hypotheses...")
        pending = db.get_pending_hypotheses()
        print(f"   Found {len(pending)} pending hypothesis")
        for h in pending:
            print(f"   - {h.name} (status: {h.status})")

        # 3. Save backtest results
        print("\n3. Running backtest (simulated)...")
        backtest = BacktestResult(
            id=None,
            hypothesis_id=h_id,
            sharpe=1.85,
            max_drawdown=0.12,
            win_rate=0.58,
            p_value=0.003,
            num_trades=147,
            config={
                "start_date": "2025-01-01",
                "end_date": "2026-02-27",
                "initial_capital": 10000,
                "position_size": 100
            },
            created_at=datetime.now()
        )

        bt_id = db.save_backtest_results(h_id, backtest)
        print(f"   Saved backtest ID: {bt_id}")
        print(f"   Sharpe: {backtest.sharpe:.2f}, Win Rate: {backtest.win_rate:.1%}")

        # 4. Generate report
        print("\n4. Generating research report...")
        report_id = db.save_report(
            hypothesis_id=h_id,
            notebook_path="research/reports/nba_home_underdog_fade.ipynb",
            recommendation="deploy",
            backtest_id=bt_id
        )
        print(f"   Saved report ID: {report_id}")

        # 5. Deploy to live
        print("\n5. Deploying to live trading...")
        dep_id = db.mark_deployed(
            hypothesis_id=h_id,
            allocation=2500.0,
            status="active"
        )
        print(f"   Created deployment ID: {dep_id}")
        print(f"   Allocated: $2,500")

        # 6. Check deployments
        print("\n6. Checking active deployments...")
        deployments = db.get_deployments(status="active")
        print(f"   Active deployments: {len(deployments)}")
        for dep in deployments:
            h = db.get_hypothesis(dep.hypothesis_id)
            print(f"   - {h.name}: ${dep.allocation:,.0f}")

        # 7. Show all data for hypothesis
        print(f"\n7. Full history for hypothesis {h_id}...")
        h = db.get_hypothesis(h_id)
        print(f"   Name: {h.name}")
        print(f"   Status: {h.status}")
        print(f"   Source: {h.source}")

        backtests = db.get_backtest_results(h_id)
        print(f"   Backtests: {len(backtests)}")
        for bt in backtests:
            print(f"     - Sharpe: {bt.sharpe:.2f}, Trades: {bt.num_trades}")

        reports = db.get_reports(h_id)
        print(f"   Reports: {len(reports)}")
        for r in reports:
            print(f"     - {r.recommendation}: {r.notebook_path}")

    print("\nTest completed successfully!")


if __name__ == "__main__":
    main()
