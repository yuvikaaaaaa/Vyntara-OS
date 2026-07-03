"""IOS — Pagination Schemas."""
from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import Field

from app.schemas.base import AppModel

T = TypeVar("T")


class PaginationParams(AppModel):
    """Query parameters for paginated collection endpoints."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="page_size")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class PageMeta(AppModel):
    """Pagination metadata embedded in every paginated response."""

    page: int
    page_size: int
    total: int
    pages: int
    has_next: bool
    has_prev: bool

    @classmethod
    def build(cls, *, page: int, page_size: int, total: int) -> "PageMeta":
        import math
        pages = max(1, math.ceil(total / page_size)) if total else 1
        return cls(
            page=page,
            page_size=page_size,
            total=total,
            pages=pages,
            has_next=page < pages,
            has_prev=page > 1,
        )


class Page(AppModel, Generic[T]):
    """
    Generic paginated response wrapper.

    Usage::

        Page[UserRead]
    """

    items: list[T]
    meta: PageMeta