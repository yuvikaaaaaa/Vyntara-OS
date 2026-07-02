"""
Intelligence Operating System — SQLAlchemy Declarative Base
============================================================
Defines the shared ``Base`` all ORM models inherit from, plus two reusable
column mixins:

``TimestampMixin``
    Adds ``created_at`` and ``updated_at`` columns with automatic UTC stamps.
    ``updated_at`` is kept current via a ``@staticmethod`` ``onupdate`` hook.

``AuditMixin(TimestampMixin)``
    Extends ``TimestampMixin`` with soft-delete support (``deleted_at``) and
    a ``to_dict()`` introspection helper for lightweight serialisation.

Design rules
~~~~~~~~~~~~
- All primary keys are UUID v4 (``gen_random_uuid()`` at the DB level as
  default, Python ``uuid.uuid4`` as the ORM-level default so unit tests work
  without a live DB).
- All ``datetime`` columns are timezone-aware (``TIMESTAMPTZ`` in PostgreSQL).
- Models never import from ``services`` or ``repositories`` — this layer has
  zero upward dependencies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, inspect, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base for all IOS ORM models.

    Using the class-based ``DeclarativeBase`` (SQLAlchemy 2.x style) gives
    full Mypy/Pyright compatibility via the ``MappedColumn`` type annotations.
    """

    # Expose the underlying metadata object for Alembic's autogenerate
    # target_metadata = Base.metadata  (referenced in alembic/env.py)


# ---------------------------------------------------------------------------
# Timestamp Mixin
# ---------------------------------------------------------------------------


class TimestampMixin:
    """
    Adds ``created_at`` and ``updated_at`` columns to any ORM model.

    Both columns are timezone-aware (``TIMESTAMPTZ``) and default to
    ``NOW()`` at the database level, ensuring correctness even for bulk
    inserts that bypass the ORM layer.

    ``updated_at`` is automatically refreshed on every UPDATE via the
    SQLAlchemy ``onupdate`` mechanism.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        doc="UTC timestamp of record creation. Set by the database.",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(tz=timezone.utc),
        doc="UTC timestamp of last modification. Updated automatically on save.",
    )


# ---------------------------------------------------------------------------
# Audit Mixin (extends TimestampMixin)
# ---------------------------------------------------------------------------


class AuditMixin(TimestampMixin):
    """
    Full audit mixin — timestamps plus soft-delete support.

    Soft deletion: set ``deleted_at`` to the current UTC time rather than
    physically deleting the row.  Repositories are expected to filter on
    ``deleted_at IS NULL`` by default.

    Provides ``to_dict()`` for lightweight serialisation (e.g. in logging
    and debugging — not a replacement for Pydantic response schemas).
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc="UTC timestamp of soft-deletion. NULL means the record is active.",
    )

    # ------------------------------------------------------------------
    # Soft-delete helpers
    # ------------------------------------------------------------------

    @property
    def is_deleted(self) -> bool:
        """Return ``True`` if this record has been soft-deleted."""
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """
        Mark this record as deleted.

        Sets ``deleted_at`` to the current UTC time.  The session must still
        be flushed/committed for the change to persist.
        """
        self.deleted_at = datetime.now(tz=timezone.utc)

    def restore(self) -> None:
        """
        Restore a soft-deleted record.

        Clears ``deleted_at``.  The session must be flushed/committed.
        """
        self.deleted_at = None

    # ------------------------------------------------------------------
    # Serialisation helper
    # ------------------------------------------------------------------

    def to_dict(self, *, exclude: set[str] | None = None) -> dict[str, Any]:
        """
        Return a plain dictionary representation of the ORM instance.

        Iterates mapped columns via SQLAlchemy's inspect API.  Does NOT
        traverse relationships to avoid N+1 loading.  Use Pydantic
        response schemas for API serialisation.

        Args:
            exclude: Column names to exclude from the output.

        Returns:
            Dictionary mapping column names to their current values.
            ``datetime`` objects are serialised to ISO 8601 strings.
            ``uuid.UUID`` objects are serialised to hyphenated strings.
        """
        excluded: set[str] = exclude or set()
        mapper = inspect(self.__class__)
        result: dict[str, Any] = {}
        for column in mapper.columns:
            name = column.key
            if name in excluded:
                continue
            value = getattr(self, name)
            if isinstance(value, datetime):
                result[name] = value.isoformat()
            elif isinstance(value, uuid.UUID):
                result[name] = str(value)
            else:
                result[name] = value
        return result


# ---------------------------------------------------------------------------
# Standard UUID primary-key mixin
# ---------------------------------------------------------------------------


class UUIDPrimaryKeyMixin:
    """
    Adds a ``id`` UUID primary-key column.

    The default is generated at the Python level (``uuid.uuid4``) so that
    the ``id`` is available immediately after model construction — before the
    INSERT — which simplifies event logging and cross-reference.

    The PostgreSQL server-side default (``gen_random_uuid()``) acts as a
    safety net for bulk inserts that bypass the ORM.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        doc="UUIDv4 primary key. Generated at ORM level before INSERT.",
    )