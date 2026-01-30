"""Tests for the exchange-agnostic trading interface.

This module tests the ExchangeClient ABC, TradableMarket, Order, and OrderBook classes.
"""

import pytest
from datetime import datetime
from typing import Dict, List, Optional
from unittest.mock import MagicMock, patch

from src.core.exchange import ExchangeClient, Order, OrderBook, TradableMarket
from src.core.models import Fill, Market, Position


class TestOrder:
    """Tests for the Order dataclass."""

    def test_order_creation_buy(self):
        """Test creating a buy order."""
        order = Order(
            order_id="test-123",
            ticker="AAPL-24JAN",
            side="buy",
            price=45.0,
            size=10,
        )
        assert order.order_id == "test-123"
        assert order.ticker == "AAPL-24JAN"
        assert order.side == "buy"
        assert order.price == 45.0
        assert order.size == 10
        assert order.filled_size == 0
        assert order.status == "pending"
        assert order.exchange is None

    def test_order_creation_sell(self):
        """Test creating a sell order."""
        order = Order(
            order_id="test-456",
            ticker="BTC-24JAN",
            side="sell",
            price=55.0,
            size=5,
            exchange="kalshi",
        )
        assert order.side == "sell"
        assert order.exchange == "kalshi"

    def test_order_invalid_side(self):
        """Test that invalid side raises error."""
        with pytest.raises(ValueError, match="side must be 'buy' or 'sell'"):
            Order(
                order_id="test",
                ticker="TEST",
                side="invalid",
                price=50.0,
                size=10,
            )

    def test_order_invalid_price(self):
        """Test that negative price raises error."""
        with pytest.raises(ValueError, match="price cannot be negative"):
            Order(
                order_id="test",
                ticker="TEST",
                side="buy",
                price=-10.0,
                size=10,
            )

    def test_order_invalid_size(self):
        """Test that non-positive size raises error."""
        with pytest.raises(ValueError, match="size must be positive"):
            Order(
                order_id="test",
                ticker="TEST",
                side="buy",
                price=50.0,
                size=0,
            )

    def test_order_invalid_filled_size(self):
        """Test that filled_size > size raises error."""
        with pytest.raises(ValueError, match="filled_size.*cannot exceed size"):
            Order(
                order_id="test",
                ticker="TEST",
                side="buy",
                price=50.0,
                size=10,
                filled_size=15,
            )

    def test_order_remaining_size(self):
        """Test remaining_size property."""
        order = Order(
            order_id="test",
            ticker="TEST",
            side="buy",
            price=50.0,
            size=10,
            filled_size=3,
        )
        assert order.remaining_size == 7

    def test_order_is_filled(self):
        """Test is_filled property."""
        order = Order(
            order_id="test",
            ticker="TEST",
            side="buy",
            price=50.0,
            size=10,
            filled_size=10,
        )
        assert order.is_filled is True

        partial_order = Order(
            order_id="test2",
            ticker="TEST",
            side="buy",
            price=50.0,
            size=10,
            filled_size=5,
        )
        assert partial_order.is_filled is False

    def test_order_is_active(self):
        """Test is_active property."""
        pending_order = Order(
            order_id="test",
            ticker="TEST",
            side="buy",
            price=50.0,
            size=10,
            status="pending",
        )
        assert pending_order.is_active is True

        open_order = Order(
            order_id="test",
            ticker="TEST",
            side="buy",
            price=50.0,
            size=10,
            status="open",
        )
        assert open_order.is_active is True

        canceled_order = Order(
            order_id="test",
            ticker="TEST",
            side="buy",
            price=50.0,
            size=10,
            status="canceled",
        )
        assert canceled_order.is_active is False


class TestOrderBook:
    """Tests for the OrderBook dataclass."""

    def test_orderbook_creation(self):
        """Test creating an orderbook."""
        ob = OrderBook(
            ticker="TEST-MARKET",
            bids=[(45.0, 100), (44.0, 200)],
            asks=[(46.0, 150), (47.0, 250)],
        )
        assert ob.ticker == "TEST-MARKET"
        assert len(ob.bids) == 2
        assert len(ob.asks) == 2

    def test_orderbook_best_bid(self):
        """Test best_bid property."""
        ob = OrderBook(
            ticker="TEST",
            bids=[(45.0, 100), (44.0, 200)],
            asks=[(46.0, 150)],
        )
        assert ob.best_bid == 45.0

    def test_orderbook_best_bid_empty(self):
        """Test best_bid with no bids."""
        ob = OrderBook(
            ticker="TEST",
            bids=[],
            asks=[(46.0, 150)],
        )
        assert ob.best_bid is None

    def test_orderbook_best_ask(self):
        """Test best_ask property."""
        ob = OrderBook(
            ticker="TEST",
            bids=[(45.0, 100)],
            asks=[(46.0, 150), (47.0, 250)],
        )
        assert ob.best_ask == 46.0

    def test_orderbook_best_ask_empty(self):
        """Test best_ask with no asks."""
        ob = OrderBook(
            ticker="TEST",
            bids=[(45.0, 100)],
            asks=[],
        )
        assert ob.best_ask is None

    def test_orderbook_spread(self):
        """Test spread property."""
        ob = OrderBook(
            ticker="TEST",
            bids=[(45.0, 100)],
            asks=[(47.0, 150)],
        )
        assert ob.spread == 2.0

    def test_orderbook_spread_none(self):
        """Test spread when missing bid or ask."""
        ob = OrderBook(
            ticker="TEST",
            bids=[],
            asks=[(47.0, 150)],
        )
        assert ob.spread is None

    def test_orderbook_mid_price(self):
        """Test mid_price property."""
        ob = OrderBook(
            ticker="TEST",
            bids=[(45.0, 100)],
            asks=[(47.0, 150)],
        )
        assert ob.mid_price == 46.0

    def test_orderbook_mid_price_none(self):
        """Test mid_price when missing bid or ask."""
        ob = OrderBook(
            ticker="TEST",
            bids=[(45.0, 100)],
            asks=[],
        )
        assert ob.mid_price is None

    def test_orderbook_bid_depth(self):
        """Test bid_depth property."""
        ob = OrderBook(
            ticker="TEST",
            bids=[(45.0, 100), (44.0, 200), (43.0, 50)],
            asks=[],
        )
        assert ob.bid_depth == 350

    def test_orderbook_ask_depth(self):
        """Test ask_depth property."""
        ob = OrderBook(
            ticker="TEST",
            bids=[],
            asks=[(46.0, 150), (47.0, 250)],
        )
        assert ob.ask_depth == 400


class MockExchangeClient(ExchangeClient):
    """Mock implementation of ExchangeClient for testing."""

    def __init__(self):
        self._markets: Dict[str, Market] = {}
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, Order] = {}
        self._orderbooks: Dict[str, OrderBook] = {}
        self._balance = 1000.0
        self._order_counter = 0

    @property
    def name(self) -> str:
        return "mock"

    def get_market(self, ticker: str) -> TradableMarket:
        if ticker not in self._markets:
            self._markets[ticker] = Market(
                ticker=ticker,
                title=f"Test Market {ticker}",
                status="open",
            )
        return TradableMarket(self._markets[ticker], self)

    def get_markets(
        self, status: Optional[str] = None, limit: int = 100
    ) -> List[TradableMarket]:
        markets = list(self._markets.values())
        if status:
            markets = [m for m in markets if m.status == status]
        return [TradableMarket(m, self) for m in markets[:limit]]

    def get_balance(self) -> float:
        return self._balance

    def get_all_positions(self) -> Dict[str, Position]:
        return self._positions.copy()

    def get_all_orders(self, status: Optional[str] = None) -> List[Order]:
        orders = list(self._orders.values())
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def _place_order(
        self, ticker: str, side: str, price: float, size: int
    ) -> Order:
        self._order_counter += 1
        order = Order(
            order_id=f"mock-order-{self._order_counter}",
            ticker=ticker,
            side=side,
            price=price,
            size=size,
            status="open",
            exchange=self.name,
        )
        self._orders[order.order_id] = order
        return order

    def _cancel_order(self, order_id: str) -> bool:
        if order_id in self._orders:
            self._orders[order_id].status = "canceled"
            return True
        return False

    def _get_orderbook(self, ticker: str) -> OrderBook:
        if ticker not in self._orderbooks:
            self._orderbooks[ticker] = OrderBook(
                ticker=ticker,
                bids=[(45.0, 100), (44.0, 200)],
                asks=[(46.0, 150), (47.0, 250)],
            )
        return self._orderbooks[ticker]

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
        if ticker not in self._markets:
            self._markets[ticker] = Market(
                ticker=ticker,
                title=f"Test Market {ticker}",
                status="open",
            )
        return self._markets[ticker]


class TestTradableMarket:
    """Tests for the TradableMarket class."""

    def test_tradable_market_properties(self):
        """Test TradableMarket property delegation."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        assert market.ticker == "TEST-123"
        assert market.title == "Test Market TEST-123"
        assert market.status == "open"
        assert market.exchange == "mock"

    def test_tradable_market_buy(self):
        """Test placing a buy order through TradableMarket."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        order = market.buy(price=45.0, size=10)

        assert order.ticker == "TEST-123"
        assert order.side == "buy"
        assert order.price == 45.0
        assert order.size == 10
        assert order.status == "open"
        assert order.exchange == "mock"

    def test_tradable_market_sell(self):
        """Test placing a sell order through TradableMarket."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        order = market.sell(price=55.0, size=5)

        assert order.ticker == "TEST-123"
        assert order.side == "sell"
        assert order.price == 55.0
        assert order.size == 5

    def test_tradable_market_cancel_order(self):
        """Test canceling an order through TradableMarket."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        order = market.buy(price=45.0, size=10)
        result = market.cancel_order(order.order_id)

        assert result is True
        assert client._orders[order.order_id].status == "canceled"

    def test_tradable_market_get_orderbook(self):
        """Test getting orderbook through TradableMarket."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        orderbook = market.get_orderbook()

        assert orderbook.ticker == "TEST-123"
        assert orderbook.best_bid == 45.0
        assert orderbook.best_ask == 46.0
        assert orderbook.spread == 1.0

    def test_tradable_market_get_position(self):
        """Test getting position through TradableMarket."""
        client = MockExchangeClient()
        client._positions["TEST-123"] = Position(
            ticker="TEST-123",
            size=100,
            entry_price=45.0,
            current_price=50.0,
        )
        market = client.get_market("TEST-123")

        position = market.get_position()

        assert position is not None
        assert position.ticker == "TEST-123"
        assert position.size == 100
        assert position.entry_price == 45.0

    def test_tradable_market_get_position_none(self):
        """Test getting position when none exists."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        position = market.get_position()

        assert position is None

    def test_tradable_market_get_orders(self):
        """Test getting orders through TradableMarket."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        # Place some orders
        market.buy(price=44.0, size=10)
        market.buy(price=45.0, size=20)
        market.sell(price=55.0, size=5)

        orders = market.get_orders()

        assert len(orders) == 3

    def test_tradable_market_refresh(self):
        """Test refreshing market data."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        # Modify underlying market
        client._markets["TEST-123"].status = "closed"

        # Refresh
        market.refresh()

        assert market.status == "closed"

    def test_tradable_market_repr(self):
        """Test TradableMarket string representation."""
        client = MockExchangeClient()
        market = client.get_market("TEST-123")

        repr_str = repr(market)

        assert "TradableMarket" in repr_str
        assert "TEST-123" in repr_str
        assert "mock" in repr_str


class TestExchangeClient:
    """Tests for the ExchangeClient interface."""

    def test_mock_client_get_markets(self):
        """Test getting multiple markets."""
        client = MockExchangeClient()

        # Add some markets
        client._markets["TEST-1"] = Market(ticker="TEST-1", title="Test 1", status="open")
        client._markets["TEST-2"] = Market(ticker="TEST-2", title="Test 2", status="open")
        client._markets["TEST-3"] = Market(ticker="TEST-3", title="Test 3", status="closed")

        # Get all markets
        all_markets = client.get_markets()
        assert len(all_markets) == 3

        # Filter by status
        open_markets = client.get_markets(status="open")
        assert len(open_markets) == 2

    def test_mock_client_get_balance(self):
        """Test getting account balance."""
        client = MockExchangeClient()

        balance = client.get_balance()

        assert balance == 1000.0

    def test_mock_client_get_all_positions(self):
        """Test getting all positions."""
        client = MockExchangeClient()
        client._positions["TEST-1"] = Position(
            ticker="TEST-1", size=100, entry_price=45.0, current_price=50.0
        )
        client._positions["TEST-2"] = Position(
            ticker="TEST-2", size=-50, entry_price=60.0, current_price=55.0
        )

        positions = client.get_all_positions()

        assert len(positions) == 2
        assert "TEST-1" in positions
        assert "TEST-2" in positions

    def test_mock_client_get_all_orders(self):
        """Test getting all orders."""
        client = MockExchangeClient()
        market1 = client.get_market("TEST-1")
        market2 = client.get_market("TEST-2")

        market1.buy(price=45.0, size=10)
        market2.sell(price=55.0, size=5)

        all_orders = client.get_all_orders()

        assert len(all_orders) == 2

    def test_mock_client_name(self):
        """Test exchange name property."""
        client = MockExchangeClient()

        assert client.name == "mock"


class TestKalshiExchangeImport:
    """Tests that KalshiExchange can be imported and basic instantiation works."""

    def test_import_kalshi_exchange(self):
        """Test that KalshiExchange can be imported."""
        from src.exchanges import KalshiExchange

        assert KalshiExchange is not None

    def test_kalshi_exchange_name(self):
        """Test KalshiExchange name property."""
        from src.exchanges import KalshiExchange

        # Mock the config and API client
        with patch("src.exchanges.kalshi.get_config") as mock_config:
            mock_config.return_value = MagicMock(
                api_base_url="https://api.kalshi.com",
                api_timeout=30,
                api_max_retries=3,
                api_key_id=None,
                api_private_key_path=None,
                min_volume=0,
                rate_limits=MagicMock(requests_per_second=10, requests_per_minute=100),
            )

            with patch("src.exchanges.kalshi.KalshiClient"):
                exchange = KalshiExchange()
                assert exchange.name == "kalshi"


class TestPolymarketExchangeImport:
    """Tests that PolymarketExchange can be imported and basic instantiation works."""

    def test_import_polymarket_exchange(self):
        """Test that PolymarketExchange can be imported."""
        from src.exchanges import PolymarketExchange

        assert PolymarketExchange is not None

    def test_polymarket_exchange_name(self):
        """Test PolymarketExchange name property."""
        from src.exchanges import PolymarketExchange

        exchange = PolymarketExchange()  # Will be uninitialized without credentials
        assert exchange.name == "polymarket"

    def test_polymarket_exchange_uninitialized_balance(self):
        """Test that uninitialized exchange returns 0 balance."""
        from src.exchanges import PolymarketExchange

        exchange = PolymarketExchange()
        assert exchange.get_balance() == 0.0


class TestIntegration:
    """Integration tests for the exchange interface."""

    def test_full_trading_workflow(self):
        """Test a complete trading workflow using mock client."""
        client = MockExchangeClient()

        # 1. Get a market
        market = client.get_market("AAPL-24JAN-100")
        assert market.ticker == "AAPL-24JAN-100"

        # 2. Check orderbook
        orderbook = market.get_orderbook()
        assert orderbook.best_bid is not None
        assert orderbook.best_ask is not None
        assert orderbook.spread is not None

        # 3. Place a buy order
        buy_order = market.buy(price=orderbook.best_bid, size=10)
        assert buy_order.status == "open"

        # 4. Place a sell order
        sell_order = market.sell(price=orderbook.best_ask, size=5)
        assert sell_order.status == "open"

        # 5. Check orders for this market
        orders = market.get_orders()
        assert len(orders) == 2

        # 6. Cancel the buy order
        result = market.cancel_order(buy_order.order_id)
        assert result is True

        # 7. Check that buy order is now canceled
        all_orders = client.get_all_orders()
        canceled = [o for o in all_orders if o.status == "canceled"]
        assert len(canceled) == 1
        assert canceled[0].order_id == buy_order.order_id

    def test_multiple_markets(self):
        """Test working with multiple markets."""
        client = MockExchangeClient()

        # Get multiple markets
        market1 = client.get_market("MARKET-1")
        market2 = client.get_market("MARKET-2")

        # Place orders on both
        order1 = market1.buy(price=45.0, size=10)
        order2 = market2.sell(price=55.0, size=5)

        # Verify orders are tracked separately
        assert market1.get_orders()[0].order_id == order1.order_id
        assert market2.get_orders()[0].order_id == order2.order_id

        # Verify all orders are accessible from client
        all_orders = client.get_all_orders()
        assert len(all_orders) == 2
