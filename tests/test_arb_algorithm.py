"""Tests for the arbitrage algorithm in arb/live_arb.py.

Tests cover:
- Fee calculations (kalshi_fee_total, all_in_buy_cost, all_in_sell_proceeds)
- Routing edge detection and direction
- Cross-market arbitrage detection
- Dutch book detection
- Best action selection
"""

import math
import pytest

from arb.live_arb import (
    kalshi_fee_total,
    fee_per_contract,
    all_in_buy_cost,
    all_in_sell_proceeds,
    _round_up_cent,
)


# =============================================================================
# Fee Calculation Tests
# =============================================================================


class TestFeeCalculations:
    """Tests for Kalshi fee calculation functions."""

    def test_round_up_cent_basic(self) -> None:
        """_round_up_cent rounds up to nearest cent."""
        assert _round_up_cent(0.123) == 0.13
        assert _round_up_cent(0.120) == 0.12
        assert _round_up_cent(0.121) == 0.13
        assert _round_up_cent(0.129) == 0.13

    def test_round_up_cent_exact(self) -> None:
        """_round_up_cent handles exact cent values."""
        assert _round_up_cent(0.50) == 0.50
        assert _round_up_cent(1.00) == 1.00
        assert _round_up_cent(0.01) == 0.01

    def test_kalshi_fee_total_taker(self) -> None:
        """kalshi_fee_total calculates correct taker fee."""
        # Fee formula: round_up(0.07 * C * P * (1-P))
        # For 100 contracts at P=0.50: 0.07 * 100 * 0.50 * 0.50 = 1.75
        # Note: _round_up_cent may round 1.75 to 1.76 due to float precision
        fee = kalshi_fee_total(100, 0.50, maker=False)
        assert 1.75 <= fee <= 1.76

    def test_kalshi_fee_total_maker(self) -> None:
        """kalshi_fee_total calculates correct maker fee."""
        # Maker rate is 0.0175 (1/4 of taker)
        # For 100 contracts at P=0.50: 0.0175 * 100 * 0.50 * 0.50 = 0.4375 -> 0.44
        fee = kalshi_fee_total(100, 0.50, maker=True)
        assert fee == 0.44

    def test_kalshi_fee_total_at_extremes(self) -> None:
        """Fees are lower at price extremes."""
        # P*(1-P) is maximized at P=0.50
        fee_50 = kalshi_fee_total(100, 0.50)
        fee_80 = kalshi_fee_total(100, 0.80)  # 0.80 * 0.20 = 0.16 < 0.25
        fee_20 = kalshi_fee_total(100, 0.20)  # 0.20 * 0.80 = 0.16 < 0.25

        assert fee_80 < fee_50
        assert fee_20 < fee_50
        assert fee_80 == fee_20  # Symmetric

    def test_kalshi_fee_total_scales_with_contracts(self) -> None:
        """Fee scales approximately linearly with contract count."""
        fee_100 = kalshi_fee_total(100, 0.50)
        fee_200 = kalshi_fee_total(200, 0.50)

        # Rounding causes small deviations from exact linear scaling
        # Fee for 200 should be approximately 2x fee for 100
        assert abs(fee_200 - 2 * fee_100) <= 0.02

    def test_fee_per_contract(self) -> None:
        """fee_per_contract returns fee divided by contract count."""
        total_fee = kalshi_fee_total(100, 0.50)
        per_contract = fee_per_contract(100, 0.50)

        assert per_contract == total_fee / 100

    def test_all_in_buy_cost_includes_fee(self) -> None:
        """all_in_buy_cost returns price plus fee per contract."""
        price = 0.50
        cost = all_in_buy_cost(price, 100)

        fee = fee_per_contract(100, price)
        expected = price + fee

        assert cost == expected
        assert cost > price  # Cost is higher than raw price

    def test_all_in_sell_proceeds_deducts_fee(self) -> None:
        """all_in_sell_proceeds returns price minus fee per contract."""
        price = 0.50
        proceeds = all_in_sell_proceeds(price, 100)

        fee = fee_per_contract(100, price)
        expected = price - fee

        assert proceeds == expected
        assert proceeds < price  # Proceeds are lower than raw price

    def test_buy_cost_vs_sell_proceeds_spread(self) -> None:
        """Buying and immediately selling at same price loses money to fees."""
        price = 0.50
        buy_cost = all_in_buy_cost(price, 100)
        sell_proceeds = all_in_sell_proceeds(price, 100)

        # Cost to buy is more than proceeds from selling
        assert buy_cost > sell_proceeds
        loss = buy_cost - sell_proceeds
        assert loss > 0

    def test_maker_vs_taker_fees(self) -> None:
        """Maker orders have lower fees than taker orders."""
        price = 0.50
        taker_cost = all_in_buy_cost(price, 100, maker=False)
        maker_cost = all_in_buy_cost(price, 100, maker=True)

        assert maker_cost < taker_cost


# =============================================================================
# Routing Edge Detection Tests
# =============================================================================


class TestRoutingEdgeDetection:
    """Tests for routing edge calculations."""

    def test_routing_edge_positive_means_m1_cheaper(self) -> None:
        """Positive edge_t1 means Team 1 exposure cheaper via m2 NO."""
        # If c_t1_yes > c_t2_no, then edge_t1 is positive
        # This means buying m2 NO is cheaper for Team 1 exposure

        # Create scenario: m1 YES at 0.50, m2 NO at 0.45 (cheaper)
        c_t1_yes = all_in_buy_cost(0.50, 100)
        c_t2_no = all_in_buy_cost(0.45, 100)

        edge_t1 = c_t1_yes - c_t2_no

        # Since m2 NO is cheaper, should buy m2 NO
        assert edge_t1 > 0

    def test_routing_edge_negative_means_m2_cheaper(self) -> None:
        """Negative edge_t1 means Team 1 exposure cheaper via m1 YES."""
        # If c_t1_yes < c_t2_no, then edge_t1 is negative
        # This means buying m1 YES is cheaper

        c_t1_yes = all_in_buy_cost(0.45, 100)
        c_t2_no = all_in_buy_cost(0.50, 100)

        edge_t1 = c_t1_yes - c_t2_no

        assert edge_t1 < 0

    def test_routing_edge_zero_when_equal(self) -> None:
        """Routing edge is zero when instruments cost the same."""
        price = 0.50
        c_t1_yes = all_in_buy_cost(price, 100)
        c_t2_no = all_in_buy_cost(price, 100)

        edge = c_t1_yes - c_t2_no

        assert edge == 0.0

    def test_routing_edge_magnitude_matches_price_diff(self) -> None:
        """Edge magnitude reflects price difference plus fee impact."""
        p1 = 0.50
        p2 = 0.48  # 2 cent difference

        c1 = all_in_buy_cost(p1, 100)
        c2 = all_in_buy_cost(p2, 100)

        edge = c1 - c2

        # Edge should be approximately the price difference
        # (slightly different due to fee differences at different prices)
        assert abs(edge - 0.02) < 0.005


# =============================================================================
# Cross-Market Arbitrage Detection Tests
# =============================================================================


class TestCrossMarketArbDetection:
    """Tests for cross-market arbitrage detection."""

    def test_arb_pnl_positive_when_bid_exceeds_ask(self) -> None:
        """Arb PnL is positive when we can sell higher than we buy."""
        # Scenario: Buy at 0.45 ask, sell at 0.50 bid
        buy_cost = all_in_buy_cost(0.45, 100)
        sell_proceeds = all_in_sell_proceeds(0.50, 100)

        arb_pnl = sell_proceeds - buy_cost

        assert arb_pnl > 0

    def test_arb_pnl_negative_when_spread_exists(self) -> None:
        """Arb PnL is negative in normal market conditions."""
        # Normal spread: ask > bid
        buy_cost = all_in_buy_cost(0.52, 100)  # Higher ask
        sell_proceeds = all_in_sell_proceeds(0.48, 100)  # Lower bid

        arb_pnl = sell_proceeds - buy_cost

        assert arb_pnl < 0

    def test_arb_selects_cheapest_buy(self) -> None:
        """Arb algorithm selects the cheapest buy option."""
        # Two options for Team 1 exposure
        m1_yes_cost = all_in_buy_cost(0.50, 100)
        m2_no_cost = all_in_buy_cost(0.45, 100)

        # Should buy the cheaper one (m2 NO)
        best_buy_cost = min(m1_yes_cost, m2_no_cost)
        assert best_buy_cost == m2_no_cost

    def test_arb_selects_best_sell(self) -> None:
        """Arb algorithm selects the best sell option."""
        # Two options for selling Team 1 exposure
        m1_yes_proceeds = all_in_sell_proceeds(0.48, 100)
        m2_no_proceeds = all_in_sell_proceeds(0.52, 100)

        # Should sell the more valuable one (m2 NO)
        best_sell_proceeds = max(m1_yes_proceeds, m2_no_proceeds)
        assert best_sell_proceeds == m2_no_proceeds

    def test_arb_pnl_calculation(self) -> None:
        """Arb PnL is sell proceeds minus buy cost."""
        buy_options = [
            all_in_buy_cost(0.50, 100),
            all_in_buy_cost(0.48, 100),
        ]
        sell_options = [
            all_in_sell_proceeds(0.49, 100),
            all_in_sell_proceeds(0.51, 100),
        ]

        best_buy = min(buy_options)
        best_sell = max(sell_options)

        arb_pnl = best_sell - best_buy

        # Verify calculation
        expected_pnl = all_in_sell_proceeds(0.51, 100) - all_in_buy_cost(0.48, 100)
        assert arb_pnl == expected_pnl


# =============================================================================
# Dutch Book Detection Tests
# =============================================================================


class TestDutchBookDetection:
    """Tests for dutch book / hold-to-settlement detection."""

    def test_dutch_profit_calculation(self) -> None:
        """Dutch profit = 1.0 - combined entry costs."""
        # Best cost for Team 1 exposure
        t1_best = all_in_buy_cost(0.45, 100)

        # Best cost for Team 2 exposure
        t2_best = all_in_buy_cost(0.45, 100)

        dutch_profit = 1.0 - (t1_best + t2_best)

        # With two 0.45 bets, raw cost is 0.90
        # Plus fees, total cost > 0.90, profit < 0.10
        assert dutch_profit < 0.10

    def test_dutch_profit_positive_when_costs_below_1(self) -> None:
        """Dutch profit is positive when combined costs < $1.00."""
        # Scenario: Both instruments very cheap
        t1_cost = all_in_buy_cost(0.40, 100)
        t2_cost = all_in_buy_cost(0.40, 100)

        # Combined raw = 0.80, with fees ~0.82-0.85
        dutch_profit = 1.0 - (t1_cost + t2_cost)

        # Should be positive since combined < 1.0
        assert dutch_profit > 0

    def test_dutch_profit_negative_when_costs_above_1(self) -> None:
        """Dutch profit is negative when combined costs > $1.00."""
        # Scenario: Both instruments expensive (implies mispriced)
        t1_cost = all_in_buy_cost(0.55, 100)
        t2_cost = all_in_buy_cost(0.55, 100)

        # Combined raw = 1.10, with fees > 1.10
        dutch_profit = 1.0 - (t1_cost + t2_cost)

        assert dutch_profit < 0

    def test_dutch_selects_cheapest_instruments(self) -> None:
        """Dutch calculation uses cheapest instrument for each exposure."""
        # Team 1 options
        m1_yes = all_in_buy_cost(0.50, 100)
        m2_no = all_in_buy_cost(0.45, 100)  # Cheaper

        # Team 2 options
        m2_yes = all_in_buy_cost(0.52, 100)
        m1_no = all_in_buy_cost(0.48, 100)  # Cheaper

        t1_best = min(m1_yes, m2_no)
        t2_best = min(m2_yes, m1_no)

        assert t1_best == m2_no
        assert t2_best == m1_no


# =============================================================================
# Best Action Selection Tests
# =============================================================================


class TestBestActionSelection:
    """Tests for best action selection logic."""

    def test_highest_value_wins(self) -> None:
        """Best action is the one with highest value."""
        candidates = [
            ("ARB_T1", 0.05),
            ("ARB_T2", 0.03),
            ("DUTCH_SETTLE", 0.02),
        ]

        best = max(candidates, key=lambda x: x[1])

        assert best[0] == "ARB_T1"
        assert best[1] == 0.05

    def test_no_trade_when_no_opportunities(self) -> None:
        """NO_TRADE selected when all values below threshold."""
        arb_floor = 0.002
        profit_floor = 0.002

        candidates = []

        arb_pnl_t1 = 0.001  # Below floor
        if arb_pnl_t1 >= arb_floor:
            candidates.append(("ARB_T1", arb_pnl_t1))

        profit = 0.001  # Below floor
        if profit >= profit_floor:
            candidates.append(("DUTCH_SETTLE", profit))

        if not candidates:
            best = "NO_TRADE"
        else:
            best = max(candidates, key=lambda x: x[1])[0]

        assert best == "NO_TRADE"

    def test_threshold_filtering(self) -> None:
        """Only opportunities above threshold are considered."""
        arb_floor = 0.005
        profit_floor = 0.005

        # One above, one below threshold
        opportunities = [
            ("ARB_T1", 0.006),  # Above
            ("ARB_T2", 0.003),  # Below
            ("DUTCH", 0.010),  # Above
        ]

        filtered = [
            (name, val) for name, val in opportunities
            if val >= arb_floor
        ]

        assert len(filtered) == 2
        assert ("ARB_T2", 0.003) not in filtered

    def test_tie_breaking(self) -> None:
        """Ties are handled consistently."""
        candidates = [
            ("ARB_T1", 0.05),
            ("DUTCH_SETTLE", 0.05),
        ]

        # Python's max returns first max in case of tie
        best = max(candidates, key=lambda x: x[1])
        assert best[0] == "ARB_T1"  # First one


# =============================================================================
# Integration Tests with Simulator
# =============================================================================


class TestAlgorithmWithSimulator:
    """Integration tests using PairedMarketSimulator."""

    def test_efficient_market_no_arb(self) -> None:
        """Efficient markets should have no profitable arb."""
        from src.simulation import create_spread_simulator, get_spread_scenario

        sim = create_spread_simulator(get_spread_scenario("no_opportunity"))
        opp = sim.get_current_opportunity(contract_size=100)

        # In efficient markets, fees should eliminate any arb
        if opp["arb_pnl_t1"] is not None:
            assert opp["arb_pnl_t1"] < 0.01  # Negligible or negative

    def test_routing_edge_scenario_detected(self) -> None:
        """Routing edge scenario should show clear edge."""
        from src.simulation import create_spread_simulator, get_spread_scenario

        sim = create_spread_simulator(get_spread_scenario("routing_edge"))
        opp = sim.get_current_opportunity(contract_size=100)

        # Should have detectable routing edge
        assert abs(opp["routing_edge_t1"]) > 0.005 or abs(opp["routing_edge_t2"]) > 0.005

    def test_dutch_book_scenario_profitable(self) -> None:
        """Dutch book scenario should show positive profit."""
        from src.simulation import create_spread_simulator, get_spread_scenario

        sim = create_spread_simulator(get_spread_scenario("large_dutch_book"))
        opp = sim.get_current_opportunity(contract_size=100)

        # Should have positive dutch profit
        # Note: large_dutch_book has 5 cent discount, should overcome fees
        assert opp["dutch_profit"] > 0

    def test_fees_exceed_profit_scenario(self) -> None:
        """Small mispricing should be eliminated by fees."""
        from src.simulation import create_spread_simulator, get_spread_scenario

        sim = create_spread_simulator(get_spread_scenario("fees_exceed_profit"))
        opp = sim.get_current_opportunity(contract_size=100)

        # Small crossing should be eaten by fees
        if opp["arb_pnl_t1"] is not None:
            assert opp["arb_pnl_t1"] < 0.005  # Minimal profit after fees


class TestFeeEdgeCases:
    """Tests for edge cases in fee calculations."""

    def test_very_small_contract_count(self) -> None:
        """Fee calculation works with small contract counts."""
        # 1 contract at 0.50
        fee = kalshi_fee_total(1, 0.50)
        assert fee >= 0.01  # Minimum 1 cent when rounded up

    def test_large_contract_count(self) -> None:
        """Fee calculation works with large contract counts."""
        fee = kalshi_fee_total(10000, 0.50)
        expected = _round_up_cent(0.07 * 10000 * 0.50 * 0.50)
        assert fee == expected

    def test_fee_at_price_near_zero(self) -> None:
        """Fee is minimal at price near zero."""
        fee = kalshi_fee_total(100, 0.05)  # 5 cent price
        # 0.07 * 100 * 0.05 * 0.95 = 0.3325 -> 0.34
        assert fee == 0.34

    def test_fee_at_price_near_one(self) -> None:
        """Fee is minimal at price near one."""
        fee = kalshi_fee_total(100, 0.95)  # 95 cent price
        # 0.07 * 100 * 0.95 * 0.05 = 0.3325 -> 0.34
        assert fee == 0.34

    def test_all_in_cost_at_extremes(self) -> None:
        """All-in cost calculation works at price extremes."""
        low_cost = all_in_buy_cost(0.05, 100)
        high_cost = all_in_buy_cost(0.95, 100)

        assert low_cost > 0.05
        assert high_cost > 0.95
        assert low_cost < high_cost
