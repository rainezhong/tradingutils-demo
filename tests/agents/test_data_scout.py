"""
Tests for DataScoutAgent pattern detection.
"""

import pytest
import sqlite3
import tempfile
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from agents.data_scout import DataScoutAgent, Hypothesis


@pytest.fixture
def test_db():
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

    yield path

    # Cleanup
    os.unlink(path)


def test_spread_anomaly_detection(test_db):
    """Test spread anomaly detection."""
    with DataScoutAgent(test_db) as agent:
        hypotheses = agent.find_spread_anomalies("TEST-TICKER")

        # Should detect the anomalous 8-cent spread
        assert len(hypotheses) > 0
        h = hypotheses[0]
        assert h.pattern_type == 'spread_anomaly'
        assert h.ticker == 'TEST-TICKER'
        assert h.metadata['spread'] == 8
        assert h.confidence > 0.5


def test_price_movement_detection(test_db):
    """Test price jump detection."""
    with DataScoutAgent(test_db) as agent:
        hypotheses = agent.find_price_movements("TEST-JUMP", window_size=10)

        # Should detect the 20-point jump
        assert len(hypotheses) > 0

        # Find the jump detection
        jump_hyp = [h for h in hypotheses if abs(h.metadata.get('change', 0)) > 15]
        assert len(jump_hyp) > 0

        h = jump_hyp[0]
        assert h.pattern_type == 'price_movement'
        assert h.ticker == 'TEST-JUMP'
        assert h.statistical_significance > 2.0  # z-score > 2


def test_momentum_detection(test_db):
    """Test momentum pattern detection."""
    with DataScoutAgent(test_db) as agent:
        hypotheses = agent.find_momentum("TEST-MOMENTUM", min_streak=5)

        # Should detect the upward momentum streak
        assert len(hypotheses) > 0
        h = hypotheses[0]
        assert h.pattern_type == 'momentum'
        assert h.ticker == 'TEST-MOMENTUM'
        assert h.metadata['direction'] == 'up'
        assert h.metadata['streak_length'] >= 5


def test_mean_reversion_detection(test_db):
    """Test mean reversion opportunity detection."""
    with DataScoutAgent(test_db) as agent:
        hypotheses = agent.find_mean_reversion("TEST-JUMP", lookback=50)

        # Should detect the deviation from mean after the jump
        assert len(hypotheses) > 0

        # At least one should be the jump itself
        jump_reversions = [h for h in hypotheses if abs(h.metadata.get('deviation', 0)) > 10]
        assert len(jump_reversions) > 0


def test_scan_for_patterns(test_db):
    """Test full pattern scan."""
    with DataScoutAgent(test_db) as agent:
        hypotheses = agent.scan_for_patterns(min_snapshots=50)

        # Should find patterns across all tickers
        assert len(hypotheses) > 0

        # Check we have different pattern types
        pattern_types = {h.pattern_type for h in hypotheses}
        assert 'spread_anomaly' in pattern_types
        assert 'price_movement' in pattern_types
        assert 'momentum' in pattern_types


def test_z_score_calculation():
    """Test z-score calculation."""
    agent = DataScoutAgent()
    z = agent.calculate_z_score(110, 100, 10)
    assert z == 1.0

    z = agent.calculate_z_score(90, 100, 10)
    assert z == -1.0


def test_t_statistic_calculation():
    """Test t-statistic calculation."""
    agent = DataScoutAgent()
    t = agent.calculate_t_statistic(105, 100, 10, 25)
    assert abs(t - 2.5) < 0.01  # 5 / (10/5)


def test_hypothesis_string_representation():
    """Test Hypothesis string formatting."""
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
    assert 'SPREAD_ANOMALY' in s
    assert 'TEST' in s
    assert '85.00%' in s
    assert '3.5000' in s
    assert 'N=100' in s


def test_context_manager():
    """Test DataScoutAgent context manager."""
    # Create a simple in-memory db for testing
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE kalshi_snapshots (
            id INTEGER PRIMARY KEY,
            ts REAL,
            ticker TEXT,
            yes_bid INTEGER,
            yes_ask INTEGER,
            yes_mid REAL
        )
    """)
    conn.close()

    # Test context manager
    with DataScoutAgent(path) as agent:
        assert agent.conn is not None
        assert agent.conn.row_factory == sqlite3.Row

    # Connection should be closed
    assert agent.conn is None

    os.unlink(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
