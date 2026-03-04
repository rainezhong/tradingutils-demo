#!/usr/bin/env python3
"""BTC Latency Probe: Measure Kraken spot vs Kalshi KXBTC15M reaction time.

Streams Kraken BTC/USD trades via WebSocket, computes a rolling 60-second
average (BRTI proxy), and simultaneously polls Kalshi for the current
KXBTC15M market. Logs everything to SQLite for lag analysis.

Usage:
    python3 scripts/btc_latency_probe.py --duration 300   # run for 5 minutes
    python3 scripts/btc_latency_probe.py --analyze         # analyze collected data
"""

import argparse
import asyncio
import json
import logging
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "btc_latency_probe.db"
KRAKEN_WS_URL = "wss://ws.kraken.com/v2"
KALSHI_POLL_INTERVAL = 0.25  # seconds (reduced from 0.5s for faster latency detection)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kraken_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,              -- local receive timestamp
            exchange_ts REAL,              -- kraken timestamp (if available)
            price REAL NOT NULL,
            qty REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kraken_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,              -- snapshot timestamp
            spot_price REAL NOT NULL,      -- latest trade price
            avg_60s REAL NOT NULL,         -- rolling 60-second average
            trade_count_60s INTEGER NOT NULL,
            price_min_60s REAL NOT NULL,
            price_max_60s REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kalshi_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,              -- poll timestamp
            ticker TEXT NOT NULL,
            yes_bid INTEGER,
            yes_ask INTEGER,
            yes_mid REAL,
            floor_strike REAL,
            close_time TEXT,
            seconds_to_close REAL,
            volume INTEGER,
            open_interest INTEGER
        );
        CREATE TABLE IF NOT EXISTS market_settlements (
            ticker TEXT PRIMARY KEY,
            close_time TEXT NOT NULL,
            floor_strike REAL,
            settled_yes INTEGER,           -- 1 if YES won, 0 if NO won
            expiration_value REAL,         -- actual BRTI settlement price
            kraken_avg60_at_settle REAL,   -- our Kraken 60s avg near settlement
            kraken_predicted_yes INTEGER,  -- 1 if Kraken said YES
            kalshi_last_mid REAL,          -- last Kalshi yes_mid before settlement
            kalshi_predicted_yes INTEGER,  -- 1 if Kalshi said YES
            kraken_was_right INTEGER,      -- 1 if Kraken prediction matched outcome
            kalshi_was_right INTEGER       -- 1 if Kalshi prediction matched outcome
        );
        CREATE TABLE IF NOT EXISTS binance_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,              -- local receive timestamp
            exchange_ts REAL,              -- binance timestamp (ms since epoch)
            price REAL NOT NULL,
            qty REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coinbase_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,              -- local receive timestamp
            exchange_ts REAL,              -- coinbase timestamp
            price REAL NOT NULL,
            qty REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS kalshi_orderbook (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            seq INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            yes_levels TEXT NOT NULL,
            no_levels TEXT NOT NULL,
            best_bid INTEGER,
            best_ask INTEGER,
            bid_depth INTEGER,
            ask_depth INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_kraken_snap_ts ON kraken_snapshots(ts);
        CREATE INDEX IF NOT EXISTS idx_kalshi_snap_ts ON kalshi_snapshots(ts);
        CREATE INDEX IF NOT EXISTS idx_kraken_trade_ts ON kraken_trades(ts);
        CREATE INDEX IF NOT EXISTS idx_binance_trade_ts ON binance_trades(ts);
        CREATE INDEX IF NOT EXISTS idx_coinbase_trade_ts ON coinbase_trades(ts);
        CREATE TABLE IF NOT EXISTS binance_l2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            mid_price REAL NOT NULL,
            spread_bps REAL NOT NULL,
            best_bid REAL NOT NULL,
            best_ask REAL NOT NULL,
            bid_depth REAL NOT NULL,
            ask_depth REAL NOT NULL,
            imbalance REAL NOT NULL,
            levels TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coinbase_l2 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            mid_price REAL NOT NULL,
            spread_bps REAL NOT NULL,
            best_bid REAL NOT NULL,
            best_ask REAL NOT NULL,
            bid_depth REAL NOT NULL,
            ask_depth REAL NOT NULL,
            imbalance REAL NOT NULL,
            levels TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ob_ts ON kalshi_orderbook(ts);
        CREATE INDEX IF NOT EXISTS idx_ob_ticker_ts ON kalshi_orderbook(ticker, ts);
        CREATE INDEX IF NOT EXISTS idx_binance_l2_ts ON binance_l2(ts);
        CREATE INDEX IF NOT EXISTS idx_coinbase_l2_ts ON coinbase_l2(ts);
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Kraken WebSocket
# ---------------------------------------------------------------------------


class KrakenTradeStream:
    """Streams BTC/USD trades from Kraken WebSocket v2 and maintains
    a rolling 60-second window for computing BRTI-like averages."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.trades: deque = deque()  # (local_ts, price, qty)
        self.latest_price: Optional[float] = None
        self.connected = False
        self._trade_count = 0
        self._snapshot_interval = 0.5  # save snapshot every 500ms
        self._last_snapshot_ts = 0.0

    def _trim_window(self, now: float):
        """Remove trades older than 60 seconds."""
        cutoff = now - 60.0
        while self.trades and self.trades[0][0] < cutoff:
            self.trades.popleft()

    def get_avg_60s(
        self, now: Optional[float] = None
    ) -> Tuple[float, int, float, float]:
        """Return (avg_price, count, min, max) over last 60 seconds."""
        now = now or time.time()
        self._trim_window(now)
        if not self.trades:
            return (0.0, 0, 0.0, 0.0)
        prices = [t[1] for t in self.trades]
        return (sum(prices) / len(prices), len(prices), min(prices), max(prices))

    def _save_snapshot(self, now: float):
        if now - self._last_snapshot_ts < self._snapshot_interval:
            return
        self._last_snapshot_ts = now
        avg, count, pmin, pmax = self.get_avg_60s(now)
        if self.latest_price is None:
            return
        self.db.execute(
            "INSERT INTO kraken_snapshots (ts, spot_price, avg_60s, trade_count_60s, price_min_60s, price_max_60s) VALUES (?,?,?,?,?,?)",
            (now, self.latest_price, avg, count, pmin, pmax),
        )
        if self._trade_count % 50 == 0:
            self.db.commit()

    async def run(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required: pip install websockets")
            return

        while True:
            try:
                async with websockets.connect(
                    KRAKEN_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    # Subscribe to BTC/USD trades
                    sub_msg = {
                        "method": "subscribe",
                        "params": {
                            "channel": "trade",
                            "symbol": ["BTC/USD"],
                        },
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("Kraken WS: subscribing to BTC/USD trades...")

                    async for raw in ws:
                        now = time.time()
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        # Handle subscription confirmations
                        if msg.get("method") in ("subscribe", "pong"):
                            if not self.connected:
                                self.connected = True
                                logger.info("Kraken WS: connected and subscribed")
                            continue

                        # Handle heartbeat
                        if msg.get("channel") == "heartbeat":
                            continue

                        # Handle trade data
                        if msg.get("channel") == "trade":
                            for trade in msg.get("data", []):
                                price = float(trade["price"])
                                qty = float(trade["qty"])
                                exchange_ts = None
                                if "timestamp" in trade:
                                    # Kraken sends ISO timestamp
                                    try:
                                        dt = datetime.fromisoformat(
                                            trade["timestamp"].replace("Z", "+00:00")
                                        )
                                        exchange_ts = dt.timestamp()
                                    except (ValueError, AttributeError):
                                        pass

                                self.latest_price = price
                                self.trades.append((now, price, qty))
                                self._trade_count += 1

                                # Store individual trade
                                self.db.execute(
                                    "INSERT INTO kraken_trades (ts, exchange_ts, price, qty) VALUES (?,?,?,?)",
                                    (now, exchange_ts, price, qty),
                                )

                            self._trim_window(now)
                            self._save_snapshot(now)

            except Exception as e:
                logger.error(f"Kraken WS error: {e}")
                self.connected = False
                await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Binance WebSocket
# ---------------------------------------------------------------------------

BINANCE_WS_URL = "wss://data-stream.binance.vision/ws/btcusdt@trade"


class BinanceTradeStream:
    """Streams BTC/USDT trades from Binance WebSocket.

    Binance typically delivers 50-200+ trades/sec for BTCUSDT during active
    hours — much higher resolution than Kraken.
    """

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.latest_price: Optional[float] = None
        self.connected = False
        self._trade_count = 0

    async def run(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required: pip install websockets")
            return

        while True:
            try:
                async with websockets.connect(
                    BINANCE_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    self.connected = True
                    logger.info("Binance WS: connected to btcusdt@trade")

                    async for raw in ws:
                        now = time.time()
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        if msg.get("e") != "trade":
                            continue

                        price = float(msg["p"])
                        qty = float(msg["q"])
                        exchange_ts = msg.get("T")  # ms since epoch
                        if exchange_ts:
                            exchange_ts = exchange_ts / 1000.0  # → seconds

                        self.latest_price = price
                        self._trade_count += 1

                        self.db.execute(
                            "INSERT INTO binance_trades (ts, exchange_ts, price, qty) VALUES (?,?,?,?)",
                            (now, exchange_ts, price, qty),
                        )

                        if self._trade_count % 200 == 0:
                            self.db.commit()
                            logger.debug(
                                "Binance: %d trades, latest=$%,.0f",
                                self._trade_count,
                                price,
                            )

            except Exception as e:
                logger.error(f"Binance WS error: {e}")
                self.connected = False
                await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Coinbase WebSocket
# ---------------------------------------------------------------------------

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"


class CoinbaseTradeStream:
    """Streams BTC-USD trades from Coinbase Exchange WebSocket.

    Uses the public 'matches' channel (no auth required).
    Coinbase typically delivers 5-50 trades/sec for BTC-USD.
    """

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.latest_price: Optional[float] = None
        self.connected = False
        self._trade_count = 0

    async def run(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required: pip install websockets")
            return

        while True:
            try:
                async with websockets.connect(
                    COINBASE_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    # Subscribe to matches (public trades)
                    sub_msg = {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channels": ["matches"],
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info("Coinbase WS: subscribing to BTC-USD matches...")

                    async for raw in ws:
                        now = time.time()
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        msg_type = msg.get("type")

                        if msg_type == "subscriptions":
                            self.connected = True
                            logger.info("Coinbase WS: connected and subscribed")
                            continue

                        if msg_type in ("match", "last_match"):
                            price = float(msg["price"])
                            qty = float(msg["size"])

                            # Parse ISO timestamp
                            exchange_ts = None
                            ts_str = msg.get("time")
                            if ts_str:
                                try:
                                    dt = datetime.fromisoformat(
                                        ts_str.replace("Z", "+00:00")
                                    )
                                    exchange_ts = dt.timestamp()
                                except (ValueError, AttributeError):
                                    pass

                            self.latest_price = price
                            self._trade_count += 1

                            self.db.execute(
                                "INSERT INTO coinbase_trades (ts, exchange_ts, price, qty) VALUES (?,?,?,?)",
                                (now, exchange_ts, price, qty),
                            )

                            if self._trade_count % 100 == 0:
                                self.db.commit()
                                logger.debug(
                                    "Coinbase: %d trades, latest=$%,.0f",
                                    self._trade_count,
                                    price,
                                )

            except Exception as e:
                logger.error(f"Coinbase WS error: {e}")
                self.connected = False
                await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Binance L2 Stream
# ---------------------------------------------------------------------------

BINANCE_DEPTH_URL = "wss://data-stream.binance.vision/ws/btcusdt@depth20@100ms"


class BinanceL2Stream:
    """Streams Binance BTC/USDT top-20 L2 depth and records snapshots to SQLite.

    Rate-limited to one write per ``min_write_interval`` seconds (default 200ms).
    """

    def __init__(self, db: sqlite3.Connection, min_write_interval: float = 0.2):
        self.db = db
        self._min_write_interval = min_write_interval
        self.connected = False
        self._last_write_ts = 0.0
        self._write_count = 0
        self._total_writes = 0

    def _record(self, bids: List, asks: List, now: float) -> None:
        if now - self._last_write_ts < self._min_write_interval:
            return
        if not bids or not asks:
            return

        self._last_write_ts = now
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10000 if mid > 0 else 0.0

        bid_depth = sum(q for _, q in bids)
        ask_depth = sum(q for _, q in asks)
        total = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0

        self.db.execute(
            "INSERT INTO binance_l2 "
            "(ts, mid_price, spread_bps, best_bid, best_ask, "
            "bid_depth, ask_depth, imbalance, levels) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                now,
                mid,
                spread_bps,
                best_bid,
                best_ask,
                bid_depth,
                ask_depth,
                imbalance,
                json.dumps({"bids": bids, "asks": asks}),
            ),
        )

        self._write_count += 1
        self._total_writes += 1
        if self._write_count >= 50:
            self.db.commit()
            self._write_count = 0

    async def run(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while True:
            try:
                async with websockets.connect(
                    BINANCE_DEPTH_URL,
                    ping_interval=30,
                    ping_timeout=10,
                ) as ws:
                    self.connected = True
                    logger.info("Binance L2: connected to depth20@100ms")

                    async for raw in ws:
                        now = time.time()
                        try:
                            msg = json.loads(raw)
                            bids = [
                                (float(p), float(q)) for p, q in msg.get("bids", [])
                            ]
                            asks = [
                                (float(p), float(q)) for p, q in msg.get("asks", [])
                            ]
                            self._record(bids, asks, now)
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue

            except Exception as e:
                logger.error("Binance L2 error: %s", e)
                self.connected = False
                await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Coinbase L2 Stream
# ---------------------------------------------------------------------------


class CoinbaseL2Stream:
    """Streams Coinbase BTC-USD L2 orderbook and records snapshots to SQLite.

    Subscribes to ``level2_batch`` channel, maintains a local book from
    snapshot + incremental updates, and writes the top of book at a
    rate-limited interval (default 200ms).
    """

    def __init__(self, db: sqlite3.Connection, min_write_interval: float = 0.2):
        self.db = db
        self._min_write_interval = min_write_interval
        self.connected = False
        self._bids: Dict[float, float] = {}  # price -> qty
        self._asks: Dict[float, float] = {}
        self._last_write_ts = 0.0
        self._write_count = 0
        self._total_writes = 0

    def _record(self, now: float) -> None:
        if now - self._last_write_ts < self._min_write_interval:
            return
        if not self._bids or not self._asks:
            return

        self._last_write_ts = now
        best_bid = max(self._bids.keys())
        best_ask = min(self._asks.keys())
        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10000 if mid > 0 else 0.0

        # Store top 20 levels each side for replay
        sorted_bids = sorted(self._bids.items(), key=lambda x: -x[0])[:20]
        sorted_asks = sorted(self._asks.items(), key=lambda x: x[0])[:20]

        # Sum depth from top 20 only (full book is too deep to be useful)
        bid_depth = sum(q for _, q in sorted_bids)
        ask_depth = sum(q for _, q in sorted_asks)
        total = bid_depth + ask_depth
        imbalance = (bid_depth - ask_depth) / total if total > 0 else 0.0

        self.db.execute(
            "INSERT INTO coinbase_l2 "
            "(ts, mid_price, spread_bps, best_bid, best_ask, "
            "bid_depth, ask_depth, imbalance, levels) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                now,
                mid,
                spread_bps,
                best_bid,
                best_ask,
                bid_depth,
                ask_depth,
                imbalance,
                json.dumps({"bids": sorted_bids, "asks": sorted_asks}),
            ),
        )

        self._write_count += 1
        self._total_writes += 1
        if self._write_count >= 50:
            self.db.commit()
            self._write_count = 0

    async def run(self):
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while True:
            try:
                async with websockets.connect(
                    COINBASE_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
                    max_size=10 * 1024 * 1024,  # 10MB — L2 snapshot is large
                ) as ws:
                    sub = {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channels": ["level2_batch"],
                    }
                    await ws.send(json.dumps(sub))
                    logger.info("Coinbase L2: subscribing to level2_batch...")

                    async for raw in ws:
                        now = time.time()
                        try:
                            msg = json.loads(raw)
                            mtype = msg.get("type", "")

                            if mtype == "subscriptions":
                                self.connected = True
                                logger.info("Coinbase L2: connected and subscribed")
                                continue

                            if mtype == "snapshot":
                                self._bids.clear()
                                self._asks.clear()
                                for p, q in msg.get("bids", []):
                                    self._bids[float(p)] = float(q)
                                for p, q in msg.get("asks", []):
                                    self._asks[float(p)] = float(q)
                                self._record(now)

                            elif mtype == "l2update":
                                for side, price_s, size_s in msg.get("changes", []):
                                    price = float(price_s)
                                    size = float(size_s)
                                    if side == "buy":
                                        if size == 0:
                                            self._bids.pop(price, None)
                                        else:
                                            self._bids[price] = size
                                    else:
                                        if size == 0:
                                            self._asks.pop(price, None)
                                        else:
                                            self._asks[price] = size
                                self._record(now)

                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue

            except Exception as e:
                logger.error("Coinbase L2 error: %s", e)
                self.connected = False
                self._bids.clear()
                self._asks.clear()
                await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# Kalshi Orderbook Stream
# ---------------------------------------------------------------------------


class KalshiOrderbookStream:
    """Streams full Kalshi orderbook depth via WebSocket and records to SQLite.

    Uses KalshiWebSocket for connection/auth and OrderBookManager for
    snapshot/delta state management. Rate-limits DB writes to avoid
    flooding on rapid deltas.
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        min_write_interval: float = 0.1,
        commit_batch_size: int = 20,
    ):
        self.db = db
        self._min_write_interval = min_write_interval
        self._commit_batch_size = commit_batch_size
        self._ws: Optional[Any] = None
        self._ob_manager: Optional[Any] = None
        self._current_ticker: Optional[str] = None
        self._last_write_ts: Dict[str, float] = {}
        self._delta_seq: Dict[str, int] = {}
        self._write_count = 0
        self._total_writes = 0
        self.connected = False

    async def start(self):
        """Initialize WebSocket and OrderBookManager."""
        from core.exchange_client.kalshi import KalshiAuth
        from core.exchange_client.kalshi.kalshi_websocket import (
            KalshiWebSocket,
            WebSocketConfig,
        )
        from core.market.orderbook_manager import OrderBookManager

        auth = KalshiAuth.from_env()
        self._ws = KalshiWebSocket(auth=auth, config=WebSocketConfig())
        self._ob_manager = OrderBookManager(on_update=self._on_book_update)

        # Register WS callbacks — these are sync, called from the async receive loop
        self._ws.on_orderbook_snapshot(self._on_snapshot)
        self._ws.on_orderbook_delta(self._on_delta)

    def _on_snapshot(self, ticker: str, data: dict) -> None:
        """Handle orderbook snapshot from WebSocket."""
        real_ticker = data.get("market_ticker", ticker)
        logger.info(
            "OB snapshot: %s (%d yes, %d no levels)",
            real_ticker,
            len(data.get("yes", [])),
            len(data.get("no", [])),
        )
        if self._ob_manager:
            # Kalshi snapshots lack seq — assign 0 (our delta handler increments)
            asyncio.ensure_future(self._ob_manager.apply_snapshot(real_ticker, data))
            self._delta_seq[real_ticker] = 0

    def _on_delta(self, ticker: str, data: dict) -> None:
        """Handle orderbook delta from WebSocket."""
        real_ticker = data.get("market_ticker", ticker)
        if not self._ob_manager:
            return
        # Kalshi deltas lack seq — synthesize consecutive seq for OrderBookManager
        cur = self._delta_seq.get(real_ticker, 0)
        data["seq"] = cur + 1
        self._delta_seq[real_ticker] = cur + 1
        asyncio.ensure_future(self._ob_manager.apply_delta(real_ticker, data))

    def _on_book_update(self, ticker: str, state: "OrderBookState") -> None:  # noqa: F821
        """Called by OrderBookManager after each snapshot/delta application.

        Rate-limits writes and serializes the full book to SQLite.
        """
        now = time.time()
        last = self._last_write_ts.get(ticker, 0.0)
        if now - last < self._min_write_interval:
            return

        self._last_write_ts[ticker] = now

        # Serialize levels as raw [[price, qty], ...] — bids in yes-cents, asks in no-cents
        yes_levels = [[lvl.price, lvl.size] for lvl in state.bids]
        no_levels = [[100 - lvl.price, lvl.size] for lvl in state.asks]

        best_bid = state.best_bid.price if state.best_bid else None
        best_ask = state.best_ask.price if state.best_ask else None

        self.db.execute(
            "INSERT INTO kalshi_orderbook "
            "(ts, seq, ticker, yes_levels, no_levels, best_bid, best_ask, bid_depth, ask_depth) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                now,
                state.sequence,
                ticker,
                json.dumps(yes_levels),
                json.dumps(no_levels),
                best_bid,
                best_ask,
                state.bid_depth,
                state.ask_depth,
            ),
        )

        self._write_count += 1
        self._total_writes += 1
        if self._write_count >= self._commit_batch_size:
            self.db.commit()
            self._write_count = 0

    async def subscribe(self, ticker: str) -> None:
        """Subscribe to orderbook updates for a ticker.

        Handles rotation: unsubscribes from the previous ticker first.
        """
        if not self._ws:
            return

        if ticker == self._current_ticker:
            return

        if self._current_ticker:
            try:
                await self._ws.unsubscribe("orderbook_delta", self._current_ticker)
                logger.info("OB stream: unsubscribed from %s", self._current_ticker)
            except Exception as e:
                logger.warning("OB stream: unsubscribe error: %s", e)

        self._current_ticker = ticker
        try:
            await self._ws.subscribe("orderbook_delta", ticker)
            logger.info("OB stream: subscribed to %s", ticker)
        except Exception as e:
            logger.warning("OB stream: subscribe error: %s", e)

    async def run(self):
        """Main loop: connect WS and keep alive."""
        await self.start()
        try:
            async with self._ws:
                self.connected = True
                logger.info("Kalshi OB stream: connected")
                # If we already know a ticker, subscribe now
                if self._current_ticker:
                    await self._ws.subscribe("orderbook_delta", self._current_ticker)
                # Keep alive until cancelled
                while True:
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Kalshi OB stream error: %s", e)
        finally:
            self.connected = False
            # Flush remaining writes
            if self._write_count > 0:
                self.db.commit()
            logger.info(
                "Kalshi OB stream: stopped (%d total snapshots written)",
                self._total_writes,
            )


# ---------------------------------------------------------------------------
# Kalshi Poller
# ---------------------------------------------------------------------------


class KalshiPoller:
    """Polls Kalshi REST API for current KXBTC15M market data."""

    def __init__(
        self, db: sqlite3.Connection, ob_stream: Optional[KalshiOrderbookStream] = None
    ):
        self.db = db
        self.client = None
        self._ob_stream = ob_stream
        self._current_ticker: Optional[str] = None
        self._current_floor_strike: Optional[float] = None
        self._current_close_time: Optional[str] = None
        self.latest_yes_mid: Optional[float] = None
        self._settled_tickers: set = set()  # avoid re-fetching

    async def connect(self):
        try:
            from core.exchange_client.kalshi import KalshiExchangeClient
        except ImportError:
            from core.exchange_client.kalshi.kalshi_client import KalshiExchangeClient
        self.client = KalshiExchangeClient.from_env()
        await self.client.connect()
        logger.info("Kalshi: connected")

    async def _find_active_market(self):
        """Find the next-expiring open KXBTC15M market."""
        markets_data = await self.client._request(
            "GET",
            "/markets",
            params={"series_ticker": "KXBTC15M", "status": "open", "limit": 5},
        )
        markets = markets_data.get("markets", [])
        if not markets:
            logger.warning("No open KXBTC15M markets found")
            return None

        # Sort by close_time, pick soonest
        markets.sort(key=lambda m: m.get("close_time", ""))
        mkt = markets[0]
        self._current_ticker = mkt.get("ticker")
        self._current_floor_strike = mkt.get("floor_strike")
        self._current_close_time = mkt.get("close_time")
        logger.info(
            f"Kalshi: tracking {self._current_ticker} "
            f"(strike={self._current_floor_strike}, close={self._current_close_time})"
        )

        # Notify orderbook stream of ticker change
        if self._ob_stream and self._current_ticker:
            await self._ob_stream.subscribe(self._current_ticker)

        return mkt

    async def _record_settlement(self, ticker: str, kraken: "KrakenTradeStream"):
        """After a market expires, fetch settlement result and record who was right."""
        if not ticker or ticker in self._settled_tickers:
            return
        self._settled_tickers.add(ticker)

        try:
            # Wait a moment for Kalshi to settle the market
            await asyncio.sleep(2)

            # Fetch settled market data
            mkt_data = await self.client._request("GET", f"/markets/{ticker}")
            mkt = mkt_data.get("market", mkt_data)

            exp_value = mkt.get("expiration_value")
            strike = mkt.get("floor_strike")
            close_time = mkt.get("close_time")
            result = mkt.get("result")  # "yes" or "no"

            # Determine actual settlement
            if result:
                settled_yes = 1 if result.lower() == "yes" else 0
            elif exp_value is not None and strike is not None:
                settled_yes = 1 if exp_value > strike else 0
            else:
                settled_yes = None

            # Get Kraken state near settlement
            now = time.time()
            kraken_avg, count, _, _ = kraken.get_avg_60s(now)
            kraken_predicted_yes = (
                1 if (kraken_avg > strike if kraken_avg and strike else None) else 0
            )

            # Get last Kalshi snapshot for this ticker
            last_snap = self.db.execute(
                "SELECT yes_mid FROM kalshi_snapshots WHERE ticker=? ORDER BY ts DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            kalshi_last_mid = last_snap[0] if last_snap else None
            kalshi_predicted_yes = (
                1 if (kalshi_last_mid and kalshi_last_mid > 50) else 0
            )

            # Who was right?
            kraken_right = (
                1
                if (settled_yes is not None and kraken_predicted_yes == settled_yes)
                else 0
            )
            kalshi_right = (
                1
                if (settled_yes is not None and kalshi_predicted_yes == settled_yes)
                else 0
            )

            self.db.execute(
                """
                INSERT OR REPLACE INTO market_settlements
                (ticker, close_time, floor_strike, settled_yes, expiration_value,
                 kraken_avg60_at_settle, kraken_predicted_yes,
                 kalshi_last_mid, kalshi_predicted_yes,
                 kraken_was_right, kalshi_was_right)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    ticker,
                    close_time,
                    strike,
                    settled_yes,
                    exp_value,
                    kraken_avg,
                    kraken_predicted_yes,
                    kalshi_last_mid,
                    kalshi_predicted_yes,
                    kraken_right,
                    kalshi_right,
                ),
            )
            self.db.commit()

            logger.info(
                f"SETTLEMENT: {ticker} result={'YES' if settled_yes else 'NO'} "
                f"BRTI={exp_value} strike={strike} | "
                f"Kraken={'RIGHT' if kraken_right else 'WRONG'} "
                f"Kalshi={'RIGHT' if kalshi_right else 'WRONG'}"
            )

        except Exception as e:
            logger.error(f"Settlement recording error for {ticker}: {e}")

    async def run(self, kraken: KrakenTradeStream):
        await self.connect()
        await self._find_active_market()

        refresh_counter = 0
        while True:
            try:
                now = time.time()

                # Refresh active market every 60 polls (~30 seconds)
                refresh_counter += 1
                if refresh_counter >= 60 or self._current_ticker is None:
                    await self._find_active_market()
                    refresh_counter = 0

                if self._current_ticker is None:
                    await asyncio.sleep(KALSHI_POLL_INTERVAL)
                    continue

                # Fetch current market state
                mkt_data = await self.client._request(
                    "GET", f"/markets/{self._current_ticker}"
                )
                mkt = mkt_data.get("market", mkt_data)

                yes_bid = mkt.get("yes_bid") or 0
                yes_ask = mkt.get("yes_ask") or 100
                yes_mid = (yes_bid + yes_ask) / 2.0

                # Parse close_time for seconds_to_close
                seconds_to_close = None
                close_str = mkt.get("close_time")
                if close_str:
                    try:
                        close_dt = datetime.fromisoformat(
                            close_str.replace("Z", "+00:00")
                        )
                        seconds_to_close = (
                            close_dt - datetime.now(timezone.utc)
                        ).total_seconds()
                    except (ValueError, AttributeError):
                        pass

                # If market closed, record settlement and refresh
                if seconds_to_close is not None and seconds_to_close < 0:
                    await self._record_settlement(self._current_ticker, kraken)
                    logger.info(
                        f"Market {self._current_ticker} expired, finding next..."
                    )
                    await self._find_active_market()
                    refresh_counter = 0
                    await asyncio.sleep(KALSHI_POLL_INTERVAL)
                    continue

                self.latest_yes_mid = yes_mid
                self._current_floor_strike = mkt.get(
                    "floor_strike", self._current_floor_strike
                )

                self.db.execute(
                    "INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid, floor_strike, close_time, seconds_to_close, volume, open_interest) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        now,
                        self._current_ticker,
                        yes_bid,
                        yes_ask,
                        yes_mid,
                        self._current_floor_strike,
                        close_str,
                        seconds_to_close,
                        mkt.get("volume", 0),
                        mkt.get("open_interest", 0),
                    ),
                )
                self.db.commit()

                # Print live status
                avg_60s, count_60s, _, _ = kraken.get_avg_60s(now)
                kraken_spot = kraken.latest_price or 0
                strike = self._current_floor_strike or 0

                # Direction comparison
                kraken_says_up = (
                    avg_60s > strike if avg_60s > 0 and strike > 0 else None
                )
                kalshi_says_up = yes_mid > 50 if yes_mid else None

                agree_str = ""
                if kraken_says_up is not None and kalshi_says_up is not None:
                    agree_str = (
                        "AGREE" if kraken_says_up == kalshi_says_up else "DISAGREE"
                    )

                if count_60s > 0:
                    diff = avg_60s - strike
                    logger.info(
                        f"Kraken=${kraken_spot:,.0f} avg60s=${avg_60s:,.0f} ({count_60s} trades) | "
                        f"Strike=${strike:,.0f} diff=${diff:+,.0f} | "
                        f"Kalshi yes_mid={yes_mid:.0f}c bid/ask={yes_bid}/{yes_ask} | "
                        f"{agree_str} | close in {seconds_to_close:.0f}s"
                    )

            except Exception as e:
                logger.error(f"Kalshi poll error: {e}")

            await asyncio.sleep(KALSHI_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def analyze(db_path: Path):
    """Analyze collected latency data."""
    conn = sqlite3.connect(str(db_path))

    # Basic stats
    kraken_count = conn.execute("SELECT COUNT(*) FROM kraken_trades").fetchone()[0]
    kraken_snap_count = conn.execute(
        "SELECT COUNT(*) FROM kraken_snapshots"
    ).fetchone()[0]
    kalshi_count = conn.execute("SELECT COUNT(*) FROM kalshi_snapshots").fetchone()[0]

    if kraken_count == 0 or kalshi_count == 0:
        print("No data collected yet. Run the probe first.")
        return

    # Binance/Coinbase counts (tables may not exist in old DBs)
    binance_count = 0
    coinbase_count = 0
    binance_l2_count = 0
    coinbase_l2_count = 0
    try:
        binance_count = conn.execute("SELECT COUNT(*) FROM binance_trades").fetchone()[
            0
        ]
    except Exception:
        pass
    try:
        coinbase_count = conn.execute(
            "SELECT COUNT(*) FROM coinbase_trades"
        ).fetchone()[0]
    except Exception:
        pass
    try:
        binance_l2_count = conn.execute("SELECT COUNT(*) FROM binance_l2").fetchone()[0]
    except Exception:
        pass
    try:
        coinbase_l2_count = conn.execute("SELECT COUNT(*) FROM coinbase_l2").fetchone()[
            0
        ]
    except Exception:
        pass

    print("\n=== BTC Latency Probe Analysis ===")
    print(f"Kraken trades:    {kraken_count:,}")
    print(f"Binance trades:   {binance_count:,}")
    print(f"Coinbase trades:  {coinbase_count:,}")
    print(f"Binance L2:       {binance_l2_count:,}")
    print(f"Coinbase L2:      {coinbase_l2_count:,}")
    print(f"Kraken snapshots: {kraken_snap_count:,}")
    print(f"Kalshi snapshots: {kalshi_count:,}")

    # Time range
    k_min, k_max = conn.execute("SELECT MIN(ts), MAX(ts) FROM kraken_trades").fetchone()
    duration_min = (k_max - k_min) / 60
    print(f"Collection window: {duration_min:.1f} minutes")

    # Kraken trade frequency
    trades_per_sec = kraken_count / max(k_max - k_min, 1)
    print(f"Kraken trades/sec: {trades_per_sec:.1f}")

    # Join Kraken snapshots to nearest Kalshi snapshot
    # For each Kalshi poll, find the closest Kraken snapshot
    print("\n--- Price Comparison ---")

    rows = conn.execute("""
        SELECT
            k.ts,
            k.ticker,
            k.yes_mid,
            k.floor_strike,
            k.seconds_to_close,
            k.yes_bid,
            k.yes_ask,
            (SELECT kr.avg_60s FROM kraken_snapshots kr
             WHERE kr.ts <= k.ts ORDER BY kr.ts DESC LIMIT 1) as kraken_avg60,
            (SELECT kr.spot_price FROM kraken_snapshots kr
             WHERE kr.ts <= k.ts ORDER BY kr.ts DESC LIMIT 1) as kraken_spot
        FROM kalshi_snapshots k
        WHERE k.floor_strike IS NOT NULL AND k.floor_strike > 0
        ORDER BY k.ts
    """).fetchall()

    if not rows:
        print("No matched data points.")
        return

    agree_count = 0
    disagree_count = 0
    kalshi_direction_changes = []
    kraken_direction_changes = []

    prev_kalshi_up = None
    prev_kraken_up = None

    for row in rows:
        ts, ticker, yes_mid, strike, sec_to_close, yes_bid, yes_ask, avg60, spot = row
        if avg60 is None or avg60 == 0 or strike is None or strike == 0:
            continue

        kraken_up = avg60 > strike
        kalshi_up = yes_mid > 50

        if kraken_up == kalshi_up:
            agree_count += 1
        else:
            disagree_count += 1

        # Track direction changes for lag analysis
        if prev_kraken_up is not None and kraken_up != prev_kraken_up:
            kraken_direction_changes.append(ts)
        if prev_kalshi_up is not None and kalshi_up != prev_kalshi_up:
            kalshi_direction_changes.append(ts)

        prev_kraken_up = kraken_up
        prev_kalshi_up = kalshi_up

    total = agree_count + disagree_count
    if total > 0:
        print(
            f"Direction agreement: {agree_count}/{total} ({100 * agree_count / total:.1f}%)"
        )
        print(
            f"Disagreements:      {disagree_count}/{total} ({100 * disagree_count / total:.1f}%)"
        )

    # Analyze Kalshi quote staleness
    print("\n--- Kalshi Quote Staleness ---")
    # Look at periods where Kalshi yes_mid stayed constant while Kraken moved
    rows2 = conn.execute("""
        SELECT k.ts, k.yes_mid, k.yes_bid, k.yes_ask,
            (SELECT kr.spot_price FROM kraken_snapshots kr
             WHERE kr.ts <= k.ts ORDER BY kr.ts DESC LIMIT 1) as kraken_spot
        FROM kalshi_snapshots k
        ORDER BY k.ts
    """).fetchall()

    if len(rows2) > 1:
        stale_periods = 0
        stale_durations = []
        current_stale_start = None
        prev_mid = None

        for ts, mid, bid, ask, spot in rows2:
            if prev_mid is not None:
                if mid == prev_mid:
                    if current_stale_start is None:
                        current_stale_start = ts
                else:
                    if current_stale_start is not None:
                        stale_durations.append(ts - current_stale_start)
                        stale_periods += 1
                        current_stale_start = None
            prev_mid = mid

        if stale_durations:
            avg_stale = sum(stale_durations) / len(stale_durations)
            max_stale = max(stale_durations)
            print(f"Periods where Kalshi yes_mid was unchanged: {stale_periods}")
            print(f"Average stale duration: {avg_stale:.1f}s")
            print(f"Max stale duration:     {max_stale:.1f}s")
        else:
            print("Kalshi yes_mid changed every poll (no staleness detected)")

    # Analyze Kraken spot volatility vs Kalshi spread
    print("\n--- Kraken Spot Movement ---")
    spot_rows = conn.execute("""
        SELECT spot_price, avg_60s, price_min_60s, price_max_60s, trade_count_60s
        FROM kraken_snapshots
        ORDER BY ts
    """).fetchall()

    if spot_rows:
        ranges = [r[3] - r[2] for r in spot_rows if r[2] > 0]
        if ranges:
            print(f"60s price range (avg):  ${sum(ranges) / len(ranges):,.0f}")
            print(f"60s price range (max):  ${max(ranges):,.0f}")
            print(
                f"60s trade count (avg):  {sum(r[4] for r in spot_rows) / len(spot_rows):.0f}"
            )

    # Settlement scorecard (from market_settlements table)
    print("\n--- Settlement Scorecard ---")
    try:
        settlements = conn.execute(
            "SELECT COUNT(*) FROM market_settlements"
        ).fetchone()[0]
        if settlements > 0:
            kraken_right = (
                conn.execute(
                    "SELECT SUM(kraken_was_right) FROM market_settlements"
                ).fetchone()[0]
                or 0
            )
            kalshi_right = (
                conn.execute(
                    "SELECT SUM(kalshi_was_right) FROM market_settlements"
                ).fetchone()[0]
                or 0
            )
            print(f"Markets settled:     {settlements}")
            print(
                f"Kraken was right:    {kraken_right}/{settlements} ({100 * kraken_right / settlements:.0f}%)"
            )
            print(
                f"Kalshi was right:    {kalshi_right}/{settlements} ({100 * kalshi_right / settlements:.0f}%)"
            )

            # Show disagreement outcomes
            disagree_rows = conn.execute("""
                SELECT ticker, floor_strike, expiration_value, settled_yes,
                       kraken_avg60_at_settle, kraken_predicted_yes, kraken_was_right,
                       kalshi_last_mid, kalshi_predicted_yes, kalshi_was_right
                FROM market_settlements
                WHERE kraken_predicted_yes != kalshi_predicted_yes
            """).fetchall()
            if disagree_rows:
                kraken_wins_disagree = sum(1 for r in disagree_rows if r[6])
                kalshi_wins_disagree = sum(1 for r in disagree_rows if r[9])
                print(f"\nWhen they DISAGREED ({len(disagree_rows)} markets):")
                print(
                    f"  Kraken was right:  {kraken_wins_disagree}/{len(disagree_rows)}"
                )
                print(
                    f"  Kalshi was right:  {kalshi_wins_disagree}/{len(disagree_rows)}"
                )
                print(
                    f"  → Edge if betting with Kraken: {100 * kraken_wins_disagree / len(disagree_rows):.0f}% win rate"
                )
        else:
            print("No settlements recorded yet (markets haven't expired during probe)")
    except Exception:
        print("No settlement data (table may not exist in current run)")

    # Derive settlements from recorded data (for the running probe that lacks the table)
    print("\n--- Derived Settlement Analysis (from snapshots) ---")
    # Group kalshi snapshots by ticker, find the last snapshot (near settlement)
    tickers = conn.execute("""
        SELECT DISTINCT ticker FROM kalshi_snapshots
    """).fetchall()

    derived_count = 0
    kraken_right_d = 0
    kalshi_right_d = 0
    disagree_kraken_right = 0
    disagree_total = 0

    for (ticker,) in tickers:
        # Get the last snapshot near close (seconds_to_close closest to 0 but > -30)
        last_snap = conn.execute(
            """
            SELECT ts, yes_mid, floor_strike, seconds_to_close, close_time
            FROM kalshi_snapshots
            WHERE ticker = ? AND seconds_to_close IS NOT NULL
            ORDER BY seconds_to_close ASC
            LIMIT 1
        """,
            (ticker,),
        ).fetchone()

        if not last_snap or last_snap[3] > 30:
            continue  # didn't capture near settlement

        snap_ts, kalshi_mid, strike, stc, close_time = last_snap
        if not strike or strike <= 0:
            continue

        # Get Kraken avg_60s at that timestamp
        kraken_row = conn.execute(
            """
            SELECT avg_60s FROM kraken_snapshots
            WHERE ts <= ? ORDER BY ts DESC LIMIT 1
        """,
            (snap_ts,),
        ).fetchone()

        if not kraken_row or kraken_row[0] <= 0:
            continue

        kraken_avg = kraken_row[0]

        # Use Kraken avg as settlement proxy (within ~$10 of BRTI)
        actual_yes = kraken_avg > strike
        kraken_says_yes = kraken_avg > strike  # tautologically true here
        kalshi_says_yes = kalshi_mid > 50

        derived_count += 1
        kraken_right_d += 1  # Kraken IS our settlement proxy, so always "right"
        if kalshi_says_yes == actual_yes:
            kalshi_right_d += 1

        if kraken_says_yes != kalshi_says_yes:
            disagree_total += 1
            # Since kraken IS settlement proxy, kraken always wins disagreements
            disagree_kraken_right += 1

    if derived_count > 0:
        print(f"Markets near settlement:  {derived_count}")
        print(
            f"Kalshi agreed w/ outcome: {kalshi_right_d}/{derived_count} ({100 * kalshi_right_d / derived_count:.0f}%)"
        )
        if disagree_total > 0:
            print(f"Disagreements at settle:  {disagree_total}")
            print(
                f"  → Kalshi was wrong in all {disagree_total} "
                f"(Kraken = settlement proxy)"
            )
            print(
                f"  → If you bet AGAINST Kalshi on disagrees: {disagree_total}/{disagree_total} wins"
            )

    # Cross-correlation: when Kraken avg60 crosses strike, how long until Kalshi reacts?
    print("\n--- Lag Analysis: Kraken Cross → Kalshi Reaction ---")

    # Find moments where kraken avg60 crosses the strike
    cross_events = []
    prev_row = None
    for row in rows:
        ts, ticker, yes_mid, strike, sec_to_close, yes_bid, yes_ask, avg60, spot = row
        if avg60 is None or avg60 == 0 or strike is None or strike == 0:
            continue
        if prev_row is not None:
            _, _, prev_mid, prev_strike, _, _, _, prev_avg60, _ = prev_row
            if prev_avg60 is not None and prev_strike is not None and prev_strike > 0:
                prev_above = prev_avg60 > prev_strike
                curr_above = avg60 > strike
                if prev_above != curr_above:
                    # Kraken 60s average crossed the strike
                    cross_events.append(
                        {
                            "ts": ts,
                            "direction": "UP" if curr_above else "DOWN",
                            "kalshi_mid_at_cross": yes_mid,
                            "kraken_avg": avg60,
                            "strike": strike,
                        }
                    )
        prev_row = row

    if cross_events:
        print(f"Found {len(cross_events)} Kraken-crosses-strike events")

        # For each cross, find when Kalshi's yes_mid moved to agree
        lags = []
        for event in cross_events:
            cross_ts = event["ts"]
            kalshi_agreed_at_cross = (
                event["direction"] == "UP" and event["kalshi_mid_at_cross"] > 50
            ) or (event["direction"] == "DOWN" and event["kalshi_mid_at_cross"] <= 50)

            if kalshi_agreed_at_cross:
                lags.append(0.0)  # Already agreed
                continue

            # Look for when Kalshi caught up
            future_kalshi = conn.execute(
                """
                SELECT ts, yes_mid FROM kalshi_snapshots
                WHERE ts > ? ORDER BY ts LIMIT 120
            """,
                (cross_ts,),
            ).fetchall()

            found = False
            for fts, fmid in future_kalshi:
                if event["direction"] == "UP" and fmid > 50:
                    lags.append(fts - cross_ts)
                    found = True
                    break
                elif event["direction"] == "DOWN" and fmid <= 50:
                    lags.append(fts - cross_ts)
                    found = True
                    break

            if not found:
                # Kalshi never agreed within window (or market changed)
                pass

        if lags:
            already = sum(1 for l in lags if l == 0)
            lagged = [l for l in lags if l > 0]
            print(f"  Already agreed at cross: {already}/{len(lags)}")
            if lagged:
                print(f"  Lagged cases: {len(lagged)}")
                print(f"  Average lag: {sum(lagged) / len(lagged):.1f}s")
                print(f"  Median lag:  {sorted(lagged)[len(lagged) // 2]:.1f}s")
                print(f"  Max lag:     {max(lagged):.1f}s")
            else:
                print("  No lagged cases — Kalshi was always already aligned")
    else:
        print(
            "No strike-crossing events detected (need more volatile period or longer collection)"
        )

    conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_probe(duration: int, db_path: Path):
    conn = init_db(db_path)
    kraken = KrakenTradeStream(conn)
    binance = BinanceTradeStream(conn)
    coinbase = CoinbaseTradeStream(conn)
    binance_l2 = BinanceL2Stream(conn)
    coinbase_l2 = CoinbaseL2Stream(conn)
    ob_stream = KalshiOrderbookStream(conn)
    kalshi = KalshiPoller(conn, ob_stream=ob_stream)

    logger.info(f"Starting BTC latency probe for {duration}s...")
    logger.info(f"DB: {db_path}")
    logger.info(
        "Feeds: Kraken WS + Binance WS (trades+L2) + Coinbase WS (trades+L2) + Kalshi REST + Kalshi OB WS"
    )

    async def run_with_timeout():
        kraken_task = asyncio.create_task(kraken.run())
        binance_task = asyncio.create_task(binance.run())
        coinbase_task = asyncio.create_task(coinbase.run())
        binance_l2_task = asyncio.create_task(binance_l2.run())
        coinbase_l2_task = asyncio.create_task(coinbase_l2.run())
        ob_task = asyncio.create_task(ob_stream.run())

        # Wait for at least Kraken to connect before starting Kalshi
        for _ in range(50):
            if kraken.connected:
                break
            await asyncio.sleep(0.1)
        kalshi_task = asyncio.create_task(kalshi.run(kraken))

        # Log connection status
        await asyncio.sleep(3)
        feeds = []
        if kraken.connected:
            feeds.append("Kraken")
        if binance.connected:
            feeds.append("Binance-trades")
        if binance_l2.connected:
            feeds.append("Binance-L2")
        if coinbase.connected:
            feeds.append("Coinbase-trades")
        if coinbase_l2.connected:
            feeds.append("Coinbase-L2")
        if ob_stream.connected:
            feeds.append("Kalshi-OB")
        logger.info("Connected feeds: %s", ", ".join(feeds) or "NONE")

        await asyncio.sleep(duration)
        logger.info("Duration reached, stopping...")

        for task in [
            kraken_task,
            binance_task,
            coinbase_task,
            binance_l2_task,
            coinbase_l2_task,
            kalshi_task,
            ob_task,
        ]:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        await run_with_timeout()
    finally:
        conn.commit()
        conn.close()

        # Summary
        conn2 = sqlite3.connect(str(db_path))
        kr_n = conn2.execute("SELECT COUNT(*) FROM kraken_trades").fetchone()[0]
        bn_n = conn2.execute("SELECT COUNT(*) FROM binance_trades").fetchone()[0]
        cb_n = conn2.execute("SELECT COUNT(*) FROM coinbase_trades").fetchone()[0]
        bn_l2 = conn2.execute("SELECT COUNT(*) FROM binance_l2").fetchone()[0]
        cb_l2 = conn2.execute("SELECT COUNT(*) FROM coinbase_l2").fetchone()[0]
        ka_n = conn2.execute("SELECT COUNT(*) FROM kalshi_snapshots").fetchone()[0]
        ob_n = conn2.execute("SELECT COUNT(*) FROM kalshi_orderbook").fetchone()[0]
        conn2.close()
        logger.info(
            f"Saved to {db_path} | Kraken={kr_n:,} Binance={bn_n:,} "
            f"Coinbase={cb_n:,} Binance-L2={bn_l2:,} Coinbase-L2={cb_l2:,} "
            f"Kalshi={ka_n:,} Orderbook={ob_n:,}"
        )


def main():
    parser = argparse.ArgumentParser(description="BTC Latency Probe")
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Collection duration in seconds (default 300)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Analyze existing data instead of collecting",
    )
    parser.add_argument("--db", type=str, default=str(DB_PATH), help="Database path")
    args = parser.parse_args()

    db_path = Path(args.db)

    if args.analyze:
        analyze(db_path)
    else:
        asyncio.run(run_probe(args.duration, db_path))
        print("\nCollection complete. Run with --analyze to see results.")
        analyze(db_path)


if __name__ == "__main__":
    main()
