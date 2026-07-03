"""IOS — Common Reusable Schema Components."""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from app.schemas.base import AppModel

# ---------------------------------------------------------------------------
# Annotated type aliases
# ---------------------------------------------------------------------------

NonEmptyStr = Annotated[str, StringConstraints(min_length=1, max_length=500, strip_whitespace=True)]
ShortStr    = Annotated[str, StringConstraints(min_length=1, max_length=100, strip_whitespace=True)]
LongText    = Annotated[str, StringConstraints(min_length=1)]
EmailStr    = Annotated[str, StringConstraints(
    min_length=5,
    max_length=255,
    pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    strip_whitespace=True,
)]
UsernameStr = Annotated[str, StringConstraints(
    min_length=3,
    max_length=50,
    pattern=r"^[a-zA-Z0-9_-]+$",
    strip_whitespace=True,
)]
PasswordStr = Annotated[str, StringConstraints(min_length=8, max_length=128)]

TagList = Annotated[list[str], Field(default_factory=list, max_length=20)]


# ---------------------------------------------------------------------------
# Shared value objects
# ---------------------------------------------------------------------------


class HealthStatus(AppModel):
    """Single-service health status (used in health check responses)."""

    name: str
    status: str
    latency_ms: float | None = None
    version: str | None = None
    details: dict = Field(default_factory=dict)
    error: str | None = None


class TokenPair(AppModel):
    """JWT access + refresh token pair returned after authentication."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until access token expires


class IDResponse(AppModel):
    """Minimal response returning only a created resource UUID."""

    id: UUID


class SuccessResponse(AppModel):
    """Generic operation success confirmation."""

    success: bool = True
    message: str = "Operation completed successfully."