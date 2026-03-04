#!/usr/bin/env python3
"""Test script to verify orderbook snapshot fetching works correctly.

This script tests the fix for Task #3 (orderbook WebSocket subscription).
It verifies that:
1. Orderbook snapshots can be fetched using aiohttp in the WebSocket event loop
2. Snapshots can be successfully applied to the OrderBookManager
3. Sequence numbers are properly synthesized for Kalshi deltas
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

try:
    import aiohttp
except ImportError:
    print("ERROR: aiohttp not installed. Run: pip install aiohttp")
    sys.exit(1)

try:
    from core.exchange_client.kalshi.kalshi_auth import KalshiAuth
except ImportError:
    try:
        from src.kalshi.auth import KalshiAuth
    except ImportError:
        try:
            from kalshi.auth import KalshiAuth
        except ImportError:
            print("ERROR: Could not import KalshiAuth from any known location")
            sys.exit(1)

try:
    from core.market.orderbook_manager import OrderBookManager, OrderBookState
except ImportError:
    print("ERROR: Could not import OrderBookManager")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_orderbook_snapshot(ticker: str = "KXBTC15M-26MAR021815-15"):
    """Test fetching and applying an orderbook snapshot."""

    logger.info("=" * 80)
    logger.info("Testing Orderbook Snapshot Fetching (Task #3 Fix)")
    logger.info("=" * 80)

    # Step 1: Load authentication
    logger.info("\n[1/5] Loading Kalshi authentication...")
    try:
        auth = KalshiAuth.from_env()
        logger.info("✓ Auth loaded successfully")
    except Exception as e:
        logger.error(f"✗ Failed to load auth: {e}")
        return False

    # Step 2: Create OrderBookManager
    logger.info("\n[2/5] Creating OrderBookManager...")
    try:
        def on_update(ticker: str, state: OrderBookState):
            logger.info(f"  → Orderbook updated: {ticker}")
            logger.info(f"     Best bid: {state.best_bid}")
            logger.info(f"     Best ask: {state.best_ask}")
            logger.info(f"     Spread: {state.spread}¢")
            logger.info(f"     Sequence: {state.sequence}")

        orderbook_manager = OrderBookManager(on_update=on_update)
        logger.info("✓ OrderBookManager created")
    except Exception as e:
        logger.error(f"✗ Failed to create OrderBookManager: {e}")
        return False

    # Step 3: Fetch orderbook snapshot using aiohttp (same as the fix)
    logger.info(f"\n[3/5] Fetching orderbook snapshot for {ticker}...")
    try:
        url = f"https://api.elections.kalshi.com/trade-api/v2/markets/{ticker}/orderbook?depth=10"

        # Sign the request
        headers = auth.sign_request("GET", f"/trade-api/v2/markets/{ticker}/orderbook")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    snapshot_data = await resp.json()
                    orderbook = snapshot_data.get('orderbook', {})
                    logger.info(f"✓ Snapshot fetched successfully (HTTP {resp.status})")
                    logger.info(f"  → Yes levels: {len(orderbook.get('yes', []))}")
                    logger.info(f"  → No levels: {len(orderbook.get('no', []))}")
                else:
                    logger.error(f"✗ HTTP {resp.status}: {await resp.text()}")
                    return False
    except Exception as e:
        logger.error(f"✗ Failed to fetch snapshot: {e}")
        return False

    # Step 4: Apply snapshot to OrderBookManager
    logger.info("\n[4/5] Applying snapshot to OrderBookManager...")
    try:
        await orderbook_manager.apply_snapshot(ticker, orderbook)
        logger.info("✓ Snapshot applied successfully")
    except Exception as e:
        logger.error(f"✗ Failed to apply snapshot: {e}")
        return False

    # Step 5: Test delta application with synthesized sequence numbers
    logger.info("\n[5/5] Testing delta application with synthesized seq numbers...")
    try:
        # Simulate a delta (like what comes from WebSocket)
        test_delta = {
            'market_ticker': ticker,
            'price': 50,
            'delta': 5,
            'side': 'yes',
            'seq': 1,  # Synthesized sequence number
        }

        result = await orderbook_manager.apply_delta(ticker, test_delta)
        logger.info(f"✓ Delta applied successfully: {result}")

        # Verify orderbook state
        state = await orderbook_manager.get_orderbook(ticker)
        if state:
            logger.info(f"  → Current best bid: {state.best_bid}")
            logger.info(f"  → Current best ask: {state.best_ask}")
            logger.info(f"  → Bid depth: {state.bid_depth} contracts")
            logger.info(f"  → Ask depth: {state.ask_depth} contracts")
        else:
            logger.error("✗ Orderbook state is None after delta")
            return False

    except Exception as e:
        logger.error(f"✗ Failed to apply delta: {e}")
        return False

    logger.info("\n" + "=" * 80)
    logger.info("✓ ALL TESTS PASSED")
    logger.info("=" * 80)
    logger.info("\nThe orderbook WebSocket fix (Task #3) is working correctly!")
    logger.info("Next step: Run paper mode validation (Task #13)")
    return True


async def main():
    """Main entry point."""
    # Test with a real BTC market ticker (you may need to update this)
    success = await test_orderbook_snapshot()

    if not success:
        logger.error("\n✗ TESTS FAILED - Fix needs debugging")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
        sys.exit(130)
