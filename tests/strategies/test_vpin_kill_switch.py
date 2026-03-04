"""Tests for VPIN kill switch integration in prediction MM."""

import time
from unittest.mock import MagicMock, Mock, patch

import pytest

from strategies.prediction_mm.config import PredictionMMConfig
from strategies.prediction_mm.orchestrator import KillSwitchState, PredictionMMOrchestrator


@pytest.fixture
def mock_dependencies():
    """Mock all external dependencies."""
    with patch("strategies.prediction_mm.orchestrator.KalshiClient", None), \
         patch("strategies.prediction_mm.orchestrator.KalshiCryptoScanner", None), \
         patch("strategies.prediction_mm.orchestrator.KrakenPriceFeed", None):
        yield


@pytest.fixture
def kill_switch_config():
    """Config with VPIN kill switch enabled."""
    return PredictionMMConfig(
        enable_vpin_kill_switch=True,
        vpin_check_interval_sec=0.1,
        vpin_bucket_volume=10.0,
        vpin_num_buckets=50,
        vpin_warning_threshold=0.50,
        vpin_toxic_threshold=0.70,
        vpin_toxic_cooldown_sec=5.0,
        vpin_warning_spread_multiplier=2.5,
        dry_run=True,
    )


@pytest.fixture
def orchestrator_with_vpin(mock_dependencies, kill_switch_config):
    """Create orchestrator with VPIN kill switch enabled."""
    om = MagicMock()
    orch = PredictionMMOrchestrator(
        config=kill_switch_config,
        kalshi_client=None,
        order_manager=om,
    )
    return orch


class TestVPINKillSwitchConfig:
    """Test configuration loading."""

    def test_default_config_disabled(self):
        """VPIN kill switch should be disabled by default."""
        config = PredictionMMConfig()
        assert config.enable_vpin_kill_switch is False

    def test_enabled_config(self, kill_switch_config):
        """Test VPIN kill switch config parameters."""
        assert kill_switch_config.enable_vpin_kill_switch is True
        assert kill_switch_config.vpin_check_interval_sec == 0.1
        assert kill_switch_config.vpin_bucket_volume == 10.0
        assert kill_switch_config.vpin_num_buckets == 50
        assert kill_switch_config.vpin_warning_threshold == 0.50
        assert kill_switch_config.vpin_toxic_threshold == 0.70
        assert kill_switch_config.vpin_toxic_cooldown_sec == 5.0
        assert kill_switch_config.vpin_warning_spread_multiplier == 2.5

    def test_yaml_serialization(self, kill_switch_config):
        """Test YAML round-trip for kill switch config."""
        yaml_dict = kill_switch_config.to_yaml_dict()
        assert "vpin_kill_switch" in yaml_dict
        vpin = yaml_dict["vpin_kill_switch"]
        assert vpin["enabled"] is True
        assert vpin["check_interval_sec"] == 0.1
        assert vpin["warning_threshold"] == 0.50
        assert vpin["toxic_threshold"] == 0.70
        assert vpin["toxic_cooldown_sec"] == 5.0
        assert vpin["warning_spread_multiplier"] == 2.5

        # Round-trip
        config2 = PredictionMMConfig.from_yaml_dict(yaml_dict)
        assert config2.enable_vpin_kill_switch is True
        assert config2.vpin_warning_threshold == 0.50
        assert config2.vpin_toxic_threshold == 0.70


class TestVPINKillSwitchInitialization:
    """Test VPIN calculator initialization."""

    def test_vpin_created_when_enabled(self, orchestrator_with_vpin):
        """VPIN calculator should be created when enabled."""
        assert orchestrator_with_vpin._vpin is not None
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.NORMAL

    def test_vpin_not_created_when_disabled(self, mock_dependencies):
        """VPIN calculator should not be created when disabled."""
        config = PredictionMMConfig(enable_vpin_kill_switch=False)
        orch = PredictionMMOrchestrator(config=config)
        assert orch._vpin is None

    def test_initial_state_is_normal(self, orchestrator_with_vpin):
        """Initial kill switch state should be NORMAL."""
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.NORMAL
        assert orchestrator_with_vpin._toxic_until == 0.0


class TestVPINFeedOnFill:
    """Test VPIN receives trade data from fills."""

    def test_vpin_receives_fill_data(self, orchestrator_with_vpin):
        """VPIN should receive trade data when fill occurs."""
        # Mock market
        mock_market = Mock()
        mock_market.yes_bid = 45
        mock_market.yes_ask = 55
        orchestrator_with_vpin._markets = {"TEST-MARKET": mock_market}

        # Mock VPIN
        orchestrator_with_vpin._vpin = Mock()

        # Trigger fill
        orchestrator_with_vpin._on_fill("TEST-MARKET", is_buy=True, size=10, price_cents=50)

        # Verify VPIN received the trade
        orchestrator_with_vpin._vpin.on_trade.assert_called_once_with(
            price=50, size=10, bid=45, ask=55, is_buy=True
        )

    def test_vpin_not_fed_when_disabled(self, mock_dependencies):
        """VPIN should not receive data when disabled."""
        config = PredictionMMConfig(enable_vpin_kill_switch=False)
        orch = PredictionMMOrchestrator(config=config)
        orch._vpin = None

        # Mock market
        mock_market = Mock()
        mock_market.yes_bid = 45
        mock_market.yes_ask = 55
        orch._markets = {"TEST-MARKET": mock_market}

        # Trigger fill - should not crash
        orch._on_fill("TEST-MARKET", is_buy=True, size=10, price_cents=50)


class TestVPINStateTransitions:
    """Test kill switch state machine."""

    def test_normal_to_warning(self, orchestrator_with_vpin):
        """Test transition from NORMAL to WARNING."""
        # Mock VPIN reading in warning zone
        mock_reading = Mock()
        mock_reading.vpin = 0.60
        mock_reading.is_toxic = False
        mock_reading.is_warning = True
        orchestrator_with_vpin._vpin.get_reading = Mock(return_value=mock_reading)

        # Check kill switch
        orchestrator_with_vpin._check_vpin_kill_switch()

        # Should transition to WARNING
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.WARNING

    def test_warning_to_toxic(self, orchestrator_with_vpin):
        """Test transition from WARNING to TOXIC."""
        # Start in WARNING
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.WARNING

        # Mock VPIN reading in toxic zone
        mock_reading = Mock()
        mock_reading.vpin = 0.75
        mock_reading.is_toxic = True
        mock_reading.is_warning = True
        orchestrator_with_vpin._vpin.get_reading = Mock(return_value=mock_reading)

        # Check kill switch
        orchestrator_with_vpin._check_vpin_kill_switch()

        # Should transition to TOXIC
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.TOXIC
        # Should set cooldown
        assert orchestrator_with_vpin._toxic_until > time.time()

    def test_toxic_to_normal_after_cooldown(self, orchestrator_with_vpin):
        """Test transition from TOXIC to NORMAL after cooldown expires."""
        # Start in TOXIC with expired cooldown
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.TOXIC
        orchestrator_with_vpin._toxic_until = time.time() - 1.0

        # Mock VPIN reading back to normal
        mock_reading = Mock()
        mock_reading.vpin = 0.30
        mock_reading.is_toxic = False
        mock_reading.is_warning = False
        orchestrator_with_vpin._vpin.get_reading = Mock(return_value=mock_reading)

        # Check kill switch
        orchestrator_with_vpin._check_vpin_kill_switch()

        # Should transition to NORMAL
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.NORMAL

    def test_stays_toxic_during_cooldown(self, orchestrator_with_vpin):
        """Test kill switch stays TOXIC during cooldown even if VPIN drops."""
        # Start in TOXIC with active cooldown
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.TOXIC
        orchestrator_with_vpin._toxic_until = time.time() + 10.0

        # Mock VPIN reading back to normal
        mock_reading = Mock()
        mock_reading.vpin = 0.30
        mock_reading.is_toxic = False
        mock_reading.is_warning = False
        orchestrator_with_vpin._vpin.get_reading = Mock(return_value=mock_reading)

        # Check kill switch
        orchestrator_with_vpin._check_vpin_kill_switch()

        # Should stay TOXIC due to cooldown
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.TOXIC


class TestVPINQuoteCancellation:
    """Test automatic quote cancellation in TOXIC state."""

    def test_cancel_all_on_toxic(self, orchestrator_with_vpin):
        """All quotes should be cancelled when entering TOXIC state."""
        # Mock VPIN reading in toxic zone
        mock_reading = Mock()
        mock_reading.vpin = 0.80
        mock_reading.is_toxic = True
        mock_reading.is_warning = True
        orchestrator_with_vpin._vpin.get_reading = Mock(return_value=mock_reading)

        # Mock executor
        orchestrator_with_vpin._executor.cancel_all = Mock()

        # Check kill switch
        orchestrator_with_vpin._check_vpin_kill_switch()

        # Should cancel all quotes
        orchestrator_with_vpin._executor.cancel_all.assert_called_once()

    def test_no_quotes_generated_in_toxic(self, orchestrator_with_vpin):
        """No quotes should be generated in TOXIC state."""
        # Set TOXIC state
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.TOXIC

        # Get spread multiplier
        multiplier = orchestrator_with_vpin._get_vpin_spread_multiplier()
        assert multiplier == 1.0  # Not used in TOXIC

        # TODO: Full on_tick test would verify should_quote_bid/ask = False


class TestVPINSpreadWidening:
    """Test spread widening in WARNING state."""

    def test_spread_multiplier_in_warning(self, orchestrator_with_vpin):
        """Spread multiplier should be applied in WARNING state."""
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.WARNING
        multiplier = orchestrator_with_vpin._get_vpin_spread_multiplier()
        assert multiplier == 2.5

    def test_spread_multiplier_in_normal(self, orchestrator_with_vpin):
        """Spread multiplier should be 1.0 in NORMAL state."""
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.NORMAL
        multiplier = orchestrator_with_vpin._get_vpin_spread_multiplier()
        assert multiplier == 1.0

    def test_spread_multiplier_in_toxic(self, orchestrator_with_vpin):
        """Spread multiplier should be 1.0 in TOXIC (quotes are pulled anyway)."""
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.TOXIC
        multiplier = orchestrator_with_vpin._get_vpin_spread_multiplier()
        assert multiplier == 1.0


class TestVPINRateLimiting:
    """Test VPIN check rate limiting."""

    def test_rate_limited_checks(self, orchestrator_with_vpin):
        """VPIN checks should be rate-limited."""
        # Mock VPIN reading
        mock_reading = Mock()
        mock_reading.vpin = 0.30
        mock_reading.is_toxic = False
        mock_reading.is_warning = False
        orchestrator_with_vpin._vpin.get_reading = Mock(return_value=mock_reading)

        # First check should succeed
        orchestrator_with_vpin._check_vpin_kill_switch()
        assert orchestrator_with_vpin._vpin.get_reading.call_count == 1

        # Immediate second check should be rate-limited
        orchestrator_with_vpin._check_vpin_kill_switch()
        # Still only 1 call (rate-limited)
        assert orchestrator_with_vpin._vpin.get_reading.call_count == 1

        # After interval, should check again
        orchestrator_with_vpin._last_vpin_check = time.time() - 1.0
        orchestrator_with_vpin._check_vpin_kill_switch()
        assert orchestrator_with_vpin._vpin.get_reading.call_count == 2


class TestVPINLogging:
    """Test VPIN kill switch logging."""

    def test_state_transition_logged(self, orchestrator_with_vpin):
        """State transitions should be logged."""
        orchestrator_with_vpin.log = Mock()

        # Transition to WARNING
        orchestrator_with_vpin._transition_kill_switch_state(
            KillSwitchState.WARNING, vpin=0.60, reason="test"
        )

        # Check log was called
        orchestrator_with_vpin.log.assert_called()
        log_msg = orchestrator_with_vpin.log.call_args[0][0]
        assert "VPIN KILL SWITCH" in log_msg
        assert "NORMAL" in log_msg
        assert "WARNING" in log_msg
        assert "0.600" in log_msg

    def test_no_log_on_same_state(self, orchestrator_with_vpin):
        """No log if state doesn't change."""
        orchestrator_with_vpin.log = Mock()
        orchestrator_with_vpin._kill_switch_state = KillSwitchState.NORMAL

        # Transition to same state
        orchestrator_with_vpin._transition_kill_switch_state(
            KillSwitchState.NORMAL, vpin=0.30, reason="test"
        )

        # Should not log
        orchestrator_with_vpin.log.assert_not_called()


class TestVPINIntegration:
    """Integration tests for VPIN kill switch."""

    def test_full_workflow_normal_to_toxic_to_normal(self, orchestrator_with_vpin):
        """Test full workflow: NORMAL → WARNING → TOXIC → cooldown → NORMAL."""
        # Start NORMAL
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.NORMAL

        # Mock VPIN: WARNING
        mock_reading = Mock()
        mock_reading.vpin = 0.55
        mock_reading.is_toxic = False
        mock_reading.is_warning = True
        orchestrator_with_vpin._vpin.get_reading = Mock(return_value=mock_reading)
        orchestrator_with_vpin._check_vpin_kill_switch()
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.WARNING
        assert orchestrator_with_vpin._get_vpin_spread_multiplier() == 2.5

        # Wait for rate limit
        time.sleep(0.15)

        # Mock VPIN: TOXIC
        mock_reading.vpin = 0.75
        mock_reading.is_toxic = True
        mock_reading.is_warning = True
        orchestrator_with_vpin._executor.cancel_all = Mock()
        orchestrator_with_vpin._check_vpin_kill_switch()
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.TOXIC
        assert orchestrator_with_vpin._toxic_until > time.time()
        orchestrator_with_vpin._executor.cancel_all.assert_called_once()

        # VPIN drops but still in cooldown
        time.sleep(0.15)
        mock_reading.vpin = 0.30
        mock_reading.is_toxic = False
        mock_reading.is_warning = False
        orchestrator_with_vpin._check_vpin_kill_switch()
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.TOXIC

        # Expire cooldown
        orchestrator_with_vpin._toxic_until = time.time() - 1.0
        time.sleep(0.15)
        orchestrator_with_vpin._check_vpin_kill_switch()
        assert orchestrator_with_vpin._kill_switch_state == KillSwitchState.NORMAL
        assert orchestrator_with_vpin._get_vpin_spread_multiplier() == 1.0

    def test_no_crash_when_vpin_unavailable(self, mock_dependencies):
        """Strategy should not crash when VPIN module is unavailable."""
        with patch("strategies.prediction_mm.orchestrator.VPINCalculator", None):
            config = PredictionMMConfig(enable_vpin_kill_switch=True)
            orch = PredictionMMOrchestrator(config=config)
            # VPIN should not be created
            assert orch._vpin is None
            # Check should not crash
            orch._check_vpin_kill_switch()
            assert orch._kill_switch_state == KillSwitchState.NORMAL
