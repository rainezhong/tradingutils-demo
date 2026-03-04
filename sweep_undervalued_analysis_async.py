#!/usr/bin/env python3
"""Scan Kalshi markets for undervalued high-priced contracts."""

import asyncio
from typing import List, Tuple, Optional
import importlib.util
import requests

# Import Kalshi client
from core import KalshiExchangeClient

# Import score analyzer for NBA win probability
spec = importlib.util.spec_from_file_location(
    "score_analyzer",
    "strategies/nba_mispricing/score_analyzer.py"
)
score_analyzer_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(score_analyzer_module)
ScoreAnalyzer = score_analyzer_module.ScoreAnalyzer

def get_espn_game_data():
    """Fetch live game data from ESPN."""
    url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data
    except Exception as e:
        print(f"Error fetching ESPN data: {e}")
        return None

def parse_ticker(ticker: str) -> Optional[Tuple[str, str, str, str]]:
    """Parse Kalshi NBA ticker to extract teams.
    
    Returns (date, away_team, home_team, contract_team) or None.
    Example: KXNBAGAME-26FEB26LALLAC-LAL -> (26FEB26, LAL, LAC, LAL)
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
    
    # Teams are 6 chars total (3 each) or 8 chars (4 each)
    if len(teams_str) == 6:
        away = teams_str[:3]
        home = teams_str[3:]
    elif len(teams_str) == 8:
        away = teams_str[:4]
        home = teams_str[4:]
    else:
        return None
    
    # The ticker suffix tells us which team
    team = parts[2] if len(parts) > 2 else None
    
    return (date_str, away, home, team)

async def find_undervalued_favorites():
    """Scan Kalshi for undervalued high-priced NBA contracts."""
    
    print("=" * 100)
    print("SCANNING KALSHI FOR UNDERVALUED HIGH-PRICED CONTRACTS")
    print("=" * 100)
    print()
    
    # Get Kalshi client
    client = KalshiExchangeClient.from_env(demo=False)
    await client.connect()
    
    # Get NBA game markets
    print("Fetching NBA markets from Kalshi...")
    try:
        markets = await client.get_markets(series_ticker="KXNBAGAME", status="open")
    except Exception as e:
        print(f"Error fetching markets: {e}")
        await client.disconnect()
        return
    
    print(f"Found {len(markets)} open NBA markets\n")
    
    # Get live NBA scores from ESPN
    print("Fetching live scores from ESPN...")
    espn_data = get_espn_game_data()
    
    if not espn_data:
        print("Could not fetch ESPN data")
        await client.disconnect()
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
        
        # Create lookup keys (both directions)
        espn_games[f"{away_team}_{home_team}"] = {
            "away_team": away_team,
            "home_team": home_team,
            "away_score": away_score,
            "home_score": home_score,
            "period": period,
            "clock": clock,
            "state": game_state,
        }
        espn_games[f"{home_team}_{away_team}"] = espn_games[f"{away_team}_{home_team}"]
    
    print(f"Found {len(espn_data.get('events', []))} games on ESPN\n")
    
    # Analyze each market
    undervalued = []
    
    for market in markets:
        ticker = market.ticker
        
        # Parse ticker
        parsed = parse_ticker(ticker)
        if not parsed:
            continue
        
        date_str, away, home, team = parsed
        
        # Find matching ESPN game - try multiple combinations
        espn_game = None
        for key in [f"{away}_{home}", f"{home}_{away}"]:
            if key in espn_games:
                espn_game = espn_games[key]
                break
        
        if not espn_game:
            continue
        
        # Skip if game not in progress
        if espn_game["state"] != "in":
            continue
        
        # Get market data
        yes_bid = market.yes_bid / 100.0
        yes_ask = market.yes_ask / 100.0
        market_mid = (yes_bid + yes_ask) / 2.0
        
        # Only look at high-priced contracts (>75 cents)
        if market_mid < 0.75:
            continue
        
        # Calculate fair value based on game state
        away_score = espn_game["away_score"]
        home_score = espn_game["home_score"]
        period = espn_game["period"]
        clock = espn_game["clock"]
        
        # Match team abbreviations (ESPN might use different format)
        actual_away = espn_game["away_team"]
        actual_home = espn_game["home_team"]
        
        # Determine which team this contract is for
        is_home_team = (team == home or team == actual_home)
        
        # Calculate time remaining in seconds
        try:
            if ":" in clock:
                parts = clock.split(":")
                if len(parts) == 2:
                    mins, secs = parts
                    time_remaining_secs = int(float(mins)) * 60 + float(secs)
                else:
                    continue
            else:
                time_remaining_secs = float(clock)
            
            # Add time from future quarters
            if period == 1:
                time_remaining_secs += 36 * 60  # 3 more quarters
            elif period == 2:
                time_remaining_secs += 24 * 60  # 2 more quarters
            elif period == 3:
                time_remaining_secs += 12 * 60  # 1 more quarter
            # period 4 is final quarter
        except Exception as e:
            print(f"Error parsing clock '{clock}': {e}")
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
        if edge > 0.01:  # At least 1% edge
            undervalued.append({
                "ticker": ticker,
                "team": team,
                "away": actual_away,
                "home": actual_home,
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
    
    await client.disconnect()
    
    # Sort by edge (highest first)
    undervalued.sort(key=lambda x: x["edge"], reverse=True)
    
    # Display results
    print("=" * 100)
    print("UNDERVALUED HIGH-PRICED CONTRACTS (>75 cents, >1% edge)")
    print("=" * 100)
    print()
    
    if not undervalued:
        print("No undervalued contracts found at this time.")
        print("\nThis could mean:")
        print("  1. Markets are efficient (prices match fair value)")
        print("  2. No games currently in progress")
        print("  3. No heavy favorites with significant edge")
    else:
        print(f"{'Ticker':<40} {'Score':<12} {'Q':<3} {'Time':<10} {'Mkt':<8} {'Fair':<8} {'Edge':<8} {'Edge bps':<10}")
        print("-" * 120)
        
        for contract in undervalued:
            is_home = contract['team'] == contract['home']
            score_display = f"{contract['away']} {contract['score'].split('-')[0]}-{contract['score'].split('-')[1]} {contract['home']}"
            if is_home:
                score_display = f"{score_display} (H)"
            else:
                score_display = f"(A) {score_display}"
            
            print(f"{contract['ticker']:<40} "
                  f"{score_display:<12} "
                  f"Q{contract['period']:<2} "
                  f"{contract['clock']:<10} "
                  f"${contract['market_mid']:<7.2f} "
                  f"${contract['fair_value']:<7.2f} "
                  f"{contract['edge']:+.3f}   "
                  f"{contract['edge_bps']:+6.0f}")
        
        print()
        print(f"Total undervalued contracts found: {len(undervalued)}")
        print()
        
        # Show top 5 with details
        if undervalued:
            print("=" * 100)
            print("TOP OPPORTUNITIES (Detailed)")
            print("=" * 100)
            
            for i, contract in enumerate(undervalued[:5], 1):
                is_home = contract['team'] == contract['home']
                print(f"\n#{i}. {contract['ticker']}")
                print(f"  Team: {contract['team']} ({'Home' if is_home else 'Away'})")
                print(f"  Score: {contract['away']} {contract['score'].split('-')[0]} - {contract['home']} {contract['score'].split('-')[1]}")
                print(f"  Period: Q{contract['period']}, Time: {contract['clock']}")
                print(f"  Score differential: {contract['score_diff']:+d} points")
                print(f"  Time remaining: {contract['time_remaining']//60:.0f}:{contract['time_remaining']%60:02.0f}")
                print(f"  Market: Bid ${contract['market_bid']:.3f}, Ask ${contract['market_ask']:.3f}, Mid ${contract['market_mid']:.3f}")
                print(f"  Fair value: ${contract['fair_value']:.3f}")
                print(f"  Edge: {contract['edge']:+.4f} ({contract['edge_bps']:+.0f} bps)")
                print(f"  Implied win prob: {contract['market_mid']*100:.1f}%")
                print(f"  True win prob: {contract['fair_value']*100:.1f}%")
                print(f"  Expected profit per $1: ${contract['edge']:.3f}")

if __name__ == "__main__":
    asyncio.run(find_undervalued_favorites())
