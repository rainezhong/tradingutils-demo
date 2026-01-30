#!/usr/bin/env python3
"""Example usage of the MarketMaker class.

This script demonstrates:
1. Initializing a market maker with configuration
2. Generating quotes based on market state
3. Processing fills and updating position
4. Tracking P&L

Run from project root:
    python examples/market_maker_example.py
"""

import logging
import sys
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, ".")

from src.market_making.config import MarketMakerConfig
from src.market_making.constants import SIDE_ASK, SIDE_BID
from src.market_making.models import Fill, MarketState
from src.market_maker import MarketMaker


def setup_logging():
    """Configure logging for the example."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def create_market_state(
    ticker: str,
    bid: float,
    ask: float,
) -> MarketState:
    """Create a market state for testing."""
    return MarketState(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        best_bid=bid,
        best_ask=ask,
        mid_price=(bid + ask) / 2,
        bid_size=100,
        ask_size=100,
    )


def main():
    """Run the market maker example."""
    setup_logging()
    print("=" * 60)
    print("Market Maker Example")
    print("=" * 60)

    # 1. Create configuration
    print("\n1. Creating market maker configuration...")
    config = MarketMakerConfig(
        target_spread=0.04,      # 4% target spread
        edge_per_side=0.005,    # 0.5% edge
        quote_size=20,           # 20 contracts per quote
        max_position=50,         # Max 50 contracts
        inventory_skew_factor=0.01,
        min_spread_to_quote=0.02,
    )
    print(f"   Target spread: {config.target_spread:.1%}")
    print(f"   Quote size: {config.quote_size}")
    print(f"   Max position: {config.max_position}")

    # 2. Initialize market maker
    print("\n2. Initializing market maker...")
    ticker = "BTC-50K-YES"
    mm = MarketMaker(ticker, config)
    print(f"   Ticker: {mm.ticker}")
    print(f"   Initial position: {mm.position.contracts}")

    # 3. Create market state
    print("\n3. Creating market state...")
    market = create_market_state(ticker, bid=0.45, ask=0.55)
    print(f"   Best bid: {market.best_bid:.2f}")
    print(f"   Best ask: {market.best_ask:.2f}")
    print(f"   Mid price: {market.mid_price:.2f}")
    print(f"   Spread: {market.spread_pct:.1%}")

    # 4. Check if should quote
    print("\n4. Checking if should quote...")
    should_quote = mm.should_quote(market)
    print(f"   Should quote: {should_quote}")

    # 5. Generate quotes
    print("\n5. Generating quotes...")
    quotes = mm.generate_quotes(market)
    for quote in quotes:
        print(f"   {quote.side}: {quote.price:.3f} x {quote.size}")

    # 6. Simulate a buy fill
    print("\n6. Simulating BID fill...")
    bid_quote = next(q for q in quotes if q.side == SIDE_BID)
    fill = Fill(
        order_id="FILL001",
        ticker=ticker,
        side=SIDE_BID,
        price=bid_quote.price,
        size=bid_quote.size,
        timestamp=datetime.now(timezone.utc),
    )
    mm.update_position(fill)
    print(f"   Filled: BUY {fill.size} @ {fill.price:.3f}")
    print(f"   New position: {mm.position.contracts}")
    print(f"   Avg entry: {mm.position.avg_entry_price:.3f}")

    # 7. Generate new quotes (with inventory skew)
    print("\n7. Generating quotes with inventory skew...")
    quotes2 = mm.generate_quotes(market)
    for quote in quotes2:
        print(f"   {quote.side}: {quote.price:.3f} x {quote.size}")

    # Compare with original quotes
    old_bid = next(q for q in quotes if q.side == SIDE_BID)
    new_bid = next(q for q in quotes2 if q.side == SIDE_BID)
    print(f"   Bid moved: {old_bid.price:.3f} -> {new_bid.price:.3f} (skewed down)")

    # 8. Calculate unrealized P&L at higher price
    print("\n8. Calculating unrealized P&L...")
    market_up = create_market_state(ticker, bid=0.50, ask=0.60)
    pnl = mm.calculate_unrealized_pnl(market_up.mid_price)
    print(f"   Current mid: {market_up.mid_price:.2f}")
    print(f"   Entry price: {mm.position.avg_entry_price:.3f}")
    print(f"   Unrealized P&L: ${pnl:.2f}")

    # 9. Simulate selling to close position
    print("\n9. Simulating ASK fill to close position...")
    sell_fill = Fill(
        order_id="FILL002",
        ticker=ticker,
        side=SIDE_ASK,
        price=0.55,
        size=mm.position.contracts,
        timestamp=datetime.now(timezone.utc),
    )
    mm.update_position(sell_fill)
    print(f"   Filled: SELL {sell_fill.size} @ {sell_fill.price:.3f}")
    print(f"   New position: {mm.position.contracts}")
    print(f"   Realized P&L: ${mm.position.realized_pnl:.2f}")

    # 10. Get final status
    print("\n10. Final status:")
    status = mm.get_status(market_up)
    print(f"   Position: {status['position']['contracts']}")
    print(f"   Realized P&L: ${status['position']['realized_pnl']:.2f}")
    print(f"   Quotes generated: {status['stats']['quotes_generated']}")
    print(f"   Quotes filled: {status['stats']['quotes_filled']}")

    print("\n" + "=" * 60)
    print("Example complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
