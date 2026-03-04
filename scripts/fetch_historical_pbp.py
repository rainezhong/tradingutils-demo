#!/usr/bin/env python3
"""
Fetch historical play-by-play data for 3 NBA seasons.

Uses LeagueGameLog to discover game IDs, then fetches PBP via the live endpoint.
Caches everything to data/nba_cache/pbp/ — skips already-cached games.

Usage:
    python3 scripts/fetch_historical_pbp.py
    python3 scripts/fetch_historical_pbp.py --seasons 2024-25 2023-24
    python3 scripts/fetch_historical_pbp.py --include-playoffs
"""

import argparse
import os
import pickle
import time

from nba_api.stats.endpoints import leaguegamelog
from nba_api.live.nba.endpoints import playbyplay

CACHE_DIR = "data/nba_cache"
PBP_CACHE_DIR = os.path.join(CACHE_DIR, "pbp")
GAME_LOG_CACHE_DIR = os.path.join(CACHE_DIR, "game_logs")


def fetch_game_ids(season: str, season_type: str = "Regular Season") -> list:
    """Fetch all game IDs for a season via LeagueGameLog. Returns deduplicated list."""
    cache_key = f"{season}_{season_type.replace(' ', '_')}"
    cache_path = os.path.join(GAME_LOG_CACHE_DIR, f"game_ids_{cache_key}.pkl")

    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            game_ids = pickle.load(f)
        print(f"  [{season} {season_type}] Loaded {len(game_ids)} game IDs from cache")
        return game_ids

    print(f"  [{season} {season_type}] Fetching game log...", end=" ", flush=True)
    for attempt in range(3):
        try:
            log = leaguegamelog.LeagueGameLog(
                season=season,
                season_type_all_star=season_type,
                timeout=30,
            )
            df = log.get_data_frames()[0]
            break
        except Exception as e:
            if attempt < 2:
                print(f"retry ({e})...", end=" ", flush=True)
                time.sleep(3)
            else:
                print(f"ERROR: {e}")
                return []

    # Each game appears twice (one row per team) — deduplicate
    game_ids = sorted(df["GAME_ID"].unique().tolist())
    print(f"{len(game_ids)} games")

    os.makedirs(GAME_LOG_CACHE_DIR, exist_ok=True)
    with open(cache_path, "wb") as f:
        pickle.dump(game_ids, f)

    time.sleep(1)  # Rate limit
    return game_ids


def fetch_pbp(game_id: str) -> bool:
    """Fetch PBP for a single game. Returns True if fetched, False if cached/error."""
    cache_path = os.path.join(PBP_CACHE_DIR, f"pbp_{game_id}.pkl")

    if os.path.exists(cache_path):
        return False  # Already cached

    try:
        pbp = playbyplay.PlayByPlay(game_id, timeout=15)
        actions = pbp.get_dict()["game"]["actions"]
    except Exception as e:
        print(f"    ERROR fetching {game_id}: {e}")
        return False

    with open(cache_path, "wb") as f:
        pickle.dump(actions, f)
    return True


def main():
    parser = argparse.ArgumentParser(description="Fetch historical NBA PBP data")
    parser.add_argument(
        "--seasons",
        nargs="+",
        default=["2024-25", "2023-24", "2022-23"],
        help="Seasons to fetch (default: 2024-25 2023-24 2022-23)",
    )
    parser.add_argument(
        "--include-playoffs",
        action="store_true",
        help="Also fetch playoff games",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=1.0,
        help="Seconds between API calls (default: 1.0)",
    )
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.makedirs(PBP_CACHE_DIR, exist_ok=True)

    print("=== Fetch Historical PBP Data ===\n")

    # Step 1: Collect all game IDs
    all_game_ids = []
    season_types = ["Regular Season"]
    if args.include_playoffs:
        season_types.append("Playoffs")

    for season in args.seasons:
        for season_type in season_types:
            ids = fetch_game_ids(season, season_type)
            all_game_ids.extend(ids)

    # Deduplicate (shouldn't be needed but just in case)
    all_game_ids = sorted(set(all_game_ids))
    print(f"\nTotal unique game IDs: {len(all_game_ids)}")

    # Check how many are already cached
    already_cached = sum(
        1
        for gid in all_game_ids
        if os.path.exists(os.path.join(PBP_CACHE_DIR, f"pbp_{gid}.pkl"))
    )
    to_fetch = len(all_game_ids) - already_cached
    print(f"Already cached: {already_cached}")
    print(f"To fetch: {to_fetch}")

    if to_fetch == 0:
        print("\nAll games already cached.")
        return

    print(f"\nFetching PBP data ({args.rate_limit}s between calls)...")
    print(f"Estimated time: ~{to_fetch * args.rate_limit / 60:.0f} minutes\n")

    fetched = 0
    errors = 0
    for i, game_id in enumerate(all_game_ids):
        cache_path = os.path.join(PBP_CACHE_DIR, f"pbp_{game_id}.pkl")
        if os.path.exists(cache_path):
            continue

        success = fetch_pbp(game_id)
        if success:
            fetched += 1
            if fetched % 50 == 0:
                print(f"  [{fetched}/{to_fetch}] fetched ({errors} errors)")
            time.sleep(args.rate_limit)
        else:
            errors += 1

    print("\n=== Summary ===")
    print(f"  Total games: {len(all_game_ids)}")
    print(f"  Previously cached: {already_cached}")
    print(f"  Newly fetched: {fetched}")
    print(f"  Errors: {errors}")
    print(f"  Cache dir: {PBP_CACHE_DIR}/")


if __name__ == "__main__":
    main()
