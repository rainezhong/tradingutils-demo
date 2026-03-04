#!/usr/bin/env python3
"""
Build synthetic game recordings by merging Kalshi candle data with NBA play-by-play scores.

Candle data (data/nba_cache/candles_*.pkl) provides 1-minute price snapshots.
NBA play-by-play provides score/period/clock at each game action.
Output: JSON recordings in the same format as nba_recorder.py, consumable by backtest_blowout.py.

First run: ~3.5 min (21 date lookups + 155 PBP fetches with 1s rate limit)
Subsequent runs (cached): <10 seconds
"""

import argparse
import bisect
import glob
import json
import os
import pickle
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# NBA API imports
from nba_api.stats.endpoints import leaguegamefinder
from nba_api.stats.static import teams as nba_teams_static
from nba_api.live.nba.endpoints import playbyplay

# Month abbreviation → number
MONTH_MAP = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

CACHE_DIR = "data/nba_cache"
PBP_CACHE_DIR = os.path.join(CACHE_DIR, "pbp")
GAME_ID_MAP_PATH = os.path.join(CACHE_DIR, "game_id_map.pkl")
OUTPUT_DIR = "data/recordings/synthetic"


def parse_event_ticker(event_ticker: str):
    """Parse KXNBAGAME-26JAN06CLEIND → (date_str, away, home)."""
    m = re.match(
        r"KXNBAGAME-(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})", event_ticker
    )
    if not m:
        return None
    yy, mon_str, dd, away, home = m.groups()
    month = MONTH_MAP.get(mon_str)
    if not month:
        return None
    date_str = f"20{yy}-{month:02d}-{int(dd):02d}"
    return date_str, away, home


def discover_events(candle_dir: str):
    """Scan candle files and return dict of event_ticker → (date, away, home)."""
    events = {}
    for path in glob.glob(os.path.join(candle_dir, "candles_KXNBAGAME-*.pkl")):
        basename = os.path.basename(path)
        m = re.match(r"candles_(KXNBAGAME-\w+)-(\w+)\.pkl", basename)
        if not m:
            continue
        event_ticker = m.group(1)
        if event_ticker in events:
            continue
        parsed = parse_event_ticker(event_ticker)
        if parsed:
            events[event_ticker] = parsed
    return events


def build_nba_team_id_map():
    """Build abbreviation → NBA team_id map."""
    return {t["abbreviation"]: t["id"] for t in nba_teams_static.get_teams()}


def fetch_game_ids(events: dict, force_refresh: bool = False):
    """
    For each event, find the NBA game_id via LeagueGameFinder.
    Batches by date (one API call per unique date). Caches to disk.
    Returns dict of event_ticker → game_id.
    """
    # Load existing cache
    cached = {}
    if os.path.exists(GAME_ID_MAP_PATH) and not force_refresh:
        with open(GAME_ID_MAP_PATH, "rb") as f:
            cached = pickle.load(f)

    build_nba_team_id_map()

    # Group events by date, skipping dates where all events are already cached/attempted
    date_events = defaultdict(list)
    for event_ticker, (date_str, away, home) in events.items():
        date_events[date_str].append((event_ticker, away, home))

    # Only fetch dates that have uncached events
    # cached stores event_ticker → game_id for found games
    # We also track "attempted" dates to avoid re-querying dates with unmatched games
    dates_needing_fetch = []
    game_id_map = dict(cached)
    for date_str in sorted(date_events.keys()):
        events_on_date = date_events[date_str]
        all_known = all(et in cached for et, _, _ in events_on_date)
        if not all_known:
            dates_needing_fetch.append(date_str)

    if not dates_needing_fetch:
        print(f"  Game ID map loaded from cache ({len(game_id_map)} entries)")
        return game_id_map

    dates_sorted = dates_needing_fetch
    print(f"  Fetching game IDs for {len(dates_sorted)} dates...")

    for i, date_str in enumerate(dates_sorted):
        print(f"    [{i + 1}/{len(dates_sorted)}] {date_str}...", end=" ", flush=True)

        # LeagueGameFinder: search by date
        # date_from and date_to format: MM/DD/YYYY
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        date_fmt = dt.strftime("%m/%d/%Y")

        try:
            finder = leaguegamefinder.LeagueGameFinder(
                date_from_nullable=date_fmt,
                date_to_nullable=date_fmt,
                league_id_nullable="00",  # NBA
            )
            games_df = finder.get_data_frames()[0]
        except Exception as e:
            print(f"ERROR: {e}")
            time.sleep(2)
            continue

        # Build lookup: (away_abbrev, home_abbrev) → game_id
        # Each game appears twice in the results (one row per team)
        # The home team has MATCHUP like "IND vs. CLE", away has "CLE @ IND"
        game_lookup = {}
        for _, row in games_df.iterrows():
            game_id = row["GAME_ID"]
            team_abbrev = row["TEAM_ABBREVIATION"]
            matchup = row["MATCHUP"]
            if " vs. " in matchup:
                # This is the home team row: "IND vs. CLE"
                home_abbrev = team_abbrev
                away_abbrev = matchup.split(" vs. ")[-1].strip()
                game_lookup[(away_abbrev, home_abbrev)] = game_id

        matched = 0
        for event_ticker, away, home in date_events[date_str]:
            gid = game_lookup.get((away, home))
            if gid:
                game_id_map[event_ticker] = gid
                matched += 1
            else:
                game_id_map[event_ticker] = None  # Mark as attempted but unmatched
                print(f"\n      WARNING: No NBA game found for {away}@{home}", end="")

        print(f"{matched}/{len(date_events[date_str])} matched")
        time.sleep(1)  # Rate limit

    # Cache
    os.makedirs(os.path.dirname(GAME_ID_MAP_PATH), exist_ok=True)
    with open(GAME_ID_MAP_PATH, "wb") as f:
        pickle.dump(game_id_map, f)
    print(f"  Cached {len(game_id_map)} game IDs to {GAME_ID_MAP_PATH}")
    return game_id_map


def fetch_pbp(game_id: str, force_refresh: bool = False):
    """Fetch play-by-play for a game, with disk caching."""
    os.makedirs(PBP_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(PBP_CACHE_DIR, f"pbp_{game_id}.pkl")

    if os.path.exists(cache_path) and not force_refresh:
        with open(cache_path, "rb") as f:
            return pickle.load(f)

    try:
        pbp = playbyplay.PlayByPlay(game_id)
        actions = pbp.get_dict()["game"]["actions"]
    except Exception as e:
        print(f"      PBP error for {game_id}: {e}")
        return None

    with open(cache_path, "wb") as f:
        pickle.dump(actions, f)
    return actions


def parse_pbp_clock(clock_str: str) -> float:
    """Parse PBP clock like 'PT11M42.00S' → seconds remaining (e.g. 702.0)."""
    m = re.match(r"PT(\d+)M([\d.]+)S", clock_str)
    if m:
        return int(m.group(1)) * 60 + float(m.group(2))
    m = re.match(r"PT([\d.]+)S", clock_str)
    if m:
        return float(m.group(1))
    return 0.0


def _parse_iso_ts(s: str) -> float:
    """Parse ISO timestamp like '2026-01-07T00:10:43.7Z' to unix timestamp.
    Python 3.9 fromisoformat doesn't handle 'Z' or variable fractional seconds."""
    s = s.replace("Z", "+00:00")
    # Normalize fractional seconds to 6 digits for Python 3.9
    m = re.match(r"(.+\.\d+)(\+.*)", s)
    if m:
        base, tz = m.groups()
        # Pad or truncate fractional part to 6 digits
        dot_idx = base.rindex(".")
        frac = base[dot_idx + 1 :]
        frac = (frac + "000000")[:6]
        s = base[: dot_idx + 1] + frac + tz
    return datetime.fromisoformat(s).timestamp()


def build_score_timeline(actions: list):
    """
    Convert PBP actions → sorted list of (unix_ts, home_score, away_score, period, time_remaining_str).
    time_remaining_str formatted as "Q4 8:30" to match parse_time_remaining() in late_game_blowout.py.
    """
    timeline = []
    for action in actions:
        period = action.get("period", 0)
        clock_str = action.get("clock", "PT0M0.00S")
        time_actual = action.get("timeActual")  # ISO UTC string
        score_home = action.get("scoreHome")
        score_away = action.get("scoreAway")

        if time_actual is None or score_home is None or score_away is None:
            continue

        # Parse timestamp (handle varied fractional second lengths for Python 3.9)
        try:
            ts = _parse_iso_ts(time_actual)
        except (ValueError, AttributeError):
            continue

        # Parse clock → time_remaining string like "Q4 8:30"
        secs = parse_pbp_clock(clock_str)
        minutes = int(secs) // 60
        seconds = int(secs) % 60
        time_str = f"Q{period} {minutes}:{seconds:02d}"

        home_score = int(score_home) if score_home else 0
        away_score = int(score_away) if score_away else 0

        timeline.append((ts, home_score, away_score, period, time_str))

    timeline.sort(key=lambda x: x[0])
    return timeline


def load_candles(event_ticker: str, team: str):
    """Load candle pickle for a specific team market. Returns list of dicts."""
    path = os.path.join(CACHE_DIR, f"candles_{event_ticker}-{team}.pkl")
    if not os.path.exists(path):
        return []
    with open(path, "rb") as f:
        return pickle.load(f)


def merge_candles_and_scores(
    event_ticker: str,
    away: str,
    home: str,
    score_timeline: list,
    game_id: str,
    date_str: str,
):
    """
    Merge candle prices with score timeline to produce recording frames.
    Returns (metadata_dict, frames_list).
    """
    home_candles = load_candles(event_ticker, home)
    away_candles = load_candles(event_ticker, away)

    # Build timestamp → candle data dicts
    home_by_ts = {c["ts"]: c for c in home_candles}
    away_by_ts = {c["ts"]: c for c in away_candles}

    # Union of all timestamps, sorted
    all_ts = sorted(set(home_by_ts.keys()) | set(away_by_ts.keys()))
    if not all_ts:
        return None, None

    # Score timeline timestamps for bisection
    if score_timeline:
        score_ts_list = [s[0] for s in score_timeline]
    else:
        score_ts_list = []

    # Determine final score from last timeline entry
    final_home_score = 0
    final_away_score = 0
    final_period = 0
    if score_timeline:
        _, final_home_score, final_away_score, final_period, _ = score_timeline[-1]

    home_ticker = f"{event_ticker}-{home}"
    away_ticker = f"{event_ticker}-{away}"

    frames = []
    for ts in all_ts:
        hc = home_by_ts.get(ts)
        ac = away_by_ts.get(ts)

        # Prices: candle values are in cents (0-100), convert to probability (0-1)
        if hc and ac:
            home_bid = hc["yes_bid_close"] / 100.0
            home_ask = hc["yes_ask_close"] / 100.0
            away_bid = ac["yes_bid_close"] / 100.0
            away_ask = ac["yes_ask_close"] / 100.0
        elif hc:
            home_bid = hc["yes_bid_close"] / 100.0
            home_ask = hc["yes_ask_close"] / 100.0
            # Derive away from complementary market
            away_ask = max(0, 1.0 - home_bid)
            away_bid = max(0, 1.0 - home_ask)
        elif ac:
            away_bid = ac["yes_bid_close"] / 100.0
            away_ask = ac["yes_ask_close"] / 100.0
            home_ask = max(0, 1.0 - away_bid)
            home_bid = max(0, 1.0 - away_ask)
        else:
            continue

        # Find score/period/time at this candle timestamp via bisection
        if score_ts_list and ts >= score_ts_list[0]:
            idx = bisect.bisect_right(score_ts_list, ts) - 1
            idx = max(0, min(idx, len(score_timeline) - 1))
            _, home_score, away_score, period, time_str = score_timeline[idx]

            # Check if game is over (after last PBP action and period >= 4)
            if ts > score_ts_list[-1] and period >= 4:
                game_status = "final"
                time_str = "Final"
                home_score = final_home_score
                away_score = final_away_score
            else:
                game_status = "live"
        else:
            # Before first PBP action → pregame
            home_score = 0
            away_score = 0
            period = 0
            time_str = "pregame"
            game_status = "pregame"

        volume = (hc.get("volume", 0) if hc else 0) + (ac.get("volume", 0) if ac else 0)

        frame = {
            "timestamp": ts,
            "home_score": home_score,
            "away_score": away_score,
            "period": period,
            "time_remaining": time_str,
            "game_status": game_status,
            "home_ticker": home_ticker,
            "away_ticker": away_ticker,
            "home_bid": round(home_bid, 2),
            "home_ask": round(home_ask, 2),
            "away_bid": round(away_bid, 2),
            "away_ask": round(away_ask, 2),
            "volume": volume,
        }
        frames.append(frame)

    # Ensure a final frame exists if game completed
    if frames and final_period >= 4:
        last = frames[-1]
        if last["game_status"] != "final":
            final_frame = dict(last)
            final_frame["timestamp"] = last["timestamp"] + 60
            final_frame["game_status"] = "final"
            final_frame["time_remaining"] = "Final"
            final_frame["home_score"] = final_home_score
            final_frame["away_score"] = final_away_score
            final_frame["period"] = final_period
            frames.append(final_frame)

    metadata = {
        "game_id": game_id,
        "home_team": home,
        "away_team": away,
        "home_ticker": home_ticker,
        "away_ticker": away_ticker,
        "date": date_str,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "poll_interval_ms": 60000,  # 1-minute candle resolution
        "total_frames": len(frames),
        "final_home_score": final_home_score if final_period >= 4 else None,
        "final_away_score": final_away_score if final_period >= 4 else None,
        "final_status": "final" if final_period >= 4 else None,
        "synthetic": True,
    }

    return metadata, frames


def main():
    parser = argparse.ArgumentParser(
        description="Build synthetic recordings from candle + PBP data"
    )
    parser.add_argument(
        "--force-refresh", action="store_true", help="Re-fetch all data from NBA API"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse and fetch but don't write files"
    )
    parser.add_argument(
        "--event", type=str, help="Process a single event ticker (for debugging)"
    )
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print("=== Build Synthetic Recordings ===\n")

    # Step 1: Discover events from candle files
    print("Step 1: Discovering events from candle files...")
    events = discover_events(CACHE_DIR)
    print(
        f"  Found {len(events)} events across {len(set(d for d, _, _ in events.values()))} dates\n"
    )

    if args.event:
        if args.event not in events:
            print(f"ERROR: Event {args.event} not found in candle files")
            sys.exit(1)
        events = {args.event: events[args.event]}
        print(f"  Filtered to single event: {args.event}\n")

    # Step 2: Fetch NBA game IDs
    print("Step 2: Fetching NBA game IDs...")
    game_id_map = fetch_game_ids(events, force_refresh=args.force_refresh)
    matched = sum(1 for et in events if game_id_map.get(et))
    print(f"  Matched {matched}/{len(events)} events to NBA games\n")

    # Step 3-6: Fetch PBP, build timelines, merge, and output
    print("Step 3: Fetching play-by-play and building recordings...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(PBP_CACHE_DIR, exist_ok=True)

    created = 0
    skipped = 0
    errors = 0
    pbp_fetches = 0

    for event_ticker in sorted(events.keys()):
        date_str, away, home = events[event_ticker]
        game_id = game_id_map.get(event_ticker)

        if not game_id:
            skipped += 1
            continue

        # Fetch PBP
        cache_path = os.path.join(PBP_CACHE_DIR, f"pbp_{game_id}.pkl")
        need_fetch = not os.path.exists(cache_path) or args.force_refresh
        actions = fetch_pbp(game_id, force_refresh=args.force_refresh)

        if need_fetch:
            pbp_fetches += 1
            if pbp_fetches % 10 == 0:
                print(f"    ({pbp_fetches} PBP fetches so far...)")
            time.sleep(1)  # Rate limit

        if actions is None:
            errors += 1
            continue

        # Build score timeline
        score_timeline = build_score_timeline(actions)

        # Merge candles + scores
        metadata, frames = merge_candles_and_scores(
            event_ticker, away, home, score_timeline, game_id, date_str
        )

        if metadata is None or not frames:
            errors += 1
            continue

        # Write output
        output_path = os.path.join(
            OUTPUT_DIR, f"{away}_vs_{home}_{date_str.replace('-', '')}_synthetic.json"
        )

        if not args.dry_run:
            with open(output_path, "w") as f:
                json.dump({"metadata": metadata, "frames": frames}, f)

        created += 1
        if created % 20 == 0:
            print(f"    {created} recordings created...")

    print("\n=== Summary ===")
    print(f"  Events discovered: {len(events)}")
    print(f"  Recordings created: {created}")
    print(f"  Skipped (no game ID): {skipped}")
    print(f"  Errors: {errors}")
    print(f"  PBP API calls: {pbp_fetches}")
    print(f"  Output directory: {OUTPUT_DIR}/")

    if created > 0 and not args.dry_run:
        print("\nTo backtest on synthetic data:")
        print(
            "  python scripts/backtest_blowout.py --recordings data/recordings/synthetic/*.json"
        )
        print("\nTo backtest on all data:")
        print(
            "  python scripts/backtest_blowout.py --recordings data/recordings/*.json data/recordings/synthetic/*.json"
        )


if __name__ == "__main__":
    main()
