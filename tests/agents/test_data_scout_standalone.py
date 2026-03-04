#!/usr/bin/env python3
"""
Standalone test for DataScoutAgent.

Run with: python3 tests/agents/test_data_scout_standalone.py
"""

import sqlite3
import tempfile
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agents.data_scout import DataScoutAgent, Hypothesis


def create_test_db():
    """Create a temporary database with test data."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    conn = sqlite3.connect(path)
    cursor = conn.cursor()

    # Create schema
    cursor.execute("""
        CREATE TABLE kalshi_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            ticker TEXT NOT NULL,
            yes_bid INTEGER,
            yes_ask INTEGER,
            yes_mid REAL,
            floor_strike REAL,
            close_time TEXT,
            seconds_to_close REAL,
            volume INTEGER,
            open_interest INTEGER
        )
    """)

    # Insert test data for spread anomaly test
    base_ts = 1771392873.0
    ticker = "TEST-TICKER"

    # Normal spreads (1-2 cents)
    for i in range(50):
        cursor.execute("""
            INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
            VALUES (?, ?, ?, ?, ?)
        """, (base_ts + i, ticker, 50, 51, 50.5))

    # Anomalous spread (8 cents - 4x normal)
    cursor.execute("""
        INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
        VALUES (?, ?, ?, ?, ?)
    """, (base_ts + 50, ticker, 50, 58, 54.0))

    # More normal spreads
    for i in range(51, 100):
        cursor.execute("""
            INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
        VALUES (?, ?, ?, ?, ?)
        """, (base_ts + i, ticker, 50, 52, 51.0))

    # Insert test data for price movement test
    ticker2 = "TEST-JUMP"
    price = 50.0

    # Stable prices
    for i in range(20):
        cursor.execute("""
            INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
            VALUES (?, ?, ?, ?, ?)
        """, (base_ts + i, ticker2, int(price), int(price) + 1, price))

    # Sudden jump
    for i in range(20, 25):
        new_price = 70.0  # 20 point jump
        cursor.execute("""
            INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
            VALUES (?, ?, ?, ?, ?)
        """, (base_ts + i, ticker2, int(new_price), int(new_price) + 1, new_price))

    # Back to normal
    for i in range(25, 100):
        cursor.execute("""
            INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
            VALUES (?, ?, ?, ?, ?)
        """, (base_ts + i, ticker2, int(price), int(price) + 1, price))

    # Insert test data for momentum test
    ticker3 = "TEST-MOMENTUM"
    price = 50.0

    # Strong upward momentum (10 consecutive increases)
    for i in range(15):
        cursor.execute("""
            INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
            VALUES (?, ?, ?, ?, ?)
        """, (base_ts + i, ticker3, int(price), int(price) + 1, price))
        price += 1  # Consistent upward move

    # Stabilize
    for i in range(15, 100):
        cursor.execute("""
            INSERT INTO kalshi_snapshots (ts, ticker, yes_bid, yes_ask, yes_mid)
            VALUES (?, ?, ?, ?, ?)
        """, (base_ts + i, ticker3, int(price), int(price) + 1, price))

    conn.commit()
    conn.close()

    return path


def test_spread_anomaly_detection():
    """Test spread anomaly detection."""
    print("Testing spread anomaly detection...", end=" ")
    db_path = create_test_db()

    try:
        with DataScoutAgent(db_path) as agent:
            hypotheses = agent.find_spread_anomalies("TEST-TICKER")

            assert len(hypotheses) > 0, "Should detect spread anomaly"
            h = hypotheses[0]
            assert h.pattern_type == 'spread_anomaly', f"Wrong type: {h.pattern_type}"
            assert h.ticker == 'TEST-TICKER', f"Wrong ticker: {h.ticker}"
            assert h.metadata['spread'] == 8, f"Wrong spread: {h.metadata['spread']}"
            assert h.confidence > 0.5, f"Confidence too low: {h.confidence}"

        print("PASSED")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False
    finally:
        os.unlink(db_path)


def test_price_movement_detection():
    """Test price jump detection."""
    print("Testing price movement detection...", end=" ")
    db_path = create_test_db()

    try:
        with DataScoutAgent(db_path) as agent:
            hypotheses = agent.find_price_movements("TEST-JUMP", window_size=10)

            assert len(hypotheses) > 0, "Should detect price movements"

            # Find the jump detection
            jump_hyp = [h for h in hypotheses if abs(h.metadata.get('change', 0)) > 15]
            assert len(jump_hyp) > 0, "Should detect significant jump"

            h = jump_hyp[0]
            assert h.pattern_type == 'price_movement', f"Wrong type: {h.pattern_type}"
            assert h.ticker == 'TEST-JUMP', f"Wrong ticker: {h.ticker}"
            assert h.statistical_significance > 2.0, f"Significance too low: {h.statistical_significance}"

        print("PASSED")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False
    finally:
        os.unlink(db_path)


def test_momentum_detection():
    """Test momentum pattern detection."""
    print("Testing momentum detection...", end=" ")
    db_path = create_test_db()

    try:
        with DataScoutAgent(db_path) as agent:
            hypotheses = agent.find_momentum("TEST-MOMENTUM", min_streak=5)

            assert len(hypotheses) > 0, "Should detect momentum"
            h = hypotheses[0]
            assert h.pattern_type == 'momentum', f"Wrong type: {h.pattern_type}"
            assert h.ticker == 'TEST-MOMENTUM', f"Wrong ticker: {h.ticker}"
            assert h.metadata['direction'] == 'up', f"Wrong direction: {h.metadata['direction']}"
            assert h.metadata['streak_length'] >= 5, f"Streak too short: {h.metadata['streak_length']}"

        print("PASSED")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False
    finally:
        os.unlink(db_path)


def test_scan_for_patterns():
    """Test full pattern scan."""
    print("Testing full pattern scan...", end=" ")
    db_path = create_test_db()

    try:
        with DataScoutAgent(db_path) as agent:
            hypotheses = agent.scan_for_patterns(min_snapshots=50)

            assert len(hypotheses) > 0, "Should find patterns"

            # Check we have different pattern types
            pattern_types = {h.pattern_type for h in hypotheses}
            assert 'spread_anomaly' in pattern_types, "Missing spread anomaly patterns"
            assert 'price_movement' in pattern_types, "Missing price movement patterns"
            assert 'momentum' in pattern_types, "Missing momentum patterns"

        print("PASSED")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False
    finally:
        os.unlink(db_path)


def test_statistical_functions():
    """Test statistical calculation functions."""
    print("Testing statistical functions...", end=" ")

    try:
        agent = DataScoutAgent()

        # Test z-score
        z = agent.calculate_z_score(110, 100, 10)
        assert abs(z - 1.0) < 0.01, f"Z-score wrong: {z}"

        z = agent.calculate_z_score(90, 100, 10)
        assert abs(z + 1.0) < 0.01, f"Z-score wrong: {z}"

        # Test t-statistic
        t = agent.calculate_t_statistic(105, 100, 10, 25)
        assert abs(t - 2.5) < 0.01, f"T-statistic wrong: {t}"

        print("PASSED")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False


def test_hypothesis_representation():
    """Test Hypothesis string formatting."""
    print("Testing hypothesis string representation...", end=" ")

    try:
        h = Hypothesis(
            pattern_type='spread_anomaly',
            ticker='TEST',
            description='Test description',
            confidence=0.85,
            statistical_significance=3.5,
            data_points=100,
            metadata={}
        )

        s = str(h)
        assert 'SPREAD_ANOMALY' in s, "Missing pattern type"
        assert 'TEST' in s, "Missing ticker"
        assert '85.00%' in s, "Missing confidence"
        assert '3.5000' in s, "Missing significance"
        assert 'N=100' in s, "Missing data points"

        print("PASSED")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        return False


def main():
    """Run all tests."""
    print("\n" + "="*80)
    print("Data Scout Agent - Standalone Tests")
    print("="*80 + "\n")

    tests = [
        test_spread_anomaly_detection,
        test_price_movement_detection,
        test_momentum_detection,
        test_scan_for_patterns,
        test_statistical_functions,
        test_hypothesis_representation,
    ]

    results = [test() for test in tests]

    print("\n" + "="*80)
    print(f"Results: {sum(results)}/{len(results)} tests passed")
    print("="*80 + "\n")

    return all(results)


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
