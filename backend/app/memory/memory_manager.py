"""IOS Memory — Memory Manager."""
from __future__ import annotations

from uuid import UUID

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.memory.exceptions import MemoryNotFoundError
from app.memory.interfaces import IMemoryLayer
from app.memory.memory_snapshot import MemorySnapshot
from app.memory.types import (
    MemoryLayerType,
    MemoryRecord,
    MemorySearchRequest,
    SnapshotMeta,
    SnapshotRestoreResult,
    WorkingMemorySlot,
    WorkingMemoryState,
)
from app.memory.working_memory import WorkingMemory


logger = get_logger(__name__)


class MemoryManager:
    """
    Single entry point for all memory layer operations.

    Coordinates:
    - WorkingMemory  (ephemeral, Redis-backed)
    - EpisodicMemory (experience records, PostgreSQL + Qdrant)
    - SemanticMemory (long-term knowledge, PostgreSQL + Qdrant)
    - MemorySnapshot (checkpoint / restore)

    All layer objects are injected; MemoryManager never instantiates
    them internally, keeping it fully testable.
    """

    def __init__(
        self,
        working: "WorkingMemory",  # type: ignore[name-defined]
        episodic: IMemoryLayer,
        semantic: IMemoryLayer,
        snapshot: MemorySnapshot,
    ) -> None:
        from app.memory.working_memory import WorkingMemory as _WM
        self._working: _WM = working
        self._episodic = episodic
        self._semantic = semantic
        self._snapshot = snapshot
        self._layers: dict[MemoryLayerType, IMemoryLayer] = {
            MemoryLayerType.EPISODIC: episodic,
            MemoryLayerType.SEMANTIC: semantic,
        }

    # ------------------------------------------------------------------
    # Write routing
    # ------------------------------------------------------------------

    async def remember(self, record: MemoryRecord) -> MemoryRecord:
        """
        Persist a memory record to the appropriate layer.

        Args:
            record: The record to persist (layer field determines routing).

        Returns:
            Saved record with any DB-assigned fields populated.
        """
        async with create_async_span(
            "memory_manager.remember",
            attributes={"layer": record.layer.value},
        ):
            if record.layer == MemoryLayerType.WORKING:
                await self._working.write(record)
                return record
            layer = self._layers.get(record.layer)
            if layer is None:
                raise MemoryNotFoundError(
                    f"No layer registered for type '{record.layer}'."
                )
            return await layer.write(record)

    async def remember_episodic(self, record: MemoryRecord) -> MemoryRecord:
        """Convenience: write directly to episodic layer."""
        record.layer = MemoryLayerType.EPISODIC
        return await self.remember(record)

    async def remember_semantic(self, record: MemoryRecord) -> MemoryRecord:
        """Convenience: write directly to semantic layer."""
        record.layer = MemoryLayerType.SEMANTIC
        return await self.remember(record)

    # ------------------------------------------------------------------
    # Read routing
    # ------------------------------------------------------------------

    async def recall(
        self, record_id: UUID, user_id: UUID, layer: MemoryLayerType
    ) -> MemoryRecord:
        """
        Retrieve a record by ID from the specified layer.

        Raises:
            MemoryNotFoundError: Record not found in the target layer.
        """
        async with create_async_span(
            "memory_manager.recall",
            attributes={"layer": layer.value, "record_id": str(record_id)},
        ):
            if layer == MemoryLayerType.WORKING:
                raise MemoryNotFoundError(
                    "Working memory does not support ID-based recall. Use get_slot()."
                )
            target = self._layers.get(layer)
            if not target:
                raise MemoryNotFoundError(f"Unknown layer: {layer}")
            return await target.read(record_id, user_id)

    # ------------------------------------------------------------------
    # Delete routing
    # ------------------------------------------------------------------

    async def forget(
        self, record_id: UUID, user_id: UUID, layer: MemoryLayerType
    ) -> None:
        """Soft-delete a record from the specified layer."""
        async with create_async_span(
            "memory_manager.forget",
            attributes={"layer": layer.value},
        ):
            if layer == MemoryLayerType.WORKING:
                await self._working.delete_slot(record_id.hex)
                return
            target = self._layers.get(layer)
            if target:
                await target.delete(record_id, user_id)

    # ------------------------------------------------------------------
    # Working memory helpers (pass-through for convenience)
    # ------------------------------------------------------------------

    async def set_context(
        self, key: str, value, *, pinned: bool = False
    ) -> WorkingMemorySlot:
        """Write a named slot to working memory."""
        from app.memory.types import MemoryPriority
        return await self._working.set_slot(
            key, value, priority=MemoryPriority.NORMAL, pinned=pinned
        )

    async def get_context(self, key: str):
        """Read a named slot from working memory."""
        return await self._working.get_slot(key)

    async def export_working_context(self, max_tokens: int | None = None) -> str:
        """Serialise working memory into a flat string for LLM injection."""
        return await self._working.export_context(max_tokens)

    async def working_memory_state(self) -> WorkingMemoryState:
        return await self._working.get_state()

    async def token_usage(self) -> tuple[int, int]:
        """Return (used_tokens, budget_tokens)."""
        return await self._working.token_usage()

    async def clear_working_memory(self, *, preserve_pinned: bool = True) -> int:
        """Clear working memory; return number of slots removed."""
        return await self._working.clear(preserve_pinned=preserve_pinned)

    async def compress_working_memory(self, summary: str, token_count: int) -> None:
        """Store a compressed summary replacing detailed slot content."""
        await self._working.set_summary(summary, token_count)

    # ------------------------------------------------------------------
    # Listing / counting
    # ------------------------------------------------------------------

    async def list_recent(
        self, user_id: UUID, layer: MemoryLayerType, *, limit: int = 20
    ) -> list[MemoryRecord]:
        """Return recent records from the specified layer."""
        target = self._layers.get(layer)
        if not target:
            return []
        return await target.list_recent(user_id, limit=limit)

    async def count(self, user_id: UUID, layer: MemoryLayerType) -> int:
        """Return total record count in the specified layer for a user."""
        target = self._layers.get(layer)
        return await target.count(user_id) if target else 0

    async def count_all(self, user_id: UUID) -> dict[str, int]:
        """Return record counts across all layers."""
        return {
            MemoryLayerType.WORKING.value: await self._working.count(user_id),
            MemoryLayerType.EPISODIC.value: await self._episodic.count(user_id),
            MemoryLayerType.SEMANTIC.value: await self._semantic.count(user_id),
        }

    # ------------------------------------------------------------------
    # Snapshot operations
    # ------------------------------------------------------------------

    async def create_snapshot(
        self,
        user_id: UUID,
        layer: MemoryLayerType,
        *,
        trigger: str = "manual",
        notes: str | None = None,
    ) -> SnapshotMeta:
        """
        Create a checkpoint snapshot of a memory layer.

        Fetches recent records from the target layer and persists them.
        """
        async with create_async_span(
            "memory_manager.create_snapshot",
            attributes={"layer": layer.value, "trigger": trigger},
        ):
            records = await self.list_recent(user_id, layer, limit=500)
            used, _ = await self.token_usage()
            meta = await self._snapshot.create(
                user_id,
                layer,
                records,
                trigger=trigger,
                notes=notes,
                working_tokens=used if layer == MemoryLayerType.WORKING else None,
            )
            logger.info(
                "snapshot_created",
                user_id=str(user_id),
                layer=layer.value,
                snapshot_id=str(meta.id),
            )
            return meta

    async def restore_snapshot(
        self, snapshot_id: UUID, user_id: UUID, layer: MemoryLayerType
    ) -> SnapshotRestoreResult:
        """Restore memory records from a snapshot into the target layer."""
        async with create_async_span(
            "memory_manager.restore_snapshot",
            attributes={"snapshot_id": str(snapshot_id)},
        ):
            target = self._layers.get(layer)
            if not target:
                from app.memory.exceptions import SnapshotError
                raise SnapshotError(f"Cannot restore into unknown layer '{layer}'.")
            result = await self._snapshot.restore(snapshot_id, user_id, target)
            logger.info(
                "snapshot_restored",
                snapshot_id=str(snapshot_id),
                records=result.records_restored,
            )
            return result

    async def list_snapshots(
        self,
        user_id: UUID,
        layer: MemoryLayerType | None = None,
        limit: int = 20,
    ) -> list[SnapshotMeta]:
        return await self._snapshot.list_snapshots(user_id, layer=layer, limit=limit)

    async def verify_snapshot(self, snapshot_id: UUID, user_id: UUID) -> bool:
        return await self._snapshot.verify(snapshot_id, user_id)

    # ------------------------------------------------------------------
    # Consolidation (working → episodic/semantic promotion)
    # ------------------------------------------------------------------

    async def consolidate_to_episodic(
        self, user_id: UUID, task_record: MemoryRecord
    ) -> MemoryRecord:
        """
        Promote a working-memory summary into an episodic experience record.

        Called by agents after task completion.
        """
        async with create_async_span("memory_manager.consolidate_to_episodic"):
            task_record.layer = MemoryLayerType.EPISODIC
            saved = await self._episodic.write(task_record)
            logger.info(
                "memory_consolidated_episodic",
                user_id=str(user_id),
                record_id=str(saved.id),
            )
            return saved

    async def consolidate_to_semantic(
        self, user_id: UUID, knowledge_record: MemoryRecord
    ) -> MemoryRecord:
        """
        Promote a fact or concept into semantic long-term memory.

        Called by agents after document ingestion or knowledge extraction.
        """
        async with create_async_span("memory_manager.consolidate_to_semantic"):
            knowledge_record.layer = MemoryLayerType.SEMANTIC
            saved = await self._semantic.write(knowledge_record)
            logger.info(
                "memory_consolidated_semantic",
                user_id=str(user_id),
                record_id=str(saved.id),
            )
            return saved