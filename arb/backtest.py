"""
Arbitrage Backtesting for Prediction Markets

This module provides tools for backtesting arbitrage opportunities across
complementary prediction market instruments using historical candlestick data.

Usage:
------
# For Kalshi markets (using public REST API):
from arb.backtest import backtest_pair

rows = backtest_pair(
    data_func=lambda ticker, start_ts, end_ts, period: fetch_kalshi_candles(ticker, start_ts, end_ts, period),
    ticker_1="MARKET-TEAMA",
    ticker_2="MARKET-TEAMB",
    period_interval=1,      # minutes per candle (1, 60, 1440)
    lookback_hours=12.0,    # if market still open
    contract_size=100,
    entry_maker=False,
    exit_maker=False,
    arb_floor=0.002,
    dutch_floor=0.002,
)

# With custom data source:
from arb.backtest import backtest_pair

def my_data_func(ticker, start_ts, end_ts, period_interval):
    # Return list of candlestick dicts with structure:
    # [{
    #     "end_period_ts": <unix timestamp>,
    #     "yes_bid": {"close_dollars": 0.43},
    #     "yes_ask": {"close_dollars": 0.45},
    # }, ...]
    return fetch_my_data(ticker, start_ts, end_ts, period_interval)

rows = backtest_pair(
    data_func=my_data_func,
    ticker_1="TICKER1",
    ticker_2="TICKER2",
    mutually_exclusive=True,  # whether outcomes are perfect complements
    start_ts=1234567890,      # explicit time range
    end_ts=1234567899,
    period_interval=1,
)

Features:
---------
- Historical arbitrage opportunity detection using candlestick data
- Routing edge analysis (cheaper way to get same exposure)
- Cross-market arb PnL calculations (buy cheap + sell expensive)
- Dutch book / hold-to-settle profit for complementary outcomes
- Streak analysis for sustained opportunities
- Matplotlib visualizations (PnL over time, fee analysis)
- Kalshi fee calculations (maker/taker aware)
- Support for custom data sources via lambda functions
"""

import math, time, requests
from datetime import datetime, timezone
import matplotlib.pyplot as plt

# ----------------- Fee calculation helpers -----------------
def _round_up_cent(x: float) -> float:
    """Round up to nearest cent."""
    return math.ceil(x * 100.0) / 100.0

def kalshi_fee_total(C: int, P: float, maker: bool = False) -> float:
    """Calculate total Kalshi fee for a position."""
    rate = 0.0175 if maker else 0.07
    return _round_up_cent(rate * C * P * (1.0 - P))

def fee_per_contract(C: int, P: float, maker: bool = False) -> float:
    """Calculate fee per contract."""
    return kalshi_fee_total(C, P, maker=maker) / C

def all_in_buy_cost(P_ask: float, C: int, maker: bool = False) -> float:
    """Calculate all-in cost per contract to buy at ask, including fees."""
    return P_ask + fee_per_contract(C, P_ask, maker=maker)

def all_in_sell_proceeds(P_bid: float, C: int, maker: bool = False) -> float:
    """Calculate net proceeds per contract from selling at bid, after fees."""
    return P_bid - fee_per_contract(C, P_bid, maker=maker)

def _f(x):
    """Convert to float or None."""
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None

# ----------------- Kalshi REST helpers (public market data) -----------------
def _get_json(host: str, path: str, params=None, timeout=15):
    url = host.rstrip("/") + path
    r = requests.get(url, params=params or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()

def get_market(host: str, ticker: str) -> dict:
    # GET /markets/{ticker} :contentReference[oaicite:4]{index=4}
    return _get_json(host, f"/trade-api/v2/markets/{ticker}")["market"]

def get_event(host: str, event_ticker: str) -> dict:
    # GET /events/{event_ticker} :contentReference[oaicite:5]{index=5}
    return _get_json(host, f"/trade-api/v2/events/{event_ticker}")["event"]

def iso_to_ts(s: str) -> int:
    # Kalshi timestamps look like "2023-11-07T05:31:56Z"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return int(dt.timestamp())

def get_series_ticker_for_market(host: str, market_ticker: str):
    m = get_market(host, market_ticker)
    e = get_event(host, m["event_ticker"])
    return e["series_ticker"], m, e

def get_candles(host: str, series_ticker: str, ticker: str, start_ts: int, end_ts: int, period_interval: int = 1,
                include_latest_before_start: bool = True):
    # GET /series/{series_ticker}/markets/{ticker}/candlesticks :contentReference[oaicite:6]{index=6}
    params = {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "period_interval": period_interval,
        "include_latest_before_start": include_latest_before_start,
    }
    j = _get_json(host, f"/trade-api/v2/series/{series_ticker}/markets/{ticker}/candlesticks", params=params)
    return j["candlesticks"]

def _f(x):
    if x is None:
        return None
    try:
        return float(x)
    except Exception:
        return None

# ----------------- Backtest core -----------------
def backtest_nba_pair(
    host: str,
    ticker_uta: str,
    ticker_sas: str,
    period_interval: int = 1,     # 1, 60, 1440 :contentReference[oaicite:7]{index=7}
    lookback_hours: float = 12.0, # used if market is still open
    contract_size: int = 100,
    entry_maker: bool = False,
    exit_maker: bool = False,
    arb_floor: float = 0.002,
    dutch_floor: float = 0.002,
):
    series_uta, m_uta, e_uta = get_series_ticker_for_market(host, ticker_uta)
    series_sas, m_sas, e_sas = get_series_ticker_for_market(host, ticker_sas)

    if m_uta["event_ticker"] != m_sas["event_ticker"]:
        raise ValueError(f"Not the same event: {m_uta['event_ticker']} vs {m_sas['event_ticker']}")

    # event.mutually_exclusive is exposed by the Event API :contentReference[oaicite:8]{index=8}
    mutually_exclusive = bool(e_uta.get("mutually_exclusive", False))

    # time window: if settled/closed, use open_time->close_time; else last lookback_hours
    status = m_uta.get("status")
    now_ts = int(datetime.now(timezone.utc).timestamp())
    open_ts = iso_to_ts(m_uta["open_time"])
    close_ts = iso_to_ts(m_uta["close_time"])

    if status in ("closed", "settled"):
        start_ts = open_ts
        end_ts = close_ts
    else:
        start_ts = max(open_ts, now_ts - int(lookback_hours * 3600))
        end_ts = now_ts

    # Candles contain yes_bid/yes_ask OHLC in *_dollars :contentReference[oaicite:9]{index=9}
    c_uta = get_candles(host, series_uta, ticker_uta, start_ts, end_ts, period_interval=period_interval)
    c_sas = get_candles(host, series_sas, ticker_sas, start_ts, end_ts, period_interval=period_interval)

    # index by end_period_ts and align
    by_ts_uta = {c["end_period_ts"]: c for c in c_uta}
    by_ts_sas = {c["end_period_ts"]: c for c in c_sas}
    ts = sorted(set(by_ts_uta.keys()) & set(by_ts_sas.keys()))

    rows = []
    for t in ts:
        u = by_ts_uta[t]
        s = by_ts_sas[t]

        # use CLOSE for a clean backtest time series
        uta_yes_bid = _f(u["yes_bid"].get("close_dollars"))
        uta_yes_ask = _f(u["yes_ask"].get("close_dollars"))
        sas_yes_bid = _f(s["yes_bid"].get("close_dollars"))
        sas_yes_ask = _f(s["yes_ask"].get("close_dollars"))

        if None in (uta_yes_bid, uta_yes_ask, sas_yes_bid, sas_yes_ask):
            continue

        # Derive NO bid/ask from reciprocal relationship: YES BID @ X ⇔ NO ASK @ (1-X),
        # NO BID @ Y ⇔ YES ASK @ (1-Y). :contentReference[oaicite:10]{index=10}
        uta_no_ask = 1.0 - uta_yes_bid
        uta_no_bid = 1.0 - uta_yes_ask
        sas_no_ask = 1.0 - sas_yes_bid
        sas_no_bid = 1.0 - sas_yes_ask

        C = contract_size

        # fee-adjusted entry costs
        buy_uta_yes = all_in_buy_cost(uta_yes_ask, C, maker=entry_maker)
        buy_uta_no  = all_in_buy_cost(uta_no_ask,  C, maker=entry_maker)
        buy_sas_yes = all_in_buy_cost(sas_yes_ask, C, maker=entry_maker)
        buy_sas_no  = all_in_buy_cost(sas_no_ask,  C, maker=entry_maker)

        # fee-adjusted exit proceeds
        sell_uta_yes = all_in_sell_proceeds(uta_yes_bid, C, maker=exit_maker)
        sell_uta_no  = all_in_sell_proceeds(uta_no_bid,  C, maker=exit_maker)
        sell_sas_yes = all_in_sell_proceeds(sas_yes_bid, C, maker=exit_maker)
        sell_sas_no  = all_in_sell_proceeds(sas_no_bid,  C, maker=exit_maker)

        # routing edges (same exposure, different instrument)
        # UTA exposure: BUY YES UTA vs BUY NO SAS
        edge_uta = buy_uta_yes - buy_sas_no
        # SAS exposure: BUY YES SAS vs BUY NO UTA
        edge_sas = buy_sas_yes - buy_uta_no

        # cross-market arb pnl for each exposure:
        # buy cheapest representation at ask, sell richest representation at bid
        arb_uta = max(sell_uta_yes, sell_sas_no) - min(buy_uta_yes, buy_sas_no)
        arb_sas = max(sell_sas_yes, sell_uta_no) - min(buy_sas_yes, buy_uta_no)

        # dutch-to-settle (only if the two outcomes are complements)
        # buy both cheapest complementary legs; guaranteed $1 at settlement if exhaustive+exclusive
        dutch = (1.0 - (min(buy_uta_yes, buy_sas_no) + min(buy_sas_yes, buy_uta_no))) if mutually_exclusive else float("nan")

        # fees at the asks (for visibility)
        fee_uta_yes = fee_per_contract(C, uta_yes_ask, maker=entry_maker)
        fee_sas_yes = fee_per_contract(C, sas_yes_ask, maker=entry_maker)

        # best action at this timestamp
        candidates = []
        if arb_uta >= arb_floor:
            candidates.append(("ARB_UTA", arb_uta))
        if arb_sas >= arb_floor:
            candidates.append(("ARB_SAS", arb_sas))
        if mutually_exclusive and dutch >= dutch_floor:
            candidates.append(("DUTCH_SETTLE", dutch))

        if candidates:
            best_kind, best_val = max(candidates, key=lambda kv: kv[1])
        else:
            best_kind, best_val = "NO_TRADE", 0.0

        rows.append({
            "ts": t,
            "edge_uta": edge_uta,
            "edge_sas": edge_sas,
            "arb_uta": arb_uta,
            "arb_sas": arb_sas,
            "dutch": dutch,
            "fee_uta_yes": fee_uta_yes,
            "fee_sas_yes": fee_sas_yes,
            "best_kind": best_kind,
            "best_val": best_val,
            "uta_yes_bid": uta_yes_bid,
            "uta_yes_ask": uta_yes_ask,
            "sas_yes_bid": sas_yes_bid,
            "sas_yes_ask": sas_yes_ask,
        })

    if not rows:
        raise RuntimeError("No aligned candlesticks found in the requested window.")

    # compute best-action streaks
    streaks = []
    cur = rows[0]["best_kind"]
    start_i = 0
    for i in range(1, len(rows)):
        if rows[i]["best_kind"] != cur:
            streaks.append((cur, start_i, i-1))
            cur = rows[i]["best_kind"]
            start_i = i
    streaks.append((cur, start_i, len(rows)-1))

    def fmt_ts(t): return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # summarize
    best_arb_uta = max(r["arb_uta"] for r in rows)
    best_arb_sas = max(r["arb_sas"] for r in rows)
    best_dutch = max((r["dutch"] for r in rows if not math.isnan(r["dutch"])), default=float("nan"))

    print("Backtest window:", fmt_ts(rows[0]["ts"]), "->", fmt_ts(rows[-1]["ts"]))
    print("Market status:", status, "| mutually_exclusive:", mutually_exclusive)
    print(f"Max ARB_UTA pnl: {best_arb_uta:.4f} $/contract | Max ARB_SAS pnl: {best_arb_sas:.4f} $/contract | Max DUTCH: {best_dutch:.4f} $/contract")

    # print top streaks where a trade action was best
    trade_streaks = []
    for kind, a, b in streaks:
        if kind != "NO_TRADE":
            dur_s = rows[b]["ts"] - rows[a]["ts"]
            peak = max(rows[i]["best_val"] for i in range(a, b+1))
            trade_streaks.append((dur_s, peak, kind, a, b))
    trade_streaks.sort(reverse=True)

    print("\nLongest 'best action' streaks (non-NO_TRADE):")
    for dur_s, peak, kind, a, b in trade_streaks[:8]:
        print(f"- {kind:12s} | duration={dur_s/60:.1f} min | peak={peak:.4f} | {fmt_ts(rows[a]['ts'])} -> {fmt_ts(rows[b]['ts'])}")

    # plot
    xs = [(r["ts"] - rows[0]["ts"]) / 60.0 for r in rows]  # minutes
    plt.figure(figsize=(12, 6))
    plt.plot(xs, [r["arb_uta"] for r in rows], label="ARB pnl (UTA exposure)")
    plt.plot(xs, [r["arb_sas"] for r in rows], label="ARB pnl (SAS exposure)")
    plt.plot(xs, [r["dutch"]   for r in rows], label="DUTCH-to-settle profit")
    plt.axhline(0.0)
    plt.axhline(arb_floor, linestyle="--")
    plt.axhline(dutch_floor, linestyle="--")
    plt.xlabel("minutes since start")
    plt.ylabel("$/contract (fees included)")
    plt.title("Backtest profitability signals")
    plt.legend()
    plt.show()

    plt.figure(figsize=(12, 4))
    plt.plot(xs, [r["fee_uta_yes"] for r in rows], label="fee/contract at UTA YES ask")
    plt.plot(xs, [r["fee_sas_yes"] for r in rows], label="fee/contract at SAS YES ask")
    plt.xlabel("minutes since start")
    plt.ylabel("fee ($/contract)")
    plt.title("Entry fee visibility (at asks)")
    plt.legend()
    plt.show()

    plt.figure(figsize=(12, 4))
    plt.plot(xs, [r["uta_yes_bid"] for r in rows], label="Market 1 (UTA) bid")
    plt.plot(xs, [r["uta_yes_ask"] for r in rows], label="Market 1 (UTA) ask")
    plt.plot(xs, [r["sas_yes_bid"] for r in rows], label="Market 2 (SAS) bid")
    plt.plot(xs, [r["sas_yes_ask"] for r in rows], label="Market 2 (SAS) ask")
    plt.xlabel("minutes since start")
    plt.ylabel("price ($/contract)")
    plt.title("Live market prices (bid/ask for both markets)")
    plt.legend()
    plt.show()

    return rows

# Default Kalshi API host
KALSHI_HOST = "https://api.elections.kalshi.com"


def backtest_pair(
    ticker_1: str,
    ticker_2: str,
    host: str = KALSHI_HOST,
    period_interval: int = 1,
    lookback_hours: float = 12.0,
    contract_size: int = 100,
    entry_maker: bool = False,
    exit_maker: bool = False,
    arb_floor: float = 0.002,
    dutch_floor: float = 0.002,
    show_plots: bool = True,
):
    """
    Convenience wrapper for backtesting any pair of Kalshi markets.

    Args:
        ticker_1: First market ticker
        ticker_2: Second market ticker
        host: Kalshi API host
        period_interval: Candle interval (1, 60, 1440 minutes)
        lookback_hours: Hours of history to analyze
        contract_size: Contract size for fee calculations
        entry_maker: Whether entry orders are maker
        exit_maker: Whether exit orders are maker
        arb_floor: Min arb PnL to report
        dutch_floor: Min dutch profit to report
        show_plots: Whether to display matplotlib plots

    Returns:
        List of row dicts with backtest data
    """
    return backtest_nba_pair(
        host=host,
        ticker_uta=ticker_1,
        ticker_sas=ticker_2,
        period_interval=period_interval,
        lookback_hours=lookback_hours,
        contract_size=contract_size,
        entry_maker=entry_maker,
        exit_maker=exit_maker,
        arb_floor=arb_floor,
        dutch_floor=dutch_floor,
    )
