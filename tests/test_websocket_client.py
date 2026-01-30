"""Tests for the Kalshi WebSocket client."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.config import Config
from src.core.websocket_client import (
    Channel,
    ConnectionState,
    KalshiWebSocketClient,
    WebSocketConfig,
)
from src.core.exceptions import WebSocketError


class TestWebSocketConfig(unittest.TestCase):
    """Tests for WebSocketConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = WebSocketConfig()
        self.assertEqual(
            config.url,
            "wss://api.elections.kalshi.com/trade-api/ws/v2",
        )
        self.assertEqual(config.reconnect_delay_base, 1.0)
        self.assertEqual(config.reconnect_delay_max, 60.0)
        self.assertEqual(config.heartbeat_interval, 30.0)
        self.assertEqual(config.message_timeout, 60.0)

    def test_custom_values(self):
        """Test custom configuration values."""
        config = WebSocketConfig(
            url="wss://custom.url/ws",
            reconnect_delay_base=2.0,
            heartbeat_interval=15.0,
        )
        self.assertEqual(config.url, "wss://custom.url/ws")
        self.assertEqual(config.reconnect_delay_base, 2.0)
        self.assertEqual(config.heartbeat_interval, 15.0)


class TestChannel(unittest.TestCase):
    """Tests for Channel enum."""

    def test_channel_values(self):
        """Test channel enum values."""
        self.assertEqual(Channel.ORDERBOOK_DELTA.value, "orderbook_delta")
        self.assertEqual(Channel.TICKER.value, "ticker")
        self.assertEqual(Channel.TRADE.value, "trade")
        self.assertEqual(Channel.FILL.value, "fill")
        self.assertEqual(Channel.ORDER.value, "order")


class TestKalshiWebSocketClient(unittest.TestCase):
    """Tests for KalshiWebSocketClient."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = Config(
            api_key_id="",
            api_private_key_path="",
        )
        self.ws_config = WebSocketConfig(
            reconnect_delay_base=0.1,
            reconnect_delay_max=0.5,
            heartbeat_interval=0.1,
        )

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    def test_initial_state(self):
        """Test initial client state."""
        client = KalshiWebSocketClient(self.config, self.ws_config)

        self.assertEqual(client.state, ConnectionState.DISCONNECTED)
        self.assertFalse(client.is_connected)
        self.assertFalse(client.is_authenticated)

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_connect_success(self, mock_websockets):
        """Test successful connection."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError())
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)

        async def run_test():
            await client.connect()
            self.assertEqual(client.state, ConnectionState.CONNECTED)
            self.assertTrue(client.is_connected)
            await client.disconnect()

        asyncio.run(run_test())

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_connect_failure(self, mock_websockets):
        """Test connection failure."""
        mock_websockets.connect = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        client = KalshiWebSocketClient(self.config, self.ws_config)

        async def run_test():
            with self.assertRaises(WebSocketError):
                await client.connect()
            self.assertEqual(client.state, ConnectionState.DISCONNECTED)

        asyncio.run(run_test())

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_disconnect(self, mock_websockets):
        """Test disconnection."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError())
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)

        async def run_test():
            await client.connect()
            await client.disconnect()
            self.assertEqual(client.state, ConnectionState.DISCONNECTED)
            self.assertFalse(client.is_connected)
            mock_ws.close.assert_called_once()

        asyncio.run(run_test())

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_subscribe(self, mock_websockets):
        """Test subscription."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError())
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)

        async def run_test():
            await client.connect()
            await client.subscribe("orderbook_delta", "TEST-TICKER")

            # Verify send was called with correct message
            mock_ws.send.assert_called()
            call_args = mock_ws.send.call_args[0][0]
            message = json.loads(call_args)

            self.assertEqual(message["cmd"], "subscribe")
            self.assertIn("orderbook_delta", message["params"]["channels"])
            self.assertIn("TEST-TICKER", message["params"]["market_tickers"])

            await client.disconnect()

        asyncio.run(run_test())

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_unsubscribe(self, mock_websockets):
        """Test unsubscription."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError())
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)

        async def run_test():
            await client.connect()
            await client.subscribe("orderbook_delta", "TEST-TICKER")
            await client.unsubscribe("orderbook_delta", "TEST-TICKER")

            # Find unsubscribe call
            calls = [json.loads(c[0][0]) for c in mock_ws.send.call_args_list]
            unsub_calls = [c for c in calls if c.get("cmd") == "unsubscribe"]

            self.assertEqual(len(unsub_calls), 1)
            self.assertIn("orderbook_delta", unsub_calls[0]["params"]["channels"])

            await client.disconnect()

        asyncio.run(run_test())

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_pending_subscription(self, mock_websockets):
        """Test subscription queued when not connected."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError())
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)

        async def run_test():
            # Subscribe before connecting
            await client.subscribe("orderbook_delta", "TEST-TICKER")

            # Connect should restore pending subscription
            await client.connect()
            await asyncio.sleep(0.05)  # Allow async tasks to run

            # Verify subscription was sent
            calls = [json.loads(c[0][0]) for c in mock_ws.send.call_args_list]
            sub_calls = [c for c in calls if c.get("cmd") == "subscribe"]
            self.assertGreaterEqual(len(sub_calls), 1)

            await client.disconnect()

        asyncio.run(run_test())


class TestWebSocketMessageHandling(unittest.TestCase):
    """Tests for WebSocket message handling."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = Config(api_key_id="", api_private_key_path="")
        self.ws_config = WebSocketConfig()

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_orderbook_snapshot_callback(self, mock_websockets):
        """Test orderbook snapshot callback is invoked."""
        received_data = []

        def on_snapshot(ticker, data):
            received_data.append((ticker, data))

        snapshot_message = json.dumps({
            "type": "orderbook_snapshot",
            "sid": "TEST-TICKER",
            "msg": {
                "yes": [[50, 100]],
                "no": [[50, 100]],
                "seq": 1000,
            },
        })

        mock_ws = AsyncMock()
        messages = [snapshot_message]
        call_count = [0]

        async def mock_recv():
            if call_count[0] < len(messages):
                msg = messages[call_count[0]]
                call_count[0] += 1
                return msg
            raise asyncio.CancelledError()

        mock_ws.recv = mock_recv
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)
        client.on_orderbook_snapshot(on_snapshot)

        async def run_test():
            await client.connect()
            await asyncio.sleep(0.1)  # Allow message processing
            await client.disconnect()

        asyncio.run(run_test())

        self.assertEqual(len(received_data), 1)
        self.assertEqual(received_data[0][0], "TEST-TICKER")

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_orderbook_delta_callback(self, mock_websockets):
        """Test orderbook delta callback is invoked."""
        received_data = []

        def on_delta(ticker, data):
            received_data.append((ticker, data))

        delta_message = json.dumps({
            "type": "orderbook_delta",
            "sid": "TEST-TICKER",
            "msg": {
                "side": "yes",
                "price": 50,
                "delta": 10,
                "seq": 1001,
            },
        })

        mock_ws = AsyncMock()
        messages = [delta_message]
        call_count = [0]

        async def mock_recv():
            if call_count[0] < len(messages):
                msg = messages[call_count[0]]
                call_count[0] += 1
                return msg
            raise asyncio.CancelledError()

        mock_ws.recv = mock_recv
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)
        client.on_orderbook_delta(on_delta)

        async def run_test():
            await client.connect()
            await asyncio.sleep(0.1)
            await client.disconnect()

        asyncio.run(run_test())

        self.assertEqual(len(received_data), 1)
        self.assertEqual(received_data[0][0], "TEST-TICKER")

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_trade_callback(self, mock_websockets):
        """Test trade callback is invoked."""
        received_data = []

        def on_trade(ticker, data):
            received_data.append((ticker, data))

        trade_message = json.dumps({
            "type": "trade",
            "sid": "TEST-TICKER",
            "msg": {
                "price": 55,
                "size": 10,
                "side": "yes",
            },
        })

        mock_ws = AsyncMock()
        messages = [trade_message]
        call_count = [0]

        async def mock_recv():
            if call_count[0] < len(messages):
                msg = messages[call_count[0]]
                call_count[0] += 1
                return msg
            raise asyncio.CancelledError()

        mock_ws.recv = mock_recv
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)
        client.on_trade(on_trade)

        async def run_test():
            await client.connect()
            await asyncio.sleep(0.1)
            await client.disconnect()

        asyncio.run(run_test())

        self.assertEqual(len(received_data), 1)

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_error_callback(self, mock_websockets):
        """Test error callback is invoked on server error."""
        received_errors = []

        def on_error(error):
            received_errors.append(error)

        error_message = json.dumps({
            "type": "error",
            "msg": {"message": "Invalid subscription"},
        })

        mock_ws = AsyncMock()
        messages = [error_message]
        call_count = [0]

        async def mock_recv():
            if call_count[0] < len(messages):
                msg = messages[call_count[0]]
                call_count[0] += 1
                return msg
            raise asyncio.CancelledError()

        mock_ws.recv = mock_recv
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)
        client.on_error(on_error)

        async def run_test():
            await client.connect()
            await asyncio.sleep(0.1)
            await client.disconnect()

        asyncio.run(run_test())

        self.assertEqual(len(received_errors), 1)
        self.assertIsInstance(received_errors[0], WebSocketError)

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_invalid_json_handling(self, mock_websockets):
        """Test handling of invalid JSON messages."""
        mock_ws = AsyncMock()
        messages = ["not valid json", '{"type": "ticker", "sid": "TEST"}']
        call_count = [0]

        async def mock_recv():
            if call_count[0] < len(messages):
                msg = messages[call_count[0]]
                call_count[0] += 1
                return msg
            raise asyncio.CancelledError()

        mock_ws.recv = mock_recv
        mock_ws.send = AsyncMock()
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        client = KalshiWebSocketClient(self.config, self.ws_config)

        async def run_test():
            # Should not raise, just log warning
            await client.connect()
            await asyncio.sleep(0.1)
            await client.disconnect()

        # Should complete without error
        asyncio.run(run_test())


class TestWebSocketContextManager(unittest.TestCase):
    """Tests for async context manager support."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = Config(api_key_id="", api_private_key_path="")
        self.ws_config = WebSocketConfig()

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_async_context_manager(self, mock_websockets):
        """Test async context manager connects and disconnects."""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.CancelledError())
        mock_websockets.connect = AsyncMock(return_value=mock_ws)

        async def run_test():
            async with KalshiWebSocketClient(
                self.config, self.ws_config
            ) as client:
                self.assertEqual(client.state, ConnectionState.CONNECTED)

            mock_ws.close.assert_called()

        asyncio.run(run_test())


class TestWebSocketReconnection(unittest.TestCase):
    """Tests for reconnection logic."""

    def setUp(self):
        """Set up test fixtures."""
        self.config = Config(api_key_id="", api_private_key_path="")
        self.ws_config = WebSocketConfig(
            reconnect_delay_base=0.05,
            reconnect_delay_max=0.2,
            max_reconnect_attempts=3,
        )

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    @patch("src.core.websocket_client.websockets")
    def test_reconnection_attempts_exhausted(self, mock_websockets):
        """Test error after max reconnection attempts."""
        from websockets.exceptions import ConnectionClosed

        errors_received = []

        def on_error(error):
            errors_received.append(error)

        # Fail all reconnection attempts
        mock_websockets.connect = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        mock_ws = AsyncMock()

        # First connection succeeds, then fails
        connect_count = [0]

        async def mock_connect(*args, **kwargs):
            connect_count[0] += 1
            if connect_count[0] == 1:
                return mock_ws
            raise Exception("Connection refused")

        # Simulate connection closed after first message
        async def mock_recv():
            raise ConnectionClosed(1006, "Connection lost")

        mock_ws.recv = mock_recv
        mock_websockets.connect = mock_connect

        client = KalshiWebSocketClient(self.config, self.ws_config)
        client.on_error(on_error)

        async def run_test():
            try:
                await client.connect()
                await asyncio.sleep(1)  # Wait for reconnection attempts
            except Exception:
                pass
            finally:
                client._running = False
                await client.disconnect()

        asyncio.run(run_test())

        # Should have received max reconnection error
        max_reconnect_errors = [
            e for e in errors_received
            if "Max reconnection" in str(e)
        ]
        self.assertGreaterEqual(len(max_reconnect_errors), 1)


class TestWebSocketCallbackRegistration(unittest.TestCase):
    """Tests for callback registration."""

    @patch("src.core.websocket_client.WEBSOCKETS_AVAILABLE", True)
    def test_multiple_callbacks(self):
        """Test multiple callbacks can be registered."""
        config = Config(api_key_id="", api_private_key_path="")
        client = KalshiWebSocketClient(config)

        callbacks_called = {"cb1": False, "cb2": False}

        def cb1(ticker, data):
            callbacks_called["cb1"] = True

        def cb2(ticker, data):
            callbacks_called["cb2"] = True

        client.on_orderbook_delta(cb1)
        client.on_orderbook_delta(cb2)

        # Manually invoke callbacks
        for callback in client._orderbook_delta_callbacks:
            callback("TEST", {})

        self.assertTrue(callbacks_called["cb1"])
        self.assertTrue(callbacks_called["cb2"])


if __name__ == "__main__":
    unittest.main()
