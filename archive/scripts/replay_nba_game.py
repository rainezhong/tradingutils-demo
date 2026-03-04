#!/usr/bin/env python3
"""
Replay a recorded NBA game with dashboard visualization.

Usage:
    # Replay at 10x speed with dashboard
    python scripts/replay_nba_game.py data/recordings/LAL_vs_BOS_2025-01-28.json --speed 10

    # Replay at real-time speed (1x)
    python scripts/replay_nba_game.py data/recordings/LAL_vs_BOS.json

    # Replay at 60x speed (1 minute of game = 1 second of real time)
    python scripts/replay_nba_game.py data/recordings/LAL_vs_BOS.json --speed 60

    # Replay without running the strategy (just watch scores)
    python scripts/replay_nba_game.py data/recordings/LAL_vs_BOS.json --no-strategy

    # Skip to a specific period
    python scripts/replay_nba_game.py data/recordings/LAL_vs_BOS.json --start-period 3

    # Run with custom strategy config
    python scripts/replay_nba_game.py data/recordings/LAL_vs_BOS.json --min-edge 5

Dashboard will be available at: http://localhost:8080
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.simulation.nba_recorder import NBAGameRecorder
from src.simulation.nba_replay import NBAGameReplay, MockScoreFeed
from src.kalshi.mock_client import MockKalshiClient
from dashboard.state import state_aggregator
from signal_extraction.data_feeds.score_feed import ScoreAnalyzer


async def run_dashboard_server(host: str = "0.0.0.0", port: int = 8080):
    """Start the dashboard web server.

    This is a simplified version - for full dashboard, use the existing
    dashboard server infrastructure.
    """
    try:
        from aiohttp import web

        async def handle_index(request):
            return web.Response(
                text="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>NBA Game Replay</title>
                    <style>
                        body { font-family: monospace; background: #1a1a2e; color: #eee; padding: 20px; }
                        .game-box { background: #16213e; padding: 20px; border-radius: 10px; margin: 10px 0; }
                        .score { font-size: 48px; font-weight: bold; }
                        .edge { color: #4ecca3; }
                        .signal { color: #ff6b6b; font-weight: bold; }
                        #log { background: #0f0f23; padding: 10px; height: 300px; overflow-y: auto; font-size: 12px; }
                    </style>
                </head>
                <body>
                    <h1>NBA Game Replay Dashboard</h1>
                    <div id="game-state" class="game-box">Loading...</div>
                    <h2>Activity Log</h2>
                    <div id="log"></div>
                    <script>
                        const ws = new WebSocket(`ws://${window.location.host}/ws`);
                        ws.onmessage = (event) => {
                            const msg = JSON.parse(event.data);
                            if (msg.type === 'nba_state') {
                                const d = msg.data;
                                document.getElementById('game-state').innerHTML = `
                                    <div class="score">${d.away_team} ${d.away_score} - ${d.home_score} ${d.home_team}</div>
                                    <div>Q${d.period} ${d.time_remaining}</div>
                                    <div>Win Prob: ${(d.home_win_prob * 100).toFixed(1)}% | Market: ${(d.market_price * 100).toFixed(1)}%</div>
                                    <div class="edge">Edge: ${d.edge_cents.toFixed(1)}¢</div>
                                    <div class="signal">${d.last_signal || ''}</div>
                                `;
                            } else if (msg.type === 'activity') {
                                const log = document.getElementById('log');
                                log.innerHTML = `<div>[${msg.data.timestamp}] ${msg.data.message}</div>` + log.innerHTML;
                            }
                        };
                    </script>
                </body>
                </html>
                """,
                content_type="text/html",
            )

        async def handle_ws(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)

            queue = state_aggregator.subscribe()
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=1.0)
                        await ws.send_json(msg)
                    except asyncio.TimeoutError:
                        if ws.closed:
                            break
                        continue
            finally:
                state_aggregator.unsubscribe(queue)

            return ws

        app = web.Application()
        app.router.add_get("/", handle_index)
        app.router.add_get("/ws", handle_ws)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        print(f"Dashboard running at http://{host}:{port}")
        return runner

    except ImportError:
        print("aiohttp not installed - dashboard disabled")
        print("Install with: pip install aiohttp")
        return None


async def main():
    parser = argparse.ArgumentParser(
        description="Replay a recorded NBA game with dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "recording",
        type=str,
        help="Path to the recording file (.json)",
    )

    parser.add_argument(
        "--speed",
        "-s",
        type=float,
        default=10.0,
        help="Replay speed multiplier (default: 10.0)",
    )

    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=8080,
        help="Dashboard port (default: 8080)",
    )

    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Run without dashboard server",
    )

    parser.add_argument(
        "--no-strategy",
        action="store_true",
        help="Don't run strategy - just show game progression",
    )

    parser.add_argument(
        "--start-period",
        type=int,
        help="Skip to start of specified period (1-4, 5+ for OT)",
    )

    parser.add_argument(
        "--start-frame",
        type=int,
        help="Start at a specific frame number",
    )

    parser.add_argument(
        "--min-edge",
        type=float,
        default=3.0,
        help="Minimum edge in cents to generate signal (default: 3.0)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    # Load recording
    recording_path = Path(args.recording)
    if not recording_path.exists():
        print(f"Error: Recording file not found: {recording_path}")
        return

    print(f"\n{'=' * 60}")
    print("NBA Game Replay")
    print(f"{'=' * 60}")

    recording = NBAGameRecorder.load(str(recording_path))

    print(f"Speed:        {args.speed}x")
    print(f"Min Edge:     {args.min_edge}¢")
    print(f"{'=' * 60}\n")

    # Create replay
    replay = NBAGameReplay(recording, speed=args.speed)

    # Skip to start position if specified
    if args.start_period:
        frame = replay.skip_to_period(args.start_period)
        if frame:
            print(f"Skipping to period {args.start_period}")
        else:
            print(f"Warning: Period {args.start_period} not found in recording")

    if args.start_frame:
        frame = replay.skip_to_frame(args.start_frame)
        if frame:
            print(f"Skipping to frame {args.start_frame}")

    # Create mock client
    mock_client = MockKalshiClient(initial_balance=100000)

    # Create mock score feed
    MockScoreFeed(replay)

    # Create analyzer
    analyzer = ScoreAnalyzer()

    # Start dashboard if requested
    dashboard_runner = None
    if not args.no_dashboard:
        dashboard_runner = await run_dashboard_server(port=args.port)

    # Track state for strategy
    position = 0

    # Frame callback for verbose output
    frame_count = 0
    last_print_time = 0.0

    def on_frame(frame):
        nonlocal frame_count, last_print_time, position

        frame_count += 1

        # Calculate win probability
        time_remaining_seconds = analyzer.parse_time_remaining(frame.time_remaining)
        score_diff = frame.home_score - frame.away_score
        home_win_prob = analyzer.calculate_win_probability(
            score_diff,
            frame.period,
            time_remaining_seconds,
        )

        # Calculate market mid
        market_mid = (frame.home_bid + frame.home_ask) / 2

        # Calculate edge
        edge_cents = abs(home_win_prob - market_mid) * 100

        # Determine signal
        should_trade = frame.period <= 2  # First half only by default
        last_signal = None

        if not args.no_strategy and should_trade and edge_cents >= args.min_edge:
            if home_win_prob > market_mid:
                last_signal = "BUY YES (home)"
            else:
                last_signal = "BUY NO (away)"

        # Publish to dashboard
        state_aggregator.publish_nba_state(
            game_id=recording.game_id,
            home_team=recording.home_team,
            away_team=recording.away_team,
            home_score=frame.home_score,
            away_score=frame.away_score,
            period=frame.period,
            time_remaining=frame.time_remaining,
            home_win_prob=home_win_prob,
            market_price=market_mid,
            edge_cents=edge_cents,
            is_trading_allowed=should_trade,
            last_signal=last_signal,
            position=position,
        )

        # Log signals
        if last_signal and edge_cents >= args.min_edge:
            state_aggregator.log_activity(
                strategy="nba",
                event_type="signal",
                message=f"{last_signal} | Edge: {edge_cents:.1f}¢ | "
                f"Q{frame.period} {frame.time_remaining}",
                details={
                    "edge_cents": edge_cents,
                    "home_win_prob": home_win_prob,
                    "market_mid": market_mid,
                },
            )

        # Print progress periodically
        now = frame.timestamp
        if args.verbose or (now - last_print_time >= 30):  # Every 30s of game time
            last_print_time = now
            print(
                f"[Q{frame.period} {frame.time_remaining}] "
                f"{recording.away_team} {frame.away_score} - {frame.home_score} {recording.home_team} | "
                f"Edge: {edge_cents:.1f}¢ | "
                f"Prob: {home_win_prob * 100:.1f}% vs Mkt: {market_mid * 100:.1f}%"
                f"{' | ' + last_signal if last_signal else ''}"
            )

    replay.on_frame(on_frame)

    # Score change callback
    def on_score_change(score):
        state_aggregator.log_activity(
            strategy="nba",
            event_type="decision",
            message=f"SCORE: {score.away_team} {score.away_score} - "
            f"{score.home_score} {score.home_team}",
        )

    replay.on_score_change(on_score_change)

    # Run replay
    print(f"Starting replay at {args.speed}x speed...")
    print(f"Open http://localhost:{args.port} to view dashboard\n")

    try:
        async for frame in replay.run(mock_client):
            pass

    except KeyboardInterrupt:
        print("\nReplay stopped by user")
        replay.stop()

    # Print summary
    print(f"\n{'=' * 60}")
    print("Replay Complete")
    print(f"{'=' * 60}")
    print(f"Total frames:     {frame_count}")
    print(f"Real time:        {replay.state.elapsed_real_time:.1f}s")
    print(f"Game time:        {replay.state.elapsed_game_time:.1f}s")

    if recording.metadata.final_status:
        print(
            f"Final score:      {recording.away_team} {recording.metadata.final_away_score} - "
            f"{recording.metadata.final_home_score} {recording.home_team}"
        )

    # Cleanup
    if dashboard_runner:
        await dashboard_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
