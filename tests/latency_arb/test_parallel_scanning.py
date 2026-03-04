"""Tests for parallel market scanning optimization."""

import asyncio
import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategies.crypto_latency.config import CryptoLatencyConfig
from strategies.crypto_latency.kalshi_scanner import KalshiCryptoScanner


class TestParallelScanning:
    """Test parallel market fetching."""

    @pytest.fixture
    def mock_client(self):
        """Mock Kalshi client with async _request."""
        client = MagicMock()
        # Make _request an async function
        client._request = AsyncMock()
        return client

    @pytest.fixture
    def config(self):
        """Scanner config with multiple symbols."""
        return CryptoLatencyConfig(
            symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            scan_interval_sec=60,
        )

    @pytest.fixture
    def scanner(self, mock_client, config):
        """Scanner instance."""
        return KalshiCryptoScanner(mock_client, config)

    def test_parallel_series_fetching(self, scanner, mock_client):
        """Verify that multiple series are fetched in parallel."""
        # Create expiration time 8 minutes in the future (within valid window)
        expiration = (datetime.utcnow() + timedelta(minutes=8)).isoformat() + "Z"

        # Mock response for each series
        mock_response = {
            "markets": [
                {
                    "ticker": "KXBTC15M-26JAN030000",
                    "title": "Bitcoin above $50000?",
                    "close_time": expiration,
                    "yes_bid": 45,
                    "yes_ask": 55,
                    "no_bid": 45,
                    "no_ask": 55,
                    "volume": 100,
                    "open_interest": 50,
                    "floor_strike": 50000.0,
                }
            ]
        }

        # Configure mock to return response after 100ms delay
        async def delayed_response(*args, **kwargs):
            await asyncio.sleep(0.1)  # 100ms delay
            return mock_response

        mock_client._request.side_effect = delayed_response

        # Measure scan time
        start = time.time()
        markets = scanner.scan(force=True)
        elapsed = time.time() - start

        # Should have called _request 3 times (BTC, ETH, SOL)
        assert mock_client._request.call_count == 3

        # Parallel execution should take ~100ms, not 300ms
        # Allow 50ms margin for overhead
        assert elapsed < 0.2, f"Expected <200ms, got {elapsed*1000:.0f}ms (should be parallel)"

        # Sequential would take 300ms+, so anything under 200ms proves parallelization
        assert len(markets) == 3  # One market per series

    def test_handles_partial_failures(self, scanner, mock_client):
        """Verify graceful handling when some series fail."""
        # Create expiration time 8 minutes in the future
        expiration = (datetime.utcnow() + timedelta(minutes=8)).isoformat() + "Z"

        responses = [
            # BTC succeeds
            {
                "markets": [
                    {
                        "ticker": "KXBTC15M-26JAN030000",
                        "title": "Bitcoin above $50000?",
                        "close_time": expiration,
                        "yes_bid": 45,
                        "yes_ask": 55,
                        "no_bid": 45,
                        "no_ask": 55,
                        "floor_strike": 50000.0,
                    }
                ]
            },
            # ETH fails
            Exception("Network error"),
            # SOL succeeds
            {
                "markets": [
                    {
                        "ticker": "KXSOL15M-26JAN030000",
                        "title": "Solana above $100?",
                        "close_time": expiration,
                        "yes_bid": 40,
                        "yes_ask": 60,
                        "no_bid": 40,
                        "no_ask": 60,
                        "floor_strike": 100.0,
                    }
                ]
            },
        ]

        # Configure mock to return different responses
        async def get_response(*args, **kwargs):
            series = kwargs.get("params", {}).get("series_ticker", "")
            if "BTC" in series:
                return responses[0]
            elif "ETH" in series:
                raise responses[1]
            else:
                return responses[2]

        mock_client._request.side_effect = get_response

        # Should succeed despite ETH failure
        markets = scanner.scan(force=True)

        # Should get 2 markets (BTC and SOL)
        assert len(markets) == 2
        tickers = [m.ticker for m in markets]
        assert "KXBTC15M-26JAN030000" in tickers
        assert "KXSOL15M-26JAN030000" in tickers

    def test_refresh_prices_parallel(self, scanner, mock_client):
        """Verify refresh_prices fetches multiple series in parallel."""
        # Setup cached markets from different series
        scanner._markets = {
            "KXBTC15M-26JAN030000": MagicMock(ticker="KXBTC15M-26JAN030000"),
            "KXETH15M-26JAN030000": MagicMock(ticker="KXETH15M-26JAN030000"),
            "KXSOL15M-26JAN030000": MagicMock(ticker="KXSOL15M-26JAN030000"),
        }

        mock_response = {
            "markets": [
                {
                    "ticker": "KXBTC15M-26JAN030000",
                    "yes_bid": 45,
                    "yes_ask": 55,
                    "no_bid": 45,
                    "no_ask": 55,
                    "floor_strike": 50000.0,
                }
            ]
        }

        async def delayed_response(*args, **kwargs):
            await asyncio.sleep(0.1)
            return mock_response

        mock_client._request.side_effect = delayed_response

        start = time.time()
        scanner.refresh_prices()
        elapsed = time.time() - start

        # Should call _request 3 times (once per series)
        assert mock_client._request.call_count == 3

        # Should complete in ~100ms (parallel), not 300ms (sequential)
        assert elapsed < 0.2, f"Expected <200ms, got {elapsed*1000:.0f}ms"

    def test_respects_cache_interval(self, scanner, mock_client):
        """Verify scan respects cache interval."""
        mock_response = {"markets": []}
        mock_client._request.return_value = mock_response

        # First scan
        markets1 = scanner.scan(force=False)
        call_count1 = mock_client._request.call_count

        # Second scan immediately (should use cache)
        markets2 = scanner.scan(force=False)
        call_count2 = mock_client._request.call_count

        # Should not have made additional API calls
        assert call_count2 == call_count1

        # Force scan should bypass cache
        markets3 = scanner.scan(force=True)
        call_count3 = mock_client._request.call_count

        assert call_count3 > call_count2
