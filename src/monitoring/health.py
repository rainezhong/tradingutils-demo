"""Health Check System.

Provides health check infrastructure for monitoring system status:
- Component health aggregation
- Liveness and readiness probes
- HTTP endpoint via FastAPI
- Async health check execution

Example:
    from src.monitoring.health import HealthChecker, HealthStatus, create_health_app

    # Create health checker
    health = HealthChecker()

    # Register component checks
    async def check_database():
        try:
            await db.execute("SELECT 1")
            return HealthStatus.HEALTHY
        except Exception:
            return HealthStatus.UNHEALTHY

    health.register("database", check_database)
    health.register("api_kalshi", check_kalshi_api)
    health.register("api_polymarket", check_polymarket_api)

    # Create FastAPI app
    app = create_health_app(health)

    # Run with uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from .logger import get_logger

logger = get_logger(__name__)


class HealthStatus(str, Enum):
    """Health status levels for components."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentHealth:
    """Health status for a single component.

    Attributes:
        status: Current health status
        message: Optional status message
        last_check: When the check was last run
        latency_ms: Check execution time in milliseconds
        metadata: Additional health metadata
    """

    status: HealthStatus
    message: Optional[str] = None
    last_check: datetime = field(default_factory=datetime.now)
    latency_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    """Aggregated health report for all components.

    Attributes:
        status: Overall system health status
        components: Individual component health statuses
        timestamp: When the report was generated
    """

    status: HealthStatus
    components: Dict[str, ComponentHealth]
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "status": self.status.value,
            "timestamp": self.timestamp.isoformat(),
            "components": {
                name: {
                    "status": comp.status.value,
                    "message": comp.message,
                    "last_check": comp.last_check.isoformat(),
                    "latency_ms": comp.latency_ms,
                    **comp.metadata,
                }
                for name, comp in self.components.items()
            },
        }


# Type for health check functions
HealthCheckFn = Callable[[], Awaitable[Union[HealthStatus, ComponentHealth]]]


class HealthChecker:
    """Component health aggregator.

    Manages registration and execution of health checks for
    individual system components. Aggregates results into
    overall system health status.

    Example:
        checker = HealthChecker()

        async def check_db():
            # Check database connectivity
            return HealthStatus.HEALTHY

        checker.register("database", check_db)
        report = await checker.check_all()
        print(report.status)  # HealthStatus.HEALTHY
    """

    def __init__(self, timeout_seconds: float = 10.0):
        """Initialize health checker.

        Args:
            timeout_seconds: Timeout for individual health checks
        """
        self._checks: Dict[str, HealthCheckFn] = {}
        self._timeout = timeout_seconds
        self._last_report: Optional[HealthReport] = None

    def register(self, name: str, check_fn: HealthCheckFn) -> None:
        """Register a health check function.

        Args:
            name: Unique identifier for the component
            check_fn: Async function that returns HealthStatus or ComponentHealth
        """
        self._checks[name] = check_fn
        logger.debug("Health check registered", component=name)

    def unregister(self, name: str) -> None:
        """Remove a registered health check.

        Args:
            name: Component identifier to remove
        """
        self._checks.pop(name, None)

    async def check_component(self, name: str) -> ComponentHealth:
        """Run health check for a single component.

        Args:
            name: Component identifier

        Returns:
            ComponentHealth with status and metadata

        Raises:
            KeyError: If component is not registered
        """
        if name not in self._checks:
            raise KeyError(f"Health check not registered: {name}")

        check_fn = self._checks[name]
        start_time = datetime.now()

        try:
            result = await asyncio.wait_for(
                check_fn(),
                timeout=self._timeout,
            )

            latency_ms = (datetime.now() - start_time).total_seconds() * 1000

            if isinstance(result, ComponentHealth):
                result.latency_ms = latency_ms
                return result

            return ComponentHealth(
                status=result,
                latency_ms=latency_ms,
            )

        except asyncio.TimeoutError:
            logger.warning(
                "Health check timed out",
                component=name,
                timeout=self._timeout,
            )
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"Check timed out after {self._timeout}s",
            )
        except Exception as e:
            logger.error(
                "Health check failed",
                component=name,
                error=str(e),
            )
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

    async def check_all(self) -> HealthReport:
        """Run all registered health checks.

        Returns:
            HealthReport with aggregated status and component details
        """
        components: Dict[str, ComponentHealth] = {}
        overall = HealthStatus.HEALTHY

        # Run all checks concurrently
        check_tasks = {
            name: self.check_component(name)
            for name in self._checks
        }

        results = await asyncio.gather(
            *check_tasks.values(),
            return_exceptions=True,
        )

        for name, result in zip(check_tasks.keys(), results):
            if isinstance(result, Exception):
                components[name] = ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message=str(result),
                )
            else:
                components[name] = result

            # Update overall status (worst status wins)
            if components[name].status == HealthStatus.UNHEALTHY:
                overall = HealthStatus.UNHEALTHY
            elif (
                components[name].status == HealthStatus.DEGRADED
                and overall == HealthStatus.HEALTHY
            ):
                overall = HealthStatus.DEGRADED

        report = HealthReport(
            status=overall,
            components=components,
        )
        self._last_report = report

        logger.info(
            "Health check completed",
            status=overall.value,
            healthy=sum(1 for c in components.values() if c.status == HealthStatus.HEALTHY),
            degraded=sum(1 for c in components.values() if c.status == HealthStatus.DEGRADED),
            unhealthy=sum(1 for c in components.values() if c.status == HealthStatus.UNHEALTHY),
        )

        return report

    def get_last_report(self) -> Optional[HealthReport]:
        """Get the most recent health report.

        Returns:
            Last HealthReport or None if no checks have run
        """
        return self._last_report

    @property
    def registered_checks(self) -> List[str]:
        """Get list of registered component names."""
        return list(self._checks.keys())


def create_health_app(health_checker: HealthChecker) -> Any:
    """Create FastAPI application with health check endpoints.

    Creates endpoints:
    - GET /health - Full health status with component details
    - GET /health/live - Liveness probe (always 200 if service is running)
    - GET /health/ready - Readiness probe (503 if unhealthy)

    Args:
        health_checker: Configured HealthChecker instance

    Returns:
        FastAPI application instance
    """
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.responses import JSONResponse
    except ImportError:
        raise ImportError(
            "FastAPI is required for health endpoints. "
            "Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(
        title="Trading System Health",
        description="Health check endpoints for the trading system",
        version="1.0.0",
    )

    @app.get("/health", response_class=JSONResponse)
    async def health() -> Dict[str, Any]:
        """Full health check endpoint.

        Returns detailed health status for all components.
        """
        report = await health_checker.check_all()
        return report.to_dict()

    @app.get("/health/live", response_class=JSONResponse)
    async def liveness() -> Dict[str, str]:
        """Liveness probe endpoint.

        Returns 200 OK if the service is running.
        Used by Kubernetes to determine if container should be restarted.
        """
        return {"status": "ok"}

    @app.get("/health/ready", response_class=JSONResponse)
    async def readiness() -> Dict[str, Any]:
        """Readiness probe endpoint.

        Returns 200 if system is ready to accept traffic.
        Returns 503 if system is unhealthy.
        Used by Kubernetes to determine if pod should receive traffic.
        """
        report = await health_checker.check_all()
        if report.status == HealthStatus.UNHEALTHY:
            raise HTTPException(
                status_code=503,
                detail=report.to_dict(),
            )
        return report.to_dict()

    return app


# =============================================================================
# Common Health Check Implementations
# =============================================================================


def create_database_check(
    db_connection: Any,
    query: str = "SELECT 1",
) -> HealthCheckFn:
    """Create a database health check function.

    Args:
        db_connection: Database connection with execute method
        query: Query to execute for health check

    Returns:
        Health check function
    """
    async def check() -> HealthStatus:
        try:
            if asyncio.iscoroutinefunction(db_connection.execute):
                await db_connection.execute(query)
            else:
                db_connection.execute(query)
            return HealthStatus.HEALTHY
        except Exception as e:
            logger.warning("Database health check failed", error=str(e))
            return HealthStatus.UNHEALTHY

    return check


def create_api_check(
    url: str,
    expected_status: int = 200,
    timeout: float = 5.0,
) -> HealthCheckFn:
    """Create an API endpoint health check function.

    Args:
        url: URL to check
        expected_status: Expected HTTP status code
        timeout: Request timeout in seconds

    Returns:
        Health check function
    """
    async def check() -> ComponentHealth:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                    if response.status == expected_status:
                        return ComponentHealth(
                            status=HealthStatus.HEALTHY,
                            metadata={"status_code": response.status},
                        )
                    else:
                        return ComponentHealth(
                            status=HealthStatus.DEGRADED,
                            message=f"Unexpected status: {response.status}",
                            metadata={"status_code": response.status},
                        )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

    return check


def create_websocket_check(
    ws_manager: Any,
    platform: str,
) -> HealthCheckFn:
    """Create a WebSocket connection health check function.

    Args:
        ws_manager: WebSocket manager with is_connected property/method
        platform: Platform identifier for logging

    Returns:
        Health check function
    """
    async def check() -> ComponentHealth:
        try:
            is_connected = (
                await ws_manager.is_connected()
                if asyncio.iscoroutinefunction(ws_manager.is_connected)
                else ws_manager.is_connected
            )

            if is_connected:
                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    metadata={"platform": platform, "connected": True},
                )
            else:
                return ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message="WebSocket disconnected",
                    metadata={"platform": platform, "connected": False},
                )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=str(e),
                metadata={"platform": platform},
            )

    return check


def create_memory_check(
    threshold_mb: float = 1000,
) -> HealthCheckFn:
    """Create a memory usage health check function.

    Args:
        threshold_mb: Memory usage threshold in MB

    Returns:
        Health check function
    """
    async def check() -> ComponentHealth:
        try:
            import psutil

            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024

            if memory_mb < threshold_mb * 0.8:
                status = HealthStatus.HEALTHY
            elif memory_mb < threshold_mb:
                status = HealthStatus.DEGRADED
            else:
                status = HealthStatus.UNHEALTHY

            return ComponentHealth(
                status=status,
                message=f"Memory usage: {memory_mb:.1f} MB",
                metadata={
                    "memory_mb": memory_mb,
                    "threshold_mb": threshold_mb,
                },
            )
        except ImportError:
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="psutil not installed, memory check skipped",
            )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=str(e),
            )

    return check
