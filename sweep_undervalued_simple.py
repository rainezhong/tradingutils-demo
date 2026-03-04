#!/usr/bin/env python3
"""Scan Kalshi markets for undervalued high-priced contracts."""

import asyncio
from typing import Optional, Tuple
import requests
from scipy.stats import norm

# Import Kalshi client
from core import KalshiExchangeClient


def calculate_win_probability(score_diff: int, time_remaining_seconds: int) -> float:
    """Calculate win probability using normal distribution model.
    
    Based on the assumption that remaining score differential follows a normal
    distribution with std dev proportional to sqrt(remaining possessions).
    """
    if time_remaining_seconds <= 0:
        return 1.0 if score_diff > 0 else (0.5 if score_diff == 0 else 0.0)
    
    # Estimate remaining possessions (avg ~100 possessions per 48 min game)
    possessions_per_second = 100.0 / (48.0 * 60.0)
    remaining_possessions = time_remaining_seconds * possessions_per_second
    
    # Standard deviation of score differential
    # Empirically, std per possession is ~2.4 points in NBA
    std_per_possession = 2.4
    std_dev = std_per_possession * (remaining_possessions ** 0.5)
    
    if std_dev < 0.1:  # Avoid division by zero
        return 1.0 if score_diff > 0 else (0.5 if score_diff == 0 else 0.0)
    
    # Win probability = P(final_diff > 0) = P(Z > -score_diff/std_dev)
    z_score = score_diff / std_dev
    win_prob = norm.cdf(z_score)
    
    return max(0.01, min(0.99, win_prob))


def get_espn_game_data():
    """Fetch live game data from ESPN."""
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching ESPN data: {e}")
        return None


def parse_ticker(ticker: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse Kalshi NBA ticker.
    
    Returns (date, away_team, home_team, contract_team) or None.
    """
    if not ticker.startswith("KXNBAGAME-"):
        return None
    
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    
    game_part = parts[1]
    if len(game_part) < 7:
        return None
    
    date_str = game_part[:7]
    teams_str = game_part[7:]
    
    if len(teams_str) == 6:
        away, home = teams_str[:3], teams_str[3:]
    elif len(teams_str) == 8:
        away, home = teams_str[:4], teams_str[4:]
    else:
        return None
    
    team = parts[2] if len(parts) > 2 else None
    return (date_str, away, home, team)


async def find_undervalued_favorites():
    """Scan Kalshi for undervalued high-priced NBA contracts."""
    
    print("=" * 100)
    print("KALSHI NBA SWEEP: UNDERVALUED HIGH-PRICED CONTRACTS")
    print("=" * 100)
    print()
    
    # Get Kalshi client
    client = KalshiExchangeClient.from_env(demo=False)
    await client.connect()
    
    # Get NBA markets
    print("Fetching NBA markets from Kalshi...")
    try:
        markets = await client.get_markets(series_ticker="KXNBAGAME", status="open")
    except Exception as e:
        print(f"Error: {e}")
        await client.disconnect()
        return
    
    print(f"Found {len(markets)} open NBA markets\n")
    
    # Get ESPN data
    print("Fetching live scores from ESPN...")
    espn_data = get_espn_game_data()
    
    if not espn_data:
        await client.disconnect()
        return
    
    # Build ESPN game lookup
    espn_games = {}
    for event in espn_data.get("events", []):
        comps = event.get("competitions", [])
        if not comps:
            continue
        
        competitors = comps[0].get("competitors", [])
        if len(competitors) < 2:
            continue
        
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        
        if not home or not away:
            continue
        
        home_team = home["team"]["abbreviation"]
        away_team = away["team"]["abbreviation"]
        
        status = comps[0].get("status", {})
        
        game_data = {
            "away_team": away_team,
            "home_team": home_team,
            "away_score": int(away["score"]),
            "home_score": int(home["score"]),
            "period": status.get("period", 0),
            "clock": status.get("displayClock", "0:00"),
            "state": status.get("type", {}).get("state", ""),
        }
        
        espn_games[f"{away_team}_{home_team}"] = game_data
        espn_games[f"{home_team}_{away_team}"] = game_data
    
    print(f"Found {len(espn_data.get('events', []))} games on ESPN")
    print(f"Games in progress: {sum(1 for g in espn_games.values() if g.get('state') == 'in')}\n")
    
    # Analyze markets
    undervalued = []
    
    for market in markets:
        ticker = market.ticker
        parsed = parse_ticker(ticker)
        if not parsed:
            continue
        
        date_str, away, home, team = parsed
        
        # Find ESPN game
        espn_game = espn_games.get(f"{away}_{home}") or espn_games.get(f"{home}_{away}")
        if not espn_game or espn_game["state"] != "in":
            continue
        
        # Market data
        yes_bid = market.yes_bid / 100.0
        yes_ask = market.yes_ask / 100.0
        market_mid = (yes_bid + yes_ask) / 2.0
        
        # Only high-priced contracts (>75%)
        if market_mid < 0.75:
            continue
        
        # Game state
        away_score = espn_game["away_score"]
        home_score = espn_game["home_score"]
        period = espn_game["period"]
        clock = espn_game["clock"]
        
        actual_away = espn_game["away_team"]
        actual_home = espn_game["home_team"]
        
        is_home_team = (team == home or team == actual_home)
        
        # Parse time
        try:
            if ":" in clock:
                mins, secs = clock.split(":")
                time_remaining_secs = int(float(mins)) * 60 + float(secs)
            else:
                time_remaining_secs = float(clock)
            
            if period == 1:
                time_remaining_secs += 36 * 60
            elif period == 2:
                time_remaining_secs += 24 * 60
            elif period == 3:
                time_remaining_secs += 12 * 60
        except:
            continue
        
        # Calculate edge
        score_diff = (home_score - away_score) if is_home_team else (away_score - home_score)
        fair_value = calculate_win_probability(score_diff, time_remaining_secs)
        edge = fair_value - market_mid
        
        if edge > 0.01:  # At least 1% edge
            undervalued.append({
                "ticker": ticker,
                "team": team,
                "away": actual_away,
                "home": actual_home,
                "away_score": away_score,
                "home_score": home_score,
                "period": period,
                "clock": clock,
                "market_bid": yes_bid,
                "market_ask": yes_ask,
                "market_mid": market_mid,
                "fair_value": fair_value,
                "edge": edge,
                "score_diff": score_diff,
                "time_remaining": time_remaining_secs,
                "is_home": is_home_team,
            })
    
    await client.disconnect()
    
    # Display results
    undervalued.sort(key=lambda x: x["edge"], reverse=True)
    
    print("=" * 100)
    print(f"RESULTS: {len(undervalued)} UNDERVALUED CONTRACTS (>75%, >1% edge)")
    print("=" * 100)
    print()
    
    if not undervalued:
        print("No undervalued contracts found.")
        print("\nPossible reasons:")
        print("  • Markets are efficiently priced")
        print("  • No games with heavy favorites currently in progress")
        print("  • All edges < 1%")
    else:
        print(f"{'Ticker':<45} {'Score':<15} {'Q':<3} {'Time':<8} {'Mkt%':<7} {'Fair%':<7} {'Edge':<7}")
        print("-" * 110)
        
        for c in undervalued:
            score = f"{c['away']} {c['away_score']}-{c['home_score']} {c['home']}"
            side = "(H)" if c['is_home'] else "(A)"
            
            print(f"{c['ticker']:<45} {score:<15} Q{c['period']:<2} {c['clock']:<8} "
                  f"{c['market_mid']*100:<6.1f}% {c['fair_value']*100:<6.1f}% {c['edge']*100:+5.1f}%")
        
        print()
        
        # Top 3 detailed
        print("=" * 100)
        print("TOP 3 OPPORTUNITIES")
        print("=" * 100)
        
        for i, c in enumerate(undervalued[:3], 1):
            side = "Home" if c['is_home'] else "Away"
            print(f"\n#{i}. {c['ticker']}")
            print(f"   {c['team']} ({side}) in {c['away']} {c['away_score']} - {c['home']} {c['home_score']}")
            print(f"   Period Q{c['period']}, Time {c['clock']} ({c['time_remaining']//60:.0f}:{c['time_remaining']%60:02.0f} remaining)")
            print(f"   Lead: {c['score_diff']:+d} points")
            print(f"   Market: {c['market_mid']*100:.1f}% (bid {c['market_bid']*100:.1f}%, ask {c['market_ask']*100:.1f}%)")
            print(f"   Fair value: {c['fair_value']*100:.1f}%")
            print(f"   EDGE: {c['edge']*100:+.2f}% ({c['edge']*10000:+.0f} bps)")
            print(f"   Expected profit per $1: ${c['edge']:.3f}")

if __name__ == "__main__":
    asyncio.run(find_undervalued_favorites())
