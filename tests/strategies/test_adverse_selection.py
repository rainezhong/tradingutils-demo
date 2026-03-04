"""Tests for AdverseSelectionDetector."""

import time

from strategies.prediction_mm.adverse_selection import AdverseSelectionDetector


class TestTradeImbalance:
    def test_balanced_flow_no_premium(self):
        """Equal buys and sells → zero imbalance premium."""
        det = AdverseSelectionDetector(
            trade_window_sec=60, imbalance_premium_scale=0.02
        )
        now = time.time()
        for i in range(20):
            det.record_trade("T", is_buy=True, size=1, ts=now + i)
            det.record_trade("T", is_buy=False, size=1, ts=now + i)
        prem = det.get_premium("T")
        assert prem == 0.0

    def test_directional_buy_flow_premium(self):
        """All buys → maximum imbalance premium."""
        det = AdverseSelectionDetector(
            trade_window_sec=60,
            imbalance_premium_scale=0.02,
            max_adverse_premium_vol=0.05,
        )
        now = time.time()
        for i in range(20):
            det.record_trade("T", is_buy=True, size=1, ts=now + i)
        prem = det.get_premium("T")
        assert prem > 0
        assert prem <= 0.05  # capped

    def test_directional_sell_flow_premium(self):
        """All sells → same magnitude premium (symmetric)."""
        det = AdverseSelectionDetector(
            trade_window_sec=60, imbalance_premium_scale=0.02
        )
        now = time.time()
        for i in range(20):
            det.record_trade("T", is_buy=False, size=1, ts=now + i)
        prem = det.get_premium("T")
        assert prem > 0

    def test_capped_at_max(self):
        """Premium never exceeds max_adverse_premium_vol."""
        det = AdverseSelectionDetector(
            trade_window_sec=60,
            imbalance_premium_scale=0.10,  # very high scale
            max_adverse_premium_vol=0.03,
        )
        now = time.time()
        for i in range(50):
            det.record_trade("T", is_buy=True, size=10, ts=now + i)
        prem = det.get_premium("T")
        assert prem <= 0.03

    def test_no_trades_no_premium(self):
        """No recorded trades → zero premium."""
        det = AdverseSelectionDetector()
        assert det.get_premium("T") == 0.0


class TestFillAsymmetry:
    def test_symmetric_fills_no_premium(self):
        """Equal bid and ask fills → no fill asymmetry premium."""
        det = AdverseSelectionDetector(fill_asymmetry_threshold=1.5)
        now = time.time()
        for i in range(10):
            det.record_fill("T", is_our_bid=True, ts=now + i)
            det.record_fill("T", is_our_bid=False, ts=now + i)
        prem = det.get_premium("T")
        assert prem == 0.0

    def test_asymmetric_fills_premium(self):
        """Heavily skewed fills → nonzero premium."""
        det = AdverseSelectionDetector(
            fill_asymmetry_threshold=1.5,
            imbalance_premium_scale=0.02,
        )
        now = time.time()
        # 10 bid fills, 2 ask fills → ratio = 5.0, well above 1.5
        for i in range(10):
            det.record_fill("T", is_our_bid=True, ts=now + i)
        for i in range(2):
            det.record_fill("T", is_our_bid=False, ts=now + i)
        prem = det.get_premium("T")
        assert prem > 0
