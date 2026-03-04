#!/usr/bin/env python3
"""Example: Using scenario tests to evaluate strategy improvements.

This script demonstrates how to:
1. Test a baseline configuration
2. Test an improved configuration
3. Compare results across scenarios
4. Identify which improvements help in which scenarios
"""

from strategies.latency_arb.config import LatencyArbConfig
from test_latency_scenarios import (
    MarketFollowsTrend,
    MarketOscillates,
    MarketIgnoresExternal,
    MarketGoesOpposite,
    MarketOvershoots,
    MarketDelayedFollow,
    MarketSuddenReversal,
    MarketLowLiquidity,
    simulate_strategy_response,
)


def evaluate_config(config, config_name):
    """Evaluate a config across all scenarios."""
    scenarios = [
        ("Follows Trend", MarketFollowsTrend),
        ("Oscillates", MarketOscillates),
        ("Ignores External", MarketIgnoresExternal),
        ("Goes Opposite", MarketGoesOpposite),
        ("Overshoots", MarketOvershoots),
        ("Delayed Follow", MarketDelayedFollow),
        ("Sudden Reversal", MarketSuddenReversal),
        ("Low Liquidity", MarketLowLiquidity),
    ]

    print(f"\n{'='*70}")
    print(f"Configuration: {config_name}")
    print(f"{'='*70}")
    print(f"{'Scenario':<20} | {'Opps':>5} | {'Exec':>4} | {'P&L':>10}")
    print("-" * 70)

    total_pnl = 0
    total_opps = 0
    total_exec = 0

    for scenario_name, scenario_class in scenarios:
        sim = scenario_class(ticker=f"TEST-{scenario_name.upper()}")
        result = simulate_strategy_response(sim, config)

        total_pnl += result["final_pnl_cents"]
        total_opps += result["num_opportunities"]
        total_exec += result["num_executions"]

        pnl_str = f"{result['final_pnl_cents']:+} cents"
        print(
            f"{scenario_name:<20} | {result['num_opportunities']:>5} | "
            f"{result['num_executions']:>4} | {pnl_str:>10}"
        )

    print("-" * 70)
    print(
        f"{'TOTAL':<20} | {total_opps:>5} | {total_exec:>4} | "
        f"{total_pnl:+} cents"
    )

    return {
        "total_pnl": total_pnl,
        "total_opps": total_opps,
        "total_exec": total_exec,
        "win_rate": total_exec / max(1, total_opps),
    }


def main():
    print("\n" + "=" * 70)
    print("LATENCY STRATEGY SCENARIO EVALUATION")
    print("=" * 70)

    # ========================================================================
    # 1. BASELINE: Current production config
    # ========================================================================
    baseline_config = LatencyArbConfig(
        min_edge_pct=0.15,
        signal_stability_enabled=False,
        early_exit_enabled=False,
        kelly_fraction=0,
        base_position_usd=50.0,
    )

    baseline_results = evaluate_config(baseline_config, "BASELINE (current prod)")

    # ========================================================================
    # 2. IMPROVED: Add signal stability filter
    # ========================================================================
    improved_config_v1 = LatencyArbConfig(
        min_edge_pct=0.15,
        signal_stability_enabled=True,
        signal_stability_duration_sec=1.0,
        early_exit_enabled=False,
        kelly_fraction=0,
        base_position_usd=50.0,
    )

    v1_results = evaluate_config(improved_config_v1, "V1: Add Stability Filter")

    # ========================================================================
    # 3. IMPROVED: Add early exit protection
    # ========================================================================
    improved_config_v2 = LatencyArbConfig(
        min_edge_pct=0.15,
        signal_stability_enabled=True,
        signal_stability_duration_sec=1.0,
        early_exit_enabled=True,
        early_exit_profit_threshold=0.15,
        kelly_fraction=0,
        base_position_usd=50.0,
    )

    v2_results = evaluate_config(improved_config_v2, "V2: Add Early Exit")

    # ========================================================================
    # 4. IMPROVED: Lower edge threshold to catch more opportunities
    # ========================================================================
    improved_config_v3 = LatencyArbConfig(
        min_edge_pct=0.10,  # Lower threshold
        signal_stability_enabled=True,
        signal_stability_duration_sec=1.0,
        early_exit_enabled=True,
        early_exit_profit_threshold=0.15,
        kelly_fraction=0,
        base_position_usd=50.0,
    )

    v3_results = evaluate_config(improved_config_v3, "V3: Lower Edge Threshold")

    # ========================================================================
    # 5. COMPARISON SUMMARY
    # ========================================================================
    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"{'Config':<30} | {'Total P&L':>12} | {'Exec':>4} | {'Win Rate':>8}")
    print("-" * 70)

    configs = [
        ("BASELINE", baseline_results),
        ("V1: Stability Filter", v1_results),
        ("V2: + Early Exit", v2_results),
        ("V3: + Lower Threshold", v3_results),
    ]

    for name, results in configs:
        wr = results["win_rate"] * 100
        print(
            f"{name:<30} | {results['total_pnl']:+>10} ¢ | "
            f"{results['total_exec']:>4} | {wr:>7.1f}%"
        )

    # ========================================================================
    # 6. INSIGHTS
    # ========================================================================
    print("\n" + "=" * 70)
    print("INSIGHTS & RECOMMENDATIONS")
    print("=" * 70)

    print("\n1. Stability Filter Impact:")
    print(f"   - Baseline executions: {baseline_results['total_exec']}")
    print(f"   - With stability: {v1_results['total_exec']}")
    if v1_results["total_exec"] < baseline_results["total_exec"]:
        print("   ✓ Stability filter successfully reduces entries (prevents whipsaws)")
    else:
        print("   ✗ Stability filter not effective (review duration threshold)")

    print("\n2. Early Exit Impact:")
    pnl_improvement = v2_results["total_pnl"] - v1_results["total_pnl"]
    print(f"   - P&L change: {pnl_improvement:+} cents")
    if pnl_improvement > 0:
        print("   ✓ Early exit improves P&L (captures profits before reversals)")
    elif pnl_improvement == 0:
        print("   - Early exit has no impact (scenarios didn't trigger exit)")
    else:
        print("   ✗ Early exit reduces P&L (exiting too early?)")

    print("\n3. Edge Threshold Impact:")
    opps_improvement = v3_results["total_opps"] - v2_results["total_opps"]
    print(f"   - Opportunity increase: {opps_improvement:+}")
    print(f"   - P&L change: {v3_results['total_pnl'] - v2_results['total_pnl']:+} cents")
    if v3_results["total_pnl"] > v2_results["total_pnl"]:
        print("   ✓ Lower threshold captures more profitable opportunities")
    else:
        print("   ✗ Lower threshold increases noise without profit improvement")

    print("\n4. Overall Recommendation:")
    best_config = max(configs, key=lambda x: x[1]["total_pnl"])
    print(f"   Best performing config: {best_config[0]}")
    print(f"   Total P&L: {best_config[1]['total_pnl']:+} cents")
    print(f"   Win Rate: {best_config[1]['win_rate']*100:.1f}%")

    # Scenario-specific insights
    print("\n5. Scenario-Specific Insights:")
    print("   - Run individual tests to see which scenarios benefit most")
    print("   - Example:")
    print("     $ python3 -m pytest test_latency_scenarios.py::test_market_oscillates_adverse_selection -v")


if __name__ == "__main__":
    main()
