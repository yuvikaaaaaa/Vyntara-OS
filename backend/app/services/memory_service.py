"""IOS — Memory Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import MemoryOutcome, MemorySourceType, MemoryType
from app.core.exceptions import AuthorizationError, NotFoundError
from app.models.memory import EpisodicMemory, MemorySnapshot, SemanticMemory, WorkingMemory
from app.schemas.memory import (
    EpisodicMemoryCreate,
    SemanticMemoryCreate,
    SemanticMemoryUpdate,
    WorkingMemoryUpdate,
)
from app.services.base import BaseService


class MemoryService(BaseService):
    """Orchestrates all memory layer operations."""

    # ------------------------------------------------------------------
    # Working Memory
    # ------------------------------------------------------------------

    async def get_working_memory(
        self, conversation_id: UUID, user_id: UUID
    ) -> WorkingMemory | None:
        async with self._transaction() as uow:
            wm = await uow.memory.get_by_conversation(conversation_id)
            if wm and wm.user_id != user_id:
                raise AuthorizationError("Access denied.")
            return wm

    async def update_working_memory(
        self, conversation_id: UUID, user_id: UUID, data: WorkingMemoryUpdate
    ) -> WorkingMemory:
        async with self._span("update_working_memory"):
            async with self._transaction() as uow:
                wm = await uow.memory.get_by_conversation(conversation_id)
                if not wm:
                    raise NotFoundError("Working memory not found.")
                if wm.user_id != user_id:
                    raise AuthorizationError("Access denied.")
                updates = data.model_dump(exclude_none=True)
                await uow.memory.update(wm, updates)
                return wm

    async def compress_working_memory(
        self,
        conversation_id: UUID,
        user_id: UUID,
        *,
        new_token_count: int,
        summary: str,
        summary_token_count: int,
    ) -> WorkingMemory:
        """Record a context compression event on the working memory record."""
        async with self._span("compress_working_memory"):
            async with self._transaction() as uow:
                wm = await uow.memory.get_by_conversation(conversation_id)
                if not wm or wm.user_id != user_id:
                    raise NotFoundError("Working memory not found.")
                await uow.memory.update_token_count(wm.id, new_token_count)
                await uow.memory.increment_compression_count(wm.id)
                await uow.memory.update(
                    wm,
                    {
                        "summary": summary,
                        "summary_token_count": summary_token_count,
                    },
                )
                return wm

    # ------------------------------------------------------------------
    # Episodic Memory
    # ------------------------------------------------------------------

    async def record_episode(
        self,
        user_id: UUID,
        data: EpisodicMemoryCreate,
        *,
        agent_task_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> EpisodicMemory:
        async with self._span("record_episode"):
            async with self._transaction() as uow:
                mem = EpisodicMemory(
                    user_id=user_id,
                    agent_task_id=agent_task_id,
                    conversation_id=conversation_id,
                    task_description=data.task_description,
                    execution_summary=data.execution_summary,
                    outcome=data.outcome,
                    quality_score=data.quality_score,
                    reflection_score=data.reflection_score,
                    tools_used=data.tools_used,
                    agents_used=data.agents_used,
                    total_tokens=data.total_tokens,
                    duration_ms=data.duration_ms,
                    step_count=data.step_count,
                    tags=data.tags,
                    extra_data=data.extra_data,
                )
                saved = await uow.memory.create_episodic(mem)
                self._log.info("episode_recorded", memory_id=str(saved.id))
                return saved

    async def get_episode(self, memory_id: UUID, user_id: UUID) -> EpisodicMemory:
        async with self._transaction() as uow:
            mem = await uow.memory.get_episodic_by_id(memory_id)
            if not mem or mem.is_deleted:
                raise NotFoundError("Episodic memory not found.")
            if mem.user_id != user_id:
                raise AuthorizationError("Access denied.")
            return mem

    async def list_episodes(
        self,
        user_id: UUID,
        *,
        outcome: MemoryOutcome | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EpisodicMemory], int]:
        async with self._transaction() as uow:
            return await uow.memory.list_episodic(
                user_id, outcome=outcome, page=page, page_size=page_size
            )

    async def mark_episode_embedded(
        self, memory_id: UUID, qdrant_point_id: UUID
    ) -> None:
        async with self._transaction() as uow:
            await uow.memory.mark_episodic_embedded(memory_id, qdrant_point_id)

    async def get_unembedded_episodes(
        self, user_id: UUID, limit: int = 50
    ) -> list[EpisodicMemory]:
        async with self._transaction() as uow:
            return await uow.memory.get_unembedded_episodic(user_id, limit=limit)

    async def rate_episode(
        self, memory_id: UUID, user_id: UUID, rating: int
    ) -> EpisodicMemory:
        """Store explicit user star rating (1-5) on an episodic memory."""
        async with self._transaction() as uow:
            mem = await uow.memory.get_episodic_by_id(memory_id)
            if not mem or mem.user_id != user_id:
                raise NotFoundError("Episodic memory not found.")
            await uow.memory.update(mem, {"user_rating": rating})
            return mem

    # ------------------------------------------------------------------
    # Semantic Memory
    # ------------------------------------------------------------------

    async def create_semantic(
        self, user_id: UUID, data: SemanticMemoryCreate
    ) -> SemanticMemory:
        async with self._span("create_semantic"):
            async with self._transaction() as uow:
                mem = SemanticMemory(
                    user_id=user_id,
                    content=data.content,
                    summary=data.summary,
                    importance=data.importance,
                    source_type=data.source_type,
                    source_id=data.source_id,
                    tags=data.tags,
                    expires_at=data.expires_at,
                    extra_data=data.extra_data,
                )
                saved = await uow.memory.create_semantic(mem)
                return saved

    async def get_semantic(self, memory_id: UUID, user_id: UUID) -> SemanticMemory:
        async with self._transaction() as uow:
            mem = await uow.memory.get_semantic_by_id(memory_id)
            if not mem or mem.is_deleted:
                raise NotFoundError("Semantic memory not found.")
            if mem.user_id != user_id:
                raise AuthorizationError("Access denied.")
            return mem

    async def list_semantic(
        self,
        user_id: UUID,
        *,
        source_type: MemorySourceType | None = None,
        tags: list[str] | None = None,
        min_importance: float = 0.0,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SemanticMemory], int]:
        async with self._transaction() as uow:
            return await uow.memory.list_semantic(
                user_id,
                source_type=source_type,
                tags=tags,
                min_importance=min_importance,
                page=page,
                page_size=page_size,
            )

    async def update_semantic(
        self, memory_id: UUID, user_id: UUID, data: SemanticMemoryUpdate
    ) -> SemanticMemory:
        async with self._transaction() as uow:
            mem = await uow.memory.get_semantic_by_id(memory_id)
            if not mem or mem.is_deleted or mem.user_id != user_id:
                raise NotFoundError("Semantic memory not found.")
            await uow.memory.update(mem, data.model_dump(exclude_none=True))
            return mem

    async def delete_semantic(self, memory_id: UUID, user_id: UUID) -> None:
        async with self._transaction() as uow:
            mem = await uow.memory.get_semantic_by_id(memory_id)
            if not mem or mem.user_id != user_id:
                raise NotFoundError("Semantic memory not found.")
            await uow.memory.soft_delete(mem)

    async def record_access(self, memory_id: UUID) -> None:
        async with self._transaction() as uow:
            await uow.memory.increment_access_count(memory_id)

    async def mark_semantic_embedded(
        self, memory_id: UUID, qdrant_point_id: UUID
    ) -> None:
        async with self._transaction() as uow:
            await uow.memory.mark_semantic_embedded(memory_id, qdrant_point_id)

    async def prune_expired_semantic(self, user_id: UUID) -> int:
        """Soft-delete semantic memories that have passed their expires_at."""
        async with self._transaction() as uow:
            expired = await uow.memory.get_expired_semantic(limit=200)
            user_expired = [m for m in expired if m.user_id == user_id]
            for mem in user_expired:
                await uow.memory.soft_delete(mem)
            return len(user_expired)

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    async def create_snapshot(
        self,
        user_id: UUID,
        snapshot_type: MemoryType,
        trigger: str,
        state_json: dict,
        *,
        working_tokens: int | None = None,
        episodic_count: int | None = None,
        semantic_count: int | None = None,
        notes: str | None = None,
    ) -> MemorySnapshot:
        import hashlib
        import json
        raw = json.dumps(state_json, sort_keys=True, default=str)
        checksum = hashlib.sha256(raw.encode()).hexdigest()
        async with self._transaction() as uow:
            snap = MemorySnapshot(
                user_id=user_id,
                snapshot_type=snapshot_type,
                trigger=trigger,
                state_json=state_json,
                size_bytes=len(raw.encode()),
                checksum_sha256=checksum,
                working_memory_tokens=working_tokens,
                episodic_count=episodic_count,
                semantic_count=semantic_count,
                notes=notes,
            )
            return await uow.memory.create_snapshot(snap)

    async def list_snapshots(
        self,
        user_id: UUID,
        snapshot_type: MemoryType | None = None,
        limit: int = 20,
    ) -> list[MemorySnapshot]:
        async with self._transaction() as uow:
            return await uow.memory.list_snapshots(
                user_id, snapshot_type=snapshot_type, limit=limit
            )

    async def get_snapshot(
        self, snapshot_id: UUID, user_id: UUID
    ) -> MemorySnapshot:
        async with self._transaction() as uow:
            snap = await uow.memory.get_snapshot_by_id(snapshot_id)
            if not snap or snap.user_id != user_id:
                raise NotFoundError("Snapshot not found.")
            return snap