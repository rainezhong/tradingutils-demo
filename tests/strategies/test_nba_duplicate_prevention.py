"""Test NBA underdog strategy duplicate bet prevention.

Ensures we don't bet on the same outcome twice via different markets
(e.g., Team A YES at 10¢ and Team B NO at 10¢ = same bet).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from strategies.nba_underdog_strategy import NBAUnderdogStrategy, NBAUnderdogConfig
from core.order_manager import Side


@pytest.fixture
def mock_client():
    """Mock exchange client."""
    client = AsyncMock()
    client.get_markets = AsyncMock(return_value={"markets": []})
    return client


@pytest.fixture
def strategy(mock_client):
    """Create strategy with mock client."""
    config = NBAUnderdogConfig(
        min_price_cents=5,
        max_price_cents=15,
        max_bets_per_game=2,  # Allows 2 bets, but dedup should prevent
        position_size=10,
        use_kelly_sizing=False,  # Disable Kelly to simplify testing
    )
    strat = NBAUnderdogStrategy(mock_client, config, dry_run=True)
    # Mock get_bankroll to avoid formatting issues
    strat.get_bankroll = AsyncMock(return_value=1000.0)
    return strat


def test_extract_team_from_ticker(strategy):
    """Test extracting team abbreviation from ticker."""
    assert strategy._extract_team_from_ticker("KXNBAGAME-26FEB26GSWLAL-GSW") == "GSW"
    assert strategy._extract_team_from_ticker("KXNBAGAME-26FEB26GSWLAL-LAL") == "LAL"
    assert strategy._extract_team_from_ticker("KXNBAGAME-26JAN30BOSATL-BOS") == "BOS"
    assert strategy._extract_team_from_ticker("INVALID-TICKER") is None


@pytest.mark.asyncio
async def test_prevents_duplicate_yes_bet_on_same_team(strategy):
    """Test that we can't bet YES on Team A twice via different markets."""
    # Create two markets for the same game
    market1 = MagicMock()
    market1.ticker = "KXNBAGAME-26FEB26GSWLAL-GSW"
    market1.event_ticker = "KXNBAGAME-26FEB26GSWLAL"
    market1.yes_ask = 10
    market1.yes_bid = 8
    market1.close_time = datetime.now(timezone.utc)

    market2 = MagicMock()
    market2.ticker = "KXNBAGAME-26FEB26GSWLAL-GSW"  # Same team, different price
    market2.event_ticker = "KXNBAGAME-26FEB26GSWLAL"
    market2.yes_ask = 12
    market2.yes_bid = 10
    market2.close_time = datetime.now(timezone.utc)

    # Mock signal generation
    signal1 = MagicMock()
    signal1.side = Side.YES
    signal1.target_price_cents = 10

    signal2 = MagicMock()
    signal2.side = Side.YES
    signal2.target_price_cents = 12

    # Place first bet (should succeed)
    result1 = await strategy._place_bet_from_signal(market1, signal1)
    assert result1 is True
    assert "GSW" in strategy._game_outcomes.values()

    # Try to place second bet on same team (should be blocked)
    result2 = await strategy._place_bet_from_signal(market2, signal2)
    assert result2 is False


@pytest.mark.asyncio
async def test_prevents_duplicate_via_opposite_side(strategy):
    """Test that we can't bet on Team A via (Team A YES) and (Team B NO)."""
    # Market 1: Team A to win
    market_a = MagicMock()
    market_a.ticker = "KXNBAGAME-26FEB26GSWLAL-GSW"
    market_a.event_ticker = "KXNBAGAME-26FEB26GSWLAL"
    market_a.yes_ask = 10  # GSW underdog
    market_a.yes_bid = 8
    market_a.close_time = datetime.now(timezone.utc)

    # Market 2: Team B to win
    market_b = MagicMock()
    market_b.ticker = "KXNBAGAME-26FEB26GSWLAL-LAL"
    market_b.event_ticker = "KXNBAGAME-26FEB26GSWLAL"  # Same game!
    market_b.yes_ask = 92  # LAL favorite
    market_b.yes_bid = 90
    market_b.close_time = datetime.now(timezone.utc)

    # Signal 1: Buy YES on GSW (bet GSW wins)
    signal_a = MagicMock()
    signal_a.side = Side.YES
    signal_a.target_price_cents = 10

    # Signal 2: Buy NO on LAL (bet GSW wins!)
    signal_b = MagicMock()
    signal_b.side = Side.NO
    signal_b.target_price_cents = 10

    # Place first bet (should succeed)
    result1 = await strategy._place_bet_from_signal(market_a, signal_a)
    assert result1 is True
    assert strategy._game_outcomes["KXNBAGAME-26FEB26GSWLAL"] == "GSW"

    # Try to place second bet on same outcome (should be blocked)
    result2 = await strategy._place_bet_from_signal(market_b, signal_b)
    assert result2 is False  # Should be blocked!


@pytest.mark.asyncio
async def test_allows_bets_on_different_games(strategy):
    """Test that bets on different games are allowed."""
    # Market 1: Game 1
    market1 = MagicMock()
    market1.ticker = "KXNBAGAME-26FEB26GSWLAL-GSW"
    market1.event_ticker = "KXNBAGAME-26FEB26GSWLAL"
    market1.yes_ask = 10
    market1.yes_bid = 8
    market1.close_time = datetime.now(timezone.utc)

    # Market 2: Game 2 (different game)
    market2 = MagicMock()
    market2.ticker = "KXNBAGAME-26FEB26BOSATL-BOS"
    market2.event_ticker = "KXNBAGAME-26FEB26BOSATL"  # Different game
    market2.yes_ask = 12
    market2.yes_bid = 10
    market2.close_time = datetime.now(timezone.utc)

    signal1 = MagicMock()
    signal1.side = Side.YES
    signal1.target_price_cents = 10

    signal2 = MagicMock()
    signal2.side = Side.YES
    signal2.target_price_cents = 12

    # Both should succeed (different games)
    result1 = await strategy._place_bet_from_signal(market1, signal1)
    assert result1 is True

    result2 = await strategy._place_bet_from_signal(market2, signal2)
    assert result2 is True


@pytest.mark.asyncio
async def test_blocks_opposite_bet_on_same_team_market(strategy):
    """Test we can't bet YES then NO on the same team's market."""
    market = MagicMock()
    market.ticker = "KXNBAGAME-26FEB26GSWLAL-GSW"
    market.event_ticker = "KXNBAGAME-26FEB26GSWLAL"
    market.yes_ask = 10
    market.yes_bid = 8
    market.close_time = datetime.now(timezone.utc)

    # First bet: YES on GSW
    signal1 = MagicMock()
    signal1.side = Side.YES
    signal1.target_price_cents = 10

    result1 = await strategy._place_bet_from_signal(market, signal1)
    assert result1 is True

    # Second bet: NO on GSW (opposite of first bet)
    signal2 = MagicMock()
    signal2.side = Side.NO
    signal2.target_price_cents = 10

    result2 = await strategy._place_bet_from_signal(market, signal2)
    assert result2 is False  # Should be blocked
