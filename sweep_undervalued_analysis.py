#!/usr/bin/env python3
"""Scan Kalshi markets for undervalued high-priced contracts."""

import sys
from datetime import datetime
from typing import List, Tuple, Optional
import importlib.util

# Import Kalshi client
from src.core.api_client import KalshiClient

# Import score analyzer for NBA win probability
spec = importlib.util.spec_from_file_location(
    "score_analyzer",
    "strategies/nba_mispricing/score_analyzer.py"
)
score_analyzer_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score_analyzer_module)
ScoreAnalyzer = score_analyzer_module.ScoreAnalyzer

def get_espn_game_data(game_id: str):
    """Fetch live game data from ESPN."""
    import requests
    
    # Extract ESPN game ID from Kalshi ticker if needed
    # For now, we'll need to fetch the scoreboard
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        print(f"Error fetching ESPN data: {e}")
        return None

def parse_ticker(ticker: str) -> Optional[Tuple[str, str, str]]:
    """Parse Kalshi NBA ticker to extract teams.
    
    Returns (date, away_team, home_team) or None.
    Example: KXNBAGAME-26FEB26LALLAC-LAL -> (26FEB26, LAL, LAC)
    """
    if not ticker.startswith("KXNBAGAME-"):
        return None
    
    parts = ticker.split("-")
    if len(parts) < 3:
        return None
    
    # parts[1] is like "26FEB26LALLAC"
    game_part = parts[1]
    
    # Extract date (first 7 chars: 26FEB26)
    if len(game_part) < 7:
        return None
    date_str = game_part[:7]
    teams_str = game_part[7:]
    
    # Teams are 6 chars total (3 each)
    if len(teams_str) != 6:
        # Try alternate lengths
        if len(teams_str) == 8:  # 4-char teams
            away = teams_str[:4]
            home = teams_str[4:]
        else:
            return None
    else:
        away = teams_str[:3]
        home = teams_str[3:]
    
    # The ticker suffix tells us which team
    team = parts[2] if len(parts) > 2 else None
    
    return (date_str, away, home, team)

def find_undervalued_favorites():
    """Scan Kalshi for undervalued high-priced NBA contracts."""
    
    client = KalshiClient()
    
    print("=" * 100)
    print("SCANNING KALSHI FOR UNDERVALUED HIGH-PRICED CONTRACTS")
    print("=" * 100)
    print()
    
    # Get NBA game markets
    print("Fetching NBA markets from Kalshi...")
    try:
        markets = client.get_markets(series_ticker="KXNBAGAME", status="open")
    except Exception as e:
        print(f"Error fetching markets: {e}")
        return
    
    print(f"Found {len(markets)} open NBA markets\n")
    
    # Get live NBA scores from ESPN
    print("Fetching live scores from ESPN...")
    espn_data = get_espn_game_data(None)
    
    if not espn_data:
        print("Could not fetch ESPN data")
        return
    
    # Build ESPN game lookup
    espn_games = {}
    for event in espn_data.get("events", []):
        competitions = event.get("competitions", [])
        if not competitions:
            continue
        
        comp = competitions[0]
        competitors = comp.get("competitors", [])
        
        if len(competitors) < 2:
            continue
        
        # Extract teams and scores
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        
        if not home or not away:
            continue
        
        home_team = home["team"]["abbreviation"]
        away_team = away["team"]["abbreviation"]
        home_score = int(home["score"])
        away_score = int(away["score"])
        
        status = comp.get("status", {})
        period = status.get("period", 0)
        clock = status.get("displayClock", "0:00")
        game_state = status.get("type", {}).get("state", "")
        
        # Create lookup key
        key = f"{away_team}_{home_team}"
        espn_games[key] = {
            "away_team": away_team,
            "home_team": home_team,
            "away_score": away_score,
            "home_score": home_score,
            "period": period,
            "clock": clock,
            "state": game_state,
        }
    
    print(f"Found {len(espn_games)} live games on ESPN\n")
    
    # Analyze each market
    undervalued = []
    
    for market in markets:
        ticker = market.get("ticker", "")
        
        # Parse ticker
        parsed = parse_ticker(ticker)
        if not parsed:
            continue
        
        date_str, away, home, team = parsed
        
        # Find matching ESPN game
        game_key = f"{away}_{home}"
        espn_game = espn_games.get(game_key)
        
        if not espn_game:
            # Try reversed (sometimes home/away flipped in parsing)
            game_key = f"{home}_{away}"
            espn_game = espn_games.get(game_key)
            if espn_game:
                # Swap away/home
                away, home = home, away
        
        if not espn_game:
            continue
        
        # Skip if game not in progress
        if espn_game["state"] != "in":
            continue
        
        # Get market data
        yes_bid = market.get("yes_bid", 0) / 100.0
        yes_ask = market.get("yes_ask", 100) / 100.0
        market_mid = (yes_bid + yes_ask) / 2.0
        
        # Only look at high-priced contracts (>75 cents)
        if market_mid < 0.75:
            continue
        
        # Calculate fair value based on game state
        away_score = espn_game["away_score"]
        home_score = espn_game["home_score"]
        period = espn_game["period"]
        clock = espn_game["clock"]
        
        # Determine which team this contract is for
        is_home_team = (team == home)
        
        # Calculate time remaining in seconds
        try:
            mins, secs = clock.split(":")
            time_remaining_secs = int(mins) * 60 + float(secs)
            
            # Add time from future quarters
            if period == 1:
                time_remaining_secs += 36 * 60  # 3 more quarters
            elif period == 2:
                time_remaining_secs += 24 * 60  # 2 more quarters
            elif period == 3:
                time_remaining_secs += 12 * 60  # 1 more quarter
            # period 4 is final quarter
        except:
            continue
        
        # Calculate win probability
        if is_home_team:
            score_diff = home_score - away_score
        else:
            score_diff = away_score - home_score
        
        # Use ScoreAnalyzer
        win_prob = ScoreAnalyzer.calculate_win_probability(
            score_diff=score_diff,
            time_remaining_seconds=time_remaining_secs
        )
        
        # Calculate edge
        fair_value = win_prob
        edge = fair_value - market_mid
        edge_bps = edge * 10000
        
        # Find undervalued (fair value > market price)
        if edge > 0.02:  # At least 2% edge
            undervalued.append({
                "ticker": ticker,
                "team": team,
                "away": away,
                "home": home,
                "score": f"{away_score}-{home_score}",
                "period": period,
                "clock": clock,
                "market_bid": yes_bid,
                "market_ask": yes_ask,
                "market_mid": market_mid,
                "fair_value": fair_value,
                "edge": edge,
                "edge_bps": edge_bps,
                "score_diff": score_diff,
                "time_remaining": time_remaining_secs,
            })
    
    # Sort by edge (highest first)
    undervalued.sort(key=lambda x: x["edge"], reverse=True)
    
    # Display results
    print("=" * 100)
    print("UNDERVALUED HIGH-PRICED CONTRACTS (>75 cents, >2% edge)")
    print("=" * 100)
    print()
    
    if not undervalued:
        print("No undervalued contracts found at this time.")
        print("\nThis could mean:")
        print("  1. Markets are efficient (prices match fair value)")
        print("  2. No games currently in progress")
        print("  3. No heavy favorites with significant edge")
    else:
        print(f"{'Ticker':<35} {'Team':<6} {'Score':<10} {'Q':<3} {'Time':<8} {'Mkt Mid':<10} {'Fair Val':<10} {'Edge':<10} {'Edge bps':<10}")
        print("-" * 120)
        
        for contract in undervalued:
            print(f"{contract['ticker']:<35} "
                  f"{contract['team']:<6} "
                  f"{contract['score']:<10} "
                  f"Q{contract['period']:<2} "
                  f"{contract['clock']:<8} "
                  f"${contract['market_mid']:<9.2f} "
                  f"${contract['fair_value']:<9.2f} "
                  f"{contract['edge']:+.4f}    "
                  f"{contract['edge_bps']:+.0f}")
        
        print()
        print(f"Total undervalued contracts found: {len(undervalued)}")
        print()
        
        # Show top 3 with details
        if undervalued:
            print("=" * 100)
            print("TOP OPPORTUNITIES (Detailed)")
            print("=" * 100)
            
            for i, contract in enumerate(undervalued[:3], 1):
                print(f"\n#{i}. {contract['ticker']}")
                print(f"  Team: {contract['team']} ({'Home' if contract['team'] == contract['home'] else 'Away'})")
                print(f"  Score: {contract['away']} {contract['score'].split('-')[0]} - {contract['home']} {contract['score'].split('-')[1]}")
                print(f"  Period: Q{contract['period']}, Time: {contract['clock']}")
                print(f"  Score differential: {contract['score_diff']:+d} points")
                print(f"  Time remaining: {contract['time_remaining']//60:.0f}:{contract['time_remaining']%60:02.0f}")
                print(f"  Market: Bid ${contract['market_bid']:.2f}, Ask ${contract['market_ask']:.2f}, Mid ${contract['market_mid']:.2f}")
                print(f"  Fair value: ${contract['fair_value']:.2f}")
                print(f"  Edge: {contract['edge']:+.4f} ({contract['edge_bps']:+.0f} bps)")
                print(f"  Implied win prob: {contract['market_mid']*100:.1f}%")
                print(f"  True win prob: {contract['fair_value']*100:.1f}%")

if __name__ == "__main__":
    find_undervalued_favorites()
