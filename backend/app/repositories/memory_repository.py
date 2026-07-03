"""IOS — Memory Repository."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.core.enums import MemoryOutcome, MemorySourceType, MemoryType
from app.models.memory import EpisodicMemory, MemorySnapshot, SemanticMemory, WorkingMemory
from app.repositories.base import BaseRepository


class MemoryRepository(BaseRepository[WorkingMemory]):
    model = WorkingMemory

    # ------------------------------------------------------------------
    # WorkingMemory
    # ------------------------------------------------------------------

    async def get_by_conversation(
        self, conversation_id: UUID
    ) -> WorkingMemory | None:
        stmt = select(WorkingMemory).where(
            WorkingMemory.conversation_id == conversation_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def update_token_count(
        self, working_memory_id: UUID, token_count: int
    ) -> None:
        stmt = (
            update(WorkingMemory)
            .where(WorkingMemory.id == working_memory_id)
            .values(
                current_token_count=token_count,
                last_accessed_at=datetime.now(tz=timezone.utc),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def increment_compression_count(
        self, working_memory_id: UUID
    ) -> None:
        stmt = (
            update(WorkingMemory)
            .where(WorkingMemory.id == working_memory_id)
            .values(compression_count=WorkingMemory.compression_count + 1)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # EpisodicMemory
    # ------------------------------------------------------------------

    async def create_episodic(self, mem: EpisodicMemory) -> EpisodicMemory:
        self._session.add(mem)
        await self._session.flush()
        await self._session.refresh(mem)
        return mem

    async def list_episodic(
        self,
        user_id: UUID,
        *,
        outcome: MemoryOutcome | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[EpisodicMemory], int]:
        filters = [
            EpisodicMemory.user_id == user_id,
            EpisodicMemory.deleted_at.is_(None),
        ]
        if outcome:
            filters.append(EpisodicMemory.outcome == outcome)

        total_stmt = select(func.count()).select_from(EpisodicMemory).where(
            and_(*filters)
        )
        total = (await self._session.execute(total_stmt)).scalar() or 0

        stmt = (
            select(EpisodicMemory)
            .where(and_(*filters))
            .order_by(EpisodicMemory.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_episodic_by_id(
        self, memory_id: UUID
    ) -> EpisodicMemory | None:
        return await self._session.get(EpisodicMemory, memory_id)

    async def mark_episodic_embedded(
        self, memory_id: UUID, qdrant_point_id: UUID
    ) -> None:
        stmt = (
            update(EpisodicMemory)
            .where(EpisodicMemory.id == memory_id)
            .values(is_embedded=True, qdrant_point_id=qdrant_point_id)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_unembedded_episodic(
        self, user_id: UUID, limit: int = 50
    ) -> list[EpisodicMemory]:
        stmt = (
            select(EpisodicMemory)
            .where(
                EpisodicMemory.user_id == user_id,
                EpisodicMemory.is_embedded.is_(False),
                EpisodicMemory.deleted_at.is_(None),
            )
            .order_by(EpisodicMemory.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # SemanticMemory
    # ------------------------------------------------------------------

    async def create_semantic(self, mem: SemanticMemory) -> SemanticMemory:
        self._session.add(mem)
        await self._session.flush()
        await self._session.refresh(mem)
        return mem

    async def get_semantic_by_id(
        self, memory_id: UUID
    ) -> SemanticMemory | None:
        return await self._session.get(SemanticMemory, memory_id)

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
        filters = [
            SemanticMemory.user_id == user_id,
            SemanticMemory.importance >= min_importance,
            SemanticMemory.deleted_at.is_(None),
        ]
        if source_type:
            filters.append(SemanticMemory.source_type == source_type)
        if tags:
            filters.append(SemanticMemory.tags.overlap(tags))  # type: ignore[attr-defined]

        total_stmt = select(func.count()).select_from(SemanticMemory).where(
            and_(*filters)
        )
        total = (await self._session.execute(total_stmt)).scalar() or 0

        stmt = (
            select(SemanticMemory)
            .where(and_(*filters))
            .order_by(SemanticMemory.importance.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def mark_semantic_embedded(
        self, memory_id: UUID, qdrant_point_id: UUID
    ) -> None:
        stmt = (
            update(SemanticMemory)
            .where(SemanticMemory.id == memory_id)
            .values(is_embedded=True, qdrant_point_id=qdrant_point_id)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def increment_access_count(self, memory_id: UUID) -> None:
        stmt = (
            update(SemanticMemory)
            .where(SemanticMemory.id == memory_id)
            .values(
                access_count=SemanticMemory.access_count + 1,
                last_accessed_at=datetime.now(tz=timezone.utc),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_expired_semantic(
        self, limit: int = 100
    ) -> list[SemanticMemory]:
        now = datetime.now(tz=timezone.utc)
        stmt = (
            select(SemanticMemory)
            .where(
                SemanticMemory.expires_at <= now,
                SemanticMemory.deleted_at.is_(None),
            )
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # MemorySnapshot
    # ------------------------------------------------------------------

    async def create_snapshot(self, snapshot: MemorySnapshot) -> MemorySnapshot:
        self._session.add(snapshot)
        await self._session.flush()
        await self._session.refresh(snapshot)
        return snapshot

    async def list_snapshots(
        self,
        user_id: UUID,
        snapshot_type: MemoryType | None = None,
        limit: int = 20,
    ) -> list[MemorySnapshot]:
        filters: list = [MemorySnapshot.user_id == user_id]
        if snapshot_type:
            filters.append(MemorySnapshot.snapshot_type == snapshot_type)
        stmt = (
            select(MemorySnapshot)
            .where(and_(*filters))
            .order_by(MemorySnapshot.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_snapshot_by_id(
        self, snapshot_id: UUID
    ) -> MemorySnapshot | None:
        return await self._session.get(MemorySnapshot, snapshot_id)