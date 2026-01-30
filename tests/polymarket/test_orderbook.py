"""Tests for Polymarket order book manager."""

import pytest
from datetime import datetime

from src.polymarket.orderbook import OrderBookManager


class TestOrderBookManager:
    """Tests for OrderBookManager."""

    def test_apply_snapshot(self):
        """Test applying a full snapshot."""
        manager = OrderBookManager()

        bids = [
            {"price": 0.55, "size": 100},
            {"price": 0.54, "size": 200},
        ]
        asks = [
            {"price": 0.57, "size": 150},
            {"price": 0.58, "size": 250},
        ]

        manager.apply_snapshot("asset1", "market1", bids, asks)

        book = manager.get_orderbook("asset1")
        assert book is not None
        assert book.best_bid == 0.55
        assert book.best_ask == 0.57

    def test_apply_delta_add(self):
        """Test adding a price level via delta."""
        manager = OrderBookManager()
        manager.apply_snapshot("asset1", "market1", [], [])

        manager.apply_delta("asset1", "bid", 0.55, 100)

        bid, ask = manager.get_best_bid_ask("asset1")
        assert bid == 0.55

    def test_apply_delta_remove(self):
        """Test removing a price level via delta."""
        manager = OrderBookManager()
        manager.apply_snapshot(
            "asset1", "market1",
            [{"price": 0.55, "size": 100}],
            [],
        )

        manager.apply_delta("asset1", "bid", 0.55, 0)  # Size 0 removes level

        bid, ask = manager.get_best_bid_ask("asset1")
        assert bid is None

    def test_apply_delta_update(self):
        """Test updating a price level via delta."""
        manager = OrderBookManager()
        manager.apply_snapshot(
            "asset1", "market1",
            [{"price": 0.55, "size": 100}],
            [],
        )

        manager.apply_delta("asset1", "bid", 0.55, 200)

        book = manager.get_orderbook("asset1")
        assert book.bids[0].size == 200

    def test_apply_deltas_batch(self):
        """Test applying multiple deltas atomically."""
        manager = OrderBookManager()
        manager.apply_snapshot("asset1", "market1", [], [])

        changes = [
            {"side": "bid", "price": 0.55, "size": 100},
            {"side": "bid", "price": 0.54, "size": 200},
            {"side": "ask", "price": 0.57, "size": 150},
        ]

        manager.apply_deltas("asset1", changes)

        book = manager.get_orderbook("asset1")
        assert len(book.bids) == 2
        assert len(book.asks) == 1

    def test_get_nonexistent_book(self):
        """Test getting order book for unknown asset."""
        manager = OrderBookManager()

        book = manager.get_orderbook("unknown")
        assert book is None

    def test_get_mid_price(self):
        """Test mid price calculation."""
        manager = OrderBookManager()
        manager.apply_snapshot(
            "asset1", "market1",
            [{"price": 0.55, "size": 100}],
            [{"price": 0.57, "size": 100}],
        )

        mid = manager.get_mid_price("asset1")
        assert mid == pytest.approx(0.56)

    def test_get_spread(self):
        """Test spread calculation."""
        manager = OrderBookManager()
        manager.apply_snapshot(
            "asset1", "market1",
            [{"price": 0.55, "size": 100}],
            [{"price": 0.60, "size": 100}],
        )

        spread = manager.get_spread("asset1")
        assert spread == pytest.approx(0.05)

    def test_get_depth_at_price(self):
        """Test depth calculation at price level."""
        manager = OrderBookManager()
        manager.apply_snapshot(
            "asset1", "market1",
            [
                {"price": 0.55, "size": 100},
                {"price": 0.54, "size": 200},
                {"price": 0.53, "size": 300},
            ],
            [
                {"price": 0.57, "size": 150},
                {"price": 0.58, "size": 250},
            ],
        )

        # Bids at or above 0.54
        depth = manager.get_depth_at_price("asset1", "bid", 0.54)
        assert depth == 300  # 100 + 200

        # Asks at or below 0.58
        depth = manager.get_depth_at_price("asset1", "ask", 0.58)
        assert depth == 400  # 150 + 250

    def test_estimate_fill_price_buy(self):
        """Test fill price estimation for buy order."""
        manager = OrderBookManager()
        manager.apply_snapshot(
            "asset1", "market1",
            [],
            [
                {"price": 0.57, "size": 100},
                {"price": 0.58, "size": 100},
                {"price": 0.59, "size": 100},
            ],
        )

        # Buy 150 shares - takes 100 @ 0.57, 50 @ 0.58
        avg_price = manager.estimate_fill_price("asset1", "buy", 150)
        expected = (100 * 0.57 + 50 * 0.58) / 150
        assert avg_price == pytest.approx(expected)

    def test_estimate_fill_price_insufficient_liquidity(self):
        """Test fill price with insufficient liquidity."""
        manager = OrderBookManager()
        manager.apply_snapshot(
            "asset1", "market1",
            [],
            [{"price": 0.57, "size": 100}],
        )

        # Try to buy 200 but only 100 available
        avg_price = manager.estimate_fill_price("asset1", "buy", 200)
        assert avg_price is None

    def test_callback_on_update(self):
        """Test callback is called on updates."""
        manager = OrderBookManager()
        updates = []

        manager.on_update(lambda asset_id, book: updates.append((asset_id, book)))

        manager.apply_snapshot(
            "asset1", "market1",
            [{"price": 0.55, "size": 100}],
            [],
        )

        assert len(updates) == 1
        assert updates[0][0] == "asset1"
        assert updates[0][1].best_bid == 0.55

    def test_get_assets(self):
        """Test getting list of tracked assets."""
        manager = OrderBookManager()
        manager.apply_snapshot("asset1", "market1", [], [])
        manager.apply_snapshot("asset2", "market2", [], [])

        assets = manager.get_assets()
        assert set(assets) == {"asset1", "asset2"}

    def test_clear_specific_asset(self):
        """Test clearing a specific asset."""
        manager = OrderBookManager()
        manager.apply_snapshot("asset1", "market1", [], [])
        manager.apply_snapshot("asset2", "market2", [], [])

        manager.clear("asset1")

        assert manager.get_orderbook("asset1") is None
        assert manager.get_orderbook("asset2") is not None

    def test_clear_all(self):
        """Test clearing all assets."""
        manager = OrderBookManager()
        manager.apply_snapshot("asset1", "market1", [], [])
        manager.apply_snapshot("asset2", "market2", [], [])

        manager.clear()

        assert len(manager.get_assets()) == 0

    def test_sequence_ordering(self):
        """Test that stale updates are ignored."""
        manager = OrderBookManager()
        manager.apply_snapshot("asset1", "market1", [], [], sequence=100)

        # This should be ignored (stale)
        manager.apply_delta("asset1", "bid", 0.55, 100, sequence=50)

        bid, _ = manager.get_best_bid_ask("asset1")
        assert bid is None

        # This should be applied
        manager.apply_delta("asset1", "bid", 0.56, 100, sequence=150)

        bid, _ = manager.get_best_bid_ask("asset1")
        assert bid == 0.56
