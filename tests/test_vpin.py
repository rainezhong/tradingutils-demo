"""Tests for VPINCalculator — Volume-Synchronized Probability of Informed Trading."""

from core.indicators.vpin import VPINCalculator, VPINConfig


class TestVPINBasics:
    def test_no_reading_before_first_bucket(self):
        vpin = VPINCalculator(VPINConfig(bucket_volume=10.0, num_buckets=5))
        assert vpin.get_reading() is None

    def test_single_bucket_all_buys(self):
        """If all volume in a bucket is buy-initiated, imbalance = 1.0."""
        vpin = VPINCalculator(VPINConfig(bucket_volume=10.0, num_buckets=5))
        # Feed 10 units of buy volume
        for _ in range(10):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)

        reading = vpin.get_reading()
        assert reading is not None
        assert reading.vpin == 1.0
        assert reading.is_toxic is True
        assert reading.num_buckets == 1

    def test_single_bucket_balanced(self):
        """Equal buy/sell → imbalance = 0."""
        vpin = VPINCalculator(VPINConfig(bucket_volume=10.0, num_buckets=5))
        for _ in range(5):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)
        for _ in range(5):
            vpin.on_trade(price=100.0, size=1.0, is_buy=False)

        reading = vpin.get_reading()
        assert reading is not None
        assert reading.vpin == 0.0
        assert reading.is_toxic is False
        assert reading.is_warning is False

    def test_multiple_buckets_averaged(self):
        """VPIN is mean of bucket imbalances."""
        vpin = VPINCalculator(VPINConfig(bucket_volume=10.0, num_buckets=5))

        # Bucket 1: all buys → imbalance = 1.0
        for _ in range(10):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)

        # Bucket 2: all sells → imbalance = 1.0
        for _ in range(10):
            vpin.on_trade(price=100.0, size=1.0, is_buy=False)

        # Bucket 3: balanced → imbalance = 0.0
        for _ in range(5):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)
        for _ in range(5):
            vpin.on_trade(price=100.0, size=1.0, is_buy=False)

        reading = vpin.get_reading()
        assert reading is not None
        assert reading.num_buckets == 3
        # Mean of [1.0, 1.0, 0.0] = 0.6667
        assert abs(reading.vpin - 2.0 / 3.0) < 0.01


class TestTradeClassification:
    def test_lee_ready_at_ask_is_buy(self):
        vpin = VPINCalculator(VPINConfig(bucket_volume=5.0, num_buckets=5))
        # All trades at ask → classified as buys
        for _ in range(5):
            vpin.on_trade(price=100.50, size=1.0, bid=100.40, ask=100.50)

        reading = vpin.get_reading()
        assert reading is not None
        assert reading.vpin == 1.0  # All buys

    def test_lee_ready_at_bid_is_sell(self):
        vpin = VPINCalculator(VPINConfig(bucket_volume=5.0, num_buckets=5))
        # All trades at bid → classified as sells
        for _ in range(5):
            vpin.on_trade(price=100.40, size=1.0, bid=100.40, ask=100.50)

        reading = vpin.get_reading()
        assert reading is not None
        assert reading.vpin == 1.0  # All sells (still high imbalance)

    def test_tick_rule_fallback(self):
        """When no bid/ask, use tick rule: price up → buy, down → sell."""
        vpin = VPINCalculator(VPINConfig(bucket_volume=4.0, num_buckets=5))
        # Alternating up/down should produce balanced flow
        vpin.on_trade(price=100.0, size=1.0)  # first trade, no info
        vpin.on_trade(price=101.0, size=1.0)  # up → buy
        vpin.on_trade(price=100.0, size=1.0)  # down → sell
        vpin.on_trade(price=101.0, size=1.0)  # up → buy

        reading = vpin.get_reading()
        assert reading is not None
        # 3 buys, 1 sell → imbalance = |3-1|/4 = 0.5
        assert abs(reading.vpin - 0.5) < 0.01


class TestLargeTradesSplitBuckets:
    def test_single_large_trade_fills_multiple_buckets(self):
        """A trade larger than bucket_volume should fill multiple buckets."""
        vpin = VPINCalculator(VPINConfig(bucket_volume=5.0, num_buckets=10))
        # One trade of 15 units → fills 3 buckets
        vpin.on_trade(price=100.0, size=15.0, is_buy=True)

        reading = vpin.get_reading()
        assert reading is not None
        assert reading.num_buckets == 3
        assert reading.vpin == 1.0  # All buy

    def test_partial_bucket_not_counted(self):
        """In-progress bucket shouldn't affect VPIN."""
        vpin = VPINCalculator(VPINConfig(bucket_volume=10.0, num_buckets=5))
        # 7 units — not enough for a full bucket
        vpin.on_trade(price=100.0, size=7.0, is_buy=True)
        assert vpin.get_reading() is None

        # 3 more units complete the bucket
        vpin.on_trade(price=100.0, size=3.0, is_buy=True)
        reading = vpin.get_reading()
        assert reading is not None
        assert reading.num_buckets == 1


class TestBucketWindowLimit:
    def test_old_buckets_dropped(self):
        """Only keep num_buckets worth of history."""
        vpin = VPINCalculator(VPINConfig(bucket_volume=1.0, num_buckets=3))

        # Fill 5 buckets: first 3 are all-buy (imb=1), last 2 are balanced (imb=0)
        for _ in range(3):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)
        for _ in range(2):
            vpin.on_trade(price=100.0, size=0.5, is_buy=True)
            vpin.on_trade(price=100.0, size=0.5, is_buy=False)

        reading = vpin.get_reading()
        assert reading is not None
        assert reading.num_buckets == 3  # Capped at num_buckets
        # Last 3 buckets: [1.0, 0.0, 0.0] → mean = 0.333
        assert abs(reading.vpin - 1.0 / 3.0) < 0.01


class TestThresholds:
    def test_toxic_threshold(self):
        cfg = VPINConfig(bucket_volume=10.0, num_buckets=5, toxic_threshold=0.70)
        vpin = VPINCalculator(cfg)
        # All buys → VPIN = 1.0, above toxic
        for _ in range(10):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)
        reading = vpin.get_reading()
        assert reading.is_toxic is True

    def test_warning_threshold(self):
        cfg = VPINConfig(
            bucket_volume=10.0, num_buckets=5,
            warning_threshold=0.40, toxic_threshold=0.70,
        )
        vpin = VPINCalculator(cfg)
        # 8 buys, 2 sells → imbalance = 0.6 → above warning, below toxic
        for _ in range(8):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)
        for _ in range(2):
            vpin.on_trade(price=100.0, size=1.0, is_buy=False)
        reading = vpin.get_reading()
        assert reading.is_warning is True
        assert reading.is_toxic is False


class TestReset:
    def test_reset_clears_state(self):
        vpin = VPINCalculator(VPINConfig(bucket_volume=5.0, num_buckets=5))
        for _ in range(5):
            vpin.on_trade(price=100.0, size=1.0, is_buy=True)
        assert vpin.get_reading() is not None

        vpin.reset()
        assert vpin.get_reading() is None
