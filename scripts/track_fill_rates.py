#!/usr/bin/env python3
"""Track fill rates by direction during live crypto scalp trading.

Usage:
  1. Start crypto scalp strategy in one terminal
  2. Run this script in another: python3 scripts/track_fill_rates.py
  3. Watch real-time stats as signals come in

This parses the strategy logs to extract signals and fills, tracking:
- Fill rate by direction (YES vs NO)
- Fill rate by entry price (ITM vs OTM)
- Average signal-to-fill time
- Repricing speed (signal price vs market price at order time)
"""

import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class SignalEvent:
    """A trading signal."""
    timestamp: datetime
    side: str  # "YES" or "NO"
    ticker: str
    entry_price: int  # cents
    spot_delta: float  # USD

    # Filled data (if filled)
    filled: bool = False
    fill_time: Optional[datetime] = None
    fill_price: Optional[int] = None

    # Repricing data
    market_moved: bool = False  # Did market move between signal and order?
    price_gap: Optional[int] = None  # Cents moved

    @property
    def fill_latency_ms(self) -> Optional[float]:
        """Time from signal to fill in milliseconds."""
        if not self.filled or not self.fill_time:
            return None
        return (self.fill_time - self.timestamp).total_seconds() * 1000

    @property
    def moneyness(self) -> str:
        """Classify as ITM, ATM, or OTM."""
        if self.entry_price >= 60:
            return "ITM"
        elif self.entry_price >= 40:
            return "ATM"
        else:
            return "OTM"


class FillRateTracker:
    """Track fill rates by direction in real-time."""

    def __init__(self):
        self.signals: list[SignalEvent] = []
        self.signal_by_ticker: dict[str, SignalEvent] = {}

    def add_signal(self, timestamp: datetime, side: str, ticker: str,
                   entry_price: int, spot_delta: float) -> None:
        """Record a new signal."""
        signal = SignalEvent(
            timestamp=timestamp,
            side=side,
            ticker=ticker,
            entry_price=entry_price,
            spot_delta=spot_delta,
        )
        self.signals.append(signal)
        self.signal_by_ticker[ticker] = signal
        self._print_update(signal, "SIGNAL")

    def add_fill(self, timestamp: datetime, ticker: str, fill_price: int) -> None:
        """Record a fill for a signal."""
        if ticker not in self.signal_by_ticker:
            return  # Fill for unknown signal, ignore

        signal = self.signal_by_ticker[ticker]
        signal.filled = True
        signal.fill_time = timestamp
        signal.fill_price = fill_price
        self._print_update(signal, "FILLED")

    def add_timeout(self, ticker: str) -> None:
        """Record a timeout (no fill)."""
        if ticker not in self.signal_by_ticker:
            return

        signal = self.signal_by_ticker[ticker]
        self._print_update(signal, "TIMEOUT")

    def add_skip(self, timestamp: datetime, ticker: str, reason: str, price_gap: int) -> None:
        """Record a skipped signal (pre-flight check failed)."""
        if ticker not in self.signal_by_ticker:
            return

        signal = self.signal_by_ticker[ticker]
        signal.market_moved = True
        signal.price_gap = price_gap
        self._print_update(signal, f"SKIP ({reason})")

    def _print_update(self, signal: SignalEvent, event: str) -> None:
        """Print a single line update."""
        now = datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] {event:12s} | {signal.side:3s} @ {signal.entry_price:2d}¢ "
              f"| {signal.ticker} | {signal.spot_delta:+.1f}")

        if signal.filled and signal.fill_latency_ms:
            print(f"           └─ ✅ Filled in {signal.fill_latency_ms:.0f}ms @ {signal.fill_price}¢")

        self._print_stats()

    def _print_stats(self) -> None:
        """Print current statistics."""
        if not self.signals:
            return

        # Group by side
        yes_signals = [s for s in self.signals if s.side == "YES"]
        no_signals = [s for s in self.signals if s.side == "NO"]

        yes_filled = [s for s in yes_signals if s.filled]
        no_filled = [s for s in no_signals if s.filled]

        yes_rate = len(yes_filled) / len(yes_signals) * 100 if yes_signals else 0
        no_rate = len(no_filled) / len(no_signals) * 100 if no_signals else 0

        total_filled = len(yes_filled) + len(no_filled)
        total_rate = total_filled / len(self.signals) * 100

        print()
        print("="*80)
        print(f"YES: {len(yes_filled)}/{len(yes_signals)} fills ({yes_rate:.0f}%)  "
              f"| NO: {len(no_filled)}/{len(no_signals)} fills ({no_rate:.0f}%)  "
              f"| TOTAL: {total_filled}/{len(self.signals)} ({total_rate:.0f}%)")

        # Moneyness breakdown
        itm = [s for s in self.signals if s.moneyness == "ITM"]
        atm = [s for s in self.signals if s.moneyness == "ATM"]
        otm = [s for s in self.signals if s.moneyness == "OTM"]

        if itm:
            itm_filled = [s for s in itm if s.filled]
            print(f"ITM (60-75¢): {len(itm_filled)}/{len(itm)} ({len(itm_filled)/len(itm)*100:.0f}%)", end="")
        if atm:
            atm_filled = [s for s in atm if s.filled]
            print(f"  | ATM (40-59¢): {len(atm_filled)}/{len(atm)} ({len(atm_filled)/len(atm)*100:.0f}%)", end="")
        if otm:
            otm_filled = [s for s in otm if s.filled]
            print(f"  | OTM (25-39¢): {len(otm_filled)}/{len(otm)} ({len(otm_filled)/len(otm)*100:.0f}%)")
        else:
            print()

        # Average fill time
        fills = [s for s in self.signals if s.filled and s.fill_latency_ms]
        if fills:
            avg_latency = sum(s.fill_latency_ms for s in fills) / len(fills)
            print(f"Avg fill time: {avg_latency:.0f}ms")

        print("="*80)
        print()


def tail_log_file(log_path: str, tracker: FillRateTracker) -> None:
    """Tail log file and parse signals/fills."""

    # Regex patterns
    signal_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*SIGNAL.*: (YES|NO) (KXBTC\S+) \| '
        r'spot_delta=\$([+-]?\d+\.?\d*) \| entry=(\d+)c'
    )

    fill_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*ENTRY confirmed.*@ (\d+)c'
    )

    timeout_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*TIMEOUT'
    )

    skip_pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2}).*SKIP.*market moved (\d+)c'
    )

    print(f"Tailing {log_path}...")
    print("Waiting for signals...\n")

    try:
        with open(log_path, 'r') as f:
            # Seek to end
            f.seek(0, 2)

            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.1)
                    continue

                # Parse signal
                match = signal_pattern.search(line)
                if match:
                    time_str, side, ticker, spot_delta, entry_price = match.groups()
                    now = datetime.now()
                    timestamp = now.replace(
                        hour=int(time_str[:2]),
                        minute=int(time_str[3:5]),
                        second=int(time_str[6:8]),
                        microsecond=0
                    )

                    tracker.add_signal(
                        timestamp=timestamp,
                        side=side,
                        ticker=ticker,
                        entry_price=int(entry_price),
                        spot_delta=float(spot_delta),
                    )
                    continue

                # Parse fill
                match = fill_pattern.search(line)
                if match:
                    time_str, fill_price = match.groups()
                    # Extract ticker from line context (assume it's in the same line)
                    ticker_match = re.search(r'KXBTC\S+', line)
                    if ticker_match:
                        ticker = ticker_match.group()
                        now = datetime.now()
                        timestamp = now.replace(
                            hour=int(time_str[:2]),
                            minute=int(time_str[3:5]),
                            second=int(time_str[6:8]),
                            microsecond=0
                        )
                        tracker.add_fill(timestamp, ticker, int(fill_price))
                    continue

                # Parse timeout
                if timeout_pattern.search(line):
                    ticker_match = re.search(r'KXBTC\S+', line)
                    if ticker_match:
                        tracker.add_timeout(ticker_match.group())
                    continue

                # Parse skip
                match = skip_pattern.search(line)
                if match:
                    time_str, price_gap = match.groups()
                    ticker_match = re.search(r'KXBTC\S+', line)
                    if ticker_match:
                        now = datetime.now()
                        timestamp = now.replace(
                            hour=int(time_str[:2]),
                            minute=int(time_str[3:5]),
                            second=int(time_str[6:8]),
                            microsecond=0
                        )
                        tracker.add_skip(timestamp, ticker_match.group(), "market moved", int(price_gap))

    except KeyboardInterrupt:
        print("\n\n📊 Final Statistics:")
        print("="*80)
        tracker._print_stats()
        sys.exit(0)


if __name__ == "__main__":
    # Find latest log file
    import glob
    import os

    # Look for crypto scalp logs
    log_patterns = [
        "logs/crypto_scalp*.log",
        "logs/paper_scalp*.log",
        "logs/live_scalp*.log",
    ]

    log_files = []
    for pattern in log_patterns:
        log_files.extend(glob.glob(pattern))

    if not log_files:
        print("❌ No crypto scalp log files found")
        print("Run the crypto scalp strategy first!")
        sys.exit(1)

    # Use most recent
    log_path = max(log_files, key=os.path.getmtime)

    if len(sys.argv) > 1:
        log_path = sys.argv[1]

    tracker = FillRateTracker()
    tail_log_file(log_path, tracker)
