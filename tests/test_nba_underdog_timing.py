#!/usr/bin/env python3
"""
Comprehensive tests for NBA underdog strategy timing fix.

Ensures the strategy correctly checks game start time (not market close time)
for the 2-5 hour entry window.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies.nba_underdog_strategy import NBAUnderdogStrategy, NBAUnderdogConfig


class TestNBAUnderdogTiming:
    """Test suite for timing logic fixes."""

    def setup_method(self):
        """Set up test fixtures."""
        # Create mock exchange client
        self.mock_client = Mock()
        self.mock_client.get_balance = Mock(return_value=Mock(available=100.0))

        # Create strategy with test config
        self.config = NBAUnderdogConfig(
            min_time_until_close_hours=2,
            max_time_until_close_hours=5,
            min_price_cents=5,
            max_price_cents=15,
        )
        self.strategy = NBAUnderdogStrategy(self.mock_client, self.config, dry_run=True)

    def test_parse_game_start_from_ticker(self):
        """Test parsing game start time from ticker."""
        # Test valid tickers (date + 1 day for evening games)
        test_cases = [
            ("KXNBAGAME-26FEB26CHAIND-IND", datetime(2026, 2, 27, 0, 0, 0, tzinfo=timezone.utc)),
            ("KXNBAGAME-26JAN15LAKGSH-LAK", datetime(2026, 1, 16, 0, 0, 0, tzinfo=timezone.utc)),
            ("KXNBAGAME-26DEC31NOPMIN-NOP", datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)),
        ]

        for ticker, expected_date in test_cases:
            result = self.strategy._parse_game_start_from_ticker(ticker)
            assert result == expected_date, f"Failed for {ticker}: got {result}, expected {expected_date}"

        # Test invalid tickers
        invalid_tickers = [
            "INVALID-TICKER",
            "KXNBAGAME-26XXX26TEAMS-A",  # Invalid month
            "KXNBAGAME-BADFORMAT",
        ]

        for ticker in invalid_tickers:
            result = self.strategy._parse_game_start_from_ticker(ticker)
            assert result is None, f"Should return None for invalid ticker: {ticker}"

    def test_market_filter_timing_window(self):
        """Test that market_filter correctly filters by game start time."""
        now = datetime.now(timezone.utc)

        # Create test markets at different times
        # Note: Ticker only encodes DATE, not time. Games start at midnight UTC.
        # So we need to create game dates that are N hours away from now when parsed as midnight.
        test_cases = []

        # Calculate how many hours until the next few midnights
        today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_midnight = today_midnight + timedelta(days=1)
        day_after_midnight = today_midnight + timedelta(days=2)
        three_days_midnight = today_midnight + timedelta(days=3)

        hours_to_tomorrow = (tomorrow_midnight - now).total_seconds() / 3600
        hours_to_day_after = (day_after_midnight - now).total_seconds() / 3600
        hours_to_three_days = (three_days_midnight - now).total_seconds() / 3600

        # Build test cases based on actual midnight boundaries
        if 2 <= hours_to_tomorrow <= 5:
            test_cases.append((tomorrow_midnight, True, f"Tomorrow midnight ({hours_to_tomorrow:.1f}h) - in window"))
        elif hours_to_tomorrow < 2:
            test_cases.append((tomorrow_midnight, False, f"Tomorrow midnight ({hours_to_tomorrow:.1f}h) - too close"))
        else:
            test_cases.append((tomorrow_midnight, False, f"Tomorrow midnight ({hours_to_tomorrow:.1f}h) - too far"))

        if 2 <= hours_to_day_after <= 5:
            test_cases.append((day_after_midnight, True, f"Day after midnight ({hours_to_day_after:.1f}h) - in window"))
        elif hours_to_day_after < 2:
            test_cases.append((day_after_midnight, False, f"Day after midnight ({hours_to_day_after:.1f}h) - too close"))
        else:
            test_cases.append((day_after_midnight, False, f"Day after midnight ({hours_to_day_after:.1f}h) - too far"))

        # Three days out is definitely too far
        test_cases.append((three_days_midnight, False, f"Three days midnight ({hours_to_three_days:.1f}h) - too far"))

        for game_start, should_pass, description in test_cases:
            # Create ticker with game date
            ticker_date = game_start.strftime("%y%b%d").upper()
            ticker = f"KXNBAGAME-{ticker_date}CHAIND-IND"

            # Create mock market with all required fields
            market = Mock()
            market.ticker = ticker
            market.event_ticker = f"KXNBAGAME-{ticker_date}CHAIND"
            market.status = "open"
            market.yes_ask = 10  # 10¢ (in range)
            market.no_ask = 90
            market.yes_bid = 8
            market.no_bid = 88
            market.close_time = now + timedelta(days=14)  # Kalshi's actual close time (should be ignored)

            # Test filter
            result = self.strategy.market_filter(market)

            assert result == should_pass, f"Failed: {description} (ticker={ticker}, result={result}, expected={should_pass})"

    def test_market_filter_price_range(self):
        """Test that market_filter still respects price range."""
        now = datetime.now(timezone.utc)

        # Find a game date that's in timing window
        game_start = None
        for days_offset in range(1, 10):
            game_midnight = (now.replace(hour=0, minute=0, second=0, microsecond=0) +
                           timedelta(days=days_offset))
            hours_until = (game_midnight - now).total_seconds() / 3600
            if 2 <= hours_until <= 5:
                game_start = game_midnight
                break

        if not game_start:
            # No dates in timing window right now - skip test
            print("    ⊘ Skipped (no dates in 2-5h window)")
            return

        ticker_date = game_start.strftime("%y%b%d").upper()
        ticker = f"KXNBAGAME-{ticker_date}CHAIND-IND"

        # Test different prices
        price_tests = [
            (5, True, "5¢ - at min"),
            (10, True, "10¢ - middle"),
            (15, True, "15¢ - at max"),
            (3, False, "3¢ - too low"),
            (20, False, "20¢ - too high"),
        ]

        for price_cents, should_pass, description in price_tests:
            market = Mock()
            market.ticker = ticker
            market.event_ticker = f"KXNBAGAME-{ticker_date}CHAIND"
            market.status = "open"
            market.yes_ask = price_cents
            market.no_ask = 100 - price_cents
            market.yes_bid = price_cents - 2
            market.no_bid = 100 - price_cents - 2
            market.close_time = now + timedelta(days=14)

            result = self.strategy.market_filter(market)
            assert result == should_pass, f"Failed: {description}"

    def test_market_close_time_ignored(self):
        """Test that market close_time (14 days later) is now ignored."""
        now = datetime.now(timezone.utc)

        # Find a game date in the timing window
        game_start = None
        for days_offset in range(1, 10):
            game_midnight = (now.replace(hour=0, minute=0, second=0, microsecond=0) +
                           timedelta(days=days_offset))
            hours_until = (game_midnight - now).total_seconds() / 3600
            if 2 <= hours_until <= 5:
                game_start = game_midnight
                break

        if not game_start:
            # No dates in timing window right now - skip test
            print("    ⊘ Skipped (no dates in 2-5h window)")
            return

        market_close = now + timedelta(days=14)  # 14 days later (Kalshi's actual close time)

        ticker_date = game_start.strftime("%y%b%d").upper()
        ticker = f"KXNBAGAME-{ticker_date}CHAIND-IND"

        market = Mock()
        market.ticker = ticker
        market.event_ticker = f"KXNBAGAME-{ticker_date}CHAIND"
        market.status = "open"
        market.close_time = market_close  # This should be ignored now
        market.yes_ask = 10
        market.no_ask = 90
        market.yes_bid = 8
        market.no_bid = 88

        # Should pass because game is in 3h, even though market closes in 14 days
        result = self.strategy.market_filter(market)
        assert result == True, "Should filter by game start time, not market close time"

    def test_regression_historical_behavior(self):
        """Regression test: ensure new logic matches historical backtest expectations."""
        # Historical backtest optimized for "2-5h before game"
        # This test ensures we're checking game timing, not market timing

        now = datetime.now(timezone.utc)

        # Find dates that are in the valid window (2-5h before midnight)
        valid_game_dates = []
        invalid_game_dates = []

        for days_offset in range(1, 10):
            game_midnight = (now.replace(hour=0, minute=0, second=0, microsecond=0) +
                           timedelta(days=days_offset))
            hours_until = (game_midnight - now).total_seconds() / 3600

            if 2 <= hours_until <= 5:
                valid_game_dates.append((game_midnight, hours_until))
            elif hours_until > 5 or hours_until < 2:
                invalid_game_dates.append((game_midnight, hours_until))

            if len(valid_game_dates) >= 2 and len(invalid_game_dates) >= 2:
                break

        # Test valid dates (should PASS)
        for game_start, hours in valid_game_dates:
            ticker_date = game_start.strftime("%y%b%d").upper()
            ticker = f"KXNBAGAME-{ticker_date}TESTAB-TST"

            market = Mock()
            market.ticker = ticker
            market.event_ticker = f"KXNBAGAME-{ticker_date}TESTAB"
            market.status = "open"
            market.yes_ask = 10
            market.no_ask = 90
            market.yes_bid = 8
            market.no_bid = 88
            market.close_time = now + timedelta(days=14)

            result = self.strategy.market_filter(market)
            assert result == True, f"Should accept market {hours:.1f}h before game (date={ticker_date})"

        # Test invalid dates (should FAIL)
        for game_start, hours in invalid_game_dates:
            ticker_date = game_start.strftime("%y%b%d").upper()
            ticker = f"KXNBAGAME-{ticker_date}TESTAB-TST"

            market = Mock()
            market.ticker = ticker
            market.event_ticker = f"KXNBAGAME-{ticker_date}TESTAB"
            market.status = "open"
            market.yes_ask = 10
            market.no_ask = 90
            market.yes_bid = 8
            market.no_bid = 88
            market.close_time = now + timedelta(days=14)

            result = self.strategy.market_filter(market)
            assert result == False, f"Should reject market {hours:.1f}h before game (date={ticker_date})"


def run_tests():
    """Run all tests and report results."""
    test_suite = TestNBAUnderdogTiming()

    tests = [
        ("Parse game start from ticker", test_suite.test_parse_game_start_from_ticker),
        ("Market filter timing window", test_suite.test_market_filter_timing_window),
        ("Market filter price range", test_suite.test_market_filter_price_range),
        ("Market close time ignored", test_suite.test_market_close_time_ignored),
        ("Regression: historical behavior", test_suite.test_regression_historical_behavior),
    ]

    print("\n" + "="*80)
    print("NBA UNDERDOG TIMING FIX - TEST SUITE")
    print("="*80 + "\n")

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            test_suite.setup_method()  # Reset state
            test_func()
            print(f"✓ {test_name}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {test_name}")
            print(f"  Error: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test_name}")
            print(f"  Unexpected error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "="*80)
    print(f"Results: {passed} passed, {failed} failed")
    print("="*80 + "\n")

    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
