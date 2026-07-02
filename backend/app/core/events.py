"""
Intelligence Operating System — Application Lifespan Events
============================================================
Manages the full lifecycle of shared application resources:
  - Startup: initialise DB engine pool, Redis, Qdrant, Neo4j drivers,
             configure logging, set up OTel, seed Qdrant collections
  - Shutdown: graceful teardown in reverse order to prevent resource leaks

All resources are stored in ``app.state`` so they are accessible to
FastAPI dependency-injection functions via ``request.app.state``.

Usage (inside ``main.py``)::

    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from app.core.events import lifespan

    app = FastAPI(lifespan=lifespan)
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.telemetry import setup_telemetry, shutdown_telemetry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    FastAPI lifespan context manager.

    Everything before ``yield`` runs at startup; everything after runs at
    shutdown.  Resources are attached to ``app.state`` so that FastAPI's
    dependency injection can access them via ``request.app.state``.

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to FastAPI to handle requests.
    """
    settings = get_settings()

    # ------------------------------------------------------------------
    # 1. Logging — must be first so all subsequent startup logs are captured
    # ------------------------------------------------------------------
    configure_logging(
        log_level=settings.log_level,
        log_format=settings.log_format,
        log_file=settings.log_file,
    )
    logger.info(
        "ios_startup_begin",
        environment=settings.environment,
        version="1.0.0",
    )

    # ------------------------------------------------------------------
    # 2. OpenTelemetry — before any instrumented clients are created
    # ------------------------------------------------------------------
    setup_telemetry(fastapi_app=app)

    # ------------------------------------------------------------------
    # 3. Database (PostgreSQL via SQLAlchemy async engine)
    # ------------------------------------------------------------------
    from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

    db_engine: AsyncEngine = create_async_engine(
        settings.db.async_url,
        pool_size=settings.db.pool_size,
        max_overflow=settings.db.max_overflow,
        pool_timeout=settings.db.pool_timeout,
        pool_recycle=settings.db.pool_recycle,
        echo=settings.db.echo_sql,
        future=True,
    )
    app.state.db_engine = db_engine
    logger.info(
        "database_engine_created",
        host=settings.db.host,
        port=settings.db.port,
        db=settings.db.db,
        pool_size=settings.db.pool_size,
    )

    # ------------------------------------------------------------------
    # 4. Redis
    # ------------------------------------------------------------------
    import redis.asyncio as aioredis

    redis_client: aioredis.Redis = aioredis.from_url(
        settings.redis.url,
        max_connections=settings.redis.max_connections,
        socket_timeout=settings.redis.socket_timeout,
        socket_connect_timeout=settings.redis.socket_connect_timeout,
        decode_responses=True,
    )
    # Verify connectivity
    await redis_client.ping()
    app.state.redis = redis_client
    logger.info(
        "redis_connected",
        host=settings.redis.host,
        port=settings.redis.port,
    )

    # ------------------------------------------------------------------
    # 5. Qdrant
    # ------------------------------------------------------------------
    from qdrant_client import AsyncQdrantClient

    qdrant_kwargs: dict = {
        "host": settings.qdrant.host,
        "port": settings.qdrant.port,
        "grpc_port": settings.qdrant.grpc_port,
        "prefer_grpc": settings.qdrant.prefer_grpc,
        "https": settings.qdrant.https,
        "timeout": settings.qdrant.timeout,
    }
    if settings.qdrant.api_key:
        qdrant_kwargs["api_key"] = settings.qdrant.api_key.get_secret_value()

    qdrant_client = AsyncQdrantClient(**qdrant_kwargs)
    app.state.qdrant = qdrant_client
    logger.info(
        "qdrant_connected",
        host=settings.qdrant.host,
        port=settings.qdrant.port,
    )

    # Ensure Qdrant collections exist
    await _ensure_qdrant_collections(qdrant_client, settings.embedding.model_dimension)

    # ------------------------------------------------------------------
    # 6. Neo4j
    # ------------------------------------------------------------------
    from neo4j import AsyncGraphDatabase

    neo4j_driver = AsyncGraphDatabase.driver(
        settings.neo4j.uri,
        auth=(settings.neo4j.user, settings.neo4j.password.get_secret_value()),
        max_connection_pool_size=settings.neo4j.max_connection_pool_size,
        connection_timeout=settings.neo4j.connection_timeout,
        connection_acquisition_timeout=settings.neo4j.connection_acquisition_timeout,
    )
    await neo4j_driver.verify_connectivity()
    app.state.neo4j = neo4j_driver
    logger.info("neo4j_connected", uri=settings.neo4j.uri)

    # ------------------------------------------------------------------
    # 7. Startup complete
    # ------------------------------------------------------------------
    logger.info("ios_startup_complete", environment=settings.environment)

    # ------------------------------------------------------------------
    # Hand control to FastAPI — requests are now served
    # ------------------------------------------------------------------
    yield

    # ------------------------------------------------------------------
    # Shutdown (reverse order)
    # ------------------------------------------------------------------
    logger.info("ios_shutdown_begin")

    # Neo4j
    try:
        await neo4j_driver.close()
        logger.info("neo4j_disconnected")
    except Exception as exc:
        logger.error("neo4j_shutdown_error", exc=str(exc))

    # Qdrant (no explicit close needed for async client; GC handles it)
    logger.info("qdrant_client_released")

    # Redis
    try:
        await redis_client.aclose()
        logger.info("redis_disconnected")
    except Exception as exc:
        logger.error("redis_shutdown_error", exc=str(exc))

    # SQLAlchemy engine
    try:
        await db_engine.dispose()
        logger.info("database_engine_disposed")
    except Exception as exc:
        logger.error("database_shutdown_error", exc=str(exc))

    # OpenTelemetry — flush pending spans
    shutdown_telemetry()

    logger.info("ios_shutdown_complete")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _ensure_qdrant_collections(
    client: "AsyncQdrantClient",
    vector_dimension: int,
) -> None:
    """
    Idempotently create all required Qdrant collections.

    Called once during startup.  If a collection already exists it is
    left unchanged.  This prevents startup failures after the first run.

    Args:
        client: Async Qdrant client.
        vector_dimension: Dimension of the dense vector (from embedding model).
    """
    from qdrant_client.models import (
        Distance,
        HnswConfigDiff,
        OptimizersConfigDiff,
        SparseIndexParams,
        SparseVectorParams,
        VectorParams,
        VectorsConfig,
        SparseVectorsConfig,
    )

    from app.core.constants import (
        QDRANT_COLLECTION_DOCUMENT_CHUNKS,
        QDRANT_COLLECTION_ENTITY_EMBEDDINGS,
        QDRANT_COLLECTION_EPISODIC_MEMORIES,
        QDRANT_COLLECTION_SEMANTIC_MEMORIES,
    )

    # Retrieve existing collections once
    existing_response = await client.get_collections()
    existing_names: set[str] = {c.name for c in existing_response.collections}

    collections_config = [
        {
            "name": QDRANT_COLLECTION_DOCUMENT_CHUNKS,
            "vectors_config": VectorsConfig(
                dense=VectorParams(
                    size=vector_dimension,
                    distance=Distance.COSINE,
                    hnsw_config=HnswConfigDiff(m=16, ef_construct=200),
                )
            ),
            "sparse_vectors_config": SparseVectorsConfig(
                sparse=SparseVectorParams(index=SparseIndexParams(on_disk=False))
            ),
            "optimizers_config": OptimizersConfigDiff(
                indexing_threshold=20_000,
                memmap_threshold=50_000,
            ),
        },
        {
            "name": QDRANT_COLLECTION_SEMANTIC_MEMORIES,
            "vectors_config": VectorsConfig(
                dense=VectorParams(
                    size=vector_dimension,
                    distance=Distance.COSINE,
                )
            ),
        },
        {
            "name": QDRANT_COLLECTION_EPISODIC_MEMORIES,
            "vectors_config": VectorsConfig(
                dense=VectorParams(
                    size=768,  # all-mpnet-base-v2 dimension
                    distance=Distance.COSINE,
                )
            ),
        },
        {
            "name": QDRANT_COLLECTION_ENTITY_EMBEDDINGS,
            "vectors_config": VectorsConfig(
                dense=VectorParams(
                    size=vector_dimension,
                    distance=Distance.COSINE,
                )
            ),
        },
    ]

    for cfg in collections_config:
        name = cfg["name"]
        if name not in existing_names:
            kwargs = {k: v for k, v in cfg.items() if k != "name"}
            await client.create_collection(collection_name=name, **kwargs)
            logger.info("qdrant_collection_created", collection=name)
        else:
            logger.debug("qdrant_collection_exists", collection=name)
