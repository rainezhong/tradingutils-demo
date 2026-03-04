"""Calibrator - validates fill time predictions against observed fill behavior."""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ObservedFill:
    """An observed fill from trade logs."""

    trade_id: str
    ticker: str
    side: str
    price: int
    size: int
    entry_timestamp: float
    fill_timestamp: float
    fill_time_seconds: float
    spread_at_entry: int


@dataclass
class CalibrationResult:
    """Calibration report comparing predicted vs actual fill behavior."""

    total_observations: int
    mean_fill_time_seconds: float
    median_fill_time_seconds: float

    # Calibration by time bucket: predicted P(fill within Xs) vs actual frequency
    calibration_30s: Tuple[float, float]  # (predicted, actual)
    calibration_60s: Tuple[float, float]
    calibration_120s: Tuple[float, float]

    # MAE of probability predictions
    mae: float

    # Breakdown by spread bucket
    by_spread: Dict[int, Dict[str, float]]

    # Breakdown by side
    by_side: Dict[str, Dict[str, float]]


class Calibrator:
    """Validates fill time predictions against observed fill behavior.

    Reads spread capture trade logs to extract actual fill times,
    then compares against model predictions.
    """

    def __init__(self, log_dir: str = "data/spread_capture"):
        self._log_dir = Path(log_dir)

    def extract_fills(self) -> List[ObservedFill]:
        """Extract observed fills from trade log JSONL files.

        Matches spread_entry_attempt -> fill events by trade_id/ticker
        to compute actual fill times.
        """
        fills = []

        for path in sorted(self._log_dir.glob("depth_*.jsonl")):
            session_fills = self._extract_from_file(path)
            fills.extend(session_fills)

        logger.info(f"Extracted {len(fills)} observed fills from trade logs")
        return fills

    def _extract_from_file(self, path: Path) -> List[ObservedFill]:
        """Extract fills from a single log file."""
        # Track entry attempts: (ticker, order side context) -> entry info
        entry_attempts: Dict[str, dict] = {}  # trade_id -> entry data
        order_to_trade: Dict[str, str] = {}  # order_id -> trade_id
        fills = []

        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")
                    ts = event.get("timestamp")

                    # Parse timestamp - can be ISO string or float
                    if isinstance(ts, str):
                        try:
                            ts = datetime.fromisoformat(ts).timestamp()
                        except (ValueError, TypeError):
                            continue
                    elif not isinstance(ts, (int, float)):
                        continue

                    if event_type == "spread_entry_attempt":
                        trade_id = event.get("trade_id", "")
                        entry_attempts[trade_id] = {
                            "ticker": event.get("ticker", ""),
                            "entry_price": event.get("entry_price", 0),
                            "entry_size": event.get("entry_size", 0),
                            "target_exit": event.get("target_exit", 0),
                            "spread_at_entry": event.get("spread_at_entry", 0),
                            "timestamp": ts,
                        }

                    elif event_type == "order_placed":
                        # Link order to most recent entry attempt for this ticker
                        ticker = event.get("ticker", "")
                        order_id = event.get("order_id", "")
                        for tid, edata in reversed(list(entry_attempts.items())):
                            if edata["ticker"] == ticker:
                                order_to_trade[order_id] = tid
                                break

                    elif event_type == "fill":
                        order_id = event.get("order_id", "")
                        trade_id = order_to_trade.get(order_id)
                        if trade_id and trade_id in entry_attempts:
                            edata = entry_attempts[trade_id]
                            fill_time = ts - edata["timestamp"]
                            if fill_time >= 0:
                                fills.append(
                                    ObservedFill(
                                        trade_id=trade_id,
                                        ticker=edata["ticker"],
                                        side=event.get("side", "buy"),
                                        price=event.get("price", 0),
                                        size=event.get("size", 0),
                                        entry_timestamp=edata["timestamp"],
                                        fill_timestamp=ts,
                                        fill_time_seconds=fill_time,
                                        spread_at_entry=edata["spread_at_entry"],
                                    )
                                )

        except Exception as e:
            logger.warning(f"Error reading {path}: {e}")

        return fills

    def calibrate(
        self,
        fills: Optional[List[ObservedFill]] = None,
        predicted_probs: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> CalibrationResult:
        """Run calibration analysis.

        Args:
            fills: Observed fills (extracted if not provided)
            predicted_probs: Optional dict of trade_id -> {p_30s, p_60s, p_120s}

        Returns:
            CalibrationResult with accuracy metrics
        """
        if fills is None:
            fills = self.extract_fills()

        if not fills:
            return CalibrationResult(
                total_observations=0,
                mean_fill_time_seconds=0.0,
                median_fill_time_seconds=0.0,
                calibration_30s=(0.0, 0.0),
                calibration_60s=(0.0, 0.0),
                calibration_120s=(0.0, 0.0),
                mae=0.0,
                by_spread={},
                by_side={},
            )

        fill_times = [f.fill_time_seconds for f in fills]
        fill_times_sorted = sorted(fill_times)
        n = len(fill_times)

        mean_ft = sum(fill_times) / n
        median_ft = fill_times_sorted[n // 2]

        # Actual fill rates at time horizons
        actual_30s = sum(1 for t in fill_times if t <= 30) / n
        actual_60s = sum(1 for t in fill_times if t <= 60) / n
        actual_120s = sum(1 for t in fill_times if t <= 120) / n

        # Predicted averages (if available)
        pred_30s = pred_60s = pred_120s = 0.0
        mae = 0.0
        if predicted_probs:
            pred_vals = list(predicted_probs.values())
            if pred_vals:
                pred_30s = sum(p.get("p_30s", 0) for p in pred_vals) / len(pred_vals)
                pred_60s = sum(p.get("p_60s", 0) for p in pred_vals) / len(pred_vals)
                pred_120s = sum(p.get("p_120s", 0) for p in pred_vals) / len(pred_vals)
                mae = (
                    abs(pred_30s - actual_30s)
                    + abs(pred_60s - actual_60s)
                    + abs(pred_120s - actual_120s)
                ) / 3

        # Breakdown by spread
        by_spread: Dict[int, Dict[str, float]] = defaultdict(
            lambda: {"count": 0, "mean_ft": 0.0, "p_fill_60s": 0.0}
        )
        for f in fills:
            bucket = (f.spread_at_entry // 5) * 5
            by_spread[bucket]["count"] += 1
            by_spread[bucket]["mean_ft"] += f.fill_time_seconds
            by_spread[bucket]["p_fill_60s"] += 1.0 if f.fill_time_seconds <= 60 else 0.0

        for bucket, stats in by_spread.items():
            cnt = stats["count"]
            if cnt > 0:
                stats["mean_ft"] /= cnt
                stats["p_fill_60s"] /= cnt

        # Breakdown by side
        by_side: Dict[str, Dict[str, float]] = defaultdict(
            lambda: {"count": 0, "mean_ft": 0.0, "p_fill_60s": 0.0}
        )
        for f in fills:
            by_side[f.side]["count"] += 1
            by_side[f.side]["mean_ft"] += f.fill_time_seconds
            by_side[f.side]["p_fill_60s"] += 1.0 if f.fill_time_seconds <= 60 else 0.0

        for side, stats in by_side.items():
            cnt = stats["count"]
            if cnt > 0:
                stats["mean_ft"] /= cnt
                stats["p_fill_60s"] /= cnt

        return CalibrationResult(
            total_observations=n,
            mean_fill_time_seconds=mean_ft,
            median_fill_time_seconds=median_ft,
            calibration_30s=(pred_30s, actual_30s),
            calibration_60s=(pred_60s, actual_60s),
            calibration_120s=(pred_120s, actual_120s),
            mae=mae,
            by_spread=dict(by_spread),
            by_side=dict(by_side),
        )

    def print_report(self, result: Optional[CalibrationResult] = None) -> str:
        """Generate a human-readable calibration report."""
        if result is None:
            result = self.calibrate()

        lines = [
            "=== Fill Time Calibration Report ===",
            f"Total observations: {result.total_observations}",
            f"Mean fill time: {result.mean_fill_time_seconds:.1f}s",
            f"Median fill time: {result.median_fill_time_seconds:.1f}s",
            "",
            "--- Calibration (predicted vs actual) ---",
            f"P(fill within 30s):  pred={result.calibration_30s[0]:.3f}  actual={result.calibration_30s[1]:.3f}",
            f"P(fill within 60s):  pred={result.calibration_60s[0]:.3f}  actual={result.calibration_60s[1]:.3f}",
            f"P(fill within 120s): pred={result.calibration_120s[0]:.3f}  actual={result.calibration_120s[1]:.3f}",
            f"MAE: {result.mae:.4f}",
            "",
            "--- By Spread Bucket ---",
        ]

        for bucket in sorted(result.by_spread.keys()):
            stats = result.by_spread[bucket]
            lines.append(
                f"  spread {bucket}-{bucket + 4}c: n={stats['count']:.0f}, "
                f"mean_ft={stats['mean_ft']:.1f}s, "
                f"P(fill 60s)={stats['p_fill_60s']:.3f}"
            )

        lines.append("")
        lines.append("--- By Side ---")
        for side, stats in sorted(result.by_side.items()):
            lines.append(
                f"  {side}: n={stats['count']:.0f}, "
                f"mean_ft={stats['mean_ft']:.1f}s, "
                f"P(fill 60s)={stats['p_fill_60s']:.3f}"
            )

        report = "\n".join(lines)
        logger.info(report)
        return report
