#!/usr/bin/env python3
"""
Stream live Markov model probability for the Super Bowl.

Polls ESPN for the live score, feeds it into MarkovWinModel (NFL),
and prints the calibrated probability every few seconds.

Usage:
    python scripts/stream_sb_probability.py
    python scripts/stream_sb_probability.py --interval 10
"""

import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_extraction.models.markov_win_model import (
    GameState,
    MarkovWinModel,
    SportType,
)

# Suppress the MarkovWinModel init print
import io

_stdout = sys.stdout
sys.stdout = io.StringIO()
model = MarkovWinModel(SportType.NFL)
sys.stdout = _stdout

# Calibration: shrink toward 50% (same as backtest optimal)
SHRINK_FACTOR = 0.50


def calibrate(raw_prob: float) -> float:
    return raw_prob * SHRINK_FACTOR + 0.5 * (1 - SHRINK_FACTOR)


def fetch_espn_scoreboard():
    """Fetch NFL scoreboard from ESPN API."""
    url = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def parse_nfl_game(event: dict) -> dict:
    """Parse an ESPN event into game state."""
    competition = event.get("competitions", [{}])[0]
    competitors = competition.get("competitors", [])

    home = away = None
    for c in competitors:
        if c.get("homeAway") == "home":
            home = c
        else:
            away = c

    if not home or not away:
        return None

    status = competition.get("status", {})
    state = status.get("type", {}).get("state", "pre")  # pre, in, post
    period = status.get("period", 0)
    clock = status.get("displayClock", "15:00")

    return {
        "home_team": home.get("team", {}).get("abbreviation", "?"),
        "away_team": away.get("team", {}).get("abbreviation", "?"),
        "home_score": int(home.get("score", 0)),
        "away_score": int(away.get("score", 0)),
        "period": period,
        "clock": clock,
        "state": state,
        "name": event.get("name", ""),
    }


def clock_to_seconds(clock_str: str) -> float:
    """Parse '4:21' to seconds."""
    try:
        parts = clock_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return float(clock_str)
    except (ValueError, AttributeError):
        return 0.0


def nfl_time_remaining(period: int, clock_str: str) -> float:
    """Total game seconds remaining (4 x 15min quarters)."""
    period_secs = clock_to_seconds(clock_str)
    remaining_periods = max(0, 4 - period)
    return period_secs + remaining_periods * 15 * 60


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Stream Super Bowl Markov probability")
    parser.add_argument(
        "--interval", type=int, default=5, help="Poll interval seconds (default: 5)"
    )
    parser.add_argument(
        "--shrink", type=float, default=0.50, help="Shrink factor (default: 0.50)"
    )
    args = parser.parse_args()

    global SHRINK_FACTOR
    SHRINK_FACTOR = args.shrink

    print("=" * 70)
    print("  SUPER BOWL - LIVE MARKOV MODEL PROBABILITY")
    print(f"  Model: MarkovWinModel (NFL) | Calibration: shrink={SHRINK_FACTOR}")
    print(f"  Polling ESPN every {args.interval}s")
    print("=" * 70)
    print()

    prev_score = None

    while True:
        try:
            data = fetch_espn_scoreboard()
            events = data.get("events", [])

            # Find the championship / Super Bowl game
            sb_game = None
            for event in events:
                name = event.get("name", "").lower()
                event.get("season", {}).get("type", 0)
                # Super Bowl is typically season type 3 (postseason)
                # Also match by team names
                parsed = parse_nfl_game(event)
                if parsed:
                    teams = f"{parsed['away_team']}@{parsed['home_team']}".upper()
                    if (
                        "SEA" in teams
                        or "NE" in teams
                        or "championship" in name
                        or "super bowl" in name
                    ):
                        sb_game = parsed
                        break

            if not sb_game:
                # Show all games found
                game_names = [e.get("name", "?")[:40] for e in events[:5]]
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] No Super Bowl game found. Games: {game_names}")
                time.sleep(args.interval)
                continue

            state_str = sb_game["state"]
            home = sb_game["home_team"]
            away = sb_game["away_team"]
            hs = sb_game["home_score"]
            as_ = sb_game["away_score"]
            period = sb_game["period"]
            clock = sb_game["clock"]

            if state_str == "pre":
                ts = time.strftime("%H:%M:%S")
                print(f"[{ts}] {away} @ {home} — PREGAME (kickoff pending)")
                time.sleep(args.interval)
                continue

            time_rem = nfl_time_remaining(period, clock)

            game_state = GameState(
                score_diff=hs - as_,  # home - away
                time_remaining=time_rem,
                period=period,
                home_possession=True,  # ESPN doesn't easily expose this
                momentum=0.0,
            )

            raw_prob = model.get_win_probability(game_state)
            cal_prob = calibrate(raw_prob)

            # Determine which team is "home" for Kalshi mapping
            # SEA is home team in Super Bowl LX
            sea_prob = cal_prob if home == "SEA" else 1 - cal_prob
            ne_prob = 1 - sea_prob

            sea_cents = int(round(sea_prob * 100))
            ne_cents = int(round(ne_prob * 100))

            ts = time.strftime("%H:%M:%S")
            score_changed = prev_score != (hs, as_)
            marker = " ***" if score_changed else ""

            # Quarter display
            if period <= 4:
                q_str = f"Q{period}"
            else:
                q_str = f"OT{period - 4}"

            bar_len = 40
            sea_bar = int(sea_prob * bar_len)
            ne_bar = bar_len - sea_bar
            bar = f"{'█' * sea_bar}{'░' * ne_bar}"

            print(
                f"[{ts}] {q_str} {clock}  "
                f"{away} {as_} - {hs} {home}  │  "
                f"SEA {sea_cents}c  [{bar}]  NE {ne_cents}c  "
                f"(raw={raw_prob:.3f}){marker}"
            )

            prev_score = (hs, as_)

            if state_str == "post":
                print()
                winner = home if hs > as_ else away
                print(f"  FINAL: {winner} wins! {away} {as_} - {hs} {home}")
                break

        except Exception as e:
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] Error: {e}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
