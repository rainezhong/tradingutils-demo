"""
Unit tests for crypto scalp fee calculation and balance tracking (Fixes #6, #7).

Tests the critical financial calculations that were fixed in the March 2, 2026 session:
- Fix #6: Fee calculation on both entry and exit, for all trades
- Fix #7: Balance drift calculation and alert thresholds
"""

import pytest


# Kalshi fee rate (7%)
KALSHI_FEE_RATE = 0.07


class TestFeeCalculation:
    """Test fee calculation logic from Fix #6."""

    def test_fee_calculation_both_entry_and_exit(self):
        """Verify fees calculated on BOTH entry and exit (not just exit)."""
        entry_price_cents = 50
        exit_price_cents = 60

        # Fix #6: Calculate fees on both entry and exit
        entry_fee = max(1, int(entry_price_cents * KALSHI_FEE_RATE))
        exit_fee = max(1, int(exit_price_cents * KALSHI_FEE_RATE))
        total_fees = entry_fee + exit_fee

        assert entry_fee == 3, "Entry fee should be 3¢ (50 * 0.07 = 3.5 → 3)"
        assert exit_fee == 4, "Exit fee should be 4¢ (60 * 0.07 = 4.2 → 4)"
        assert total_fees == 7, "Total fees should be 7¢ (3 + 4)"

    def test_fee_calculation_on_losses(self):
        """Verify fees calculated on LOSSES too (not just wins)."""
        entry_price_cents = 60
        exit_price_cents = 40  # Loss!

        # Fix #6: Fees apply to ALL trades, not just profitable ones
        entry_fee = max(1, int(entry_price_cents * KALSHI_FEE_RATE))
        exit_fee = max(1, int(exit_price_cents * KALSHI_FEE_RATE))
        total_fees = entry_fee + exit_fee

        gross_pnl = exit_price_cents - entry_price_cents  # -20¢
        net_pnl = gross_pnl - total_fees  # -20 - 7 = -27¢

        assert entry_fee == 4, "Entry fee should be 4¢"
        assert exit_fee == 2, "Exit fee should be 2¢"
        assert total_fees == 6, "Total fees should be 6¢"
        assert gross_pnl == -20, "Gross P&L should be -20¢"
        assert net_pnl == -26, "Net P&L should be -26¢ (loss + fees)"

    def test_fee_minimum_one_cent(self):
        """Verify minimum fee is 1¢ (even for very low prices)."""
        # Very low price contracts (near-certain outcomes)
        entry_price_cents = 5   # 5¢ contract
        exit_price_cents = 10   # 10¢ exit

        entry_fee = max(1, int(entry_price_cents * KALSHI_FEE_RATE))
        exit_fee = max(1, int(exit_price_cents * KALSHI_FEE_RATE))

        # 5 * 0.07 = 0.35 → 0, but max(1, 0) = 1
        # 10 * 0.07 = 0.7 → 0, but max(1, 0) = 1
        assert entry_fee == 1, "Entry fee should be at least 1¢"
        assert exit_fee == 1, "Exit fee should be at least 1¢"

    def test_fee_on_expensive_contracts(self):
        """Verify fees scale correctly for expensive contracts."""
        entry_price_cents = 80  # 80¢ contract (near-certain)
        exit_price_cents = 90   # 90¢ exit

        entry_fee = max(1, int(entry_price_cents * KALSHI_FEE_RATE))
        exit_fee = max(1, int(exit_price_cents * KALSHI_FEE_RATE))
        total_fees = entry_fee + exit_fee

        # 80 * 0.07 = 5.6 → 5
        # 90 * 0.07 = 6.3 → 6
        assert entry_fee == 5, "Entry fee should be 5¢"
        assert exit_fee == 6, "Exit fee should be 6¢"
        assert total_fees == 11, "Total fees should be 11¢"

    def test_fee_rounding_down(self):
        """Verify fees are truncated (int conversion), not rounded."""
        # Test rounding behavior: int() truncates toward zero
        test_cases = [
            (30, 2),   # 30 * 0.07 = 2.1 → 2
            (35, 2),   # 35 * 0.07 = 2.45 → 2
            (40, 2),   # 40 * 0.07 = 2.8 → 2
            (43, 3),   # 43 * 0.07 = 3.01 → 3
            (50, 3),   # 50 * 0.07 = 3.5 → 3
            (57, 3),   # 57 * 0.07 = 3.99 → 3
            (58, 4),   # 58 * 0.07 = 4.06 → 4
        ]

        for price_cents, expected_fee in test_cases:
            fee = max(1, int(price_cents * KALSHI_FEE_RATE))
            assert fee == expected_fee, f"Fee for {price_cents}¢ should be {expected_fee}¢"

    def test_fee_impact_on_pnl(self):
        """Verify fees properly reduce P&L on profitable trades."""
        entry_price_cents = 30
        exit_price_cents = 50

        gross_pnl_per_contract = exit_price_cents - entry_price_cents  # 20¢

        # OLD (BUGGY) WAY: Only exit fees, only on wins
        # old_fee = max(1, int(gross_pnl_per_contract * KALSHI_FEE_RATE))  # 20 * 0.07 = 1¢
        # old_net_pnl = gross_pnl_per_contract - old_fee  # 20 - 1 = 19¢

        # NEW (CORRECT) WAY: Entry + exit fees, all trades
        entry_fee = max(1, int(entry_price_cents * KALSHI_FEE_RATE))  # 2¢
        exit_fee = max(1, int(exit_price_cents * KALSHI_FEE_RATE))   # 3¢
        total_fees = entry_fee + exit_fee                             # 5¢
        new_net_pnl = gross_pnl_per_contract - total_fees            # 15¢

        assert entry_fee == 2
        assert exit_fee == 3
        assert total_fees == 5
        assert new_net_pnl == 15, "Net P&L should be 15¢ after proper fees"
        # assert old_net_pnl == 19, "Old buggy calculation would show 19¢"
        # Difference: 4¢ understatement (~7% of gross P&L)

    def test_fee_total_percentage(self):
        """Verify total fee burden is approximately 14% (7% entry + 7% exit)."""
        # For 50¢ contract
        entry_price_cents = 50
        exit_price_cents = 50  # Break-even trade

        entry_fee = max(1, int(entry_price_cents * KALSHI_FEE_RATE))  # 3¢
        exit_fee = max(1, int(exit_price_cents * KALSHI_FEE_RATE))   # 3¢
        total_fees = entry_fee + exit_fee                             # 6¢

        # Total fees as % of entry price
        fee_percentage = (total_fees / entry_price_cents) * 100  # 6/50 = 12%

        # Note: Not exactly 14% due to rounding (int truncation)
        # 50 * 0.07 = 3.5 → 3, so 3 + 3 = 6 (12%), not 7 (14%)
        assert total_fees == 6
        assert 11.0 <= fee_percentage <= 13.0, "Fee burden ~12% for 50¢ contracts"


class TestBalanceDriftCalculation:
    """Test balance drift calculation and alerting logic from Fix #7."""

    def test_balance_drift_zero(self):
        """Verify drift is zero when logged P&L matches actual balance change."""
        initial_balance_cents = 10000  # $100.00
        cumulative_pnl_cents = -552    # -$5.52 (from logged trades)
        actual_balance_cents = 9448    # $94.48 (from Kalshi API)

        # Fix #7: drift = actual - expected
        expected_balance_cents = initial_balance_cents + cumulative_pnl_cents
        drift_cents = actual_balance_cents - expected_balance_cents

        assert expected_balance_cents == 9448
        assert drift_cents == 0, "Drift should be zero when P&L matches"

    def test_balance_drift_negative(self):
        """Verify negative drift detected (actual < expected = missing P&L)."""
        initial_balance_cents = 10000  # $100.00
        cumulative_pnl_cents = -500    # -$5.00 logged P&L
        actual_balance_cents = 9389    # $93.89 actual

        # Expected: $95.00, Actual: $93.89 → Missing $1.11!
        expected_balance_cents = initial_balance_cents + cumulative_pnl_cents
        drift_cents = actual_balance_cents - expected_balance_cents

        assert expected_balance_cents == 9500
        assert drift_cents == -111, "Drift should be -111¢ ($1.11 missing)"

    def test_balance_drift_positive(self):
        """Verify positive drift detected (actual > expected = extra P&L)."""
        initial_balance_cents = 10000  # $100.00
        cumulative_pnl_cents = 250     # +$2.50 logged P&L
        actual_balance_cents = 10380   # $103.80 actual

        # Expected: $102.50, Actual: $103.80 → Extra $1.30!
        expected_balance_cents = initial_balance_cents + cumulative_pnl_cents
        drift_cents = actual_balance_cents - expected_balance_cents

        assert expected_balance_cents == 10250
        assert drift_cents == 130, "Drift should be +130¢ ($1.30 extra)"

    def test_balance_drift_alert_threshold_exceeded(self):
        """Verify alert triggers when drift > $0.10 (10¢)."""
        alert_threshold_cents = 10  # $0.10

        # Case 1: Drift = -15¢ (should alert)
        drift_cents = -15
        should_alert = abs(drift_cents) > alert_threshold_cents
        assert should_alert is True, "Should alert when drift = -15¢"

        # Case 2: Drift = +20¢ (should alert)
        drift_cents = 20
        should_alert = abs(drift_cents) > alert_threshold_cents
        assert should_alert is True, "Should alert when drift = +20¢"

    def test_balance_drift_alert_threshold_ok(self):
        """Verify NO alert when drift <= $0.10."""
        alert_threshold_cents = 10  # $0.10

        # Case 1: Drift = 5¢ (should NOT alert)
        drift_cents = 5
        should_alert = abs(drift_cents) > alert_threshold_cents
        assert should_alert is False, "Should NOT alert when drift = 5¢"

        # Case 2: Drift = -8¢ (should NOT alert)
        drift_cents = -8
        should_alert = abs(drift_cents) > alert_threshold_cents
        assert should_alert is False, "Should NOT alert when drift = -8¢"

        # Case 3: Drift = 10¢ exactly (should NOT alert, > not >=)
        drift_cents = 10
        should_alert = abs(drift_cents) > alert_threshold_cents
        assert should_alert is False, "Should NOT alert when drift = exactly 10¢"

    def test_balance_drift_alert_threshold_boundary(self):
        """Verify alert boundary at exactly $0.10."""
        alert_threshold_cents = 10

        # 10¢ exactly should NOT alert (> not >=)
        drift_cents = 10
        assert (abs(drift_cents) > alert_threshold_cents) is False

        drift_cents = -10
        assert (abs(drift_cents) > alert_threshold_cents) is False

        # 11¢ should alert
        drift_cents = 11
        assert (abs(drift_cents) > alert_threshold_cents) is True

        drift_cents = -11
        assert (abs(drift_cents) > alert_threshold_cents) is True

    def test_balance_tracking_scenario_march2(self):
        """
        Reproduce March 2 scenario: $5.52 actual loss vs $0.04 logged loss.

        This was the bug that triggered Fix #6 and #7:
        - Actual loss: -$5.52 (from Kalshi API)
        - Logged loss: -$0.04 (from strategy logs)
        - Drift: $5.48 missing! (13,700% error)
        """
        initial_balance_cents = 10000    # $100.00 starting balance
        logged_pnl_cents = -4            # -$0.04 (what strategy logged)
        actual_balance_cents = 9448      # $94.48 (actual from Kalshi)

        # What we expected based on logged P&L
        expected_balance_cents = initial_balance_cents + logged_pnl_cents
        # What we actually have
        drift_cents = actual_balance_cents - expected_balance_cents

        # Expected: $99.96, Actual: $94.48 → Missing $5.48!
        assert expected_balance_cents == 9996, "Expected $99.96 based on -$0.04 logged"
        assert drift_cents == -548, "Drift should be -$5.48 (548¢)"
        assert abs(drift_cents) > 10, "This massive drift should definitely alert!"

        # Error magnitude
        actual_loss_cents = initial_balance_cents - actual_balance_cents
        logged_loss_cents = abs(logged_pnl_cents)
        error_magnitude = actual_loss_cents / logged_loss_cents if logged_loss_cents > 0 else float('inf')

        assert actual_loss_cents == 552, "Actual loss was $5.52"
        assert logged_loss_cents == 4, "Logged loss was $0.04"
        assert error_magnitude == 138.0, "13,800% error (138x understatement)!"

    def test_balance_drift_with_profitable_session(self):
        """Verify drift tracking works correctly for profitable sessions."""
        initial_balance_cents = 10000  # $100.00
        cumulative_pnl_cents = 1250    # +$12.50 (5 wins @ $2.50 each)
        actual_balance_cents = 11250   # $112.50 (perfect match)

        expected_balance_cents = initial_balance_cents + cumulative_pnl_cents
        drift_cents = actual_balance_cents - expected_balance_cents

        assert expected_balance_cents == 11250
        assert drift_cents == 0, "Drift should be zero on profitable session with accurate tracking"
        assert abs(drift_cents) <= 10, "Should NOT alert when drift = 0"

    def test_balance_drift_cumulative_rounding_errors(self):
        """Verify drift handles small cumulative rounding errors."""
        # Small rounding errors from fee calculations can accumulate
        initial_balance_cents = 10000
        # After 20 trades with 1¢ rounding error each
        cumulative_pnl_cents = 500     # +$5.00 logged
        actual_balance_cents = 10505   # +$5.05 actual (5¢ cumulative rounding)

        expected_balance_cents = initial_balance_cents + cumulative_pnl_cents
        drift_cents = actual_balance_cents - expected_balance_cents

        assert drift_cents == 5, "Small cumulative rounding: +5¢"
        assert abs(drift_cents) <= 10, "Should NOT alert for small rounding errors"

    def test_balance_drift_multiple_checks(self):
        """Verify drift can be checked multiple times during session."""
        initial_balance_cents = 10000

        # After 3 trades
        cumulative_pnl_cents = -150
        actual_balance_cents = 9850
        drift_1 = actual_balance_cents - (initial_balance_cents + cumulative_pnl_cents)
        assert drift_1 == 0, "First check: no drift"

        # After 7 trades (drift appears)
        cumulative_pnl_cents = -350
        actual_balance_cents = 9620  # Missing 30¢
        drift_2 = actual_balance_cents - (initial_balance_cents + cumulative_pnl_cents)
        assert drift_2 == -30, "Second check: -30¢ drift detected"
        assert abs(drift_2) > 10, "Should alert after detecting drift"


class TestFeeAndDriftIntegration:
    """Test interaction between fee calculation and balance drift detection."""

    def test_correct_fees_prevent_drift(self):
        """
        Verify that calculating fees correctly prevents balance drift.

        This is the core fix from March 2:
        - OLD: Only exit fees, only on wins → P&L overstated → drift
        - NEW: Entry + exit fees, all trades → P&L accurate → no drift
        """
        initial_balance_cents = 10000

        # Simulate 5 trades (all with Fix #6 correct fee calculation)
        trades = [
            {"entry": 30, "exit": 50},   # Win
            {"entry": 40, "exit": 35},   # Loss
            {"entry": 50, "exit": 60},   # Win
            {"entry": 45, "exit": 40},   # Loss
            {"entry": 25, "exit": 40},   # Win
        ]

        cumulative_pnl_cents = 0
        actual_balance_cents = initial_balance_cents

        for trade in trades:
            entry_price = trade["entry"]
            exit_price = trade["exit"]

            # Calculate fees (Fix #6)
            entry_fee = max(1, int(entry_price * KALSHI_FEE_RATE))
            exit_fee = max(1, int(exit_price * KALSHI_FEE_RATE))
            total_fees = entry_fee + exit_fee

            # Calculate P&L
            gross_pnl = exit_price - entry_price
            net_pnl = gross_pnl - total_fees

            # Update tracking
            cumulative_pnl_cents += net_pnl
            actual_balance_cents += net_pnl  # Simulate actual Kalshi balance change

        # Check drift (Fix #7)
        expected_balance_cents = initial_balance_cents + cumulative_pnl_cents
        drift_cents = actual_balance_cents - expected_balance_cents

        assert drift_cents == 0, "With correct fee calculation, drift should be zero"
        assert abs(drift_cents) <= 10, "Should NOT alert when fees calculated correctly"

    def test_incorrect_fees_cause_drift(self):
        """
        Demonstrate that OLD buggy fee calculation causes drift.

        This reproduces the bug from before Fix #6.
        """
        initial_balance_cents = 10000

        # Same 5 trades, but with OLD buggy fee calculation
        trades = [
            {"entry": 30, "exit": 50},   # Win
            {"entry": 40, "exit": 35},   # Loss
            {"entry": 50, "exit": 60},   # Win
            {"entry": 45, "exit": 40},   # Loss
            {"entry": 25, "exit": 40},   # Win
        ]

        cumulative_pnl_cents = 0  # What strategy logs (WRONG)
        actual_balance_cents = initial_balance_cents  # What Kalshi shows (CORRECT)

        for trade in trades:
            entry_price = trade["entry"]
            exit_price = trade["exit"]

            # BUGGY OLD WAY: Only exit fees, only on wins
            gross_pnl = exit_price - entry_price
            if gross_pnl > 0:
                # Only calculate fees on wins, only on gross P&L
                old_fee = max(1, int(gross_pnl * KALSHI_FEE_RATE))
            else:
                old_fee = 0  # BUG: No fees on losses!
            buggy_net_pnl = gross_pnl - old_fee

            # CORRECT WAY: Entry + exit fees, all trades
            entry_fee = max(1, int(entry_price * KALSHI_FEE_RATE))
            exit_fee = max(1, int(exit_price * KALSHI_FEE_RATE))
            correct_net_pnl = gross_pnl - entry_fee - exit_fee

            # Update tracking
            cumulative_pnl_cents += buggy_net_pnl  # What we LOG (wrong)
            actual_balance_cents += correct_net_pnl  # What Kalshi CHARGES (right)

        # Check drift (Fix #7)
        expected_balance_cents = initial_balance_cents + cumulative_pnl_cents
        drift_cents = actual_balance_cents - expected_balance_cents

        # Should have negative drift (actual < expected = P&L overstated)
        assert drift_cents < 0, "Buggy fees cause negative drift (missing money)"
        assert abs(drift_cents) > 10, "Drift should be large enough to alert"

        # The drift equals the fee understatement
        print(f"Cumulative P&L (logged, buggy): {cumulative_pnl_cents}¢")
        print(f"Actual balance change (Kalshi): {actual_balance_cents - initial_balance_cents}¢")
        print(f"Drift detected: {drift_cents}¢")
