"""IOS — API Response and Error Schemas."""
from __future__ import annotations

from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import Field

from app.schemas.base import AppModel

T = TypeVar("T")


class ProblemDetail(AppModel):
    """
    RFC 7807 Problem Details for HTTP APIs.

    Returned by the global exception handler for all IosBaseException subclasses.
    """

    type: str = Field(description="URI identifying the problem type.")
    title: str = Field(description="Machine-readable error code (SCREAMING_SNAKE_CASE).")
    status: int = Field(description="HTTP status code.")
    detail: str = Field(description="Human-readable explanation.")
    instance: str = Field(description="Request path where the problem occurred.")
    request_id: UUID | None = None
    extensions: dict[str, Any] | None = None


class ValidationErrorDetail(AppModel):
    """Single Pydantic validation error entry."""

    loc: list[str | int]
    msg: str
    type: str


class ValidationProblem(AppModel):
    """422 Validation error response body."""

    type: str = "https://ios.internal/errors/validation_error"
    title: str = "VALIDATION_ERROR"
    status: int = 422
    detail: str = "Request body validation failed."
    instance: str
    errors: list[ValidationErrorDetail]
    request_id: UUID | None = None


class ApiResponse(AppModel, Generic[T]):
    """
    Generic success envelope.

    Used by endpoints that want to return both data and metadata
    (e.g. task submission that returns the task + stream URL).
    """

    data: T
    meta: dict[str, Any] = Field(default_factory=dict)