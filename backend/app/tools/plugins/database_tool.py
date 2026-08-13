"""IOS Tools Plugins — Database Tool."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.constants import TOOL_SQL_READ, TOOL_SQL_WRITE
from app.database.session import get_engine
from app.tools.exceptions import ToolExecutionError, ToolTimeoutError, ToolValidationError
from app.tools.plugins.base_tool import BaseToolPlugin
from app.tools.types import ToolCapability, ToolHealth, ToolMetadata, ToolStatus, ToolType

# Statement prefixes considered mutating — anything not matching a
# read-only prefix is treated as a write and requires TOOL_SQL_WRITE.
_READ_ONLY_PREFIXES = ("select", "with", "show", "explain")

_DEFAULT_TIMEOUT_SECONDS = 30


class DatabaseTool(BaseToolPlugin):
    """
    Parameterized SQL execution tool built entirely on the project's
    existing database session infrastructure.

    Uses ``app.database.session.get_engine()`` — the same shared
    SQLAlchemy async engine every repository is built on — and never
    opens a raw ``asyncpg``/``psycopg`` connection itself. A single
    statement is executed per invocation inside ``engine.begin()``,
    which:
    - **commits** automatically on successful context exit
    - **rolls back** automatically if an exception propagates from
      within the block (SQLAlchemy's standard begin()-context contract)

    This mirrors exactly how ``app.database.session.get_db_session()``
    manages transactions for the ORM/repository layer, just applied to
    an arbitrary caller-supplied parameterized statement rather than a
    Repository method — appropriate for this tool's purpose (ad-hoc
    agent-issued SQL) without duplicating any repository logic.

    Query safety:
    - All statements are executed via SQLAlchemy's ``text()`` construct
      with bound parameters (``params`` dict) — string formatting /
      concatenation of user-supplied values into SQL is never performed.
    - Read-only permission (``TOOL_SQL_READ``) is required for
      SELECT/WITH/SHOW/EXPLAIN statements; anything else additionally
      requires ``TOOL_SQL_WRITE``, enforced both by the framework's
      ``ToolValidator`` (via ``required_permission`` on registration) and
      defensively again here at execution time based on statement shape.
    """

    def __init__(self, *, default_timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> None:
        metadata = ToolMetadata(
            name="database",
            display_name="Database",
            description="Execute a parameterized SQL statement against the application database.",
            tool_type=ToolType.DATA_ACCESS,
            capabilities=[ToolCapability.READ, ToolCapability.WRITE],
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "params": {"type": "object"},
                    "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "required": ["query"],
            },
            required_permission=TOOL_SQL_READ,
            default_timeout_seconds=default_timeout_seconds,
            max_timeout_seconds=120,
            max_retries=1,   # SQL statements are not blindly retried by default
            tags=["database", "sql"],
        )
        super().__init__(metadata)
        self._default_timeout = default_timeout_seconds

    async def run(self, arguments: dict[str, Any]) -> Any:
        async with self._span("run"):
            query = arguments.get("query")
            if not query or not isinstance(query, str):
                raise ToolValidationError("'query' argument must be a non-empty string.")

            params = arguments.get("params") or {}
            if not isinstance(params, dict):
                raise ToolValidationError("'params' argument must be an object/dict.")

            granted_permissions = set(arguments.get("_granted_permissions", []))
            self._enforce_statement_permission(query, granted_permissions)

            timeout = arguments.get("timeout_seconds", self._default_timeout)

            try:
                return await self._execute(query, params, timeout)
            except ToolValidationError:
                raise
            except SQLAlchemyError as exc:
                raise ToolExecutionError(
                    f"Database execution failed: {exc}",
                    details={"query_preview": query[:200]},
                ) from exc

    async def health_check(self) -> ToolHealth:
        try:
            engine = get_engine()
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return ToolHealth(
                tool_name=self._metadata.name,
                is_healthy=True,
                status=ToolStatus.COMPLETE,
            )
        except Exception as exc:
            return ToolHealth(
                tool_name=self._metadata.name,
                is_healthy=False,
                status=ToolStatus.FAILED,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def _execute(
        self, query: str, params: dict[str, Any], timeout: float
    ) -> dict:
        import asyncio

        engine = get_engine()

        async def _run() -> dict:
            async with engine.begin() as conn:
                result = await conn.execute(text(query), params)
                if result.returns_rows:
                    columns = list(result.keys())
                    rows = [dict(zip(columns, row)) for row in result.fetchall()]
                    return {
                        "columns": columns,
                        "rows": rows,
                        "row_count": len(rows),
                        "committed": True,
                    }
                return {
                    "columns": [],
                    "rows": [],
                    "row_count": result.rowcount if result.rowcount is not None else 0,
                    "committed": True,
                }

        try:
            return await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ToolTimeoutError(
                f"Database query exceeded timeout of {timeout}s.",
                details={"query_preview": query[:200], "timeout_seconds": timeout},
            ) from exc

    # ------------------------------------------------------------------
    # Permission enforcement
    # ------------------------------------------------------------------

    @staticmethod
    def _enforce_statement_permission(
        query: str, granted_permissions: set[str]
    ) -> None:
        """
        Defensive secondary check mirroring the framework-level
        ``required_permission`` enforcement: SELECT/WITH/SHOW/EXPLAIN
        only require TOOL_SQL_READ (already gated by registration); any
        other statement additionally requires TOOL_SQL_WRITE to be
        present among the caller's granted permissions.

        ``granted_permissions`` is sourced from
        ``arguments["_granted_permissions"]`` — a convention the
        ToolManager/caller populates from ``ToolRequest.permissions``
        before invoking ``run()``, since ``ITool.run()`` only receives
        the argument dict, not the full ``ToolRequest``.
        """
        normalised = query.strip().lower()
        first_word_match = re.match(r"^\s*(\w+)", normalised)
        first_word = first_word_match.group(1) if first_word_match else ""

        is_read_only = first_word in _READ_ONLY_PREFIXES
        if not is_read_only and TOOL_SQL_WRITE not in granted_permissions:
            raise ToolValidationError(
                "This statement performs a write/DDL operation and requires "
                f"the '{TOOL_SQL_WRITE}' permission.",
                details={"statement_type": first_word or "unknown"},
            )