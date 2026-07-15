"""IOS Retrieval — Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.retrieval.types import (
    QueryRewriteResult,
    QueryRewriteStrategy,
    RerankedItem,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalSource,
    RetrievedItem,
)


class IRetriever(ABC):
    """
    Contract every retriever must satisfy.

    Implementations: VectorRetriever, GraphRetriever, and any future
    source-specific retriever (e.g. web search, document store).

    Each retriever is independently replaceable — RetrievalManager and
    HybridRetriever depend only on this interface.
    """

    @property
    @abstractmethod
    def source(self) -> RetrievalSource:
        """Identify which RetrievalSource this retriever produces."""

    @abstractmethod
    async def retrieve(self, request: RetrievalRequest) -> list[RetrievedItem]:
        """
        Execute retrieval for the given request.

        Must never raise on partial failure — return an empty list and log
        a warning instead, so a single failing source doesn't abort a
        hybrid retrieval.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the underlying data source is reachable."""


class IReranker(ABC):
    """Contract for cross-source re-ranking implementations."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        items: list[RetrievedItem],
        *,
        top_k: int | None = None,
    ) -> list[RerankedItem]:
        """Re-rank a merged candidate list and return top_k results."""


class IQueryRewriter(ABC):
    """Contract for query rewriting / expansion implementations."""

    @abstractmethod
    async def rewrite(
        self, query: str, strategy: QueryRewriteStrategy
    ) -> QueryRewriteResult:
        """Transform a raw query according to the requested strategy."""


class IRetrievalCacheBackend(ABC):
    """Contract for the retrieval result cache storage backend."""

    @abstractmethod
    async def get(self, key: str) -> RetrievalResponse | None:
        """Return a cached response, or None if absent or expired."""

    @abstractmethod
    async def set(
        self, key: str, response: RetrievalResponse, *, ttl_seconds: int = 300
    ) -> None:
        """Store a response under the given key with a TTL."""

    @abstractmethod
    async def invalidate(self, key: str) -> None:
        """Remove a cached entry."""

    @abstractmethod
    async def invalidate_prefix(self, prefix: str) -> int:
        """Remove all cached entries whose key starts with prefix. Returns count removed."""