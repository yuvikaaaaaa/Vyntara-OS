"""IOS — Schema Base Models."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AppModel(BaseModel):
    """Base for all request schemas — strict validation, no extra fields."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        use_enum_values=True,
        validate_assignment=True,
        populate_by_name=True,
    )


class OrmModel(AppModel):
    """Base for all response schemas that map from SQLAlchemy ORM objects."""

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
        use_enum_values=True,
        populate_by_name=True,
    )


class TimestampedSchema(OrmModel):
    """Adds created_at / updated_at to ORM response schemas."""

    created_at: datetime
    updated_at: datetime


class AuditedSchema(TimestampedSchema):
    """Adds soft-delete field for ORM models that use AuditMixin."""

    deleted_at: datetime | None = None