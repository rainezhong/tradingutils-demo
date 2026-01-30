"""Kalshi public API wrapper for market data."""

import time
from typing import Optional

import requests


class KalshiPublicClient:
    """Client for Kalshi public market data API."""

    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict] = None,
        max_retries: int = 3,
    ) -> dict:
        """Make HTTP request with exponential backoff retry."""
        url = f"{self.BASE_URL}{endpoint}"
        delays = [1, 2, 4]

        for attempt in range(max_retries):
            try:
                response = self.session.request(method, url, params=params, timeout=30)

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", delays[attempt]))
                    time.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    time.sleep(delays[attempt])
                    continue
                raise

            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    time.sleep(delays[attempt])
                    continue
                raise

            except requests.exceptions.HTTPError:
                if attempt < max_retries - 1 and response.status_code >= 500:
                    time.sleep(delays[attempt])
                    continue
                raise

        return {}

    def get_markets(
        self,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> dict:
        """Fetch a single page of markets."""
        params = {"limit": limit}

        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor

        return self._request_with_retry("GET", "/markets", params=params)

    def get_all_markets(self, min_volume: int = 1000) -> list[dict]:
        """Fetch all markets with pagination, filtered by minimum volume."""
        all_markets = []
        cursor = None

        while True:
            response = self.get_markets(status="open", limit=100, cursor=cursor)
            markets = response.get("markets", [])

            if not markets:
                break

            for market in markets:
                volume = market.get("volume_24h", 0) or 0
                if volume >= min_volume:
                    all_markets.append(market)

            cursor = response.get("cursor")
            if not cursor:
                break

        return all_markets

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market by ticker."""
        return self._request_with_retry("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str) -> dict:
        """Fetch orderbook for a market."""
        return self._request_with_retry("GET", f"/markets/{ticker}/orderbook")
