"""
Intelligence Operating System — Database Package
================================================
Infrastructure layer for all persistent storage backends:

    PostgreSQL  — relational data (ORM via SQLAlchemy)
    Redis       — cache, working memory, pub/sub, rate limiting
    Qdrant      — vector embeddings (dense + sparse hybrid search)
    Neo4j       — knowledge graph

Public API re-exported here for convenient import patterns:

    from app.database import (
        Base,
        AuditMixin,
        UUIDPrimaryKeyMixin,
        TimestampMixin,
        get_db_session,
        run_in_transaction,
        transactional,
        RedisCache,
        RedisHashStore,
        RedisPubSub,
        RedisLock,
        build_key as redis_key,
    )

Internal modules (not re-exported, imported directly by RAG / memory layers):
    app.database.postgres   — PostgreSQL-specific utilities and metrics
    app.database.qdrant     — Qdrant CRUD and search primitives
    app.database.neo4j      — Neo4j schema bootstrap and Cypher helpers
    app.database.health     — Unified health check aggregator

Architecture note
~~~~~~~~~~~~~~~~~
This package is strictly **infrastructure** — it contains no business logic,
domain entities, or service-layer code.  All database-aware business logic
lives in ``app.infrastructure.repositories``.
"""

from app.database.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.database.health import HealthReport, ServiceHealth, ServiceStatus, check_all_services
from app.database.redis import (
    RedisCache,
    RedisHashStore,
    RedisLock,
    RedisPubSub,
    build_key,
    check_rate_limit,
    create_redis_client,
    ping_redis,
    stream_append,
    stream_read_from,
)
from app.database.session import (
    dispose_engine,
    get_db_session,
    get_engine,
    get_session_factory,
    ping_database,
    reset_session_factory,
    run_in_transaction,
    transactional,
)

__all__ = [
    # Base / mixins
    "Base",
    "TimestampMixin",
    "AuditMixin",
    "UUIDPrimaryKeyMixin",
    # Session management
    "get_db_session",
    "get_engine",
    "get_session_factory",
    "run_in_transaction",
    "transactional",
    "ping_database",
    "dispose_engine",
    "reset_session_factory",
    # Redis
    "RedisCache",
    "RedisHashStore",
    "RedisPubSub",
    "RedisLock",
    "build_key",
    "check_rate_limit",
    "create_redis_client",
    "ping_redis",
    "stream_append",
    "stream_read_from",
    # Health
    "HealthReport",
    "ServiceHealth",
    "ServiceStatus",
    "check_all_services",
]