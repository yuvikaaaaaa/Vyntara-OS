"""IOS Memory — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class MemoryError(IosBaseException):
    http_status = 500
    code = "MEMORY_ERROR"


class WorkingMemoryError(MemoryError):
    code = "WORKING_MEMORY_ERROR"


class WorkingMemoryFullError(WorkingMemoryError):
    code = "WORKING_MEMORY_FULL"


class EpisodicMemoryError(MemoryError):
    code = "EPISODIC_MEMORY_ERROR"


class SemanticMemoryError(MemoryError):
    code = "SEMANTIC_MEMORY_ERROR"


class MemoryNotFoundError(MemoryError):
    http_status = 404
    code = "MEMORY_RECORD_NOT_FOUND"


class MemoryExpiredError(MemoryError):
    http_status = 410
    code = "MEMORY_EXPIRED"


class SnapshotError(MemoryError):
    code = "SNAPSHOT_ERROR"


class SnapshotNotFoundError(SnapshotError):
    http_status = 404
    code = "SNAPSHOT_NOT_FOUND"


class SnapshotCorruptedError(SnapshotError):
    code = "SNAPSHOT_CORRUPTED"


class SearchError(MemoryError):
    code = "MEMORY_SEARCH_ERROR"


class EmbeddingRequiredError(MemoryError):
    code = "EMBEDDING_PROVIDER_REQUIRED"


class CompactionError(MemoryError):
    code = "MEMORY_COMPACTION_ERROR"


class DuplicateMemoryError(MemoryError):
    code = "DUPLICATE_MEMORY"