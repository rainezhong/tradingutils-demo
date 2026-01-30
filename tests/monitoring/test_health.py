"""Tests for health check system."""

import asyncio
from datetime import datetime

import pytest

from src.monitoring.health import (
    HealthChecker,
    HealthStatus,
    ComponentHealth,
    HealthReport,
    create_database_check,
    create_api_check,
    create_websocket_check,
    create_memory_check,
)


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_status_values(self):
        """Test health status values."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"


class TestComponentHealth:
    """Tests for ComponentHealth dataclass."""

    def test_default_values(self):
        """Test default values."""
        health = ComponentHealth(status=HealthStatus.HEALTHY)

        assert health.status == HealthStatus.HEALTHY
        assert health.message is None
        assert health.latency_ms is None
        assert health.metadata == {}
        assert isinstance(health.last_check, datetime)

    def test_with_all_fields(self):
        """Test with all fields specified."""
        health = ComponentHealth(
            status=HealthStatus.DEGRADED,
            message="High latency",
            latency_ms=500.0,
            metadata={"attempts": 3},
        )

        assert health.status == HealthStatus.DEGRADED
        assert health.message == "High latency"
        assert health.latency_ms == 500.0
        assert health.metadata["attempts"] == 3


class TestHealthReport:
    """Tests for HealthReport dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        report = HealthReport(
            status=HealthStatus.HEALTHY,
            components={
                "db": ComponentHealth(status=HealthStatus.HEALTHY),
                "api": ComponentHealth(
                    status=HealthStatus.DEGRADED,
                    message="Slow",
                    latency_ms=250.0,
                ),
            },
        )

        result = report.to_dict()

        assert result["status"] == "healthy"
        assert "timestamp" in result
        assert "db" in result["components"]
        assert "api" in result["components"]
        assert result["components"]["api"]["status"] == "degraded"
        assert result["components"]["api"]["message"] == "Slow"


class TestHealthChecker:
    """Tests for HealthChecker class."""

    def setup_method(self):
        """Setup before each test."""
        self.checker = HealthChecker(timeout_seconds=5.0)

    def test_register_check(self):
        """Test registering a health check."""
        async def check():
            return HealthStatus.HEALTHY

        self.checker.register("test", check)

        assert "test" in self.checker.registered_checks

    def test_unregister_check(self):
        """Test unregistering a health check."""
        async def check():
            return HealthStatus.HEALTHY

        self.checker.register("test", check)
        self.checker.unregister("test")

        assert "test" not in self.checker.registered_checks

    @pytest.mark.asyncio
    async def test_check_component_healthy(self):
        """Test checking a healthy component."""
        async def check():
            return HealthStatus.HEALTHY

        self.checker.register("test", check)
        result = await self.checker.check_component("test")

        assert result.status == HealthStatus.HEALTHY
        assert result.latency_ms is not None

    @pytest.mark.asyncio
    async def test_check_component_returns_component_health(self):
        """Test when check returns ComponentHealth directly."""
        async def check():
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message="Slow response",
            )

        self.checker.register("test", check)
        result = await self.checker.check_component("test")

        assert result.status == HealthStatus.DEGRADED
        assert result.message == "Slow response"

    @pytest.mark.asyncio
    async def test_check_component_not_registered(self):
        """Test checking unregistered component."""
        with pytest.raises(KeyError):
            await self.checker.check_component("nonexistent")

    @pytest.mark.asyncio
    async def test_check_component_timeout(self):
        """Test health check timeout."""
        self.checker = HealthChecker(timeout_seconds=0.1)

        async def slow_check():
            await asyncio.sleep(1.0)
            return HealthStatus.HEALTHY

        self.checker.register("slow", slow_check)
        result = await self.checker.check_component("slow")

        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.message.lower()

    @pytest.mark.asyncio
    async def test_check_component_exception(self):
        """Test health check that raises exception."""
        async def bad_check():
            raise RuntimeError("Check failed")

        self.checker.register("bad", bad_check)
        result = await self.checker.check_component("bad")

        assert result.status == HealthStatus.UNHEALTHY
        assert "Check failed" in result.message

    @pytest.mark.asyncio
    async def test_check_all_healthy(self):
        """Test checking all components when all healthy."""
        async def healthy():
            return HealthStatus.HEALTHY

        self.checker.register("db", healthy)
        self.checker.register("api", healthy)

        report = await self.checker.check_all()

        assert report.status == HealthStatus.HEALTHY
        assert report.components["db"].status == HealthStatus.HEALTHY
        assert report.components["api"].status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_all_with_unhealthy(self):
        """Test overall status is unhealthy if any component unhealthy."""
        async def healthy():
            return HealthStatus.HEALTHY

        async def unhealthy():
            return HealthStatus.UNHEALTHY

        self.checker.register("db", healthy)
        self.checker.register("api", unhealthy)

        report = await self.checker.check_all()

        assert report.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_check_all_with_degraded(self):
        """Test overall status is degraded if component degraded."""
        async def healthy():
            return HealthStatus.HEALTHY

        async def degraded():
            return HealthStatus.DEGRADED

        self.checker.register("db", healthy)
        self.checker.register("api", degraded)

        report = await self.checker.check_all()

        assert report.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_check_all_unhealthy_overrides_degraded(self):
        """Test that unhealthy status overrides degraded."""
        async def unhealthy():
            return HealthStatus.UNHEALTHY

        async def degraded():
            return HealthStatus.DEGRADED

        self.checker.register("db", unhealthy)
        self.checker.register("api", degraded)

        report = await self.checker.check_all()

        assert report.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_get_last_report(self):
        """Test getting last health report."""
        async def healthy():
            return HealthStatus.HEALTHY

        self.checker.register("test", healthy)

        # No report initially
        assert self.checker.get_last_report() is None

        # After check, report is available
        await self.checker.check_all()
        report = self.checker.get_last_report()

        assert report is not None
        assert report.status == HealthStatus.HEALTHY


class TestHealthCheckFactories:
    """Tests for health check factory functions."""

    @pytest.mark.asyncio
    async def test_create_database_check_healthy(self):
        """Test database check when healthy."""
        class MockDB:
            async def execute(self, query):
                return True

        check = create_database_check(MockDB())
        result = await check()

        assert result == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_create_database_check_unhealthy(self):
        """Test database check when unhealthy."""
        class MockDB:
            async def execute(self, query):
                raise RuntimeError("Connection failed")

        check = create_database_check(MockDB())
        result = await check()

        assert result == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_create_websocket_check_connected(self):
        """Test WebSocket check when connected."""
        class MockWS:
            @property
            def is_connected(self):
                return True

        check = create_websocket_check(MockWS(), "kalshi")
        result = await check()

        assert result.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_create_websocket_check_disconnected(self):
        """Test WebSocket check when disconnected."""
        class MockWS:
            @property
            def is_connected(self):
                return False

        check = create_websocket_check(MockWS(), "kalshi")
        result = await check()

        assert result.status == HealthStatus.UNHEALTHY


class TestHealthApp:
    """Tests for FastAPI health app creation."""

    def test_create_health_app(self):
        """Test creating FastAPI app."""
        try:
            from src.monitoring.health import create_health_app

            checker = HealthChecker()
            app = create_health_app(checker)

            # App should have routes
            routes = [route.path for route in app.routes]
            assert "/health" in routes
            assert "/health/live" in routes
            assert "/health/ready" in routes

        except ImportError:
            pytest.skip("FastAPI not installed")

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Test /health endpoint."""
        try:
            from fastapi.testclient import TestClient
            from src.monitoring.health import create_health_app

            checker = HealthChecker()

            async def healthy():
                return HealthStatus.HEALTHY

            checker.register("test", healthy)

            app = create_health_app(checker)
            client = TestClient(app)

            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "healthy"

        except ImportError:
            pytest.skip("FastAPI not installed")

    @pytest.mark.asyncio
    async def test_liveness_endpoint(self):
        """Test /health/live endpoint."""
        try:
            from fastapi.testclient import TestClient
            from src.monitoring.health import create_health_app

            checker = HealthChecker()
            app = create_health_app(checker)
            client = TestClient(app)

            response = client.get("/health/live")

            assert response.status_code == 200
            assert response.json()["status"] == "ok"

        except ImportError:
            pytest.skip("FastAPI not installed")

    @pytest.mark.asyncio
    async def test_readiness_endpoint_healthy(self):
        """Test /health/ready endpoint when healthy."""
        try:
            from fastapi.testclient import TestClient
            from src.monitoring.health import create_health_app

            checker = HealthChecker()

            async def healthy():
                return HealthStatus.HEALTHY

            checker.register("test", healthy)

            app = create_health_app(checker)
            client = TestClient(app)

            response = client.get("/health/ready")

            assert response.status_code == 200

        except ImportError:
            pytest.skip("FastAPI not installed")

    @pytest.mark.asyncio
    async def test_readiness_endpoint_unhealthy(self):
        """Test /health/ready endpoint when unhealthy."""
        try:
            from fastapi.testclient import TestClient
            from src.monitoring.health import create_health_app

            checker = HealthChecker()

            async def unhealthy():
                return HealthStatus.UNHEALTHY

            checker.register("test", unhealthy)

            app = create_health_app(checker)
            client = TestClient(app)

            response = client.get("/health/ready")

            assert response.status_code == 503

        except ImportError:
            pytest.skip("FastAPI not installed")
