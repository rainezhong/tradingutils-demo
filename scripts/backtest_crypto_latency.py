#!/usr/bin/env python3
"""Backtest the crypto latency strategy using historical price data.

Simulates 15-minute binary options on BTC/ETH/SOL using historical prices.
Uses CoinGecko free API for historical minute-level data.

Usage:
    python scripts/backtest_crypto_latency.py --days 7
    python scripts/backtest_crypto_latency.py --days 30 --kelly 0.5
"""

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import httpx

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class SimulatedMarket:
    """A simulated 15-minute binary option."""
    asset: str
    strike_price: float
    open_time: datetime
    close_time: datetime
    open_price: float  # Price when market opened
    close_price: float  # Price at settlement
    result: str  # 'yes' or 'no'


@dataclass
class Trade:
    """A simulated trade."""
    market: SimulatedMarket
    side: str  # 'yes' or 'no'
    contracts: int
    entry_price: float  # 0-1
    implied_prob: float
    market_prob: float
    edge: float
    pnl: float = 0.0


def fetch_historical_prices(asset: str, days: int) -> List[Tuple[datetime, float]]:
    """Fetch historical minute prices from CoinGecko."""

    coin_ids = {
        'BTC': 'bitcoin',
        'ETH': 'ethereum',
        'SOL': 'solana'
    }

    coin_id = coin_ids.get(asset, asset.lower())

    # CoinGecko free API - get OHLC data
    # For >1 day, returns hourly. For <=1 day, returns minutely
    # We'll fetch daily and interpolate

    print(f"Fetching {days} days of {asset} price history...")

    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {
        'vs_currency': 'usd',
        'days': days,
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        prices = []
        for ts, price in data.get('prices', []):
            dt = datetime.utcfromtimestamp(ts / 1000)
            prices.append((dt, price))

        print(f"  Got {len(prices)} price points")
        return prices

    except Exception as e:
        print(f"  Error fetching {asset}: {e}")
        return []


def interpolate_price(prices: List[Tuple[datetime, float]], target_time: datetime) -> Optional[float]:
    """Get interpolated price at a specific time."""
    if not prices:
        return None

    # Find surrounding prices
    for i in range(len(prices) - 1):
        t1, p1 = prices[i]
        t2, p2 = prices[i + 1]

        if t1 <= target_time <= t2:
            # Linear interpolation
            if t2 == t1:
                return p1
            ratio = (target_time - t1).total_seconds() / (t2 - t1).total_seconds()
            return p1 + ratio * (p2 - p1)

    # Return closest if outside range
    if target_time < prices[0][0]:
        return prices[0][1]
    return prices[-1][1]


def generate_simulated_markets(
    prices: List[Tuple[datetime, float]],
    asset: str,
    interval_minutes: int = 15
) -> List[SimulatedMarket]:
    """Generate simulated 15-minute markets from price history."""

    if len(prices) < 2:
        return []

    markets = []
    start_time = prices[0][0]
    end_time = prices[-1][0]

    # Generate markets every 15 minutes
    current = start_time.replace(minute=(start_time.minute // 15) * 15, second=0, microsecond=0)

    while current + timedelta(minutes=interval_minutes) <= end_time:
        open_time = current
        close_time = current + timedelta(minutes=interval_minutes)

        open_price = interpolate_price(prices, open_time)
        close_price = interpolate_price(prices, close_time)

        if open_price and close_price:
            # Strike = open price (typical for these markets)
            strike = open_price
            result = 'yes' if close_price > strike else 'no'

            markets.append(SimulatedMarket(
                asset=asset,
                strike_price=strike,
                open_time=open_time,
                close_time=close_time,
                open_price=open_price,
                close_price=close_price,
                result=result,
            ))

        current += timedelta(minutes=interval_minutes)

    return markets


def normal_cdf(x: float) -> float:
    """Standard normal CDF approximation."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x / 2)
    return 0.5 * (1.0 + sign * y)


def calculate_implied_prob(
    spot: float,
    strike: float,
    time_sec: float,
    volatility: float = 0.5
) -> float:
    """Calculate implied probability using Black-Scholes."""
    if spot <= 0 or strike <= 0 or time_sec <= 0:
        return 0.5

    time_years = time_sec / (365.25 * 24 * 60 * 60)
    vol_sqrt_t = volatility * math.sqrt(time_years)

    if vol_sqrt_t <= 0:
        return 1.0 if spot > strike else 0.0

    d2 = (math.log(spot / strike) - 0.5 * volatility ** 2 * time_years) / vol_sqrt_t
    return max(0.01, min(0.99, normal_cdf(d2)))


def simulate_market_odds(
    spot: float,
    strike: float,
    time_sec: float,
) -> float:
    """Simulate market odds - usually efficient, sometimes stale."""
    # True probability now
    true_prob = calculate_implied_prob(spot, strike, time_sec)

    # Market is efficient ~85% of the time (small random noise)
    # Only ~15% of the time is there real staleness (exploitable edge)
    if random.random() > 0.15:
        # Efficient market - small noise around true prob
        stale_prob = true_prob + random.gauss(0, 0.04)
    else:
        # Stale market - larger deviation
        stale_prob = true_prob + random.gauss(0, 0.12)

    return max(0.05, min(0.95, stale_prob))


def kelly_size(win_prob: float, price: float, bankroll: float, kelly_fraction: float = 0.5, max_bet: float = 20.0) -> int:
    """Calculate Kelly bet size with max bet limit."""
    if price >= 0.99 or price <= 0.01:
        return 0

    kelly_f = (win_prob - price) / (1 - price)
    kelly_f = kelly_f * kelly_fraction
    kelly_f = max(0, min(kelly_f, 0.15))  # Cap at 15% of bankroll

    bet_dollars = bankroll * kelly_f
    bet_dollars = min(bet_dollars, max_bet)  # Max bet cap

    contracts = int(bet_dollars / price)

    return max(0, min(contracts, 50))  # Max 50 contracts


def run_backtest(
    markets: List[SimulatedMarket],
    initial_bankroll: float = 100.0,
    kelly_fraction: float = 0.5,
    min_edge: float = 0.10,
    check_interval_sec: int = 60,  # Check every minute within window
    compound: bool = False,  # Whether to compound gains
) -> Tuple[List[Trade], float]:
    """Run backtest on simulated markets."""

    trades = []
    bankroll = initial_bankroll
    sizing_bankroll = initial_bankroll  # Use fixed bankroll for sizing unless compounding

    for market in markets:
        # Simulate checking the market at various points
        window_duration = (market.close_time - market.open_time).total_seconds()

        for offset in range(check_interval_sec, int(window_duration) - 30, check_interval_sec):
            check_time = market.open_time + timedelta(seconds=offset)
            time_to_expiry = (market.close_time - check_time).total_seconds()

            # Simulate spot price at check time (interpolate between open and close)
            progress = offset / window_duration
            spot = market.open_price + progress * (market.close_price - market.open_price)
            # Add some noise to make it more realistic
            spot = spot * (1 + random.gauss(0, 0.001))

            # Calculate our implied probability
            implied_prob = calculate_implied_prob(spot, market.strike_price, time_to_expiry)

            # Simulate stale market odds
            market_prob = simulate_market_odds(spot, market.strike_price, time_to_expiry)

            # Check for opportunity
            edge = implied_prob - market_prob

            if edge > min_edge:
                # Buy YES
                side = 'yes'
                win_prob = implied_prob
                entry_price = market_prob
            elif edge < -min_edge:
                # Buy NO
                side = 'no'
                win_prob = 1 - implied_prob
                entry_price = 1 - market_prob
                edge = -edge
            else:
                continue

            # Calculate position size (use sizing_bankroll, not actual bankroll)
            contracts = kelly_size(win_prob, entry_price, sizing_bankroll, kelly_fraction)

            if contracts <= 0:
                continue

            # Only take one trade per market
            already_traded = any(t.market == market for t in trades)
            if already_traded:
                continue

            # Execute trade
            won = (side == market.result)
            if won:
                pnl = (1 - entry_price) * contracts
            else:
                pnl = -entry_price * contracts

            # Subtract fees (~3 cents per contract)
            pnl -= 0.03 * contracts

            bankroll += pnl

            # Update sizing bankroll if compounding
            if compound:
                sizing_bankroll = max(initial_bankroll * 0.5, bankroll)

            trades.append(Trade(
                market=market,
                side=side,
                contracts=contracts,
                entry_price=entry_price,
                implied_prob=implied_prob,
                market_prob=market_prob,
                edge=edge,
                pnl=pnl,
            ))

            break  # One trade per market

    return trades, bankroll


def main():
    parser = argparse.ArgumentParser(description="Backtest crypto latency strategy")
    parser.add_argument('--days', type=int, default=7, help='Days of history (default: 7)')
    parser.add_argument('--kelly', type=float, default=0.5, help='Kelly fraction (default: 0.5)')
    parser.add_argument('--edge', type=float, default=0.10, help='Min edge (default: 0.10)')
    parser.add_argument('--bankroll', type=float, default=100.0, help='Starting bankroll')
    args = parser.parse_args()

    print("=" * 60)
    print("CRYPTO LATENCY STRATEGY BACKTEST")
    print("=" * 60)
    print(f"Period: {args.days} days")
    print(f"Kelly Fraction: {args.kelly}")
    print(f"Min Edge: {args.edge * 100}%")
    print(f"Starting Bankroll: ${args.bankroll}")
    print("=" * 60)
    print()

    # Fetch historical prices
    all_markets = []
    for asset in ['BTC', 'ETH', 'SOL']:
        prices = fetch_historical_prices(asset, args.days)
        if prices:
            markets = generate_simulated_markets(prices, asset)
            all_markets.extend(markets)
            print(f"  Generated {len(markets)} simulated {asset} markets")

    print(f"\nTotal simulated markets: {len(all_markets)}")

    # Sort by time
    all_markets.sort(key=lambda m: m.open_time)

    # Run backtest
    print("\nRunning backtest...")
    trades, final_bankroll = run_backtest(
        all_markets,
        initial_bankroll=args.bankroll,
        kelly_fraction=args.kelly,
        min_edge=args.edge,
    )

    # Results
    print()
    print("=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)

    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl <= 0)
    total_pnl = sum(t.pnl for t in trades)

    print(f"Total Trades: {len(trades)}")
    print(f"Wins: {wins} ({wins/len(trades)*100:.1f}%)" if trades else "Wins: 0")
    print(f"Losses: {losses} ({losses/len(trades)*100:.1f}%)" if trades else "Losses: 0")
    print()
    print(f"Starting Bankroll: ${args.bankroll:.2f}")
    print(f"Final Bankroll: ${final_bankroll:.2f}")
    print(f"Total P&L: ${total_pnl:+.2f}")
    print(f"Return: {(final_bankroll/args.bankroll - 1)*100:+.1f}%")
    print()

    # Breakdown by asset
    print("By Asset:")
    for asset in ['BTC', 'ETH', 'SOL']:
        asset_trades = [t for t in trades if t.market.asset == asset]
        if asset_trades:
            asset_pnl = sum(t.pnl for t in asset_trades)
            asset_wins = sum(1 for t in asset_trades if t.pnl > 0)
            print(f"  {asset}: {len(asset_trades)} trades, {asset_wins} wins, ${asset_pnl:+.2f}")

    # Sample trades
    if trades:
        print()
        print("Sample Trades:")
        for t in trades[:10]:
            won = "WIN" if t.pnl > 0 else "LOSS"
            print(f"  {t.market.asset} {t.side.upper()} {t.contracts}x @ {t.entry_price*100:.0f}c | "
                  f"edge={t.edge*100:.1f}% | {won} ${t.pnl:+.2f}")

    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
