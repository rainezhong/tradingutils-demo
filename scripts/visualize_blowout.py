#!/usr/bin/env python3
"""
Visualize blowout strategy trades and price paths for strategy development.

Produces:
1. Per-trade price path charts (entry → resolution) with score differential overlay
2. Combined grid of all trades (wins vs losses)
3. Stop loss / take profit sensitivity heatmap
4. Score lead vs price scatter at entry and throughout hold

Usage:
    python scripts/visualize_blowout.py
    python scripts/visualize_blowout.py --recordings data/recordings/synthetic/*.json
    python scripts/visualize_blowout.py --save-dir plots/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import argparse
import glob
import json
import subprocess
import re
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from strategies.late_game_blowout_strategy import (
    LateGameBlowoutStrategy,
    BlowoutStrategyConfig,
    BlowoutSide,
)


def load_game(recording_path):
    try:
        with open(recording_path) as f:
            return json.load(f)
    except json.JSONDecodeError:
        return None


def find_blowout_trades(recording_paths, config=None):
    """Find all blowout entry points and extract full Q4+ price paths."""
    if config is None:
        config = BlowoutStrategyConfig()
    strategy = LateGameBlowoutStrategy(config)

    trades = []
    for path in recording_paths:
        data = load_game(path)
        if data is None:
            continue
        frames = data.get("frames", [])
        metadata = data.get("metadata", {})

        if not frames:
            continue
        final = frames[-1]
        if (
            final.get("period", 0) < 4
            or "final" not in str(final.get("game_status", "")).lower()
        ):
            continue

        home_team = metadata.get("home_team", "HOME")
        away_team = metadata.get("away_team", "AWAY")
        final_home = final.get("home_score", 0)
        final_away = final.get("away_score", 0)
        winner = "home" if final_home > final_away else "away"

        # Collect Q4+ frames
        q4_frames = [
            f
            for f in frames
            if f.get("period", 0) >= 4 and f.get("game_status") == "live"
        ]
        if not q4_frames:
            continue

        # Check for entry
        entry_found = False
        for frame in q4_frames:
            signal = strategy.check_entry(
                home_score=frame.get("home_score", 0),
                away_score=frame.get("away_score", 0),
                period=frame.get("period", 4),
                time_remaining=frame.get("time_remaining", "12:00"),
                timestamp=frame.get("timestamp", 0),
                game_id=path,
            )
            if signal and not entry_found:
                leading = "home" if signal.leading_team == BlowoutSide.HOME else "away"
                if leading == "home":
                    entry_price = frame.get("home_bid", 0)
                else:
                    entry_price = frame.get("away_bid", 0)

                if entry_price > config.max_buy_price:
                    continue

                entry_found = True
                entry_ts = frame.get("timestamp", 0)
                entry_lead = signal.score_differential

                # Extract price path from entry onward
                price_path = []  # (seconds_from_entry, leader_bid, score_diff, time_remaining_str)
                for f2 in q4_frames:
                    ts2 = f2.get("timestamp", 0)
                    if ts2 < entry_ts:
                        continue
                    if leading == "home":
                        bid = f2.get("home_bid", 0)
                    else:
                        bid = f2.get("away_bid", 0)
                    hs = f2.get("home_score", 0)
                    as_ = f2.get("away_score", 0)
                    diff = hs - as_ if leading == "home" else as_ - hs
                    price_path.append(
                        (ts2 - entry_ts, bid, diff, f2.get("time_remaining", ""))
                    )

                # Full Q4 price path (before and after entry)
                full_path = []
                for f2 in q4_frames:
                    ts2 = f2.get("timestamp", 0)
                    if leading == "home":
                        bid = f2.get("home_bid", 0)
                    else:
                        bid = f2.get("away_bid", 0)
                    hs = f2.get("home_score", 0)
                    as_ = f2.get("away_score", 0)
                    diff = hs - as_ if leading == "home" else as_ - hs
                    full_path.append(
                        (ts2 - entry_ts, bid, diff, f2.get("time_remaining", ""))
                    )

                won = leading == winner
                trades.append(
                    {
                        "game": f"{away_team}@{home_team}",
                        "leading": leading,
                        "leading_team": home_team if leading == "home" else away_team,
                        "entry_price": entry_price,
                        "entry_lead": entry_lead,
                        "entry_time": signal.time_remaining_seconds,
                        "entry_time_str": frame.get("time_remaining", ""),
                        "won": won,
                        "final_home": final_home,
                        "final_away": final_away,
                        "price_path": price_path,
                        "full_path": full_path,
                        "path": path,
                        "synthetic": metadata.get("synthetic", False),
                    }
                )
                break

    return trades


def plot_trade_grid(trades, save_path=None):
    """Grid of all trade price paths: green=won, red=lost."""
    n = len(trades)
    if n == 0:
        print("No trades to plot")
        return

    cols = min(6, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(4 * cols, 3.5 * rows), squeeze=False)
    fig.suptitle(
        f"Blowout Trades: Price Paths ({n} trades)", fontsize=16, fontweight="bold"
    )

    for i, trade in enumerate(
        sorted(trades, key=lambda t: (not t["won"], -t["entry_lead"]))
    ):
        r, c = divmod(i, cols)
        ax = axes[r][c]

        path = trade["price_path"]
        if not path:
            continue

        secs = [p[0] / 60 for p in path]  # minutes from entry
        bids = [p[1] for p in path]
        diffs = [p[2] for p in path]

        color = "#2ecc71" if trade["won"] else "#e74c3c"
        ax.plot(secs, bids, color=color, linewidth=1.5, alpha=0.9)
        ax.axhline(
            y=trade["entry_price"],
            color="gray",
            linestyle="--",
            alpha=0.5,
            linewidth=0.8,
        )
        ax.axhline(y=1.0, color="#2ecc71", linestyle=":", alpha=0.3)

        # Score diff on secondary axis
        ax2 = ax.twinx()
        ax2.fill_between(secs, diffs, alpha=0.1, color="blue")
        ax2.plot(secs, diffs, color="blue", alpha=0.3, linewidth=0.8)
        ax2.set_ylabel("Lead", fontsize=7, color="blue")
        ax2.tick_params(axis="y", labelsize=6, colors="blue")

        result = "W" if trade["won"] else "L"
        src = " (S)" if trade["synthetic"] else ""
        ax.set_title(
            f"{trade['game']} +{trade['entry_lead']} {trade['entry_time_str']}\n"
            f"Entry: {trade['entry_price']:.0%} → {result}{src}",
            fontsize=8,
            color=color,
        )
        ax.set_xlabel("Min from entry", fontsize=7)
        ax.set_ylabel("Bid price", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.set_ylim(0.4, 1.05)

    # Hide empty subplots
    for i in range(n, rows * cols):
        r, c = divmod(i, cols)
        axes[r][c].set_visible(False)

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"  Saved: {save_path}")
    else:
        plt.show()
    plt.close(fig)


def plot_stop_take_heatmap(recording_args, save_path=None):
    """Run grid of stop loss × take profit and show P&L heatmap."""
    stops = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]
    takes = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]

    pnl_grid = np.zeros((len(stops), len(takes)))
    wr_grid = np.zeros((len(stops), len(takes)))

    total = len(stops) * len(takes)
    done = 0
    print(f"  Running {total} backtest combinations...")

    for si, stop in enumerate(stops):
        for ti, take in enumerate(takes):
            cmd = f"python3 scripts/backtest_blowout.py --recordings {recording_args} --price-stop-loss {stop} --take-profit {take}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            out = result.stdout

            pnl_match = re.search(r"Total P&L:\s+\$([-\d.]+)", out)
            wr_match = re.search(r"Win Rate:\s+([\d.]+)%", out)

            pnl_grid[si][ti] = float(pnl_match.group(1)) if pnl_match else 0
            wr_grid[si][ti] = float(wr_match.group(1)) if wr_match else 0
            done += 1
            if done % 20 == 0:
                print(f"    {done}/{total}...")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7))
    fig.suptitle("Stop Loss × Take Profit Grid Search", fontsize=16, fontweight="bold")

    stop_labels = ["off" if s == 0 else f"{s}c" for s in stops]
    take_labels = ["off" if t == 0 else f"{t}c" for t in takes]

    # P&L heatmap
    vmax = max(abs(pnl_grid.min()), abs(pnl_grid.max()))
    im1 = ax1.imshow(pnl_grid, cmap="RdYlGn", aspect="auto", vmin=-vmax, vmax=vmax)
    ax1.set_xticks(range(len(takes)))
    ax1.set_xticklabels(take_labels, fontsize=8)
    ax1.set_yticks(range(len(stops)))
    ax1.set_yticklabels(stop_labels, fontsize=8)
    ax1.set_xlabel("Take Profit")
    ax1.set_ylabel("Stop Loss")
    ax1.set_title("Total P&L ($)")
    for si in range(len(stops)):
        for ti in range(len(takes)):
            ax1.text(
                ti,
                si,
                f"${pnl_grid[si][ti]:.0f}",
                ha="center",
                va="center",
                fontsize=6,
                color="black" if abs(pnl_grid[si][ti]) < vmax * 0.6 else "white",
            )
    plt.colorbar(im1, ax=ax1, shrink=0.8)

    # Win rate heatmap
    im2 = ax2.imshow(wr_grid, cmap="RdYlGn", aspect="auto", vmin=0, vmax=100)
    ax2.set_xticks(range(len(takes)))
    ax2.set_xticklabels(take_labels, fontsize=8)
    ax2.set_yticks(range(len(stops)))
    ax2.set_yticklabels(stop_labels, fontsize=8)
    ax2.set_xlabel("Take Profit")
    ax2.set_ylabel("Stop Loss")
    ax2.set_title("Win Rate (%)")
    for si in range(len(stops)):
        for ti in range(len(takes)):
            ax2.text(
                ti,
                si,
                f"{wr_grid[si][ti]:.0f}%",
                ha="center",
                va="center",
                fontsize=6,
                color="black" if wr_grid[si][ti] > 20 else "white",
            )
    plt.colorbar(im2, ax=ax2, shrink=0.8)

    # Mark best P&L cell
    best_idx = np.unravel_index(np.argmax(pnl_grid), pnl_grid.shape)
    ax1.add_patch(
        plt.Rectangle(
            (best_idx[1] - 0.5, best_idx[0] - 0.5),
            1,
            1,
            fill=False,
            edgecolor="blue",
            linewidth=3,
        )
    )

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"  Saved: {save_path}")
    else:
        plt.show()
    plt.close(fig)


def plot_entry_analysis(trades, save_path=None):
    """Analyze entry conditions: lead size, price, time remaining vs outcome."""
    if not trades:
        return

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Entry Condition Analysis", fontsize=16, fontweight="bold")

    wins = [t for t in trades if t["won"]]
    losses = [t for t in trades if not t["won"]]

    # 1. Lead vs Entry Price (scatter)
    ax = axes[0][0]
    for t in wins:
        ax.scatter(
            t["entry_lead"],
            t["entry_price"],
            c="#2ecc71",
            s=60,
            alpha=0.7,
            edgecolors="black",
            linewidths=0.5,
            zorder=3,
        )
    for t in losses:
        ax.scatter(
            t["entry_lead"],
            t["entry_price"],
            c="#e74c3c",
            s=60,
            alpha=0.7,
            edgecolors="black",
            linewidths=0.5,
            marker="X",
            zorder=3,
        )
    ax.set_xlabel("Score Lead at Entry")
    ax.set_ylabel("Entry Price")
    ax.set_title("Lead vs Entry Price")
    ax.legend(["Win", "Loss"], loc="lower right")
    ax.grid(True, alpha=0.3)

    # 2. Time remaining vs Entry Price
    ax = axes[0][1]
    for t in wins:
        ax.scatter(
            t["entry_time"] / 60,
            t["entry_price"],
            c="#2ecc71",
            s=60,
            alpha=0.7,
            edgecolors="black",
            linewidths=0.5,
            zorder=3,
        )
    for t in losses:
        ax.scatter(
            t["entry_time"] / 60,
            t["entry_price"],
            c="#e74c3c",
            s=60,
            alpha=0.7,
            edgecolors="black",
            linewidths=0.5,
            marker="X",
            zorder=3,
        )
    ax.set_xlabel("Minutes Remaining at Entry")
    ax.set_ylabel("Entry Price")
    ax.set_title("Time Remaining vs Entry Price")
    ax.grid(True, alpha=0.3)

    # 3. Max drawdown from entry for each trade
    ax = axes[1][0]
    drawdowns = []
    for t in trades:
        path = t["price_path"]
        if not path:
            continue
        min_bid = min(p[1] for p in path)
        dd = t["entry_price"] - min_bid
        drawdowns.append((dd, t["won"], t["game"]))

    drawdowns.sort(key=lambda x: x[0])
    colors = ["#2ecc71" if d[1] else "#e74c3c" for d in drawdowns]
    ax.bar(
        range(len(drawdowns)), [d[0] * 100 for d in drawdowns], color=colors, alpha=0.7
    )
    ax.set_ylabel("Max Drawdown (cents)")
    ax.set_title("Max Drawdown per Trade (green=win, red=loss)")
    ax.set_xticks(range(len(drawdowns)))
    ax.set_xticklabels(
        [d[2].split("@")[1][:3] for d in drawdowns], rotation=45, fontsize=6
    )
    ax.grid(True, alpha=0.3, axis="y")

    # Stop loss lines
    for sl in [5, 10, 15, 20]:
        ax.axhline(y=sl, color="orange", linestyle="--", alpha=0.4, linewidth=0.8)
        ax.text(len(drawdowns) - 0.5, sl + 0.3, f"{sl}c", fontsize=7, color="orange")

    # 4. Score lead trajectory (min lead during hold)
    ax = axes[1][1]
    min_leads = []
    for t in trades:
        path = t["price_path"]
        if not path:
            continue
        min_lead = min(p[2] for p in path)
        min_leads.append((t["entry_lead"], min_lead, t["won"], t["game"]))

    for ml in min_leads:
        color = "#2ecc71" if ml[2] else "#e74c3c"
        marker = "o" if ml[2] else "X"
        ax.scatter(
            ml[0],
            ml[1],
            c=color,
            s=60,
            alpha=0.7,
            edgecolors="black",
            linewidths=0.5,
            marker=marker,
            zorder=3,
        )

    ax.axhline(y=0, color="red", linestyle="-", alpha=0.5, linewidth=1)
    ax.set_xlabel("Lead at Entry")
    ax.set_ylabel("Min Lead During Hold")
    ax.set_title("Lead Erosion: Entry Lead vs Min Lead")
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"  Saved: {save_path}")
    else:
        plt.show()
    plt.close(fig)


def plot_price_drawdown_over_time(trades, save_path=None):
    """Overlay all trades' price paths normalized to entry, showing drawdown patterns."""
    if not trades:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    fig.suptitle("Normalized Price Paths from Entry", fontsize=16, fontweight="bold")

    for t in trades:
        path = t["price_path"]
        if not path:
            continue
        secs = [p[0] / 60 for p in path]
        # Price change from entry in cents
        deltas = [(p[1] - t["entry_price"]) * 100 for p in path]
        color = "#2ecc71" if t["won"] else "#e74c3c"
        alpha = 0.6

        ax1.plot(secs, deltas, color=color, alpha=alpha, linewidth=1)

    ax1.axhline(y=0, color="black", linewidth=1)
    ax1.axhline(y=-5, color="orange", linestyle="--", alpha=0.5, label="5c stop")
    ax1.axhline(y=-10, color="red", linestyle="--", alpha=0.5, label="10c stop")
    ax1.set_xlabel("Minutes from Entry")
    ax1.set_ylabel("Price Change from Entry (cents)")
    ax1.set_title("All Trades Overlaid")
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Right panel: score lead paths
    for t in trades:
        path = t["price_path"]
        if not path:
            continue
        secs = [p[0] / 60 for p in path]
        leads = [p[2] for p in path]
        color = "#2ecc71" if t["won"] else "#e74c3c"
        ax2.plot(secs, leads, color=color, alpha=0.6, linewidth=1)

    ax2.axhline(y=0, color="red", linewidth=1.5, label="Tied")
    ax2.set_xlabel("Minutes from Entry")
    ax2.set_ylabel("Score Lead")
    ax2.set_title("Score Lead from Entry")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"  Saved: {save_path}")
    else:
        plt.show()
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Visualize blowout strategy trades")
    parser.add_argument("--recordings", "-r", type=str, nargs="+", default=None)
    parser.add_argument("--save-dir", type=str, default="plots")
    parser.add_argument(
        "--no-heatmap", action="store_true", help="Skip slow heatmap generation"
    )
    args = parser.parse_args()

    import os

    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    if args.recordings:
        recording_paths = []
        for pattern in args.recordings:
            recording_paths.extend(glob.glob(pattern))
    else:
        recording_paths = glob.glob("data/recordings/*.json") + glob.glob(
            "data/recordings/synthetic/*.json"
        )

    print(f"Loaded {len(recording_paths)} recordings")

    os.makedirs(args.save_dir, exist_ok=True)

    # Find trades
    print("Finding blowout trades...")
    trades = find_blowout_trades(recording_paths)
    print(
        f"  Found {len(trades)} trades ({sum(1 for t in trades if t['won'])} wins, {sum(1 for t in trades if not t['won'])} losses)"
    )

    # Plot 1: Trade grid
    print("\nPlotting trade price paths...")
    plot_trade_grid(
        trades, save_path=os.path.join(args.save_dir, "blowout_trade_grid.png")
    )

    # Plot 2: Entry analysis
    print("Plotting entry analysis...")
    plot_entry_analysis(
        trades, save_path=os.path.join(args.save_dir, "blowout_entry_analysis.png")
    )

    # Plot 3: Normalized price paths
    print("Plotting normalized price paths...")
    plot_price_drawdown_over_time(
        trades, save_path=os.path.join(args.save_dir, "blowout_price_paths.png")
    )

    # Plot 4: Stop/Take heatmap (slow)
    if not args.no_heatmap:
        print(
            "\nGenerating stop loss × take profit heatmap (this takes a few minutes)..."
        )
        rec_arg = (
            " ".join(args.recordings)
            if args.recordings
            else "data/recordings/*.json data/recordings/synthetic/*.json"
        )
        plot_stop_take_heatmap(
            rec_arg, save_path=os.path.join(args.save_dir, "blowout_heatmap.png")
        )

    print(f"\nAll plots saved to {args.save_dir}/")


if __name__ == "__main__":
    main()
