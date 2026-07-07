"""IOS Memory — Semantic Memory Layer."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.constants import QDRANT_COLLECTION_SEMANTIC_MEMORIES
from app.core.enums import MemorySourceType
from app.memory.base import BaseMemoryLayer
from app.memory.exceptions import (
    DuplicateMemoryError,
    MemoryNotFoundError,
    SemanticMemoryError,
)
from app.memory.interfaces import IEmbeddingGateway
from app.memory.types import (
    MemoryLayerType,
    MemoryRecord,
    MemorySearchRequest,
    SearchStrategy,
    ScoredMemory,
)
from app.services.memory_service import MemoryService


class SemanticMemory(BaseMemoryLayer):
    """
    Long-term semantic knowledge store backed by PostgreSQL + Qdrant.

    Writes persist the full record to PostgreSQL and the embedding to Qdrant.
    Search uses dense vector retrieval from Qdrant; recency fallback when
    no embedding gateway is available.
    Duplicate detection uses SHA-256 content fingerprinting.
    Importance decay is applied on each read for aging.
    """

    @property
    def layer_type(self) -> MemoryLayerType:
        return MemoryLayerType.SEMANTIC

    def __init__(
        self,
        memory_service: MemoryService,
        embedding_gateway: IEmbeddingGateway | None = None,
        qdrant_client=None,
        *,
        duplicate_threshold: float = 0.95,
        decay_rate: float = 0.05,
    ) -> None:
        super().__init__()
        self._svc = memory_service
        self._embed = embedding_gateway
        self._qdrant = qdrant_client
        self._dup_threshold = duplicate_threshold
        self._decay_rate = decay_rate
        # In-process fingerprint cache to avoid redundant DB lookups
        self._fingerprint_cache: dict[str, UUID] = {}

    # ------------------------------------------------------------------
    # IMemoryLayer
    # ------------------------------------------------------------------

    async def write(self, record: MemoryRecord) -> MemoryRecord:
        async with self._span("write", user_id=str(record.user_id)):
            # Duplicate detection via content fingerprint
            fp = self.content_fingerprint(record.content)
            if fp in self._fingerprint_cache:
                existing_id = self._fingerprint_cache[fp]
                raise DuplicateMemoryError(
                    "Semantically identical memory already exists.",
                    details={"existing_id": str(existing_id)},
                )

            from app.schemas.memory import SemanticMemoryCreate
            data = SemanticMemoryCreate(
                content=record.content,
                summary=record.summary,
                importance=record.importance,
                source_type=record.metadata.get("source_type", MemorySourceType.MANUAL),
                source_id=record.source_id,
                tags=record.tags,
                expires_at=record.expires_at,
                extra_data=record.metadata,
            )
            saved = await self._svc.create_semantic(record.user_id, data)
            record.id = saved.id
            self._fingerprint_cache[fp] = saved.id

            if self._embed and self._qdrant:
                try:
                    vector = await self._embed.embed_text(record.content)
                    await self._store_in_qdrant(saved.id, vector, record)
                    await self._svc.mark_semantic_embedded(saved.id, saved.id)
                except Exception as exc:
                    self._log.warning(
                        "semantic_embedding_failed",
                        record_id=str(saved.id),
                        exc=str(exc),
                    )
            return record

    async def read(self, record_id: UUID, user_id: UUID) -> MemoryRecord:
        async with self._span("read", record_id=str(record_id)):
            mem = await self._svc.get_semantic(record_id, user_id)
            if mem.is_deleted:
                raise MemoryNotFoundError(f"Semantic memory {record_id} not found.")

            # Passive importance decay on read
            elapsed_days = (
                datetime.now(tz=timezone.utc) - mem.created_at
            ).total_seconds() / 86_400
            decayed = self.decayed_importance(
                mem.importance, days_elapsed=elapsed_days, decay_rate=self._decay_rate
            )
            if abs(decayed - mem.importance) > 0.01:
                from app.schemas.memory import SemanticMemoryUpdate
                await self._svc.update_semantic(
                    record_id, user_id, SemanticMemoryUpdate(importance=decayed)
                )

            # Record access
            await self._svc.record_access(record_id)
            return self._to_memory_record(mem)

    async def delete(self, record_id: UUID, user_id: UUID) -> None:
        async with self._span("delete", record_id=str(record_id)):
            await self._svc.delete_semantic(record_id, user_id)
            # Invalidate fingerprint cache entry
            self._fingerprint_cache = {
                k: v for k, v in self._fingerprint_cache.items() if v != record_id
            }

    async def update_importance(
        self, record_id: UUID, user_id: UUID, importance: float
    ) -> None:
        """Directly set the importance score on a semantic memory."""
        from app.schemas.memory import SemanticMemoryUpdate
        await self._svc.update_semantic(
            record_id, user_id, SemanticMemoryUpdate(importance=importance)
        )

    async def search(self, request: MemorySearchRequest) -> list[ScoredMemory]:
        async with self._span("search", strategy=request.strategy):
            if request.strategy in (SearchStrategy.SEMANTIC, SearchStrategy.HYBRID):
                return await self._semantic_search(request)
            if request.strategy == SearchStrategy.IMPORTANCE:
                return await self._importance_search(request)
            return await self._recency_search(request)

    async def list_recent(
        self, user_id: UUID, *, limit: int = 20, offset: int = 0
    ) -> list[MemoryRecord]:
        records, _ = await self._svc.list_semantic(
            user_id,
            page=max(1, offset // limit + 1),
            page_size=limit,
        )
        return [self._to_memory_record(r) for r in records]

    async def count(self, user_id: UUID) -> int:
        _, total = await self._svc.list_semantic(user_id, page=1, page_size=1)
        return total

    async def prune_expired(self, user_id: UUID) -> int:
        """Soft-delete expired semantic memories; return count removed."""
        return await self._svc.prune_expired_semantic(user_id)

    # ------------------------------------------------------------------
    # Internal search strategies
    # ------------------------------------------------------------------

    async def _semantic_search(
        self, request: MemorySearchRequest
    ) -> list[ScoredMemory]:
        if not self._embed or not self._qdrant:
            return await self._recency_search(request)

        from app.database.qdrant import filter_by_user, search_dense, combine_filters, filter_by_tags

        try:
            vector = await self._embed.embed_text(request.query)
            f = filter_by_user(str(request.user_id))
            if request.tags_filter:
                f = combine_filters(f, filter_by_tags(request.tags_filter))

            hits = await search_dense(
                self._qdrant,
                QDRANT_COLLECTION_SEMANTIC_MEMORIES,
                vector,
                top_k=request.top_k * 2,
                score_threshold=request.min_score,
                payload_filter=f,
            )
        except Exception as exc:
            self._log.warning("semantic_vector_search_failed", exc=str(exc))
            return await self._recency_search(request)

        scored: list[ScoredMemory] = []
        for hit in hits:
            p = hit.payload or {}
            rec = MemoryRecord(
                id=UUID(str(hit.id)),
                layer=MemoryLayerType.SEMANTIC,
                user_id=request.user_id,
                content=p.get("content", ""),
                summary=p.get("summary"),
                importance=p.get("importance", 0.5),
                tags=p.get("tags") or [],
                created_at=datetime.fromisoformat(
                    p.get("created_at", datetime.now(tz=timezone.utc).isoformat())
                ),
            )
            if rec.is_expired and request.exclude_expired:
                continue
            scored.append(
                self.composite_score(
                    rec,
                    semantic_score=hit.score,
                    semantic_weight=request.semantic_weight,
                )
            )
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[: request.top_k]

    async def _importance_search(
        self, request: MemorySearchRequest
    ) -> list[ScoredMemory]:
        records, _ = await self._svc.list_semantic(
            request.user_id, min_importance=request.min_score, page=1, page_size=request.top_k
        )
        result = []
        for r in records:
            rec = self._to_memory_record(r)
            if rec.is_expired and request.exclude_expired:
                continue
            result.append(
                ScoredMemory(
                    record=rec,
                    score=rec.importance,
                    importance_score=rec.importance,
                )
            )
        return result

    async def _recency_search(self, request: MemorySearchRequest) -> list[ScoredMemory]:
        records = await self.list_recent(request.user_id, limit=request.top_k)
        scored = [self.composite_score(r) for r in records if not (r.is_expired and request.exclude_expired)]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    # ------------------------------------------------------------------
    # Qdrant persistence
    # ------------------------------------------------------------------

    async def _store_in_qdrant(
        self, point_id: UUID, vector: list[float], record: MemoryRecord
    ) -> None:
        from app.database.qdrant import build_point, upsert_points
        point = build_point(
            point_id=point_id,
            dense_vector=vector,
            payload={
                "user_id": str(record.user_id),
                "content": record.content[:2000],
                "summary": record.summary,
                "importance": record.importance,
                "tags": record.tags,
                "source_type": record.metadata.get("source_type", "manual"),
                "created_at": datetime.now(tz=timezone.utc).isoformat(),
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            },
        )
        await upsert_points(self._qdrant, QDRANT_COLLECTION_SEMANTIC_MEMORIES, [point])

    @staticmethod
    def _to_memory_record(mem) -> MemoryRecord:
        return MemoryRecord(
            id=mem.id,
            layer=MemoryLayerType.SEMANTIC,
            user_id=mem.user_id,
            content=mem.content,
            summary=mem.summary,
            importance=mem.importance,
            tags=mem.tags or [],
            created_at=mem.created_at,
            last_accessed=mem.last_accessed_at,
            access_count=mem.access_count,
            expires_at=mem.expires_at,
            qdrant_point_id=mem.qdrant_point_id,
            source_id=mem.source_id,
            metadata={
                "source_type": str(mem.source_type) if mem.source_type else "manual",
            },
        )