"""IOS Memory — Episodic Memory Layer."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.constants import QDRANT_COLLECTION_EPISODIC_MEMORIES
from app.core.enums import MemoryOutcome as ServiceOutcome
from app.memory.base import BaseMemoryLayer
from app.memory.exceptions import EpisodicMemoryError, MemoryNotFoundError
from app.memory.interfaces import IEmbeddingGateway
from app.memory.types import (
    MemoryLayerType,
    MemoryOutcome,
    MemoryRecord,
    MemorySearchRequest,
    SearchStrategy,
    ScoredMemory,
)
from app.services.memory_service import MemoryService


class EpisodicMemory(BaseMemoryLayer):
    """Experience-based memory backed by PostgreSQL + Qdrant."""

    @property
    def layer_type(self) -> MemoryLayerType:
        return MemoryLayerType.EPISODIC

    def __init__(self, memory_service: MemoryService, embedding_gateway: IEmbeddingGateway | None = None, qdrant_client=None) -> None:
        super().__init__()
        self._svc = memory_service
        self._embed = embedding_gateway
        self._qdrant = qdrant_client

    async def write(self, record: MemoryRecord) -> MemoryRecord:
        async with self._span("write"):
            from app.core.enums import MemoryOutcome as Svc
            from app.schemas.memory import EpisodicMemoryCreate
            outcome_map = {MemoryOutcome.SUCCESS: Svc.SUCCESS, MemoryOutcome.PARTIAL: Svc.PARTIAL, MemoryOutcome.FAILURE: Svc.FAILURE}
            outcome = outcome_map.get(record.metadata.get("outcome", MemoryOutcome.SUCCESS), Svc.SUCCESS)
            data = EpisodicMemoryCreate(
                task_description=record.content,
                execution_summary=record.summary,
                outcome=outcome,
                quality_score=record.importance,
                tools_used=record.metadata.get("tools_used", []),
                agents_used=record.metadata.get("agents_used", []),
                total_tokens=record.metadata.get("total_tokens"),
                duration_ms=record.metadata.get("duration_ms"),
                step_count=record.metadata.get("step_count", 0),
                tags=record.tags,
                extra_data=record.metadata,
            )
            saved = await self._svc.record_episode(record.user_id, data, agent_task_id=record.source_id)
            if self._embed and self._qdrant:
                try:
                    vector = await self._embed.embed_text(record.content)
                    await self._store_qdrant(saved.id, vector, record)
                    await self._svc.mark_episode_embedded(saved.id, saved.id)
                except Exception as exc:
                    self._log.warning("episodic_embed_failed", exc=str(exc))
            record.id = saved.id
            return record

    async def read(self, record_id: UUID, user_id: UUID) -> MemoryRecord:
        ep = await self._svc.get_episode(record_id, user_id)
        return self._to_record(ep)

    async def delete(self, record_id: UUID, user_id: UUID) -> None:
        self._log.info("episodic_delete", record_id=str(record_id))

    async def search(self, request: MemorySearchRequest) -> list[ScoredMemory]:
        if request.strategy in (SearchStrategy.SEMANTIC, SearchStrategy.HYBRID) and self._embed and self._qdrant:
            return await self._vector_search(request)
        return await self._recency_search(request)

    async def list_recent(self, user_id: UUID, *, limit: int = 20, offset: int = 0) -> list[MemoryRecord]:
        episodes, _ = await self._svc.list_episodes(user_id, page=1, page_size=limit)
        return [self._to_record(e) for e in episodes]

    async def count(self, user_id: UUID) -> int:
        _, total = await self._svc.list_episodes(user_id, page=1, page_size=1)
        return total

    async def _vector_search(self, request: MemorySearchRequest) -> list[ScoredMemory]:
        from app.database.qdrant import filter_by_user, search_dense
        try:
            vector = await self._embed.embed_text(request.query)
            hits = await search_dense(self._qdrant, QDRANT_COLLECTION_EPISODIC_MEMORIES, vector, top_k=request.top_k * 2, score_threshold=request.min_score, payload_filter=filter_by_user(str(request.user_id)))
        except Exception as exc:
            self._log.warning("episodic_vector_search_failed", exc=str(exc))
            return await self._recency_search(request)
        scored = []
        for hit in hits:
            p = hit.payload or {}
            rec = MemoryRecord(id=UUID(str(hit.id)), layer=MemoryLayerType.EPISODIC, user_id=request.user_id, content=p.get("task_description", ""), summary=p.get("execution_summary"), importance=p.get("quality_score") or 0.5, tags=p.get("tags") or [], created_at=datetime.fromisoformat(p.get("created_at", datetime.now(tz=timezone.utc).isoformat())))
            scored.append(self.composite_score(rec, semantic_score=hit.score, semantic_weight=request.semantic_weight))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[: request.top_k]

    async def _recency_search(self, request: MemorySearchRequest) -> list[ScoredMemory]:
        records = await self.list_recent(request.user_id, limit=request.top_k)
        return sorted([self.composite_score(r) for r in records], key=lambda s: s.score, reverse=True)

    async def _store_qdrant(self, point_id: UUID, vector: list[float], record: MemoryRecord) -> None:
        from app.database.qdrant import build_point, upsert_points
        point = build_point(point_id=point_id, dense_vector=vector, payload={"user_id": str(record.user_id), "task_description": record.content[:1000], "quality_score": record.importance, "tags": record.tags, "outcome": record.metadata.get("outcome", "success"), "agents_used": record.metadata.get("agents_used", []), "tools_used": record.metadata.get("tools_used", []), "created_at": datetime.now(tz=timezone.utc).isoformat()})
        await upsert_points(self._qdrant, QDRANT_COLLECTION_EPISODIC_MEMORIES, [point])

    @staticmethod
    def _to_record(ep) -> MemoryRecord:
        from app.core.enums import MemoryOutcome as Svc
        outcome_map = {Svc.SUCCESS: MemoryOutcome.SUCCESS, Svc.PARTIAL: MemoryOutcome.PARTIAL, Svc.FAILURE: MemoryOutcome.FAILURE}
        return MemoryRecord(id=ep.id, layer=MemoryLayerType.EPISODIC, user_id=ep.user_id, content=ep.task_description, summary=ep.execution_summary, importance=ep.quality_score or 0.5, tags=ep.tags or [], created_at=ep.created_at, qdrant_point_id=ep.qdrant_point_id, source_id=ep.agent_task_id, metadata={"outcome": outcome_map.get(ep.outcome, MemoryOutcome.SUCCESS), "tools_used": ep.tools_used or [], "agents_used": ep.agents_used or [], "total_tokens": ep.total_tokens, "duration_ms": ep.duration_ms, "step_count": ep.step_count})