#!/usr/bin/env python3
"""Detailed analysis of losing trades from blowout strategy."""

import json
from pathlib import Path
from src.backtesting.engine import BacktestEngine, BacktestConfig
from src.backtesting.adapters.nba_adapter import NBADataFeed, BlowoutAdapter

# The 4 losing trades
losing_games = [
    ("CLE_vs_LAC_20260204_214639.json", -0.30),
    ("IND_vs_TOR_20260208_143224.json", -0.30),
    ("MEM_vs_GSW_20260209_212836.json", -4.63),
    ("HOU_vs_NYK_20260221_201054.json", -4.72),
]

config = BacktestConfig(
    initial_bankroll=100.0,
    fill_probability=1.0,
    slippage=0.03,
)

for game_file, expected_loss in losing_games:
    recording = f"data/recordings/{game_file}"
    print("=" * 80)
    print(f"ANALYZING: {game_file}")
    print(f"Expected loss: ${expected_loss:.2f}")
    print("=" * 80)
    
    # Load the game data
    with open(recording) as f:
        game_data = json.load(f)
    
    # Print game metadata
    print(f"\nGame ID: {game_data.get('game_id')}")
    print(f"Date: {game_data.get('date')}")
    print(f"Home: {game_data.get('home_team')} vs Away: {game_data.get('away_team')}")
    
    # Find final score if available
    snapshots = game_data.get('snapshots', [])
    if snapshots:
        final = snapshots[-1]
        print(f"Final Score: Away {final.get('away_score')} - Home {final.get('home_score')}")
        print(f"Total snapshots: {len(snapshots)}")
    
    # Run backtest with verbose to see trade details
    print("\nRunning backtest...")
    feed = NBADataFeed(recording)
    adapter = BlowoutAdapter()
    engine = BacktestEngine(config)
    result = engine.run(feed, adapter, verbose=True)
    
    print(f"\n{result.report()}")
    
    # Analyze the fills
    if result.fills:
        for i, fill in enumerate(result.fills, 1):
            print(f"\nFill #{i}:")
            print(f"  Ticker: {fill.ticker}")
            print(f"  Side: {fill.side}")
            print(f"  Price: ${fill.price:.4f}")
            print(f"  Size: {fill.size}")
            print(f"  Fee: ${fill.fee:.4f}")
            print(f"  Timestamp: {fill.timestamp}")
            
            # Get settlement
            settlement = result.settlements.get(fill.ticker)
            print(f"  Settlement: {settlement}")
            
            # Calculate P&L
            if fill.side == "BID":  # bought YES
                pnl = (settlement - fill.price) * fill.size if settlement is not None else -fill.price * fill.size
            else:  # sold YES (bought NO)
                pnl = (fill.price - settlement) * fill.size if settlement is not None else fill.price * fill.size
            
            print(f"  P&L (before fees): ${pnl:.2f}")
            print(f"  P&L (after fees): ${pnl - fill.fee:.2f}")
    
    print("\n" + "=" * 80 + "\n")
