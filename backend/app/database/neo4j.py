"""
Intelligence Operating System — Neo4j Graph Database Infrastructure
===================================================================
All Neo4j interactions are channelled through this module:

- Async session factory and transaction wrappers
- Schema bootstrap (constraints, indexes)
- Entity and relationship CRUD primitives
- Path-finding and neighbourhood traversal helpers
- Health check

Graph schema mirrors the SDD:
    (:Entity)       — universal node for extracted named entities
    (:Concept)      — domain concept nodes
    (:Document)     — ingested documents
    (:Person)       — person entities
    (:Organization) — organisation entities
    (:Technology)   — technology / tool entities
    (:Task)         — task nodes linking to concepts they touch
    (:Session)      — conversation sessions

Zero business logic lives here — that belongs in the memory and RAG modules.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from neo4j import AsyncDriver, AsyncSession, AsyncTransaction, Record
from neo4j.exceptions import (
    DriverError,
    Neo4jError,
    ServiceUnavailable,
    TransientError,
)

from app.core.constants import KG_MAX_NEIGHBOUR_NODES, KG_MAX_TRAVERSAL_DEPTH
from app.core.exceptions import KnowledgeGraphError, Neo4jConnectionError
from app.core.logging import get_logger
from app.core.telemetry import create_async_span, set_span_attribute

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_CONSTRAINT_STATEMENTS: list[str] = [
    # Uniqueness constraints (also create implicit indexes)
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
    "FOR (e:Entity) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT concept_id_unique IF NOT EXISTS "
    "FOR (c:Concept) REQUIRE c.id IS UNIQUE",
    "CREATE CONSTRAINT document_id_unique IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT person_id_unique IF NOT EXISTS "
    "FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT organization_id_unique IF NOT EXISTS "
    "FOR (o:Organization) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT technology_id_unique IF NOT EXISTS "
    "FOR (t:Technology) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT task_id_unique IF NOT EXISTS "
    "FOR (t:Task) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT session_id_unique IF NOT EXISTS "
    "FOR (s:Session) REQUIRE s.id IS UNIQUE",
]

_INDEX_STATEMENTS: list[str] = [
    "CREATE INDEX entity_name_index IF NOT EXISTS "
    "FOR (e:Entity) ON (e.name)",
    "CREATE INDEX concept_domain_index IF NOT EXISTS "
    "FOR (c:Concept) ON (c.domain)",
    "CREATE INDEX technology_category_index IF NOT EXISTS "
    "FOR (t:Technology) ON (t.category)",
    "CREATE FULLTEXT INDEX entity_fulltext_search IF NOT EXISTS "
    "FOR (n:Entity|Concept) ON EACH [n.name, n.description]",
]


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


async def bootstrap_schema(driver: AsyncDriver) -> None:
    """
    Idempotently create all required Neo4j constraints and indexes.

    Uses ``IF NOT EXISTS`` clauses so repeated calls are safe.
    Must be called once during application startup.

    Args:
        driver: Active async Neo4j driver.

    Raises:
        Neo4jConnectionError: If the schema cannot be initialised.
    """
    async with create_async_span("neo4j.bootstrap_schema"):
        async with driver.session() as session:
            for stmt in _CONSTRAINT_STATEMENTS + _INDEX_STATEMENTS:
                try:
                    await session.run(stmt)
                    logger.debug("neo4j_schema_stmt_ok", stmt=stmt[:80])
                except Neo4jError as exc:
                    # Some Neo4j editions don't support all index types —
                    # log and continue rather than aborting startup.
                    logger.warning(
                        "neo4j_schema_stmt_warning",
                        stmt=stmt[:80],
                        exc=str(exc),
                    )
        logger.info("neo4j_schema_bootstrapped")


# ---------------------------------------------------------------------------
# Session / transaction context managers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def get_neo4j_session(
    driver: AsyncDriver,
    *,
    database: str | None = None,
    fetch_size: int = 200,
) -> AsyncIterator[AsyncSession]:
    """
    Async context manager providing a Neo4j session.

    The session is closed automatically on exit regardless of exceptions.

    Args:
        driver: Active async Neo4j driver (from ``app.state.neo4j``).
        database: Target database name (``None`` uses the default).
        fetch_size: Streaming batch size for large result sets.

    Yields:
        Open ``AsyncSession``.

    Raises:
        Neo4jConnectionError: If the session cannot be opened.

    Example::

        async with get_neo4j_session(driver) as session:
            result = await session.run("MATCH (e:Entity) RETURN e LIMIT 10")
            records = await result.data()
    """
    try:
        session_kwargs: dict[str, Any] = {"fetch_size": fetch_size}
        if database:
            session_kwargs["database"] = database
        async with driver.session(**session_kwargs) as session:
            yield session
    except ServiceUnavailable as exc:
        raise Neo4jConnectionError(
            f"Neo4j service unavailable: {exc}",
            details={"error": str(exc)},
        ) from exc
    except DriverError as exc:
        raise Neo4jConnectionError(
            f"Neo4j driver error: {exc}",
            details={"error": str(exc)},
        ) from exc


@asynccontextmanager
async def neo4j_write_transaction(
    driver: AsyncDriver,
    database: str | None = None,
) -> AsyncIterator[AsyncTransaction]:
    """
    Async context manager for a write transaction with automatic retry.

    Neo4j recommends wrapping write operations in explicit transactions
    for deadlock retry (``TransientError``).

    Args:
        driver: Active async Neo4j driver.
        database: Target database (``None`` = default).

    Yields:
        Active ``AsyncTransaction``.

    Raises:
        KnowledgeGraphError: On persistent Neo4j errors.
    """
    async with get_neo4j_session(driver, database=database) as session:
        max_retries = 3
        for attempt in range(max_retries):
            tx = await session.begin_transaction()
            try:
                yield tx
                await tx.commit()
                return
            except TransientError as exc:
                await tx.rollback()
                if attempt < max_retries - 1:
                    wait = 0.1 * (2 ** attempt)
                    logger.warning(
                        "neo4j_transient_error_retry",
                        attempt=attempt + 1,
                        wait=wait,
                        exc=str(exc),
                    )
                    await asyncio.sleep(wait)
                else:
                    raise KnowledgeGraphError(
                        f"Neo4j write failed after {max_retries} attempts: {exc}"
                    ) from exc
            except Neo4jError as exc:
                await tx.rollback()
                raise KnowledgeGraphError(
                    f"Neo4j write transaction error: {exc}",
                    details={"error": str(exc)},
                ) from exc
            except Exception:
                await tx.rollback()
                raise


# ---------------------------------------------------------------------------
# Generic Cypher execution
# ---------------------------------------------------------------------------


async def run_read_query(
    driver: AsyncDriver,
    query: str,
    parameters: dict[str, Any] | None = None,
    *,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """
    Execute a read-only Cypher query and return results as plain dicts.

    Args:
        driver: Active async Neo4j driver.
        query: Cypher query string.
        parameters: Query bind parameters.
        database: Target database (``None`` = default).

    Returns:
        List of result records as dictionaries.

    Raises:
        KnowledgeGraphError: On Neo4j query errors.
    """
    async with create_async_span(
        "neo4j.read",
        attributes={"neo4j.query_length": len(query)},
    ):
        async with get_neo4j_session(driver, database=database) as session:
            try:
                result = await session.run(query, parameters or {})
                records: list[Record] = await result.data()
                set_span_attribute("neo4j.result_count", len(records))
                return [dict(r) for r in records]
            except Neo4jError as exc:
                raise KnowledgeGraphError(
                    f"Neo4j read query error: {exc}",
                    details={"query_preview": query[:200]},
                ) from exc


async def run_write_query(
    driver: AsyncDriver,
    query: str,
    parameters: dict[str, Any] | None = None,
    *,
    database: str | None = None,
) -> list[dict[str, Any]]:
    """
    Execute a write Cypher query inside a managed transaction.

    Args:
        driver: Active async Neo4j driver.
        query: Cypher query string.
        parameters: Query bind parameters.
        database: Target database (``None`` = default).

    Returns:
        List of result records as dictionaries.

    Raises:
        KnowledgeGraphError: On Neo4j errors.
    """
    async with create_async_span(
        "neo4j.write",
        attributes={"neo4j.query_length": len(query)},
    ):
        async with neo4j_write_transaction(driver, database=database) as tx:
            try:
                result = await tx.run(query, parameters or {})
                records = await result.data()
                return [dict(r) for r in records]
            except Neo4jError as exc:
                raise KnowledgeGraphError(
                    f"Neo4j write query error: {exc}",
                    details={"query_preview": query[:200]},
                ) from exc


# ---------------------------------------------------------------------------
# Entity CRUD
# ---------------------------------------------------------------------------


async def upsert_entity(
    driver: AsyncDriver,
    entity_id: str,
    label: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    """
    Create or update a graph node.

    Uses ``MERGE`` on ``id`` so the operation is idempotent.  Properties
    are set via ``ON CREATE SET`` / ``ON MATCH SET`` to preserve fields
    that are not being explicitly updated.

    Args:
        driver: Active async Neo4j driver.
        entity_id: Unique entity identifier.
        label: Node label (e.g. ``"Entity"``, ``"Concept"``, ``"Person"``).
        properties: Properties to set on the node.

    Returns:
        The upserted node as a plain dictionary.
    """
    # Sanitise label (prevent Cypher injection via label name)
    safe_label = _sanitise_label(label)
    query = f"""
        MERGE (n:{safe_label} {{id: $id}})
        ON CREATE SET n += $props, n.created_at = datetime()
        ON MATCH  SET n += $props, n.updated_at = datetime()
        RETURN n
    """
    results = await run_write_query(
        driver,
        query,
        {"id": entity_id, "props": {**properties, "id": entity_id}},
    )
    return results[0]["n"] if results else {}


async def get_entity_by_id(
    driver: AsyncDriver,
    entity_id: str,
    label: str | None = None,
) -> dict[str, Any] | None:
    """
    Retrieve a graph node by its ``id`` property.

    Args:
        driver: Active async Neo4j driver.
        entity_id: Entity identifier.
        label: Optional label filter (improves query performance).

    Returns:
        Node properties as dict, or ``None`` if not found.
    """
    if label:
        safe_label = _sanitise_label(label)
        query = f"MATCH (n:{safe_label} {{id: $id}}) RETURN n"
    else:
        query = "MATCH (n {id: $id}) RETURN n LIMIT 1"

    results = await run_read_query(driver, query, {"id": entity_id})
    if not results:
        return None
    return dict(results[0]["n"])


async def delete_entity(
    driver: AsyncDriver,
    entity_id: str,
    *,
    detach: bool = True,
) -> bool:
    """
    Delete a graph node (and optionally its relationships).

    Args:
        driver: Active async Neo4j driver.
        entity_id: Entity identifier.
        detach: If ``True``, use ``DETACH DELETE`` to also remove relationships.

    Returns:
        ``True`` if a node was deleted, ``False`` if not found.
    """
    delete_clause = "DETACH DELETE n" if detach else "DELETE n"
    query = f"MATCH (n {{id: $id}}) {delete_clause} RETURN count(n) AS deleted"
    results = await run_write_query(driver, query, {"id": entity_id})
    return bool(results and results[0].get("deleted", 0) > 0)


# ---------------------------------------------------------------------------
# Relationship CRUD
# ---------------------------------------------------------------------------


async def upsert_relationship(
    driver: AsyncDriver,
    from_id: str,
    to_id: str,
    rel_type: str,
    properties: dict[str, Any] | None = None,
) -> bool:
    """
    Create or update a relationship between two nodes.

    Uses ``MERGE`` so repeated calls are idempotent.

    Args:
        driver: Active async Neo4j driver.
        from_id: Source node ``id``.
        to_id: Target node ``id``.
        rel_type: Relationship type (e.g. ``"RELATED_TO"``, ``"MENTIONS"``).
        properties: Optional relationship properties.

    Returns:
        ``True`` on success.

    Raises:
        KnowledgeGraphError: If either node is not found.
    """
    safe_type = _sanitise_rel_type(rel_type)
    query = f"""
        MATCH (a {{id: $from_id}}), (b {{id: $to_id}})
        MERGE (a)-[r:{safe_type}]->(b)
        ON CREATE SET r += $props, r.created_at = datetime()
        ON MATCH  SET r += $props, r.updated_at = datetime()
        RETURN count(r) AS created
    """
    results = await run_write_query(
        driver,
        query,
        {
            "from_id": from_id,
            "to_id": to_id,
            "props": properties or {},
        },
    )
    if not results or results[0].get("created", 0) == 0:
        raise KnowledgeGraphError(
            f"Could not create relationship '{rel_type}' between "
            f"'{from_id}' and '{to_id}'. One or both nodes may not exist.",
            details={"from_id": from_id, "to_id": to_id, "rel_type": rel_type},
        )
    return True


async def get_neighbours(
    driver: AsyncDriver,
    entity_id: str,
    *,
    rel_types: list[str] | None = None,
    direction: str = "BOTH",
    max_depth: int = 1,
    limit: int = KG_MAX_NEIGHBOUR_NODES,
) -> list[dict[str, Any]]:
    """
    Return neighbours of a node up to ``max_depth`` hops.

    Args:
        driver: Active async Neo4j driver.
        entity_id: Root node ``id``.
        rel_types: Restrict to these relationship types (``None`` = all).
        direction: ``"OUT"``, ``"IN"``, or ``"BOTH"`` (default).
        max_depth: Maximum traversal hops (default 1; capped at
                   ``KG_MAX_TRAVERSAL_DEPTH``).
        limit: Maximum nodes to return.

    Returns:
        List of neighbour node dicts with ``node``, ``relationship``,
        ``rel_type``, and ``depth`` keys.
    """
    depth = min(max_depth, KG_MAX_TRAVERSAL_DEPTH)
    rel_filter = "|".join(rel_types) if rel_types else ""
    rel_part = f"[r:{rel_filter}*1..{depth}]" if rel_filter else f"[r*1..{depth}]"

    if direction == "OUT":
        pattern = f"(root)-{rel_part}->(neighbour)"
    elif direction == "IN":
        pattern = f"(root)<-{rel_part}-(neighbour)"
    else:
        pattern = f"(root)-{rel_part}-(neighbour)"

    query = f"""
        MATCH (root {{id: $id}})
        MATCH {pattern}
        WHERE neighbour.id <> $id
        RETURN DISTINCT neighbour, type(last(r)) AS rel_type, length(r) AS depth
        LIMIT $limit
    """
    return await run_read_query(
        driver, query, {"id": entity_id, "limit": limit}
    )


# ---------------------------------------------------------------------------
# Full-text search
# ---------------------------------------------------------------------------


async def fulltext_search_entities(
    driver: AsyncDriver,
    query_text: str,
    *,
    limit: int = 10,
    labels: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Run a full-text search over ``Entity`` and ``Concept`` nodes.

    Requires the ``entity_fulltext_search`` index created during bootstrap.

    Args:
        driver: Active async Neo4j driver.
        query_text: Lucene query string (e.g. ``"machine learning"``).
        limit: Maximum results.
        labels: Optional label filter applied post-search.

    Returns:
        List of matching node dicts with ``node`` and ``score`` keys.
    """
    query = """
        CALL db.index.fulltext.queryNodes("entity_fulltext_search", $query)
        YIELD node, score
        RETURN node, score
        ORDER BY score DESC
        LIMIT $limit
    """
    results = await run_read_query(
        driver, query, {"query": query_text, "limit": limit}
    )
    if labels:
        label_set = set(labels)
        results = [
            r for r in results
            if any(lbl in label_set for lbl in r["node"].labels)
            if hasattr(r["node"], "labels")
        ]
    return results


# ---------------------------------------------------------------------------
# Path finding
# ---------------------------------------------------------------------------


async def shortest_path(
    driver: AsyncDriver,
    from_id: str,
    to_id: str,
    *,
    max_depth: int = KG_MAX_TRAVERSAL_DEPTH,
    rel_types: list[str] | None = None,
) -> list[dict[str, Any]] | None:
    """
    Find the shortest path between two nodes.

    Args:
        driver: Active async Neo4j driver.
        from_id: Source node ``id``.
        to_id: Target node ``id``.
        max_depth: Maximum path length.
        rel_types: Restrict to these relationship types.

    Returns:
        List of node dicts along the path (including start and end),
        or ``None`` if no path exists.
    """
    rel_filter = f":{('|'.join(rel_types))}" if rel_types else ""
    query = f"""
        MATCH (a {{id: $from_id}}), (b {{id: $to_id}})
        MATCH path = shortestPath((a)-[{rel_filter}*..{max_depth}]-(b))
        RETURN [n IN nodes(path) | n] AS path_nodes,
               length(path)         AS path_length
    """
    results = await run_read_query(
        driver, query, {"from_id": from_id, "to_id": to_id}
    )
    if not results:
        return None
    return results[0].get("path_nodes", [])


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


async def ping_neo4j(driver: AsyncDriver) -> bool:
    """
    Verify Neo4j connectivity with a trivial query.

    Returns:
        ``True`` if Neo4j responds; ``False`` otherwise.
    """
    try:
        async with get_neo4j_session(driver) as session:
            result = await session.run("RETURN 1 AS ping")
            await result.single()
        return True
    except Exception as exc:
        logger.warning("neo4j_ping_failed", exc=str(exc))
        return False


async def get_neo4j_version(driver: AsyncDriver) -> str:
    """
    Return the Neo4j server version string.

    Args:
        driver: Active async Neo4j driver.

    Returns:
        Version string (e.g. ``"Neo4j/5.14.0"``).
    """
    try:
        results = await run_read_query(driver, "CALL dbms.components() YIELD name, versions")
        if results:
            return f"{results[0].get('name', 'Neo4j')} {results[0].get('versions', ['?'])[0]}"
        return "unknown"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_LABEL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "Entity", "Concept", "Document", "Person",
        "Organization", "Technology", "Task", "Session",
    }
)

_REL_TYPE_PATTERN = __import__("re").compile(r"^[A-Z][A-Z0-9_]*$")


def _sanitise_label(label: str) -> str:
    """
    Validate a Neo4j node label against the allowlist.

    Raises:
        KnowledgeGraphError: If the label is not in the allowlist.
    """
    if label not in _LABEL_ALLOWLIST:
        raise KnowledgeGraphError(
            f"Unsupported graph node label: '{label}'. "
            f"Allowed labels: {sorted(_LABEL_ALLOWLIST)}",
            details={"label": label},
        )
    return label


def _sanitise_rel_type(rel_type: str) -> str:
    """
    Validate that a relationship type matches the expected naming pattern
    (SCREAMING_SNAKE_CASE).

    Raises:
        KnowledgeGraphError: If the type is malformed.
    """
    if not _REL_TYPE_PATTERN.match(rel_type):
        raise KnowledgeGraphError(
            f"Invalid relationship type: '{rel_type}'. "
            "Must match ^[A-Z][A-Z0-9_]*$.",
            details={"rel_type": rel_type},
        )
    return rel_type