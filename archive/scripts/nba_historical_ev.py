#!/usr/bin/env python3
"""
NBA Historical EV Analyzer

Aggregates NBA markets from Kalshi by season and analyzes expected value (EV)
as games progress. Allows mass-averaging all games as a timeseries and plotting.

Key concepts:
- Normalizes time to "minutes until settlement" so games can be aligned
- EV = Settlement Value - Price Paid (100 for winners, 0 for losers)
- Aggregates by price ranges to see calibration (were 70% favorites actually 70%?)

Usage:
    python scripts/nba_historical_ev.py                    # Analyze current season
    python scripts/nba_historical_ev.py --season 2025      # Specific season
    python scripts/nba_historical_ev.py --limit 50         # Limit games for testing
    python scripts/nba_historical_ev.py --export data.csv  # Export raw data
    python scripts/nba_historical_ev.py --no-cache         # Skip cache, refetch all
"""

import sys
import os
import argparse
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class GameData:
    """Container for a single NBA game's historical data."""

    event_ticker: str
    game_date: datetime
    title: str
    home_team: str
    away_team: str
    winner: str  # 'home' or 'away'
    home_ticker: str
    away_ticker: str
    # Time series data: indexed by minutes_until_settlement
    # Each entry: {minutes: {'home_mid': float, 'away_mid': float, 'volume': int}}
    price_history: Dict[int, Dict[str, float]] = field(default_factory=dict)
    settlement_time: Optional[datetime] = None


@dataclass
class SeasonStats:
    """Aggregated statistics for a season."""

    season: str
    total_games: int
    avg_favorite_price: float
    favorite_win_rate: float
    calibration_by_bucket: Dict[str, Dict[str, float]] = field(default_factory=dict)
    avg_ev_by_time: Dict[int, float] = field(default_factory=dict)


def get_nba_season(game_date: datetime) -> str:
    """
    Determine NBA season from game date.
    NBA season spans Oct-Jun, so:
    - Games Oct-Dec are part of season starting that year (e.g., Oct 2024 = 2024-25 season)
    - Games Jan-Jun are part of season that started previous year (e.g., Jan 2025 = 2024-25 season)
    """
    if game_date.month >= 10:  # Oct, Nov, Dec
        return f"{game_date.year}-{str(game_date.year + 1)[-2:]}"
    else:  # Jan-Jun
        return f"{game_date.year - 1}-{str(game_date.year)[-2:]}"


def parse_ticker_teams(
    ticker: str, title: str, yes_subtitle: str
) -> Tuple[str, str, str]:
    """
    Parse team info from market data.
    Returns: (side, team_name, opponent)
    """
    # Title format: "Team1 at Team2 Winner?"
    # yes_subtitle is the team this market is for
    team_name = yes_subtitle.strip()

    # Determine if home or away from title
    if " at " in title:
        parts = title.replace(" Winner?", "").split(" at ")
        away_team = parts[0].strip()
        home_team = parts[1].strip()

        if team_name == home_team:
            return "home", team_name, away_team
        else:
            return "away", team_name, home_team

    return "unknown", team_name, ""


class NBAHistoricalAnalyzer:
    """Main analyzer for NBA historical EV data."""

    CACHE_DIR = Path("data/nba_cache")
    SERIES_TICKER = "KXNBAGAME"

    def __init__(self, use_cache: bool = True):
        self.use_cache = use_cache
        self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._client = None
        self._kw = None

    @property
    def client(self):
        """Lazy-load Kalshi client."""
        if self._client is None:
            from kalshi_utils.client_wrapper import KalshiWrapped

            self._kw = KalshiWrapped()
            self._client = self._kw.GetClient()
        return self._client

    def fetch_all_settled_markets(self, limit: Optional[int] = None) -> List[Any]:
        """Fetch all settled NBA markets with pagination."""
        cache_file = self.CACHE_DIR / "settled_markets.pkl"

        # Try cache first
        if self.use_cache and cache_file.exists():
            cache_age = datetime.now() - datetime.fromtimestamp(
                cache_file.stat().st_mtime
            )
            if cache_age < timedelta(hours=24):
                print(
                    f"Loading markets from cache ({cache_age.seconds // 3600}h old)..."
                )
                with open(cache_file, "rb") as f:
                    markets = pickle.load(f)
                if limit:
                    return markets[: limit * 2]  # *2 because each game has 2 markets
                return markets

        print("Fetching settled NBA markets from Kalshi...")
        all_markets = []
        cursor = None

        while True:
            resp = self.client.get_markets(
                limit=1000,
                series_ticker=self.SERIES_TICKER,
                status="settled",
                cursor=cursor,
            )
            all_markets.extend(resp.markets)
            print(f"  Fetched {len(all_markets)} markets...")

            if not resp.cursor:
                break
            cursor = resp.cursor

        # Cache results
        with open(cache_file, "wb") as f:
            pickle.dump(all_markets, f)

        if limit:
            return all_markets[: limit * 2]
        return all_markets

    def group_markets_by_game(self, markets: List[Any]) -> Dict[str, Dict[str, Any]]:
        """Group markets by event (game), creating pairs."""
        games = defaultdict(dict)

        for m in markets:
            d = m.model_dump()
            event_ticker = d["event_ticker"]
            side, team_name, opponent = parse_ticker_teams(
                d["ticker"], d["title"], d["yes_sub_title"]
            )

            games[event_ticker][side] = {
                "market": m,
                "ticker": d["ticker"],
                "team": team_name,
                "result": d["result"],
                "settlement_value": d.get("settlement_value", 0),
                "close_time": d["close_time"],
                "title": d["title"],
                "volume": d["volume"],
            }

        # Filter to complete games (both sides present)
        complete_games = {k: v for k, v in games.items() if "home" in v and "away" in v}

        return complete_games

    def fetch_game_candlesticks(
        self, ticker: str, settlement_time: datetime, lookback_hours: int = 6
    ) -> List[Dict[str, Any]]:
        """Fetch candlestick data for a market."""
        cache_file = self.CACHE_DIR / f"candles_{ticker}.pkl"

        if self.use_cache and cache_file.exists():
            with open(cache_file, "rb") as f:
                return pickle.load(f)

        try:
            end_ts = int(settlement_time.timestamp())
            start_ts = int(
                (settlement_time - timedelta(hours=lookback_hours)).timestamp()
            )

            resp = self.client.get_market_candlesticks(
                ticker=ticker,
                series_ticker=self.SERIES_TICKER,
                start_ts=start_ts,
                end_ts=end_ts,
                period_interval=1,  # 1 minute
            )

            if not resp.candlesticks:
                return []

            candles = []
            for c in resp.candlesticks:
                candles.append(
                    {
                        "ts": c.end_period_ts,
                        "yes_bid_close": c.yes_bid.close if c.yes_bid else None,
                        "yes_ask_close": c.yes_ask.close if c.yes_ask else None,
                        "volume": c.volume,
                        "open_interest": c.open_interest,
                    }
                )

            # Cache
            with open(cache_file, "wb") as f:
                pickle.dump(candles, f)

            return candles

        except Exception as e:
            print(f"    Error fetching candles for {ticker}: {e}")
            return []

    def process_game(
        self, event_ticker: str, game_info: Dict[str, Any], fetch_candles: bool = True
    ) -> Optional[GameData]:
        """Process a single game into GameData structure."""
        home_info = game_info["home"]
        away_info = game_info["away"]

        # Determine winner
        if home_info["result"] == "yes":
            winner = "home"
        elif away_info["result"] == "yes":
            winner = "away"
        else:
            return None  # Skip if no clear winner

        settlement_time = home_info["close_time"]
        if isinstance(settlement_time, str):
            settlement_time = datetime.fromisoformat(
                settlement_time.replace("Z", "+00:00")
            )

        game_data = GameData(
            event_ticker=event_ticker,
            game_date=settlement_time,
            title=home_info["title"],
            home_team=home_info["team"],
            away_team=away_info["team"],
            winner=winner,
            home_ticker=home_info["ticker"],
            away_ticker=away_info["ticker"],
            settlement_time=settlement_time,
        )

        if not fetch_candles:
            return game_data

        # Fetch candlesticks for both sides
        home_candles = self.fetch_game_candlesticks(
            home_info["ticker"], settlement_time
        )
        away_candles = self.fetch_game_candlesticks(
            away_info["ticker"], settlement_time
        )

        # Build price history indexed by minutes until settlement
        settlement_ts = int(settlement_time.timestamp())

        # Process home candles
        home_by_min = {}
        for c in home_candles:
            minutes_until = (settlement_ts - c["ts"]) // 60
            if minutes_until >= 0:
                bid = c["yes_bid_close"]
                ask = c["yes_ask_close"]
                if bid is not None and ask is not None:
                    home_by_min[minutes_until] = {
                        "mid": (bid + ask) / 2 / 100,  # Convert cents to dollars
                        "bid": bid / 100,
                        "ask": ask / 100,
                        "volume": c["volume"],
                    }

        # Process away candles
        away_by_min = {}
        for c in away_candles:
            minutes_until = (settlement_ts - c["ts"]) // 60
            if minutes_until >= 0:
                bid = c["yes_bid_close"]
                ask = c["yes_ask_close"]
                if bid is not None and ask is not None:
                    away_by_min[minutes_until] = {
                        "mid": (bid + ask) / 2 / 100,
                        "bid": bid / 100,
                        "ask": ask / 100,
                        "volume": c["volume"],
                    }

        # Combine into price history
        all_minutes = set(home_by_min.keys()) | set(away_by_min.keys())
        for m in all_minutes:
            game_data.price_history[m] = {
                "home_mid": home_by_min.get(m, {}).get("mid"),
                "away_mid": away_by_min.get(m, {}).get("mid"),
                "home_bid": home_by_min.get(m, {}).get("bid"),
                "home_ask": home_by_min.get(m, {}).get("ask"),
                "away_bid": away_by_min.get(m, {}).get("bid"),
                "away_ask": away_by_min.get(m, {}).get("ask"),
                "volume": (home_by_min.get(m, {}).get("volume", 0) or 0)
                + (away_by_min.get(m, {}).get("volume", 0) or 0),
            }

        return game_data

    def analyze_games(
        self, games: List[GameData], time_buckets: List[int] = None
    ) -> pd.DataFrame:
        """
        Analyze games and create a DataFrame with EV calculations.

        For each game at each time point, compute:
        - favorite_price: higher priced team's mid
        - underdog_price: lower priced team's mid
        - favorite_won: 1 if favorite won, 0 otherwise
        - ev_favorite: 1 - favorite_price if favorite won, -favorite_price otherwise
        """
        if time_buckets is None:
            # Default: every 10 minutes up to 360 (6 hours)
            time_buckets = list(range(0, 361, 10))

        rows = []

        for game in games:
            if not game.price_history:
                continue

            season = get_nba_season(game.game_date)

            for minutes in time_buckets:
                # Find closest available minute
                available = sorted(game.price_history.keys())
                closest = min(available, key=lambda x: abs(x - minutes), default=None)

                if closest is None or abs(closest - minutes) > 5:
                    continue

                data = game.price_history[closest]
                home_mid = data.get("home_mid")
                away_mid = data.get("away_mid")

                if home_mid is None or away_mid is None:
                    continue

                # Determine favorite
                if home_mid >= away_mid:
                    favorite = "home"
                    favorite_price = home_mid
                    underdog_price = away_mid
                else:
                    favorite = "away"
                    favorite_price = away_mid
                    underdog_price = home_mid

                favorite_won = 1 if favorite == game.winner else 0

                # EV calculation: if you bet $1 on favorite
                # Win: you get $1 (payout) - favorite_price (cost) = 1 - favorite_price
                # Lose: you lose favorite_price
                # Expected: P(win) * (1 - price) - P(lose) * price
                # At fair odds: P(win) should equal price
                # Realized EV for this bet: 1 - price if won, -price if lost
                ev_favorite = (1 - favorite_price) if favorite_won else -favorite_price

                rows.append(
                    {
                        "event_ticker": game.event_ticker,
                        "game_date": game.game_date,
                        "season": season,
                        "title": game.title,
                        "home_team": game.home_team,
                        "away_team": game.away_team,
                        "winner": game.winner,
                        "minutes_until_settlement": minutes,
                        "home_mid": home_mid,
                        "away_mid": away_mid,
                        "favorite": favorite,
                        "favorite_price": favorite_price,
                        "underdog_price": underdog_price,
                        "favorite_won": favorite_won,
                        "ev_favorite": ev_favorite,
                        "volume": data.get("volume", 0),
                    }
                )

        return pd.DataFrame(rows)

    def aggregate_by_time(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate EV statistics by minutes until settlement.
        This creates the "average game progression" timeseries.
        """
        agg = (
            df.groupby("minutes_until_settlement")
            .agg(
                {
                    "favorite_price": "mean",
                    "favorite_won": "mean",  # = actual win rate
                    "ev_favorite": "mean",
                    "volume": "sum",
                    "event_ticker": "count",  # = number of games at this time
                }
            )
            .rename(columns={"event_ticker": "game_count"})
        )

        # Add calibration: difference between price and actual win rate
        agg["calibration_error"] = agg["favorite_won"] - agg["favorite_price"]

        return agg.sort_index(ascending=False)  # Sort by time (most distant first)

    def aggregate_by_price_bucket(
        self, df: pd.DataFrame, buckets: List[Tuple[float, float]] = None
    ) -> pd.DataFrame:
        """
        Aggregate by price buckets to analyze calibration.
        E.g., Do 70-75% favorites actually win 70-75% of the time?
        """
        if buckets is None:
            buckets = [
                (0.50, 0.55),
                (0.55, 0.60),
                (0.60, 0.65),
                (0.65, 0.70),
                (0.70, 0.75),
                (0.75, 0.80),
                (0.80, 0.85),
                (0.85, 0.90),
                (0.90, 0.95),
                (0.95, 1.00),
            ]

        rows = []
        for low, high in buckets:
            mask = (df["favorite_price"] >= low) & (df["favorite_price"] < high)
            subset = df[mask]

            if len(subset) == 0:
                continue

            rows.append(
                {
                    "price_range": f"{low:.0%}-{high:.0%}",
                    "low": low,
                    "high": high,
                    "count": len(subset),
                    "unique_games": subset["event_ticker"].nunique(),
                    "avg_price": subset["favorite_price"].mean(),
                    "actual_win_rate": subset["favorite_won"].mean(),
                    "calibration_error": subset["favorite_won"].mean()
                    - subset["favorite_price"].mean(),
                    "avg_ev": subset["ev_favorite"].mean(),
                }
            )

        return pd.DataFrame(rows)

    def run_analysis(
        self,
        season: Optional[str] = None,
        limit: Optional[int] = None,
        export_path: Optional[str] = None,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Run full analysis pipeline.

        Returns:
            - raw_df: Raw data with all game/time observations
            - time_agg: Aggregated by minutes until settlement
            - price_agg: Aggregated by price buckets
        """
        print("\n" + "=" * 70)
        print("  NBA Historical EV Analysis")
        print("=" * 70 + "\n")

        # Fetch markets
        markets = self.fetch_all_settled_markets(limit=limit)
        print(f"Total markets: {len(markets)}")

        # Group by game
        games_dict = self.group_markets_by_game(markets)
        print(f"Complete games: {len(games_dict)}")

        # Filter by season if specified
        if season:
            print(f"Filtering to season: {season}")

        # Process each game
        print("\nProcessing games (fetching candlesticks)...")
        games = []
        for i, (event_ticker, game_info) in enumerate(games_dict.items()):
            if (i + 1) % 20 == 0:
                print(f"  Processed {i + 1}/{len(games_dict)} games...")

            game_data = self.process_game(event_ticker, game_info)
            if game_data:
                # Filter by season
                if season and get_nba_season(game_data.game_date) != season:
                    continue
                games.append(game_data)

        print(f"Games with data: {len(games)}")

        if not games:
            print("No games found!")
            return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        # Analyze
        print("\nAnalyzing...")
        raw_df = self.analyze_games(games)
        time_agg = self.aggregate_by_time(raw_df)
        price_agg = self.aggregate_by_price_bucket(raw_df)

        # Export if requested
        if export_path:
            raw_df.to_csv(export_path, index=False)
            print(f"\nExported raw data to: {export_path}")

        return raw_df, time_agg, price_agg


def plot_timeseries(time_agg: pd.DataFrame, season: str = "All Seasons"):
    """Plot the aggregated timeseries."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(f"NBA Market EV Analysis - {season}", fontsize=14, fontweight="bold")

    # 1. Average Favorite Price over time
    ax1 = axes[0, 0]
    ax1.plot(
        time_agg.index,
        time_agg["favorite_price"],
        "b-",
        linewidth=2,
        label="Avg Favorite Price",
    )
    ax1.plot(
        time_agg.index,
        time_agg["favorite_won"],
        "g--",
        linewidth=2,
        label="Actual Win Rate",
    )
    ax1.set_xlabel("Minutes Until Settlement")
    ax1.set_ylabel("Probability")
    ax1.set_title("Favorite Price vs Actual Win Rate")
    ax1.legend()
    ax1.invert_xaxis()
    ax1.grid(True, alpha=0.3)
    ax1.set_ylim(0.5, 1.0)

    # 2. Calibration Error over time
    ax2 = axes[0, 1]
    colors = ["g" if x >= 0 else "r" for x in time_agg["calibration_error"]]
    ax2.bar(
        time_agg.index, time_agg["calibration_error"] * 100, color=colors, alpha=0.7
    )
    ax2.axhline(y=0, color="black", linestyle="-", linewidth=1)
    ax2.set_xlabel("Minutes Until Settlement")
    ax2.set_ylabel("Calibration Error (%)")
    ax2.set_title("Calibration: Actual Win Rate - Price")
    ax2.invert_xaxis()
    ax2.grid(True, alpha=0.3)

    # 3. Average EV over time
    ax3 = axes[1, 0]
    colors = ["g" if x >= 0 else "r" for x in time_agg["ev_favorite"]]
    ax3.bar(time_agg.index, time_agg["ev_favorite"] * 100, color=colors, alpha=0.7)
    ax3.axhline(y=0, color="black", linestyle="-", linewidth=1)
    ax3.set_xlabel("Minutes Until Settlement")
    ax3.set_ylabel("Average EV (cents per $1 bet)")
    ax3.set_title("Expected Value of Betting Favorite")
    ax3.invert_xaxis()
    ax3.grid(True, alpha=0.3)

    # 4. Game count / Volume over time
    ax4 = axes[1, 1]
    ax4.bar(time_agg.index, time_agg["game_count"], color="steelblue", alpha=0.7)
    ax4.set_xlabel("Minutes Until Settlement")
    ax4.set_ylabel("Number of Observations")
    ax4.set_title("Data Coverage")
    ax4.invert_xaxis()
    ax4.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig("data/nba_ev_timeseries.png", dpi=150, bbox_inches="tight")
    plt.show()
    print("\nPlot saved to: data/nba_ev_timeseries.png")


def plot_calibration(price_agg: pd.DataFrame, season: str = "All Seasons"):
    """Plot calibration chart."""
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(f"NBA Market Calibration - {season}", fontsize=14, fontweight="bold")

    # 1. Price vs Actual Win Rate
    ax1 = axes[0]
    ax1.plot([0.5, 1.0], [0.5, 1.0], "k--", alpha=0.5, label="Perfect Calibration")
    ax1.scatter(
        price_agg["avg_price"],
        price_agg["actual_win_rate"],
        s=price_agg["count"] / 10,
        alpha=0.7,
        label="Observed",
    )
    ax1.set_xlabel("Average Market Price (Implied Probability)")
    ax1.set_ylabel("Actual Win Rate")
    ax1.set_title("Calibration Plot")
    ax1.legend()
    ax1.set_xlim(0.5, 1.0)
    ax1.set_ylim(0.5, 1.0)
    ax1.grid(True, alpha=0.3)

    # Add price range labels
    for _, row in price_agg.iterrows():
        ax1.annotate(
            row["price_range"],
            (row["avg_price"], row["actual_win_rate"]),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=8,
        )

    # 2. EV by Price Bucket
    ax2 = axes[1]
    colors = ["g" if x >= 0 else "r" for x in price_agg["avg_ev"]]
    bars = ax2.bar(
        price_agg["price_range"], price_agg["avg_ev"] * 100, color=colors, alpha=0.7
    )
    ax2.axhline(y=0, color="black", linestyle="-", linewidth=1)
    ax2.set_xlabel("Favorite Price Range")
    ax2.set_ylabel("Average EV (cents per $1)")
    ax2.set_title("EV by Price Bucket")
    ax2.tick_params(axis="x", rotation=45)
    ax2.grid(True, alpha=0.3, axis="y")

    # Add count labels
    for bar, count in zip(bars, price_agg["unique_games"]):
        ax2.annotate(
            f"n={count}",
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center",
            va="bottom",
            fontsize=8,
        )

    plt.tight_layout()
    plt.savefig("data/nba_calibration.png", dpi=150, bbox_inches="tight")
    plt.show()
    print("\nPlot saved to: data/nba_calibration.png")


def print_summary(
    raw_df: pd.DataFrame, time_agg: pd.DataFrame, price_agg: pd.DataFrame
):
    """Print summary statistics."""
    print("\n" + "=" * 70)
    print("  SUMMARY STATISTICS")
    print("=" * 70)

    print(f"\nTotal observations: {len(raw_df):,}")
    print(f"Unique games: {raw_df['event_ticker'].nunique()}")
    print(f"Seasons covered: {sorted(raw_df['season'].unique())}")
    print(
        f"Date range: {raw_df['game_date'].min().date()} to {raw_df['game_date'].max().date()}"
    )

    print("\n--- Overall Statistics ---")
    print(f"Average favorite price: {raw_df['favorite_price'].mean():.1%}")
    print(f"Favorite win rate: {raw_df['favorite_won'].mean():.1%}")
    print(
        f"Average EV (betting favorite): {raw_df['ev_favorite'].mean() * 100:.2f} cents/$1"
    )

    print("\n--- Calibration by Price Bucket ---")
    print(
        price_agg[
            [
                "price_range",
                "unique_games",
                "avg_price",
                "actual_win_rate",
                "calibration_error",
                "avg_ev",
            ]
        ].to_string(index=False)
    )

    print("\n--- Key Time Points ---")
    key_times = [0, 30, 60, 120, 180, 300]
    for t in key_times:
        if t in time_agg.index:
            row = time_agg.loc[t]
            print(
                f"  {t:3d} min: Price={row['favorite_price']:.1%}, WinRate={row['favorite_won']:.1%}, EV={row['ev_favorite'] * 100:+.2f}¢"
            )


def main():
    parser = argparse.ArgumentParser(description="Analyze NBA Historical EV on Kalshi")
    parser.add_argument(
        "--season", type=str, help="Filter to specific season (e.g., '2024-25')"
    )
    parser.add_argument("--limit", type=int, help="Limit number of games to process")
    parser.add_argument(
        "--export", type=str, metavar="FILE", help="Export raw data to CSV"
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Skip cache, refetch all data"
    )
    parser.add_argument("--no-plot", action="store_true", help="Skip plotting")
    parser.add_argument(
        "--list-seasons", action="store_true", help="List available seasons and exit"
    )

    args = parser.parse_args()

    analyzer = NBAHistoricalAnalyzer(use_cache=not args.no_cache)

    # List seasons mode
    if args.list_seasons:
        markets = analyzer.fetch_all_settled_markets()
        games = analyzer.group_markets_by_game(markets)
        seasons = set()
        for event_ticker, game_info in games.items():
            if "home" in game_info:
                date = game_info["home"]["close_time"]
                if isinstance(date, str):
                    date = datetime.fromisoformat(date.replace("Z", "+00:00"))
                seasons.add(get_nba_season(date))
        print("\nAvailable seasons:")
        for s in sorted(seasons):
            print(f"  {s}")
        return

    # Run analysis
    raw_df, time_agg, price_agg = analyzer.run_analysis(
        season=args.season,
        limit=args.limit,
        export_path=args.export,
    )

    if raw_df.empty:
        return

    # Print summary
    print_summary(raw_df, time_agg, price_agg)

    # Plot
    if not args.no_plot:
        try:
            season_label = args.season if args.season else "All Seasons"
            plot_timeseries(time_agg, season_label)
            plot_calibration(price_agg, season_label)
        except Exception as e:
            print(f"\nPlotting error (may need display): {e}")
            print("Use --no-plot to skip, or run in a notebook for plotting.")


if __name__ == "__main__":
    main()
