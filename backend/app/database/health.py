"""
Intelligence Operating System — Infrastructure Health Checks
============================================================
Provides a single ``check_all_services()`` coroutine that probes every
infrastructure dependency in parallel and returns a structured
``HealthReport``.

Three endpoint semantics (consumed by ``api/v1/health.py``):
  - ``/health``       — Full report with per-service detail
  - ``/health/live``  — Liveness: is the process alive? (always 200)
  - ``/health/ready`` — Readiness: can the process serve traffic?
                        Returns 503 if any required service is unhealthy.

Required services (readiness fails if any are down):
    PostgreSQL, Redis, Qdrant

Optional services (readiness succeeds even if these are degraded):
    Neo4j, Ollama

This design allows the application to start serving read queries
even if the graph DB has not yet booted.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine
import redis.asyncio as aioredis
from neo4j import AsyncDriver
from qdrant_client import AsyncQdrantClient

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ServiceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ServiceHealth:
    """Health status for a single infrastructure service."""

    name: str
    status: ServiceStatus
    latency_ms: float | None = None
    version: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "version": self.version,
            "details": self.details,
            "error": self.error,
        }


@dataclass
class HealthReport:
    """
    Aggregated health report for all infrastructure services.

    Attributes:
        status: Overall system status (worst of all required services).
        is_ready: ``True`` if all required services are healthy.
        services: Per-service health detail.
        checked_at: ISO 8601 timestamp of the check.
        app_version: Application version string.
    """

    status: ServiceStatus
    is_ready: bool
    services: list[ServiceHealth]
    checked_at: str
    app_version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "is_ready": self.is_ready,
            "app_version": self.app_version,
            "checked_at": self.checked_at,
            "services": [s.to_dict() for s in self.services],
        }


# ---------------------------------------------------------------------------
# Per-service checkers
# ---------------------------------------------------------------------------


async def _check_postgres(engine: AsyncEngine) -> ServiceHealth:
    """Probe PostgreSQL with a ``SELECT 1`` query."""
    start = time.perf_counter()
    try:
        from sqlalchemy import text
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version()"))
            row = result.fetchone()
            version = str(row[0]).split(" ")[1] if row else "unknown"
        latency = round((time.perf_counter() - start) * 1000, 2)
        return ServiceHealth(
            name="postgresql",
            status=ServiceStatus.HEALTHY,
            latency_ms=latency,
            version=version,
        )
    except Exception as exc:
        latency = round((time.perf_counter() - start) * 1000, 2)
        logger.warning("health_postgres_failed", exc=str(exc))
        return ServiceHealth(
            name="postgresql",
            status=ServiceStatus.UNHEALTHY,
            latency_ms=latency,
            error=str(exc),
        )


async def _check_redis(client: aioredis.Redis) -> ServiceHealth:
    """Probe Redis with a PING command and retrieve version info."""
    start = time.perf_counter()
    try:
        await client.ping()
        info = await client.info("server")
        version = info.get("redis_version", "unknown")
        latency = round((time.perf_counter() - start) * 1000, 2)
        return ServiceHealth(
            name="redis",
            status=ServiceStatus.HEALTHY,
            latency_ms=latency,
            version=version,
            details={
                "connected_clients": info.get("connected_clients", "?"),
                "used_memory_human": info.get("used_memory_human", "?"),
                "uptime_in_seconds": info.get("uptime_in_seconds", "?"),
            },
        )
    except Exception as exc:
        latency = round((time.perf_counter() - start) * 1000, 2)
        logger.warning("health_redis_failed", exc=str(exc))
        return ServiceHealth(
            name="redis",
            status=ServiceStatus.UNHEALTHY,
            latency_ms=latency,
            error=str(exc),
        )


async def _check_qdrant(client: AsyncQdrantClient) -> ServiceHealth:
    """Probe Qdrant by listing collections."""
    start = time.perf_counter()
    try:
        collections = await client.get_collections()
        col_names = [c.name for c in collections.collections]
        latency = round((time.perf_counter() - start) * 1000, 2)
        return ServiceHealth(
            name="qdrant",
            status=ServiceStatus.HEALTHY,
            latency_ms=latency,
            details={"collections": col_names, "collection_count": len(col_names)},
        )
    except Exception as exc:
        latency = round((time.perf_counter() - start) * 1000, 2)
        logger.warning("health_qdrant_failed", exc=str(exc))
        return ServiceHealth(
            name="qdrant",
            status=ServiceStatus.UNHEALTHY,
            latency_ms=latency,
            error=str(exc),
        )


async def _check_neo4j(driver: AsyncDriver) -> ServiceHealth:
    """Probe Neo4j with a trivial Cypher query."""
    start = time.perf_counter()
    try:
        async with driver.session() as session:
            result = await session.run(
                "CALL dbms.components() YIELD name, versions RETURN name, versions LIMIT 1"
            )
            record = await result.single()
            version = (
                f"{record['name']} {record['versions'][0]}"
                if record
                else "unknown"
            )
        latency = round((time.perf_counter() - start) * 1000, 2)
        return ServiceHealth(
            name="neo4j",
            status=ServiceStatus.HEALTHY,
            latency_ms=latency,
            version=version,
        )
    except Exception as exc:
        latency = round((time.perf_counter() - start) * 1000, 2)
        logger.warning("health_neo4j_failed", exc=str(exc))
        return ServiceHealth(
            name="neo4j",
            status=ServiceStatus.DEGRADED,   # Optional — degraded, not unhealthy
            latency_ms=latency,
            error=str(exc),
        )


async def _check_ollama() -> ServiceHealth:
    """Probe Ollama REST API by fetching the model list."""
    settings = get_settings()
    base_url = str(settings.ollama.base_url).rstrip("/")
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            response = await http.get(f"{base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            models = [m.get("name", "") for m in data.get("models", [])]
        latency = round((time.perf_counter() - start) * 1000, 2)
        return ServiceHealth(
            name="ollama",
            status=ServiceStatus.HEALTHY,
            latency_ms=latency,
            details={"loaded_models": models, "model_count": len(models)},
        )
    except Exception as exc:
        latency = round((time.perf_counter() - start) * 1000, 2)
        logger.warning("health_ollama_failed", exc=str(exc))
        return ServiceHealth(
            name="ollama",
            status=ServiceStatus.DEGRADED,   # Optional — degraded
            latency_ms=latency,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


# Required services: readiness fails if any of these are unhealthy
_REQUIRED_SERVICES: frozenset[str] = frozenset({"postgresql", "redis", "qdrant"})


async def check_all_services(
    engine: AsyncEngine,
    redis_client: aioredis.Redis,
    qdrant_client: AsyncQdrantClient,
    neo4j_driver: AsyncDriver,
    *,
    timeout: float = 10.0,
) -> HealthReport:
    """
    Run all service health checks concurrently and aggregate results.

    Args:
        engine: SQLAlchemy async engine.
        redis_client: Async Redis client.
        qdrant_client: Async Qdrant client.
        neo4j_driver: Async Neo4j driver.
        timeout: Maximum seconds to wait for all checks.

    Returns:
        ``HealthReport`` with per-service results and overall status.
    """
    from app.core.utils import utcnow_isoformat

    # Run all probes concurrently with a global timeout
    try:
        results: list[ServiceHealth] = await asyncio.wait_for(
            asyncio.gather(
                _check_postgres(engine),
                _check_redis(redis_client),
                _check_qdrant(qdrant_client),
                _check_neo4j(neo4j_driver),
                _check_ollama(),
                return_exceptions=False,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.error("health_check_global_timeout", timeout=timeout)
        # Build a worst-case report
        services = [
            ServiceHealth(
                name=name,
                status=ServiceStatus.UNKNOWN,
                error="Health check timed out",
            )
            for name in ["postgresql", "redis", "qdrant", "neo4j", "ollama"]
        ]
        return HealthReport(
            status=ServiceStatus.UNHEALTHY,
            is_ready=False,
            services=services,
            checked_at=utcnow_isoformat(),
        )

    # Determine overall status
    required_statuses = [
        s.status for s in results if s.name in _REQUIRED_SERVICES
    ]
    is_ready = all(
        st == ServiceStatus.HEALTHY for st in required_statuses
    )

    if all(st == ServiceStatus.HEALTHY for st in [s.status for s in results]):
        overall = ServiceStatus.HEALTHY
    elif is_ready:
        overall = ServiceStatus.DEGRADED
    else:
        overall = ServiceStatus.UNHEALTHY

    report = HealthReport(
        status=overall,
        is_ready=is_ready,
        services=list(results),
        checked_at=utcnow_isoformat(),
    )

    log_fn = logger.info if is_ready else logger.error
    log_fn(
        "health_check_complete",
        status=overall.value,
        is_ready=is_ready,
        services={s.name: s.status.value for s in results},
    )
    return report


async def check_liveness() -> dict[str, Any]:
    """
    Liveness check — always returns ``{"alive": true}``.

    The process is alive if this function can execute.  No external
    dependencies are probed.

    Returns:
        Dict with ``alive`` and ``timestamp`` keys.
    """
    from app.core.utils import utcnow_isoformat
    return {"alive": True, "timestamp": utcnow_isoformat()}