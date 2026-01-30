"""Pytest fixtures for arbitrage tests."""

from datetime import datetime
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

from arb.spread_detector import (
    MarketQuote,
    MatchedMarketPair,
    Platform,
    SpreadOpportunity,
)
from src.arbitrage.config import ArbitrageConfig
from src.arbitrage.circuit_breaker import CircuitBreaker
from src.arbitrage.fee_calculator import FeeCalculator
from src.arbitrage.detector import OpportunityDetector


@pytest.fixture
def config():
    """Create a test configuration."""
    return ArbitrageConfig(
        min_edge_cents=5.0,  # Updated default
        min_edge_cents_conservative=7.0,
        min_roi_pct=0.03,  # Updated default
        prefer_maker_orders=True,
        min_liquidity_usd=100.0,
        max_position_per_market=100,
        max_concurrent_spreads=3,
        scan_interval_seconds=1.0,
        paper_mode=True,
        max_daily_loss=500.0,
        max_error_rate=0.10,
        min_fill_rate=0.70,
        fee_safety_margin=0.15,
        use_conservative_fees_for_filtering=True,
        max_depth_usage_pct=0.60,
        use_fill_or_kill=True,
    )


@pytest.fixture
def fee_calculator(config):
    """Create a fee calculator with test config."""
    return FeeCalculator(config)


@pytest.fixture
def circuit_breaker(config):
    """Create a circuit breaker with test config."""
    return CircuitBreaker(config)


@pytest.fixture
def matched_pair():
    """Create a sample matched market pair."""
    return MatchedMarketPair(
        pair_id="test-pair-1",
        event_description="Test event: Will X happen?",
        platform_1=Platform.KALSHI,
        market_1_id="KALSHI-X-YES",
        market_1_name="Will X happen?",
        platform_2=Platform.POLYMARKET,
        market_2_id="0x123abc",
        market_2_name="X happening",
        match_confidence=0.95,
        category="test",
    )


@pytest.fixture
def kalshi_yes_quote():
    """Create a sample Kalshi YES quote."""
    return MarketQuote(
        platform=Platform.KALSHI,
        market_id="KALSHI-X-YES",
        market_name="Will X happen?",
        outcome="yes",
        best_bid=0.45,
        best_ask=0.46,
        bid_size=100,
        ask_size=150,
        bid_depth_usd=450.0,
        ask_depth_usd=690.0,
        timestamp=datetime.now(),
    )


@pytest.fixture
def kalshi_no_quote():
    """Create a sample Kalshi NO quote."""
    return MarketQuote(
        platform=Platform.KALSHI,
        market_id="KALSHI-X-YES",
        market_name="Will X happen?",
        outcome="no",
        best_bid=0.53,
        best_ask=0.55,
        bid_size=100,
        ask_size=120,
        bid_depth_usd=530.0,
        ask_depth_usd=660.0,
        timestamp=datetime.now(),
    )


@pytest.fixture
def poly_yes_quote():
    """Create a sample Polymarket YES quote."""
    return MarketQuote(
        platform=Platform.POLYMARKET,
        market_id="0x123abc",
        market_name="X happening",
        outcome="yes",
        best_bid=0.48,
        best_ask=0.49,
        bid_size=200,
        ask_size=180,
        bid_depth_usd=960.0,
        ask_depth_usd=882.0,
        timestamp=datetime.now(),
    )


@pytest.fixture
def poly_no_quote():
    """Create a sample Polymarket NO quote."""
    return MarketQuote(
        platform=Platform.POLYMARKET,
        market_id="0x123abc",
        market_name="X happening",
        outcome="no",
        best_bid=0.50,
        best_ask=0.52,
        bid_size=150,
        ask_size=140,
        bid_depth_usd=750.0,
        ask_depth_usd=728.0,
        timestamp=datetime.now(),
    )


@pytest.fixture
def mock_quote_source(
    matched_pair, kalshi_yes_quote, kalshi_no_quote, poly_yes_quote, poly_no_quote
):
    """Create a mock quote source."""
    source = MagicMock()
    source.get_matched_pairs.return_value = [matched_pair]
    source.get_quotes.return_value = (
        kalshi_yes_quote,
        kalshi_no_quote,
        poly_yes_quote,
        poly_no_quote,
    )
    return source


@pytest.fixture
def spread_opportunity(matched_pair):
    """Create a sample spread opportunity."""
    return SpreadOpportunity(
        pair=matched_pair,
        opportunity_type="cross_platform_arb",
        buy_platform=Platform.KALSHI,
        buy_market_id="KALSHI-X-YES",
        buy_outcome="yes",
        buy_price=0.46,
        sell_platform=Platform.POLYMARKET,
        sell_market_id="0x123abc",
        sell_outcome="yes",
        sell_price=0.48,
        gross_edge_per_contract=0.02,
        net_edge_per_contract=0.015,
        total_fees_per_contract=0.005,
        max_contracts=100,
        available_liquidity_usd=500.0,
        estimated_profit_usd=1.50,
    )
