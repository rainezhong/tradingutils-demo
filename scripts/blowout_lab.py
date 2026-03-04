#!/usr/bin/env python3
"""
Blowout Strategy Lab — tweak parameters and instantly see results.

HOW TO USE:
  1. Edit the PARAMS section below (lines 25-55)
  2. Run: python3 scripts/blowout_lab.py
  3. See results, tweak, repeat

Everything you'd want to change is in one place. Just change the numbers.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json
import glob
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.kalshi.fees import calculate_fee

# ============================================================================
#  PARAMS — EDIT THESE AND RE-RUN
# ============================================================================

# Entry filters
MIN_LEAD = 12  # minimum point lead to enter (try 12, 15, 18, 20)
MAX_TIME_MIN = 10  # max minutes remaining to enter (try 10, 8, 6, 5)
MIN_TIME_MIN = 2  # min minutes remaining to enter (try 0, 2, 3)
MAX_BUY_PRICE = 0.92  # max entry price (try 0.92, 0.88, 0.85, 0.80)
MIN_BUY_PRICE = 0.00  # min entry price (try 0.00, 0.70, 0.80)
MIN_PERIOD = 4  # minimum period (4=Q4 only, 3=Q3+, 5=OT only)

# Exit rules (set to 0 to disable)
PRICE_STOP = 0  # exit if bid drops this many CENTS below entry (try 0, 5, 10, 15, 20)
TAKE_PROFIT = 0  # exit if bid rises this many CENTS above entry (try 0, 3, 5, 8)
TRAILING_STOP = 0  # trail this many CENTS below high water mark (try 0, 3, 5, 8)

# Score-based exit (set to 0 to disable)
EXIT_IF_LEAD_BELOW = 0  # exit if score lead drops below this (try 0, 3, 5, 8)

# Position sizing
POSITION_SIZE = 5.00  # dollars per trade
USE_KELLY = True  # if True, scale size by edge (experimental)

# Data
USE_REAL = True  # include data/recordings/*.json
USE_SYNTHETIC = True  # include data/recordings/synthetic/*.json

# ============================================================================
#  END PARAMS — everything below runs automatically
# ============================================================================


def parse_time(time_str):
    """Parse 'Q4 8:30' → seconds remaining."""
    try:
        time_str = time_str.split()[-1]
        parts = time_str.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(float(parts[1]))
        elif len(parts) == 1:
            return int(float(parts[0]))
    except Exception:
        pass
    return 0


def run():
    # Load recordings
    paths = []
    if USE_REAL:
        paths += glob.glob("data/recordings/*.json")
    if USE_SYNTHETIC:
        paths += glob.glob("data/recordings/synthetic/*.json")

    trades = []
    games_scanned = 0
    games_complete = 0

    for path in sorted(paths):
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception:
            continue

        frames = data.get("frames", [])
        metadata = data.get("metadata", {})
        if not frames:
            continue

        final = frames[-1]
        games_scanned += 1
        if (
            final.get("period", 0) < 4
            or "final" not in str(final.get("game_status", "")).lower()
        ):
            continue
        games_complete += 1

        home = metadata.get("home_team", "???")
        away = metadata.get("away_team", "???")
        final_home = final.get("home_score", 0)
        final_away = final.get("away_score", 0)
        winner = "home" if final_home > final_away else "away"

        entered = False
        trade = None

        for frame in frames:
            period = frame.get("period", 0)
            if period < MIN_PERIOD:
                continue
            status = frame.get("game_status", "")
            if status == "final":
                continue

            hs = frame.get("home_score", 0)
            as_ = frame.get("away_score", 0)
            time_str = frame.get("time_remaining", "12:00")
            frame.get("timestamp", 0)
            secs = parse_time(time_str)

            home_bid = frame.get("home_bid", 0.05)
            frame.get("home_ask", 0.95)
            away_bid = frame.get("away_bid", 0.05)
            frame.get("away_ask", 0.95)

            # ---- Check exits on active trade ----
            if trade and not trade.get("exited"):
                if trade["side"] == "home":
                    cur_bid = home_bid
                    cur_lead = hs - as_
                else:
                    cur_bid = away_bid
                    cur_lead = as_ - hs

                trade["high"] = max(trade["high"], cur_bid)
                trade["low"] = min(trade["low"], cur_bid)
                trade["min_lead"] = min(trade["min_lead"], cur_lead)

                exit_reason = None

                # Take profit
                if (
                    TAKE_PROFIT > 0
                    and cur_bid >= trade["entry_price"] + TAKE_PROFIT / 100
                ):
                    exit_reason = "take_profit"

                # Trailing stop
                if not exit_reason and TRAILING_STOP > 0:
                    if trade["high"] >= trade["entry_price"] + TRAILING_STOP / 100:
                        if cur_bid <= trade["high"] - TRAILING_STOP / 100:
                            exit_reason = "trailing_stop"

                # Price stop
                if not exit_reason and PRICE_STOP > 0:
                    if cur_bid <= trade["entry_price"] - PRICE_STOP / 100:
                        exit_reason = "price_stop"

                # Score-based exit
                if not exit_reason and EXIT_IF_LEAD_BELOW > 0:
                    if cur_lead < EXIT_IF_LEAD_BELOW:
                        exit_reason = "lead_eroded"

                if exit_reason:
                    trade["exited"] = True
                    trade["exit_price"] = cur_bid
                    trade["exit_reason"] = exit_reason
                    trade["exit_time"] = time_str
                    exit_fee = calculate_fee(cur_bid, trade["contracts"], maker=True)
                    trade["exit_fee"] = exit_fee
                    trade["pnl"] = (
                        (cur_bid - trade["entry_price"]) * trade["contracts"]
                        - trade["entry_fee"]
                        - exit_fee
                    )

            # ---- Check entry ----
            if entered:
                continue

            lead_home = hs - as_
            lead_away = as_ - hs
            lead = max(lead_home, lead_away)
            leading = "home" if lead_home >= lead_away else "away"

            if lead < MIN_LEAD:
                continue
            if secs > MAX_TIME_MIN * 60:
                continue
            if secs < MIN_TIME_MIN * 60:
                continue

            entry_price = home_bid if leading == "home" else away_bid
            if entry_price > MAX_BUY_PRICE or entry_price < MIN_BUY_PRICE:
                continue
            if entry_price <= 0:
                continue

            entered = True
            contracts = int(POSITION_SIZE / entry_price)
            if contracts <= 0:
                continue
            entry_fee = calculate_fee(entry_price, contracts, maker=True)

            trade = {
                "game": f"{away}@{home}",
                "side": leading,
                "team": home if leading == "home" else away,
                "entry_price": entry_price,
                "entry_lead": lead,
                "entry_time": time_str,
                "entry_secs": secs,
                "contracts": contracts,
                "entry_fee": entry_fee,
                "high": entry_price,
                "low": entry_price,
                "min_lead": lead,
                "winner": winner,
                "final": f"{final_away}-{final_home}",
                "exited": False,
                "exit_reason": None,
                "exit_price": None,
                "exit_fee": 0,
                "pnl": None,
                "synthetic": metadata.get("synthetic", False),
            }
            trades.append(trade)

        # Resolve unstopped trades at game end
        if trade and not trade.get("exited"):
            trade["exit_reason"] = "resolution"
            if trade["side"] == winner:
                trade["pnl"] = (1.0 - trade["entry_price"]) * trade[
                    "contracts"
                ] - trade["entry_fee"]
                trade["result"] = "win"
            else:
                trade["pnl"] = (
                    -trade["entry_price"] * trade["contracts"] - trade["entry_fee"]
                )
                trade["result"] = "loss"

    # Classify stopped trades
    for t in trades:
        if t["pnl"] is None:
            t["pnl"] = 0
        if "result" not in t:
            if t["pnl"] > 0:
                t["result"] = "win"
            elif t["pnl"] < 0:
                t["result"] = "loss"
            else:
                t["result"] = "flat"

    # ---- PRINT RESULTS ----
    print()
    print("=" * 72)
    print("  BLOWOUT LAB RESULTS")
    print("=" * 72)

    # Show active params
    print()
    print("  Parameters:")
    print(
        f"    Lead >= {MIN_LEAD}pts | Time: {MIN_TIME_MIN}-{MAX_TIME_MIN} min | Price: {MIN_BUY_PRICE:.0%}-{MAX_BUY_PRICE:.0%} | Period >= Q{MIN_PERIOD}"
    )
    stops_desc = []
    if PRICE_STOP:
        stops_desc.append(f"stop={PRICE_STOP}c")
    if TAKE_PROFIT:
        stops_desc.append(f"TP={TAKE_PROFIT}c")
    if TRAILING_STOP:
        stops_desc.append(f"trail={TRAILING_STOP}c")
    if EXIT_IF_LEAD_BELOW:
        stops_desc.append(f"exit_lead<{EXIT_IF_LEAD_BELOW}")
    print(f"    Exits: {', '.join(stops_desc) if stops_desc else 'hold to resolution'}")
    print(f"    Position: ${POSITION_SIZE:.2f} per trade")
    print()

    # Summary
    n = len(trades)
    wins = [t for t in trades if t["result"] == "win"]
    losses = [t for t in trades if t["result"] == "loss"]
    total_pnl = sum(t["pnl"] for t in trades)
    total_fees = sum(t["entry_fee"] + t.get("exit_fee", 0) for t in trades)

    print(f"  Games: {games_complete} complete / {games_scanned} total")
    print(
        f"  Trades: {n}   Wins: {len(wins)}   Losses: {len(losses)}   Win Rate: {len(wins) / n * 100:.1f}%"
        if n
        else "  No trades found!"
    )
    if n:
        print(f"  Total P&L: ${total_pnl:+.2f}   Fees: ${total_fees:.2f}")
        if wins:
            print(
                f"  Avg Win: ${sum(t['pnl'] for t in wins) / len(wins):.2f}   ", end=""
            )
        if losses:
            print(f"  Avg Loss: ${sum(t['pnl'] for t in losses) / len(losses):.2f}")
        else:
            print()

        # Exit breakdown
        from collections import Counter

        exits = Counter(t["exit_reason"] for t in trades)
        print(f"  Exits: {dict(exits)}")

    # Trade table
    if n:
        print()
        print(
            f"  {'Game':<16} {'Lead':>5} {'Time':>8} {'Entry':>6} {'Ct':>3} {'Exit':>14} {'P&L':>8} {'Score':>9}"
        )
        print("  " + "-" * 70)
        for t in trades:
            marker = "S" if t["synthetic"] else " "
            exit_str = t["exit_reason"] or "???"
            print(
                f" {marker}{t['game']:<15} +{t['entry_lead']:<4} {t['entry_time']:>8} {t['entry_price']:>5.0%}"
                f"  {t['contracts']:>3} {exit_str:>14} ${t['pnl']:>+7.2f} {t['final']:>9}"
            )

    # Quick what-if hints
    if n:
        print()
        print("  --- Quick insights ---")
        sorted(set(t["entry_lead"] for t in trades))
        for threshold in [12, 15, 18, 20]:
            subset = [t for t in trades if t["entry_lead"] >= threshold]
            if subset and len(subset) != n:
                sw = sum(1 for t in subset if t["result"] == "win")
                sp = sum(t["pnl"] for t in subset)
                print(
                    f"    If MIN_LEAD={threshold}: {len(subset)} trades, {sw}/{len(subset)} wins ({sw / len(subset) * 100:.0f}%), P&L ${sp:+.2f}"
                )

        for max_t in [8, 6, 5, 3]:
            subset = [t for t in trades if t["entry_secs"] <= max_t * 60]
            if subset and len(subset) != n:
                sw = sum(1 for t in subset if t["result"] == "win")
                sp = sum(t["pnl"] for t in subset)
                print(
                    f"    If MAX_TIME={max_t}min: {len(subset)} trades, {sw}/{len(subset)} wins ({sw / len(subset) * 100:.0f}%), P&L ${sp:+.2f}"
                )

        for mp in [0.90, 0.88, 0.85, 0.80]:
            subset = [t for t in trades if t["entry_price"] <= mp]
            if subset and len(subset) != n:
                sw = sum(1 for t in subset if t["result"] == "win")
                sp = sum(t["pnl"] for t in subset)
                print(
                    f"    If MAX_PRICE={mp:.0%}: {len(subset)} trades, {sw}/{len(subset)} wins ({sw / len(subset) * 100:.0f}%), P&L ${sp:+.2f}"
                )

    print()
    print("=" * 72)
    print("  Edit PARAMS at top of scripts/blowout_lab.py and re-run!")
    print("=" * 72)
    print()


if __name__ == "__main__":
    run()
