#!/usr/bin/env python3
"""Trade log analyzer and validator.

This tool analyzes trade logs to:
- Verify position accuracy
- Reconcile P&L calculations
- Detect anomalies and errors
- Generate trade summaries

Usage:
    python tools/validate_trades.py trades.json
    python tools/validate_trades.py --from-engine <engine_status.json>
    python tools/validate_trades.py --live <ticker>
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class Trade:
    """Represents a single trade."""

    ticker: str
    order_id: str
    side: str  # "BID" or "ASK"
    price: float
    size: int
    timestamp: datetime
    fill_type: str = "normal"  # normal, force_close

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "order_id": self.order_id,
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "timestamp": self.timestamp.isoformat(),
            "fill_type": self.fill_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Trade":
        return cls(
            ticker=data["ticker"],
            order_id=data["order_id"],
            side=data["side"],
            price=data["price"],
            size=data["size"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            fill_type=data.get("fill_type", "normal"),
        )


@dataclass
class PositionTracker:
    """Tracks position through trades."""

    contracts: int = 0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0
    total_volume: int = 0
    trade_count: int = 0

    def update(self, trade: Trade) -> float:
        """Update position with trade, return realized P&L from this trade."""
        pnl = 0.0

        if trade.side == "BID":
            # Buying
            if self.contracts >= 0:
                # Adding to long or opening long
                new_contracts = self.contracts + trade.size
                if self.contracts == 0:
                    self.avg_entry_price = trade.price
                else:
                    # Weighted average
                    total_cost = (self.contracts * self.avg_entry_price +
                                  trade.size * trade.price)
                    self.avg_entry_price = total_cost / new_contracts
                self.contracts = new_contracts
            else:
                # Covering short
                cover_size = min(trade.size, abs(self.contracts))
                pnl = cover_size * (self.avg_entry_price - trade.price)
                self.realized_pnl += pnl
                self.contracts += trade.size

                if self.contracts > 0:
                    # Flipped to long
                    self.avg_entry_price = trade.price
        else:
            # Selling (ASK)
            if self.contracts <= 0:
                # Adding to short or opening short
                new_contracts = self.contracts - trade.size
                if self.contracts == 0:
                    self.avg_entry_price = trade.price
                else:
                    total_cost = (abs(self.contracts) * self.avg_entry_price +
                                  trade.size * trade.price)
                    self.avg_entry_price = total_cost / abs(new_contracts)
                self.contracts = new_contracts
            else:
                # Closing long
                close_size = min(trade.size, self.contracts)
                pnl = close_size * (trade.price - self.avg_entry_price)
                self.realized_pnl += pnl
                self.contracts -= trade.size

                if self.contracts < 0:
                    # Flipped to short
                    self.avg_entry_price = trade.price

        self.total_volume += trade.size
        self.trade_count += 1
        return pnl


@dataclass
class ValidationResult:
    """Result of trade validation."""

    is_valid: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def add_error(self, error: str) -> None:
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)


class TradeValidator:
    """Validates trade logs for consistency and accuracy."""

    def __init__(self):
        self.trades: list[Trade] = []
        self.positions: dict[str, PositionTracker] = {}

    def load_trades(self, trades_file: Path) -> None:
        """Load trades from JSON file."""
        with open(trades_file) as f:
            data = json.load(f)

        if isinstance(data, list):
            self.trades = [Trade.from_dict(t) for t in data]
        elif "trades" in data:
            self.trades = [Trade.from_dict(t) for t in data["trades"]]
        else:
            raise ValueError("Invalid trade file format")

        # Sort by timestamp
        self.trades.sort(key=lambda t: t.timestamp)

    def add_trade(self, trade: Trade) -> None:
        """Add a single trade."""
        self.trades.append(trade)

    def validate(self) -> ValidationResult:
        """Run all validations and return result."""
        result = ValidationResult()

        # Reset positions
        self.positions = {}

        # Process each trade
        for trade in self.trades:
            self._validate_trade(trade, result)

            if trade.ticker not in self.positions:
                self.positions[trade.ticker] = PositionTracker()

            self.positions[trade.ticker].update(trade)

        # Post-validation checks
        self._check_final_positions(result)
        self._generate_summary(result)

        return result

    def _validate_trade(self, trade: Trade, result: ValidationResult) -> None:
        """Validate a single trade."""
        # Price bounds
        if not 0 < trade.price < 1:
            result.add_error(
                f"Trade {trade.order_id}: price {trade.price} out of bounds"
            )

        # Size positive
        if trade.size <= 0:
            result.add_error(
                f"Trade {trade.order_id}: size {trade.size} not positive"
            )

        # Valid side
        if trade.side not in ("BID", "ASK"):
            result.add_error(
                f"Trade {trade.order_id}: invalid side {trade.side}"
            )

        # Ticker present
        if not trade.ticker:
            result.add_error(
                f"Trade {trade.order_id}: missing ticker"
            )

        # Check for suspicious prices
        if trade.price < 0.05 or trade.price > 0.95:
            result.add_warning(
                f"Trade {trade.order_id}: extreme price {trade.price:.4f}"
            )

    def _check_final_positions(self, result: ValidationResult) -> None:
        """Check final positions for issues."""
        for ticker, pos in self.positions.items():
            if abs(pos.contracts) > 1000:
                result.add_warning(
                    f"{ticker}: Large final position {pos.contracts}"
                )

            if pos.realized_pnl < -100:
                result.add_warning(
                    f"{ticker}: Large realized loss ${pos.realized_pnl:.2f}"
                )

    def _generate_summary(self, result: ValidationResult) -> None:
        """Generate validation summary."""
        total_trades = len(self.trades)
        total_volume = sum(t.size for t in self.trades)
        total_pnl = sum(p.realized_pnl for p in self.positions.values())

        tickers = set(t.ticker for t in self.trades)

        buy_trades = sum(1 for t in self.trades if t.side == "BID")
        sell_trades = sum(1 for t in self.trades if t.side == "ASK")

        result.summary = {
            "total_trades": total_trades,
            "total_volume": total_volume,
            "total_realized_pnl": round(total_pnl, 4),
            "tickers_traded": list(tickers),
            "buy_trades": buy_trades,
            "sell_trades": sell_trades,
            "positions": {
                ticker: {
                    "contracts": pos.contracts,
                    "avg_entry": round(pos.avg_entry_price, 4),
                    "realized_pnl": round(pos.realized_pnl, 4),
                    "volume": pos.total_volume,
                    "trades": pos.trade_count,
                }
                for ticker, pos in self.positions.items()
            },
        }


class PnLReconciler:
    """Reconciles P&L calculations."""

    def __init__(self, trades: list[Trade]):
        self.trades = trades

    def reconcile(self, expected_pnl: float, tolerance: float = 0.01) -> dict:
        """Reconcile against expected P&L."""
        tracker = PositionTracker()

        trade_pnl = []
        for trade in self.trades:
            pnl = tracker.update(trade)
            if pnl != 0:
                trade_pnl.append({
                    "order_id": trade.order_id,
                    "pnl": round(pnl, 4),
                    "cumulative": round(tracker.realized_pnl, 4),
                })

        calculated_pnl = tracker.realized_pnl
        difference = calculated_pnl - expected_pnl

        return {
            "calculated_pnl": round(calculated_pnl, 4),
            "expected_pnl": round(expected_pnl, 4),
            "difference": round(difference, 4),
            "matches": abs(difference) < tolerance,
            "trade_pnl_breakdown": trade_pnl,
        }


class AnomalyDetector:
    """Detects anomalies in trading patterns."""

    def __init__(self, trades: list[Trade]):
        self.trades = trades

    def detect(self) -> list[dict]:
        """Detect anomalies in trade patterns."""
        anomalies = []

        # Check for rapid trades
        self._check_rapid_trades(anomalies)

        # Check for wash trades
        self._check_wash_trades(anomalies)

        # Check for price anomalies
        self._check_price_anomalies(anomalies)

        return anomalies

    def _check_rapid_trades(self, anomalies: list) -> None:
        """Check for suspiciously rapid trading."""
        if len(self.trades) < 2:
            return

        sorted_trades = sorted(self.trades, key=lambda t: t.timestamp)

        for i in range(1, len(sorted_trades)):
            t1 = sorted_trades[i - 1]
            t2 = sorted_trades[i]
            delta = (t2.timestamp - t1.timestamp).total_seconds()

            if delta < 0.1:  # Less than 100ms apart
                anomalies.append({
                    "type": "rapid_trade",
                    "severity": "warning",
                    "description": f"Trades {t1.order_id} and {t2.order_id} "
                                   f"only {delta:.3f}s apart",
                    "trades": [t1.order_id, t2.order_id],
                })

    def _check_wash_trades(self, anomalies: list) -> None:
        """Check for potential wash trades."""
        sorted_trades = sorted(self.trades, key=lambda t: t.timestamp)

        for i in range(1, len(sorted_trades)):
            t1 = sorted_trades[i - 1]
            t2 = sorted_trades[i]

            # Same ticker, opposite sides, same size, similar price
            if (t1.ticker == t2.ticker and
                t1.side != t2.side and
                t1.size == t2.size and
                abs(t1.price - t2.price) < 0.01):

                delta = (t2.timestamp - t1.timestamp).total_seconds()
                if delta < 1.0:  # Within 1 second
                    anomalies.append({
                        "type": "potential_wash",
                        "severity": "info",
                        "description": f"Potential wash trade: {t1.order_id} "
                                       f"and {t2.order_id}",
                        "trades": [t1.order_id, t2.order_id],
                    })

    def _check_price_anomalies(self, anomalies: list) -> None:
        """Check for price anomalies."""
        by_ticker: dict[str, list[Trade]] = {}
        for trade in self.trades:
            if trade.ticker not in by_ticker:
                by_ticker[trade.ticker] = []
            by_ticker[trade.ticker].append(trade)

        for ticker, trades in by_ticker.items():
            prices = [t.price for t in trades]
            if len(prices) < 2:
                continue

            avg_price = sum(prices) / len(prices)

            for trade in trades:
                deviation = abs(trade.price - avg_price) / avg_price
                if deviation > 0.5:  # More than 50% from average
                    anomalies.append({
                        "type": "price_outlier",
                        "severity": "warning",
                        "description": f"Trade {trade.order_id} price "
                                       f"{trade.price:.4f} is {deviation:.0%} "
                                       f"from average {avg_price:.4f}",
                        "trade": trade.order_id,
                    })


def print_validation_result(result: ValidationResult) -> None:
    """Print validation result to console."""
    print("\n" + "=" * 60)
    print("TRADE VALIDATION REPORT")
    print("=" * 60)

    if result.is_valid:
        print("\n[PASS] All validations passed")
    else:
        print(f"\n[FAIL] Validation failed with {len(result.errors)} errors")

    if result.errors:
        print("\nERRORS:")
        for err in result.errors:
            print(f"  - {err}")

    if result.warnings:
        print("\nWARNINGS:")
        for warn in result.warnings:
            print(f"  - {warn}")

    print("\nSUMMARY:")
    summary = result.summary
    print(f"  Total Trades: {summary.get('total_trades', 0)}")
    print(f"  Total Volume: {summary.get('total_volume', 0)}")
    print(f"  Buy Trades: {summary.get('buy_trades', 0)}")
    print(f"  Sell Trades: {summary.get('sell_trades', 0)}")
    print(f"  Realized P&L: ${summary.get('total_realized_pnl', 0):.4f}")
    print(f"  Tickers: {', '.join(summary.get('tickers_traded', []))}")

    if "positions" in summary:
        print("\nPOSITIONS:")
        for ticker, pos in summary["positions"].items():
            print(f"  {ticker}:")
            print(f"    Contracts: {pos['contracts']}")
            print(f"    Avg Entry: {pos['avg_entry']:.4f}")
            print(f"    Realized P&L: ${pos['realized_pnl']:.4f}")
            print(f"    Volume: {pos['volume']}")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Validate trade logs and reconcile P&L"
    )
    parser.add_argument(
        "trades_file",
        nargs="?",
        help="Path to trades JSON file"
    )
    parser.add_argument(
        "--expected-pnl",
        type=float,
        help="Expected P&L for reconciliation"
    )
    parser.add_argument(
        "--detect-anomalies",
        action="store_true",
        help="Detect trading anomalies"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for results (JSON)"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run with demo data"
    )

    args = parser.parse_args()

    if args.demo:
        # Create demo trades
        trades = [
            Trade("DEMO", "o1", "BID", 0.45, 10, datetime.now()),
            Trade("DEMO", "o2", "BID", 0.46, 10, datetime.now()),
            Trade("DEMO", "o3", "ASK", 0.52, 15, datetime.now()),
            Trade("DEMO", "o4", "ASK", 0.48, 5, datetime.now()),
        ]

        validator = TradeValidator()
        for t in trades:
            validator.add_trade(t)

    elif args.trades_file:
        validator = TradeValidator()
        validator.load_trades(Path(args.trades_file))
    else:
        parser.print_help()
        return 1

    # Run validation
    result = validator.validate()
    print_validation_result(result)

    # Reconcile if expected P&L provided
    if args.expected_pnl is not None:
        reconciler = PnLReconciler(validator.trades)
        recon_result = reconciler.reconcile(args.expected_pnl)

        print("\nP&L RECONCILIATION:")
        print(f"  Calculated: ${recon_result['calculated_pnl']:.4f}")
        print(f"  Expected: ${recon_result['expected_pnl']:.4f}")
        print(f"  Difference: ${recon_result['difference']:.4f}")
        print(f"  Matches: {recon_result['matches']}")

    # Detect anomalies
    if args.detect_anomalies:
        detector = AnomalyDetector(validator.trades)
        anomalies = detector.detect()

        print("\nANOMALY DETECTION:")
        if anomalies:
            for anom in anomalies:
                print(f"  [{anom['severity'].upper()}] {anom['type']}")
                print(f"    {anom['description']}")
        else:
            print("  No anomalies detected")

    # Save output
    if args.output:
        output = {
            "validation": {
                "is_valid": result.is_valid,
                "errors": result.errors,
                "warnings": result.warnings,
                "summary": result.summary,
            }
        }

        if args.expected_pnl is not None:
            output["reconciliation"] = recon_result

        if args.detect_anomalies:
            output["anomalies"] = anomalies

        with open(args.output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults saved to {args.output}")

    return 0 if result.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
