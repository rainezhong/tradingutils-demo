#!/usr/bin/env python3
"""
Arbitrage Algorithm Test Runner

Usage:
    python scripts/test_arb.py              # Run all tests interactively
    python scripts/test_arb.py detect       # Test opportunity detection
    python scripts/test_arb.py execute      # Test full execution
    python scripts/test_arb.py failure      # Test failure/rollback handling
    python scripts/test_arb.py live         # Test with real market data (read-only)
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from typing import List, Optional


def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60 + "\n")


def print_result(label: str, value, indent: int = 2):
    """Print a formatted result."""
    prefix = " " * indent
    print(f"{prefix}{label}: {value}")


def test_detection():
    """Test opportunity detection with mock data."""
    print_header("TEST: Opportunity Detection")

    from arb.spread_detector import SpreadDetector
    from tests.test_arb_integration_e2e import MockMarketMatcher

    # Create mock matcher with test opportunities
    matcher = MockMarketMatcher()

    # Opportunity 1: Cross-platform arb on BTC (large spread to overcome fees)
    # Fees: Kalshi ~7% of P*(1-P), Poly ~2% of notional
    # Need at least 5-6 cent gross edge to have 2+ cent net edge
    matcher.add_pair(
        pair_id="btc_100k",
        kalshi_ticker="BTC-100K-YES",
        poly_token_id="poly_btc_100k_abc",
        event_description="Will BTC exceed $100,000 by March 2026?",
    )
    matcher.set_quotes(
        pair_id="btc_100k",
        kalshi_yes_bid=0.40,
        kalshi_yes_ask=0.42,  # Buy here at 42 cents
        poly_yes_bid=0.50,    # Sell here at 50 cents (8 cent gross edge)
        poly_yes_ask=0.52,
        size=200,
    )

    # Opportunity 2: Another arb on ETH
    matcher.add_pair(
        pair_id="eth_5k",
        kalshi_ticker="ETH-5K-YES",
        poly_token_id="poly_eth_5k_xyz",
        event_description="Will ETH exceed $5,000 by June 2026?",
    )
    matcher.set_quotes(
        pair_id="eth_5k",
        kalshi_yes_bid=0.30,
        kalshi_yes_ask=0.32,  # Buy here
        poly_yes_bid=0.40,    # Sell here (8 cent gross edge)
        poly_yes_ask=0.42,
        size=150,
    )

    # Opportunity 3: No edge (should be filtered)
    matcher.add_pair(
        pair_id="no_edge",
        kalshi_ticker="TEST-NO-EDGE",
        poly_token_id="poly_no_edge",
        event_description="No arbitrage opportunity here",
    )
    matcher.set_quotes(
        pair_id="no_edge",
        kalshi_yes_bid=0.50,
        kalshi_yes_ask=0.51,
        poly_yes_bid=0.50,
        poly_yes_ask=0.51,
        size=100,
    )

    # Create detector with generous quote age tolerance
    detector = SpreadDetector(
        market_matcher=matcher,
        min_edge_cents=2.0,
        min_liquidity_usd=50.0,
        max_quote_age_ms=60000.0,  # 60 second tolerance for testing
    )

    # Run detection
    print("Running detection cycle...")
    opportunities = detector.check_once()

    print(f"\nFound {len(opportunities)} opportunities:\n")

    for i, opp in enumerate(opportunities, 1):
        print(f"  [{i}] {opp.pair.event_description}")
        print_result("Type", opp.opportunity_type, 6)
        print_result("Buy", f"{opp.buy_platform.value} @ ${opp.buy_price:.3f}", 6)
        print_result("Sell", f"{opp.sell_platform.value} @ ${opp.sell_price:.3f}", 6)
        print_result("Gross edge", f"${opp.gross_edge_per_contract:.4f}/contract", 6)
        print_result("Net edge (after fees)", f"${opp.net_edge_per_contract:.4f}/contract", 6)
        print_result("Max contracts", opp.max_contracts, 6)
        print_result("Estimated profit", f"${opp.estimated_profit_usd:.2f}", 6)
        print()

    if len(opportunities) >= 2:
        print("SUCCESS: Detection working correctly")
        return True
    else:
        print("WARNING: Expected at least 2 opportunities")
        return False


def test_execution():
    """Test full spread execution with mocks."""
    print_header("TEST: Full Spread Execution")

    from tests.test_arb_integration_e2e import MockMarketMatcher, MockExchange
    from src.oms import OrderManagementSystem, SpreadExecutor, CapitalManager, SpreadExecutorConfig
    from arb.spread_detector import SpreadDetector

    # Setup mock exchanges
    print("Setting up mock exchanges...")
    kalshi = MockExchange("kalshi", initial_balance=10000.0)
    poly = MockExchange("polymarket", initial_balance=10000.0)

    print_result("Kalshi balance", f"${kalshi.get_balance():.2f}")
    print_result("Polymarket balance", f"${poly.get_balance():.2f}")

    # Setup capital manager
    capital_mgr = CapitalManager()
    capital_mgr.sync_from_exchange(kalshi)
    capital_mgr.sync_from_exchange(poly)

    # Setup OMS
    oms = OrderManagementSystem(capital_manager=capital_mgr)
    oms.register_exchange(kalshi)
    oms.register_exchange(poly)

    # Setup executor
    executor = SpreadExecutor(
        oms,
        capital_mgr,
        SpreadExecutorConfig(
            leg1_timeout_seconds=5.0,
            leg2_timeout_seconds=5.0,
            poll_interval_seconds=0.1,
        ),
    )

    # Create opportunity
    matcher = MockMarketMatcher()
    matcher.add_pair(
        pair_id="exec_test",
        kalshi_ticker="EXEC-TEST-YES",
        poly_token_id="poly_exec_test",
        event_description="Execution test market",
    )
    matcher.set_quotes(
        pair_id="exec_test",
        kalshi_yes_bid=0.44,
        kalshi_yes_ask=0.45,
        poly_yes_bid=0.50,
        poly_yes_ask=0.51,
        size=50,
    )

    # Detect
    detector = SpreadDetector(matcher, min_edge_cents=2.0, min_liquidity_usd=10.0)
    opportunities = detector.check_once()

    if not opportunities:
        print("ERROR: No opportunities detected")
        return False

    opp = opportunities[0]
    print(f"\nExecuting opportunity: {opp.pair.event_description}")
    print_result("Expected profit", f"${opp.estimated_profit_usd:.2f}")

    # Execute
    result = executor.execute_spread(
        opportunity_id="test_exec_1",
        leg1_exchange=opp.buy_platform.value,
        leg1_ticker=opp.buy_market_id,
        leg1_side="buy",
        leg1_price=opp.buy_price * 100,
        leg1_size=min(opp.max_contracts, 25),
        leg2_exchange=opp.sell_platform.value,
        leg2_ticker=opp.sell_market_id,
        leg2_side="sell",
        leg2_price=opp.sell_price * 100,
        leg2_size=min(opp.max_contracts, 25),
        expected_profit=opp.estimated_profit_usd,
    )

    print(f"\nExecution result:")
    print_result("Status", result.status.value)
    print_result("Leg 1 filled", result.leg1.is_filled)
    print_result("Leg 2 filled", result.leg2.is_filled)
    print_result("Duration", f"{result.duration_seconds:.2f}s" if result.duration_seconds else "N/A")

    if result.actual_profit is not None:
        print_result("Actual profit", f"${result.actual_profit:.2f}")

    print(f"\nFinal balances:")
    print_result("Kalshi", f"${kalshi.get_balance():.2f}")
    print_result("Polymarket", f"${poly.get_balance():.2f}")

    print(f"\nOMS metrics:")
    metrics = oms.get_metrics()
    for key, value in metrics.items():
        print_result(key, value)

    if result.is_successful:
        print("\nSUCCESS: Execution completed successfully")
        return True
    else:
        print(f"\nWARNING: Execution status: {result.status.value}")
        return False


def test_failure_handling():
    """Test failure scenarios and rollback."""
    print_header("TEST: Failure Handling & Rollback")

    from tests.test_arb_integration_e2e import MockExchange
    from src.oms import OrderManagementSystem, SpreadExecutor, CapitalManager, SpreadExecutorConfig

    # Setup with Polymarket rejecting orders
    print("Setting up exchanges (Polymarket will reject orders)...")
    kalshi = MockExchange("kalshi", initial_balance=10000.0)
    poly = MockExchange("polymarket", initial_balance=10000.0)

    # Configure Poly to reject
    poly.set_fill_behavior(reject_orders=True)

    print_result("Kalshi", "Normal operation")
    print_result("Polymarket", "REJECTING ALL ORDERS")

    capital_mgr = CapitalManager()
    capital_mgr.sync_from_exchange(kalshi)
    capital_mgr.sync_from_exchange(poly)

    initial_kalshi = kalshi.get_balance()

    oms = OrderManagementSystem(capital_manager=capital_mgr)
    oms.register_exchange(kalshi)
    oms.register_exchange(poly)

    executor = SpreadExecutor(
        oms,
        capital_mgr,
        SpreadExecutorConfig(
            leg1_timeout_seconds=2.0,
            leg2_timeout_seconds=2.0,
            rollback_timeout_seconds=2.0,
            poll_interval_seconds=0.1,
        ),
    )

    print("\nExecuting spread (leg 2 will fail)...")
    result = executor.execute_spread(
        opportunity_id="fail_test",
        leg1_exchange="kalshi",
        leg1_ticker="FAIL-TEST",
        leg1_side="buy",
        leg1_price=45.0,
        leg1_size=10,
        leg2_exchange="polymarket",
        leg2_ticker="FAIL-TEST-POLY",
        leg2_side="sell",
        leg2_price=50.0,
        leg2_size=10,
    )

    print(f"\nResult:")
    print_result("Status", result.status.value)
    print_result("Leg 1 status", result.leg1.status.value)
    print_result("Leg 2 status", result.leg2.status.value)

    if result.error:
        print_result("Error", result.error)

    if result.rollback_order:
        print_result("Rollback attempted", "Yes")
        print_result("Rollback order", result.rollback_order.order_id)

    final_kalshi = kalshi.get_balance()
    print(f"\nBalance change:")
    print_result("Initial", f"${initial_kalshi:.2f}")
    print_result("Final", f"${final_kalshi:.2f}")
    print_result("Difference", f"${final_kalshi - initial_kalshi:.2f}")

    if result.status.value in ("rolled_back", "partial", "failed"):
        print("\nSUCCESS: Failure handling working correctly")
        return True
    else:
        print("\nWARNING: Unexpected status")
        return False


def test_live_detection():
    """Test with real market data (read-only, no trading)."""
    print_header("TEST: Live Market Detection (Read-Only)")

    try:
        from src.exchanges.kalshi import KalshiExchange
        from src.matching import MarketMatcher
    except ImportError as e:
        print(f"Import error: {e}")
        print("Make sure all dependencies are installed")
        return False

    print("Connecting to Kalshi (read-only)...")

    try:
        kalshi = KalshiExchange()

        # Fetch markets
        print("Fetching open markets...")
        markets = kalshi.get_markets(status="open", limit=20)

        print(f"Found {len(markets)} open markets\n")

        for i, market in enumerate(markets[:5], 1):
            print(f"  [{i}] {market.ticker}")
            print_result("Title", market.title[:60] + "..." if len(market.title) > 60 else market.title, 6)
            print_result("Status", market.status, 6)
            print()

        if len(markets) >= 5:
            print("SUCCESS: Live data connection working")
            return True
        else:
            print("WARNING: Fewer markets than expected")
            return True

    except Exception as e:
        print(f"Error: {e}")
        print("\nThis is expected if you don't have API credentials configured.")
        print("Set KALSHI_API_KEY and KALSHI_API_SECRET environment variables.")
        return False


def test_capital_management():
    """Test capital reservation system."""
    print_header("TEST: Capital Management")

    from tests.test_arb_integration_e2e import MockExchange
    from src.oms import CapitalManager

    kalshi = MockExchange("kalshi", initial_balance=10000.0)
    poly = MockExchange("polymarket", initial_balance=5000.0)

    capital_mgr = CapitalManager(safety_margin=0.05)
    capital_mgr.sync_from_exchange(kalshi)
    capital_mgr.sync_from_exchange(poly)

    print("Initial state:")
    summary = capital_mgr.get_summary()
    print_result("Total balance", f"${summary['total_balance']:.2f}")
    print_result("Total available", f"${summary['total_available']:.2f}")
    print_result("Safety margin", "5%")

    # Test reservation
    print("\nReserving $2000 on Kalshi...")
    reserved = capital_mgr.reserve(
        reservation_id="test_spread_1",
        exchange="kalshi",
        amount=2000.0,
        purpose="Test spread leg 1",
        ttl_seconds=60,
    )
    print_result("Reserved", reserved)
    print_result("Kalshi available now", f"${capital_mgr.get_available_capital('kalshi'):.2f}")

    # Try to over-reserve
    print("\nTrying to reserve $8000 more on Kalshi (should fail)...")
    over_reserved = capital_mgr.reserve(
        reservation_id="too_big",
        exchange="kalshi",
        amount=8000.0,
        purpose="Too big",
    )
    print_result("Reserved", over_reserved)

    # Release
    print("\nReleasing reservation...")
    released = capital_mgr.release("test_spread_1")
    print_result("Released", f"${released:.2f}")
    print_result("Kalshi available now", f"${capital_mgr.get_available_capital('kalshi'):.2f}")

    if reserved and not over_reserved and released == 2000.0:
        print("\nSUCCESS: Capital management working correctly")
        return True
    else:
        print("\nWARNING: Unexpected behavior")
        return False


def run_all_tests():
    """Run all tests interactively."""
    print_header("ARBITRAGE ALGORITHM TEST SUITE")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    tests = [
        ("Opportunity Detection", test_detection),
        ("Capital Management", test_capital_management),
        ("Full Execution", test_execution),
        ("Failure Handling", test_failure_handling),
    ]

    results = []

    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success, None))
        except Exception as e:
            results.append((name, False, str(e)))

    # Summary
    print_header("TEST SUMMARY")

    passed = sum(1 for _, success, _ in results if success)
    total = len(results)

    for name, success, error in results:
        status = "PASS" if success else "FAIL"
        print(f"  [{status}] {name}")
        if error:
            print(f"         Error: {error}")

    print(f"\nResult: {passed}/{total} tests passed")

    return passed == total


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        # Interactive mode
        run_all_tests()
    else:
        command = sys.argv[1].lower()

        if command == "detect":
            test_detection()
        elif command == "execute":
            test_execution()
        elif command == "failure":
            test_failure_handling()
        elif command == "live":
            test_live_detection()
        elif command == "capital":
            test_capital_management()
        elif command == "all":
            run_all_tests()
        else:
            print(__doc__)
            sys.exit(1)


if __name__ == "__main__":
    main()
