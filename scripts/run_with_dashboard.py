#!/usr/bin/env python3
"""
Run trading algorithms with the live dashboard.

Usage:
    python scripts/run_with_dashboard.py

This starts:
1. Dashboard web server on http://localhost:8080
2. SpreadDetector scanning for opportunities (with mock data)

Open http://localhost:8080 in your browser to see live updates.
Press Ctrl+C to stop.
"""

import sys
import os
import threading
import time
import signal

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    print("=" * 60)
    print("  Trading Dashboard + Live Algorithms")
    print("=" * 60)
    print()

    # 1. Start dashboard server in background thread
    print("[1/2] Starting dashboard server...")

    import uvicorn
    from dashboard.app import create_app

    app = create_app()

    def run_server():
        uvicorn.run(app, host="127.0.0.1", port=8080, log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(2)

    print("      Dashboard running at http://127.0.0.1:8080")
    print()

    # 2. Set up spread detector with mock data
    print("[2/2] Starting spread detector...")

    from arb.spread_detector import SpreadDetector
    from tests.test_arb_integration_e2e import MockMarketMatcher

    # Create mock matcher with realistic opportunities
    matcher = MockMarketMatcher()

    # BTC market
    matcher.add_pair(
        pair_id="btc_100k",
        kalshi_ticker="BTC-100K-YES",
        poly_token_id="poly_btc_100k",
        event_description="Will BTC exceed $100,000 by March 2026?",
    )
    matcher.set_quotes(
        pair_id="btc_100k",
        kalshi_yes_bid=0.40,
        kalshi_yes_ask=0.42,
        poly_yes_bid=0.50,
        poly_yes_ask=0.52,
        size=200,
    )

    # ETH market
    matcher.add_pair(
        pair_id="eth_5k",
        kalshi_ticker="ETH-5K-YES",
        poly_token_id="poly_eth_5k",
        event_description="Will ETH exceed $5,000 by June 2026?",
    )
    matcher.set_quotes(
        pair_id="eth_5k",
        kalshi_yes_bid=0.30,
        kalshi_yes_ask=0.32,
        poly_yes_bid=0.40,
        poly_yes_ask=0.42,
        size=150,
    )

    # Create detector
    detector = SpreadDetector(
        market_matcher=matcher,
        min_edge_cents=2.0,
        min_liquidity_usd=50.0,
        max_quote_age_ms=60000.0,
        poll_interval_ms=2000,  # Check every 2 seconds
    )

    # Start detector (runs in background thread)
    detector.start()
    print("      Spread detector running (poll interval: 2s)")
    print()

    print("=" * 60)
    print("  Open http://127.0.0.1:8080 in your browser")
    print("  Press Ctrl+C to stop")
    print("=" * 60)

    # Handle Ctrl+C gracefully
    def signal_handler(sig, frame):
        print("\n\nShutting down...")
        detector.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    # Keep main thread alive
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
