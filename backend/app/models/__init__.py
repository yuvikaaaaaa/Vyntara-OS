"""
Intelligence Operating System — Models Package
===============================================
This ``__init__`` serves two critical purposes:

1. **SQLAlchemy mapper registration**
   Every ORM model class must be imported before ``Base.metadata`` is used
   for table creation (``Base.metadata.create_all``) or Alembic autogenerate
   (``target_metadata = Base.metadata``).  Importing this package guarantees
   all models are registered regardless of which other module imports first.

2. **Public API**
   Re-exports every model class so application code can do::

       from app.models import User, AgentTask, KnowledgeChunk

   instead of hunting across nine model files.

Import order matters:
   Models with no FK dependencies are imported first, followed by models
   that reference them.  Python's module system handles circular imports
   transparently for ``TYPE_CHECKING`` blocks, but the import order here
   ensures the SQLAlchemy mapper sees all tables before any relationship
   resolution occurs.

Alembic ``env.py`` usage::

    from app.models import Base          # triggers full registration
    target_metadata = Base.metadata
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-export shared Base and mixins first
# ---------------------------------------------------------------------------
from app.database.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

# ---------------------------------------------------------------------------
# User / Auth domain  (no FK to other models except self-referencing)
# ---------------------------------------------------------------------------
from app.models.user import APIKey, OAuthAccount, User, UserPreference, UserSession

# ---------------------------------------------------------------------------
# Configuration (references User only)
# ---------------------------------------------------------------------------
from app.models.configuration import ModelConfiguration, PromptTemplate

# ---------------------------------------------------------------------------
# Audit (append-only; no FKs enforced at DB level)
# ---------------------------------------------------------------------------
from app.models.audit import AuditLog, EventLog

# ---------------------------------------------------------------------------
# Knowledge / RAG (references User)
# ---------------------------------------------------------------------------
from app.models.knowledge import (
    Citation,
    EmbeddingMetadata,
    KnowledgeChunk,
    KnowledgeDocument,
)

# ---------------------------------------------------------------------------
# Conversation (references User, KnowledgeDocument via Attachment)
# ---------------------------------------------------------------------------
from app.models.conversation import Attachment, Conversation, Message

# ---------------------------------------------------------------------------
# Agent (references User, Conversation)
# ---------------------------------------------------------------------------
from app.models.agent import AgentExecution, AgentTask

# ---------------------------------------------------------------------------
# Workflow (references AgentTask, User)
# ---------------------------------------------------------------------------
from app.models.workflow import PlannerState, Workflow

# ---------------------------------------------------------------------------
# Tool (references AgentTask, AgentExecution)
# ---------------------------------------------------------------------------
from app.models.tool import Tool, ToolExecution, ToolResult

# ---------------------------------------------------------------------------
# Memory (references User, Conversation, AgentTask)
# ---------------------------------------------------------------------------
from app.models.memory import (
    EpisodicMemory,
    MemorySnapshot,
    SemanticMemory,
    WorkingMemory,
)

# ---------------------------------------------------------------------------
# Evaluation (references AgentTask, Message, User)
# ---------------------------------------------------------------------------
from app.models.evaluation import Benchmark, Evaluation, Feedback

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Base / mixins
    "Base",
    "TimestampMixin",
    "AuditMixin",
    "UUIDPrimaryKeyMixin",
    # User domain
    "User",
    "UserSession",
    "APIKey",
    "UserPreference",
    "OAuthAccount",
    # Configuration
    "PromptTemplate",
    "ModelConfiguration",
    # Audit
    "AuditLog",
    "EventLog",
    # Knowledge / RAG
    "KnowledgeDocument",
    "KnowledgeChunk",
    "EmbeddingMetadata",
    "Citation",
    # Conversation
    "Conversation",
    "Message",
    "Attachment",
    # Agent
    "AgentTask",
    "AgentExecution",
    # Workflow
    "Workflow",
    "PlannerState",
    # Tool
    "Tool",
    "ToolExecution",
    "ToolResult",
    # Memory
    "WorkingMemory",
    "EpisodicMemory",
    "SemanticMemory",
    "MemorySnapshot",
    # Evaluation
    "Evaluation",
    "Benchmark",
    "Feedback",
]