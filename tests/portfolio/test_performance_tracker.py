"""
Tests for PerformanceTracker.
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import os

from core.portfolio.performance_tracker import PerformanceTracker


@pytest.fixture
def temp_db():
    """Create temporary database."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except:
        pass


def test_record_trade(temp_db):
    """Test recording a single trade."""
    tracker = PerformanceTracker(temp_db)

    trade_id = tracker.record_trade(
        strategy_name="test-strategy",
        ticker="KXTEST-01",
        timestamp=datetime.now(),
        side="buy",
        price=0.50,
        size=10,
        pnl=5.0,
        settled_at=datetime.now(),
    )

    assert trade_id > 0


def test_get_strategy_stats_no_data(temp_db):
    """Test getting stats with no data."""
    tracker = PerformanceTracker(temp_db)

    stats = tracker.get_strategy_stats("nonexistent", lookback_days=30)
    assert stats is None


def test_get_strategy_stats_with_trades(temp_db):
    """Test calculating strategy stats."""
    tracker = PerformanceTracker(temp_db)

    # Record winning trades
    now = datetime.now()
    for i in range(10):
        tracker.record_trade(
            strategy_name="winner",
            ticker=f"KXTEST-{i:02d}",
            timestamp=now - timedelta(days=i),
            side="buy",
            price=0.50,
            size=10,
            pnl=10.0,
            settled_at=now - timedelta(days=i) + timedelta(hours=1),
        )

    stats = tracker.get_strategy_stats("winner", lookback_days=30)

    assert stats is not None
    assert stats.strategy_name == "winner"
    assert stats.num_trades == 10
    assert stats.total_pnl == 100.0
    assert stats.edge == 10.0
    assert stats.win_rate == 1.0


def test_edge_calculation(temp_db):
    """Test edge calculation with mixed wins/losses."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()

    # 3 wins of +10
    for i in range(3):
        tracker.record_trade(
            strategy_name="mixed",
            ticker=f"KXWIN-{i:02d}",
            timestamp=now - timedelta(days=i),
            side="buy",
            price=0.50,
            size=10,
            pnl=10.0,
            settled_at=now,
        )

    # 2 losses of -5
    for i in range(2):
        tracker.record_trade(
            strategy_name="mixed",
            ticker=f"KXLOSS-{i:02d}",
            timestamp=now - timedelta(days=i + 3),
            side="buy",
            price=0.50,
            size=10,
            pnl=-5.0,
            settled_at=now,
        )

    stats = tracker.get_strategy_stats("mixed", lookback_days=30)

    assert stats.num_trades == 5
    assert stats.total_pnl == 20.0  # 3*10 - 2*5 = 20
    assert stats.edge == 4.0  # 20 / 5 = 4
    assert stats.win_rate == 0.6  # 3 / 5 = 0.6
    assert stats.avg_win == 10.0
    assert stats.avg_loss == -5.0


def test_variance_calculation(temp_db):
    """Test variance calculation."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()
    pnls = [10.0, -5.0, 15.0, -10.0, 20.0]

    for i, pnl in enumerate(pnls):
        tracker.record_trade(
            strategy_name="variance-test",
            ticker=f"KXTEST-{i:02d}",
            timestamp=now - timedelta(days=i),
            side="buy",
            price=0.50,
            size=10,
            pnl=pnl,
            settled_at=now,
        )

    stats = tracker.get_strategy_stats("variance-test", lookback_days=30)

    # Calculate expected variance manually
    edge = sum(pnls) / len(pnls)  # 6.0
    variance = sum((p - edge) ** 2 for p in pnls) / len(pnls)

    assert stats.edge == pytest.approx(edge)
    assert stats.variance == pytest.approx(variance)
    assert stats.std_dev == pytest.approx(variance ** 0.5)


def test_lookback_window(temp_db):
    """Test lookback window filtering."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()

    # Recent trade (within 30 days)
    tracker.record_trade(
        strategy_name="windowed",
        ticker="KXRECENT",
        timestamp=now - timedelta(days=5),
        side="buy",
        price=0.50,
        size=10,
        pnl=10.0,
        settled_at=now,
    )

    # Old trade (outside 30 days)
    tracker.record_trade(
        strategy_name="windowed",
        ticker="KXOLD",
        timestamp=now - timedelta(days=90),
        side="buy",
        price=0.50,
        size=10,
        pnl=10.0,
        settled_at=now - timedelta(days=89),
    )

    stats = tracker.get_strategy_stats("windowed", lookback_days=30)

    assert stats.num_trades == 1  # Only recent trade


def test_get_all_strategy_names(temp_db):
    """Test getting list of all strategies."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()

    for strategy in ["strat-a", "strat-b", "strat-c"]:
        tracker.record_trade(
            strategy_name=strategy,
            ticker="KXTEST",
            timestamp=now,
            side="buy",
            price=0.50,
            size=10,
        )

    names = tracker.get_all_strategy_names()

    assert set(names) == {"strat-a", "strat-b", "strat-c"}


def test_unsettled_trades_excluded(temp_db):
    """Test that unsettled trades (no PnL) are excluded from stats."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()

    # Settled trade
    tracker.record_trade(
        strategy_name="test",
        ticker="KXSETTLED",
        timestamp=now,
        side="buy",
        price=0.50,
        size=10,
        pnl=10.0,
        settled_at=now + timedelta(hours=1),
    )

    # Unsettled trade (no PnL)
    tracker.record_trade(
        strategy_name="test",
        ticker="KXOPEN",
        timestamp=now,
        side="buy",
        price=0.50,
        size=10,
        pnl=None,
        settled_at=None,
    )

    stats = tracker.get_strategy_stats("test", lookback_days=30)

    assert stats.num_trades == 1  # Only settled trade


def test_record_backtest_fills(temp_db):
    """Test recording batch of backtest fills."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()
    trades = [
        {
            "ticker": "KXTEST-01",
            "timestamp": now - timedelta(hours=i),
            "side": "buy",
            "price": 0.50,
            "size": 10,
            "pnl": 5.0,
            "settled_at": now - timedelta(hours=i) + timedelta(minutes=30),
        }
        for i in range(5)
    ]

    tracker.record_backtest_fills("backtest-strat", trades)

    stats = tracker.get_strategy_stats("backtest-strat", lookback_days=1)

    assert stats.num_trades == 5
    assert stats.total_pnl == 25.0


def test_get_trade_pnls(temp_db):
    """Test getting list of trade PnLs for empirical Kelly."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()
    expected_pnls = [10.0, -5.0, 15.0, -10.0, 20.0]

    for i, pnl in enumerate(expected_pnls):
        tracker.record_trade(
            strategy_name="pnl-test",
            ticker=f"KXTEST-{i:02d}",
            timestamp=now - timedelta(days=i),
            side="buy",
            price=0.50,
            size=10,
            pnl=pnl,
            settled_at=now,
        )

    # Also add an unsettled trade (should be excluded)
    tracker.record_trade(
        strategy_name="pnl-test",
        ticker="KXUNSETTLED",
        timestamp=now,
        side="buy",
        price=0.50,
        size=10,
        pnl=None,
        settled_at=None,
    )

    pnls = tracker.get_trade_pnls("pnl-test", lookback_days=30)

    # Should get settled trades in chronological order
    assert len(pnls) == 5
    assert set(pnls) == set(expected_pnls)


def test_get_trade_pnls_lookback_window(temp_db):
    """Test trade PnLs respect lookback window."""
    tracker = PerformanceTracker(temp_db)

    now = datetime.now()

    # Recent trade
    tracker.record_trade(
        strategy_name="windowed",
        ticker="KXRECENT",
        timestamp=now - timedelta(days=5),
        side="buy",
        price=0.50,
        size=10,
        pnl=10.0,
        settled_at=now,
    )

    # Old trade (outside window)
    tracker.record_trade(
        strategy_name="windowed",
        ticker="KXOLD",
        timestamp=now - timedelta(days=90),
        side="buy",
        price=0.50,
        size=10,
        pnl=20.0,
        settled_at=now - timedelta(days=89),
    )

    pnls = tracker.get_trade_pnls("windowed", lookback_days=30)

    assert len(pnls) == 1
    assert pnls[0] == 10.0  # Only recent trade


def test_get_trade_pnls_empty(temp_db):
    """Test getting trade PnLs for nonexistent strategy."""
    tracker = PerformanceTracker(temp_db)

    pnls = tracker.get_trade_pnls("nonexistent", lookback_days=30)

    assert pnls == []
