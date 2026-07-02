"""
Intelligence Operating System — PostgreSQL Infrastructure
=========================================================
PostgreSQL-specific infrastructure utilities.  This module contains
everything that is *PostgreSQL-specific* and does not belong in the generic
``session.py`` abstraction:

- Extension verification and installation (``uuid-ossp``, ``pg_trgm``, etc.)
- Database version negotiation
- Connection pool metrics exposed to Prometheus
- EXPLAIN ANALYZE helper for query profiling in development
- Partitioned table management (audit log monthly partitions)
- Sequence and statistics helpers

None of these functions contain business logic.  They are infrastructure
plumbing called from startup hooks and management scripts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import QueuePool, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.core.exceptions import DatabaseConnectionError, DatabaseQueryError
from app.core.logging import get_logger
from app.core.telemetry import create_async_span

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Required PostgreSQL extensions
# ---------------------------------------------------------------------------

REQUIRED_EXTENSIONS: list[str] = [
    "uuid-ossp",   # gen_random_uuid() for server-side UUID generation
    "pg_trgm",     # Trigram indexes for LIKE/ILIKE full-text search
    "btree_gin",   # Multi-column GIN indexes
    "pgcrypto",    # gen_random_bytes() for token generation at DB level
]


async def ensure_extensions(conn: AsyncConnection) -> None:
    """
    Ensure all required PostgreSQL extensions are installed.

    Extensions are created with ``IF NOT EXISTS`` so repeated calls are safe.
    Requires a superuser or a role with ``CREATE EXTENSION`` privilege.

    Args:
        conn: Active async SQLAlchemy connection (must be outside a
              read-only transaction).

    Raises:
        DatabaseQueryError: If any extension cannot be created.
    """
    async with create_async_span("db.ensure_extensions"):
        for ext in REQUIRED_EXTENSIONS:
            try:
                await conn.execute(
                    text(f"CREATE EXTENSION IF NOT EXISTS \"{ext}\"")
                )
                logger.debug("pg_extension_ensured", extension=ext)
            except Exception as exc:
                logger.error(
                    "pg_extension_failed",
                    extension=ext,
                    exc=str(exc),
                )
                raise DatabaseQueryError(
                    f"Failed to create PostgreSQL extension '{ext}': {exc}",
                    details={"extension": ext},
                ) from exc
        await conn.commit()
        logger.info("pg_extensions_ready", extensions=REQUIRED_EXTENSIONS)


# ---------------------------------------------------------------------------
# Database version check
# ---------------------------------------------------------------------------


async def get_postgres_version(conn: AsyncConnection) -> str:
    """
    Return the PostgreSQL server version string.

    Args:
        conn: Active async SQLAlchemy connection.

    Returns:
        Version string, e.g. ``"PostgreSQL 16.1 on x86_64-pc-linux-gnu ..."``.
    """
    result = await conn.execute(text("SELECT version()"))
    row = result.fetchone()
    return str(row[0]) if row else "unknown"


async def assert_minimum_postgres_version(
    conn: AsyncConnection,
    minimum_major: int = 14,
) -> None:
    """
    Assert that the connected PostgreSQL server meets the minimum version.

    IOS requires PostgreSQL ≥ 14 for:
    - Logical replication improvements
    - ``pg_stat_statements`` coverage
    - ``MERGE`` statement support (15+)

    Args:
        conn: Active async connection.
        minimum_major: Minimum major version required (default 14).

    Raises:
        DatabaseConnectionError: If the server version is below the minimum.
    """
    result = await conn.execute(text("SHOW server_version_num"))
    row = result.fetchone()
    if not row:
        raise DatabaseConnectionError("Could not determine PostgreSQL version.")
    version_num = int(row[0])          # e.g. 160001 for 16.1
    major = version_num // 10_000
    if major < minimum_major:
        raise DatabaseConnectionError(
            f"PostgreSQL {minimum_major}+ required; connected server is "
            f"version {major} (version_num={version_num}).",
            details={"required_major": minimum_major, "actual_major": major},
        )
    logger.info("pg_version_ok", major=major, version_num=version_num)


# ---------------------------------------------------------------------------
# Connection pool metrics (Prometheus-compatible)
# ---------------------------------------------------------------------------


def get_pool_metrics(engine: AsyncEngine) -> dict[str, int | float]:
    """
    Return current connection pool statistics.

    These values are exposed by the ``/metrics`` Prometheus endpoint via the
    ``DBPoolMetrics`` collector registered in ``monitoring.py``.

    Args:
        engine: The application's shared ``AsyncEngine``.

    Returns:
        Dictionary with pool counters:
        - ``pool_size``: Configured pool size
        - ``checked_in``: Connections currently in the pool (idle)
        - ``checked_out``: Connections currently in use
        - ``overflow``: Overflow connections open beyond ``pool_size``
        - ``invalid``: Connections marked as invalid/broken
    """
    raw_pool = engine.sync_engine.pool
    if not isinstance(raw_pool, QueuePool):
        # NullPool or other pool types — return zeros
        return {
            "pool_size": 0,
            "checked_in": 0,
            "checked_out": 0,
            "overflow": 0,
            "invalid": 0,
        }
    return {
        "pool_size": raw_pool.size(),
        "checked_in": raw_pool.checkedin(),
        "checked_out": raw_pool.checkedout(),
        "overflow": raw_pool.overflow(),
        "invalid": raw_pool.invalid(),
    }


# ---------------------------------------------------------------------------
# EXPLAIN ANALYZE (development / profiling only)
# ---------------------------------------------------------------------------


async def explain_query(
    conn: AsyncConnection,
    query: str,
    params: dict[str, Any] | None = None,
    *,
    analyze: bool = True,
    buffers: bool = True,
    format: str = "text",
) -> str:
    """
    Run ``EXPLAIN [ANALYZE] [BUFFERS]`` on an arbitrary SQL query.

    **Development / profiling only.**  Never call this in a production request
    path.  Use it from management scripts or test fixtures to validate query
    plans.

    Args:
        conn: Active async connection.
        query: SQL query to explain (without EXPLAIN prefix).
        params: Optional bind parameters.
        analyze: Include ``ANALYZE`` to actually execute the query.
        buffers: Include ``BUFFERS`` for I/O statistics (requires analyze=True).
        format: Output format — ``"text"`` (human-readable) or ``"json"``.

    Returns:
        EXPLAIN output as a string.
    """
    options = ["EXPLAIN"]
    if analyze:
        opts = ["ANALYZE true"]
        if buffers:
            opts.append("BUFFERS true")
        opts.append(f"FORMAT {format.upper()}")
        options.append(f"({', '.join(opts)})")
    else:
        options.append(f"(FORMAT {format.upper()})")

    explain_sql = f"{' '.join(options)} {query}"
    result = await conn.execute(text(explain_sql), params or {})
    rows = result.fetchall()
    if format.lower() == "json":
        import json
        return json.dumps(rows[0][0], indent=2)
    return "\n".join(row[0] for row in rows)


# ---------------------------------------------------------------------------
# Audit log partition management
# ---------------------------------------------------------------------------


async def create_audit_log_partition(
    conn: AsyncConnection,
    year: int,
    month: int,
) -> None:
    """
    Create a monthly ``RANGE`` partition for the ``audit_logs`` table.

    Partitions must be created before data is inserted for that period.
    This function is idempotent — if the partition already exists, the
    ``IF NOT EXISTS`` clause prevents an error.

    Args:
        conn: Active async connection with DDL privileges.
        year: Partition year (e.g. 2025).
        month: Partition month 1-12.

    Example::

        async with engine.begin() as conn:
            await create_audit_log_partition(conn, 2025, 3)
        # Creates partition: audit_logs_2025_03
    """
    # Calculate partition bounds
    from_dt = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        to_dt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        to_dt = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    partition_name = f"audit_logs_{year}_{month:02d}"
    from_str = from_dt.strftime("%Y-%m-%d")
    to_str = to_dt.strftime("%Y-%m-%d")

    ddl = f"""
        CREATE TABLE IF NOT EXISTS {partition_name}
        PARTITION OF audit_logs
        FOR VALUES FROM ('{from_str}') TO ('{to_str}')
    """
    try:
        await conn.execute(text(ddl))
        logger.info(
            "audit_partition_created",
            partition=partition_name,
            from_date=from_str,
            to_date=to_str,
        )
    except Exception as exc:
        logger.error(
            "audit_partition_creation_failed",
            partition=partition_name,
            exc=str(exc),
        )
        raise DatabaseQueryError(
            f"Failed to create audit log partition '{partition_name}': {exc}",
            details={"partition": partition_name},
        ) from exc


async def ensure_current_audit_partitions(
    conn: AsyncConnection,
    months_ahead: int = 3,
) -> None:
    """
    Ensure audit log partitions exist for the current and upcoming months.

    Called once at startup and periodically by a background scheduler.

    Args:
        conn: Active async connection with DDL privileges.
        months_ahead: Number of future months to pre-create (default 3).
    """

    now = datetime.now(tz=timezone.utc)
    year, month = now.year, now.month

    for _ in range(months_ahead + 1):
        await create_audit_log_partition(conn, year, month)
        # Advance to next month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


# ---------------------------------------------------------------------------
# Table statistics
# ---------------------------------------------------------------------------


async def get_table_sizes(conn: AsyncConnection) -> list[dict[str, Any]]:
    """
    Return size information for all user-defined tables in the database.

    Useful for monitoring and capacity planning dashboards.

    Args:
        conn: Active async connection.

    Returns:
        List of dicts with keys:
        ``table_name``, ``row_estimate``, ``total_size``, ``table_size``,
        ``index_size``.  Sizes are human-readable strings (e.g. ``"128 kB"``).
    """
    sql = text("""
        SELECT
            relname                                    AS table_name,
            n_live_tup                                 AS row_estimate,
            pg_size_pretty(pg_total_relation_size(c.oid))  AS total_size,
            pg_size_pretty(pg_relation_size(c.oid))        AS table_size,
            pg_size_pretty(
                pg_total_relation_size(c.oid) - pg_relation_size(c.oid)
            )                                          AS index_size
        FROM   pg_class c
        JOIN   pg_namespace n ON n.oid = c.relnamespace
        JOIN   pg_stat_user_tables s ON s.relname = c.relname
        WHERE  n.nspname = 'public'
          AND  c.relkind  = 'r'
        ORDER  BY pg_total_relation_size(c.oid) DESC
    """)
    result = await conn.execute(sql)
    return [
        {
            "table_name": row.table_name,
            "row_estimate": row.row_estimate,
            "total_size": row.total_size,
            "table_size": row.table_size,
            "index_size": row.index_size,
        }
        for row in result.fetchall()
    ]


async def get_index_usage(conn: AsyncConnection) -> list[dict[str, Any]]:
    """
    Return index usage statistics for identifying unused indexes.

    Args:
        conn: Active async connection.

    Returns:
        List of dicts with ``table``, ``index``, ``scans``, ``size`` keys.
        Zero-scan indexes are candidates for removal.
    """
    sql = text("""
        SELECT
            t.relname                                   AS table_name,
            ix.relname                                  AS index_name,
            s.idx_scan                                  AS scans,
            pg_size_pretty(pg_relation_size(ix.oid))    AS size
        FROM   pg_class t
        JOIN   pg_index i  ON i.indrelid = t.oid
        JOIN   pg_class ix ON ix.oid = i.indexrelid
        JOIN   pg_stat_user_indexes s
               ON s.indexrelid = i.indexrelid
        WHERE  t.relkind = 'r'
        ORDER  BY s.idx_scan ASC, pg_relation_size(ix.oid) DESC
    """)
    result = await conn.execute(sql)
    return [
        {
            "table_name": row.table_name,
            "index_name": row.index_name,
            "scans": row.scans,
            "size": row.size,
        }
        for row in result.fetchall()
    ]


# ---------------------------------------------------------------------------
# Long-running query detection
# ---------------------------------------------------------------------------


async def get_long_running_queries(
    conn: AsyncConnection,
    min_duration_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """
    Return queries that have been running longer than ``min_duration_seconds``.

    Used by monitoring alerts to detect runaway queries.

    Args:
        conn: Active async connection.
        min_duration_seconds: Threshold in seconds (default 30 s).

    Returns:
        List of dicts with ``pid``, ``duration_seconds``, ``query``,
        ``state``, ``wait_event`` keys.
    """
    sql = text("""
        SELECT
            pid,
            EXTRACT(EPOCH FROM (NOW() - query_start))::FLOAT AS duration_seconds,
            LEFT(query, 500)                                  AS query,
            state,
            wait_event
        FROM   pg_stat_activity
        WHERE  state   != 'idle'
          AND  query_start < NOW() - INTERVAL ':min_duration seconds'
          AND  pid != pg_backend_pid()
        ORDER  BY duration_seconds DESC
    """)
    result = await conn.execute(
        sql,
        {"min_duration": min_duration_seconds},
    )
    return [
        {
            "pid": row.pid,
            "duration_seconds": row.duration_seconds,
            "query": row.query,
            "state": row.state,
            "wait_event": row.wait_event,
        }
        for row in result.fetchall()
    ]