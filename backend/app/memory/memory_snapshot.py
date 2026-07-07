"""IOS Memory — Memory Snapshot."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from app.core.enums import MemoryType
from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.memory.exceptions import SnapshotCorruptedError, SnapshotNotFoundError
from app.memory.types import (
    MemoryLayerType,
    MemoryRecord,
    SnapshotMeta,
    SnapshotRestoreResult,
)
from app.services.memory_service import MemoryService

logger = get_logger(__name__)

# Mapping between memory module layer type and service-layer enum
_LAYER_TO_SERVICE: dict[MemoryLayerType, MemoryType] = {
    MemoryLayerType.WORKING: MemoryType.WORKING,
    MemoryLayerType.EPISODIC: MemoryType.EPISODIC,
    MemoryLayerType.SEMANTIC: MemoryType.SEMANTIC,
    MemoryLayerType.SNAPSHOT: MemoryType.WORKING,
}


class MemorySnapshot:
    """
    Creates, lists, verifies, and restores memory layer snapshots.

    Each snapshot is:
    - Serialised to JSON
    - Checksummed with SHA-256 for tamper detection
    - Persisted via MemoryService (which writes to PostgreSQL)
    - Tagged with a trigger label and optional notes

    Restore replays the serialised records back into the target layer.
    """

    def __init__(self, memory_service: MemoryService) -> None:
        self._svc = memory_service

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    async def create(
        self,
        user_id: UUID,
        layer: MemoryLayerType,
        records: list[MemoryRecord],
        *,
        trigger: str = "manual",
        notes: str | None = None,
        working_tokens: int | None = None,
    ) -> SnapshotMeta:
        """
        Serialise and persist a snapshot of the given records.

        Args:
            user_id: Owning user.
            layer: Which memory layer is being snapshotted.
            records: Records to serialise into the snapshot.
            trigger: Why this snapshot was created.
            notes: Human-readable context notes.
            working_tokens: Current working-memory token count (for metadata).

        Returns:
            SnapshotMeta describing the created snapshot.
        """
        async with create_async_span(
            "memory.snapshot.create",
            attributes={"layer": layer.value, "records": str(len(records))},
        ):
            state_json = self._serialise(records)
            raw_bytes = json.dumps(state_json, sort_keys=True, default=str).encode()
            checksum = hashlib.sha256(raw_bytes).hexdigest()

            layer_enum = _LAYER_TO_SERVICE.get(layer, MemoryType.WORKING)
            saved = await self._svc.create_snapshot(
                user_id,
                layer_enum,
                trigger,
                state_json,
                working_tokens=working_tokens,
                episodic_count=len(records) if layer == MemoryLayerType.EPISODIC else None,
                semantic_count=len(records) if layer == MemoryLayerType.SEMANTIC else None,
                notes=notes,
            )
            logger.info(
                "snapshot_created",
                snapshot_id=str(saved.id),
                layer=layer.value,
                records=len(records),
                checksum=checksum[:12],
            )
            return SnapshotMeta(
                id=saved.id,
                user_id=user_id,
                layer=layer,
                trigger=trigger,
                created_at=saved.created_at,
                size_bytes=saved.size_bytes,
                checksum=saved.checksum_sha256,
                notes=notes,
            )

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    async def list_snapshots(
        self,
        user_id: UUID,
        layer: MemoryLayerType | None = None,
        limit: int = 20,
    ) -> list[SnapshotMeta]:
        """Return snapshot descriptors for a user, newest first."""
        layer_enum = _LAYER_TO_SERVICE.get(layer) if layer else None
        snaps = await self._svc.list_snapshots(user_id, snapshot_type=layer_enum, limit=limit)
        return [
            SnapshotMeta(
                id=s.id,
                user_id=user_id,
                layer=layer or MemoryLayerType.WORKING,
                trigger=s.trigger,
                created_at=s.created_at,
                size_bytes=s.size_bytes,
                checksum=s.checksum_sha256,
                notes=s.notes,
            )
            for s in snaps
        ]

    async def get_snapshot(self, snapshot_id: UUID, user_id: UUID) -> SnapshotMeta:
        snap = await self._svc.get_snapshot(snapshot_id, user_id)
        layer_map = {v: k for k, v in _LAYER_TO_SERVICE.items()}
        layer = layer_map.get(snap.snapshot_type, MemoryLayerType.WORKING)
        return SnapshotMeta(
            id=snap.id,
            user_id=user_id,
            layer=layer,
            trigger=snap.trigger,
            created_at=snap.created_at,
            size_bytes=snap.size_bytes,
            checksum=snap.checksum_sha256,
            notes=snap.notes,
        )

    # ------------------------------------------------------------------
    # Restoration
    # ------------------------------------------------------------------

    async def restore(
        self,
        snapshot_id: UUID,
        user_id: UUID,
        target_layer: "IMemoryLayer",  # type: ignore[name-defined]
    ) -> SnapshotRestoreResult:
        """
        Restore memory records from a snapshot into a target layer.

        Args:
            snapshot_id: ID of the snapshot to restore.
            user_id: Owning user (must match snapshot).
            target_layer: The memory layer implementation to write records into.

        Returns:
            SnapshotRestoreResult with success status and record count.
        """
        async with create_async_span(
            "memory.snapshot.restore",
            attributes={"snapshot_id": str(snapshot_id)},
        ):
            snap = await self._svc.get_snapshot(snapshot_id, user_id)
            if not snap:
                raise SnapshotNotFoundError(f"Snapshot {snapshot_id} not found.")

            # Integrity check
            raw = json.dumps(snap.state_json, sort_keys=True, default=str).encode()
            actual_checksum = hashlib.sha256(raw).hexdigest()
            if snap.checksum_sha256 and actual_checksum != snap.checksum_sha256:
                raise SnapshotCorruptedError(
                    f"Snapshot {snapshot_id} integrity check failed.",
                    details={
                        "expected": snap.checksum_sha256,
                        "actual": actual_checksum,
                    },
                )

            records = self._deserialise(snap.state_json)
            restored = 0
            errors: list[str] = []
            for record in records:
                try:
                    await target_layer.write(record)
                    restored += 1
                except Exception as exc:
                    errors.append(str(exc))

            logger.info(
                "snapshot_restored",
                snapshot_id=str(snapshot_id),
                records_restored=restored,
                errors=len(errors),
            )
            return SnapshotRestoreResult(
                snapshot_id=snapshot_id,
                layer=target_layer.layer_type,
                records_restored=restored,
                success=len(errors) == 0,
                error="; ".join(errors[:5]) if errors else None,
            )

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------

    async def verify(self, snapshot_id: UUID, user_id: UUID) -> bool:
        """
        Verify snapshot integrity without restoring.

        Returns:
            True if checksum matches, False otherwise.
        """
        snap = await self._svc.get_snapshot(snapshot_id, user_id)
        if not snap.checksum_sha256:
            return True  # No checksum stored — cannot verify, assume valid
        raw = json.dumps(snap.state_json, sort_keys=True, default=str).encode()
        return hashlib.sha256(raw).hexdigest() == snap.checksum_sha256

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise(records: list[MemoryRecord]) -> dict:
        return {
            "version": "1",
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "records": [
                {
                    "id": str(r.id),
                    "layer": r.layer.value,
                    "user_id": str(r.user_id),
                    "content": r.content,
                    "summary": r.summary,
                    "importance": r.importance,
                    "priority": r.priority,
                    "tags": r.tags,
                    "created_at": r.created_at.isoformat(),
                    "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                    "source_id": str(r.source_id) if r.source_id else None,
                    "metadata": r.metadata,
                }
                for r in records
            ],
        }

    @staticmethod
    def _deserialise(state_json: dict) -> list[MemoryRecord]:
        from uuid import UUID
        records: list[MemoryRecord] = []
        for d in state_json.get("records", []):
            try:
                records.append(
                    MemoryRecord(
                        id=UUID(d["id"]),
                        layer=MemoryLayerType(d["layer"]),
                        user_id=UUID(d["user_id"]),
                        content=d["content"],
                        summary=d.get("summary"),
                        importance=d.get("importance", 0.5),
                        tags=d.get("tags") or [],
                        created_at=datetime.fromisoformat(d["created_at"]),
                        expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None,
                        source_id=UUID(d["source_id"]) if d.get("source_id") else None,
                        metadata=d.get("metadata") or {},
                    )
                )
            except (KeyError, ValueError):
                continue
        return records