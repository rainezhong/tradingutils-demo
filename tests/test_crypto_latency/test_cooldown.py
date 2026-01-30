"""Tests for market cooldown functionality."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from src.strategies.crypto_latency.config import CryptoLatencyConfig
from src.strategies.crypto_latency.kalshi_executor import (
    KalshiExecutor,
    KalshiOpportunity,
    KalshiPosition,
)
from src.strategies.crypto_latency.kalshi_scanner import KalshiCryptoMarket


@pytest.fixture
def config():
    """Create test configuration."""
    return CryptoLatencyConfig(
        market_cooldown_enabled=True,
        market_cooldown_mode="lifetime",
        market_cooldown_duration_sec=300,
        min_time_to_expiry_sec=120,
    )


@pytest.fixture
def mock_client():
    """Create mock Kalshi client."""
    client = MagicMock()
    client.place_order.return_value = {"order": {"order_id": "test-order-123"}}
    return client


@pytest.fixture
def executor(config, mock_client):
    """Create executor instance."""
    return KalshiExecutor(client=mock_client, config=config)


@pytest.fixture
def mock_market():
    """Create a mock Kalshi market."""
    market = MagicMock(spec=KalshiCryptoMarket)
    market.ticker = "KXBTC-25JAN28-50000"
    market.asset = "BTC"
    market.title = "Will BTC be above $50000?"
    market.strike_price = 50000.0
    market.expiration_time = datetime.utcnow() + timedelta(minutes=10)
    market.time_to_expiry_sec = 600
    market.quote_age_ms = 100.0  # Fresh quote for tests
    market.yes_bid = 40
    market.yes_ask = 42
    market.yes_mid = 0.41
    market.no_bid = 58
    market.no_ask = 60
    market.no_mid = 0.59
    return market


class TestCooldownPreventsReentry:
    """Test that cooldown prevents re-entering a market."""

    def test_market_not_cooled_initially(self, executor, mock_market):
        """Markets should not be cooled initially."""
        assert executor.is_market_cooled(mock_market.ticker) is False

    def test_register_cooldown_lifetime_mode(self, executor, mock_market):
        """Registering cooldown in lifetime mode uses market expiry."""
        executor.register_cooldown(mock_market.ticker, mock_market.expiration_time)

        assert executor.is_market_cooled(mock_market.ticker) is True

    def test_cooled_market_blocked_from_execution(self, executor, mock_market):
        """Cooled markets should be blocked in pre-execution checks."""
        # Register cooldown
        executor.register_cooldown(mock_market.ticker, mock_market.expiration_time)

        # Create opportunity
        opportunity = KalshiOpportunity(
            market=mock_market,
            side="yes",
            spot_price=55000.0,
            implied_prob=0.65,
            market_prob=0.40,
            edge=0.25,
            confidence=0.8,
            recommended_price=42,
            recommended_size=5,
        )

        # Attempt execution
        result = executor.execute(opportunity)

        assert result.success is False
        assert "cooldown" in result.error.lower()

    def test_cooldown_expires_after_market_expiry(self, executor, mock_market):
        """Cooldown should expire after market expiry time."""
        # Set expiry to past
        past_expiry = datetime.utcnow() - timedelta(seconds=1)
        executor.register_cooldown(mock_market.ticker, past_expiry)

        # Should no longer be cooled
        assert executor.is_market_cooled(mock_market.ticker) is False

    def test_cleanup_expired_cooldowns(self, executor):
        """cleanup_expired_cooldowns should remove expired entries."""
        now = datetime.utcnow()

        # Add some cooldowns
        executor._cooled_markets = {
            "EXPIRED-1": now - timedelta(seconds=10),
            "EXPIRED-2": now - timedelta(seconds=5),
            "ACTIVE": now + timedelta(minutes=5),
        }

        removed = executor.cleanup_expired_cooldowns()

        assert removed == 2
        assert "EXPIRED-1" not in executor._cooled_markets
        assert "EXPIRED-2" not in executor._cooled_markets
        assert "ACTIVE" in executor._cooled_markets


class TestCooldownModes:
    """Test different cooldown modes."""

    def test_duration_mode_cooldown(self, mock_client):
        """Duration mode should use configured duration instead of market expiry."""
        config = CryptoLatencyConfig(
            market_cooldown_enabled=True,
            market_cooldown_mode="duration",
            market_cooldown_duration_sec=60,  # 1 minute cooldown
        )
        executor = KalshiExecutor(client=mock_client, config=config)

        # Market expires in 10 minutes, but cooldown is only 1 minute
        market_expiry = datetime.utcnow() + timedelta(minutes=10)

        with patch('src.strategies.crypto_latency.kalshi_executor.datetime') as mock_dt:
            mock_dt.utcnow.return_value = datetime.utcnow()
            executor.register_cooldown("TEST-TICKER", market_expiry)

        # Should be cooled
        assert executor.is_market_cooled("TEST-TICKER") is True

        # Check that cooldown time is ~1 minute from now, not 10 minutes
        cooldown_until = executor._cooled_markets["TEST-TICKER"]
        expected_cooldown = datetime.utcnow() + timedelta(seconds=60)

        # Allow 5 second tolerance
        assert abs((cooldown_until - expected_cooldown).total_seconds()) < 5

    def test_cooldown_disabled(self, mock_client, mock_market):
        """When disabled, cooldown should have no effect."""
        config = CryptoLatencyConfig(
            market_cooldown_enabled=False,
        )
        executor = KalshiExecutor(client=mock_client, config=config)

        # Register should do nothing
        executor.register_cooldown(mock_market.ticker, mock_market.expiration_time)

        # Should not be cooled
        assert executor.is_market_cooled(mock_market.ticker) is False


class TestPreExecutionRejectsCooledMarket:
    """Test that pre-execution checks reject cooled markets."""

    def test_pre_execution_rejects_cooled_market(self, executor, mock_market):
        """_pre_execution_checks should reject cooled markets."""
        executor.register_cooldown(mock_market.ticker, mock_market.expiration_time)

        opportunity = KalshiOpportunity(
            market=mock_market,
            side="yes",
            spot_price=55000.0,
            implied_prob=0.65,
            market_prob=0.40,
            edge=0.25,
            confidence=0.8,
            recommended_price=42,
            recommended_size=5,
        )

        error = executor._pre_execution_checks(opportunity)

        assert error is not None
        assert "cooldown" in error.lower()

    def test_pre_execution_allows_non_cooled_market(self, executor, mock_market):
        """_pre_execution_checks should allow non-cooled markets."""
        opportunity = KalshiOpportunity(
            market=mock_market,
            side="yes",
            spot_price=55000.0,
            implied_prob=0.65,
            market_prob=0.40,
            edge=0.25,
            confidence=0.8,
            recommended_price=42,
            recommended_size=5,
        )

        error = executor._pre_execution_checks(opportunity)

        # Should pass cooldown check (may fail other checks)
        if error:
            assert "cooldown" not in error.lower()
