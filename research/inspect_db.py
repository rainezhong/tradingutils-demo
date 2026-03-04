#!/usr/bin/env python3
"""
Utility to inspect the research tracking database.
Displays all hypotheses, backtests, reports, and deployments.
"""

from research_db import ResearchDB
import sys


def print_separator():
    print("=" * 80)


def main():
    """Display all data in the research database."""
    try:
        with ResearchDB() as db:
            # Hypotheses
            print_separator()
            print("HYPOTHESES")
            print_separator()

            cursor = db.conn.cursor()
            cursor.execute("SELECT * FROM hypotheses ORDER BY created_at DESC")
            hypotheses = cursor.fetchall()

            if not hypotheses:
                print("No hypotheses found.")
            else:
                for h in hypotheses:
                    print(f"\nID: {h['id']}")
                    print(f"Name: {h['name']}")
                    print(f"Description: {h['description']}")
                    print(f"Source: {h['source']}")
                    print(f"Status: {h['status']}")
                    print(f"Created: {h['created_at']}")
                    print(f"Metadata: {h['metadata']}")

            # Backtests
            print("\n")
            print_separator()
            print("BACKTESTS")
            print_separator()

            cursor.execute("SELECT * FROM backtests ORDER BY created_at DESC")
            backtests = cursor.fetchall()

            if not backtests:
                print("No backtests found.")
            else:
                for bt in backtests:
                    print(f"\nID: {bt['id']}")
                    print(f"Hypothesis ID: {bt['hypothesis_id']}")
                    print(f"Sharpe: {bt['sharpe']:.2f}")
                    print(f"Max Drawdown: {bt['max_drawdown']:.1%}")
                    print(f"Win Rate: {bt['win_rate']:.1%}")
                    print(f"P-Value: {bt['p_value']:.4f}")
                    print(f"Num Trades: {bt['num_trades']}")
                    print(f"Created: {bt['created_at']}")
                    print(f"Config: {bt['config']}")

            # Reports
            print("\n")
            print_separator()
            print("REPORTS")
            print_separator()

            cursor.execute("SELECT * FROM reports ORDER BY created_at DESC")
            reports = cursor.fetchall()

            if not reports:
                print("No reports found.")
            else:
                for r in reports:
                    print(f"\nID: {r['id']}")
                    print(f"Hypothesis ID: {r['hypothesis_id']}")
                    print(f"Backtest ID: {r['backtest_id']}")
                    print(f"Notebook: {r['notebook_path']}")
                    print(f"Recommendation: {r['recommendation']}")
                    print(f"Created: {r['created_at']}")

            # Deployments
            print("\n")
            print_separator()
            print("DEPLOYMENTS")
            print_separator()

            cursor.execute("SELECT * FROM deployments ORDER BY deployed_at DESC")
            deployments = cursor.fetchall()

            if not deployments:
                print("No deployments found.")
            else:
                for d in deployments:
                    print(f"\nID: {d['id']}")
                    print(f"Hypothesis ID: {d['hypothesis_id']}")
                    print(f"Status: {d['status']}")
                    print(f"Allocation: ${d['allocation']:,.2f}")
                    print(f"Deployed: {d['deployed_at']}")

            # Summary
            print("\n")
            print_separator()
            print("SUMMARY")
            print_separator()
            print(f"Total Hypotheses: {len(hypotheses)}")
            print(f"Total Backtests: {len(backtests)}")
            print(f"Total Reports: {len(reports)}")
            print(f"Total Deployments: {len(deployments)}")

            if hypotheses:
                cursor.execute("SELECT status, COUNT(*) as count FROM hypotheses GROUP BY status")
                status_counts = cursor.fetchall()
                print("\nHypotheses by Status:")
                for row in status_counts:
                    print(f"  {row['status']}: {row['count']}")

            if deployments:
                cursor.execute("SELECT SUM(allocation) as total FROM deployments WHERE status = 'active'")
                total_allocation = cursor.fetchone()['total'] or 0
                print(f"\nTotal Active Allocation: ${total_allocation:,.2f}")

            print_separator()

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
