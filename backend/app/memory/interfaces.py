"""IOS Memory — Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.memory.types import (
    MemoryLayerType,
    MemoryRecord,
    MemorySearchRequest,
    ScoredMemory,
)


class IMemoryLayer(ABC):
    """
    Contract every memory layer must satisfy.

    Implementations: WorkingMemory, EpisodicMemory, SemanticMemory.
    """

    @property
    @abstractmethod
    def layer_type(self) -> MemoryLayerType: ...

    @abstractmethod
    async def write(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a memory record. Returns the saved record with any DB-assigned fields."""

    @abstractmethod
    async def read(self, record_id: UUID, user_id: UUID) -> MemoryRecord:
        """Retrieve a record by ID. Raises MemoryNotFoundError if absent."""

    @abstractmethod
    async def delete(self, record_id: UUID, user_id: UUID) -> None:
        """Soft-delete a record."""

    @abstractmethod
    async def search(
        self, request: MemorySearchRequest
    ) -> list[ScoredMemory]:
        """Return scored records matching the search request."""

    @abstractmethod
    async def list_recent(
        self,
        user_id: UUID,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """Return the most recently created records for a user."""

    @abstractmethod
    async def count(self, user_id: UUID) -> int:
        """Return the total record count for a user."""


class IEmbeddingGateway(ABC):
    """
    Thin adapter over AI Core's embedding capability.

    Injected into memory layers that need vector similarity.
    Decouples memory from the specific AI Core provider.
    """

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts efficiently."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Return the vector dimension produced by this gateway."""