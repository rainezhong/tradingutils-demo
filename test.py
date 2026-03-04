"""Kalshi API Test Suite - Tests core functionality on demo account.

Tests:
1. Balance retrieval
2. Market discovery
3. Order placement (buy YES, buy NO)
4. Order cancellation
5. Latency measurements
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from core.exchange_client.kalshi import KalshiExchangeClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class LatencyResult:
    """Latency measurement for an API call."""

    operation: str
    latency_ms: float
    success: bool
    error: Optional[str] = None


class KalshiTestSuite:
    """Test suite for Kalshi API functionality."""

    def __init__(self, client: KalshiExchangeClient):
        self.client = client
        self.latencies: List[LatencyResult] = []

    def _measure(self, operation: str):
        """Context manager for measuring latency."""

        class LatencyMeasurer:
            def __init__(inner_self):
                inner_self.start = None
                inner_self.operation = operation
                inner_self.parent = self

            async def __aenter__(inner_self):
                inner_self.start = time.perf_counter()
                return inner_self

            async def __aexit__(inner_self, exc_type, exc_val, exc_tb):
                elapsed_ms = (time.perf_counter() - inner_self.start) * 1000
                result = LatencyResult(
                    operation=inner_self.operation,
                    latency_ms=elapsed_ms,
                    success=exc_type is None,
                    error=str(exc_val) if exc_val else None,
                )
                inner_self.parent.latencies.append(result)
                return False

        return LatencyMeasurer()

    async def test_balance(self) -> bool:
        """Test: Get account balance."""
        logger.info("Testing: Get Balance")
        async with self._measure("get_balance"):
            balance = await self.client.get_balance()
            logger.info(f"  Balance: ${balance.balance:.2f}")
            logger.info(f"  Available: ${balance.available:.2f}")
        return True

    async def test_markets(self) -> Optional[str]:
        """Test: Fetch open markets, return a ticker for trading tests."""
        logger.info("Testing: Get Markets")
        async with self._measure("get_markets"):
            markets = await self.client.get_markets(status="open", limit=10)

        if not markets:
            logger.error("  No open markets found!")
            return None

        # Find a market suitable for testing (prefer quick settle or active ones)
        # For demo, just pick the first one
        ticker = markets[0].ticker
        logger.info(f"  Found {len(markets)} markets")
        logger.info(f"  Selected: {ticker}")
        return ticker

    async def test_buy_yes(self, ticker: str) -> Optional[str]:
        """Test: Place a YES order."""
        logger.info(f"Testing: Buy YES @ $0.99 on {ticker}")
        async with self._measure("create_order_yes"):
            response = await self.client.create_order(
                ticker=ticker,
                action="buy",
                side="yes",
                type="limit",
                yes_price=99,
                count=1,
            )

        order_id = response.order_id
        logger.info(f"  Order ID: {order_id}")
        logger.info(f"  Status: {response.status}")
        return order_id

    async def test_buy_no(self, ticker: str) -> Optional[str]:
        """Test: Place a NO order."""
        logger.info(f"Testing: Buy NO @ $0.99 on {ticker}")
        async with self._measure("create_order_no"):
            response = await self.client.create_order(
                ticker=ticker,
                action="buy",
                side="no",
                type="limit",
                no_price=99,
                count=1,
            )

        order_id = response.order_id
        logger.info(f"  Order ID: {order_id}")
        logger.info(f"  Status: {response.status}")
        return order_id

    async def test_cancel_order(self, order_id: str) -> bool:
        """Test: Cancel an order."""
        logger.info(f"Testing: Cancel Order {order_id[:8]}...")
        try:
            async with self._measure("cancel_order"):
                await self.client.cancel_order(order_id=order_id)
            logger.info("  Cancelled successfully")
            return True
        except Exception as e:
            # Order may have already filled or market closed
            if "404" in str(e) or "not found" in str(e).lower():
                logger.info("  Order already filled/closed (expected for quick-settle)")
                return True
            raise

    async def test_get_orders(self) -> bool:
        """Test: Get open orders."""
        logger.info("Testing: Get Orders")
        async with self._measure("get_orders"):
            orders = await self.client.get_orders(status="resting")
        logger.info(f"  Open orders: {len(orders)}")
        return True

    def print_latency_report(self):
        """Print latency summary."""
        logger.info("")
        logger.info("=" * 60)
        logger.info("LATENCY REPORT")
        logger.info("=" * 60)

        for result in self.latencies:
            status = "✓" if result.success else "✗"
            logger.info(f"  {status} {result.operation}: {result.latency_ms:.1f}ms")

        successful = [r for r in self.latencies if r.success]
        if successful:
            values = sorted([r.latency_ms for r in successful])
            avg_ms = sum(values) / len(values)
            min_ms = values[0]
            max_ms = values[-1]
            p50 = values[int(len(values) * 0.5)]
            p90 = values[int(len(values) * 0.9)]
            p99 = values[int(len(values) * 0.99)]

            import math

            variance = (
                sum((x - avg_ms) ** 2 for x in values) / (len(values) - 1)
                if len(values) > 1
                else 0
            )
            stdev = math.sqrt(variance)

            logger.info("")
            logger.info(f"  Count: {len(values)}")
            logger.info(f"  Avg: {avg_ms:.1f}ms | StDev: {stdev:.1f}ms")
            logger.info(f"  Min: {min_ms:.1f}ms | Max: {max_ms:.1f}ms")
            logger.info(f"  p50: {p50:.1f}ms | p90: {p90:.1f}ms | p99: {p99:.1f}ms")

            # By Operation Analysis
            ops = set(r.operation for r in successful)
            logger.info("")
            logger.info("  By Operation:")
            logger.info(
                f"    {'Operation':<20} {'Avg':<8} {'StDev':<8} {'p90':<8} {'Count':<6}"
            )
            logger.info(f"    {'-' * 60}")

            for op in sorted(ops):
                op_vals = sorted(
                    [r.latency_ms for r in successful if r.operation == op]
                )
                if op_vals:
                    op_avg = sum(op_vals) / len(op_vals)
                    op_var = (
                        sum((x - op_avg) ** 2 for x in op_vals) / (len(op_vals) - 1)
                        if len(op_vals) > 1
                        else 0
                    )
                    op_stdev = math.sqrt(op_var)
                    op_p90 = op_vals[int(len(op_vals) * 0.9)]

                    logger.info(
                        f"    {op:<20} {op_avg:.1f}ms   {op_stdev:.1f}ms   {op_p90:.1f}ms   {len(op_vals):<6}"
                    )

        logger.info("=" * 60)


async def main():
    """Run latency distribution test."""
    logger.info("=" * 60)
    logger.info("KALSHI LATENCY DISTRIBUTION TEST (N=50)")
    logger.info("=" * 60)

    try:
        async with KalshiExchangeClient.from_env(demo=True) as client:
            suite = KalshiTestSuite(client)

            # multiple iterations
            iterations = 50

            logger.info(f"Running {iterations} iterations...")

            # Initial setup
            ticker = await suite.test_markets()
            if not ticker:
                return

            for i in range(iterations):
                if i % 10 == 0:
                    logger.info(f"Iteration {i + 1}/{iterations}...")

                # Alternate order: sometimes YES first, sometimes NO first to remove bias
                if i % 2 == 0:
                    o1 = await suite.test_buy_yes(ticker)
                    o2 = await suite.test_buy_no(ticker)
                else:
                    o2 = await suite.test_buy_no(ticker)
                    o1 = await suite.test_buy_yes(ticker)

                # Cleanup
                if o1:
                    await suite.test_cancel_order(o1)
                if o2:
                    await suite.test_cancel_order(o2)

                # periodic balance check
                if i % 10 == 0:
                    await suite.test_balance()

                await asyncio.sleep(0.2)  # small delay

            suite.print_latency_report()

    except Exception as e:
        logger.error(f"Test failed: {e}")
        raise

    logger.info("")
    logger.info("All tests complete!")


if __name__ == "__main__":
    asyncio.run(main())
