"""Multi-Channel Alert Manager.

Provides alert delivery to multiple channels with deduplication:
- Slack notifications
- Email via SMTP
- Severity-based routing (INFO, WARNING, CRITICAL)
- 5-minute cooldown for duplicate alerts
- Templated alert messages

Example:
    from src.monitoring.alerts import AlertManager, AlertConfig, AlertSeverity

    config = AlertConfig(
        slack_token="xoxb-...",
        slack_channel="#trading-alerts",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="alerts@example.com",
        smtp_password="...",
        email_recipients=["team@example.com"],
    )

    alert_manager = AlertManager(config)

    # Send an alert
    await alert_manager.send_alert(
        name="daily_loss_limit",
        severity=AlertSeverity.CRITICAL,
        message="Daily loss limit exceeded: $-250.00",
        context={"current_loss": -250.00, "limit": -200.00}
    )
"""

import asyncio
import smtplib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union

from .logger import get_logger

logger = get_logger(__name__)


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertConfig:
    """Configuration for alert delivery channels.

    Attributes:
        slack_token: Slack bot OAuth token
        slack_channel: Default Slack channel for alerts
        smtp_host: SMTP server hostname
        smtp_port: SMTP server port (587 for TLS, 465 for SSL)
        smtp_user: SMTP authentication username
        smtp_password: SMTP authentication password
        smtp_use_tls: Whether to use TLS (default True)
        email_from: From address for email alerts
        email_recipients: List of email recipients for alerts
        cooldown_minutes: Deduplication cooldown period (default 5)
        enabled_channels: List of enabled channels ('slack', 'email')
    """

    slack_token: Optional[str] = None
    slack_channel: str = "#trading-alerts"
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True
    email_from: Optional[str] = None
    email_recipients: List[str] = field(default_factory=list)
    cooldown_minutes: int = 5
    enabled_channels: List[str] = field(default_factory=lambda: ["slack", "email"])

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlertConfig":
        """Create config from dictionary.

        Args:
            data: Configuration dictionary

        Returns:
            AlertConfig instance
        """
        return cls(
            slack_token=data.get("slack_token"),
            slack_channel=data.get("slack_channel", "#trading-alerts"),
            smtp_host=data.get("smtp_host"),
            smtp_port=data.get("smtp_port", 587),
            smtp_user=data.get("smtp_user"),
            smtp_password=data.get("smtp_password"),
            smtp_use_tls=data.get("smtp_use_tls", True),
            email_from=data.get("email_from"),
            email_recipients=data.get("email_recipients", []),
            cooldown_minutes=data.get("cooldown_minutes", 5),
            enabled_channels=data.get("enabled_channels", ["slack", "email"]),
        )


@dataclass
class Alert:
    """Represents an alert event.

    Attributes:
        name: Unique identifier for the alert type
        severity: Alert severity level
        message: Human-readable alert message
        context: Additional context data
        timestamp: When the alert was created
    """

    name: str
    severity: AlertSeverity
    message: str
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


# Alert message templates
ALERT_TEMPLATES = {
    "risk_breach": {
        "title": "Risk Limit Breach",
        "template": "{message}\n\nLimit Type: {limit_type}\nCurrent: {current_value}\nLimit: {limit_value}\nUtilization: {utilization_pct:.1f}%",
    },
    "daily_loss_limit": {
        "title": "Daily Loss Limit Exceeded",
        "template": "{message}\n\nCurrent Loss: ${current_loss:.2f}\nLimit: ${limit:.2f}\nTrading has been halted.",
    },
    "position_limit": {
        "title": "Position Limit Exceeded",
        "template": "{message}\n\nTicker: {ticker}\nCurrent Size: {current_size}\nLimit: {limit}",
    },
    "api_error": {
        "title": "API Error",
        "template": "{message}\n\nPlatform: {platform}\nEndpoint: {endpoint}\nError: {error}",
    },
    "websocket_disconnect": {
        "title": "WebSocket Disconnected",
        "template": "{message}\n\nPlatform: {platform}\nDuration: {downtime}",
    },
    "execution_failure": {
        "title": "Trade Execution Failed",
        "template": "{message}\n\nOrder ID: {order_id}\nTicker: {ticker}\nReason: {reason}",
    },
    "system_health": {
        "title": "System Health Alert",
        "template": "{message}\n\nComponent: {component}\nStatus: {status}\nDetails: {details}",
    },
}


class AlertManager:
    """Multi-channel alert manager with deduplication.

    Manages alert delivery to Slack and email with:
    - Severity-based channel routing
    - 5-minute cooldown for duplicate alerts
    - Templated message formatting
    - Async delivery

    Example:
        config = AlertConfig(slack_token="xoxb-...")
        manager = AlertManager(config)

        await manager.send_alert(
            name="daily_loss_limit",
            severity=AlertSeverity.CRITICAL,
            message="Daily loss limit exceeded",
            context={"current_loss": -250, "limit": -200}
        )
    """

    def __init__(self, config: AlertConfig):
        """Initialize alert manager.

        Args:
            config: Alert configuration
        """
        self.config = config
        self._alert_history: Dict[str, datetime] = {}
        self._cooldown = timedelta(minutes=config.cooldown_minutes)
        self._handlers: List[Callable[[Alert], None]] = []
        self._slack_client: Optional[Any] = None

        # Initialize Slack client if configured
        if config.slack_token and "slack" in config.enabled_channels:
            try:
                from slack_sdk.web.async_client import AsyncWebClient

                self._slack_client = AsyncWebClient(token=config.slack_token)
            except ImportError:
                logger.warning(
                    "slack_sdk not installed, Slack alerts disabled",
                    install_cmd="pip install slack_sdk",
                )

    def add_handler(self, handler: Callable[[Alert], None]) -> None:
        """Add a custom alert handler.

        Args:
            handler: Callable that receives Alert objects
        """
        self._handlers.append(handler)

    async def send_alert(
        self,
        name: str,
        severity: Union[AlertSeverity, str],
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send an alert if not in cooldown period.

        Args:
            name: Alert type identifier (used for deduplication)
            severity: Alert severity level
            message: Alert message
            context: Additional context data

        Returns:
            True if alert was sent, False if suppressed by cooldown
        """
        # Convert string severity to enum if needed
        if isinstance(severity, str):
            severity = AlertSeverity(severity.lower())

        # Check cooldown
        if name in self._alert_history:
            if datetime.now() - self._alert_history[name] < self._cooldown:
                logger.debug(
                    "Alert suppressed by cooldown",
                    alert_name=name,
                    cooldown_remaining=(
                        self._cooldown
                        - (datetime.now() - self._alert_history[name])
                    ).seconds,
                )
                return False

        # Update cooldown timestamp
        self._alert_history[name] = datetime.now()

        # Create alert object
        alert = Alert(
            name=name,
            severity=severity,
            message=message,
            context=context or {},
        )

        # Format message using template if available
        formatted_message = self._format_message(alert)

        # Log the alert
        log_method = {
            AlertSeverity.INFO: logger.info,
            AlertSeverity.WARNING: logger.warning,
            AlertSeverity.CRITICAL: logger.critical,
        }[severity]
        log_method(
            "Alert triggered",
            alert_name=name,
            severity=severity.value,
            message=message,
            **alert.context,
        )

        # Deliver to channels based on severity
        delivery_tasks = []

        if severity == AlertSeverity.CRITICAL:
            # Critical: both Slack and email
            if self._slack_client and "slack" in self.config.enabled_channels:
                delivery_tasks.append(self._send_slack(formatted_message, severity))
            if self._is_email_configured() and "email" in self.config.enabled_channels:
                delivery_tasks.append(self._send_email(alert, formatted_message))
        elif severity == AlertSeverity.WARNING:
            # Warning: Slack only
            if self._slack_client and "slack" in self.config.enabled_channels:
                delivery_tasks.append(self._send_slack(formatted_message, severity))
        else:
            # Info: Slack only (lower priority channel)
            if self._slack_client and "slack" in self.config.enabled_channels:
                delivery_tasks.append(self._send_slack(formatted_message, severity))

        # Call custom handlers
        for handler in self._handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(
                    "Alert handler failed",
                    handler=handler.__name__,
                    error=str(e),
                )

        # Execute delivery tasks
        if delivery_tasks:
            await asyncio.gather(*delivery_tasks, return_exceptions=True)

        return True

    def _format_message(self, alert: Alert) -> str:
        """Format alert message using template.

        Args:
            alert: Alert to format

        Returns:
            Formatted message string
        """
        if alert.name in ALERT_TEMPLATES:
            template = ALERT_TEMPLATES[alert.name]
            try:
                return template["template"].format(
                    message=alert.message,
                    **alert.context,
                )
            except KeyError:
                # Fall back to basic message if context is missing fields
                pass

        # Default format
        context_str = "\n".join(
            f"{k}: {v}" for k, v in alert.context.items()
        ) if alert.context else ""

        return f"{alert.message}\n\n{context_str}" if context_str else alert.message

    def _get_alert_title(self, alert: Alert) -> str:
        """Get alert title from template or generate default.

        Args:
            alert: Alert to get title for

        Returns:
            Alert title string
        """
        if alert.name in ALERT_TEMPLATES:
            return ALERT_TEMPLATES[alert.name]["title"]
        return f"[{alert.severity.value.upper()}] {alert.name.replace('_', ' ').title()}"

    async def _send_slack(self, message: str, severity: AlertSeverity) -> None:
        """Send alert to Slack.

        Args:
            message: Formatted message
            severity: Alert severity for color coding
        """
        if not self._slack_client:
            return

        color_map = {
            AlertSeverity.CRITICAL: "danger",
            AlertSeverity.WARNING: "warning",
            AlertSeverity.INFO: "good",
        }

        try:
            await self._slack_client.chat_postMessage(
                channel=self.config.slack_channel,
                text=message,
                attachments=[
                    {
                        "color": color_map[severity],
                        "text": message,
                        "footer": "Trading Alerts",
                        "ts": datetime.now().timestamp(),
                    }
                ],
            )
            logger.debug("Slack alert sent", channel=self.config.slack_channel)
        except Exception as e:
            logger.error("Failed to send Slack alert", error=str(e))

    def _is_email_configured(self) -> bool:
        """Check if email is properly configured."""
        return bool(
            self.config.smtp_host
            and self.config.smtp_user
            and self.config.smtp_password
            and self.config.email_recipients
        )

    async def _send_email(self, alert: Alert, message: str) -> None:
        """Send alert via email.

        Args:
            alert: Alert object
            message: Formatted message
        """
        if not self._is_email_configured():
            return

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[{alert.severity.value.upper()}] {self._get_alert_title(alert)}"
            msg["From"] = self.config.email_from or self.config.smtp_user
            msg["To"] = ", ".join(self.config.email_recipients)

            # Plain text version
            text_part = MIMEText(message, "plain")
            msg.attach(text_part)

            # HTML version
            html_message = self._format_html_email(alert, message)
            html_part = MIMEText(html_message, "html")
            msg.attach(html_part)

            # Send in executor to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self._send_email_sync,
                msg,
            )
            logger.debug(
                "Email alert sent",
                recipients=self.config.email_recipients,
            )
        except Exception as e:
            logger.error("Failed to send email alert", error=str(e))

    def _send_email_sync(self, msg: MIMEMultipart) -> None:
        """Synchronous email sending (runs in executor).

        Args:
            msg: Email message to send
        """
        with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port) as server:
            if self.config.smtp_use_tls:
                server.starttls()
            server.login(self.config.smtp_user, self.config.smtp_password)
            server.send_message(msg)

    def _format_html_email(self, alert: Alert, message: str) -> str:
        """Format HTML email content.

        Args:
            alert: Alert object
            message: Plain text message

        Returns:
            HTML formatted email content
        """
        severity_colors = {
            AlertSeverity.CRITICAL: "#dc3545",
            AlertSeverity.WARNING: "#ffc107",
            AlertSeverity.INFO: "#28a745",
        }

        context_rows = "\n".join(
            f"<tr><td style='padding: 8px; border: 1px solid #ddd;'><strong>{k}</strong></td>"
            f"<td style='padding: 8px; border: 1px solid #ddd;'>{v}</td></tr>"
            for k, v in alert.context.items()
        )

        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; padding: 20px;">
            <div style="background-color: {severity_colors[alert.severity]}; color: white; padding: 15px; border-radius: 5px;">
                <h2 style="margin: 0;">{self._get_alert_title(alert)}</h2>
                <p style="margin: 5px 0 0 0; opacity: 0.9;">{alert.severity.value.upper()} - {alert.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>
            <div style="padding: 20px; background-color: #f8f9fa; border-radius: 0 0 5px 5px;">
                <p style="font-size: 16px;">{alert.message}</p>
                {f'<table style="width: 100%; border-collapse: collapse; margin-top: 15px;">{context_rows}</table>' if context_rows else ''}
            </div>
            <p style="color: #6c757d; font-size: 12px; margin-top: 20px;">
                This alert was generated by the Trading Alert System.
            </p>
        </body>
        </html>
        """

    def clear_cooldowns(self) -> None:
        """Clear all alert cooldowns.

        Useful for testing or when you need to resend alerts immediately.
        """
        self._alert_history.clear()

    def get_cooldown_status(self) -> Dict[str, float]:
        """Get remaining cooldown time for each alert type.

        Returns:
            Dict mapping alert names to remaining cooldown seconds
        """
        now = datetime.now()
        return {
            name: max(0, (self._cooldown - (now - timestamp)).total_seconds())
            for name, timestamp in self._alert_history.items()
        }
