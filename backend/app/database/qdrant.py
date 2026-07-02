"""
Intelligence Operating System — Qdrant Vector Database Infrastructure
=====================================================================
All Qdrant interactions are routed through this module:

- Collection schema definitions (vector params for all four collections)
- Point upsert with retry
- Dense vector search
- Sparse vector search (BM25)
- Hybrid search combining dense + sparse via Reciprocal Rank Fusion
- Payload filter builders (type-safe wrappers over ``qdrant_client.models``)
- Scroll / paginate all points in a collection
- Point deletion by IDs or filter

Zero business logic.  The RAG and memory modules consume these primitives.

Collections defined in the SDD:
    ``document_chunks``       — Chunked document embeddings (dense + sparse)
    ``semantic_memories``     — Semantic memory embeddings (dense)
    ``episodic_memories``     — Experience memory embeddings (dense, 768-dim)
    ``entity_embeddings``     — Knowledge graph entity embeddings (dense)
"""

from __future__ import annotations

import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.constants import (
    EMBEDDING_MODEL_DIMENSION_DEFAULT,
    QDRANT_COLLECTION_DOCUMENT_CHUNKS,
    QDRANT_COLLECTION_ENTITY_EMBEDDINGS,
    QDRANT_COLLECTION_EPISODIC_MEMORIES,
    QDRANT_COLLECTION_SEMANTIC_MEMORIES,
)
from app.core.exceptions import QdrantConnectionError, RetrievalError
from app.core.logging import get_logger
from app.core.telemetry import create_async_span, set_span_attribute

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Collection schema registry
# ---------------------------------------------------------------------------

# Episodic memories use a lighter embedding model (768-dim vs 1024-dim)
_EPISODIC_VECTOR_DIM = 768


def get_collection_config(
    collection_name: str,
    dense_dim: int = EMBEDDING_MODEL_DIMENSION_DEFAULT,
) -> dict[str, Any]:
    """
    Return the creation parameters for a named collection.

    Args:
        collection_name: One of the four IOS collection names.
        dense_dim: Dense vector dimension (from embedding model config).

    Returns:
        Dict suitable for ``AsyncQdrantClient.create_collection(**config)``.

    Raises:
        ValueError: Unknown collection name.
    """
    if collection_name == QDRANT_COLLECTION_DOCUMENT_CHUNKS:
        return {
            "collection_name": collection_name,
            "vectors_config": {
                "dense": models.VectorParams(
                    size=dense_dim,
                    distance=models.Distance.COSINE,
                    hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
                    on_disk=False,
                )
            },
            "sparse_vectors_config": {
                "sparse": models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False)
                )
            },
            "optimizers_config": models.OptimizersConfigDiff(
                indexing_threshold=20_000,
                memmap_threshold=50_000,
            ),
            "quantization_config": models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True,
                )
            ),
        }

    elif collection_name == QDRANT_COLLECTION_SEMANTIC_MEMORIES:
        return {
            "collection_name": collection_name,
            "vectors_config": {
                "dense": models.VectorParams(
                    size=dense_dim,
                    distance=models.Distance.COSINE,
                )
            },
        }

    elif collection_name == QDRANT_COLLECTION_EPISODIC_MEMORIES:
        return {
            "collection_name": collection_name,
            "vectors_config": {
                "dense": models.VectorParams(
                    size=_EPISODIC_VECTOR_DIM,
                    distance=models.Distance.COSINE,
                )
            },
        }

    elif collection_name == QDRANT_COLLECTION_ENTITY_EMBEDDINGS:
        return {
            "collection_name": collection_name,
            "vectors_config": {
                "dense": models.VectorParams(
                    size=dense_dim,
                    distance=models.Distance.COSINE,
                )
            },
        }

    else:
        raise ValueError(
            f"Unknown Qdrant collection: '{collection_name}'. "
            f"Valid collections: {[QDRANT_COLLECTION_DOCUMENT_CHUNKS, QDRANT_COLLECTION_SEMANTIC_MEMORIES, QDRANT_COLLECTION_EPISODIC_MEMORIES, QDRANT_COLLECTION_ENTITY_EMBEDDINGS]}"
        )


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------


async def ensure_collection(
    client: AsyncQdrantClient,
    collection_name: str,
    dense_dim: int = EMBEDDING_MODEL_DIMENSION_DEFAULT,
) -> bool:
    """
    Create a collection if it does not already exist.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection name.
        dense_dim: Dense vector dimension.

    Returns:
        ``True`` if created, ``False`` if already existed.

    Raises:
        QdrantConnectionError: On connection failure.
    """
    async with create_async_span(
        "qdrant.ensure_collection",
        attributes={"qdrant.collection": collection_name},
    ):
        try:
            existing = await client.get_collections()
            existing_names = {c.name for c in existing.collections}
            if collection_name in existing_names:
                logger.debug("qdrant_collection_exists", collection=collection_name)
                return False

            config = get_collection_config(collection_name, dense_dim)
            await client.create_collection(**config)
            logger.info("qdrant_collection_created", collection=collection_name)
            return True
        except UnexpectedResponse as exc:
            raise QdrantConnectionError(
                f"Qdrant error ensuring collection '{collection_name}': {exc}",
                details={"collection": collection_name},
            ) from exc


async def delete_collection(
    client: AsyncQdrantClient,
    collection_name: str,
) -> None:
    """
    Delete a collection and all its data.

    **Irreversible.**  Used in integration tests and data management scripts.

    Args:
        client: Async Qdrant client.
        collection_name: Collection to delete.
    """
    try:
        await client.delete_collection(collection_name)
        logger.warning("qdrant_collection_deleted", collection=collection_name)
    except UnexpectedResponse as exc:
        logger.error(
            "qdrant_delete_collection_error",
            collection=collection_name,
            exc=str(exc),
        )
        raise QdrantConnectionError(str(exc)) from exc


async def get_collection_info(
    client: AsyncQdrantClient,
    collection_name: str,
) -> dict[str, Any]:
    """
    Return metadata and statistics for a collection.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection name.

    Returns:
        Dict with ``points_count``, ``segments_count``, ``status``,
        ``vectors_count``, ``indexed_vectors_count`` keys.
    """
    try:
        info = await client.get_collection(collection_name)
        return {
            "points_count": info.points_count or 0,
            "segments_count": info.segments_count or 0,
            "status": str(info.status),
            "vectors_count": info.vectors_count or 0,
            "indexed_vectors_count": info.indexed_vectors_count or 0,
        }
    except UnexpectedResponse as exc:
        raise QdrantConnectionError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Upsert operations
# ---------------------------------------------------------------------------


async def upsert_points(
    client: AsyncQdrantClient,
    collection_name: str,
    points: list[models.PointStruct],
    *,
    batch_size: int = 64,
    wait: bool = True,
) -> int:
    """
    Upsert a list of points into a collection in batches.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection.
        points: List of ``PointStruct`` objects with id, vector, payload.
        batch_size: Number of points per upsert batch (default 64).
        wait: Wait for indexing to complete (default True for consistency).

    Returns:
        Total number of points upserted.

    Raises:
        QdrantConnectionError: On Qdrant communication error.
    """
    async with create_async_span(
        "qdrant.upsert",
        attributes={
            "qdrant.collection": collection_name,
            "qdrant.points_count": len(points),
        },
    ):
        total = 0
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            try:
                await client.upsert(
                    collection_name=collection_name,
                    points=batch,
                    wait=wait,
                )
                total += len(batch)
                logger.debug(
                    "qdrant_batch_upserted",
                    collection=collection_name,
                    batch_size=len(batch),
                    total_so_far=total,
                )
            except UnexpectedResponse as exc:
                raise QdrantConnectionError(
                    f"Qdrant upsert error in '{collection_name}': {exc}",
                    details={
                        "collection": collection_name,
                        "batch_start": i,
                    },
                ) from exc
        logger.info(
            "qdrant_upsert_complete",
            collection=collection_name,
            total=total,
        )
        return total


def build_point(
    point_id: str | uuid.UUID,
    dense_vector: list[float],
    payload: dict[str, Any],
    *,
    sparse_vector: models.SparseVector | None = None,
    vector_name: str = "dense",
    sparse_vector_name: str = "sparse",
) -> models.PointStruct:
    """
    Construct a ``PointStruct`` for upsert.

    Supports both dense-only and hybrid (dense + sparse) points.

    Args:
        point_id: Point identifier (UUID).
        dense_vector: Dense embedding vector.
        payload: Metadata dict stored alongside the vector.
        sparse_vector: Optional sparse (BM25) vector.
        vector_name: Named vector key for dense (default ``"dense"``).
        sparse_vector_name: Named vector key for sparse (default ``"sparse"``).

    Returns:
        ``PointStruct`` ready for upsert.
    """
    vectors: dict[str, Any] = {vector_name: dense_vector}
    if sparse_vector is not None:
        vectors[sparse_vector_name] = sparse_vector

    return models.PointStruct(
        id=str(point_id),
        vector=vectors,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Dense vector search
# ---------------------------------------------------------------------------


async def search_dense(
    client: AsyncQdrantClient,
    collection_name: str,
    query_vector: list[float],
    *,
    top_k: int = 20,
    score_threshold: float | None = None,
    payload_filter: models.Filter | None = None,
    with_payload: bool = True,
    vector_name: str = "dense",
) -> list[models.ScoredPoint]:
    """
    Perform dense (semantic) vector search.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection.
        query_vector: Query embedding vector.
        top_k: Number of results to return.
        score_threshold: Minimum cosine similarity score (0.0–1.0).
        payload_filter: Optional Qdrant filter for payload fields.
        with_payload: Whether to include payload in results.
        vector_name: Named vector to search (default ``"dense"``).

    Returns:
        List of ``ScoredPoint`` objects sorted by descending score.

    Raises:
        RetrievalError: On Qdrant search failure.
    """
    async with create_async_span(
        "qdrant.search.dense",
        attributes={
            "qdrant.collection": collection_name,
            "qdrant.top_k": top_k,
        },
    ):
        try:
            results = await client.search(
                collection_name=collection_name,
                query_vector=models.NamedVector(
                    name=vector_name,
                    vector=query_vector,
                ),
                limit=top_k,
                score_threshold=score_threshold,
                query_filter=payload_filter,
                with_payload=with_payload,
                with_vectors=False,
            )
            set_span_attribute("qdrant.results_count", len(results))
            return results
        except UnexpectedResponse as exc:
            raise RetrievalError(
                f"Dense search failed in '{collection_name}': {exc}",
                details={"collection": collection_name},
            ) from exc


# ---------------------------------------------------------------------------
# Sparse vector search (BM25)
# ---------------------------------------------------------------------------


async def search_sparse(
    client: AsyncQdrantClient,
    collection_name: str,
    sparse_vector: models.SparseVector,
    *,
    top_k: int = 20,
    payload_filter: models.Filter | None = None,
    with_payload: bool = True,
    vector_name: str = "sparse",
) -> list[models.ScoredPoint]:
    """
    Perform sparse (BM25) vector search.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection (must have sparse vectors indexed).
        sparse_vector: Sparse vector with ``indices`` and ``values``.
        top_k: Number of results to return.
        payload_filter: Optional payload filter.
        with_payload: Whether to include payload.
        vector_name: Named sparse vector key (default ``"sparse"``).

    Returns:
        List of ``ScoredPoint`` objects.

    Raises:
        RetrievalError: On search failure.
    """
    async with create_async_span(
        "qdrant.search.sparse",
        attributes={"qdrant.collection": collection_name, "qdrant.top_k": top_k},
    ):
        try:
            results = await client.search(
                collection_name=collection_name,
                query_vector=models.NamedSparseVector(
                    name=vector_name,
                    vector=sparse_vector,
                ),
                limit=top_k,
                query_filter=payload_filter,
                with_payload=with_payload,
                with_vectors=False,
            )
            set_span_attribute("qdrant.results_count", len(results))
            return results
        except UnexpectedResponse as exc:
            raise RetrievalError(
                f"Sparse search failed in '{collection_name}': {exc}",
                details={"collection": collection_name},
            ) from exc


# ---------------------------------------------------------------------------
# Hybrid search (dense + sparse) via Qdrant Query API
# ---------------------------------------------------------------------------


async def search_hybrid(
    client: AsyncQdrantClient,
    collection_name: str,
    query_dense: list[float],
    query_sparse: models.SparseVector,
    *,
    top_k: int = 20,
    payload_filter: models.Filter | None = None,
    dense_weight: float = 0.5,
    sparse_weight: float = 0.5,
) -> list[models.ScoredPoint]:
    """
    Perform hybrid dense + sparse search using Qdrant's Query API.

    Combines results from both retrievers using Qdrant's built-in
    Reciprocal Rank Fusion (RRF) fusion.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection.
        query_dense: Dense query embedding.
        query_sparse: Sparse BM25 query vector.
        top_k: Number of final fused results to return.
        payload_filter: Optional filter applied to both retrievers.
        dense_weight: RRF weight for dense results (informational only —
                      Qdrant RRF is unweighted; reserved for future API).
        sparse_weight: RRF weight for sparse results.

    Returns:
        List of ``ScoredPoint`` objects after RRF fusion.

    Raises:
        RetrievalError: On search failure.
    """
    async with create_async_span(
        "qdrant.search.hybrid",
        attributes={
            "qdrant.collection": collection_name,
            "qdrant.top_k": top_k,
        },
    ):
        try:
            results = await client.query_points(
                collection_name=collection_name,
                prefetch=[
                    models.Prefetch(
                        query=query_dense,
                        using="dense",
                        limit=top_k * 2,
                        filter=payload_filter,
                    ),
                    models.Prefetch(
                        query=query_sparse,
                        using="sparse",
                        limit=top_k * 2,
                        filter=payload_filter,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=top_k,
                with_payload=True,
            )
            set_span_attribute("qdrant.results_count", len(results.points))
            return results.points
        except UnexpectedResponse as exc:
            raise RetrievalError(
                f"Hybrid search failed in '{collection_name}': {exc}",
                details={"collection": collection_name},
            ) from exc


# ---------------------------------------------------------------------------
# Point retrieval by ID
# ---------------------------------------------------------------------------


async def get_points_by_ids(
    client: AsyncQdrantClient,
    collection_name: str,
    point_ids: list[str],
    *,
    with_payload: bool = True,
    with_vectors: bool = False,
) -> list[models.Record]:
    """
    Retrieve specific points by their IDs.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection.
        point_ids: List of point ID strings.
        with_payload: Include payload in response.
        with_vectors: Include vector values in response.

    Returns:
        List of ``Record`` objects (empty list for missing IDs).
    """
    try:
        return await client.retrieve(
            collection_name=collection_name,
            ids=point_ids,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )
    except UnexpectedResponse as exc:
        raise RetrievalError(
            f"Point retrieval failed in '{collection_name}': {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def delete_points_by_ids(
    client: AsyncQdrantClient,
    collection_name: str,
    point_ids: list[str],
    *,
    wait: bool = True,
) -> None:
    """
    Delete points by their IDs.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection.
        point_ids: Point IDs to delete.
        wait: Wait for operation to complete.
    """
    try:
        await client.delete(
            collection_name=collection_name,
            points_selector=models.PointIdsList(points=point_ids),
            wait=wait,
        )
        logger.info(
            "qdrant_points_deleted",
            collection=collection_name,
            count=len(point_ids),
        )
    except UnexpectedResponse as exc:
        raise QdrantConnectionError(
            f"Delete failed in '{collection_name}': {exc}"
        ) from exc


async def delete_points_by_filter(
    client: AsyncQdrantClient,
    collection_name: str,
    payload_filter: models.Filter,
    *,
    wait: bool = True,
) -> None:
    """
    Delete all points matching a payload filter.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection.
        payload_filter: Filter selecting points to delete.
        wait: Wait for operation to complete.
    """
    try:
        await client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(filter=payload_filter),
            wait=wait,
        )
        logger.info(
            "qdrant_points_deleted_by_filter",
            collection=collection_name,
        )
    except UnexpectedResponse as exc:
        raise QdrantConnectionError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Scroll (full collection iteration)
# ---------------------------------------------------------------------------


async def scroll_all_points(
    client: AsyncQdrantClient,
    collection_name: str,
    payload_filter: models.Filter | None = None,
    *,
    batch_size: int = 100,
    with_payload: bool = True,
) -> list[models.Record]:
    """
    Scroll through all points in a collection, handling pagination automatically.

    Args:
        client: Async Qdrant client.
        collection_name: Target collection.
        payload_filter: Optional filter.
        batch_size: Points per scroll request.
        with_payload: Include payload in results.

    Returns:
        All matching ``Record`` objects.
    """
    all_records: list[models.Record] = []
    offset: str | None = None

    while True:
        records, next_offset = await client.scroll(
            collection_name=collection_name,
            scroll_filter=payload_filter,
            limit=batch_size,
            offset=offset,
            with_payload=with_payload,
            with_vectors=False,
        )
        all_records.extend(records)
        if next_offset is None:
            break
        offset = next_offset

    return all_records


# ---------------------------------------------------------------------------
# Payload filter builders (type-safe wrappers)
# ---------------------------------------------------------------------------


def filter_by_user(user_id: str) -> models.Filter:
    """Build a filter restricting results to a specific user_id."""
    return models.Filter(
        must=[
            models.FieldCondition(
                key="user_id",
                match=models.MatchValue(value=user_id),
            )
        ]
    )


def filter_by_field(key: str, value: Any) -> models.Filter:
    """Build a filter matching a single payload field value."""
    return models.Filter(
        must=[
            models.FieldCondition(
                key=key,
                match=models.MatchValue(value=value),
            )
        ]
    )


def filter_by_tags(tags: list[str]) -> models.Filter:
    """Build a filter matching any of the specified tags."""
    return models.Filter(
        must=[
            models.FieldCondition(
                key="tags",
                match=models.MatchAny(any=tags),
            )
        ]
    )


def combine_filters(*filters: models.Filter) -> models.Filter:
    """Combine multiple filters with AND logic (``must`` clause)."""
    must_conditions: list[models.Condition] = []
    for f in filters:
        if f.must:
            must_conditions.extend(f.must)
    return models.Filter(must=must_conditions)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


async def ping_qdrant(client: AsyncQdrantClient) -> bool:
    """
    Verify Qdrant connectivity by listing collections.

    Returns:
        ``True`` if Qdrant responds; ``False`` otherwise.
    """
    try:
        await client.get_collections()
        return True
    except Exception as exc:
        logger.warning("qdrant_ping_failed", exc=str(exc))
        return False