#!/usr/bin/env python3
"""
Kalshi Sports Oracle Lag Measurement (Phase 0b)

Measures the latency between ESPN score updates and Kalshi price changes
to determine if a sports latency arb edge exists.

This script:
1. Polls ESPN API for live game scores (1-2 second updates)
2. Polls Kalshi REST API for market prices (1 second updates)
3. Detects when ESPN score changes
4. Measures how long it takes for Kalshi prices to react
5. Reports lag distribution and edge potential

Usage:
    # Measure NHL lag
    python3 scripts/measure_kalshi_sports_lag.py --series KXNHLGAME --sport hockey --league nhl

    # Measure NFL lag
    python3 scripts/measure_kalshi_sports_lag.py --series KXNFLGAME --sport football --league nfl

    # Measure soccer lag (Premier League)
    python3 scripts/measure_kalshi_sports_lag.py --series KXSOCCER --sport soccer --league eng.1

    # Run for 30 minutes
    python3 scripts/measure_kalshi_sports_lag.py --series KXNHLGAME --sport hockey --league nhl --duration 1800
"""

import sys
import os
import time
import argparse
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import statistics

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from core.exchange_client.kalshi import KalshiExchangeClient
    import httpx
except ImportError as e:
    print(f"Error: Missing dependency: {e}")
    print("Install httpx: pip install httpx")
    sys.exit(1)


@dataclass
class ScoreEvent:
    """A score change event from ESPN."""
    timestamp: float
    home_score: int
    away_score: int
    period: int
    clock: str
    home_team: str
    away_team: str


@dataclass
class PriceEvent:
    """A price change event from Kalshi."""
    timestamp: float
    ticker: str
    yes_bid: int
    yes_ask: int
    no_bid: int
    no_ask: int


@dataclass
class LagMeasurement:
    """Measured lag between score change and price change."""
    score_change_time: float
    price_change_time: float
    lag_seconds: float
    score_delta: str
    price_delta_cents: int
    ticker: str


class ESPNScoreFeed:
    """Polls ESPN API for live scores."""

    BASE_URL = "https://site.api.espn.com/apis/site/v2/sports"

    def __init__(self, sport: str, league: str):
        self.sport = sport  # "hockey", "football", "soccer", "basketball"
        self.league = league  # "nhl", "nfl", "eng.1", "nba"
        self.url = f"{self.BASE_URL}/{sport}/{league}/scoreboard"
        self.client = httpx.Client(timeout=10.0)
        self._last_scores: Dict[str, ScoreEvent] = {}

    def poll(self) -> List[ScoreEvent]:
        """Poll ESPN and return current scores for all live games."""
        try:
            response = self.client.get(self.url)
            response.raise_for_status()
            data = response.json()

            events = []
            for event in data.get("events", []):
                try:
                    # Extract teams
                    competitions = event.get("competitions", [])
                    if not competitions:
                        continue

                    comp = competitions[0]
                    competitors = comp.get("competitors", [])
                    if len(competitors) != 2:
                        continue

                    home_team = None
                    away_team = None
                    home_score = 0
                    away_score = 0

                    for team in competitors:
                        abbr = team.get("team", {}).get("abbreviation", "")
                        score = int(team.get("score", 0))
                        if team.get("homeAway") == "home":
                            home_team = abbr
                            home_score = score
                        else:
                            away_team = abbr
                            away_score = score

                    if not home_team or not away_team:
                        continue

                    # Extract game state
                    status = comp.get("status", {})
                    period = status.get("period", 0)
                    clock = status.get("displayClock", "0:00")
                    state = status.get("type", {}).get("state", "")

                    # Only track live games
                    if state != "in":
                        continue

                    events.append(ScoreEvent(
                        timestamp=time.time(),
                        home_score=home_score,
                        away_score=away_score,
                        period=period,
                        clock=clock,
                        home_team=home_team,
                        away_team=away_team,
                    ))

                except Exception as e:
                    continue

            return events

        except Exception as e:
            print(f"ESPN poll error: {e}")
            return []

    def get_score_changes(self) -> List[Tuple[ScoreEvent, ScoreEvent]]:
        """Poll ESPN and return (old, new) for any games with score changes."""
        current_scores = self.poll()
        changes = []

        for score in current_scores:
            game_id = f"{score.away_team}@{score.home_team}"
            last = self._last_scores.get(game_id)

            if last and (last.home_score != score.home_score or last.away_score != score.away_score):
                changes.append((last, score))

            self._last_scores[game_id] = score

        return changes

    def close(self):
        self.client.close()


class KalshiPriceFeed:
    """Polls Kalshi REST API for market prices."""

    def __init__(self, client: KalshiExchangeClient, series: str):
        self.client = client
        self.series = series
        self._last_prices: Dict[str, PriceEvent] = {}

    def poll(self) -> List[PriceEvent]:
        """Poll Kalshi and return current prices for all active markets."""
        try:
            response = self.client._request(
                "GET",
                "/markets",
                params={
                    "series_ticker": self.series,
                    "status": "open",
                    "limit": 100,
                }
            )

            events = []
            for market in response.get("markets", []):
                ticker = market.get("ticker", "")
                yes_bid = int(market.get("yes_bid", 0) or 0)
                yes_ask = int(market.get("yes_ask", 100) or 100)
                no_bid = int(market.get("no_bid", 0) or 0)
                no_ask = int(market.get("no_ask", 100) or 100)

                events.append(PriceEvent(
                    timestamp=time.time(),
                    ticker=ticker,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    no_bid=no_bid,
                    no_ask=no_ask,
                ))

            return events

        except Exception as e:
            print(f"Kalshi poll error: {e}")
            return []

    def get_price_changes(self) -> List[Tuple[PriceEvent, PriceEvent]]:
        """Poll Kalshi and return (old, new) for any markets with price changes."""
        current_prices = self.poll()
        changes = []

        for price in current_prices:
            last = self._last_prices.get(price.ticker)

            if last:
                # Check for meaningful price change (>= 2 cents on any side)
                yes_bid_delta = abs(price.yes_bid - last.yes_bid)
                yes_ask_delta = abs(price.yes_ask - last.yes_ask)
                no_bid_delta = abs(price.no_bid - last.no_bid)
                no_ask_delta = abs(price.no_ask - last.no_ask)

                if max(yes_bid_delta, yes_ask_delta, no_bid_delta, no_ask_delta) >= 2:
                    changes.append((last, price))

            self._last_prices[price.ticker] = price

        return changes


def measure_lag(
    espn_feed: ESPNScoreFeed,
    kalshi_feed: KalshiPriceFeed,
    duration_sec: float = 600.0,
    espn_poll_interval: float = 2.0,
    kalshi_poll_interval: float = 1.0,
) -> List[LagMeasurement]:
    """
    Measure oracle lag between ESPN score changes and Kalshi price changes.

    Returns list of lag measurements.
    """
    measurements = []
    start_time = time.time()
    score_change_times: List[Tuple[float, str]] = []  # (timestamp, score_delta_str)

    print(f"\nMeasuring lag for {duration_sec:.0f} seconds...")
    print(f"ESPN poll interval: {espn_poll_interval}s")
    print(f"Kalshi poll interval: {kalshi_poll_interval}s")
    print()

    last_espn_poll = 0.0
    last_kalshi_poll = 0.0
    score_changes_detected = 0
    price_changes_detected = 0

    while time.time() - start_time < duration_sec:
        now = time.time()

        # Poll ESPN for score changes
        if now - last_espn_poll >= espn_poll_interval:
            score_changes = espn_feed.get_score_changes()
            for old, new in score_changes:
                score_changes_detected += 1
                delta_str = f"{new.away_team} {new.away_score}-{new.home_score} {new.home_team} (was {old.away_score}-{old.home_score})"
                score_change_times.append((now, delta_str))
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ESPN: Score change: {delta_str}")

            last_espn_poll = now

        # Poll Kalshi for price changes
        if now - last_kalshi_poll >= kalshi_poll_interval:
            price_changes = kalshi_feed.get_price_changes()
            for old, new in price_changes:
                price_changes_detected += 1
                price_delta = max(
                    abs(new.yes_bid - old.yes_bid),
                    abs(new.yes_ask - old.yes_ask),
                    abs(new.no_bid - old.no_bid),
                    abs(new.no_ask - old.no_ask),
                )

                print(f"[{datetime.now().strftime('%H:%M:%S')}] Kalshi: Price change: {new.ticker} (Δ{price_delta}¢)")

                # Try to match this price change to a recent score change
                # Look for score changes within last 60 seconds
                for score_time, score_delta in score_change_times:
                    if 0 < now - score_time < 60:
                        lag = now - score_time
                        measurements.append(LagMeasurement(
                            score_change_time=score_time,
                            price_change_time=now,
                            lag_seconds=lag,
                            score_delta=score_delta,
                            price_delta_cents=price_delta,
                            ticker=new.ticker,
                        ))
                        print(f"  → Lag: {lag:.1f}s after score change")
                        break

            last_kalshi_poll = now

        # Sleep briefly to avoid busy-wait
        time.sleep(0.1)

    print(f"\nDetected {score_changes_detected} score changes, {price_changes_detected} price changes")
    return measurements


def print_lag_analysis(measurements: List[LagMeasurement]) -> None:
    """Print lag statistics and edge potential analysis."""
    if not measurements:
        print("\n" + "="*80)
        print("NO LAG MEASUREMENTS RECORDED")
        print("="*80)
        print("\nPossible reasons:")
        print("  - No live games during measurement period")
        print("  - No score changes occurred")
        print("  - Kalshi prices didn't move after score changes (already priced in)")
        print("\nTry running during an active game or extending --duration")
        return

    lags = [m.lag_seconds for m in measurements]

    print("\n" + "="*80)
    print("LAG MEASUREMENT RESULTS")
    print("="*80)

    print(f"\nSample Size:       {len(measurements)} matched score→price events")
    print(f"Mean Lag:          {statistics.mean(lags):.2f} seconds")
    print(f"Median Lag:        {statistics.median(lags):.2f} seconds")
    print(f"Std Dev:           {statistics.stdev(lags) if len(lags) > 1 else 0:.2f} seconds")
    print(f"Min Lag:           {min(lags):.2f} seconds")
    print(f"Max Lag:           {max(lags):.2f} seconds")

    # Distribution
    print("\nLAG DISTRIBUTION:")
    buckets = {
        "0-2s": 0,
        "2-5s": 0,
        "5-10s": 0,
        "10-20s": 0,
        "20-30s": 0,
        "30s+": 0,
    }

    for lag in lags:
        if lag < 2:
            buckets["0-2s"] += 1
        elif lag < 5:
            buckets["2-5s"] += 1
        elif lag < 10:
            buckets["5-10s"] += 1
        elif lag < 20:
            buckets["10-20s"] += 1
        elif lag < 30:
            buckets["20-30s"] += 1
        else:
            buckets["30s+"] += 1

    for bucket, count in buckets.items():
        pct = 100 * count / len(lags)
        bar = "█" * int(pct / 2)
        print(f"  {bucket:>8}: {count:>3} ({pct:>5.1f}%) {bar}")

    # Edge potential
    print("\nEDGE POTENTIAL ANALYSIS:")

    mean_lag = statistics.mean(lags)
    median_lag = statistics.median(lags)

    if median_lag < 3:
        edge = "❌ VERY LOW"
        explanation = "Oracle lag is too short. After 1s ESPN delay + 1s Kalshi API poll, only ~1s edge remains."
    elif median_lag < 5:
        edge = "⚠️  LOW"
        explanation = "Marginal edge. Execution speed and low fees are critical."
    elif median_lag < 10:
        edge = "✓ MODERATE"
        explanation = "Good edge potential. 5-10s lag provides time to detect and execute."
    elif median_lag < 20:
        edge = "✓✓ STRONG"
        explanation = "Strong edge. 10-20s lag (like Polymarket) is highly profitable."
    else:
        edge = "✓✓✓ VERY STRONG"
        explanation = "Exceptional edge. 20s+ lag is rare and very profitable."

    print(f"  Edge Assessment:  {edge}")
    print(f"  Explanation:      {explanation}")

    # Recommendations
    print("\nRECOMMENDATIONS:")
    if median_lag >= 5:
        print("  ✓ Proceed with building latency arb for this sport")
        print("  ✓ Use ESPN API (free, 2s updates is sufficient)")
        print(f"  ✓ Target min edge: 3-5¢ (given {median_lag:.1f}s median lag)")
        print("  ✓ Execution window: ~{:.0f}s after score change".format(median_lag - 2))
    else:
        print("  ⚠️  Consider other sports with longer oracle lag")
        print("  ⚠️  Or use faster data source (paid SportsRadar API with sub-1s updates)")
        print("  ⚠️  Edge may be too small after fees (0.7% taker + slippage)")

    # Sample measurements
    print("\nSAMPLE MEASUREMENTS:")
    print(f"{'Lag (s)':<10} {'Score Change':<40} {'Ticker':<35}")
    print("-" * 80)
    for m in sorted(measurements, key=lambda x: x.lag_seconds)[:10]:
        print(f"{m.lag_seconds:>8.1f}s  {m.score_delta:<40} {m.ticker:<35}")

    print("="*80)


def main():
    parser = argparse.ArgumentParser(description="Measure Kalshi sports oracle lag")
    parser.add_argument("--series", required=True, help="Kalshi series (KXNHLGAME, KXNFLGAME, etc.)")
    parser.add_argument("--sport", required=True, help="ESPN sport (hockey, football, soccer)")
    parser.add_argument("--league", required=True, help="ESPN league (nhl, nfl, eng.1)")
    parser.add_argument("--duration", type=float, default=600, help="Measurement duration in seconds (default: 600)")
    parser.add_argument("--espn-interval", type=float, default=2.0, help="ESPN poll interval (default: 2.0s)")
    parser.add_argument("--kalshi-interval", type=float, default=1.0, help="Kalshi poll interval (default: 1.0s)")

    args = parser.parse_args()

    print("\n" + "="*80)
    print("KALSHI SPORTS ORACLE LAG MEASUREMENT (Phase 0b)")
    print("="*80)
    print(f"\nSeries:   {args.series}")
    print(f"Sport:    {args.sport} / {args.league}")
    print(f"Duration: {args.duration:.0f}s")
    print()

    # Initialize feeds
    try:
        kalshi_client = KalshiExchangeClient.from_env()
        print("✓ Connected to Kalshi API")
    except Exception as e:
        print(f"✗ Failed to connect to Kalshi: {e}")
        return 1

    espn_feed = ESPNScoreFeed(sport=args.sport, league=args.league)
    kalshi_feed = KalshiPriceFeed(client=kalshi_client, series=args.series)

    try:
        # Run measurement
        measurements = measure_lag(
            espn_feed=espn_feed,
            kalshi_feed=kalshi_feed,
            duration_sec=args.duration,
            espn_poll_interval=args.espn_interval,
            kalshi_poll_interval=args.kalshi_interval,
        )

        # Analyze results
        print_lag_analysis(measurements)

    except KeyboardInterrupt:
        print("\n\nMeasurement interrupted by user")
    finally:
        espn_feed.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
