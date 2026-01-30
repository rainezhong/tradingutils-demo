"""Tests for alert manager."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.monitoring.alerts import (
    AlertManager,
    AlertConfig,
    AlertSeverity,
    Alert,
    ALERT_TEMPLATES,
)


class TestAlertConfig:
    """Tests for AlertConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = AlertConfig()

        assert config.slack_token is None
        assert config.slack_channel == "#trading-alerts"
        assert config.smtp_port == 587
        assert config.cooldown_minutes == 5
        assert "slack" in config.enabled_channels
        assert "email" in config.enabled_channels

    def test_from_dict(self):
        """Test creating config from dictionary."""
        data = {
            "slack_token": "xoxb-test",
            "slack_channel": "#test-alerts",
            "smtp_host": "smtp.test.com",
            "smtp_port": 465,
            "cooldown_minutes": 10,
        }

        config = AlertConfig.from_dict(data)

        assert config.slack_token == "xoxb-test"
        assert config.slack_channel == "#test-alerts"
        assert config.smtp_host == "smtp.test.com"
        assert config.smtp_port == 465
        assert config.cooldown_minutes == 10


class TestAlertManager:
    """Tests for AlertManager class."""

    def setup_method(self):
        """Setup before each test."""
        self.config = AlertConfig(
            slack_token=None,  # Disable Slack for tests
            smtp_host=None,    # Disable email for tests
            cooldown_minutes=5,
        )
        self.manager = AlertManager(self.config)

    @pytest.mark.asyncio
    async def test_send_alert_basic(self):
        """Test sending a basic alert."""
        result = await self.manager.send_alert(
            name="test_alert",
            severity=AlertSeverity.WARNING,
            message="Test alert message",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_send_alert_with_context(self):
        """Test sending alert with context."""
        result = await self.manager.send_alert(
            name="test_alert",
            severity=AlertSeverity.CRITICAL,
            message="Risk limit exceeded",
            context={
                "current_value": 150,
                "limit_value": 100,
            },
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_alert_deduplication(self):
        """Test that duplicate alerts are suppressed."""
        # First alert should go through
        result1 = await self.manager.send_alert(
            name="duplicate_test",
            severity=AlertSeverity.WARNING,
            message="First alert",
        )
        assert result1 is True

        # Second alert with same name should be suppressed
        result2 = await self.manager.send_alert(
            name="duplicate_test",
            severity=AlertSeverity.WARNING,
            message="Second alert",
        )
        assert result2 is False

    @pytest.mark.asyncio
    async def test_different_alerts_not_deduplicated(self):
        """Test that different alert names are not deduplicated."""
        result1 = await self.manager.send_alert(
            name="alert_one",
            severity=AlertSeverity.WARNING,
            message="First alert",
        )
        result2 = await self.manager.send_alert(
            name="alert_two",
            severity=AlertSeverity.WARNING,
            message="Second alert",
        )

        assert result1 is True
        assert result2 is True

    @pytest.mark.asyncio
    async def test_cooldown_expiry(self):
        """Test that alerts can be sent after cooldown expires."""
        # Set a very short cooldown for testing
        self.manager._cooldown = timedelta(milliseconds=1)

        # First alert
        result1 = await self.manager.send_alert(
            name="cooldown_test",
            severity=AlertSeverity.WARNING,
            message="First",
        )
        assert result1 is True

        # Wait for cooldown to expire
        await asyncio.sleep(0.01)

        # Second alert should go through
        result2 = await self.manager.send_alert(
            name="cooldown_test",
            severity=AlertSeverity.WARNING,
            message="Second",
        )
        assert result2 is True

    @pytest.mark.asyncio
    async def test_severity_string_conversion(self):
        """Test that string severities are converted to enum."""
        result = await self.manager.send_alert(
            name="string_severity",
            severity="warning",  # String instead of enum
            message="Test message",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_custom_handler(self):
        """Test that custom handlers are called."""
        handler_called = []

        def custom_handler(alert: Alert):
            handler_called.append(alert)

        self.manager.add_handler(custom_handler)

        await self.manager.send_alert(
            name="handler_test",
            severity=AlertSeverity.INFO,
            message="Test",
        )

        assert len(handler_called) == 1
        assert handler_called[0].name == "handler_test"

    @pytest.mark.asyncio
    async def test_handler_exception_caught(self):
        """Test that handler exceptions don't break alert delivery."""
        def bad_handler(alert: Alert):
            raise RuntimeError("Handler error")

        self.manager.add_handler(bad_handler)

        # Should not raise
        result = await self.manager.send_alert(
            name="handler_error_test",
            severity=AlertSeverity.INFO,
            message="Test",
        )

        assert result is True

    def test_clear_cooldowns(self):
        """Test clearing all cooldowns."""
        self.manager._alert_history["test1"] = datetime.now()
        self.manager._alert_history["test2"] = datetime.now()

        self.manager.clear_cooldowns()

        assert len(self.manager._alert_history) == 0

    def test_get_cooldown_status(self):
        """Test getting cooldown status."""
        self.manager._alert_history["test"] = datetime.now()

        status = self.manager.get_cooldown_status()

        assert "test" in status
        assert status["test"] > 0  # Should have remaining cooldown


class TestAlertFormatting:
    """Tests for alert message formatting."""

    def setup_method(self):
        """Setup before each test."""
        self.config = AlertConfig()
        self.manager = AlertManager(self.config)

    def test_format_message_with_template(self):
        """Test message formatting with template."""
        alert = Alert(
            name="daily_loss_limit",
            severity=AlertSeverity.CRITICAL,
            message="Daily loss limit exceeded",
            context={
                "current_loss": -250.0,
                "limit": -200.0,
            },
        )

        formatted = self.manager._format_message(alert)

        assert "Daily loss limit exceeded" in formatted
        assert "-250.00" in formatted
        assert "-200.00" in formatted

    def test_format_message_without_template(self):
        """Test message formatting without matching template."""
        alert = Alert(
            name="unknown_alert_type",
            severity=AlertSeverity.WARNING,
            message="Unknown alert",
            context={"key": "value"},
        )

        formatted = self.manager._format_message(alert)

        assert "Unknown alert" in formatted
        assert "key: value" in formatted

    def test_get_alert_title_with_template(self):
        """Test getting title from template."""
        alert = Alert(
            name="daily_loss_limit",
            severity=AlertSeverity.CRITICAL,
            message="Test",
        )

        title = self.manager._get_alert_title(alert)

        assert title == "Daily Loss Limit Exceeded"

    def test_get_alert_title_without_template(self):
        """Test generating default title."""
        alert = Alert(
            name="custom_alert_name",
            severity=AlertSeverity.WARNING,
            message="Test",
        )

        title = self.manager._get_alert_title(alert)

        assert "WARNING" in title
        assert "Custom Alert Name" in title


class TestSlackIntegration:
    """Tests for Slack alert delivery."""

    @pytest.mark.asyncio
    async def test_slack_alert_delivery(self):
        """Test Slack message delivery."""
        config = AlertConfig(
            slack_token=None,  # Disable actual Slack client creation
            slack_channel="#test",
        )

        manager = AlertManager(config)
        mock_client = AsyncMock()
        manager._slack_client = mock_client

        await manager._send_slack("Test message", AlertSeverity.WARNING)

        mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.asyncio
    async def test_slack_disabled_when_no_token(self):
        """Test that Slack is disabled when no token provided."""
        config = AlertConfig(slack_token=None)
        manager = AlertManager(config)

        # Should not raise
        await manager._send_slack("Test", AlertSeverity.INFO)


class TestEmailIntegration:
    """Tests for email alert delivery."""

    def test_email_not_configured(self):
        """Test email check when not configured."""
        config = AlertConfig(smtp_host=None)
        manager = AlertManager(config)

        assert manager._is_email_configured() is False

    def test_email_configured(self):
        """Test email check when configured."""
        config = AlertConfig(
            smtp_host="smtp.test.com",
            smtp_user="user@test.com",
            smtp_password="password",
            email_recipients=["recipient@test.com"],
        )
        manager = AlertManager(config)

        assert manager._is_email_configured() is True

    def test_html_email_formatting(self):
        """Test HTML email formatting."""
        config = AlertConfig()
        manager = AlertManager(config)

        alert = Alert(
            name="test",
            severity=AlertSeverity.CRITICAL,
            message="Test message",
            context={"key": "value"},
        )

        html = manager._format_html_email(alert, "Test message")

        assert "<html>" in html
        assert "Test message" in html
        assert "CRITICAL" in html


class TestAlertTemplates:
    """Tests for alert templates."""

    def test_risk_breach_template_exists(self):
        """Test risk breach template."""
        assert "risk_breach" in ALERT_TEMPLATES
        assert "title" in ALERT_TEMPLATES["risk_breach"]
        assert "template" in ALERT_TEMPLATES["risk_breach"]

    def test_daily_loss_template_exists(self):
        """Test daily loss template."""
        assert "daily_loss_limit" in ALERT_TEMPLATES

    def test_api_error_template_exists(self):
        """Test API error template."""
        assert "api_error" in ALERT_TEMPLATES

    def test_websocket_disconnect_template_exists(self):
        """Test WebSocket disconnect template."""
        assert "websocket_disconnect" in ALERT_TEMPLATES
