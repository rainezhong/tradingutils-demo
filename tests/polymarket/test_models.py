"""Tests for Polymarket models."""

import pytest
from datetime import datetime

from src.polymarket.models import (
    OrderBookLevel,
    OrderSide,
    OrderStatus,
    OrderType,
    PolymarketMarket,
    PolymarketOrder,
    PolymarketOrderBook,
    PolymarketTrade,
)


class TestPolymarketMarket:
    """Tests for PolymarketMarket model."""

    def test_from_api_response(self):
        """Test creating market from API response."""
        data = {
            "condition_id": "0x123",
            "question_id": "0x456",
            "question": "Will it rain tomorrow?",
            "description": "Test market",
            "end_date_iso": "2025-12-31T00:00:00Z",
            "active": True,
            "closed": False,
            "minimum_order_size": "5.0",
            "minimum_tick_size": "0.01",
        }

        market = PolymarketMarket.from_api_response(data)

        assert market.condition_id == "0x123"
        assert market.question_id == "0x456"
        assert market.question == "Will it rain tomorrow?"
        assert market.active is True
        assert market.closed is False

    def test_missing_fields(self):
        """Test with missing fields."""
        data = {"condition_id": "0x123"}

        market = PolymarketMarket.from_api_response(data)

        assert market.condition_id == "0x123"
        assert market.question == ""
        assert market.end_date is None


class TestPolymarketOrder:
    """Tests for PolymarketOrder model."""

    def test_from_api_response(self):
        """Test creating order from API response."""
        data = {
            "id": "order123",
            "market": "market456",
            "asset_id": "token789",
            "side": "BUY",
            "price": "0.55",
            "original_size": "100",
            "size_matched": "50",
            "status": "LIVE",
            "owner": "0xowner",
        }

        order = PolymarketOrder.from_api_response(data)

        assert order.order_id == "order123"
        assert order.side == OrderSide.BUY
        assert order.price == 0.55
        assert order.original_size == 100.0
        assert order.size_matched == 50.0
        assert order.remaining_size == 50.0
        assert order.is_active is True

    def test_filled_order(self):
        """Test order status properties."""
        order = PolymarketOrder(
            order_id="test",
            market="market",
            asset_id="asset",
            side=OrderSide.SELL,
            price=0.60,
            original_size=100,
            size_matched=100,
            status=OrderStatus.MATCHED,
        )

        assert order.remaining_size == 0
        assert order.is_active is False


class TestPolymarketOrderBook:
    """Tests for PolymarketOrderBook model."""

    def test_from_api_response(self):
        """Test creating order book from API response."""
        data = {
            "bids": [
                {"price": "0.55", "size": "100"},
                {"price": "0.54", "size": "200"},
            ],
            "asks": [
                {"price": "0.57", "size": "150"},
                {"price": "0.58", "size": "250"},
            ],
        }

        book = PolymarketOrderBook.from_api_response(data, "token123", "market456")

        assert book.asset_id == "token123"
        assert book.market == "market456"
        assert len(book.bids) == 2
        assert len(book.asks) == 2
        assert book.best_bid == 0.55
        assert book.best_ask == 0.57
        assert book.mid_price == pytest.approx(0.56)
        assert book.spread == pytest.approx(0.02)

    def test_empty_book(self):
        """Test empty order book."""
        data = {"bids": [], "asks": []}

        book = PolymarketOrderBook.from_api_response(data, "token", "market")

        assert book.best_bid is None
        assert book.best_ask is None
        assert book.mid_price is None
        assert book.spread is None
        assert book.bid_depth == 0
        assert book.ask_depth == 0

    def test_depth_calculation(self):
        """Test depth calculation."""
        book = PolymarketOrderBook(
            asset_id="token",
            market="market",
            bids=[
                OrderBookLevel(price=0.55, size=100),
                OrderBookLevel(price=0.54, size=200),
            ],
            asks=[
                OrderBookLevel(price=0.57, size=150),
                OrderBookLevel(price=0.58, size=250),
            ],
        )

        assert book.bid_depth == 300
        assert book.ask_depth == 400


class TestPolymarketTrade:
    """Tests for PolymarketTrade model."""

    def test_from_api_response(self):
        """Test creating trade from API response."""
        data = {
            "id": "trade123",
            "market": "market456",
            "asset_id": "token789",
            "side": "SELL",
            "price": "0.60",
            "size": "50",
            "fee": "0.50",
        }

        trade = PolymarketTrade.from_api_response(data)

        assert trade.trade_id == "trade123"
        assert trade.side == OrderSide.SELL
        assert trade.price == 0.60
        assert trade.size == 50.0
        assert trade.fee == 0.50
