"""
Test script for NBA data feeds.
Verifies that orderbook and score feeds are working correctly.
"""

import sys
import os
import time

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)

from data_feeds.orderbook_feed import OrderbookFeed
from data_feeds.score_feed import NBAScoreFeed, get_nba_game_info_from_ticker, get_nbalive_games

double_parent_dir = os.path.dirname(parent_dir)
sys.path.append(double_parent_dir)

from kalshi_utils.client_wrapper import KalshiWrapped

def test_live_games():
    """Test fetching live NBA games."""
    print("="*70)
    print("TEST 1: Fetching Live NBA Games")
    print("="*70 + "\n")
    
    try:
        live_games = get_nbalive_games()
        
        if not live_games:
            print("❌ No live NBA games found")
            print("   Make sure there's an NBA game currently in progress")
            return False
        
        print(f"✓ Found {len(live_games)} live game(s):\n")
        for game in live_games:
            print(f"  Game ID: {game['id']}")
            print(f"  Matchup: {game['matchup']}")
            print(f"  Score:   {game['score']}")
            print(f"  Status:  {game['clock']}")
            print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_kalshi_markets():
    """Test fetching Kalshi NBA markets."""
    print("="*70)
    print("TEST 2: Fetching Kalshi NBA Markets")
    print("="*70 + "\n")
    
    try:
        kalshi = KalshiWrapped()
        client = kalshi.GetClient()
        
        print("Fetching NBA markets...")
        markets = kalshi.GetLiveNBAMarkets()
        
        if not markets:
            print("❌ No live NBA markets found on Kalshi")
            print("   This might be normal if no games are in progress")
            return False
        
        print(f"✓ Found {len(markets)} market(s):\n")
        for market in markets[:5]:  # Show first 5
            print(f"  Ticker: {market.ticker}")
            print(f"  Title:  {market.yes_sub_title}")
            print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_score_feed():
    """Test NBA score feed."""
    print("="*70)
    print("TEST 3: NBA Score Feed")
    print("="*70 + "\n")
    
    try:
        # Get a live game
        live_games = get_nbalive_games()
        
        if not live_games:
            print("❌ No live games to test with")
            return False
        
        game = live_games[0]
        print(f"Testing with game: {game['matchup']}")
        print(f"Current score: {game['score']}\n")
        
        # Extract team codes
        matchup = game['matchup']
        away_team = matchup[:3]
        home_team = matchup[3:6]
        
        # Create feed
        feed = NBAScoreFeed(
            game_id=game['id'],
            home_team_tricode=home_team,
            away_team_tricode=away_team,
            poll_interval_ms=3000
        )
        
        print("Starting score feed...")
        feed.start()
        
        # Wait for data
        print("Collecting data (10 seconds)...\n")
        for i in range(5):
            time.sleep(2)
            features = feed.get_current_features()
            
            print(f"Update {i+1}:")
            print(f"  Score: {away_team} {features['away_score']:.0f} - "
                  f"{features['home_score']:.0f} {home_team}")
            print(f"  Win Probability: {features['win_probability']:.1%}")
            print(f"  Momentum: {features['momentum']:+.2f} pts/min")
            print(f"  Game Completion: {features['game_completion']:.1%}")
            print()
        
        feed.stop()
        print("✓ Score feed working correctly")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_orderbook_feed():
    """Test orderbook feed."""
    print("="*70)
    print("TEST 4: Orderbook Feed")
    print("="*70 + "\n")
    
    try:
        kalshi = KalshiWrapped()
        client = kalshi.GetClient()
        
        markets = kalshi.GetLiveNBAMarkets()
        
        if not markets:
            print("❌ No markets to test with")
            return False
        
        ticker = markets[0].ticker
        print(f"Testing with market: {ticker}\n")
        
        # Create feed
        feed = OrderbookFeed(
            client=client,
            ticker=ticker,
            poll_interval_ms=500
        )
        
        print("Starting orderbook feed...")
        feed.start()
        
        # Wait for data
        print("Collecting data (10 seconds)...\n")
        for i in range(5):
            time.sleep(2)
            features = feed.get_current_features()
            
            print(f"Update {i+1}:")
            print(f"  Imbalance: {features['imbalance']:+.3f}")
            print(f"  Imbalance EMA: {features['imbalance_ema']:+.3f}")
            print(f"  Spread: ${features['spread']:.4f}")
            print(f"  Spread BPS: {features['spread_bps']:.1f}")
            print()
        
        feed.stop()
        print("✓ Orderbook feed working correctly")
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_game_info_extraction():
    """Test extracting game info from ticker."""
    print("="*70)
    print("TEST 5: Game Info Extraction")
    print("="*70 + "\n")
    
    try:
        kalshi = KalshiWrapped()
        markets = kalshi.GetLiveNBAMarkets()
        
        if not markets:
            print("❌ No markets to test with")
            return False
        
        ticker = markets[0].ticker
        print(f"Testing with ticker: {ticker}\n")
        
        game_info = get_nba_game_info_from_ticker(ticker)
        
        if not game_info:
            print("❌ Could not extract game info")
            return False
        
        print("✓ Successfully extracted game info:")
        print(f"  Game ID: {game_info['game_id']}")
        print(f"  Home Team: {game_info['home_team']}")
        print(f"  Away Team: {game_info['away_team']}")
        print(f"  Matchup: {game_info['matchup']}")
        print()
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("NBA TRADING BOT - FEED TESTS")
    print("="*70 + "\n")
    
    results = []
    
    # Run tests
    results.append(("Live Games API", test_live_games()))
    print()
    
    results.append(("Kalshi Markets", test_kalshi_markets()))
    print()
    
    results.append(("Score Feed", test_score_feed()))
    print()
    
    results.append(("Orderbook Feed", test_orderbook_feed()))
    print()
    
    results.append(("Game Info Extraction", test_game_info_extraction()))
    print()
    
    # Summary
    print("="*70)
    print("TEST SUMMARY")
    print("="*70)
    
    for name, passed in results:
        status = "✓ PASS" if passed else "❌ FAIL"
        print(f"  {name:30} {status}")
    
    print("="*70)
    
    all_passed = all(r[1] for r in results)
    
    if all_passed:
        print("\n✓ All tests passed! Ready to trade.")
    else:
        print("\n❌ Some tests failed. Fix errors before trading.")
    
    print()


if __name__ == "__main__":
    main()