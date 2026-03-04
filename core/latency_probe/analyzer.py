"""Post-hoc analysis on any latency probe database.

Market-agnostic — operates on the shared kalshi_snapshots /
truth_readings / market_settlements tables.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ProbeAnalyzer:
    """Analyzes latency probe data from SQLite."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path))

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return bool(row and row[0])

    def _table_count(self, name: str) -> int:
        if not self._table_exists(name):
            return 0
        return self._conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]

    def _strike_col(self) -> str:
        """Return the strike column name in kalshi_snapshots (strike or floor_strike)."""
        cols = [
            r[1] for r in
            self._conn.execute("PRAGMA table_info(kalshi_snapshots)").fetchall()
        ]
        return "strike" if "strike" in cols else "floor_strike"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Print and return full analysis summary."""
        results: Dict[str, Any] = {}

        snap_count = self._table_count("kalshi_snapshots")
        truth_count = self._table_count("truth_readings")

        print("\n=== Latency Probe Analysis ===")
        print(f"Kalshi snapshots:  {snap_count:,}")
        print(f"Truth readings:    {truth_count:,}")

        if snap_count == 0:
            print("No data collected yet. Run the probe first.")
            return results

        # Time range
        ts_min, ts_max = self._conn.execute(
            "SELECT MIN(ts), MAX(ts) FROM kalshi_snapshots"
        ).fetchone()
        if ts_min and ts_max:
            duration_min = (ts_max - ts_min) / 60
            print(f"Collection window: {duration_min:.1f} minutes")
            results["duration_min"] = duration_min

        results["disagreement"] = self.disagreement()
        results["lag"] = self.cross_event_lag()
        results["staleness"] = self.staleness()
        results["settlements"] = self.settlement_scorecard()

        return results

    def disagreement(self) -> Dict[str, Any]:
        """Compute disagreement rate between truth and Kalshi.

        Compares (truth.probability > 0.5) vs (kalshi.yes_mid > 50).
        Falls back to kraken_snapshots + floor_strike for legacy DBs.
        """
        if self._table_exists("truth_readings") and self._table_count("truth_readings") > 0:
            rows = self._conn.execute("""
                SELECT k.ts, k.ticker, k.yes_mid, t.probability
                FROM kalshi_snapshots k
                INNER JOIN truth_readings t
                    ON t.ticker = k.ticker
                    AND t.ts >= k.ts - 1.0
                    AND t.ts <= k.ts + 1.0
                ORDER BY k.ts
            """).fetchall()
        elif self._table_exists("kraken_snapshots"):
            # Legacy fallback: use kraken avg60 vs strike as truth
            sc = self._strike_col()
            rows = self._conn.execute(f"""
                SELECT k.ts, k.ticker, k.yes_mid,
                    CASE WHEN kr.avg_60s > k.{sc} THEN 0.99 ELSE 0.01 END
                FROM kalshi_snapshots k
                INNER JOIN (
                    SELECT ts, avg_60s FROM kraken_snapshots
                ) kr ON kr.ts = (
                    SELECT MAX(kr2.ts) FROM kraken_snapshots kr2
                    WHERE kr2.ts <= k.ts AND kr2.ts > k.ts - 2.0
                )
                WHERE k.{sc} IS NOT NULL AND k.{sc} > 0
                ORDER BY k.ts
            """).fetchall()
        else:
            print("\n--- Disagreement ---")
            print("No truth data available (need truth_readings or kraken_snapshots)")
            return {}

        if not rows:
            print("\n--- Disagreement ---")
            print("No matched data points (truth + kalshi at same timestamps)")
            return {}

        agree = 0
        disagree = 0
        for _ts, _ticker, yes_mid, prob in rows:
            if yes_mid is None or prob is None:
                continue
            truth_yes = prob > 0.5
            kalshi_yes = yes_mid > 50
            if truth_yes == kalshi_yes:
                agree += 1
            else:
                disagree += 1

        total = agree + disagree
        result: Dict[str, Any] = {}
        print("\n--- Disagreement ---")
        if total > 0:
            pct = 100 * disagree / total
            print(f"Direction agreement: {agree}/{total} ({100 * agree / total:.1f}%)")
            print(f"Disagreements:       {disagree}/{total} ({pct:.1f}%)")
            result = {"agree": agree, "disagree": disagree, "total": total,
                      "disagree_pct": pct}
        else:
            print("No comparable data points")

        return result

    def cross_event_lag(self) -> Dict[str, Any]:
        """Measure lag: when truth crosses 0.5, how long until Kalshi crosses 50.

        Falls back to kraken avg60 crossing strike for legacy DBs.
        """
        if self._table_exists("truth_readings") and self._table_count("truth_readings") > 0:
            rows = self._conn.execute("""
                SELECT k.ts, k.ticker, k.yes_mid, t.probability
                FROM kalshi_snapshots k
                INNER JOIN truth_readings t
                    ON t.ticker = k.ticker
                    AND t.ts >= k.ts - 1.0
                    AND t.ts <= k.ts + 1.0
                WHERE k.yes_mid IS NOT NULL
                ORDER BY k.ts
            """).fetchall()
        elif self._table_exists("kraken_snapshots"):
            sc = self._strike_col()
            rows = self._conn.execute(f"""
                SELECT k.ts, k.ticker, k.yes_mid,
                    CASE WHEN kr.avg_60s > k.{sc} THEN 0.99 ELSE 0.01 END
                FROM kalshi_snapshots k
                INNER JOIN (
                    SELECT ts, avg_60s FROM kraken_snapshots
                ) kr ON kr.ts = (
                    SELECT MAX(kr2.ts) FROM kraken_snapshots kr2
                    WHERE kr2.ts <= k.ts AND kr2.ts > k.ts - 2.0
                )
                WHERE k.{sc} IS NOT NULL AND k.{sc} > 0
                    AND k.yes_mid IS NOT NULL
                ORDER BY k.ts
            """).fetchall()
        else:
            print("\n--- Lag Analysis ---")
            print("No truth data available")
            return {}

        if len(rows) < 2:
            print("\n--- Lag Analysis ---")
            print("Not enough data for lag analysis")
            return {}

        # Find truth crossing events
        cross_events = []
        for i in range(1, len(rows)):
            _pts, _ptk, _pmid, prev_prob = rows[i - 1]
            ts, ticker, yes_mid, prob = rows[i]
            if prev_prob is None or prob is None:
                continue
            prev_above = prev_prob > 0.5
            curr_above = prob > 0.5
            if prev_above != curr_above:
                cross_events.append({
                    "ts": ts,
                    "direction": "UP" if curr_above else "DOWN",
                    "kalshi_mid": yes_mid,
                })

        print("\n--- Lag Analysis: Truth Cross → Kalshi Reaction ---")
        if not cross_events:
            print("No truth-crosses-0.5 events detected")
            return {}

        print(f"Found {len(cross_events)} truth-crosses-0.5 events")

        lags: List[float] = []
        for event in cross_events:
            cross_ts = event["ts"]
            kalshi_mid = event["kalshi_mid"]

            # Check if Kalshi already agreed at cross time
            if event["direction"] == "UP" and kalshi_mid > 50:
                lags.append(0.0)
                continue
            if event["direction"] == "DOWN" and kalshi_mid <= 50:
                lags.append(0.0)
                continue

            # Look for when Kalshi caught up (next 60 seconds of snapshots)
            future = self._conn.execute(
                "SELECT ts, yes_mid FROM kalshi_snapshots "
                "WHERE ts > ? AND ts < ? + 60 ORDER BY ts LIMIT 120",
                (cross_ts, cross_ts),
            ).fetchall()

            found = False
            for fts, fmid in future:
                if event["direction"] == "UP" and fmid > 50:
                    lags.append(fts - cross_ts)
                    found = True
                    break
                elif event["direction"] == "DOWN" and fmid <= 50:
                    lags.append(fts - cross_ts)
                    found = True
                    break
            # If Kalshi never caught up within window, skip this event

        result: Dict[str, Any] = {"cross_events": len(cross_events)}
        if lags:
            already = sum(1 for lag in lags if lag == 0)
            lagged = [lag for lag in lags if lag > 0]
            print(f"  Already agreed at cross: {already}/{len(lags)}")
            result["already_agreed"] = already
            result["total_measured"] = len(lags)
            if lagged:
                avg_lag = sum(lagged) / len(lagged)
                sorted_lags = sorted(lagged)
                median_lag = sorted_lags[len(sorted_lags) // 2]
                print(f"  Lagged cases: {len(lagged)}")
                print(f"  Average lag: {avg_lag:.1f}s")
                print(f"  Median lag:  {median_lag:.1f}s")
                print(f"  Max lag:     {max(lagged):.1f}s")
                result["lagged_count"] = len(lagged)
                result["avg_lag_sec"] = avg_lag
                result["median_lag_sec"] = median_lag
                result["max_lag_sec"] = max(lagged)
            else:
                print("  No lagged cases — Kalshi was always already aligned")
        else:
            print("  No measurable lags (all events inconclusive)")

        return result

    def staleness(self) -> Dict[str, Any]:
        """Detect periods where Kalshi yes_mid is unchanged while truth moved."""
        rows = self._conn.execute(
            "SELECT ts, yes_mid FROM kalshi_snapshots ORDER BY ts"
        ).fetchall()

        print("\n--- Kalshi Quote Staleness ---")
        if len(rows) < 2:
            print("Not enough data")
            return {}

        stale_durations: List[float] = []
        current_stale_start: Optional[float] = None
        prev_mid: Optional[float] = None

        for ts, mid in rows:
            if prev_mid is not None:
                if mid == prev_mid:
                    if current_stale_start is None:
                        current_stale_start = ts
                else:
                    if current_stale_start is not None:
                        stale_durations.append(ts - current_stale_start)
                        current_stale_start = None
            prev_mid = mid

        result: Dict[str, Any] = {}
        if stale_durations:
            avg_stale = sum(stale_durations) / len(stale_durations)
            max_stale = max(stale_durations)
            print(f"Periods where Kalshi yes_mid was unchanged: {len(stale_durations)}")
            print(f"Average stale duration: {avg_stale:.1f}s")
            print(f"Max stale duration:     {max_stale:.1f}s")
            result = {"periods": len(stale_durations),
                      "avg_sec": avg_stale, "max_sec": max_stale}
        else:
            print("Kalshi yes_mid changed every poll (no staleness detected)")

        return result

    def _get_settlement_columns(self) -> Dict[str, str]:
        """Detect settlement table schema (new framework vs legacy probe).

        Returns column name mapping: {truth_right, kalshi_right,
        truth_predicted, kalshi_predicted} → actual column names.
        """
        cols = [
            row[1] for row in
            self._conn.execute("PRAGMA table_info(market_settlements)").fetchall()
        ]
        if "truth_was_right" in cols:
            return {
                "truth_right": "truth_was_right",
                "kalshi_right": "kalshi_was_right",
                "truth_predicted": "truth_predicted_yes",
                "kalshi_predicted": "kalshi_predicted_yes",
            }
        # Legacy schema (btc_latency_probe.py): kraken_was_right, etc.
        return {
            "truth_right": "kraken_was_right",
            "kalshi_right": "kalshi_was_right",
            "truth_predicted": "kraken_predicted_yes",
            "kalshi_predicted": "kalshi_predicted_yes",
        }

    def settlement_scorecard(self) -> Dict[str, Any]:
        """Truth accuracy vs Kalshi accuracy at expiration."""
        print("\n--- Settlement Scorecard ---")

        if not self._table_exists("market_settlements"):
            print("No settlement data")
            return {}

        count = self._table_count("market_settlements")
        if count == 0:
            print("No settlements recorded yet")
            return {}

        cm = self._get_settlement_columns()

        truth_right = self._conn.execute(
            f"SELECT SUM({cm['truth_right']}) FROM market_settlements"
        ).fetchone()[0] or 0
        kalshi_right = self._conn.execute(
            f"SELECT SUM({cm['kalshi_right']}) FROM market_settlements"
        ).fetchone()[0] or 0

        label = "Truth" if cm["truth_right"] == "truth_was_right" else "Kraken"
        print(f"Markets settled:      {count}")
        print(f"{label} was right:      {truth_right}/{count} ({100 * truth_right / count:.0f}%)")
        print(f"Kalshi was right:     {kalshi_right}/{count} ({100 * kalshi_right / count:.0f}%)")

        result: Dict[str, Any] = {
            "count": count,
            "truth_right": truth_right,
            "kalshi_right": kalshi_right,
        }

        # Disagreement outcomes
        disagree_rows = self._conn.execute(f"""
            SELECT ticker, {cm['truth_right']}, {cm['kalshi_right']}
            FROM market_settlements
            WHERE {cm['truth_predicted']} != {cm['kalshi_predicted']}
        """).fetchall()

        if disagree_rows:
            truth_wins = sum(1 for r in disagree_rows if r[1])
            kalshi_wins = sum(1 for r in disagree_rows if r[2])
            n = len(disagree_rows)
            print(f"\nWhen they DISAGREED ({n} markets):")
            print(f"  {label} was right:  {truth_wins}/{n}")
            print(f"  Kalshi was right: {kalshi_wins}/{n}")
            if n > 0:
                print(f"  → Edge if betting with {label}: {100 * truth_wins / n:.0f}% win rate")
            result["disagree_count"] = n
            result["disagree_truth_wins"] = truth_wins
            result["disagree_kalshi_wins"] = kalshi_wins

        return result
