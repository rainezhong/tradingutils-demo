#!/usr/bin/env python3
"""
Fetch Historical NBA Data from Kalshi

Pulls all settled KXNBAGAME markets and their candlestick data from Kalshi API.
Caches everything to data/nba_cache/ for use by build_synthetic_recordings.py.

Usage:
    export KALSHI_API_KEY="your-key-id"
    export KALSHI_API_SECRET="/path/to/private_key.pem"

    python scripts/fetch_historical_nba.py
    python scripts/fetch_historical_nba.py --force    # Re-fetch everything
    python scripts/fetch_historical_nba.py --dry-run  # Just count, don't fetch candles

Then run:
    python scripts/build_synthetic_recordings.py
"""

import sys
import os
import pickle
import time
import argparse
import re
import requests
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict

sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from src.kalshi.auth import KalshiAuth
except ImportError:
    from core.exchange_client.kalshi.kalshi_auth import KalshiAuth

CACHE_DIR = Path("data/nba_cache")
SERIES_TICKER = "KXNBAGAME"
API_BASE = "https://api.elections.kalshi.com/trade-api/v2"


class HistoricalFetcher:
    """Fetches historical NBA data from Kalshi using direct HTTP requests."""

    def __init__(self, force: bool = False):
        self.force = force
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.auth = KalshiAuth.from_env()

    def _request(self, method: str, path: str, params: dict = None) -> dict:
        """Make authenticated API request."""
        url = f"{API_BASE}{path}"
        full_path = f"/trade-api/v2{path}"

        if params:
            # Build query string for GET requests
            query_parts = []
            for k, v in params.items():
                if v is not None:
                    query_parts.append(f"{k}={v}")
            if query_parts:
                query = "&".join(query_parts)
                url += f"?{query}"
                full_path += f"?{query}"

        headers = self.auth.sign_request(method, full_path)
        headers["Content-Type"] = "application/json"

        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json()

    def fetch_settled_markets(self) -> List[dict]:
        """Fetch all settled KXNBAGAME markets with pagination."""
        cache_path = CACHE_DIR / "settled_markets_raw.pkl"

        if cache_path.exists() and not self.force:
            age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
            if age < timedelta(hours=24):
                with open(cache_path, "rb") as f:
                    markets = pickle.load(f)
                print(
                    f"  Loaded {len(markets)} markets from cache ({age.seconds // 3600}h old)"
                )
                return markets

        print("  Fetching settled NBA markets from Kalshi API...")
        all_markets = []
        cursor = None

        while True:
            params = {
                "series_ticker": SERIES_TICKER,
                "status": "settled",
                "limit": 1000,
            }
            if cursor:
                params["cursor"] = cursor

            data = self._request("GET", "/markets", params)
            markets = data.get("markets", [])
            all_markets.extend(markets)
            print(f"    Fetched {len(all_markets)} markets...")

            cursor = data.get("cursor")
            if not cursor or not markets:
                break
            time.sleep(0.5)

        with open(cache_path, "wb") as f:
            pickle.dump(all_markets, f)
        print(f"  Cached {len(all_markets)} markets")

        return all_markets

    def group_by_event(self, markets: List[dict]) -> Dict[str, Dict]:
        """Group markets by event (game), creating home/away pairs."""
        games = defaultdict(dict)

        for m in markets:
            event_ticker = m.get("event_ticker", "")
            title = m.get("title", "")
            yes_sub = m.get("yes_sub_title", "")

            if " at " in title:
                parts = title.replace(" Winner?", "").split(" at ")
                away_name = parts[0].strip()
                home_name = parts[1].strip()

                if yes_sub.strip() == home_name:
                    side = "home"
                elif yes_sub.strip() == away_name:
                    side = "away"
                else:
                    continue
            else:
                continue

            games[event_ticker][side] = {
                "ticker": m.get("ticker", ""),
                "team": yes_sub.strip(),
                "result": m.get("result", ""),
                "close_time": m.get("close_time"),
                "title": title,
                "volume": m.get("volume", 0),
            }

        return {k: v for k, v in games.items() if "home" in v and "away" in v}

    def fetch_candlesticks(self, ticker: str, settlement_time: datetime) -> List[dict]:
        """Fetch 1-minute candlesticks for a market, with caching."""
        cache_path = CACHE_DIR / f"candles_{ticker}.pkl"

        if cache_path.exists() and not self.force:
            with open(cache_path, "rb") as f:
                return pickle.load(f)

        try:
            end_ts = int(settlement_time.timestamp())
            start_ts = int((settlement_time - timedelta(hours=6)).timestamp())

            data = self._request(
                "GET",
                f"/series/{SERIES_TICKER}/markets/{ticker}/candlesticks",
                params={
                    "start_ts": start_ts,
                    "end_ts": end_ts,
                    "period_interval": 1,
                },
            )

            candles = []
            for c in data.get("candlesticks", []):
                yes_bid = c.get("yes_bid", {}) or {}
                yes_ask = c.get("yes_ask", {}) or {}
                candles.append(
                    {
                        "ts": c.get("end_period_ts", 0),
                        "yes_bid_close": yes_bid.get("close"),
                        "yes_ask_close": yes_ask.get("close"),
                        "volume": c.get("volume", 0),
                        "open_interest": c.get("open_interest", 0),
                    }
                )

            with open(cache_path, "wb") as f:
                pickle.dump(candles, f)

            return candles

        except Exception as e:
            print(f"      Error fetching candles for {ticker}: {e}")
            return []

    def run(self, dry_run: bool = False):
        """Main pipeline."""
        print("\n" + "=" * 60)
        print("  FETCH HISTORICAL NBA DATA FROM KALSHI")
        print("=" * 60)

        # Step 1: Fetch all settled markets
        print("\n[1/3] Fetching settled markets...")
        markets = self.fetch_settled_markets()
        print(f"  Total settled NBA markets: {len(markets)}")

        # Step 2: Find ALL markets needing candlestick data (not just paired games)
        print("\n[2/3] Checking cached candlesticks...")

        # Check which ones already have candle data
        existing_candles = set()
        for p in CACHE_DIR.glob("candles_KXNBAGAME-*.pkl"):
            ticker = p.stem.replace("candles_", "")
            existing_candles.add(ticker)

        # Group by event for stats
        events = defaultdict(list)
        for m in markets:
            events[m.get("event_ticker", "")].append(m)

        # Collect ALL tickers that need fetching (skip scalar results)
        tickers_to_fetch = []
        for m in markets:
            ticker = m.get("ticker", "")
            result = m.get("result", "")
            if not ticker or result not in ("yes", "no"):
                continue
            if ticker in existing_candles:
                continue
            close_time = m.get("close_time")
            if isinstance(close_time, str):
                try:
                    settlement = datetime.fromisoformat(
                        close_time.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
            elif isinstance(close_time, datetime):
                settlement = close_time
            else:
                continue
            tickers_to_fetch.append((ticker, settlement))

        print(f"  Total events: {len(events)}")
        print(f"  Already cached: {len(existing_candles)} tickers")
        print(f"  Need to fetch: {len(tickers_to_fetch)} tickers")
        print(f"  Estimated time: {len(tickers_to_fetch) * 0.5 / 60:.1f} minutes")

        if dry_run:
            print("\n  [DRY RUN] Skipping candle fetch")

            dates = defaultdict(int)
            for event_ticker in events:
                m = re.match(r"KXNBAGAME-(\d{2})([A-Z]{3})(\d{2})", event_ticker)
                if m:
                    dates[f"{m.group(2)} {m.group(3)}"] += 1
            print("\n  Games by date (sample):")
            for d in sorted(dates.keys())[:20]:
                print(f"    {d}: {dates[d]} games")
            return

        # Step 3: Fetch missing candlesticks
        print("\n[3/3] Fetching candlesticks...")
        fetched = 0
        errors = 0

        for i, (ticker, settlement) in enumerate(tickers_to_fetch):
            if (i + 1) % 20 == 0:
                print(
                    f"    Progress: {i + 1}/{len(tickers_to_fetch)} "
                    f"({fetched} fetched, {errors} errors)"
                )

            candles = self.fetch_candlesticks(ticker, settlement)
            if candles:
                fetched += 1
            else:
                errors += 1

            time.sleep(0.3)

        # Summary
        total_candles = len(list(CACHE_DIR.glob("candles_KXNBAGAME-*.pkl")))
        print(f"\n{'=' * 60}")
        print("  SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Total events: {len(events)}")
        print(f"  Candle files: {total_candles} tickers")
        print(f"  New fetched: {fetched}")
        print(f"  Errors: {errors}")
        print("\n  Next step:")
        print("    python scripts/build_synthetic_recordings.py")
        print(f"{'=' * 60}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical NBA data from Kalshi"
    )
    parser.add_argument("--force", action="store_true", help="Re-fetch all data")
    parser.add_argument(
        "--dry-run", action="store_true", help="Count only, don't fetch candles"
    )
    args = parser.parse_args()

    fetcher = HistoricalFetcher(force=args.force)
    fetcher.run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
