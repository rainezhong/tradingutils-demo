#!/usr/bin/env python3
"""Scan Kalshi for high-priced NBA contracts and check for undervaluation."""

import asyncio
from typing import Optional, Tuple
import requests
from scipy.stats import norm

from core import KalshiExchangeClient


def calculate_win_probability(score_diff: int, time_remaining_seconds: int) -> float:
    """Calculate win probability using normal distribution model."""
    if time_remaining_seconds <= 0:
        return 1.0 if score_diff > 0 else (0.5 if score_diff == 0 else 0.0)
    
    possessions_per_second = 100.0 / (48.0 * 60.0)
    remaining_possessions = time_remaining_seconds * possessions_per_second
    std_per_possession = 2.4
    std_dev = std_per_possession * (remaining_possessions ** 0.5)
    
    if std_dev < 0.1:
        return 1.0 if score_diff > 0 else (0.5 if score_diff == 0 else 0.0)
    
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
        print(f"Error fetching ESPN: {e}")
        return None


async def scan_markets():
    """Scan Kalshi for high-priced NBA contracts."""
    
    print("=" * 100)
    print("KALSHI NBA HIGH-PRICED CONTRACTS SWEEP")
    print("=" * 100)
    print()
    
    client = KalshiExchangeClient.from_env(demo=False)
    await client.connect()
    
    print("Fetching NBA markets...")
    markets = await client.get_markets(series_ticker="KXNBAGAME", status="open")
    print(f"Found {len(markets)} open markets\n")
    
    # Get ESPN data for live games
    print("Fetching ESPN live scores...")
    espn_data = get_espn_game_data()
    
    espn_games = {}
    if espn_data:
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
    
    live_count = sum(1 for g in set(espn_games.values()) if g.get('state') == 'in')
    print(f"ESPN: {len(set(espn_games.values()))} games, {live_count} in progress\n")
    
    # Analyze all high-priced contracts
    high_priced = []
    undervalued_live = []
    
    for market in markets:
        yes_bid = market.yes_bid / 100.0
        yes_ask = market.yes_ask / 100.0
        mid = (yes_bid + yes_ask) / 2.0
        
        # Look for contracts >80 cents
        if mid < 0.80:
            continue
        
        ticker = market.ticker
        
        # Try to parse team info
        parts = ticker.split("-")
        team = parts[2] if len(parts) > 2 else "?"
        
        contract_info = {
            "ticker": ticker,
            "team": team,
            "bid": yes_bid,
            "ask": yes_ask,
            "mid": mid,
            "volume": market.volume,
            "open_interest": market.open_interest,
            "edge": None,
            "fair_value": None,
            "live": False,
        }
        
        high_priced.append(contract_info)
        
        # Check if this is a live game
        # Try to match to ESPN data (simplified matching)
        for game in espn_games.values():
            if game["state"] != "in":
                continue
            
            # Check if team abbreviation matches
            if team in [game["away_team"], game["home_team"]]:
                # Calculate fair value
                is_home = (team == game["home_team"])
                score_diff = (game["home_score"] - game["away_score"]) if is_home else (game["away_score"] - game["home_score"])
                
                # Parse time
                try:
                    clock = game["clock"]
                    if ":" in clock:
                        mins, secs = clock.split(":")
                        time_secs = int(float(mins)) * 60 + float(secs)
                    else:
                        time_secs = float(clock)
                    
                    period = game["period"]
                    if period == 1:
                        time_secs += 36 * 60
                    elif period == 2:
                        time_secs += 24 * 60
                    elif period == 3:
                        time_secs += 12 * 60
                    
                    fair = calculate_win_probability(score_diff, time_secs)
                    edge = fair - mid
                    
                    contract_info["edge"] = edge
                    contract_info["fair_value"] = fair
                    contract_info["live"] = True
                    contract_info["score"] = f"{game['away_team']} {game['away_score']}-{game['home_score']} {game['home_team']}"
                    contract_info["period"] = period
                    contract_info["clock"] = clock
                    contract_info["score_diff"] = score_diff
                    
                    if edge > 0.01:  # 1% edge
                        undervalued_live.append(contract_info)
                    
                    break  # Found match
                except:
                    pass
    
    # Sort
    high_priced.sort(key=lambda x: x["mid"], reverse=True)
    undervalued_live.sort(key=lambda x: x["edge"], reverse=True)
    
    # Display
    print("=" * 100)
    print(f"ALL HIGH-PRICED CONTRACTS (>80 cents): {len(high_priced)} found")
    print("=" * 100)
    print()
    
    print(f"{'Ticker':<50} {'Mid%':<8} {'Bid%':<8} {'Ask%':<8} {'Vol':<8} {'Status':<10}")
    print("-" * 110)
    
    for c in high_priced[:20]:  # Show top 20
        status = "LIVE" if c["live"] else "Pre-game"
        if c["edge"] is not None and c["edge"] > 0.01:
            status = f"LIVE +{c['edge']*100:.1f}%"
        
        print(f"{c['ticker']:<50} {c['mid']*100:<7.1f}% {c['bid']*100:<7.1f}% {c['ask']*100:<7.1f}% {c['volume']:<8} {status:<10}")
    
    if len(high_priced) > 20:
        print(f"... and {len(high_priced) - 20} more")
    
    print()
    
    if undervalued_live:
        print("=" * 100)
        print(f"UNDERVALUED LIVE CONTRACTS: {len(undervalued_live)} found")
        print("=" * 100)
        print()
        
        for i, c in enumerate(undervalued_live, 1):
            print(f"#{i}. {c['ticker']}")
            print(f"   {c['score']} (Q{c['period']}, {c['clock']})")
            print(f"   Team {c['team']} lead: {c['score_diff']:+d} points")
            print(f"   Market: {c['mid']*100:.1f}% (bid {c['bid']*100:.1f}%, ask {c['ask']*100:.1f}%)")
            print(f"   Fair value: {c['fair_value']*100:.1f}%")
            print(f"   EDGE: {c['edge']*100:+.2f}% ({c['edge']*10000:+.0f} bps)")
            print()
    else:
        if live_count > 0:
            print("No undervalued contracts found in live games (all fairly priced).")
        else:
            print("No live games currently - cannot calculate edges.")
    
    try:
        await client.close()
    except:
        pass

if __name__ == "__main__":
    asyncio.run(scan_markets())
