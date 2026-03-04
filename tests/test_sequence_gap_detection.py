"""Tests for WebSocket sequence gap detection."""

import asyncio
import pytest
import time
from unittest.mock import Mock, AsyncMock, patch
from typing import List, Tuple

from core.exchange_client.kalshi.kalshi_websocket import (
    KalshiWebSocket,
    WebSocketConfig,
)
from core.market.orderbook_manager import OrderBookManager, DeltaResult


class TestKalshiWebSocketSequenceDetection:
    """Test sequence gap detection in KalshiWebSocket."""

    @pytest.fixture
    def config_with_validation(self):
        """Config with sequence validation enabled."""
        return WebSocketConfig(
            url="wss://demo-api.kalshi.co/trade-api/ws/v2",
            enable_sequence_validation=True,
            gap_tolerance=0,
        )

    @pytest.fixture
    def config_with_tolerance(self):
        """Config with gap tolerance."""
        return WebSocketConfig(
            url="wss://demo-api.kalshi.co/trade-api/ws/v2",
            enable_sequence_validation=True,
            gap_tolerance=2,  # Allow gaps up to 2
        )

    @pytest.fixture
    def ws(self, config_with_validation):
        """WebSocket instance with validation enabled."""
        return KalshiWebSocket(auth=None, config=config_with_validation)

    def test_first_message_initializes_tracking(self, ws):
        """First message should initialize sequence tracking."""
        assert "TEST-1" not in ws._last_seq
        assert "TEST-1" not in ws._gap_metrics

        gap = ws._check_sequence_gap("TEST-1", 100)

        assert gap is False
        assert ws._last_seq["TEST-1"] == 100
        assert "TEST-1" in ws._gap_metrics
        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 0

    def test_consecutive_sequence_no_gap(self, ws):
        """Consecutive sequence numbers should not trigger gap."""
        ws._check_sequence_gap("TEST-1", 100)
        gap = ws._check_sequence_gap("TEST-1", 101)

        assert gap is False
        assert ws._last_seq["TEST-1"] == 101
        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 0

    def test_sequence_gap_detected(self, ws):
        """Gap in sequence should be detected."""
        ws._check_sequence_gap("TEST-1", 100)
        gap = ws._check_sequence_gap("TEST-1", 105)  # Gap of 4

        assert gap is True
        assert ws._last_seq["TEST-1"] == 105  # SHOULD update to prevent cascading gaps
        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 1
        assert ws._gap_metrics["TEST-1"]["gap_sizes"] == [4]
        assert ws._gap_metrics["TEST-1"]["last_gap_time"] is not None

    def test_gap_callback_invoked(self, ws):
        """Gap callback should be invoked on gap."""
        gap_events = []

        def on_gap(ticker, expected, actual):
            gap_events.append((ticker, expected, actual))

        ws.on_gap_detected(on_gap)
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 105)

        assert len(gap_events) == 1
        assert gap_events[0] == ("TEST-1", 101, 105)

    def test_gap_tolerance(self):
        """Small gaps within tolerance should not trigger detection."""
        config = WebSocketConfig(
            enable_sequence_validation=True,
            gap_tolerance=2,
        )
        ws = KalshiWebSocket(auth=None, config=config)

        ws._check_sequence_gap("TEST-1", 100)

        # Gap of 1 (within tolerance of 2)
        gap1 = ws._check_sequence_gap("TEST-1", 102)
        assert gap1 is False
        assert ws._last_seq["TEST-1"] == 102

        # Gap of 2 (exactly at tolerance)
        gap2 = ws._check_sequence_gap("TEST-1", 105)
        assert gap2 is False
        assert ws._last_seq["TEST-1"] == 105

        # Gap of 3 (exceeds tolerance)
        gap3 = ws._check_sequence_gap("TEST-1", 109)
        assert gap3 is True
        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 1

    def test_out_of_order_message(self, ws):
        """Out-of-order messages should not trigger gap but should not update seq."""
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 101)

        # Old message arrives
        gap = ws._check_sequence_gap("TEST-1", 99)

        assert gap is False
        assert ws._last_seq["TEST-1"] == 101  # Should NOT regress
        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 0

    def test_duplicate_message(self, ws):
        """Duplicate messages should not trigger gap."""
        ws._check_sequence_gap("TEST-1", 100)

        gap = ws._check_sequence_gap("TEST-1", 100)

        assert gap is False
        assert ws._last_seq["TEST-1"] == 100
        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 0

    def test_multiple_gaps_tracked(self, ws):
        """Multiple gaps should be tracked in metrics."""
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 101)  # Normal
        ws._check_sequence_gap("TEST-1", 105)  # Gap of 3
        ws._check_sequence_gap("TEST-1", 106)  # Normal
        ws._check_sequence_gap("TEST-1", 110)  # Gap of 3

        metrics = ws._gap_metrics["TEST-1"]
        assert metrics["total_gaps"] == 2
        assert metrics["gap_sizes"] == [3, 3]

    def test_gap_metrics_max_size(self, ws):
        """Gap sizes list should be capped at 100."""
        seq = 0

        # Generate 150 gaps (note: first iteration doesn't create a gap, just initializes)
        for i in range(150):
            ws._check_sequence_gap("TEST-1", seq)
            seq += 1
            ws._check_sequence_gap("TEST-1", seq)
            seq += 5  # Gap of 4 on next iteration

        metrics = ws._gap_metrics["TEST-1"]
        assert len(metrics["gap_sizes"]) == 100
        assert metrics["total_gaps"] == 149  # 150 iterations - 1 for initialization

    def test_get_gap_metrics_single_ticker(self, ws):
        """Should retrieve metrics for single ticker."""
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 105)

        metrics = ws.get_gap_metrics("TEST-1")
        assert metrics["total_gaps"] == 1
        assert metrics["gap_sizes"] == [4]

    def test_get_gap_metrics_all_tickers(self, ws):
        """Should retrieve metrics for all tickers."""
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 105)
        ws._check_sequence_gap("TEST-2", 200)
        ws._check_sequence_gap("TEST-2", 210)

        metrics = ws.get_gap_metrics()
        assert "TEST-1" in metrics
        assert "TEST-2" in metrics
        assert metrics["TEST-1"]["total_gaps"] == 1
        assert metrics["TEST-2"]["total_gaps"] == 1

    def test_reset_gap_metrics_single_ticker(self, ws):
        """Should reset metrics for single ticker."""
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 105)
        ws._check_sequence_gap("TEST-2", 200)

        ws.reset_gap_metrics("TEST-1")

        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 0
        assert ws._gap_metrics["TEST-1"]["gap_sizes"] == []
        assert ws._gap_metrics["TEST-2"]["total_gaps"] == 0  # Still tracked, but no gap yet

    def test_reset_gap_metrics_all(self, ws):
        """Should reset metrics for all tickers."""
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 105)
        ws._check_sequence_gap("TEST-2", 200)
        ws._check_sequence_gap("TEST-2", 210)

        ws.reset_gap_metrics()

        assert ws._gap_metrics["TEST-1"]["total_gaps"] == 0
        assert ws._gap_metrics["TEST-2"]["total_gaps"] == 0

    def test_disabled_validation(self):
        """Validation should be disabled when config flag is False."""
        config = WebSocketConfig(enable_sequence_validation=False)
        ws = KalshiWebSocket(auth=None, config=config)

        ws._check_sequence_gap("TEST-1", 100)
        gap = ws._check_sequence_gap("TEST-1", 200)  # Huge gap

        # Should not detect gap when disabled
        assert gap is False
        assert "TEST-1" not in ws._gap_metrics

    @pytest.mark.asyncio
    async def test_handle_sequence_gap_triggers_reconnect(self, ws):
        """Gap handler should close WebSocket to trigger reconnect."""
        mock_ws = AsyncMock()
        ws._ws = mock_ws

        await ws._handle_sequence_gap("TEST-1")

        mock_ws.close.assert_called_once()


class TestOrderBookManagerGapDetection:
    """Test gap detection in OrderBookManager."""

    @pytest.mark.asyncio
    async def test_apply_delta_gap_detected(self):
        """Should detect gap in delta sequence."""
        gap_events = []

        def on_gap(ticker, expected, actual):
            gap_events.append((ticker, expected, actual))

        manager = OrderBookManager(on_gap=on_gap)

        # Apply snapshot (seq=0)
        await manager.apply_snapshot(
            "TEST-1",
            {"yes": [[50, 100]], "no": [[50, 100]], "seq": 0},
        )

        # Apply delta with gap (seq=5, expected 1)
        result = await manager.apply_delta(
            "TEST-1",
            {"seq": 5, "side": "yes", "price": 51, "delta": 10},
        )

        assert result == DeltaResult.GAP
        assert len(gap_events) == 1
        assert gap_events[0] == ("TEST-1", 1, 5)

    @pytest.mark.asyncio
    async def test_apply_delta_consecutive_no_gap(self):
        """Consecutive deltas should not trigger gap."""
        gap_events = []

        def on_gap(ticker, expected, actual):
            gap_events.append((ticker, expected, actual))

        manager = OrderBookManager(on_gap=on_gap)

        await manager.apply_snapshot(
            "TEST-1",
            {"yes": [[50, 100]], "no": [[50, 100]], "seq": 0},
        )

        result1 = await manager.apply_delta(
            "TEST-1",
            {"seq": 1, "side": "yes", "price": 51, "delta": 10},
        )
        result2 = await manager.apply_delta(
            "TEST-1",
            {"seq": 2, "side": "yes", "price": 52, "delta": 10},
        )

        assert result1 == DeltaResult.APPLIED
        assert result2 == DeltaResult.APPLIED
        assert len(gap_events) == 0

    @pytest.mark.asyncio
    async def test_apply_delta_stale_no_gap_callback(self):
        """Stale deltas should not trigger gap callback."""
        gap_events = []

        def on_gap(ticker, expected, actual):
            gap_events.append((ticker, expected, actual))

        manager = OrderBookManager(on_gap=on_gap)

        await manager.apply_snapshot(
            "TEST-1",
            {"yes": [[50, 100]], "no": [[50, 100]], "seq": 5},
        )

        # Old delta
        result = await manager.apply_delta(
            "TEST-1",
            {"seq": 3, "side": "yes", "price": 51, "delta": 10},
        )

        assert result == DeltaResult.STALE
        assert len(gap_events) == 0

    @pytest.mark.asyncio
    async def test_no_gap_callback_still_works(self):
        """Manager should work without gap callback."""
        manager = OrderBookManager()

        await manager.apply_snapshot(
            "TEST-1",
            {"yes": [[50, 100]], "no": [[50, 100]], "seq": 0},
        )

        # Should not raise even with gap
        result = await manager.apply_delta(
            "TEST-1",
            {"seq": 5, "side": "yes", "price": 51, "delta": 10},
        )

        assert result == DeltaResult.GAP


class TestCEXFeedSequenceDetection:
    """Test sequence gap detection in CEX feeds."""

    def test_coinbase_supports_sequence(self):
        """Coinbase should support sequence validation."""
        from core.indicators.cex_feeds import CoinbaseL2Feed

        feed = CoinbaseL2Feed(enable_sequence_validation=True)
        assert feed.SUPPORTS_SEQUENCE is True
        assert feed._enable_sequence_validation is True

    def test_coinbase_sequence_validation_disabled_by_default(self):
        """Coinbase should have validation disabled by default."""
        from core.indicators.cex_feeds import CoinbaseL2Feed

        feed = CoinbaseL2Feed()
        assert feed._enable_sequence_validation is False

    def test_other_exchanges_no_sequence_support(self):
        """Other exchanges should not support sequence (for now)."""
        from core.indicators.cex_feeds import (
            KrakenL2Feed,
            BitstampL2Feed,
            GeminiL2Feed,
            CryptoComL2Feed,
        )

        # None of these provide sequence numbers in their WebSocket feeds
        assert KrakenL2Feed.SUPPORTS_SEQUENCE is False
        assert BitstampL2Feed.SUPPORTS_SEQUENCE is False
        assert GeminiL2Feed.SUPPORTS_SEQUENCE is False
        assert CryptoComL2Feed.SUPPORTS_SEQUENCE is False

    def test_gap_detection_mechanism(self):
        """Test gap detection logic in base class."""
        from core.indicators.cex_feeds import CoinbaseL2Feed

        feed = CoinbaseL2Feed(enable_sequence_validation=True, gap_tolerance=0)

        # First message
        gap1 = feed._check_sequence_gap(100)
        assert gap1 is False
        assert feed._last_seq == 100

        # Consecutive
        gap2 = feed._check_sequence_gap(101)
        assert gap2 is False
        assert feed._last_seq == 101

        # Gap
        gap3 = feed._check_sequence_gap(105)
        assert gap3 is True
        assert feed._last_seq == 105  # SHOULD update to prevent cascading gaps
        assert feed._total_gaps == 1
        assert feed._gap_sizes == [3]

    def test_gap_tolerance_in_cex_feed(self):
        """Test gap tolerance in CEX feeds."""
        from core.indicators.cex_feeds import CoinbaseL2Feed

        feed = CoinbaseL2Feed(enable_sequence_validation=True, gap_tolerance=2)

        feed._check_sequence_gap(100)

        # Within tolerance
        gap1 = feed._check_sequence_gap(102)
        assert gap1 is False
        assert feed._last_seq == 102

        # Exceeds tolerance
        gap2 = feed._check_sequence_gap(106)
        assert gap2 is True
        assert feed._total_gaps == 1

    def test_get_gap_metrics(self):
        """Should retrieve gap metrics."""
        from core.indicators.cex_feeds import CoinbaseL2Feed

        feed = CoinbaseL2Feed(enable_sequence_validation=True)

        feed._check_sequence_gap(100)
        feed._check_sequence_gap(101)  # Normal
        feed._check_sequence_gap(105)  # Gap of 3
        feed._check_sequence_gap(106)  # Normal
        feed._check_sequence_gap(110)  # Gap of 3

        metrics = feed.get_gap_metrics()
        assert metrics["total_gaps"] == 2
        assert metrics["gap_sizes"] == [3, 3]
        assert metrics["average_gap_size"] == 3.0
        assert metrics["last_gap_time"] is not None

    def test_disabled_validation_no_tracking(self):
        """Disabled validation should not track gaps."""
        from core.indicators.cex_feeds import CoinbaseL2Feed

        feed = CoinbaseL2Feed(enable_sequence_validation=False)

        feed._check_sequence_gap(100)
        gap = feed._check_sequence_gap(200)

        assert gap is False
        assert feed._last_seq is None  # Not tracking


@pytest.mark.integration
class TestSequenceGapIntegration:
    """Integration tests simulating real gap scenarios."""

    @pytest.mark.asyncio
    async def test_websocket_gap_triggers_orderbook_invalidation(self):
        """Simulated scenario: WS gap → reconnect → fresh snapshot."""
        from core.exchange_client.kalshi.kalshi_websocket import (
            KalshiWebSocket,
            WebSocketConfig,
        )

        config = WebSocketConfig(enable_sequence_validation=True)
        ws = KalshiWebSocket(auth=None, config=config)

        reconnect_triggered = []

        # Mock the WebSocket connection
        ws._ws = AsyncMock()
        ws._ws.close = AsyncMock(side_effect=lambda: reconnect_triggered.append(True))

        # Simulate messages
        ws._check_sequence_gap("TEST-1", 100)
        ws._check_sequence_gap("TEST-1", 101)

        # Gap detected
        gap = ws._check_sequence_gap("TEST-1", 110)
        assert gap is True

        # Handle gap (should trigger reconnect)
        await ws._handle_sequence_gap("TEST-1")

        assert len(reconnect_triggered) == 1
        assert ws.get_gap_metrics("TEST-1")["total_gaps"] == 1

    @pytest.mark.asyncio
    async def test_orderbook_manager_with_websocket_gap(self):
        """Simulated scenario: OB manager detects gap from WS deltas."""
        gap_detected = []

        def on_gap(ticker, expected, actual):
            gap_detected.append((ticker, expected, actual))

        manager = OrderBookManager(on_gap=on_gap)

        # Snapshot
        await manager.apply_snapshot(
            "KXBTC-TEST",
            {"yes": [[50, 100], [49, 200]], "no": [[50, 100]], "seq": 100},
        )

        # Normal delta
        result1 = await manager.apply_delta(
            "KXBTC-TEST",
            {"seq": 101, "side": "yes", "price": 51, "delta": 50},
        )
        assert result1 == DeltaResult.APPLIED

        # Gap in sequence (missed 102-109)
        result2 = await manager.apply_delta(
            "KXBTC-TEST",
            {"seq": 110, "side": "yes", "price": 52, "delta": 30},
        )
        assert result2 == DeltaResult.GAP
        assert len(gap_detected) == 1
        assert gap_detected[0] == ("KXBTC-TEST", 102, 110)
