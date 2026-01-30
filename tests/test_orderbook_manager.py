"""Tests for the order book manager."""

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from src.core.orderbook_manager import (
    OrderBookLevel,
    OrderBookManager,
    OrderBookState,
)
from src.core.exceptions import OrderBookError


class TestOrderBookLevel(unittest.TestCase):
    """Tests for OrderBookLevel dataclass."""

    def test_valid_level(self):
        """Test creating a valid level."""
        level = OrderBookLevel(price=50, size=100)
        self.assertEqual(level.price, 50)
        self.assertEqual(level.size, 100)

    def test_price_bounds(self):
        """Test price validation."""
        # Valid edge cases
        OrderBookLevel(price=0, size=10)
        OrderBookLevel(price=99, size=10)

        # Invalid prices
        with self.assertRaises(ValueError):
            OrderBookLevel(price=-1, size=10)
        with self.assertRaises(ValueError):
            OrderBookLevel(price=100, size=10)

    def test_negative_size(self):
        """Test size validation."""
        with self.assertRaises(ValueError):
            OrderBookLevel(price=50, size=-1)

    def test_zero_size(self):
        """Test zero size is valid."""
        level = OrderBookLevel(price=50, size=0)
        self.assertEqual(level.size, 0)


class TestOrderBookState(unittest.TestCase):
    """Tests for OrderBookState dataclass."""

    def test_empty_book(self):
        """Test empty order book."""
        state = OrderBookState(ticker="TEST")
        self.assertIsNone(state.best_bid)
        self.assertIsNone(state.best_ask)
        self.assertIsNone(state.spread)
        self.assertIsNone(state.mid_price)
        self.assertEqual(state.bid_depth, 0)
        self.assertEqual(state.ask_depth, 0)
        self.assertFalse(state.is_crossed())

    def test_best_bid_ask(self):
        """Test best bid/ask calculation."""
        bids = [
            OrderBookLevel(price=50, size=100),
            OrderBookLevel(price=48, size=200),
        ]
        asks = [
            OrderBookLevel(price=52, size=150),
            OrderBookLevel(price=54, size=100),
        ]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)

        self.assertEqual(state.best_bid.price, 50)
        self.assertEqual(state.best_ask.price, 52)
        self.assertEqual(state.spread, 2)
        self.assertEqual(state.mid_price, 51.0)

    def test_spread_pct(self):
        """Test spread percentage calculation."""
        bids = [OrderBookLevel(price=40, size=100)]
        asks = [OrderBookLevel(price=60, size=100)]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)

        # Spread = 20, mid = 50, spread_pct = 20/50*100 = 40%
        self.assertAlmostEqual(state.spread_pct, 40.0, places=2)

    def test_depth_calculation(self):
        """Test depth calculation."""
        bids = [
            OrderBookLevel(price=50, size=100),
            OrderBookLevel(price=48, size=200),
            OrderBookLevel(price=46, size=300),
        ]
        asks = [
            OrderBookLevel(price=52, size=150),
            OrderBookLevel(price=54, size=100),
        ]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)

        self.assertEqual(state.bid_depth, 600)
        self.assertEqual(state.ask_depth, 250)

    def test_crossed_book(self):
        """Test crossed book detection."""
        bids = [OrderBookLevel(price=55, size=100)]
        asks = [OrderBookLevel(price=50, size=100)]
        state = OrderBookState(ticker="TEST", bids=bids, asks=asks)

        self.assertTrue(state.is_crossed())


class TestOrderBookManager(unittest.TestCase):
    """Tests for OrderBookManager class."""

    def setUp(self):
        """Set up test fixtures."""
        self.manager = OrderBookManager()

    def test_apply_snapshot(self):
        """Test applying a snapshot."""
        snapshot = {
            "yes": [[50, 100], [48, 200]],  # bids
            "no": [[48, 150], [46, 100]],   # asks (as no prices: 100-48=52, 100-46=54)
            "seq": 1000,
        }
        self.manager.apply_snapshot("TEST", snapshot)

        book = self.manager.get_orderbook("TEST")
        self.assertIsNotNone(book)
        self.assertEqual(book.ticker, "TEST")
        self.assertEqual(book.sequence, 1000)
        self.assertEqual(len(book.bids), 2)
        self.assertEqual(len(book.asks), 2)

        # Check sorting
        self.assertEqual(book.bids[0].price, 50)  # Best bid first
        self.assertEqual(book.bids[1].price, 48)
        self.assertEqual(book.asks[0].price, 52)  # Best ask first (100-48)
        self.assertEqual(book.asks[1].price, 54)  # 100-46

    def test_apply_delta_add(self):
        """Test applying a delta that adds liquidity."""
        # First apply snapshot
        snapshot = {"yes": [[50, 100]], "no": [[50, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        # Apply delta to add liquidity at existing level
        delta = {"side": "yes", "price": 50, "delta": 50, "seq": 1001}
        result = self.manager.apply_delta("TEST", delta)

        self.assertTrue(result)
        book = self.manager.get_orderbook("TEST")
        self.assertEqual(book.best_bid.size, 150)
        self.assertEqual(book.sequence, 1001)

    def test_apply_delta_remove(self):
        """Test applying a delta that removes liquidity."""
        snapshot = {"yes": [[50, 100]], "no": [[50, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        # Remove some liquidity
        delta = {"side": "yes", "price": 50, "delta": -50, "seq": 1001}
        result = self.manager.apply_delta("TEST", delta)

        self.assertTrue(result)
        book = self.manager.get_orderbook("TEST")
        self.assertEqual(book.best_bid.size, 50)

    def test_apply_delta_remove_level(self):
        """Test applying a delta that removes an entire level."""
        snapshot = {"yes": [[50, 100], [48, 100]], "no": [[50, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        # Remove entire level
        delta = {"side": "yes", "price": 50, "delta": -100, "seq": 1001}
        result = self.manager.apply_delta("TEST", delta)

        self.assertTrue(result)
        book = self.manager.get_orderbook("TEST")
        self.assertEqual(len(book.bids), 1)
        self.assertEqual(book.best_bid.price, 48)

    def test_apply_delta_new_level(self):
        """Test applying a delta that creates a new price level."""
        snapshot = {"yes": [[50, 100]], "no": [[50, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        # Add new level
        delta = {"side": "yes", "price": 48, "delta": 50, "seq": 1001}
        result = self.manager.apply_delta("TEST", delta)

        self.assertTrue(result)
        book = self.manager.get_orderbook("TEST")
        self.assertEqual(len(book.bids), 2)
        self.assertEqual(book.bids[1].price, 48)
        self.assertEqual(book.bids[1].size, 50)

    def test_apply_delta_no_snapshot(self):
        """Test applying delta without snapshot raises error."""
        delta = {"side": "yes", "price": 50, "delta": 100, "seq": 1001}

        with self.assertRaises(OrderBookError):
            self.manager.apply_delta("TEST", delta)

    def test_apply_delta_stale_sequence(self):
        """Test stale delta is rejected."""
        snapshot = {"yes": [[50, 100]], "no": [[50, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        # Stale sequence
        delta = {"side": "yes", "price": 50, "delta": 50, "seq": 999}
        result = self.manager.apply_delta("TEST", delta)

        self.assertFalse(result)
        book = self.manager.get_orderbook("TEST")
        self.assertEqual(book.sequence, 1000)  # Unchanged

    def test_apply_delta_sequence_gap(self):
        """Test sequence gap is detected."""
        snapshot = {"yes": [[50, 100]], "no": [[50, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        # Skip sequence 1001
        delta = {"side": "yes", "price": 50, "delta": 50, "seq": 1005}
        result = self.manager.apply_delta("TEST", delta)

        self.assertFalse(result)

    def test_get_best_bid_ask(self):
        """Test get_best_bid and get_best_ask."""
        snapshot = {"yes": [[50, 100]], "no": [[48, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        bid = self.manager.get_best_bid("TEST")
        ask = self.manager.get_best_ask("TEST")

        self.assertEqual(bid.price, 50)
        self.assertEqual(ask.price, 52)  # 100-48

        # Non-existent ticker
        self.assertIsNone(self.manager.get_best_bid("UNKNOWN"))
        self.assertIsNone(self.manager.get_best_ask("UNKNOWN"))

    def test_get_spread(self):
        """Test spread calculation."""
        snapshot = {"yes": [[50, 100]], "no": [[48, 100]], "seq": 1000}
        self.manager.apply_snapshot("TEST", snapshot)

        spread = self.manager.get_spread("TEST")
        self.assertEqual(spread, 2)  # 52 - 50

        # Non-existent ticker
        self.assertIsNone(self.manager.get_spread("UNKNOWN"))

    def test_get_depth(self):
        """Test depth calculation."""
        snapshot = {
            "yes": [[50, 100], [48, 200], [46, 300], [44, 400]],
            "no": [[48, 150], [46, 250]],
            "seq": 1000,
        }
        self.manager.apply_snapshot("TEST", snapshot)

        # Default 5 levels
        bid_depth, ask_depth = self.manager.get_depth("TEST")
        self.assertEqual(bid_depth, 1000)  # All 4 levels
        self.assertEqual(ask_depth, 400)   # Both levels

        # Limit to 2 levels
        bid_depth, ask_depth = self.manager.get_depth("TEST", levels=2)
        self.assertEqual(bid_depth, 300)  # 100 + 200
        self.assertEqual(ask_depth, 400)  # 150 + 250

        # Non-existent ticker
        self.assertEqual(self.manager.get_depth("UNKNOWN"), (0, 0))

    def test_get_vwap(self):
        """Test VWAP calculation."""
        snapshot = {
            "yes": [[50, 100], [48, 100], [46, 100]],
            "no": [[48, 100], [46, 100]],
            "seq": 1000,
        }
        self.manager.apply_snapshot("TEST", snapshot)

        # Fill 150 contracts from bids
        vwap = self.manager.get_vwap("TEST", "bid", 150)
        # 100 @ 50 + 50 @ 48 = 5000 + 2400 = 7400 / 150 = 49.33
        self.assertAlmostEqual(vwap, 49.33, places=2)

        # Insufficient liquidity
        vwap = self.manager.get_vwap("TEST", "bid", 500)
        self.assertIsNone(vwap)

    def test_clear_specific_ticker(self):
        """Test clearing a specific ticker."""
        self.manager.apply_snapshot("TEST1", {"yes": [[50, 100]], "no": [], "seq": 1})
        self.manager.apply_snapshot("TEST2", {"yes": [[50, 100]], "no": [], "seq": 1})

        self.manager.clear("TEST1")

        self.assertFalse(self.manager.has_orderbook("TEST1"))
        self.assertTrue(self.manager.has_orderbook("TEST2"))

    def test_clear_all(self):
        """Test clearing all order books."""
        self.manager.apply_snapshot("TEST1", {"yes": [[50, 100]], "no": [], "seq": 1})
        self.manager.apply_snapshot("TEST2", {"yes": [[50, 100]], "no": [], "seq": 1})

        self.manager.clear()

        self.assertFalse(self.manager.has_orderbook("TEST1"))
        self.assertFalse(self.manager.has_orderbook("TEST2"))

    def test_get_all_tickers(self):
        """Test getting all tracked tickers."""
        self.manager.apply_snapshot("TEST1", {"yes": [], "no": [], "seq": 1})
        self.manager.apply_snapshot("TEST2", {"yes": [], "no": [], "seq": 1})
        self.manager.apply_snapshot("TEST3", {"yes": [], "no": [], "seq": 1})

        tickers = self.manager.get_all_tickers()
        self.assertEqual(set(tickers), {"TEST1", "TEST2", "TEST3"})

    def test_on_update_callback(self):
        """Test update callback is called."""
        updates = []

        def on_update(ticker, state):
            updates.append((ticker, state.sequence))

        manager = OrderBookManager(on_update=on_update)
        manager.apply_snapshot("TEST", {"yes": [[50, 100]], "no": [], "seq": 1000})
        manager.apply_delta("TEST", {"side": "yes", "price": 50, "delta": 10, "seq": 1001})

        self.assertEqual(len(updates), 2)
        self.assertEqual(updates[0], ("TEST", 1000))
        self.assertEqual(updates[1], ("TEST", 1001))


class TestOrderBookManagerThreadSafety(unittest.TestCase):
    """Thread safety tests for OrderBookManager."""

    def test_concurrent_snapshots(self):
        """Test concurrent snapshot applications."""
        manager = OrderBookManager()
        num_tickers = 100

        def apply_snapshot(i):
            ticker = f"TEST-{i}"
            snapshot = {"yes": [[50, i]], "no": [[50, i]], "seq": i}
            manager.apply_snapshot(ticker, snapshot)

        with ThreadPoolExecutor(max_workers=10) as executor:
            executor.map(apply_snapshot, range(num_tickers))

        self.assertEqual(len(manager.get_all_tickers()), num_tickers)

    def test_concurrent_deltas(self):
        """Test concurrent delta applications."""
        manager = OrderBookManager()
        ticker = "TEST"

        # Initial snapshot
        manager.apply_snapshot(ticker, {"yes": [[50, 1000]], "no": [], "seq": 0})

        num_deltas = 100
        lock = threading.Lock()
        next_seq = [1]  # Use list to allow modification in closure

        def apply_delta(i):
            with lock:
                seq = next_seq[0]
                next_seq[0] += 1

            delta = {"side": "yes", "price": 50, "delta": 1, "seq": seq}
            return manager.apply_delta(ticker, delta)

        with ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(apply_delta, range(num_deltas)))

        book = manager.get_orderbook(ticker)
        # Should have applied all deltas
        self.assertEqual(book.sequence, num_deltas)
        self.assertEqual(book.best_bid.size, 1000 + num_deltas)

    def test_concurrent_reads_writes(self):
        """Test concurrent reads and writes."""
        manager = OrderBookManager()
        ticker = "TEST"
        manager.apply_snapshot(ticker, {"yes": [[50, 100]], "no": [[50, 100]], "seq": 0})

        errors = []
        stop_event = threading.Event()

        def writer():
            seq = 1
            while not stop_event.is_set():
                try:
                    delta = {"side": "yes", "price": 50, "delta": 1, "seq": seq}
                    manager.apply_delta(ticker, delta)
                    seq += 1
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        def reader():
            while not stop_event.is_set():
                try:
                    manager.get_orderbook(ticker)
                    manager.get_best_bid(ticker)
                    manager.get_spread(ticker)
                    manager.get_depth(ticker)
                except Exception as e:
                    errors.append(e)
                time.sleep(0.001)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()

        time.sleep(0.5)  # Run for 500ms
        stop_event.set()

        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")


class TestOrderBookManagerEdgeCases(unittest.TestCase):
    """Edge case tests for OrderBookManager."""

    def test_empty_snapshot(self):
        """Test applying empty snapshot."""
        manager = OrderBookManager()
        manager.apply_snapshot("TEST", {"yes": [], "no": [], "seq": 1})

        book = manager.get_orderbook("TEST")
        self.assertIsNotNone(book)
        self.assertEqual(len(book.bids), 0)
        self.assertEqual(len(book.asks), 0)

    def test_single_level_book(self):
        """Test book with single level on each side."""
        manager = OrderBookManager()
        manager.apply_snapshot("TEST", {"yes": [[50, 100]], "no": [[50, 100]], "seq": 1})

        book = manager.get_orderbook("TEST")
        self.assertEqual(book.spread, 0)  # 100-50=50 ask, 50 bid
        self.assertEqual(book.mid_price, 50.0)

    def test_overwrite_snapshot(self):
        """Test that new snapshot replaces old state."""
        manager = OrderBookManager()

        manager.apply_snapshot("TEST", {"yes": [[50, 100]], "no": [], "seq": 1000})
        manager.apply_snapshot("TEST", {"yes": [[40, 50]], "no": [], "seq": 2000})

        book = manager.get_orderbook("TEST")
        self.assertEqual(book.sequence, 2000)
        self.assertEqual(book.best_bid.price, 40)
        self.assertEqual(book.best_bid.size, 50)
        self.assertEqual(len(book.bids), 1)

    def test_delta_to_zero_removes_level(self):
        """Test delta that brings size to exactly zero removes level."""
        manager = OrderBookManager()
        manager.apply_snapshot("TEST", {"yes": [[50, 100]], "no": [], "seq": 1000})

        # Remove exactly 100
        delta = {"side": "yes", "price": 50, "delta": -100, "seq": 1001}
        manager.apply_delta("TEST", delta)

        book = manager.get_orderbook("TEST")
        self.assertEqual(len(book.bids), 0)

    def test_multiple_tickers_isolated(self):
        """Test that different tickers have isolated state."""
        manager = OrderBookManager()

        manager.apply_snapshot("TEST1", {"yes": [[50, 100]], "no": [], "seq": 1})
        manager.apply_snapshot("TEST2", {"yes": [[60, 200]], "no": [], "seq": 2})

        # Modify TEST1
        manager.apply_delta("TEST1", {"side": "yes", "price": 50, "delta": 50, "seq": 2})

        book1 = manager.get_orderbook("TEST1")
        book2 = manager.get_orderbook("TEST2")

        self.assertEqual(book1.best_bid.size, 150)
        self.assertEqual(book2.best_bid.size, 200)  # Unchanged


if __name__ == "__main__":
    unittest.main()
