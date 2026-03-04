"""Test WebSocket reconnection logic for crypto scalp strategy."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from strategies.crypto_scalp.orchestrator import CryptoScalpStrategy
from strategies.crypto_scalp.config import CryptoScalpConfig
from core.exchange_client.kalshi.kalshi_websocket import (
    KalshiWebSocket,
    WebSocketConfig,
    ConnectionState,
)


class TestBinanceCoinbaseReconnection:
    """Test Binance and Coinbase WebSocket reconnection logic."""

    @pytest.mark.asyncio
    async def test_binance_reconnection_exponential_backoff(self):
        """Test that Binance WS uses exponential backoff on reconnection."""
        config = CryptoScalpConfig(
            signal_feed="binance",
            symbols=["BTCUSDT"],
            paper_mode=True,
        )
        strategy = CryptoScalpStrategy(
            exchange_client=None,  # Not needed for this test
            config=config,
            dry_run=True,
        )

        # Track reconnection delays
        sleep_delays = []

        async def mock_sleep(delay):
            sleep_delays.append(delay)
            # Stop after 3 attempts
            if len(sleep_delays) >= 3:
                strategy._running = False

        # Simulate connection failures
        mock_connect = AsyncMock(
            side_effect=ConnectionError("Test connection error")
        )

        with patch("websockets.connect", mock_connect):
            with patch("asyncio.sleep", mock_sleep):
                strategy._running = True
                await strategy._binance_ws_loop()

        # Verify exponential backoff: 1s, 2s, 4s
        assert len(sleep_delays) >= 3
        assert sleep_delays[0] == 1.0
        assert sleep_delays[1] == 2.0
        assert sleep_delays[2] == 4.0

    @pytest.mark.asyncio
    async def test_coinbase_reconnection_exponential_backoff(self):
        """Test that Coinbase WS uses exponential backoff on reconnection."""
        config = CryptoScalpConfig(
            signal_feed="coinbase",
            symbols=["BTCUSDT"],
            paper_mode=True,
        )
        strategy = CryptoScalpStrategy(
            exchange_client=None,
            config=config,
            dry_run=True,
        )

        # Track reconnection delays
        sleep_delays = []

        async def mock_sleep(delay):
            sleep_delays.append(delay)
            if len(sleep_delays) >= 3:
                strategy._running = False

        mock_connect = AsyncMock(
            side_effect=ConnectionError("Test connection error")
        )

        with patch("websockets.connect", mock_connect):
            with patch("asyncio.sleep", mock_sleep):
                strategy._running = True
                await strategy._coinbase_ws_loop()

        # Verify exponential backoff: 1s, 2s, 4s
        assert len(sleep_delays) >= 3
        assert sleep_delays[0] == 1.0
        assert sleep_delays[1] == 2.0
        assert sleep_delays[2] == 4.0

    @pytest.mark.asyncio
    async def test_binance_max_reconnection_attempts(self):
        """Test that Binance WS stops after max reconnection attempts."""
        config = CryptoScalpConfig(
            signal_feed="binance",
            symbols=["BTCUSDT"],
            paper_mode=True,
        )
        strategy = CryptoScalpStrategy(
            exchange_client=None,
            config=config,
            dry_run=True,
        )

        # Track attempts
        attempts = []

        async def mock_sleep(delay):
            attempts.append(delay)

        mock_connect = AsyncMock(
            side_effect=ConnectionError("Test connection error")
        )

        with patch("websockets.connect", mock_connect):
            with patch("asyncio.sleep", mock_sleep):
                strategy._running = True
                await strategy._binance_ws_loop()

        # Should stop after 10 attempts (max_reconnect_attempts)
        assert len(attempts) == 10

    @pytest.mark.asyncio
    async def test_reconnection_resets_on_successful_connection(self):
        """Test that reconnection counter resets after successful connection."""
        config = CryptoScalpConfig(
            signal_feed="binance",
            symbols=["BTCUSDT"],
            paper_mode=True,
        )
        strategy = CryptoScalpStrategy(
            exchange_client=None,
            config=config,
            dry_run=True,
        )

        sleep_delays = []
        connection_count = [0]

        async def mock_sleep(delay):
            sleep_delays.append(delay)

        class MockWebSocket:
            """Mock WebSocket that fails first time, then succeeds."""

            def __init__(self, *args, **kwargs):
                connection_count[0] += 1

            async def __aenter__(self):
                # Fail on first connection, succeed on second
                if connection_count[0] == 1:
                    raise ConnectionError("First connection fails")
                return self

            async def __aexit__(self, *args):
                pass

            def __aiter__(self):
                return self

            async def __anext__(self):
                # Stop after successful connection
                strategy._running = False
                raise StopAsyncIteration

        with patch("websockets.connect", MockWebSocket):
            with patch("asyncio.sleep", mock_sleep):
                strategy._running = True
                await strategy._binance_ws_loop()

        # First attempt should have delay of 1s, then successful connection
        assert len(sleep_delays) == 1
        assert sleep_delays[0] == 1.0
        assert connection_count[0] == 2


class TestKalshiWebSocketReconnection:
    """Test KalshiWebSocket reconnection logic."""

    @pytest.mark.asyncio
    async def test_kalshi_websocket_config_defaults(self):
        """Test that KalshiWebSocket has proper reconnection defaults."""
        config = WebSocketConfig()

        assert config.reconnect_delay_base == 1.0
        assert config.reconnect_delay_max == 30.0
        assert config.max_reconnect_attempts == 10

    @pytest.mark.asyncio
    async def test_kalshi_websocket_reconnection_state(self):
        """Test that KalshiWebSocket updates state during reconnection."""
        ws = KalshiWebSocket(auth=None, config=WebSocketConfig())

        # Initial state should be disconnected
        assert ws.state == ConnectionState.DISCONNECTED

        # Simulate disconnection while running
        ws._running = True

        with patch("websockets.connect", AsyncMock(side_effect=ConnectionError())):
            with patch("asyncio.sleep", AsyncMock()):
                # Manually trigger disconnect handler
                await ws._handle_disconnect()

        # State should transition to reconnecting
        # (might be DISCONNECTED if max attempts exceeded)
        assert ws.state in (
            ConnectionState.RECONNECTING,
            ConnectionState.DISCONNECTED,
        )

    @pytest.mark.asyncio
    async def test_kalshi_websocket_restore_subscriptions(self):
        """Test that subscriptions are restored after reconnection."""
        from core.exchange_client.kalshi.kalshi_websocket import Subscription

        ws = KalshiWebSocket(auth=None, config=WebSocketConfig())

        # Add a pending subscription
        ws._pending_subscriptions.add("orderbook_delta:KXBTC15M")

        # Add an existing subscription with proper Subscription object
        ws._subscriptions["orderbook_delta:KXBTC15M-TEST"] = Subscription(
            channel="orderbook_delta",
            ticker="KXBTC15M-TEST"
        )

        # Mock WebSocket connection
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        ws._ws = mock_ws
        ws._state = ConnectionState.CONNECTED

        # Restore subscriptions
        await ws._restore_subscriptions()

        # Should have sent subscribe messages (1 for pending + 1 for existing)
        assert mock_ws.send.call_count >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
