#!/usr/bin/env python3
"""
Analyze crypto scalp live trading logs to extract trades and calculate PnL.
"""

import re
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Trade:
    """Completed trade with entry and exit."""
    ticker: str
    side: str  # YES or NO
    entry_price: int  # cents
    exit_price: int  # cents
    contracts: int
    entry_time: datetime
    exit_time: datetime
    entry_source: str  # binance or coinbase
    entry_order_id: str
    exit_order_id: str

    @property
    def hold_seconds(self) -> float:
        """Hold time in seconds."""
        return (self.exit_time - self.entry_time).total_seconds()

    @property
    def pnl_cents(self) -> int:
        """PnL in cents."""
        if self.side == "YES":
            # For YES: profit when price goes up
            return (self.exit_price - self.entry_price) * self.contracts
        else:
            # For NO: profit when price goes down
            return (self.entry_price - self.exit_price) * self.contracts

    @property
    def pnl_dollars(self) -> float:
        """PnL in dollars."""
        return self.pnl_cents / 100.0


@dataclass
class Entry:
    """Entry order that hasn't been exited yet."""
    ticker: str
    side: str
    price: int
    contracts: int
    time: datetime
    source: str
    order_id: str


def parse_timestamp(log_line: str) -> Optional[datetime]:
    """Extract timestamp from log line."""
    match = re.match(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', log_line)
    if match:
        return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
    return None


def parse_entry(log_line: str) -> Optional[Entry]:
    """Parse ENTRY log line."""
    # ENTRY [binance]: YES KXBTC15M-26MAR010130-30 1 @ 64c (order f8e3091d-70d8-478f-8302-2cca916a087d)
    match = re.search(
        r'ENTRY \[(\w+)\]: (YES|NO) ([\w-]+) (\d+) @ (\d+)c \(order ([a-f0-9-]+)\)',
        log_line
    )
    if not match:
        return None

    source, side, ticker, contracts, price, order_id = match.groups()
    timestamp = parse_timestamp(log_line)
    if not timestamp:
        return None

    return Entry(
        ticker=ticker,
        side=side,
        price=int(price),
        contracts=int(contracts),
        time=timestamp,
        source=source,
        order_id=order_id
    )


def parse_exit(log_line: str) -> Optional[Tuple[str, str, int, int, datetime, str]]:
    """Parse EXIT log line. Returns (side, ticker, exit_price, entry_price, timestamp, order_id)."""
    # EXIT: YES KXBTC15M-26MAR010130-30 1 @ 46c (was 64c) | order ddbf5b16-7418-4505-b6b1-d447bf3d029e
    match = re.search(
        r'EXIT: (YES|NO) ([\w-]+) (\d+) @ (\d+)c \(was (\d+)c\) \| order ([a-f0-9-]+)',
        log_line
    )
    if not match:
        return None

    side, ticker, contracts, exit_price, entry_price, order_id = match.groups()
    timestamp = parse_timestamp(log_line)
    if not timestamp:
        return None

    return (side, ticker, int(exit_price), int(entry_price), timestamp, order_id)


def parse_logs(log_files: List[Path]) -> Tuple[List[Trade], List[Entry]]:
    """Parse log files and extract completed trades and open entries."""
    entries: Dict[Tuple[str, str, int], Entry] = {}  # (ticker, side, entry_price) -> Entry
    trades: List[Trade] = []

    # Read all log lines from all files
    all_lines = []
    for log_file in log_files:
        with open(log_file) as f:
            all_lines.extend(f.readlines())

    for line in all_lines:
        # Parse entry
        entry = parse_entry(line)
        if entry:
            key = (entry.ticker, entry.side, entry.price)
            entries[key] = entry
            continue

        # Parse exit
        exit_data = parse_exit(line)
        if exit_data:
            side, ticker, exit_price, entry_price, exit_time, exit_order_id = exit_data

            # Find matching entry
            key = (ticker, side, entry_price)
            if key in entries:
                entry = entries.pop(key)

                # Create completed trade
                trade = Trade(
                    ticker=ticker,
                    side=side,
                    entry_price=entry.price,
                    exit_price=exit_price,
                    contracts=entry.contracts,
                    entry_time=entry.time,
                    exit_time=exit_time,
                    entry_source=entry.source,
                    entry_order_id=entry.order_id,
                    exit_order_id=exit_order_id
                )
                trades.append(trade)

    # Remaining entries are open positions
    open_entries = list(entries.values())

    return trades, open_entries


def analyze_trades(trades: List[Trade]) -> Dict:
    """Analyze completed trades and return statistics."""
    if not trades:
        return {
            'total_trades': 0,
            'winners': 0,
            'losers': 0,
            'breakeven': 0,
            'win_rate': 0.0,
            'total_pnl_cents': 0,
            'total_pnl_dollars': 0.0,
            'avg_pnl_cents': 0.0,
            'avg_pnl_dollars': 0.0,
            'avg_hold_seconds': 0.0,
            'yes_trades': 0,
            'no_trades': 0,
            'yes_pnl_cents': 0,
            'no_pnl_cents': 0,
        }

    winners = [t for t in trades if t.pnl_cents > 0]
    losers = [t for t in trades if t.pnl_cents < 0]
    breakeven = [t for t in trades if t.pnl_cents == 0]

    yes_trades = [t for t in trades if t.side == "YES"]
    no_trades = [t for t in trades if t.side == "NO"]

    total_pnl_cents = sum(t.pnl_cents for t in trades)
    avg_hold_seconds = sum(t.hold_seconds for t in trades) / len(trades)

    return {
        'total_trades': len(trades),
        'winners': len(winners),
        'losers': len(losers),
        'breakeven': len(breakeven),
        'win_rate': len(winners) / len(trades) * 100,
        'total_pnl_cents': total_pnl_cents,
        'total_pnl_dollars': total_pnl_cents / 100.0,
        'avg_pnl_cents': total_pnl_cents / len(trades),
        'avg_pnl_dollars': total_pnl_cents / len(trades) / 100.0,
        'avg_hold_seconds': avg_hold_seconds,
        'yes_trades': len(yes_trades),
        'no_trades': len(no_trades),
        'yes_pnl_cents': sum(t.pnl_cents for t in yes_trades),
        'no_pnl_cents': sum(t.pnl_cents for t in no_trades),
        'binance_trades': len([t for t in trades if t.entry_source == 'binance']),
        'coinbase_trades': len([t for t in trades if t.entry_source == 'coinbase']),
        'binance_pnl_cents': sum(t.pnl_cents for t in trades if t.entry_source == 'binance'),
        'coinbase_pnl_cents': sum(t.pnl_cents for t in trades if t.entry_source == 'coinbase'),
    }


def main():
    # Log files
    log_files = [
        Path('/Users/raine/tradingutils/logs/crypto-scalp_live_20260228_221441.log'),
        Path('/Users/raine/tradingutils/logs/crypto-scalp_live_20260228_222333.log'),
    ]

    # Parse logs
    trades, open_entries = parse_logs(log_files)

    # Analyze
    stats = analyze_trades(trades)

    print("=" * 80)
    print("CRYPTO SCALP LIVE TRADING ANALYSIS - February 28, 2026")
    print("=" * 80)
    print()

    print("SUMMARY STATISTICS")
    print("-" * 80)
    print(f"Total completed trades: {stats['total_trades']}")
    print(f"Winners: {stats['winners']} ({stats['win_rate']:.1f}%)")
    print(f"Losers: {stats['losers']}")
    print(f"Breakeven: {stats['breakeven']}")
    print()

    print(f"Total P&L: {stats['total_pnl_cents']:+}c (${stats['total_pnl_dollars']:+.2f})")
    print(f"Average P&L per trade: {stats['avg_pnl_cents']:+.1f}c (${stats['avg_pnl_dollars']:+.3f})")
    print(f"Average hold time: {stats['avg_hold_seconds']:.1f} seconds ({stats['avg_hold_seconds']/60:.1f} minutes)")
    print()

    print("BREAKDOWN BY DIRECTION")
    print("-" * 80)
    print(f"YES trades: {stats['yes_trades']} | P&L: {stats['yes_pnl_cents']:+}c (${stats['yes_pnl_cents']/100:+.2f})")
    print(f"NO trades: {stats['no_trades']} | P&L: {stats['no_pnl_cents']:+}c (${stats['no_pnl_cents']/100:+.2f})")
    print()

    print("BREAKDOWN BY SIGNAL SOURCE")
    print("-" * 80)
    print(f"Binance signals: {stats['binance_trades']} | P&L: {stats['binance_pnl_cents']:+}c (${stats['binance_pnl_cents']/100:+.2f})")
    print(f"Coinbase signals: {stats['coinbase_trades']} | P&L: {stats['coinbase_pnl_cents']:+}c (${stats['coinbase_pnl_cents']/100:+.2f})")
    print()

    print("TRADE-BY-TRADE DETAILS")
    print("-" * 80)
    for i, trade in enumerate(trades, 1):
        hold_min = trade.hold_seconds / 60
        print(f"{i}. {trade.entry_time.strftime('%H:%M:%S')} [{trade.entry_source}] "
              f"{trade.side} {trade.ticker} | "
              f"{trade.entry_price}c → {trade.exit_price}c | "
              f"P&L: {trade.pnl_cents:+}c (${trade.pnl_dollars:+.2f}) | "
              f"Hold: {hold_min:.1f}min")
    print()

    if open_entries:
        print("OPEN POSITIONS (NOT EXITED)")
        print("-" * 80)
        for entry in open_entries:
            print(f"  {entry.time.strftime('%H:%M:%S')} [{entry.source}] "
                  f"{entry.side} {entry.ticker} @ {entry.price}c | "
                  f"Order: {entry.order_id}")
        print()

    # Pattern analysis
    print("PATTERN ANALYSIS")
    print("-" * 80)

    # Check if all losses are consistent
    losers = [t for t in trades if t.pnl_cents < 0]
    if losers:
        loss_amounts = [t.pnl_cents for t in losers]
        unique_losses = set(loss_amounts)
        if len(unique_losses) == 1:
            print(f"⚠️  ALL LOSSES ARE IDENTICAL: {loss_amounts[0]}c")
            print(f"   This suggests systematic slippage or fees")
        else:
            print(f"Loss amounts vary: {sorted(unique_losses)}")
        print()

    # Check hold times
    short_trades = [t for t in trades if t.hold_seconds < 30]
    print(f"Trades held < 30s: {len(short_trades)} / {len(trades)}")

    if trades:
        min_hold = min(t.hold_seconds for t in trades)
        max_hold = max(t.hold_seconds for t in trades)
        print(f"Hold time range: {min_hold:.1f}s - {max_hold:.1f}s")
    print()

    print("=" * 80)


if __name__ == '__main__':
    main()
