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
    @property
    @abstractmethod
    def layer_type(self) -> MemoryLayerType: ...

    @abstractmethod
    async def write(self, record: MemoryRecord) -> MemoryRecord: ...

    @abstractmethod
    async def read(self, record_id: UUID, user_id: UUID) -> MemoryRecord: ...

    @abstractmethod
    async def delete(self, record_id: UUID, user_id: UUID) -> None: ...

    @abstractmethod
    async def search(self, request: MemorySearchRequest) -> list[ScoredMemory]: ...

    @abstractmethod
    async def list_recent(self, user_id: UUID, *, limit: int = 20, offset: int = 0) -> list[MemoryRecord]: ...

    @abstractmethod
    async def count(self, user_id: UUID) -> int: ...


class IEmbeddingGateway(ABC):
    @abstractmethod
    async def embed_text(self, text: str) -> list[float]: ...

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    @property
    @abstractmethod
    def dimension(self) -> int: ...