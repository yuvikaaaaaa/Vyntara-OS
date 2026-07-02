"""
Intelligence Operating System — Models Base
===========================================
Single import point for all ORM models.

Every model in ``backend/app/models/`` begins with::

    from app.models.base import Base, AuditMixin, UUIDPrimaryKeyMixin

This avoids each model file importing directly from ``app.database.base``,
which keeps the dependency arrow clean (models → database, never reverse)
and makes future base-class changes a one-file fix.

Also re-exports the SQLAlchemy column primitives that every model needs so
that model files stay concise.
"""

from __future__ import annotations

# Re-export the database-layer base and mixins verbatim.
# The ``Base`` class carries the shared ``MetaData`` object, so ALL models
# must inherit from the *same* ``Base`` instance.
from app.database.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

# SQLAlchemy 2.x mapped-column API — imported here once and re-exported.
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

__all__ = [
    # Inheritance chain
    "Base",
    "TimestampMixin",
    "AuditMixin",
    "UUIDPrimaryKeyMixin",
    # SQLAlchemy column types
    "BigInteger",
    "Boolean",
    "DateTime",
    "Float",
    "ForeignKey",
    "Index",
    "Integer",
    "String",
    "Text",
    "UniqueConstraint",
    "text",
    # PostgreSQL-specific
    "ARRAY",
    "JSONB",
    "PG_UUID",
    # ORM
    "Mapped",
    "mapped_column",
    "relationship",
]