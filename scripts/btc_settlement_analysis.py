#!/usr/bin/env python3
"""
Phase 1: Kalshi BTC Settlement (BRTI) vs Kraken Spot Analysis

Kalshi settles KXBTC15M markets using the 60-second simple average of
CF Benchmarks' BRTI (Bitcoin Real-Time Index) preceding each 15-minute
mark.  The raw API response includes the actual settlement price in
`expiration_value`, so we can compare BRTI directly against Kraken spot.

Usage:
    python3 scripts/btc_settlement_analysis.py [--days 7]
    python3 scripts/btc_settlement_analysis.py --skip-fetch   # re-run analysis only

Output:
    data/btc_settlement_analysis.db  (SQLite)
    Console report characterizing BRTI vs Kraken relationship
"""

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time as _time
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from core.exchange_client.kalshi import KalshiExchangeClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_PATH = PROJECT_ROOT / "data" / "btc_settlement_analysis.db"
KRAKEN_OHLC_URL = "https://api.kraken.com/0/public/OHLC"
DEFAULT_SERIES = "KXBTC15M"


# ── Database ────────────────────────────────────────────────────────────────

SCHEMA = """
DROP TABLE IF EXISTS kalshi_markets;
DROP TABLE IF EXISTS kraken_ohlc;
DROP TABLE IF EXISTS settlement_analysis;

CREATE TABLE kalshi_markets (
    ticker            TEXT PRIMARY KEY,
    event_ticker      TEXT,
    title             TEXT,
    status            TEXT,
    result            TEXT,       -- 'yes' / 'no'
    close_time        TEXT,       -- ISO 8601 (when market closes for trading)
    close_time_ts     INTEGER,    -- same as unix seconds
    open_time         TEXT,       -- when market opens
    settlement_ts_str TEXT,       -- actual settlement timestamp from API
    floor_strike      REAL,       -- opening BTC price = strike
    expiration_value  REAL,       -- BRTI 60s avg at settlement (the answer)
    volume            REAL,
    open_interest     REAL,
    raw_json          TEXT
);
CREATE INDEX idx_km_close ON kalshi_markets(close_time_ts);

CREATE TABLE kraken_ohlc (
    timestamp_s   INTEGER PRIMARY KEY,
    interval_min  INTEGER,    -- 1 or 5
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    vwap          REAL,
    volume        REAL,
    trade_count   INTEGER
);

CREATE TABLE settlement_analysis (
    ticker            TEXT PRIMARY KEY,
    close_time_ts     INTEGER,
    floor_strike      REAL,       -- opening price
    brti_price        REAL,       -- expiration_value (60s BRTI avg)
    result            TEXT,
    kraken_price      REAL,       -- nearest Kraken candle open
    kraken_vwap       REAL,
    kraken_interval   INTEGER,    -- which candle resolution matched
    kraken_offset_s   INTEGER,    -- seconds between settlement and candle
    brti_vs_kraken    REAL,       -- brti_price - kraken_price
    brti_vs_strike    REAL,       -- brti_price - floor_strike
    kraken_vs_strike  REAL,       -- kraken_price - floor_strike
    kraken_agrees     INTEGER     -- does Kraken confirm the yes/no result?
);
CREATE INDEX idx_sa_close ON settlement_analysis(close_time_ts);
"""


def init_db(db_path: Path, fresh: bool = True) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if fresh:
        conn.executescript(SCHEMA)
    return conn


# ── Kalshi ──────────────────────────────────────────────────────────────────


async def fetch_settled_btc_markets(
    client: KalshiExchangeClient,
    days: int = 7,
    series: str = DEFAULT_SERIES,
) -> List[Dict[str, Any]]:
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    all_markets: List[Dict[str, Any]] = []
    cursor = None
    page = 0

    while True:
        page += 1
        params: Dict[str, Any] = {
            "series_ticker": series,
            "status": "settled",
            "limit": 1000,
            "min_close_ts": cutoff_ts,
        }
        if cursor:
            params["cursor"] = cursor

        logger.info(f"Kalshi page {page} ({len(all_markets)} markets so far)")
        data = await client._request("GET", "/markets", params=params)
        markets = data.get("markets", [])
        all_markets.extend(markets)

        cursor = data.get("cursor")
        if not cursor or not markets:
            break

    logger.info(f"Fetched {len(all_markets)} settled {series} markets")
    return all_markets


async def discover_btc_series(client: KalshiExchangeClient) -> None:
    logger.info("Discovering available BTC series...")
    for series in [
        "KXBTC15M",
        "KXBTCD",
        "KXBTC",
        "KXBTCMAXY",
        "KXBTCMINY",
        "KXBTC1H",
        "KXBTC5M",
        "KXBTCW",
    ]:
        try:
            data = await client._request(
                "GET",
                "/markets",
                params={"series_ticker": series, "limit": 3},
            )
            n = len(data.get("markets", []))
            if n > 0:
                sample = data["markets"][0]
                logger.info(f"  {series}: {n}+ markets (e.g. {sample.get('ticker')})")
        except Exception:
            pass


def _parse_ts(iso_str: Optional[str]) -> Optional[int]:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, AttributeError):
        return None


def store_kalshi_markets(
    conn: sqlite3.Connection, markets: List[Dict[str, Any]]
) -> int:
    stored = 0
    for m in markets:
        result = (m.get("result") or "").lower().strip()

        # expiration_value is the BRTI settlement price
        exp_val = None
        raw_ev = m.get("expiration_value")
        if raw_ev is not None:
            try:
                exp_val = float(raw_ev)
            except (ValueError, TypeError):
                pass

        conn.execute(
            """
            INSERT OR REPLACE INTO kalshi_markets
            (ticker, event_ticker, title, status, result,
             close_time, close_time_ts, open_time, settlement_ts_str,
             floor_strike, expiration_value,
             volume, open_interest, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                m.get("ticker", ""),
                m.get("event_ticker", ""),
                m.get("title", ""),
                m.get("status", ""),
                result,
                m.get("close_time", ""),
                _parse_ts(m.get("close_time")),
                m.get("open_time", ""),
                m.get("settlement_ts", ""),
                m.get("floor_strike"),
                exp_val,
                m.get("volume") or m.get("volume_fp") or 0,
                m.get("open_interest") or m.get("open_interest_fp") or 0,
                json.dumps(m),
            ),
        )
        stored += 1

    conn.commit()
    logger.info(f"Stored {stored} markets")
    return stored


# ── Kraken ──────────────────────────────────────────────────────────────────


async def fetch_kraken_ohlc(
    http: httpx.AsyncClient,
    start_ts: int,
    end_ts: int,
    interval: int = 5,
) -> List[list]:
    """Fetch Kraken XBTUSD OHLC candles.  interval=5 gives ~60h per request."""
    all_candles: List[list] = []
    since = start_ts
    total_hours = (end_ts - start_ts) / 3600

    while since < end_ts:
        try:
            resp = await http.get(
                KRAKEN_OHLC_URL,
                params={"pair": "XBTUSD", "interval": interval, "since": since},
            )
            if resp.status_code == 429:
                logger.warning("Kraken rate limited, backing off 5s")
                await asyncio.sleep(5)
                continue

            data = resp.json()
            if data.get("error"):
                logger.warning(f"Kraken API error: {data['error']}")
                await asyncio.sleep(2)
                continue

            candles = data.get("result", {}).get("XXBTZUSD", [])
            if not candles:
                break

            filtered = [c for c in candles if c[0] <= end_ts + 300]
            all_candles.extend(filtered)

            new_since = data.get("result", {}).get("last", since)
            if new_since <= since:
                break
            since = new_since

            elapsed = (since - start_ts) / 3600
            logger.info(
                f"Kraken {interval}m: {len(all_candles):,} candles "
                f"({elapsed:.0f}/{total_hours:.0f} hrs)"
            )
            await asyncio.sleep(1.1)

        except httpx.HTTPError as e:
            logger.warning(f"Kraken HTTP error: {e}, retrying in 5s")
            await asyncio.sleep(5)

    logger.info(f"Fetched {len(all_candles):,} Kraken {interval}m candles")
    return all_candles


def store_kraken_ohlc(
    conn: sqlite3.Connection,
    candles: List[list],
    interval: int,
) -> int:
    stored = 0
    for c in candles:
        conn.execute(
            """
            INSERT OR REPLACE INTO kraken_ohlc
            (timestamp_s, interval_min, open, high, low, close, vwap, volume, trade_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                int(c[0]),
                interval,
                float(c[1]),
                float(c[2]),
                float(c[3]),
                float(c[4]),
                float(c[5]),
                float(c[6]),
                int(c[7]),
            ),
        )
        stored += 1
    conn.commit()
    logger.info(f"Stored {stored:,} Kraken {interval}m candles")
    return stored


# ── Analysis ────────────────────────────────────────────────────────────────


def analyze(conn: sqlite3.Connection) -> int:
    """For each settlement, compare BRTI expiration_value against Kraken spot.

    Candle selection strategy:
    - BRTI averages the 60s of BRTI *before* close_time.
    - We want the Kraken candle whose interval covers that same period.
    - For a 1-min candle: the candle at (close_ts - 60) covers [T-60, T).
    - For a 5-min candle: the candle at (close_ts - 300) covers [T-300, T).
    - Prefer finest resolution available (1m > 5m > 15m).
    """
    markets = conn.execute("""
        SELECT * FROM kalshi_markets
        WHERE result IN ('yes', 'no')
          AND expiration_value IS NOT NULL
          AND close_time_ts IS NOT NULL
        ORDER BY close_time_ts
    """).fetchall()

    if not markets:
        logger.warning("No markets with expiration_value found")
        return 0

    # Pre-load candles grouped by interval for resolution preference
    candle_sets = {}  # interval -> {timestamp: row}
    for interval in (1, 5, 15):
        rows = conn.execute(
            "SELECT * FROM kraken_ohlc WHERE interval_min = ? ORDER BY timestamp_s",
            (interval,),
        ).fetchall()
        if rows:
            ts_list = [r["timestamp_s"] for r in rows]
            by_ts = {r["timestamp_s"]: r for r in rows}
            candle_sets[interval] = (ts_list, by_ts)

    logger.info(
        f"Analyzing {len(markets)} settlements against Kraken candles: "
        + ", ".join(f"{iv}m={len(cs[0])}" for iv, cs in sorted(candle_sets.items()))
    )

    inserted = 0
    for m in markets:
        brti = m["expiration_value"]
        strike = m["floor_strike"]
        close_ts = m["close_time_ts"]
        result = m["result"]

        # Try each resolution, finest first.
        # Look for the candle starting at (close_ts - interval*60),
        # which covers the period just before settlement.
        kraken_price = None
        kraken_vwap = None
        kraken_interval = None
        kraken_offset = None

        for interval in (1, 5, 15):
            if interval not in candle_sets:
                continue
            ts_list, by_ts = candle_sets[interval]
            target_ts = close_ts - interval * 60

            idx = bisect_left(ts_list, target_ts)
            candidates = []
            if idx > 0:
                candidates.append(ts_list[idx - 1])
            if idx < len(ts_list):
                candidates.append(ts_list[idx])

            if not candidates:
                continue

            nearest = min(candidates, key=lambda t: abs(t - target_ts))
            offset = nearest - target_ts
            max_drift = interval * 60  # allow up to one candle width of drift

            if abs(offset) <= max_drift:
                row = by_ts[nearest]
                kraken_price = row["close"]  # last price before candle end
                kraken_vwap = row["vwap"]
                kraken_interval = interval
                kraken_offset = nearest - close_ts
                break  # found finest available

        # Comparisons
        brti_vs_kraken = (brti - kraken_price) if kraken_price is not None else None
        brti_vs_strike = brti - strike if strike is not None else None
        kraken_vs_strike = (
            (kraken_price - strike)
            if (kraken_price is not None and strike is not None)
            else None
        )

        # Does Kraken agree with the result?
        kraken_agrees = None
        if kraken_price is not None and strike is not None:
            kraken_says_up = kraken_price >= strike
            kalshi_says_up = result == "yes"
            kraken_agrees = 1 if kraken_says_up == kalshi_says_up else 0

        conn.execute(
            """
            INSERT OR REPLACE INTO settlement_analysis
            (ticker, close_time_ts, floor_strike, brti_price, result,
             kraken_price, kraken_vwap, kraken_interval, kraken_offset_s,
             brti_vs_kraken, brti_vs_strike, kraken_vs_strike, kraken_agrees)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                m["ticker"],
                close_ts,
                strike,
                brti,
                result,
                kraken_price,
                kraken_vwap,
                kraken_interval,
                kraken_offset,
                brti_vs_kraken,
                brti_vs_strike,
                kraken_vs_strike,
                kraken_agrees,
            ),
        )
        inserted += 1

    conn.commit()
    logger.info(f"Analyzed {inserted} settlements")
    return inserted


# ── Report ──────────────────────────────────────────────────────────────────


def _stats(values: List[float]) -> Dict[str, float]:
    s = sorted(values)
    n = len(s)
    return {
        "n": n,
        "mean": sum(s) / n,
        "median": s[n // 2],
        "p5": s[int(n * 0.05)],
        "p95": s[int(n * 0.95)],
        "min": s[0],
        "max": s[-1],
    }


def print_report(conn: sqlite3.Connection):
    total = conn.execute("SELECT COUNT(*) as n FROM kalshi_markets").fetchone()["n"]
    with_brti = conn.execute(
        "SELECT COUNT(*) as n FROM kalshi_markets WHERE expiration_value IS NOT NULL"
    ).fetchone()["n"]
    conn.execute("SELECT COUNT(*) as n FROM settlement_analysis").fetchone()["n"]
    with_kraken = conn.execute(
        "SELECT COUNT(*) as n FROM settlement_analysis WHERE kraken_price IS NOT NULL"
    ).fetchone()["n"]
    total_candles = conn.execute("SELECT COUNT(*) as n FROM kraken_ohlc").fetchone()[
        "n"
    ]

    print("\n" + "=" * 72)
    print("  PHASE 1: Kalshi BRTI Settlement vs Kraken Spot")
    print("  Settlement reference: 60s avg of CF Benchmarks BRTI before close")
    print("=" * 72)

    print("\n  Data")
    print(f"    Kalshi settled markets:   {total:>8,}")
    print(f"    With expiration_value:    {with_brti:>8,}")
    print(f"    Matched to Kraken:        {with_kraken:>8,}")
    print(f"    Kraken candles:           {total_candles:>8,}")

    if with_kraken == 0:
        print("\n  No settlements matched to Kraken data.")
        _print_time_ranges(conn)
        print("=" * 72 + "\n")
        return

    # ── BRTI vs Kraken ──
    rows = conn.execute(
        "SELECT brti_vs_kraken FROM settlement_analysis WHERE brti_vs_kraken IS NOT NULL"
    ).fetchall()
    if rows:
        vals = [r["brti_vs_kraken"] for r in rows]
        abs_vals = [abs(v) for v in vals]
        s = _stats(vals)
        sa = _stats(abs_vals)

        print(f"\n  BRTI vs Kraken VWAP  (n={s['n']:,})")
        print(f"    Mean (signed):     ${s['mean']:>+10,.2f}")
        print(f"    Mean |diff|:       ${sa['mean']:>10,.2f}")
        print(f"    Median |diff|:     ${sa['median']:>10,.2f}")
        print(f"    P5 / P95 (signed): ${s['p5']:>+10,.2f}  /  ${s['p95']:>+,.2f}")
        print(f"    Max |diff|:        ${sa['max']:>10,.2f}")

    # ── Result agreement ──
    agree = conn.execute(
        "SELECT COUNT(*) as n FROM settlement_analysis WHERE kraken_agrees = 1"
    ).fetchone()["n"]
    disagree = conn.execute(
        "SELECT COUNT(*) as n FROM settlement_analysis WHERE kraken_agrees = 0"
    ).fetchone()["n"]
    checked = agree + disagree

    if checked > 0:
        pct = agree / checked * 100
        print("\n  Result agreement (Kraken VWAP vs strike → same yes/no?)")
        print(f"    Agree:      {agree:>6,} / {checked:,}  ({pct:.1f}%)")
        print(f"    Disagree:   {disagree:>6,} / {checked:,}")

    # ── BRTI vs Strike (confirms BRTI is self-consistent) ──
    rows = conn.execute(
        "SELECT brti_vs_strike, result FROM settlement_analysis WHERE brti_vs_strike IS NOT NULL"
    ).fetchall()
    if rows:
        up_correct = sum(
            1 for r in rows if r["result"] == "yes" and r["brti_vs_strike"] >= 0
        )
        down_correct = sum(
            1 for r in rows if r["result"] == "no" and r["brti_vs_strike"] < 0
        )
        brti_self = up_correct + down_correct
        print("\n  BRTI self-consistency (expiration_value vs floor_strike)")
        print(
            f"    Correct:    {brti_self:>6,} / {len(rows):,}  ({brti_self / len(rows) * 100:.1f}%)"
        )

    # ── Where Kraken disagrees ──
    if disagree > 0:
        rows = conn.execute("""
            SELECT s.ticker, s.close_time_ts, s.floor_strike, s.brti_price,
                   s.result, s.kraken_price, s.brti_vs_kraken, s.kraken_vs_strike,
                   m.close_time
            FROM settlement_analysis s
            JOIN kalshi_markets m ON m.ticker = s.ticker
            WHERE s.kraken_agrees = 0
            ORDER BY ABS(s.brti_vs_kraken) DESC
            LIMIT 15
        """).fetchall()
        if rows:
            print("\n  Disagreements — Kraken would settle differently (up to 15)")
            print(
                f"  {'Time (UTC)':<20} {'Strike':>10} {'BRTI':>10} "
                f"{'Kraken':>10} {'B-K':>8} {'Result':>6}"
            )
            print(f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 6}")
            for r in rows:
                t = (r["close_time"] or "?")[:19]
                print(
                    f"  {t:<20} {r['floor_strike']:>10,.2f} {r['brti_price']:>10,.2f} "
                    f"{r['kraken_price']:>10,.2f} {r['brti_vs_kraken']:>+8,.2f} "
                    f"{r['result']:>6}"
                )

    # ── Recent examples ──
    rows = conn.execute("""
        SELECT s.*, m.close_time
        FROM settlement_analysis s
        JOIN kalshi_markets m ON m.ticker = s.ticker
        WHERE s.kraken_price IS NOT NULL
        ORDER BY s.close_time_ts DESC LIMIT 12
    """).fetchall()
    if rows:
        print("\n  Recent settlements")
        print(
            f"  {'Time (UTC)':<20} {'Strike':>10} {'BRTI':>10} "
            f"{'Kraken':>10} {'B-K':>8} {'Rslt':>4} {'OK':>3}"
        )
        print(
            f"  {'-' * 20} {'-' * 10} {'-' * 10} {'-' * 10} {'-' * 8} {'-' * 4} {'-' * 3}"
        )
        for r in rows:
            t = (r["close_time"] or "?")[:19]
            ok = "Y" if r["kraken_agrees"] == 1 else "N"
            print(
                f"  {t:<20} {r['floor_strike']:>10,.2f} {r['brti_price']:>10,.2f} "
                f"{r['kraken_price']:>10,.2f} {r['brti_vs_kraken']:>+8,.2f} "
                f"{r['result']:>4} {ok:>3}"
            )

    # ── Kraken resolution breakdown ──
    res_rows = conn.execute("""
        SELECT kraken_interval, COUNT(*) as n
        FROM settlement_analysis WHERE kraken_price IS NOT NULL
        GROUP BY kraken_interval ORDER BY kraken_interval
    """).fetchall()
    if res_rows:
        print("\n  Kraken candle resolution used")
        for r in res_rows:
            print(f"    {r['kraken_interval']:>2}m candles: {r['n']:>6,} settlements")

    print(f"\n  Database: {DB_PATH}")
    print("=" * 72 + "\n")


def _print_time_ranges(conn: sqlite3.Connection):
    kr = conn.execute(
        "SELECT MIN(timestamp_s) as lo, MAX(timestamp_s) as hi FROM kraken_ohlc"
    ).fetchone()
    km = conn.execute(
        "SELECT MIN(close_time_ts) as lo, MAX(close_time_ts) as hi FROM kalshi_markets"
    ).fetchone()

    def _fmt(ts):
        return (
            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            if ts
            else "?"
        )

    print("\n  Time ranges (no overlap → no matches)")
    print(f"    Kalshi:  {_fmt(km['lo'])}  →  {_fmt(km['hi'])}")
    if kr["lo"]:
        print(f"    Kraken:  {_fmt(kr['lo'])}  →  {_fmt(kr['hi'])}")
    else:
        print("    Kraken:  (no data)")


# ── Main ────────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(
        description="Kalshi BTC Settlement (BRTI) vs Kraken Spot Analysis",
    )
    parser.add_argument(
        "--days", type=int, default=7, help="Days of history (default: 7)"
    )
    parser.add_argument("--series", default=DEFAULT_SERIES, help="Kalshi series ticker")
    parser.add_argument("--user", default=None, help="Kalshi user profile (keys/ dir)")
    parser.add_argument("--db", default=str(DB_PATH), help="Database path")
    parser.add_argument(
        "--skip-fetch", action="store_true", help="Re-run analysis only"
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    conn = init_db(db_path, fresh=not args.skip_fetch)

    if not args.skip_fetch:
        # ── Kalshi ──
        t0 = _time.monotonic()
        logger.info(f"Connecting to Kalshi ({args.series}, last {args.days} days)...")

        if args.user:
            client = KalshiExchangeClient.from_user(args.user)
        else:
            client = KalshiExchangeClient.from_env()

        async with client:
            markets = await fetch_settled_btc_markets(
                client,
                days=args.days,
                series=args.series,
            )
            if not markets:
                logger.warning(f"No settled markets for {args.series}")
                await discover_btc_series(client)
                conn.close()
                return

        store_kalshi_markets(conn, markets)
        logger.info(f"Kalshi done in {_time.monotonic() - t0:.1f}s")

        # ── Kraken ──
        row = conn.execute(
            "SELECT MIN(close_time_ts) as lo, MAX(close_time_ts) as hi "
            "FROM kalshi_markets"
        ).fetchone()

        if row and row["lo"] and row["hi"]:
            start_ts = row["lo"] - 600
            end_ts = row["hi"] + 600
            hours = (end_ts - start_ts) / 3600

            t0 = _time.monotonic()

            async with httpx.AsyncClient(timeout=30) as http:
                # 15-min candles for full 7-day coverage (720*15=180hrs)
                logger.info(f"Fetching Kraken 15m OHLC ({hours:.0f} hrs)...")
                candles_15m = await fetch_kraken_ohlc(
                    http, start_ts, end_ts, interval=15
                )
                if candles_15m:
                    store_kraken_ohlc(conn, candles_15m, interval=15)

                # 5-min candles for higher precision where available (~60hrs)
                logger.info("Fetching Kraken 5m OHLC...")
                candles_5m = await fetch_kraken_ohlc(http, start_ts, end_ts, interval=5)
                if candles_5m:
                    store_kraken_ohlc(conn, candles_5m, interval=5)

                # 1-min candles for recent precision (Kraken keeps ~12h)
                logger.info("Fetching Kraken 1m OHLC (recent)...")
                candles_1m = await fetch_kraken_ohlc(http, start_ts, end_ts, interval=1)
                if candles_1m:
                    store_kraken_ohlc(conn, candles_1m, interval=1)

            logger.info(f"Kraken done in {_time.monotonic() - t0:.1f}s")

    # ── Analyze + Report ──
    analyze(conn)
    print_report(conn)
    conn.close()


if __name__ == "__main__":
    asyncio.run(main())
