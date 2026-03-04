#!/usr/bin/env python3
"""Deep dive into the two big losing trades - FIXED."""

import json

def analyze_game(recording_path, team_abbrev):
    with open(recording_path) as f:
        data = json.load(f)
    
    metadata = data.get('metadata', {})
    frames = data.get('frames', [])
    
    print(f"\nGame: {metadata.get('away_team')} @ {metadata.get('home_team')}")
    print(f"Date: {metadata.get('date')}")
    print(f"Final Score: {metadata.get('away_team')} {metadata.get('final_away_score')} - "
          f"{metadata.get('home_team')} {metadata.get('final_home_score')}")
    print(f"Team we bet on: {team_abbrev}")
    print(f"Total frames: {len(frames)}\n")
    
    # Determine if we bet on home or away
    is_home = (team_abbrev == metadata.get('home_team'))
    
    # Find the entry point (look for when market is around 92-94 cents)
    # NOTE: Prices in the data are already in dollar format (0.92 = 92 cents)
    entry_frame = None
    entry_idx = None
    
    for idx, frame in enumerate(frames):
        if is_home:
            bid = frame.get('home_bid', 0.0)
            ask = frame.get('home_ask', 1.0)
        else:
            bid = frame.get('away_bid', 0.0)
            ask = frame.get('away_ask', 1.0)
        
        mid_price = (bid + ask) / 2.0
        
        # Look for entry around 90-96 cents
        if 0.89 <= mid_price <= 0.96:
            entry_frame = frame
            entry_idx = idx
            break
    
    if not entry_frame:
        print("Could not find entry point in 89-96 cent range!")
        # Try to find the highest price
        max_price = 0
        max_idx = 0
        for idx, frame in enumerate(frames):
            if is_home:
                bid = frame.get('home_bid', 0.0)
                ask = frame.get('home_ask', 1.0)
            else:
                bid = frame.get('away_bid', 0.0)
                ask = frame.get('away_ask', 1.0)
            mid = (bid + ask) / 2
            if mid > max_price:
                max_price = mid
                max_idx = idx
                entry_frame = frame
                entry_idx = idx
        print(f"Using highest price found: ${max_price:.2f} at frame {max_idx}\n")
    
    # Print entry context
    print(f"ENTRY POINT (frame #{entry_idx} of {len(frames)}):")
    print(f"  Period: {entry_frame.get('period')}")
    print(f"  Time remaining: {entry_frame.get('time_remaining')}")
    print(f"  Score: {metadata.get('away_team')} {entry_frame.get('away_score')} - "
          f"{metadata.get('home_team')} {entry_frame.get('home_score')}")
    
    if is_home:
        bid = entry_frame.get('home_bid', 0.0)
        ask = entry_frame.get('home_ask', 1.0)
    else:
        bid = entry_frame.get('away_bid', 0.0)
        ask = entry_frame.get('away_ask', 1.0)
    
    print(f"  Market price: Bid ${bid:.2f}, Ask ${ask:.2f}, Mid ${(bid+ask)/2:.2f}")
    
    # Calculate score differential at entry
    away_score = entry_frame.get('away_score', 0)
    home_score = entry_frame.get('home_score', 0)
    if is_home:
        score_diff = home_score - away_score
    else:
        score_diff = away_score - home_score
    
    print(f"  {team_abbrev} {'leading' if score_diff > 0 else 'trailing'} by: {abs(score_diff)} points")
    print(f"  Win probability (implied): {(bid+ask)/2*100:.1f}%")
    
    # Show key moments after entry
    print(f"\nKEY MOMENTS AFTER ENTRY:")
    print(f"  {'Frame':<8} {'Period':<8} {'Time Left':<12} {'Away':<6} {'Home':<6} {team_abbrev + ' Lead':<12} {'Market $':<10} {'Win %':<8}")
    print(f"  {'-'*80}")
    
    step = max(1, (len(frames) - entry_idx) // 20)
    for i in range(entry_idx, len(frames), step):
        frame = frames[i]
        away_sc = frame.get('away_score', 0)
        home_sc = frame.get('home_score', 0)
        
        if is_home:
            lead = home_sc - away_sc
            bid = frame.get('home_bid', 0.0)
            ask = frame.get('home_ask', 1.0)
        else:
            lead = away_sc - home_sc
            bid = frame.get('away_bid', 0.0)
            ask = frame.get('away_ask', 1.0)
        
        mid = (bid + ask) / 2.0
        
        print(f"  {i:<8} {frame.get('period', 'N/A'):<8} {frame.get('time_remaining', 'N/A'):<12} "
              f"{away_sc:<6} {home_sc:<6} {'+' if lead > 0 else ''}{lead:<12} ${mid:<9.2f} {mid*100:<7.1f}%")
    
    # Final frame
    print(f"\nFINAL RESULT:")
    print(f"  Score: {metadata.get('away_team')} {metadata.get('final_away_score')} - "
          f"{metadata.get('home_team')} {metadata.get('final_home_score')}")
    
    away_final = metadata.get('final_away_score', 0)
    home_final = metadata.get('final_home_score', 0)
    
    if is_home:
        won = home_final > away_final
        final_margin = home_final - away_final
    else:
        won = away_final > home_final
        final_margin = away_final - home_final
    
    print(f"  Result: {team_abbrev} {'WON' if won else 'LOST'}")
    print(f"  Final margin: {'+' if final_margin > 0 else ''}{final_margin} points")
    
    # Calculate the collapse
    print(f"\nTHE COLLAPSE:")
    print(f"  Lead at entry: {'+' if score_diff > 0 else ''}{score_diff}")
    print(f"  Final margin: {'+' if final_margin > 0 else ''}{final_margin}")
    print(f"  Total swing: {score_diff - final_margin} points against us")
    if is_home:
        bid_entry = entry_frame.get('home_bid', 0.0)
        ask_entry = entry_frame.get('home_ask', 1.0)
    else:
        bid_entry = entry_frame.get('away_bid', 0.0)
        ask_entry = entry_frame.get('away_ask', 1.0)
    print(f"  Win probability at entry: {(bid_entry+ask_entry)/2*100:.1f}%")
    print(f"  Actual result: {'Win (1.0)' if won else 'Loss (0.0)'}")
    print(f"  Expected profit: ${(bid_entry+ask_entry)/2 * 5:.2f} on $5 bet")
    print(f"  Actual profit: ${(1.0 if won else 0.0) * 5 - 5:.2f}")

# Analyze the two big losers
print("=" * 100)
print("DEEP DIVE: BIG LOSING TRADES")
print("=" * 100)

print("\n" + "=" * 100)
print("LOSS #1: Memphis Grizzlies @ Golden State Warriors (-$4.63)")
print("=" * 100)
analyze_game("data/recordings/MEM_vs_GSW_20260209_212836.json", "MEM")

print("\n\n" + "=" * 100)
print("LOSS #2: Houston Rockets @ New York Knicks (-$4.72)")
print("=" * 100)
analyze_game("data/recordings/HOU_vs_NYK_20260221_201054.json", "HOU")

