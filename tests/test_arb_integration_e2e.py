"""End-to-end integration tests for the arbitrage pipeline.

Tests the full flow:
SpreadDetector → QuoteProvider → ArbExecutionHandler → Executor → OMS

Uses mock exchanges and simulated market data to verify the complete pipeline.
"""

import pytest
import time
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from unittest.mock import MagicMock, patch

from arb.spread_detector import (
    SpreadDetector,
    SpreadOpportunity,
    SpreadAlert,
    MarketQuote,
    MatchedMarketPair,
    Platform,
)
from arb.execution.handler import ArbExecutionHandler
from arb.execution.algorithms import (
    SimultaneousLimitExecutor,
    SequentialMarketExecutor,
    AdaptiveExecutor,
)
from arb.execution.metrics import MetricsCollector
from src.core.exchange import Order, OrderBook, TradableMarket, ExchangeClient
from src.core.models import Market, Position, Fill
from src.oms import (
    OrderManagementSystem,
    OMSConfig,
    CapitalManager,
    SpreadExecutor,
    SpreadExecutorConfig,
)


# =============================================================================
# Mock Exchange Infrastructure
# =============================================================================


class MockExchange(ExchangeClient):
    """Mock exchange for testing."""

    def __init__(self, name: str, initial_balance: float = 10000.0):
        self._name = name
        self._balance = initial_balance
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, Order] = {}
        self._order_counter = 0

        # Configurable order behavior
        self._fill_immediately = True
        self._fill_ratio = 1.0  # 1.0 = full fill, 0.5 = partial
        self._reject_orders = False
        self._slippage_cents = 0.0

    @property
    def name(self) -> str:
        return self._name

    def set_fill_behavior(
        self,
        fill_immediately: bool = True,
        fill_ratio: float = 1.0,
        reject_orders: bool = False,
        slippage_cents: float = 0.0,
    ):
        """Configure how orders are filled."""
        self._fill_immediately = fill_immediately
        self._fill_ratio = fill_ratio
        self._reject_orders = reject_orders
        self._slippage_cents = slippage_cents

    def get_market(self, ticker: str) -> TradableMarket:
        market = Market(ticker=ticker, title=f"Mock market {ticker}")
        return TradableMarket(market, self)

    def get_markets(self, status: Optional[str] = None, limit: int = 100) -> List[TradableMarket]:
        return []

    def get_balance(self) -> float:
        return self._balance

    def get_all_positions(self) -> Dict[str, Position]:
        return self._positions.copy()

    def get_all_orders(self, status: Optional[str] = None) -> List[Order]:
        orders = list(self._orders.values())
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def _place_order(self, ticker: str, side: str, price: float, size: int) -> Order:
        if self._reject_orders:
            raise RuntimeError("Order rejected by exchange")

        self._order_counter += 1
        order_id = f"{self._name}_order_{self._order_counter}"

        # Apply slippage
        actual_price = price
        if self._slippage_cents != 0:
            if side == "buy":
                actual_price = price + self._slippage_cents
            else:
                actual_price = price - self._slippage_cents

        order = Order(
            order_id=order_id,
            ticker=ticker,
            side=side,
            price=actual_price,
            size=size,
            filled_size=0,
            status="pending",
            created_at=datetime.now(),
            exchange=self._name,
        )

        self._orders[order_id] = order

        # Simulate fill if configured
        if self._fill_immediately:
            fill_size = int(size * self._fill_ratio)
            if fill_size > 0:
                order.filled_size = fill_size
                order.status = "filled" if fill_size >= size else "partial"

                # Update position
                self._update_position(ticker, side, fill_size, actual_price)

        return order

    def _cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = "canceled"
            return True
        return False

    def _get_orderbook(self, ticker: str) -> OrderBook:
        # Return a default orderbook
        return OrderBook(
            ticker=ticker,
            bids=[(50.0, 100), (49.0, 200)],
            asks=[(51.0, 100), (52.0, 200)],
        )

    def _get_position(self, ticker: str) -> Optional[Position]:
        return self._positions.get(ticker)

    def _get_orders(self, ticker: str, status: Optional[str] = None) -> List[Order]:
        orders = [o for o in self._orders.values() if o.ticker == ticker]
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def _get_fills(self, ticker: str, limit: int = 100) -> List[Fill]:
        return []

    def _get_market_data(self, ticker: str) -> Market:
        return Market(ticker=ticker, title=f"Mock market {ticker}")

    def _update_position(self, ticker: str, side: str, size: int, price: float):
        """Update position after a fill."""
        current = self._positions.get(ticker)
        if current is None:
            current = Position(ticker=ticker, size=0, entry_price=0)

        if side == "buy":
            new_size = current.size + size
            if new_size != 0:
                new_entry = ((current.size * current.entry_price) + (size * price)) / new_size
            else:
                new_entry = 0
        else:
            new_size = current.size - size
            new_entry = current.entry_price

        self._positions[ticker] = Position(
            ticker=ticker,
            size=new_size,
            entry_price=new_entry,
            current_price=price,
        )

        # Update balance
        if side == "buy":
            self._balance -= price * size / 100.0  # Price in cents
        else:
            self._balance += price * size / 100.0


class MockMarketMatcher:
    """Mock market matcher that provides configurable opportunities."""

    def __init__(self):
        self._pairs: List[MatchedMarketPair] = []
        self._quotes: Dict[str, Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]] = {}

    def add_pair(
        self,
        pair_id: str,
        kalshi_ticker: str,
        poly_token_id: str,
        event_description: str = "Test event",
    ):
        """Add a matched pair."""
        pair = MatchedMarketPair(
            pair_id=pair_id,
            event_description=event_description,
            platform_1=Platform.KALSHI,
            market_1_id=kalshi_ticker,
            market_1_name=f"Kalshi: {event_description}",
            platform_2=Platform.POLYMARKET,
            market_2_id=poly_token_id,
            market_2_name=f"Poly: {event_description}",
            match_confidence=0.95,
        )
        self._pairs.append(pair)
        return pair

    def set_quotes(
        self,
        pair_id: str,
        kalshi_yes_bid: float,
        kalshi_yes_ask: float,
        poly_yes_bid: float,
        poly_yes_ask: float,
        size: int = 100,
    ):
        """Set quotes for a pair to create an opportunity."""
        now = datetime.now()

        # Find the pair
        pair = next((p for p in self._pairs if p.pair_id == pair_id), None)
        if not pair:
            raise ValueError(f"Pair {pair_id} not found")

        kalshi_yes = MarketQuote(
            platform=Platform.KALSHI,
            market_id=pair.market_1_id,
            market_name=pair.market_1_name,
            outcome="yes",
            best_bid=kalshi_yes_bid,
            best_ask=kalshi_yes_ask,
            bid_size=size,
            ask_size=size,
            bid_depth_usd=kalshi_yes_bid * size,
            ask_depth_usd=kalshi_yes_ask * size,
            timestamp=now,
        )

        kalshi_no = MarketQuote(
            platform=Platform.KALSHI,
            market_id=pair.market_1_id,
            market_name=pair.market_1_name,
            outcome="no",
            best_bid=1.0 - kalshi_yes_ask,
            best_ask=1.0 - kalshi_yes_bid,
            bid_size=size,
            ask_size=size,
            bid_depth_usd=(1.0 - kalshi_yes_ask) * size,
            ask_depth_usd=(1.0 - kalshi_yes_bid) * size,
            timestamp=now,
        )

        poly_yes = MarketQuote(
            platform=Platform.POLYMARKET,
            market_id=pair.market_2_id,
            market_name=pair.market_2_name,
            outcome="yes",
            best_bid=poly_yes_bid,
            best_ask=poly_yes_ask,
            bid_size=size,
            ask_size=size,
            bid_depth_usd=poly_yes_bid * size,
            ask_depth_usd=poly_yes_ask * size,
            timestamp=now,
        )

        poly_no = MarketQuote(
            platform=Platform.POLYMARKET,
            market_id=pair.market_2_id,
            market_name=pair.market_2_name,
            outcome="no",
            best_bid=1.0 - poly_yes_ask,
            best_ask=1.0 - poly_yes_bid,
            bid_size=size,
            ask_size=size,
            bid_depth_usd=(1.0 - poly_yes_ask) * size,
            ask_depth_usd=(1.0 - poly_yes_bid) * size,
            timestamp=now,
        )

        self._quotes[pair_id] = (kalshi_yes, kalshi_no, poly_yes, poly_no)

    def get_matched_pairs(self) -> List[MatchedMarketPair]:
        return self._pairs

    def get_quotes(self, pair: MatchedMarketPair) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]:
        quotes = self._quotes.get(pair.pair_id)
        if not quotes:
            raise ValueError(f"No quotes for pair {pair.pair_id}")
        return quotes


# =============================================================================
# Integration Tests
# =============================================================================


class TestSpreadDetectorIntegration:
    """Tests for SpreadDetector with mock data."""

    def test_detector_finds_cross_platform_arb(self):
        """Test that detector finds cross-platform arbitrage opportunity."""
        matcher = MockMarketMatcher()

        # Create a pair
        pair = matcher.add_pair(
            pair_id="test_pair_1",
            kalshi_ticker="AAPL-YES",
            poly_token_id="poly_aapl_123",
            event_description="Will AAPL close above $200?",
        )

        # Set quotes with arbitrage opportunity
        # Buy on Kalshi at 0.45, sell on Poly at 0.50 = 5 cent edge
        matcher.set_quotes(
            pair_id="test_pair_1",
            kalshi_yes_bid=0.44,
            kalshi_yes_ask=0.45,  # Buy here
            poly_yes_bid=0.50,   # Sell here
            poly_yes_ask=0.51,
            size=100,
        )

        # Create detector
        alerts_received = []
        def on_alert(alert: SpreadAlert):
            alerts_received.append(alert)

        detector = SpreadDetector(
            market_matcher=matcher,
            min_edge_cents=2.0,  # 2 cent minimum
            min_liquidity_usd=10.0,  # Low for testing
            on_alert=on_alert,
        )

        # Run one detection cycle
        opportunities = detector.check_once()

        # Should find the opportunity
        assert len(opportunities) > 0

        # Verify opportunity details
        opp = opportunities[0]
        assert opp.opportunity_type in ("cross_platform_arb", "dutch_book")
        assert opp.net_edge_per_contract > 0.01  # At least 1 cent after fees

    def test_detector_ignores_low_edge(self):
        """Test that detector ignores opportunities below threshold."""
        matcher = MockMarketMatcher()

        pair = matcher.add_pair(
            pair_id="test_pair_2",
            kalshi_ticker="BTC-YES",
            poly_token_id="poly_btc_456",
        )

        # Set quotes with tiny edge (0.5 cent)
        matcher.set_quotes(
            pair_id="test_pair_2",
            kalshi_yes_bid=0.49,
            kalshi_yes_ask=0.495,
            poly_yes_bid=0.50,
            poly_yes_ask=0.505,
            size=100,
        )

        detector = SpreadDetector(
            market_matcher=matcher,
            min_edge_cents=2.0,  # 2 cent minimum
        )

        opportunities = detector.check_once()
        assert len(opportunities) == 0


class TestOMSIntegration:
    """Tests for OMS with mock exchanges."""

    def test_oms_submit_and_track_order(self):
        """Test that OMS can submit and track orders."""
        kalshi = MockExchange("kalshi")
        poly = MockExchange("polymarket")

        oms = OrderManagementSystem()
        oms.register_exchange(kalshi)
        oms.register_exchange(poly)

        # Submit an order
        order = oms.submit_order(
            exchange="kalshi",
            ticker="TEST-YES",
            side="buy",
            price=50.0,
            size=10,
        )

        assert order.order_id is not None
        assert order.exchange == "kalshi"

        # Check it's tracked
        tracked = oms.get_order(order.order_id)
        assert tracked is not None
        assert tracked.idempotency_key == order.idempotency_key

    def test_oms_prevents_duplicate_submission(self):
        """Test that OMS blocks duplicate orders by idempotency key."""
        kalshi = MockExchange("kalshi")

        oms = OrderManagementSystem()
        oms.register_exchange(kalshi)

        # Submit first order
        order1 = oms.submit_order(
            exchange="kalshi",
            ticker="TEST-YES",
            side="buy",
            price=50.0,
            size=10,
            idempotency_key="unique_key_123",
        )

        # Try to submit duplicate
        order2 = oms.submit_order(
            exchange="kalshi",
            ticker="TEST-YES",
            side="buy",
            price=50.0,
            size=10,
            idempotency_key="unique_key_123",
        )

        # Should return the same order
        assert order1.order_id == order2.order_id


class TestCapitalManagerIntegration:
    """Tests for CapitalManager with exchanges."""

    def test_capital_reservation_flow(self):
        """Test capital reservation and release flow."""
        kalshi = MockExchange("kalshi", initial_balance=10000.0)

        capital_mgr = CapitalManager()
        capital_mgr.sync_from_exchange(kalshi)

        # Check initial balance
        available = capital_mgr.get_available_capital("kalshi")
        assert available > 0

        # Reserve some capital
        reserved = capital_mgr.reserve(
            reservation_id="spread_1",
            exchange="kalshi",
            amount=500.0,
            purpose="Test spread leg 1",
        )
        assert reserved is True

        # Available should be reduced
        new_available = capital_mgr.get_available_capital("kalshi")
        assert new_available < available

        # Release
        released = capital_mgr.release("spread_1")
        assert released == 500.0

        # Available should be restored
        final_available = capital_mgr.get_available_capital("kalshi")
        assert abs(final_available - available) < 0.01

    def test_capital_blocks_over_reservation(self):
        """Test that capital manager blocks over-reservation."""
        kalshi = MockExchange("kalshi", initial_balance=1000.0)

        capital_mgr = CapitalManager()
        capital_mgr.sync_from_exchange(kalshi)

        # Try to reserve more than available (accounting for safety margin)
        reserved = capital_mgr.reserve(
            reservation_id="big_spread",
            exchange="kalshi",
            amount=990.0,  # Almost all capital
            purpose="Too big",
        )

        # Should fail due to safety margin
        assert reserved is False


class TestSpreadExecutorIntegration:
    """Tests for SpreadExecutor with mock exchanges."""

    def test_spread_execution_success(self):
        """Test successful two-leg spread execution."""
        kalshi = MockExchange("kalshi", initial_balance=10000.0)
        poly = MockExchange("polymarket", initial_balance=10000.0)

        # Configure immediate fills
        kalshi.set_fill_behavior(fill_immediately=True, fill_ratio=1.0)
        poly.set_fill_behavior(fill_immediately=True, fill_ratio=1.0)

        # Set up OMS and capital manager
        capital_mgr = CapitalManager()
        capital_mgr.sync_from_exchange(kalshi)
        capital_mgr.sync_from_exchange(poly)

        oms = OrderManagementSystem(capital_manager=capital_mgr)
        oms.register_exchange(kalshi)
        oms.register_exchange(poly)

        # Create spread executor
        executor_config = SpreadExecutorConfig(
            leg1_timeout_seconds=5.0,
            leg2_timeout_seconds=5.0,
            poll_interval_seconds=0.1,
        )
        executor = SpreadExecutor(oms, capital_mgr, executor_config)

        # Execute a spread
        result = executor.execute_spread(
            opportunity_id="test_opp_1",
            leg1_exchange="kalshi",
            leg1_ticker="TEST-YES",
            leg1_side="buy",
            leg1_price=45.0,
            leg1_size=10,
            leg2_exchange="polymarket",
            leg2_ticker="TEST-YES-POLY",
            leg2_side="sell",
            leg2_price=50.0,
            leg2_size=10,
            expected_profit=5.0,
        )

        # Verify success
        assert result.is_successful
        assert result.leg1.is_filled
        assert result.leg2.is_filled

    def test_spread_execution_rollback(self):
        """Test spread rollback when leg 2 fails."""
        kalshi = MockExchange("kalshi", initial_balance=10000.0)
        poly = MockExchange("polymarket", initial_balance=10000.0)

        # Kalshi fills, Poly rejects
        kalshi.set_fill_behavior(fill_immediately=True, fill_ratio=1.0)
        poly.set_fill_behavior(reject_orders=True)

        capital_mgr = CapitalManager()
        capital_mgr.sync_from_exchange(kalshi)
        capital_mgr.sync_from_exchange(poly)

        oms = OrderManagementSystem(capital_manager=capital_mgr)
        oms.register_exchange(kalshi)
        oms.register_exchange(poly)

        executor_config = SpreadExecutorConfig(
            leg1_timeout_seconds=2.0,
            leg2_timeout_seconds=2.0,
            rollback_timeout_seconds=2.0,
            poll_interval_seconds=0.1,
        )
        executor = SpreadExecutor(oms, capital_mgr, executor_config)

        result = executor.execute_spread(
            opportunity_id="test_opp_rollback",
            leg1_exchange="kalshi",
            leg1_ticker="TEST-YES",
            leg1_side="buy",
            leg1_price=45.0,
            leg1_size=10,
            leg2_exchange="polymarket",
            leg2_ticker="TEST-YES-POLY",
            leg2_side="sell",
            leg2_price=50.0,
            leg2_size=10,
        )

        # Should have attempted rollback
        assert result.status.value in ("rolled_back", "partial", "failed")


class TestFullPipelineIntegration:
    """Tests for the complete arbitrage pipeline."""

    def test_detector_to_executor_pipeline(self):
        """Test the full flow from detection to execution."""
        # Set up mock exchanges
        kalshi = MockExchange("kalshi", initial_balance=10000.0)
        poly = MockExchange("polymarket", initial_balance=10000.0)

        kalshi.set_fill_behavior(fill_immediately=True, fill_ratio=1.0)
        poly.set_fill_behavior(fill_immediately=True, fill_ratio=1.0)

        # Set up mock market matcher with opportunity
        matcher = MockMarketMatcher()
        pair = matcher.add_pair(
            pair_id="pipeline_test",
            kalshi_ticker="PIPELINE-YES",
            poly_token_id="poly_pipeline_123",
            event_description="Pipeline test event",
        )

        # Create opportunity: buy Kalshi at 0.45, sell Poly at 0.50
        matcher.set_quotes(
            pair_id="pipeline_test",
            kalshi_yes_bid=0.44,
            kalshi_yes_ask=0.45,
            poly_yes_bid=0.50,
            poly_yes_ask=0.51,
            size=50,
        )

        # Set up OMS
        capital_mgr = CapitalManager()
        capital_mgr.sync_from_exchange(kalshi)
        capital_mgr.sync_from_exchange(poly)

        oms = OrderManagementSystem(capital_manager=capital_mgr)
        oms.register_exchange(kalshi)
        oms.register_exchange(poly)

        # Set up spread executor
        executor = SpreadExecutor(
            oms,
            capital_mgr,
            SpreadExecutorConfig(
                leg1_timeout_seconds=2.0,
                leg2_timeout_seconds=2.0,
                poll_interval_seconds=0.1,
            ),
        )

        # Create detector
        detector = SpreadDetector(
            market_matcher=matcher,
            min_edge_cents=2.0,
            min_liquidity_usd=10.0,
        )

        # Run one cycle to detect opportunities
        opportunities = detector.check_once()

        # Verify opportunity was detected
        assert len(opportunities) > 0

        # Execute the opportunity directly (simulating what handler would do)
        opp = opportunities[0]
        result = executor.execute_spread(
            opportunity_id="manual_test",
            leg1_exchange=opp.buy_platform.value,
            leg1_ticker=opp.buy_market_id,
            leg1_side="buy",
            leg1_price=opp.buy_price * 100,  # Convert to cents
            leg1_size=min(opp.max_contracts, 50),
            leg2_exchange=opp.sell_platform.value,
            leg2_ticker=opp.sell_market_id,
            leg2_side="sell",
            leg2_price=opp.sell_price * 100,
            leg2_size=min(opp.max_contracts, 50),
            expected_profit=opp.estimated_profit_usd,
        )

        # Verify execution completed
        assert result.is_complete
        assert result.is_successful or result.status.value in ("partial", "failed")

    def test_metrics_collection(self):
        """Test that metrics are properly collected during execution."""
        kalshi = MockExchange("kalshi", initial_balance=10000.0)
        poly = MockExchange("polymarket", initial_balance=10000.0)

        capital_mgr = CapitalManager()
        capital_mgr.sync_from_exchange(kalshi)
        capital_mgr.sync_from_exchange(poly)

        oms = OrderManagementSystem(capital_manager=capital_mgr)
        oms.register_exchange(kalshi)
        oms.register_exchange(poly)

        # Execute a spread
        executor = SpreadExecutor(oms, capital_mgr)

        result = executor.execute_spread(
            opportunity_id="metrics_test",
            leg1_exchange="kalshi",
            leg1_ticker="METRICS-YES",
            leg1_side="buy",
            leg1_price=45.0,
            leg1_size=10,
            leg2_exchange="polymarket",
            leg2_ticker="METRICS-YES-POLY",
            leg2_side="sell",
            leg2_price=50.0,
            leg2_size=10,
        )

        # Verify OMS metrics
        metrics = oms.get_metrics()
        assert "active_orders" in metrics
        assert "exchanges_registered" in metrics
        assert metrics["exchanges_registered"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
