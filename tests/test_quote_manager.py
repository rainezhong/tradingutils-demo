"""Tests for QuoteManager and MockAPIClient."""

import pytest
from datetime import datetime, timezone, timedelta

from src.core.models import ValidationError
from src.market_making.models import Quote, Fill, MarketState
from src.market_making.interfaces import OrderError
from src.execution.quote_manager import QuoteManager, TrackedQuote, RetryConfig
from src.execution.mock_api_client import MockAPIClient, MockOrder


# =============================================================================
# MockAPIClient Tests
# =============================================================================


class TestMockAPIClient:
    """Tests for MockAPIClient."""

    def test_place_order_success(self):
        """Test successful order placement."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)

        assert order_id is not None
        assert order_id.startswith("mock_")

        order = client.get_order(order_id)
        assert order is not None
        assert order.ticker == "TICKER"
        assert order.side == "BID"
        assert order.price == 0.45
        assert order.size == 20
        assert order.status == "open"

    def test_place_order_failure(self):
        """Test order placement failure simulation."""
        client = MockAPIClient()
        client.fail_next_place = True

        with pytest.raises(OrderError, match="Simulated order placement failure"):
            client.place_order("TICKER", "BID", 0.45, 20)

        # Flag should be reset
        assert client.fail_next_place is False

    def test_cancel_order_success(self):
        """Test successful order cancellation."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)

        result = client.cancel_order(order_id)
        assert result is True

        order = client.get_order(order_id)
        assert order.status == "cancelled"

    def test_cancel_order_not_found(self):
        """Test cancelling non-existent order."""
        client = MockAPIClient()
        result = client.cancel_order("nonexistent")
        assert result is False

    def test_cancel_order_failure(self):
        """Test cancel failure simulation."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        client.fail_next_cancel = True

        result = client.cancel_order(order_id)
        assert result is False
        assert client.fail_next_cancel is False

    def test_cancel_already_filled(self):
        """Test cancelling already filled order."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        client.simulate_fill(order_id)

        result = client.cancel_order(order_id)
        assert result is False

    def test_get_order_status_open(self):
        """Test getting status of open order."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)

        status = client.get_order_status(order_id)
        assert status["status"] == "open"
        assert status["filled_size"] == 0
        assert status["remaining_size"] == 20
        assert status["avg_fill_price"] is None

    def test_get_order_status_partial(self):
        """Test getting status of partially filled order."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        client.simulate_fill(order_id, 10)

        status = client.get_order_status(order_id)
        assert status["status"] == "partial"
        assert status["filled_size"] == 10
        assert status["remaining_size"] == 10
        assert status["avg_fill_price"] == 0.45

    def test_get_order_status_filled(self):
        """Test getting status of fully filled order."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        client.simulate_fill(order_id)

        status = client.get_order_status(order_id)
        assert status["status"] == "filled"
        assert status["filled_size"] == 20
        assert status["remaining_size"] == 0

    def test_get_order_status_not_found(self):
        """Test getting status of non-existent order."""
        client = MockAPIClient()
        with pytest.raises(OrderError, match="Order not found"):
            client.get_order_status("nonexistent")

    def test_get_order_status_failure(self):
        """Test status check failure simulation."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        client.fail_next_status = True

        with pytest.raises(OrderError, match="Simulated status check failure"):
            client.get_order_status(order_id)

    def test_simulate_fill_partial(self):
        """Test partial fill simulation."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)

        fill = client.simulate_fill(order_id, 10)
        assert fill.order_id == order_id
        assert fill.ticker == "TICKER"
        assert fill.side == "BID"
        assert fill.price == 0.45
        assert fill.size == 10

        order = client.get_order(order_id)
        assert order.filled_size == 10
        assert order.status == "partial"

    def test_simulate_fill_full(self):
        """Test full fill simulation."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)

        fill = client.simulate_fill(order_id)
        assert fill.size == 20

        order = client.get_order(order_id)
        assert order.filled_size == 20
        assert order.status == "filled"

    def test_simulate_fill_with_custom_price(self):
        """Test fill with different price."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)

        fill = client.simulate_fill(order_id, 10, fill_price=0.44)
        assert fill.price == 0.44

    def test_simulate_fill_updates_position(self):
        """Test that fills update positions correctly."""
        client = MockAPIClient()

        # Buy order
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        client.simulate_fill(order_id)

        positions = client.get_positions()
        assert positions["TICKER"] == 20

        # Sell order
        order_id = client.place_order("TICKER", "ASK", 0.50, 10)
        client.simulate_fill(order_id)

        positions = client.get_positions()
        assert positions["TICKER"] == 10

    def test_get_fills(self):
        """Test getting fill history."""
        client = MockAPIClient()

        order1 = client.place_order("TICKER1", "BID", 0.45, 20)
        order2 = client.place_order("TICKER2", "BID", 0.50, 30)

        client.simulate_fill(order1)
        client.simulate_fill(order2)

        fills = client.get_fills()
        assert len(fills) == 2

        # Filter by ticker
        fills = client.get_fills("TICKER1")
        assert len(fills) == 1
        assert fills[0].ticker == "TICKER1"

    def test_rate_limit(self):
        """Test rate limiting simulation."""
        client = MockAPIClient()
        client.set_rate_limit(1.0)

        with pytest.raises(OrderError, match="Rate limited"):
            client.place_order("TICKER", "BID", 0.45, 20)

        client.clear_rate_limit()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        assert order_id is not None

    def test_reset(self):
        """Test resetting mock state."""
        client = MockAPIClient()
        order_id = client.place_order("TICKER", "BID", 0.45, 20)
        client.simulate_fill(order_id)
        client.fail_next_place = True

        client.reset()

        assert client.get_positions() == {}
        assert client.get_fills() == []
        assert client.fail_next_place is False

    def test_set_market_data(self):
        """Test setting market data."""
        client = MockAPIClient()
        state = MarketState(
            ticker="TICKER",
            timestamp=datetime.now(timezone.utc),
            best_bid=0.45,
            best_ask=0.50,
            mid_price=0.475,
            bid_size=100,
            ask_size=100,
        )
        client.set_market_data(state)

        result = client.get_market_data("TICKER")
        assert result.ticker == "TICKER"
        assert result.best_bid == 0.45


# =============================================================================
# QuoteManager Tests
# =============================================================================


class TestQuoteManager:
    """Tests for QuoteManager."""

    def test_place_quote_success(self):
        """Test successful quote placement."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        assert placed.order_id is not None
        assert placed.ticker == "TICKER"
        assert placed.side == "BID"
        assert placed.price == 0.45
        assert placed.size == 20
        assert manager.active_order_count == 1

    def test_place_quote_already_has_order_id(self):
        """Test placing quote that already has order_id."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20, order_id="existing")
        placed = manager.place_quote(quote)

        # Should return same quote without placing new order
        assert placed.order_id == "existing"
        assert manager.active_order_count == 0

    def test_place_quote_with_retry(self):
        """Test quote placement with retry on failure."""
        client = MockAPIClient()
        config = RetryConfig(max_retries=2, base_delay=0.01)
        manager = QuoteManager(client, retry_config=config)

        # First attempt will fail, second should succeed
        client.fail_next_place = True

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        assert placed.order_id is not None

    def test_place_quote_retry_exhausted(self):
        """Test quote placement fails after retries exhausted."""
        client = MockAPIClient()
        config = RetryConfig(max_retries=1, base_delay=0.01)
        manager = QuoteManager(client, retry_config=config)

        # Set rate limit to cause persistent failure
        client.set_rate_limit(10.0)

        quote = Quote("TICKER", "BID", 0.45, 20)
        with pytest.raises(OrderError):
            manager.place_quote(quote)

    def test_cancel_quote_success(self):
        """Test successful quote cancellation."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        result = manager.cancel_quote(placed.order_id)
        assert result is True
        assert manager.active_order_count == 0

    def test_cancel_quote_not_tracked(self):
        """Test cancelling quote not in tracking."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        result = manager.cancel_quote("nonexistent")
        assert result is False

    def test_cancel_quote_api_failure(self):
        """Test cancellation with API failure."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        client.fail_next_cancel = True
        result = manager.cancel_quote(placed.order_id)

        assert result is False
        # Order should still be tracked
        assert manager.active_order_count == 1

    def test_check_fills_no_fills(self):
        """Test check_fills with no fills."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        manager.place_quote(quote)

        fills = manager.check_fills()
        assert len(fills) == 0
        assert manager.active_order_count == 1

    def test_check_fills_partial_fill(self):
        """Test check_fills with partial fill."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        client.simulate_fill(placed.order_id, 10)
        fills = manager.check_fills()

        assert len(fills) == 1
        assert fills[0].size == 10
        assert manager.active_order_count == 1  # Still tracked (partial)

        tracked = manager.get_tracked_quote(placed.order_id)
        assert tracked.filled_size == 10
        assert tracked.status == "partial"

    def test_check_fills_full_fill(self):
        """Test check_fills with full fill."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        client.simulate_fill(placed.order_id)
        fills = manager.check_fills()

        assert len(fills) == 1
        assert fills[0].size == 20
        assert manager.active_order_count == 0  # Removed from tracking

    def test_check_fills_multiple_orders(self):
        """Test check_fills with multiple orders."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote1 = Quote("TICKER1", "BID", 0.45, 20)
        quote2 = Quote("TICKER2", "ASK", 0.55, 30)

        placed1 = manager.place_quote(quote1)
        placed2 = manager.place_quote(quote2)

        client.simulate_fill(placed1.order_id)
        client.simulate_fill(placed2.order_id, 15)

        fills = manager.check_fills()

        assert len(fills) == 2
        assert manager.active_order_count == 1  # Only partial still tracked

    def test_check_fills_incremental(self):
        """Test that check_fills only returns new fills."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        # First partial fill
        client.simulate_fill(placed.order_id, 5)
        fills1 = manager.check_fills()
        assert len(fills1) == 1
        assert fills1[0].size == 5

        # Second partial fill
        client.simulate_fill(placed.order_id, 5)
        fills2 = manager.check_fills()
        assert len(fills2) == 1
        assert fills2[0].size == 5

    def test_check_fills_status_error(self):
        """Test check_fills handles status check errors gracefully."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        manager.place_quote(quote)

        client.fail_next_status = True
        fills = manager.check_fills()

        # Should not crash, just log warning
        assert len(fills) == 0
        assert manager.active_order_count == 1

    def test_get_active_quotes_all(self):
        """Test getting all active quotes."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote1 = Quote("TICKER1", "BID", 0.45, 20)
        quote2 = Quote("TICKER2", "ASK", 0.55, 30)

        manager.place_quote(quote1)
        manager.place_quote(quote2)

        quotes = manager.get_active_quotes()
        assert len(quotes) == 2

    def test_get_active_quotes_filtered(self):
        """Test getting active quotes filtered by ticker."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote1 = Quote("TICKER1", "BID", 0.45, 20)
        quote2 = Quote("TICKER2", "ASK", 0.55, 30)

        manager.place_quote(quote1)
        manager.place_quote(quote2)

        quotes = manager.get_active_quotes("TICKER1")
        assert len(quotes) == 1
        assert quotes[0].ticker == "TICKER1"

    def test_cancel_all(self):
        """Test cancelling all quotes."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote1 = Quote("TICKER1", "BID", 0.45, 20)
        quote2 = Quote("TICKER2", "ASK", 0.55, 30)

        manager.place_quote(quote1)
        manager.place_quote(quote2)

        cancelled = manager.cancel_all()
        assert cancelled == 2
        assert manager.active_order_count == 0

    def test_cancel_all_filtered(self):
        """Test cancelling quotes filtered by ticker."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote1 = Quote("TICKER1", "BID", 0.45, 20)
        quote2 = Quote("TICKER2", "ASK", 0.55, 30)

        manager.place_quote(quote1)
        manager.place_quote(quote2)

        cancelled = manager.cancel_all("TICKER1")
        assert cancelled == 1
        assert manager.active_order_count == 1

        quotes = manager.get_active_quotes()
        assert quotes[0].ticker == "TICKER2"

    def test_get_active_order_ids(self):
        """Test getting list of active order IDs."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote1 = Quote("TICKER1", "BID", 0.45, 20)
        quote2 = Quote("TICKER2", "ASK", 0.55, 30)

        placed1 = manager.place_quote(quote1)
        placed2 = manager.place_quote(quote2)

        order_ids = manager.get_active_order_ids()
        assert len(order_ids) == 2
        assert placed1.order_id in order_ids
        assert placed2.order_id in order_ids

    def test_get_tracked_quote(self):
        """Test getting tracked quote details."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        quote = Quote("TICKER", "BID", 0.45, 20)
        placed = manager.place_quote(quote)

        tracked = manager.get_tracked_quote(placed.order_id)
        assert tracked is not None
        assert tracked.quote.order_id == placed.order_id
        assert tracked.placement_time is not None
        assert tracked.status == "open"

    def test_get_tracked_quote_not_found(self):
        """Test getting tracked quote that doesn't exist."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        tracked = manager.get_tracked_quote("nonexistent")
        assert tracked is None


# =============================================================================
# RetryConfig Tests
# =============================================================================


class TestRetryConfig:
    """Tests for RetryConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 0.5
        assert config.max_delay == 30.0
        assert config.exponential_base == 2.0

    def test_custom_config(self):
        """Test custom configuration values."""
        config = RetryConfig(
            max_retries=5,
            base_delay=1.0,
            max_delay=60.0,
            exponential_base=3.0,
        )
        assert config.max_retries == 5
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0
        assert config.exponential_base == 3.0


# =============================================================================
# Integration Tests
# =============================================================================


class TestQuoteManagerIntegration:
    """Integration tests for QuoteManager workflow."""

    def test_full_order_lifecycle(self):
        """Test complete order lifecycle: place -> partial fill -> full fill."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        # Place quote
        quote = Quote("TICKER", "BID", 0.45, 100)
        placed = manager.place_quote(quote)
        assert manager.active_order_count == 1

        # Partial fill
        client.simulate_fill(placed.order_id, 30)
        fills = manager.check_fills()
        assert len(fills) == 1
        assert fills[0].size == 30
        assert manager.active_order_count == 1

        # More partial fill
        client.simulate_fill(placed.order_id, 50)
        fills = manager.check_fills()
        assert len(fills) == 1
        assert fills[0].size == 50
        assert manager.active_order_count == 1

        # Final fill
        client.simulate_fill(placed.order_id, 20)
        fills = manager.check_fills()
        assert len(fills) == 1
        assert fills[0].size == 20
        assert manager.active_order_count == 0

    def test_multiple_markets(self):
        """Test managing quotes across multiple markets."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        # Place quotes in multiple markets
        markets = ["MARKET_A", "MARKET_B", "MARKET_C"]
        for market in markets:
            bid = Quote(market, "BID", 0.45, 20)
            ask = Quote(market, "ASK", 0.55, 20)
            manager.place_quote(bid)
            manager.place_quote(ask)

        assert manager.active_order_count == 6

        # Fill some quotes
        for quote in manager.get_active_quotes("MARKET_A"):
            client.simulate_fill(quote.order_id)

        manager.check_fills()
        assert manager.active_order_count == 4

        # Cancel remaining
        cancelled = manager.cancel_all()
        assert cancelled == 4
        assert manager.active_order_count == 0

    def test_rapid_fill_detection(self):
        """Test detecting multiple rapid fills."""
        client = MockAPIClient()
        manager = QuoteManager(client)

        # Place multiple quotes
        quotes = []
        for i in range(5):
            quote = Quote(f"TICKER_{i}", "BID", 0.45, 20)
            placed = manager.place_quote(quote)
            quotes.append(placed)

        # Simulate rapid fills on all
        for placed in quotes:
            client.simulate_fill(placed.order_id)

        # Single check_fills should detect all
        fills = manager.check_fills()
        assert len(fills) == 5
        assert manager.active_order_count == 0
