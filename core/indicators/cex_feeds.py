"""L2 orderbook feeds from BRTI constituent exchanges.

Provides mid-price streams from 5 of the 7 CF Benchmarks constituent
exchanges (all that offer free public WebSocket L2 feeds).  Each feed
runs in a daemon thread with auto-reconnect.

Usage:
    feed = KrakenL2Feed()
    feed.start()
    state = feed.get_state()   # L2BookState or None
    feed.stop()
"""

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Data Structures ──────────────────────────────────────────────────────


@dataclass
class L2BookState:
    """Snapshot of one exchange's L2 book."""

    exchange: str
    mid_price: float
    best_bid: float
    best_ask: float
    spread_bps: float
    timestamp: float
    connected: bool
    bid_depth: float = 0.0  # total bid depth
    ask_depth: float = 0.0  # total ask depth
    imbalance: float = 0.0  # (bid_depth - ask_depth) / total


# ── Base Class ───────────────────────────────────────────────────────────


class ExchangeL2Feed:
    """Base class for exchange L2 feeds.

    Subclasses implement ``_ws_loop()`` which maintains the WebSocket
    connection and updates ``_best_bid`` / ``_best_ask`` under ``_lock``.

    Sequence gap detection:
    - If the exchange provides sequence numbers, subclasses should track them
    - Call _check_sequence_gap() to detect gaps and trigger reconnection
    """

    EXCHANGE: str = ""
    SUPPORTS_SEQUENCE: bool = False  # Subclasses override if they provide seq numbers

    def __init__(self, enable_sequence_validation: bool = False, gap_tolerance: int = 0):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._best_bid: float = 0.0
        self._best_ask: float = 0.0
        self._bid_depth: float = 0.0
        self._ask_depth: float = 0.0
        self._last_update: float = 0.0
        self._connected = False

        # Sequence gap detection
        self._enable_sequence_validation = enable_sequence_validation and self.SUPPORTS_SEQUENCE
        self._gap_tolerance = gap_tolerance
        self._last_seq: Optional[int] = None
        self._total_gaps = 0
        self._last_gap_time: Optional[float] = None
        self._gap_sizes: List[int] = []

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, name=f"{self.EXCHANGE}-l2", daemon=True
        )
        self._thread.start()
        logger.info("%s L2 feed started", self.EXCHANGE)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._connected = False
        logger.info("%s L2 feed stopped", self.EXCHANGE)

    def get_state(self) -> Optional[L2BookState]:
        with self._lock:
            if self._best_bid <= 0 or self._best_ask <= 0:
                return None
            mid = (self._best_bid + self._best_ask) / 2.0
            spread_bps = (self._best_ask - self._best_bid) / mid * 10000 if mid > 0 else 0.0
            total_depth = self._bid_depth + self._ask_depth
            imbalance = (self._bid_depth - self._ask_depth) / total_depth if total_depth > 0 else 0.0
            return L2BookState(
                exchange=self.EXCHANGE,
                mid_price=mid,
                best_bid=self._best_bid,
                best_ask=self._best_ask,
                spread_bps=spread_bps,
                timestamp=self._last_update,
                connected=self._connected,
                bid_depth=self._bid_depth,
                ask_depth=self._ask_depth,
                imbalance=imbalance,
            )

    @property
    def mid_price(self) -> Optional[float]:
        with self._lock:
            if self._best_bid <= 0 or self._best_ask <= 0:
                return None
            return (self._best_bid + self._best_ask) / 2.0

    def _run(self) -> None:
        asyncio.run(self._ws_loop())

    async def _ws_loop(self) -> None:
        raise NotImplementedError

    def _update_bbo(self, bid: float, ask: float, bid_depth: float = 0.0, ask_depth: float = 0.0) -> None:
        """Thread-safe update of best bid/ask and depths."""
        with self._lock:
            self._best_bid = bid
            self._best_ask = ask
            self._bid_depth = bid_depth
            self._ask_depth = ask_depth
            self._last_update = time.time()
            self._connected = True

    def _check_sequence_gap(self, seq: int) -> bool:
        """Check for sequence number gap.

        Returns True if a gap was detected (outside tolerance).
        Subclasses should call this if they track sequence numbers.
        """
        if not self._enable_sequence_validation:
            return False

        with self._lock:
            if self._last_seq is None:
                # First message - initialize tracking
                self._last_seq = seq
                return False

            expected_seq = self._last_seq + 1
            gap_size = seq - expected_seq

            if gap_size > self._gap_tolerance:
                # Gap detected
                logger.warning(
                    f"{self.EXCHANGE} sequence gap: "
                    f"expected {expected_seq}, got {seq} (gap of {gap_size})"
                )

                self._total_gaps += 1
                self._last_gap_time = time.time()
                self._gap_sizes.append(gap_size)

                # Keep only last 100 gap sizes
                if len(self._gap_sizes) > 100:
                    self._gap_sizes = self._gap_sizes[-100:]

                # Update last_seq to continue tracking from the gapped value
                self._last_seq = seq

                return True

            elif gap_size < 0:
                # Out-of-order or duplicate message
                logger.debug(
                    f"{self.EXCHANGE} out-of-order: "
                    f"expected {expected_seq}, got {seq}"
                )
                return False

            # Update last_seq for normal messages
            self._last_seq = seq
            return False

    def get_gap_metrics(self) -> Dict:
        """Get sequence gap metrics."""
        with self._lock:
            return {
                "total_gaps": self._total_gaps,
                "last_gap_time": self._last_gap_time,
                "gap_sizes": list(self._gap_sizes),
                "average_gap_size": (
                    sum(self._gap_sizes) / len(self._gap_sizes)
                    if self._gap_sizes
                    else 0
                ),
            }

    async def _async_sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while self._running and time.time() < end:
            await asyncio.sleep(0.2)


# ── Kraken ───────────────────────────────────────────────────────────────

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"


class KrakenL2Feed(ExchangeL2Feed):
    EXCHANGE = "kraken"

    def __init__(self):
        super().__init__()
        self._bids: Dict[float, float] = {}
        self._asks: Dict[float, float] = {}

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    KRAKEN_WS_URL, ping_interval=30, ping_timeout=10
                ) as ws:
                    sub = {
                        "method": "subscribe",
                        "params": {
                            "channel": "book",
                            "symbol": ["BTC/USD"],
                            "depth": 10,
                        },
                    }
                    await ws.send(json.dumps(sub))
                    logger.info("Kraken L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            channel = msg.get("channel")
                            if channel != "book":
                                continue
                            data = msg.get("data", [])
                            if not data:
                                continue
                            book = data[0]

                            mtype = msg.get("type", "")
                            if mtype == "snapshot":
                                self._bids.clear()
                                self._asks.clear()
                                for entry in book.get("bids", []):
                                    self._bids[float(entry["price"])] = float(entry["qty"])
                                for entry in book.get("asks", []):
                                    self._asks[float(entry["price"])] = float(entry["qty"])
                            elif mtype == "update":
                                for entry in book.get("bids", []):
                                    p, q = float(entry["price"]), float(entry["qty"])
                                    if q == 0:
                                        self._bids.pop(p, None)
                                    else:
                                        self._bids[p] = q
                                for entry in book.get("asks", []):
                                    p, q = float(entry["price"]), float(entry["qty"])
                                    if q == 0:
                                        self._asks.pop(p, None)
                                    else:
                                        self._asks[p] = q

                            if self._bids and self._asks:
                                best_bid = max(self._bids)
                                best_ask = min(self._asks)
                                bid_depth = sum(self._bids.values())
                                ask_depth = sum(self._asks.values())
                                self._update_bbo(best_bid, best_ask, bid_depth, ask_depth)

                        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                            continue

            except Exception as e:
                logger.error("Kraken L2 error: %s", e)
                self._connected = False
                self._bids.clear()
                self._asks.clear()
                if self._running:
                    await self._async_sleep(2.0)


# ── Coinbase ─────────────────────────────────────────────────────────────

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"


class CoinbaseL2Feed(ExchangeL2Feed):
    EXCHANGE = "coinbase"
    SUPPORTS_SEQUENCE = True  # Coinbase provides sequence numbers in level2_batch

    def __init__(self, enable_sequence_validation: bool = False, gap_tolerance: int = 0):
        super().__init__(enable_sequence_validation, gap_tolerance)
        self._bids: Dict[float, float] = {}
        self._asks: Dict[float, float] = {}

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    COINBASE_WS_URL,
                    ping_interval=30,
                    ping_timeout=10,
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
                                self._bids.clear()
                                self._asks.clear()
                                for p, q in msg.get("bids", []):
                                    self._bids[float(p)] = float(q)
                                for p, q in msg.get("asks", []):
                                    self._asks[float(p)] = float(q)

                                # Sequence validation on snapshot
                                if "sequence" in msg:
                                    seq = msg["sequence"]
                                    if self._check_sequence_gap(seq):
                                        # Gap detected - reconnect will happen automatically
                                        logger.error("Coinbase L2 gap on snapshot, reconnecting...")
                                        break

                            elif mtype == "l2update":
                                # Sequence validation on updates
                                if "sequence" in msg:
                                    seq = msg["sequence"]
                                    if self._check_sequence_gap(seq):
                                        # Gap detected - clear book and reconnect
                                        logger.error("Coinbase L2 gap on update, reconnecting...")
                                        self._bids.clear()
                                        self._asks.clear()
                                        break

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

                            if self._bids and self._asks:
                                best_bid = max(self._bids)
                                best_ask = min(self._asks)
                                bid_depth = sum(self._bids.values())
                                ask_depth = sum(self._asks.values())
                                self._update_bbo(best_bid, best_ask, bid_depth, ask_depth)

                        except (json.JSONDecodeError, KeyError, ValueError):
                            continue

            except Exception as e:
                logger.error("Coinbase L2 error: %s", e)
                self._connected = False
                self._bids.clear()
                self._asks.clear()
                if self._running:
                    await self._async_sleep(2.0)


# ── Bitstamp ─────────────────────────────────────────────────────────────

BITSTAMP_WS_URL = "wss://ws.bitstamp.net"


class BitstampL2Feed(ExchangeL2Feed):
    EXCHANGE = "bitstamp"

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    BITSTAMP_WS_URL, ping_interval=30, ping_timeout=10
                ) as ws:
                    sub = {
                        "event": "bts:subscribe",
                        "data": {"channel": "order_book_btcusd"},
                    }
                    await ws.send(json.dumps(sub))
                    logger.info("Bitstamp L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            event = msg.get("event", "")
                            if event != "data":
                                continue
                            data = msg.get("data", {})
                            bids = data.get("bids", [])
                            asks = data.get("asks", [])
                            if not bids or not asks:
                                continue

                            best_bid = float(bids[0][0])
                            best_ask = float(asks[0][0])
                            if best_bid > 0 and best_ask > 0:
                                # Calculate total depth from top N levels (Bitstamp sends ~100 levels)
                                bid_depth = sum(float(b[1]) for b in bids[:20])  # Top 20 levels
                                ask_depth = sum(float(a[1]) for a in asks[:20])
                                self._update_bbo(best_bid, best_ask, bid_depth, ask_depth)

                        except (json.JSONDecodeError, KeyError, ValueError, IndexError):
                            continue

            except Exception as e:
                logger.error("Bitstamp L2 error: %s", e)
                self._connected = False
                if self._running:
                    await self._async_sleep(2.0)


# ── Gemini ───────────────────────────────────────────────────────────────

GEMINI_WS_URL = "wss://api.gemini.com/v2/marketdata"


class GeminiL2Feed(ExchangeL2Feed):
    EXCHANGE = "gemini"

    def __init__(self):
        super().__init__()
        self._bids: Dict[float, float] = {}
        self._asks: Dict[float, float] = {}

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    GEMINI_WS_URL, ping_interval=30, ping_timeout=10
                ) as ws:
                    sub = {
                        "type": "subscribe",
                        "subscriptions": [
                            {"name": "l2", "symbols": ["BTCUSD"]}
                        ],
                    }
                    await ws.send(json.dumps(sub))
                    logger.info("Gemini L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            mtype = msg.get("type", "")

                            if mtype == "l2_updates":
                                changes = msg.get("changes", [])
                                for change in changes:
                                    # ["buy"/"sell", "price", "qty"]
                                    side = change[0]
                                    price = float(change[1])
                                    qty = float(change[2])
                                    if side == "buy":
                                        if qty == 0:
                                            self._bids.pop(price, None)
                                        else:
                                            self._bids[price] = qty
                                    elif side == "sell":
                                        if qty == 0:
                                            self._asks.pop(price, None)
                                        else:
                                            self._asks[price] = qty

                                if self._bids and self._asks:
                                    best_bid = max(self._bids)
                                    best_ask = min(self._asks)
                                    bid_depth = sum(self._bids.values())
                                    ask_depth = sum(self._asks.values())
                                    self._update_bbo(best_bid, best_ask, bid_depth, ask_depth)

                        except (json.JSONDecodeError, KeyError, ValueError, IndexError):
                            continue

            except Exception as e:
                logger.error("Gemini L2 error: %s", e)
                self._connected = False
                self._bids.clear()
                self._asks.clear()
                if self._running:
                    await self._async_sleep(2.0)


# ── Crypto.com ───────────────────────────────────────────────────────────

CRYPTOCOM_WS_URL = "wss://stream.crypto.com/exchange/v1/market"


class CryptoComL2Feed(ExchangeL2Feed):
    EXCHANGE = "crypto.com"

    def __init__(self):
        super().__init__()
        self._bids: Dict[float, float] = {}
        self._asks: Dict[float, float] = {}

    async def _ws_loop(self) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets package required")
            return

        while self._running:
            try:
                async with websockets.connect(
                    CRYPTOCOM_WS_URL, ping_interval=30, ping_timeout=10
                ) as ws:
                    sub = {
                        "id": 1,
                        "method": "subscribe",
                        "params": {"channels": ["book.BTC_USD.50"]},
                    }
                    await ws.send(json.dumps(sub))
                    logger.info("Crypto.com L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            method = msg.get("method")
                            if method not in ("subscribe", "subscribe.book.BTC_USD.50"):
                                # Also handle result messages
                                result = msg.get("result")
                                if not result:
                                    continue
                                data = result.get("data", [])
                            else:
                                result = msg.get("result", {})
                                data = result.get("data", [])

                            if not data:
                                continue

                            for entry in data:
                                bids = entry.get("bids", [])
                                asks = entry.get("asks", [])

                                # Check if this is a snapshot (has update_type or is first)
                                update_type = entry.get("update_type", "")
                                if update_type == "snapshot" or not self._bids:
                                    self._bids.clear()
                                    self._asks.clear()

                                for b in bids:
                                    # [price, qty, num_orders]
                                    p, q = float(b[0]), float(b[1])
                                    if q == 0:
                                        self._bids.pop(p, None)
                                    else:
                                        self._bids[p] = q

                                for a in asks:
                                    p, q = float(a[0]), float(a[1])
                                    if q == 0:
                                        self._asks.pop(p, None)
                                    else:
                                        self._asks[p] = q

                            if self._bids and self._asks:
                                best_bid = max(self._bids)
                                best_ask = min(self._asks)
                                bid_depth = sum(self._bids.values())
                                ask_depth = sum(self._asks.values())
                                self._update_bbo(best_bid, best_ask, bid_depth, ask_depth)

                        except (json.JSONDecodeError, KeyError, ValueError, IndexError, TypeError):
                            continue

            except Exception as e:
                logger.error("Crypto.com L2 error: %s", e)
                self._connected = False
                self._bids.clear()
                self._asks.clear()
                if self._running:
                    await self._async_sleep(2.0)


# ── Registry ─────────────────────────────────────────────────────────────

FEED_CLASSES = {
    "kraken": KrakenL2Feed,
    "coinbase": CoinbaseL2Feed,
    "bitstamp": BitstampL2Feed,
    "gemini": GeminiL2Feed,
    "crypto.com": CryptoComL2Feed,
}

ALL_EXCHANGES = list(FEED_CLASSES.keys())
