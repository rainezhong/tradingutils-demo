"""BTC Orderflow Indicator — reads Binance + Coinbase L2 depth and trade flow.

Produces a live direction/confidence signal based on book imbalance and
aggressive trade flow.  Informational only — no trading, no Kalshi integration.

Usage:
    indicator = OrderflowIndicator()
    indicator.start()
    reading = indicator.get_reading()
    indicator.stop()
"""

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── URLs ──────────────────────────────────────────────────────────────────

BINANCE_DEPTH_URL = "wss://data-stream.binance.vision/ws/btcusdt@depth20@100ms"
BINANCE_TRADE_URL = "wss://data-stream.binance.vision/ws/btcusdt@trade"
COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

# ── Data Structures ──────────────────────────────────────────────────────


@dataclass
class OrderflowConfig:
    trade_window_sec: float = 30.0
    large_trade_btc: float = 1.0
    depth_range_pct: float = 0.1
    active_volume_threshold: float = 0.5  # BTC/sec to be "ACTIVE"
    update_interval_sec: float = 1.0
    neutral_threshold: float = 0.1


@dataclass
class CexBookSnapshot:
    """L2 orderbook state from one exchange."""

    exchange: str
    mid_price: float
    spread_bps: float
    bid_depth_btc: float
    ask_depth_btc: float
    imbalance: float
    top_bid: float
    top_ask: float
    timestamp: float


@dataclass
class OrderflowReading:
    """Combined indicator output."""

    direction: str  # "UP", "DOWN", "NEUTRAL"
    confidence: float  # 0.0 - 1.0
    regime: str  # "ACTIVE" or "QUIET"

    book_imbalance: float
    trade_imbalance: float
    volume_rate_btc_sec: float
    large_trade_count: int
    combined_depth_btc: float

    binance: Optional[CexBookSnapshot]
    coinbase: Optional[CexBookSnapshot]

    timestamp: float


# ── Trade record ─────────────────────────────────────────────────────────


@dataclass
class _Trade:
    ts: float
    price: float
    qty: float
    is_buy: bool


# ── Indicator ────────────────────────────────────────────────────────────


class OrderflowIndicator:
    """Reads Binance + Coinbase orderbooks and trade flow.

    Produces a direction/confidence signal.
    """

    def __init__(self, config: Optional[OrderflowConfig] = None):
        self._config = config or OrderflowConfig()
        self._running = False
        self._threads: List[threading.Thread] = []

        # Book state (protected by lock)
        self._lock = threading.Lock()
        self._binance_bids: List[Tuple[float, float]] = []  # [(price, qty)]
        self._binance_asks: List[Tuple[float, float]] = []
        self._coinbase_bids: Dict[float, float] = {}  # price -> qty
        self._coinbase_asks: Dict[float, float] = {}

        # Trade state
        self._trades: Deque[_Trade] = deque(maxlen=10_000)

        # Cached snapshots
        self._binance_snap: Optional[CexBookSnapshot] = None
        self._coinbase_snap: Optional[CexBookSnapshot] = None

        # Callbacks
        self._callbacks: List[Callable[[OrderflowReading], None]] = []

    # ── Public API ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all WS feeds in background threads."""
        if self._running:
            return
        self._running = True
        targets = [
            ("binance-l2", self._run_binance_l2),
            ("coinbase-l2", self._run_coinbase_l2),
            ("binance-trades", self._run_binance_trades),
            ("coinbase-trades", self._run_coinbase_trades),
        ]
        for name, target in targets:
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        logger.info("OrderflowIndicator started (%d feeds)", len(self._threads))

    def stop(self) -> None:
        """Stop all feeds."""
        self._running = False
        for t in self._threads:
            t.join(timeout=3.0)
        self._threads.clear()
        logger.info("OrderflowIndicator stopped")

    def get_reading(self) -> Optional[OrderflowReading]:
        """Current signal.  Returns None if no data yet."""
        with self._lock:
            return self._compute_reading()

    def on_update(self, callback: Callable[[OrderflowReading], None]) -> None:
        """Register callback for each new reading."""
        self._callbacks.append(callback)

    # ── Signal Computation ────────────────────────────────────────────

    def _compute_reading(self) -> Optional[OrderflowReading]:
        now = time.time()
        cfg = self._config

        # Build per-exchange snapshots
        bn_snap = self._make_binance_snapshot(now)
        cb_snap = self._make_coinbase_snapshot(now)

        if bn_snap is None and cb_snap is None:
            return None

        # Book imbalance — weighted average across exchanges
        total_depth = 0.0
        weighted_imb = 0.0
        for snap in (bn_snap, cb_snap):
            if snap is None:
                continue
            d = snap.bid_depth_btc + snap.ask_depth_btc
            total_depth += d
            weighted_imb += snap.imbalance * d

        if total_depth > 0:
            book_imbalance = weighted_imb / total_depth
        else:
            book_imbalance = 0.0

        # Trade imbalance from rolling window
        cutoff = now - cfg.trade_window_sec
        buy_vol = 0.0
        sell_vol = 0.0
        large_count = 0
        for t in self._trades:
            if t.ts < cutoff:
                continue
            if t.is_buy:
                buy_vol += t.qty
            else:
                sell_vol += t.qty
            if t.qty >= cfg.large_trade_btc:
                large_count += 1

        total_vol = buy_vol + sell_vol
        if total_vol > 0:
            trade_imbalance = (buy_vol - sell_vol) / total_vol
        else:
            trade_imbalance = 0.0

        # Volume rate
        window = min(
            cfg.trade_window_sec, now - (self._trades[0].ts if self._trades else now)
        )
        if window > 0:
            volume_rate = total_vol / window
        else:
            volume_rate = 0.0

        # Direction + confidence
        direction_score = 0.6 * book_imbalance + 0.4 * trade_imbalance

        if abs(direction_score) < cfg.neutral_threshold:
            direction = "NEUTRAL"
        elif direction_score > 0:
            direction = "UP"
        else:
            direction = "DOWN"

        activity = (
            min(1.0, volume_rate / cfg.active_volume_threshold)
            if cfg.active_volume_threshold > 0
            else 1.0
        )
        confidence = abs(direction_score) * (0.5 + 0.5 * activity)
        if large_count > 0:
            confidence = min(1.0, confidence * 1.2)

        regime = "ACTIVE" if volume_rate > cfg.active_volume_threshold else "QUIET"

        return OrderflowReading(
            direction=direction,
            confidence=confidence,
            regime=regime,
            book_imbalance=book_imbalance,
            trade_imbalance=trade_imbalance,
            volume_rate_btc_sec=volume_rate,
            large_trade_count=large_count,
            combined_depth_btc=total_depth,
            binance=bn_snap,
            coinbase=cb_snap,
            timestamp=now,
        )

    def _make_binance_snapshot(self, now: float) -> Optional[CexBookSnapshot]:
        if not self._binance_bids or not self._binance_asks:
            return None
        top_bid = self._binance_bids[0][0]
        top_ask = self._binance_asks[0][0]
        mid = (top_bid + top_ask) / 2.0
        spread_bps = (top_ask - top_bid) / mid * 10000 if mid > 0 else 0.0

        range_pct = self._config.depth_range_pct / 100.0
        bid_depth = sum(q for p, q in self._binance_bids if p >= mid * (1 - range_pct))
        ask_depth = sum(q for p, q in self._binance_asks if p <= mid * (1 + range_pct))
        total = bid_depth + ask_depth
        imb = (bid_depth - ask_depth) / total if total > 0 else 0.0

        return CexBookSnapshot(
            exchange="binance",
            mid_price=mid,
            spread_bps=spread_bps,
            bid_depth_btc=bid_depth,
            ask_depth_btc=ask_depth,
            imbalance=imb,
            top_bid=top_bid,
            top_ask=top_ask,
            timestamp=now,
        )

    def _make_coinbase_snapshot(self, now: float) -> Optional[CexBookSnapshot]:
        if not self._coinbase_bids or not self._coinbase_asks:
            return None
        top_bid = max(self._coinbase_bids.keys())
        top_ask = min(self._coinbase_asks.keys())
        mid = (top_bid + top_ask) / 2.0
        spread_bps = (top_ask - top_bid) / mid * 10000 if mid > 0 else 0.0

        range_pct = self._config.depth_range_pct / 100.0
        lo = mid * (1 - range_pct)
        hi = mid * (1 + range_pct)
        bid_depth = sum(q for p, q in self._coinbase_bids.items() if p >= lo)
        ask_depth = sum(q for p, q in self._coinbase_asks.items() if p <= hi)
        total = bid_depth + ask_depth
        imb = (bid_depth - ask_depth) / total if total > 0 else 0.0

        return CexBookSnapshot(
            exchange="coinbase",
            mid_price=mid,
            spread_bps=spread_bps,
            bid_depth_btc=bid_depth,
            ask_depth_btc=ask_depth,
            imbalance=imb,
            top_bid=top_bid,
            top_ask=top_ask,
            timestamp=now,
        )

    # ── Binance L2 ───────────────────────────────────────────────────

    def _run_binance_l2(self) -> None:
        import asyncio

        asyncio.run(self._binance_l2_loop())

    async def _binance_l2_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    BINANCE_DEPTH_URL, ping_interval=30, ping_timeout=10
                ) as ws:
                    logger.info("Binance L2 connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            bids = [
                                (float(p), float(q)) for p, q in msg.get("bids", [])
                            ]
                            asks = [
                                (float(p), float(q)) for p, q in msg.get("asks", [])
                            ]
                            with self._lock:
                                self._binance_bids = bids
                                self._binance_asks = asks
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except Exception as e:
                logger.error("Binance L2 error: %s", e)
                if self._running:
                    await self._async_sleep(2.0)

    # ── Coinbase L2 ──────────────────────────────────────────────────

    def _run_coinbase_l2(self) -> None:
        import asyncio

        asyncio.run(self._coinbase_l2_loop())

    async def _coinbase_l2_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    COINBASE_WS_URL, ping_interval=30, ping_timeout=10,
                    max_size=10 * 1024 * 1024,
                ) as ws:
                    sub = {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channels": ["level2_batch"],
                    }
                    await ws.send(json.dumps(sub))
                    logger.info("Coinbase L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            mtype = msg.get("type", "")

                            if mtype == "snapshot":
                                bids = {}
                                asks = {}
                                for p, q in msg.get("bids", []):
                                    bids[float(p)] = float(q)
                                for p, q in msg.get("asks", []):
                                    asks[float(p)] = float(q)
                                with self._lock:
                                    self._coinbase_bids = bids
                                    self._coinbase_asks = asks

                            elif mtype == "l2update":
                                with self._lock:
                                    for side, price_s, size_s in msg.get("changes", []):
                                        price = float(price_s)
                                        size = float(size_s)
                                        if side == "buy":
                                            if size == 0:
                                                self._coinbase_bids.pop(price, None)
                                            else:
                                                self._coinbase_bids[price] = size
                                        else:
                                            if size == 0:
                                                self._coinbase_asks.pop(price, None)
                                            else:
                                                self._coinbase_asks[price] = size

                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except Exception as e:
                logger.error("Coinbase L2 error: %s", e)
                if self._running:
                    await self._async_sleep(2.0)

    # ── Binance Trades ───────────────────────────────────────────────

    def _run_binance_trades(self) -> None:
        import asyncio

        asyncio.run(self._binance_trades_loop())

    async def _binance_trades_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    BINANCE_TRADE_URL, ping_interval=30, ping_timeout=10
                ) as ws:
                    logger.info("Binance trades connected")
                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            if "p" in msg:
                                trade = _Trade(
                                    ts=time.time(),
                                    price=float(msg["p"]),
                                    qty=float(msg["q"]),
                                    is_buy=not msg["m"],  # m=True means seller is maker
                                )
                                self._trades.append(trade)
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except Exception as e:
                logger.error("Binance trades error: %s", e)
                if self._running:
                    await self._async_sleep(2.0)

    # ── Coinbase Trades ──────────────────────────────────────────────

    def _run_coinbase_trades(self) -> None:
        import asyncio

        asyncio.run(self._coinbase_trades_loop())

    async def _coinbase_trades_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    COINBASE_WS_URL, ping_interval=30, ping_timeout=10
                ) as ws:
                    sub = {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channels": ["matches"],
                    }
                    await ws.send(json.dumps(sub))
                    logger.info("Coinbase trades connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            if msg.get("type") == "match":
                                trade = _Trade(
                                    ts=time.time(),
                                    price=float(msg["price"]),
                                    qty=float(msg["size"]),
                                    is_buy=msg["side"] == "buy",
                                )
                                self._trades.append(trade)
                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue
            except Exception as e:
                logger.error("Coinbase trades error: %s", e)
                if self._running:
                    await self._async_sleep(2.0)

    # ── Helpers ──────────────────────────────────────────────────────

    async def _async_sleep(self, seconds: float) -> None:
        """Sleep in async context, but break early if stopped."""
        end = time.time() + seconds
        while self._running and time.time() < end:
            await __import__("asyncio").sleep(0.2)
