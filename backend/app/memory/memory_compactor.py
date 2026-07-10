"""IOS Memory — Memory Compactor."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.memory.base import BaseMemoryLayer
from app.memory.interfaces import IMemoryLayer
from app.memory.types import (
    CompactionResult,
    MemoryLayerType,
    MemoryPriority,
    MemoryRecord,
)

logger = get_logger(__name__)


@dataclass
class RetentionPolicy:
    """
    Configurable retention policy for a memory layer.

    Attributes:
        max_records: Hard cap on total records; oldest non-critical removed first.
        min_importance: Records below this importance are candidates for pruning.
        max_age_days: Records older than this are expired (overrides expires_at).
        deduplicate: Whether to run fingerprint-based duplicate removal.
    """

    max_records: int = 10_000
    min_importance: float = 0.1
    max_age_days: float | None = None
    deduplicate: bool = True


class MemoryCompactor:
    """
    Memory maintenance engine.

    Performs scheduled or on-demand compaction of memory layers:
    - Prune expired records
    - Remove low-importance records when approaching capacity
    - Deduplicate by content fingerprint
    - Evict working-memory slots to free token budget
    - Hook point for future LLM-based summarisation

    All operations are non-destructive by default (soft-delete).
    """

    def __init__(
        self,
        episodic: IMemoryLayer,
        semantic: IMemoryLayer,
        working: "WorkingMemory | None" = None,  # type: ignore[name-defined]
        *,
        episodic_policy: RetentionPolicy | None = None,
        semantic_policy: RetentionPolicy | None = None,
    ) -> None:
        self._episodic = episodic
        self._semantic = semantic
        self._working = working
        self._episodic_policy = episodic_policy or RetentionPolicy(max_records=50_000)
        self._semantic_policy = semantic_policy or RetentionPolicy(max_records=100_000)

    # ------------------------------------------------------------------
    # Full compaction pass
    # ------------------------------------------------------------------

    async def compact_all(self, user_id: UUID) -> list[CompactionResult]:
        """Run a full compaction pass on all configured layers."""
        async with create_async_span("memory_compactor.compact_all"):
            results: list[CompactionResult] = []

            if self._working:
                results.append(await self.compact_working_memory(user_id))
            results.append(await self.compact_semantic(user_id))
            results.append(await self.compact_episodic(user_id))

            total_evicted = sum(len(r.evicted_ids) for r in results)
            logger.info(
                "compaction_complete",
                user_id=str(user_id),
                layers=len(results),
                total_evicted=total_evicted,
            )
            return results

    # ------------------------------------------------------------------
    # Working memory compaction
    # ------------------------------------------------------------------

    async def compact_working_memory(
        self,
        user_id: UUID,
        *,
        target_utilisation: float = 0.75,
    ) -> CompactionResult:
        """
        Evict non-pinned slots from working memory to reach target utilisation.

        Args:
            user_id: Owning user.
            target_utilisation: Target fraction of token budget after compaction.

        Returns:
            CompactionResult with eviction details.
        """
        async with create_async_span("memory_compactor.compact_working"):
            if not self._working:
                return CompactionResult(
                    layer=MemoryLayerType.WORKING,
                    records_before=0,
                    records_after=0,
                    tokens_before=0,
                    tokens_after=0,
                )

            used, budget = await self._working.token_usage()
            target_tokens = int(budget * target_utilisation)
            tokens_before = used

            evicted_keys: list[str] = []
            if used > target_tokens:
                free_needed = used - target_tokens
                evicted_keys = await self._working.evict_lowest_priority(free_needed)

            used_after, _ = await self._working.token_usage()
            slots_before = await self._working.count(user_id)

            result = CompactionResult(
                layer=MemoryLayerType.WORKING,
                records_before=slots_before + len(evicted_keys),
                records_after=slots_before,
                tokens_before=tokens_before,
                tokens_after=used_after,
                evicted_ids=[],   # slot keys, not UUIDs — stored in metadata
            )
            if evicted_keys:
                logger.info(
                    "working_memory_compacted",
                    user_id=str(user_id),
                    evicted_slots=len(evicted_keys),
                    tokens_freed=tokens_before - used_after,
                )
            return result

    # ------------------------------------------------------------------
    # Semantic memory compaction
    # ------------------------------------------------------------------

    async def compact_semantic(self, user_id: UUID) -> CompactionResult:
        """
        Compact semantic memory:
        1. Prune expired records.
        2. Remove records below min_importance if over capacity.
        3. Optionally deduplicate.
        """
        async with create_async_span("memory_compactor.compact_semantic"):
            policy = self._semantic_policy
            total_before = await self._semantic.count(user_id)
            evicted: list[UUID] = []

            # Step 1: prune expired
            pruned = await self._prune_expired_semantic(user_id)
            evicted.extend(pruned)

            # Step 2: importance-based pruning if over capacity
            current = await self._semantic.count(user_id)
            if current > policy.max_records:
                low_importance = await self._find_low_importance(
                    user_id,
                    layer=self._semantic,
                    min_importance=policy.min_importance,
                    limit=current - policy.max_records,
                )
                for record in low_importance:
                    try:
                        await self._semantic.delete(record.id, user_id)
                        evicted.append(record.id)
                    except Exception:
                        pass

            # Step 3: deduplication
            dupes: list[UUID] = []
            if policy.deduplicate:
                dupes = await self._deduplicate_layer(user_id, self._semantic)
                evicted.extend(dupes)

            total_after = await self._semantic.count(user_id)
            result = CompactionResult(
                layer=MemoryLayerType.SEMANTIC,
                records_before=total_before,
                records_after=total_after,
                tokens_before=0,
                tokens_after=0,
                evicted_ids=evicted,
            )
            logger.info(
                "semantic_memory_compacted",
                user_id=str(user_id),
                before=total_before,
                after=total_after,
                evicted=len(evicted),
                dupes=len(dupes),
            )
            return result

    # ------------------------------------------------------------------
    # Episodic memory compaction
    # ------------------------------------------------------------------

    async def compact_episodic(self, user_id: UUID) -> CompactionResult:
        """
        Compact episodic memory:
        1. Remove failure records below importance threshold (oldest first).
        2. Optionally deduplicate.
        """
        async with create_async_span("memory_compactor.compact_episodic"):
            policy = self._episodic_policy
            total_before = await self._episodic.count(user_id)
            evicted: list[UUID] = []

            current = await self._episodic.count(user_id)
            if current > policy.max_records:
                candidates = await self._find_low_importance(
                    user_id,
                    layer=self._episodic,
                    min_importance=policy.min_importance,
                    limit=current - policy.max_records,
                )
                for record in candidates:
                    try:
                        await self._episodic.delete(record.id, user_id)
                        evicted.append(record.id)
                    except Exception:
                        pass

            dupes: list[UUID] = []
            if policy.deduplicate:
                dupes = await self._deduplicate_layer(user_id, self._episodic)
                evicted.extend(dupes)

            total_after = await self._episodic.count(user_id)
            result = CompactionResult(
                layer=MemoryLayerType.EPISODIC,
                records_before=total_before,
                records_after=total_after,
                tokens_before=0,
                tokens_after=0,
                evicted_ids=evicted,
            )
            logger.info(
                "episodic_memory_compacted",
                user_id=str(user_id),
                before=total_before,
                after=total_after,
                evicted=len(evicted),
            )
            return result

    # ------------------------------------------------------------------
    # Compression hook (future LLM summarisation)
    # ------------------------------------------------------------------

    async def compress_layer(
        self,
        user_id: UUID,
        layer: IMemoryLayer,
        *,
        summariser=None,   # Future: accepts async callable (records → summary)
    ) -> CompactionResult:
        """
        Hook point for LLM-based memory summarisation.

        When a summariser is provided, it receives a list of MemoryRecords
        and returns a single summary string that replaces them.
        Currently, without a summariser, it falls back to importance eviction.

        Args:
            user_id: Owning user.
            layer: Target memory layer.
            summariser: Optional async callable ``(records) → str``.

        Returns:
            CompactionResult.
        """
        async with create_async_span("memory_compactor.compress"):
            records = await layer.list_recent(user_id, limit=100)
            before = len(records)

            if summariser is not None and records:
                try:
                    summary: str = await summariser(records)
                    # Store summary as a single high-importance record
                    from app.memory.types import MemoryRecord
                    import uuid
                    summary_record = MemoryRecord(
                        id=uuid.uuid4(),
                        layer=layer.layer_type,
                        user_id=user_id,
                        content=summary,
                        summary=summary[:200],
                        importance=0.9,
                    )
                    await layer.write(summary_record)
                    # Soft-delete the source records
                    for r in records:
                        try:
                            await layer.delete(r.id, user_id)
                        except Exception:
                            pass
                    return CompactionResult(
                        layer=layer.layer_type,
                        records_before=before,
                        records_after=1,
                        tokens_before=sum(
                            BaseMemoryLayer.estimate_tokens(r.content) for r in records
                        ),
                        tokens_after=BaseMemoryLayer.estimate_tokens(summary),
                        evicted_ids=[r.id for r in records],
                        summary_generated=True,
                    )
                except Exception as exc:
                    logger.warning("compression_summariser_failed", exc=str(exc))

            # Fallback: no-op compaction
            return CompactionResult(
                layer=layer.layer_type,
                records_before=before,
                records_after=before,
                tokens_before=0,
                tokens_after=0,
            )

    # ------------------------------------------------------------------
    # Shared internal helpers
    # ------------------------------------------------------------------

    async def _prune_expired_semantic(self, user_id: UUID) -> list[UUID]:
        """Delegate expired semantic pruning to the semantic layer."""
        try:
            from app.memory.semantic_memory import SemanticMemory
            if isinstance(self._semantic, SemanticMemory):
                count = await self._semantic.prune_expired(user_id)
                return []   # IDs not returned by prune_expired; count only
        except Exception as exc:
            logger.warning("prune_expired_failed", exc=str(exc))
        return []

    @staticmethod
    async def _find_low_importance(
        user_id: UUID,
        layer: IMemoryLayer,
        *,
        min_importance: float,
        limit: int,
    ) -> list[MemoryRecord]:
        """Return up to ``limit`` records below the importance threshold."""
        recent = await layer.list_recent(user_id, limit=min(limit * 3, 1000))
        candidates = [r for r in recent if r.importance < min_importance]
        # Sort by ascending importance (remove least important first)
        candidates.sort(key=lambda r: r.importance)
        return candidates[:limit]

    @staticmethod
    async def _deduplicate_layer(
        user_id: UUID,
        layer: IMemoryLayer,
    ) -> list[UUID]:
        """
        Remove duplicate records (same content fingerprint).

        Keeps the newest instance; soft-deletes older duplicates.
        """
        recent = await layer.list_recent(user_id, limit=2000)
        seen_fingerprints: dict[str, MemoryRecord] = {}
        duplicates: list[UUID] = []

        for record in sorted(recent, key=lambda r: r.created_at, reverse=True):
            fp = BaseMemoryLayer.content_fingerprint(record.content)
            if fp in seen_fingerprints:
                # Current record is older than the one already kept — delete it
                try:
                    await layer.delete(record.id, user_id)
                    duplicates.append(record.id)
                except Exception:
                    pass
            else:
                seen_fingerprints[fp] = record

        return duplicates