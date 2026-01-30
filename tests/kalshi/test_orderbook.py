"""Tests for Kalshi order book manager."""

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from src.kalshi.orderbook import (
    OrderBookLevel,
    OrderBookManager,
    OrderBookState,
)
from src.kalshi.exceptions import OrderBookError


class TestOrderBookLevel(unittest.TestCase):
    """Tests for OrderBookLevel."""

    def test_valid_level(self):
        """Test creating valid level."""
        level = OrderBookLevel(price=50, size=100)
        self.assertEqual(level.price, 50)
        self.assertEqual(level.size, 100)

    def test_price_bounds(self):
        """Test price validation."""
        OrderBookLevel(price=1, size=10)
        OrderBookLevel(price=99, size=10)

        with self.assertRaises(ValueError):
            OrderBookLevel(price=0, size=10)
        with self.assertRaises(ValueError):
            OrderBookLevel(price=100, size=10)

    def test_negative_size(self):
        """Test size validation."""
        with self.assertRaises(ValueError):
            OrderBookLevel(price=50, size=-1)


class TestOrderBookState(unittest.TestCase):
    """Tests for OrderBookState."""

    def test_empty_book(self):
        """Test empty order book."""
        state = OrderBookState(ticker="TEST")
        self.assertIsNone(state.best_bid)
        self.assertIsNone(state.best_ask)
        self.assertIsNone(state.spread)
        self.assertEqual(state.bid_depth, 0)
        self.assertFalse(state.is_crossed())

    def test_best_bid_ask(self):
        """Test best bid/ask."""
        bids = [OrderBookLevel(50, 100), OrderBookLevel(48, 200)]
        asks = [OrderBookLevel(52, 150), OrderBookLevel(54, 100)]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)

        self.assertEqual(state.best_bid.price, 50)
        self.assertEqual(state.best_ask.price, 52)
        self.assertEqual(state.spread, 2)
        self.assertEqual(state.mid_price, 51.0)

    def test_spread_decimal(self):
        """Test spread as decimal."""
        bids = [OrderBookLevel(40, 100)]
        asks = [OrderBookLevel(60, 100)]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)

        self.assertEqual(state.spread, 20)
        self.assertEqual(state.spread_decimal, 0.20)

    def test_depth(self):
        """Test depth calculation."""
        bids = [OrderBookLevel(50, 100), OrderBookLevel(48, 200)]
        asks = [OrderBookLevel(52, 150)]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)

        self.assertEqual(state.bid_depth, 300)
        self.assertEqual(state.ask_depth, 150)

    def test_crossed_book(self):
        """Test crossed book detection."""
        bids = [OrderBookLevel(55, 100)]
        asks = [OrderBookLevel(50, 100)]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)
        self.assertTrue(state.is_crossed())


class TestOrderBookManager(unittest.TestCase):
    """Tests for OrderBookManager."""

    def setUp(self):
        """Set up test fixtures."""
        self.manager = OrderBookManager()

    def test_apply_snapshot(self):
        """Test applying snapshot."""
        snapshot = {
            "yes": [[50, 100], [48, 200]],
            "no": [[48, 150], [46, 100]],
            "seq": 1000,
        }
        self.manager.apply_snapshot("TEST", snapshot)

        book = self.manager.get_orderbook("TEST")
        self.assertIsNotNone(book)
        self.assertEqual(book.sequence, 1000)
        self.assertEqual(len(book.bids), 2)
        self.assertEqual(book.bids[0].price, 50)

    def test_apply_delta_add(self):
        """Test delta that adds liquidity."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100]],
            "no": [[50, 100]],
            "seq": 1000,
        })

        result = self.manager.apply_delta("TEST", {
            "side": "yes",
            "price": 50,
            "delta": 50,
            "seq": 1001,
        })

        self.assertTrue(result)
        book = self.manager.get_orderbook("TEST")
        self.assertEqual(book.best_bid.size, 150)

    def test_apply_delta_remove(self):
        """Test delta that removes liquidity."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100]],
            "no": [[50, 100]],
            "seq": 1000,
        })

        self.manager.apply_delta("TEST", {
            "side": "yes",
            "price": 50,
            "delta": -100,
            "seq": 1001,
        })

        book = self.manager.get_orderbook("TEST")
        self.assertEqual(len(book.bids), 0)

    def test_apply_delta_no_snapshot(self):
        """Test delta without snapshot raises error."""
        with self.assertRaises(OrderBookError):
            self.manager.apply_delta("TEST", {
                "side": "yes",
                "price": 50,
                "delta": 100,
                "seq": 1001,
            })

    def test_sequence_gap(self):
        """Test sequence gap detection."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100]],
            "no": [],
            "seq": 1000,
        })

        result = self.manager.apply_delta("TEST", {
            "side": "yes",
            "price": 50,
            "delta": 10,
            "seq": 1005,
        })

        self.assertFalse(result)

    def test_stale_delta(self):
        """Test stale delta is rejected."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100]],
            "no": [],
            "seq": 1000,
        })

        result = self.manager.apply_delta("TEST", {
            "side": "yes",
            "price": 50,
            "delta": 10,
            "seq": 999,
        })

        self.assertFalse(result)

    def test_get_spread(self):
        """Test spread calculation."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100]],
            "no": [[48, 100]],
            "seq": 1,
        })

        spread = self.manager.get_spread("TEST")
        self.assertEqual(spread, 2)

    def test_get_depth(self):
        """Test depth calculation."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100], [48, 200], [46, 300]],
            "no": [[48, 150]],
            "seq": 1,
        })

        bid_depth, ask_depth = self.manager.get_depth("TEST", levels=2)
        self.assertEqual(bid_depth, 300)
        self.assertEqual(ask_depth, 150)

    def test_get_vwap(self):
        """Test VWAP calculation."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100], [48, 100]],
            "no": [],
            "seq": 1,
        })

        vwap = self.manager.get_vwap("TEST", "bid", 150)
        # 100 @ 50 + 50 @ 48 = 5000 + 2400 = 7400 / 150 = 49.33
        self.assertAlmostEqual(vwap, 49.33, places=2)

    def test_vwap_insufficient_liquidity(self):
        """Test VWAP with insufficient liquidity."""
        self.manager.apply_snapshot("TEST", {
            "yes": [[50, 100]],
            "no": [],
            "seq": 1,
        })

        vwap = self.manager.get_vwap("TEST", "bid", 200)
        self.assertIsNone(vwap)

    def test_clear(self):
        """Test clearing order books."""
        self.manager.apply_snapshot("TEST1", {"yes": [], "no": [], "seq": 1})
        self.manager.apply_snapshot("TEST2", {"yes": [], "no": [], "seq": 1})

        self.manager.clear("TEST1")
        self.assertFalse(self.manager.has_orderbook("TEST1"))
        self.assertTrue(self.manager.has_orderbook("TEST2"))

        self.manager.clear()
        self.assertFalse(self.manager.has_orderbook("TEST2"))

    def test_on_update_callback(self):
        """Test update callback."""
        updates = []

        def on_update(ticker, state):
            updates.append((ticker, state.sequence))

        manager = OrderBookManager(on_update=on_update)
        manager.apply_snapshot("TEST", {"yes": [], "no": [], "seq": 1000})

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0], ("TEST", 1000))


class TestOrderBookManagerThreadSafety(unittest.TestCase):
    """Thread safety tests."""

    def test_concurrent_operations(self):
        """Test concurrent reads and writes."""
        manager = OrderBookManager()
        manager.apply_snapshot("TEST", {
            "yes": [[50, 100]],
            "no": [[50, 100]],
            "seq": 0,
        })

        errors = []
        stop_event = threading.Event()

        def writer():
            seq = 1
            while not stop_event.is_set():
                try:
                    manager.apply_delta("TEST", {
                        "side": "yes",
                        "price": 50,
                        "delta": 1,
                        "seq": seq,
                    })
                    seq += 1
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        def reader():
            while not stop_event.is_set():
                try:
                    manager.get_orderbook("TEST")
                    manager.get_spread("TEST")
                    manager.get_depth("TEST")
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()

        time.sleep(0.3)
        stop_event.set()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
