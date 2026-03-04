#!/usr/bin/env python3
"""Backtest BTC 15-minute binary strategy using historical settlements + Coinbase spot.

Uses 672 real KXBTC15M settlements from Kalshi paired with Coinbase 1-min candles
to test whether a rolling spot average + Black-Scholes model can profitably trade
these markets.

Usage:
    python3 scripts/backtest_btc_15m.py
    python3 scripts/backtest_btc_15m.py --edge 0.10 --stale-lag 120
"""

import argparse
import json
import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List
from urllib.request import Request, urlopen
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent.parent))

SETTLEMENT_DB = Path(__file__).parent.parent / "data" / "btc_settlement_analysis.db"
BACKTEST_DB = Path(__file__).parent.parent / "data" / "btc_backtest.db"


# ---------------------------------------------------------------------------
# Black-Scholes probability model (from strategies/crypto_latency/detector.py)
# ---------------------------------------------------------------------------


def normal_cdf(x: float) -> float:
    """Abramowitz-Stegun approximation of standard normal CDF."""
    a1, a2, a3, a4, a5 = (
        0.254829592,
        -0.284496736,
        1.421413741,
        -1.453152027,
        1.061405429,
    )
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(
        -x * x / 2
    )
    return 0.5 * (1.0 + sign * y)


def implied_probability(
    spot: float, strike: float, ttx_sec: float, vol: float
) -> float:
    """Black-Scholes d2: probability spot > strike at expiry.

    Args:
        spot: Current spot price
        strike: Strike price
        ttx_sec: Time to expiry in seconds
        vol: Annualized volatility (e.g., 0.50 for 50%)

    Returns:
        Probability (0-1) that price will be above strike at expiry
    """
    if spot <= 0 or strike <= 0:
        return 0.5
    time_years = ttx_sec / (365.25 * 24 * 3600)
    if time_years <= 0:
        return 1.0 if spot > strike else 0.0
    vol_sqrt_t = vol * math.sqrt(time_years)
    if vol_sqrt_t <= 0:
        return 1.0 if spot > strike else 0.0
    d2 = (math.log(spot / strike) - 0.5 * vol**2 * time_years) / vol_sqrt_t
    return max(0.001, min(0.999, normal_cdf(d2)))


def compute_realized_vol(prices: List[float], interval_sec: float = 60.0) -> float:
    """Compute annualized realized volatility from a price series.

    Args:
        prices: List of prices at regular intervals
        interval_sec: Seconds between each price observation

    Returns:
        Annualized volatility
    """
    if len(prices) < 3:
        return 0.50  # default 50% annualized
    log_returns = [
        math.log(prices[i] / prices[i - 1])
        for i in range(1, len(prices))
        if prices[i - 1] > 0
    ]
    if len(log_returns) < 2:
        return 0.50
    mean_ret = sum(log_returns) / len(log_returns)
    variance = sum((r - mean_ret) ** 2 for r in log_returns) / (len(log_returns) - 1)
    intervals_per_year = (365.25 * 24 * 3600) / interval_sec
    return math.sqrt(variance * intervals_per_year)


# ---------------------------------------------------------------------------
# Coinbase candle fetcher
# ---------------------------------------------------------------------------


def fetch_coinbase_candles(start_dt: datetime, end_dt: datetime) -> Dict[int, dict]:
    """Fetch Coinbase BTC-USD 1-min candles for a time range.

    Returns dict mapping open_timestamp (seconds) -> {open, high, low, close, volume}
    """
    candle_map = {}
    current = start_dt - timedelta(minutes=2)
    chunk_hours = 4

    while current < end_dt + timedelta(minutes=2):
        chunk_end = current + timedelta(hours=chunk_hours)
        params = urlencode(
            {
                "start": current.isoformat(),
                "end": chunk_end.isoformat(),
                "granularity": 60,
            }
        )
        url = f"https://api.exchange.coinbase.com/products/BTC-USD/candles?{params}"
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            resp = urlopen(req, timeout=10)
            candles = json.loads(resp.read())
            for c in candles:
                candle_map[c[0]] = {
                    "open": c[3],
                    "high": c[2],
                    "low": c[1],
                    "close": c[4],
                    "volume": c[5],
                }
        except Exception as e:
            print(f"  Warning: fetch error at {current}: {e}")
        current = chunk_end
        time.sleep(0.15)

    return candle_map


# ---------------------------------------------------------------------------
# Trade simulation
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    market_ticker: str
    entry_time_sec: float  # seconds before close
    direction: str  # "YES" or "NO"
    entry_price: float  # 0-1 (what we pay)
    fair_prob: float  # our model's fair probability
    edge: float  # fair_prob - entry_price (for YES)
    outcome: bool  # True if our bet won
    pnl_cents: float  # profit in cents per contract
    spot_at_entry: float
    strike: float
    vol_used: float


@dataclass
class BacktestConfig:
    min_edge: float = 0.12  # minimum edge to enter (12%)
    stale_lag_sec: int = 120  # how many seconds Kalshi lags (for entry price model)
    slippage_cents: int = 3  # slippage on entry
    fee_cents: int = 3  # fee per contract
    min_ttx_sec: int = 120  # don't trade with < 2 min to expiry
    max_ttx_sec: int = 840  # don't trade with > 14 min to expiry
    check_interval_sec: int = 60  # check every N seconds within window
    bankroll: float = 100.0  # starting bankroll in dollars
    kelly_fraction: float = 0.5  # half-Kelly
    max_bet_dollars: float = 50.0  # max per trade
    base_vol: float = 0.50  # default annualized vol if can't compute


def simulate_stale_price(
    fair_prob: float, lagged_fair_prob: float, slippage: float
) -> float:
    """Model what we'd pay on Kalshi.

    The market's quoted mid = lagged fair probability.
    We buy at the ask = lagged mid + spread/2.
    Simplified: entry_price = lagged_fair_prob + slippage.
    """
    return lagged_fair_prob + slippage


def kelly_size(
    win_prob: float,
    entry_price: float,
    kelly_frac: float,
    bankroll: float,
    max_bet: float,
) -> float:
    """Kelly criterion for binary options.

    f* = (p - c) / (1 - c) where p = win_prob, c = cost.
    Returns bet size in dollars.
    """
    if entry_price >= 0.99 or entry_price <= 0.01:
        return 0
    f = (win_prob - entry_price) / (1.0 - entry_price)
    f *= kelly_frac
    if f <= 0:
        return 0
    f = min(f, 0.25)  # never more than 25% of bankroll
    bet = bankroll * f
    return min(bet, max_bet)


# ---------------------------------------------------------------------------
# Main backtest
# ---------------------------------------------------------------------------


def run_backtest(config: BacktestConfig):
    if not SETTLEMENT_DB.exists():
        print(
            f"Error: {SETTLEMENT_DB} not found. Run btc_settlement_analysis.py first."
        )
        return

    conn = sqlite3.connect(str(SETTLEMENT_DB))

    # Load all settled markets
    markets = conn.execute("""
        SELECT ticker, close_time, expiration_value, floor_strike
        FROM kalshi_markets
        WHERE expiration_value IS NOT NULL AND expiration_value > 0
              AND floor_strike IS NOT NULL AND floor_strike > 0
        ORDER BY close_time
    """).fetchall()

    print(f"Loaded {len(markets)} settled KXBTC15M markets")
    if not markets:
        return

    # Determine time range and fetch all candles upfront
    first_close = datetime.fromisoformat(markets[0][1].replace("Z", "+00:00"))
    last_close = datetime.fromisoformat(markets[-1][1].replace("Z", "+00:00"))
    # Need candles starting 15 min before first close and a bit after last
    fetch_start = first_close - timedelta(minutes=20)
    fetch_end = last_close + timedelta(minutes=5)

    print(
        f"Fetching Coinbase 1-min candles: {fetch_start.date()} to {fetch_end.date()}..."
    )
    candle_map = fetch_coinbase_candles(fetch_start, fetch_end)
    print(f"Fetched {len(candle_map)} candles")

    if not candle_map:
        print("No candle data. Aborting.")
        return

    # Run backtest
    trades: List[Trade] = []
    bankroll = config.bankroll
    bankroll_curve = [(0, bankroll)]
    markets_analyzed = 0
    markets_skipped_no_candles = 0
    markets_with_entries = 0

    for ticker, close_time_str, brti_value, strike in markets:
        close_dt = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
        close_ts = int(close_dt.timestamp())
        open_ts = close_ts - 900  # 15 minutes before close

        # Actual settlement: did BRTI finish above strike?
        settled_yes = brti_value > strike

        # Gather candles for this window (and preceding 5 min for vol calc)
        vol_start_ts = open_ts - 300  # 5 min before window opens
        window_prices = []
        for t in range(vol_start_ts, close_ts + 60, 60):
            c = candle_map.get(t)
            if c:
                window_prices.append(c["close"])

        if len(window_prices) < 5:
            markets_skipped_no_candles += 1
            continue

        markets_analyzed += 1

        # Compute realized vol from pre-window + early window candles
        vol = compute_realized_vol(window_prices[:10], 60.0)  # use first 10 candles
        if vol < 0.05:
            vol = config.base_vol  # floor

        # Walk through the 15-min window at check_interval
        entered = False
        for offset_sec in range(
            config.check_interval_sec, 900 - 30, config.check_interval_sec
        ):
            check_ts = open_ts + offset_sec
            ttx_sec = close_ts - check_ts

            if ttx_sec < config.min_ttx_sec or ttx_sec > config.max_ttx_sec:
                continue

            # Get spot price at check time
            candle = candle_map.get(check_ts)
            if candle is None:
                # Try neighbors
                for neighbor in [-60, 60]:
                    candle = candle_map.get(check_ts + neighbor)
                    if candle:
                        break
            if candle is None:
                continue

            spot = candle["close"]

            # Our fair probability now
            fair_prob = implied_probability(spot, strike, ttx_sec, vol)

            # Lagged fair probability (what we assume Kalshi is showing)
            lagged_ts = check_ts - config.stale_lag_sec
            lagged_candle = candle_map.get(lagged_ts)
            if lagged_candle is None:
                for neighbor in [-60, 60]:
                    lagged_candle = candle_map.get(lagged_ts + neighbor)
                    if lagged_candle:
                        break
            if lagged_candle is None:
                continue

            lagged_spot = lagged_candle["close"]
            lagged_ttx = close_ts - lagged_ts
            lagged_prob = implied_probability(lagged_spot, strike, lagged_ttx, vol)

            # Determine direction and edge
            slippage = config.slippage_cents / 100.0
            fee = config.fee_cents / 100.0

            # YES side: we think fair_prob is higher than what market shows
            yes_edge = fair_prob - lagged_prob - slippage - fee
            # NO side: we think fair_prob is lower than what market shows
            no_edge = (1 - fair_prob) - (1 - lagged_prob) - slippage - fee

            direction = None
            edge = 0
            entry_price = 0

            if yes_edge >= config.min_edge and yes_edge >= no_edge:
                direction = "YES"
                edge = yes_edge
                entry_price = lagged_prob + slippage  # buy YES at stale mid + slippage
            elif no_edge >= config.min_edge:
                direction = "NO"
                edge = no_edge
                entry_price = (
                    1 - lagged_prob
                ) + slippage  # buy NO at stale (1-mid) + slippage

            if direction is None:
                continue

            # Position sizing
            win_prob = fair_prob if direction == "YES" else (1 - fair_prob)
            bet_dollars = kelly_size(
                win_prob,
                entry_price,
                config.kelly_fraction,
                bankroll,
                config.max_bet_dollars,
            )
            if bet_dollars < 1.0:
                continue

            # Determine outcome
            if direction == "YES":
                won = settled_yes
            else:
                won = not settled_yes

            # PnL per contract (in cents)
            payout = 100 if won else 0
            cost_cents = entry_price * 100
            pnl_cents = payout - cost_cents - config.fee_cents

            # Dollar PnL
            contracts = bet_dollars / entry_price
            dollar_pnl = contracts * pnl_cents / 100.0

            bankroll += dollar_pnl

            trade = Trade(
                market_ticker=ticker,
                entry_time_sec=ttx_sec,
                direction=direction,
                entry_price=entry_price,
                fair_prob=fair_prob,
                edge=edge,
                outcome=won,
                pnl_cents=pnl_cents,
                spot_at_entry=spot,
                strike=strike,
                vol_used=vol,
            )
            trades.append(trade)
            bankroll_curve.append((len(trades), bankroll))
            entered = True
            break  # One trade per market (first signal wins)

        if entered:
            markets_with_entries += 1

    conn.close()

    # ---------------------------------------------------------------------------
    # Results
    # ---------------------------------------------------------------------------
    print(f"\n{'=' * 60}")
    print("  BACKTEST RESULTS: KXBTC15M Latency Strategy")
    print(f"{'=' * 60}")
    print("\nConfig:")
    print(f"  Min edge:       {config.min_edge * 100:.0f}%")
    print(f"  Stale lag:      {config.stale_lag_sec}s")
    print(f"  Slippage:       {config.slippage_cents}c")
    print(f"  Fee:            {config.fee_cents}c")
    print(f"  Kelly fraction: {config.kelly_fraction}")
    print(f"  Starting bank:  ${config.bankroll:.0f}")
    print(f"  Min TTX:        {config.min_ttx_sec}s")
    print(f"  Max TTX:        {config.max_ttx_sec}s")
    print(f"  Check interval: {config.check_interval_sec}s")

    print("\nMarket Coverage:")
    print(f"  Total markets:     {len(markets)}")
    print(f"  Analyzed:          {markets_analyzed}")
    print(f"  Skipped (no data): {markets_skipped_no_candles}")
    print(f"  Had entries:       {markets_with_entries}")

    if not trades:
        print("\n  No trades generated. Try lowering --edge or increasing --stale-lag.")
        return

    wins = sum(1 for t in trades if t.outcome)
    losses = len(trades) - wins
    win_rate = wins / len(trades) * 100

    total_pnl_cents = sum(t.pnl_cents for t in trades)
    avg_pnl = total_pnl_cents / len(trades)

    yes_trades = [t for t in trades if t.direction == "YES"]
    no_trades = [t for t in trades if t.direction == "NO"]

    print("\nTrade Summary:")
    print(f"  Total trades:   {len(trades)}")
    print(f"  YES trades:     {len(yes_trades)}")
    print(f"  NO trades:      {len(no_trades)}")
    print(f"  Wins:           {wins} ({win_rate:.1f}%)")
    print(f"  Losses:         {losses}")

    print("\nPnL (per contract, cents):")
    print(f"  Total PnL:      {total_pnl_cents:+,.0f}c")
    print(f"  Avg PnL/trade:  {avg_pnl:+,.1f}c")
    winning_pnl = [t.pnl_cents for t in trades if t.outcome]
    losing_pnl = [t.pnl_cents for t in trades if not t.outcome]
    if winning_pnl:
        print(f"  Avg winner:     {sum(winning_pnl) / len(winning_pnl):+,.1f}c")
    if losing_pnl:
        print(f"  Avg loser:      {sum(losing_pnl) / len(losing_pnl):+,.1f}c")

    print("\nBankroll:")
    print(f"  Starting:       ${config.bankroll:,.2f}")
    print(f"  Ending:         ${bankroll:,.2f}")
    print(f"  Return:         {(bankroll / config.bankroll - 1) * 100:+,.1f}%")

    # Edge distribution
    edges = [t.edge for t in trades]
    edges_sorted = sorted(edges)
    print("\nEdge Distribution:")
    print(f"  Min edge taken:  {min(edges) * 100:.1f}%")
    print(f"  Median edge:     {edges_sorted[len(edges_sorted) // 2] * 100:.1f}%")
    print(f"  Max edge taken:  {max(edges) * 100:.1f}%")

    # Entry price distribution
    entry_prices = [t.entry_price for t in trades]
    print("\nEntry Price Distribution:")
    print(f"  Min entry:       {min(entry_prices) * 100:.0f}c")
    print(
        f"  Median entry:    {sorted(entry_prices)[len(entry_prices) // 2] * 100:.0f}c"
    )
    print(f"  Max entry:       {max(entry_prices) * 100:.0f}c")

    # TTX distribution
    ttxs = [t.entry_time_sec for t in trades]
    print("\nTime-to-Expiry at Entry:")
    print(f"  Min TTX:         {min(ttxs):.0f}s")
    print(f"  Median TTX:      {sorted(ttxs)[len(ttxs) // 2]:.0f}s")
    print(f"  Max TTX:         {max(ttxs):.0f}s")

    # Win rate by TTX bucket
    print("\nWin Rate by TTX Bucket:")
    ttx_buckets = [
        (120, 300, "2-5min"),
        (300, 480, "5-8min"),
        (480, 660, "8-11min"),
        (660, 840, "11-14min"),
    ]
    for lo, hi, label in ttx_buckets:
        bucket = [t for t in trades if lo <= t.entry_time_sec < hi]
        if bucket:
            bwins = sum(1 for t in bucket if t.outcome)
            print(
                f"  {label}: {bwins}/{len(bucket)} ({100 * bwins / len(bucket):.0f}%)"
            )

    # Win rate by edge bucket
    print("\nWin Rate by Edge Bucket:")
    edge_buckets = [
        (0.12, 0.20, "12-20%"),
        (0.20, 0.30, "20-30%"),
        (0.30, 0.50, "30-50%"),
        (0.50, 1.0, "50%+"),
    ]
    for lo, hi, label in edge_buckets:
        bucket = [t for t in trades if lo <= t.edge < hi]
        if bucket:
            bwins = sum(1 for t in bucket if t.outcome)
            bpnl = sum(t.pnl_cents for t in bucket)
            print(
                f"  {label}: {bwins}/{len(bucket)} ({100 * bwins / len(bucket):.0f}%) pnl={bpnl:+,.0f}c"
            )

    # Volatility used
    vols = [t.vol_used for t in trades]
    print("\nVolatility Used:")
    print(f"  Min:    {min(vols) * 100:.1f}%")
    print(f"  Median: {sorted(vols)[len(vols) // 2] * 100:.1f}%")
    print(f"  Max:    {max(vols) * 100:.1f}%")

    # Direction analysis
    if yes_trades:
        yes_wins = sum(1 for t in yes_trades if t.outcome)
        print(
            f"\nYES trades: {yes_wins}/{len(yes_trades)} ({100 * yes_wins / len(yes_trades):.0f}%) "
            f"pnl={sum(t.pnl_cents for t in yes_trades):+,.0f}c"
        )
    if no_trades:
        no_wins = sum(1 for t in no_trades if t.outcome)
        print(
            f"NO trades:  {no_wins}/{len(no_trades)} ({100 * no_wins / len(no_trades):.0f}%) "
            f"pnl={sum(t.pnl_cents for t in no_trades):+,.0f}c"
        )

    # Bankroll curve summary
    if len(bankroll_curve) > 5:
        peak = max(b for _, b in bankroll_curve)
        trough = min(b for _, b in bankroll_curve)
        max_dd = 0
        running_peak = bankroll_curve[0][1]
        for _, b in bankroll_curve:
            running_peak = max(running_peak, b)
            dd = (running_peak - b) / running_peak
            max_dd = max(max_dd, dd)
        print("\nBankroll Curve:")
        print(f"  Peak:           ${peak:,.2f}")
        print(f"  Trough:         ${trough:,.2f}")
        print(f"  Max drawdown:   {max_dd * 100:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Backtest KXBTC15M latency strategy")
    parser.add_argument(
        "--edge",
        type=float,
        default=0.12,
        help="Min edge to enter (default 0.12 = 12%%)",
    )
    parser.add_argument(
        "--stale-lag",
        type=int,
        default=120,
        help="Assumed Kalshi staleness in seconds (default 120)",
    )
    parser.add_argument(
        "--slippage", type=int, default=3, help="Slippage in cents (default 3)"
    )
    parser.add_argument(
        "--fee", type=int, default=3, help="Fee per contract in cents (default 3)"
    )
    parser.add_argument(
        "--min-ttx",
        type=int,
        default=120,
        help="Min time-to-expiry in seconds (default 120)",
    )
    parser.add_argument(
        "--max-ttx",
        type=int,
        default=840,
        help="Max time-to-expiry in seconds (default 840)",
    )
    parser.add_argument(
        "--check-interval",
        type=int,
        default=60,
        help="Check interval in seconds (default 60)",
    )
    parser.add_argument(
        "--bankroll", type=float, default=100.0, help="Starting bankroll (default $100)"
    )
    parser.add_argument(
        "--kelly", type=float, default=0.5, help="Kelly fraction (default 0.5)"
    )
    parser.add_argument(
        "--max-bet", type=float, default=50.0, help="Max bet per trade (default $50)"
    )
    parser.add_argument(
        "--vol", type=float, default=0.50, help="Default annualized vol (default 0.50)"
    )
    args = parser.parse_args()

    config = BacktestConfig(
        min_edge=args.edge,
        stale_lag_sec=args.stale_lag,
        slippage_cents=args.slippage,
        fee_cents=args.fee,
        min_ttx_sec=args.min_ttx,
        max_ttx_sec=args.max_ttx,
        check_interval_sec=args.check_interval,
        bankroll=args.bankroll,
        kelly_fraction=args.kelly,
        max_bet_dollars=args.max_bet,
        base_vol=args.vol,
    )

    run_backtest(config)


if __name__ == "__main__":
    main()
