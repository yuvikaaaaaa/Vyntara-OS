"""IOS Memory — Public API.

The cognitive memory hierarchy for the Intelligence Operating System.

Usage::

    from app.memory import MemoryManager, WorkingMemory, MemorySearch
    from app.memory import MemoryRecord, MemorySearchRequest, MemoryLayerType
    from app.memory import MemoryRanker, RankingWeights
"""

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------
from app.memory.types import (
    CompactionResult,
    EpisodicRecord,
    MemoryLayerType,
    MemoryOutcome,
    MemoryPriority,
    MemoryRecord,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResult,
    RetentionPolicy,
    ScoredMemory,
    SearchStrategy,
    SnapshotMeta,
    SnapshotRestoreResult,
    WorkingMemorySlot,
    WorkingMemoryState,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
from app.memory.exceptions import (
    CompactionError,
    DuplicateMemoryError,
    EmbeddingRequiredError,
    EpisodicMemoryError,
    MemoryError,
    MemoryExpiredError,
    MemoryNotFoundError,
    SearchError,
    SemanticMemoryError,
    SnapshotCorruptedError,
    SnapshotError,
    SnapshotNotFoundError,
    WorkingMemoryError,
    WorkingMemoryFullError,
)

# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
from app.memory.interfaces import IEmbeddingGateway, IMemoryLayer

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
from app.memory.base import BaseMemoryLayer

# ---------------------------------------------------------------------------
# Memory layers
# ---------------------------------------------------------------------------
from app.memory.working_memory import WorkingMemory
from app.memory.episodic_memory import EpisodicMemory
from app.memory.semantic_memory import SemanticMemory

# ---------------------------------------------------------------------------
# Supporting components
# ---------------------------------------------------------------------------
from app.memory.memory_snapshot import MemorySnapshot
from app.memory.memory_ranker import MemoryRanker, RankingWeights
from app.memory.memory_search import MemorySearch
from app.memory.memory_compactor import MemoryCompactor
from app.memory.memory_manager import MemoryManager

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Types
    "MemoryLayerType",
    "MemoryPriority",
    "MemoryOutcome",
    "SearchStrategy",
    "MemoryRecord",
    "ScoredMemory",
    "WorkingMemorySlot",
    "WorkingMemoryState",
    "EpisodicRecord",
    "MemorySearchRequest",
    "MemorySearchResult",
    "MemorySearchResponse",
    "CompactionResult",
    "SnapshotMeta",
    "SnapshotRestoreResult",
    "RetentionPolicy",
    # Exceptions
    "MemoryError",
    "WorkingMemoryError",
    "WorkingMemoryFullError",
    "EpisodicMemoryError",
    "SemanticMemoryError",
    "MemoryNotFoundError",
    "MemoryExpiredError",
    "SnapshotError",
    "SnapshotNotFoundError",
    "SnapshotCorruptedError",
    "SearchError",
    "EmbeddingRequiredError",
    "CompactionError",
    "DuplicateMemoryError",
    # Interfaces
    "IMemoryLayer",
    "IEmbeddingGateway",
    # Base
    "BaseMemoryLayer",
    # Layers
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    # Components
    "MemorySnapshot",
    "MemoryRanker",
    "RankingWeights",
    "MemorySearch",
    "MemoryCompactor",
    "MemoryManager",
]