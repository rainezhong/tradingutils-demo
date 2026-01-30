"""Tests for market-making foundation layer."""

import pytest
from datetime import datetime, timezone

from src.core.models import Snapshot, ValidationError
from src.market_making import (
    # Constants
    MIN_PRICE,
    MAX_PRICE,
    SIDE_BID,
    SIDE_ASK,
    # Models
    MarketState,
    Quote,
    Position,
    Fill,
    # Config
    MarketMakerConfig,
    RiskConfig,
    TradingConfig,
    # Utils
    validate_price,
    validate_spread,
    validate_size,
    validate_side,
    calculate_mid,
    calculate_spread_pct,
    calculate_quote_prices,
    calculate_inventory_skew,
    should_quote,
    cents_to_probability,
    probability_to_cents,
)


# =============================================================================
# MarketState Tests
# =============================================================================

class TestMarketState:
    """Tests for MarketState dataclass."""

    def test_valid_market_state(self):
        """Test creating a valid MarketState."""
        state = MarketState(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            best_bid=0.45,
            best_ask=0.50,
            mid_price=0.475,
            bid_size=100,
            ask_size=150,
        )
        assert state.ticker == "TEST"
        assert state.best_bid == 0.45
        assert state.best_ask == 0.50

    def test_spread_pct_calculation(self):
        """Test spread percentage is calculated correctly."""
        state = MarketState(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            best_bid=0.45,
            best_ask=0.50,
            mid_price=0.475,
            bid_size=100,
            ask_size=100,
        )
        # Spread = 0.05, mid = 0.475
        # spread_pct = 0.05 / 0.475 = 0.1052...
        assert abs(state.spread_pct - 0.1053) < 0.001

    def test_spread_absolute(self):
        """Test absolute spread calculation."""
        state = MarketState(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            best_bid=0.45,
            best_ask=0.50,
            mid_price=0.475,
            bid_size=100,
            ask_size=100,
        )
        assert abs(state.spread_absolute - 0.05) < 1e-9

    def test_invalid_empty_ticker(self):
        """Test validation rejects empty ticker."""
        with pytest.raises(ValidationError, match="ticker cannot be empty"):
            MarketState(
                ticker="",
                timestamp=datetime.now(timezone.utc),
                best_bid=0.45,
                best_ask=0.50,
                mid_price=0.475,
                bid_size=100,
                ask_size=100,
            )

    def test_invalid_bid_ask_order(self):
        """Test validation rejects bid >= ask."""
        with pytest.raises(ValidationError, match="must be < best_ask"):
            MarketState(
                ticker="TEST",
                timestamp=datetime.now(timezone.utc),
                best_bid=0.50,
                best_ask=0.45,
                mid_price=0.475,
                bid_size=100,
                ask_size=100,
            )

    def test_invalid_price_range(self):
        """Test validation rejects prices outside valid range."""
        with pytest.raises(ValidationError, match="must be between"):
            MarketState(
                ticker="TEST",
                timestamp=datetime.now(timezone.utc),
                best_bid=0.00,  # Below MIN_PRICE
                best_ask=0.50,
                mid_price=0.25,
                bid_size=100,
                ask_size=100,
            )

    def test_from_snapshot(self):
        """Test creating MarketState from Snapshot."""
        snapshot = Snapshot(
            ticker="TEST",
            timestamp="2024-01-01T12:00:00+00:00",
            yes_bid=45,
            yes_ask=50,
            orderbook_bid_depth=100,
            orderbook_ask_depth=150,
        )
        state = MarketState.from_snapshot(snapshot)

        assert state.ticker == "TEST"
        assert state.best_bid == 0.45
        assert state.best_ask == 0.50
        assert state.mid_price == 0.475
        assert state.bid_size == 100
        assert state.ask_size == 150

    def test_from_snapshot_missing_data(self):
        """Test from_snapshot raises error when bid/ask missing."""
        snapshot = Snapshot(
            ticker="TEST",
            timestamp="2024-01-01T12:00:00+00:00",
            yes_bid=None,
            yes_ask=50,
        )
        with pytest.raises(ValidationError, match="missing bid/ask"):
            MarketState.from_snapshot(snapshot)

    def test_to_dict(self):
        """Test conversion to dictionary."""
        state = MarketState(
            ticker="TEST",
            timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            best_bid=0.45,
            best_ask=0.50,
            mid_price=0.475,
            bid_size=100,
            ask_size=100,
        )
        d = state.to_dict()

        assert d["ticker"] == "TEST"
        assert d["best_bid"] == 0.45
        assert "spread_pct" in d


# =============================================================================
# Quote Tests
# =============================================================================

class TestQuote:
    """Tests for Quote dataclass."""

    def test_valid_quote(self):
        """Test creating a valid Quote."""
        quote = Quote(
            ticker="TEST",
            side="BID",
            price=0.45,
            size=20,
        )
        assert quote.ticker == "TEST"
        assert quote.side == "BID"
        assert quote.is_bid is True
        assert quote.is_ask is False

    def test_quote_properties(self):
        """Test quote helper properties."""
        quote = Quote(ticker="TEST", side="ASK", price=0.50, size=20)
        assert quote.is_ask is True
        assert quote.is_bid is False
        assert quote.is_submitted is False

        quote.order_id = "ORDER123"
        assert quote.is_submitted is True

    def test_invalid_side(self):
        """Test validation rejects invalid side."""
        with pytest.raises(ValidationError, match="side must be one of"):
            Quote(ticker="TEST", side="SELL", price=0.45, size=20)

    def test_invalid_price(self):
        """Test validation rejects invalid price."""
        with pytest.raises(ValidationError, match="price must be between"):
            Quote(ticker="TEST", side="BID", price=1.5, size=20)

    def test_invalid_size(self):
        """Test validation rejects non-positive size."""
        with pytest.raises(ValidationError, match="size must be positive"):
            Quote(ticker="TEST", side="BID", price=0.45, size=0)


# =============================================================================
# Position Tests
# =============================================================================

class TestPosition:
    """Tests for Position dataclass."""

    def test_valid_position(self):
        """Test creating a valid Position."""
        pos = Position(
            ticker="TEST",
            contracts=50,
            avg_entry_price=0.45,
        )
        assert pos.contracts == 50
        assert pos.is_long is True
        assert pos.is_short is False
        assert pos.is_flat is False

    def test_short_position(self):
        """Test short position properties."""
        pos = Position(
            ticker="TEST",
            contracts=-25,
            avg_entry_price=0.55,
        )
        assert pos.is_short is True
        assert pos.is_long is False
        assert pos.abs_size == 25

    def test_flat_position(self):
        """Test flat position."""
        pos = Position(ticker="TEST", contracts=0, avg_entry_price=0.0)
        assert pos.is_flat is True

    def test_update_unrealized_pnl(self):
        """Test unrealized P&L calculation."""
        pos = Position(ticker="TEST", contracts=100, avg_entry_price=0.45)
        pos.update_unrealized_pnl(0.50)

        # 100 contracts * (0.50 - 0.45) = 5.0
        assert abs(pos.unrealized_pnl - 5.0) < 1e-9

    def test_total_pnl(self):
        """Test total P&L calculation."""
        pos = Position(
            ticker="TEST",
            contracts=50,
            avg_entry_price=0.45,
            unrealized_pnl=2.5,
            realized_pnl=10.0,
        )
        assert pos.total_pnl == 12.5


# =============================================================================
# Fill Tests
# =============================================================================

class TestFill:
    """Tests for Fill dataclass."""

    def test_valid_fill(self):
        """Test creating a valid Fill."""
        fill = Fill(
            order_id="ORDER123",
            ticker="TEST",
            side="BID",
            price=0.45,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )
        assert fill.order_id == "ORDER123"
        assert fill.is_buy is True
        assert fill.is_sell is False

    def test_notional_value(self):
        """Test notional value calculation."""
        fill = Fill(
            order_id="ORDER123",
            ticker="TEST",
            side="BID",
            price=0.45,
            size=20,
            timestamp=datetime.now(timezone.utc),
        )
        # 0.45 * 20 = 9.0
        assert fill.notional_value == 9.0

    def test_invalid_empty_order_id(self):
        """Test validation rejects empty order_id."""
        with pytest.raises(ValidationError, match="order_id cannot be empty"):
            Fill(
                order_id="",
                ticker="TEST",
                side="BID",
                price=0.45,
                size=20,
                timestamp=datetime.now(timezone.utc),
            )


# =============================================================================
# Config Tests
# =============================================================================

class TestMarketMakerConfig:
    """Tests for MarketMakerConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = MarketMakerConfig()
        assert config.target_spread == 0.04
        assert config.quote_size == 20
        assert config.max_position == 50

    def test_custom_config(self):
        """Test custom configuration."""
        config = MarketMakerConfig(
            target_spread=0.06,
            quote_size=30,
            max_position=100,
        )
        assert config.target_spread == 0.06
        assert config.quote_size == 30

    def test_invalid_target_spread(self):
        """Test validation rejects non-positive spread."""
        with pytest.raises(ValueError, match="target_spread must be positive"):
            MarketMakerConfig(target_spread=-0.01)

    def test_from_dict(self):
        """Test creating config from dictionary."""
        data = {"target_spread": 0.05, "quote_size": 25}
        config = MarketMakerConfig.from_dict(data)
        assert config.target_spread == 0.05
        assert config.quote_size == 25


class TestRiskConfig:
    """Tests for RiskConfig."""

    def test_default_config(self):
        """Test default risk configuration."""
        config = RiskConfig()
        assert config.max_position_per_market == 50
        assert config.max_daily_loss == 50.0

    def test_invalid_total_less_than_per_market(self):
        """Test validation rejects total < per_market."""
        with pytest.raises(ValueError, match="max_total_position.*must be >="):
            RiskConfig(
                max_position_per_market=100,
                max_total_position=50,
            )


class TestTradingConfig:
    """Tests for TradingConfig."""

    def test_default_config(self):
        """Test default trading configuration."""
        config = TradingConfig()
        assert config.strategy.target_spread == 0.04
        assert config.risk.max_daily_loss == 50.0

    def test_from_dict(self):
        """Test creating from nested dictionary."""
        data = {
            "strategy": {"target_spread": 0.05},
            "risk": {"max_daily_loss": 100.0},
        }
        config = TradingConfig.from_dict(data)
        assert config.strategy.target_spread == 0.05
        assert config.risk.max_daily_loss == 100.0


# =============================================================================
# Utility Function Tests
# =============================================================================

class TestValidationUtils:
    """Tests for validation utility functions."""

    def test_validate_price(self):
        """Test price validation."""
        assert validate_price(0.50) is True
        assert validate_price(MIN_PRICE) is True
        assert validate_price(MAX_PRICE) is True
        assert validate_price(0.00) is False
        assert validate_price(1.00) is False
        assert validate_price(-0.5) is False

    def test_validate_spread(self):
        """Test spread validation."""
        assert validate_spread(0.45, 0.50) is True
        assert validate_spread(0.50, 0.45) is False  # bid > ask
        assert validate_spread(0.50, 0.50) is False  # bid = ask
        assert validate_spread(0.00, 0.50) is False  # invalid bid

    def test_validate_size(self):
        """Test size validation."""
        assert validate_size(20) is True
        assert validate_size(5) is True   # MIN_QUOTE_SIZE
        assert validate_size(100) is True  # MAX_QUOTE_SIZE
        assert validate_size(0) is False
        assert validate_size(4) is False   # Below min
        assert validate_size(101) is False  # Above max

    def test_validate_side(self):
        """Test side validation."""
        assert validate_side("BID") is True
        assert validate_side("ASK") is True
        assert validate_side("BUY") is False
        assert validate_side("SELL") is False


class TestCalculationUtils:
    """Tests for calculation utility functions."""

    def test_calculate_mid(self):
        """Test mid price calculation."""
        assert calculate_mid(0.45, 0.55) == 0.50
        assert calculate_mid(0.40, 0.60) == 0.50

    def test_calculate_spread_pct(self):
        """Test spread percentage calculation."""
        # 0.05 spread, 0.475 mid = 10.53%
        assert abs(calculate_spread_pct(0.45, 0.50) - 0.1053) < 0.001

        # Edge case: zero mid
        assert calculate_spread_pct(0.0, 0.0) == 0.0

    def test_cents_to_probability(self):
        """Test cents to probability conversion."""
        assert cents_to_probability(45) == 0.45
        assert cents_to_probability(100) == 1.0
        assert cents_to_probability(0) == 0.0

    def test_probability_to_cents(self):
        """Test probability to cents conversion."""
        assert probability_to_cents(0.45) == 45
        assert probability_to_cents(0.456) == 46  # Rounds up
        assert probability_to_cents(0.454) == 45  # Rounds down


class TestQuotingUtils:
    """Tests for quoting utility functions."""

    def test_calculate_quote_prices(self):
        """Test quote price calculation."""
        bid, ask = calculate_quote_prices(0.50, 0.04)
        assert abs(bid - 0.48) < 0.001
        assert abs(ask - 0.52) < 0.001

    def test_calculate_quote_prices_with_skew(self):
        """Test quote prices with inventory skew."""
        # Negative skew (long position) pushes prices down
        bid, ask = calculate_quote_prices(0.50, 0.04, inventory_skew=-0.02)
        assert bid < 0.48
        assert ask < 0.52

    def test_calculate_inventory_skew(self):
        """Test inventory skew calculation."""
        # Long 25 of max 50 = 50% long
        skew = calculate_inventory_skew(25, 50, 0.01)
        assert skew == -0.005  # Negative to push prices down

        # Short 25 = 50% short
        skew = calculate_inventory_skew(-25, 50, 0.01)
        assert skew == 0.005  # Positive to push prices up

        # Flat position
        skew = calculate_inventory_skew(0, 50, 0.01)
        assert skew == 0.0

    def test_should_quote(self):
        """Test should_quote logic."""
        # Wide spread, no position - quote both
        bid, ask = should_quote(0.05, 0.03, 0, 50)
        assert bid is True and ask is True

        # Spread too tight - quote neither
        bid, ask = should_quote(0.02, 0.03, 0, 50)
        assert bid is False and ask is False

        # At max long - only quote ask
        bid, ask = should_quote(0.05, 0.03, 50, 50)
        assert bid is False and ask is True

        # At max short - only quote bid
        bid, ask = should_quote(0.05, 0.03, -50, 50)
        assert bid is True and ask is False


# =============================================================================
# Integration Tests
# =============================================================================

class TestIntegration:
    """Integration tests combining multiple components."""

    def test_snapshot_to_quote_workflow(self):
        """Test full workflow from snapshot to quote generation."""
        # Start with a core Snapshot
        snapshot = Snapshot(
            ticker="PREDICT-YES",
            timestamp="2024-01-01T12:00:00+00:00",
            yes_bid=45,
            yes_ask=50,
            orderbook_bid_depth=100,
            orderbook_ask_depth=100,
        )

        # Convert to MarketState
        state = MarketState.from_snapshot(snapshot)
        assert state.mid_price == 0.475

        # Load config
        config = MarketMakerConfig(target_spread=0.04, quote_size=20)

        # Calculate quote prices
        bid_price, ask_price = calculate_quote_prices(
            state.mid_price,
            config.target_spread,
        )

        # Create quotes
        bid_quote = Quote(
            ticker=state.ticker,
            side=SIDE_BID,
            price=bid_price,
            size=config.quote_size,
        )
        ask_quote = Quote(
            ticker=state.ticker,
            side=SIDE_ASK,
            price=ask_price,
            size=config.quote_size,
        )

        assert bid_quote.price < ask_quote.price
        assert bid_quote.size == 20

    def test_position_tracking_workflow(self):
        """Test position tracking with fills."""
        # Start with flat position
        position = Position(
            ticker="TEST",
            contracts=0,
            avg_entry_price=0.0,
        )
        assert position.is_flat

        # Simulate a buy fill
        fill = Fill(
            order_id="ORDER1",
            ticker="TEST",
            side=SIDE_BID,
            price=0.45,
            size=50,
            timestamp=datetime.now(timezone.utc),
        )

        # Update position (simplified)
        position = Position(
            ticker="TEST",
            contracts=50,
            avg_entry_price=fill.price,
        )
        assert position.is_long
        assert position.abs_size == 50

        # Check P&L at current price
        position.update_unrealized_pnl(0.50)
        assert abs(position.unrealized_pnl - 2.5) < 1e-9  # 50 * 0.05
