"""
Intelligence Operating System — Application Enumerations
=========================================================
All ``Enum`` types used across the application are defined here to prevent
string drift and enable IDE completion / static analysis.

Rules:
  - All enums derive from ``str, Enum`` to be JSON-serialisable by default.
  - Values match the string constants in ``constants.py`` where applicable.
  - Pydantic models should annotate fields as the Enum type, not ``str``.
"""

from __future__ import annotations

from enum import Enum

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"


# ---------------------------------------------------------------------------
# User / Auth
# ---------------------------------------------------------------------------


class UserRole(str, Enum):
    """
    Hierarchy: ADMIN > OPERATOR > ANALYST > VIEWER > API_CLIENT.
    Higher roles inherit all permissions of lower roles.
    """

    ADMIN = "admin"
    OPERATOR = "operator"
    ANALYST = "analyst"
    VIEWER = "viewer"
    API_CLIENT = "api_client"


class OAuthProvider(str, Enum):
    GOOGLE = "google"
    GITHUB = "github"


# ---------------------------------------------------------------------------
# Task Lifecycle
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """Ordered task lifecycle states."""

    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TaskPriority(int, Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AgentType(str, Enum):
    """Canonical identifiers for all agent types in the system."""

    PLANNER = "planner"
    SUPERVISOR = "supervisor"
    RESEARCH = "research"
    CODING = "coding"
    VISION = "vision"
    SQL = "sql"
    ML = "ml"
    MEMORY = "memory"
    REFLECTION = "reflection"
    DEBATE = "debate"
    EVALUATION = "evaluation"
    REPORT = "report"


class AgentStatus(str, Enum):
    """Runtime status of an individual agent execution."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"  # Waiting for a tool or sub-agent
    REFLECTING = "reflecting"
    COMPLETE = "complete"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    RETRYING = "retrying"


class StepStatus(str, Enum):
    """Status of an individual task step within an execution plan."""

    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRYING = "retrying"


class WorkflowType(str, Enum):
    """High-level workflow category used to select the appropriate graph."""

    RESEARCH = "research"
    CODING = "coding"
    ANALYSIS = "analysis"
    RAG = "rag"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


class MemoryType(str, Enum):
    """Memory layer identifiers."""

    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    KNOWLEDGE_GRAPH = "knowledge_graph"


class MemoryOutcome(str, Enum):
    """Outcome tag stored with episodic memory records."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


class MemorySourceType(str, Enum):
    """Source of a semantic memory record."""

    CONVERSATION = "conversation"
    DOCUMENT = "document"
    EXPERIENCE = "experience"
    MANUAL = "manual"


# ---------------------------------------------------------------------------
# RAG / Documents
# ---------------------------------------------------------------------------


class DocumentStatus(str, Enum):
    """Ingestion pipeline status for a document."""

    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    DELETED = "deleted"


class FileType(str, Enum):
    """Supported document file types."""

    PDF = "pdf"
    DOCX = "docx"
    TXT = "txt"
    MD = "md"
    HTML = "html"
    CSV = "csv"


class ChunkingStrategy(str, Enum):
    """Chunking algorithm selection."""

    RECURSIVE = "recursive"
    SEMANTIC = "semantic"
    SLIDING_WINDOW = "sliding_window"


class RetrievalStrategy(str, Enum):
    """RAG retrieval strategy."""

    VECTOR_ONLY = "vector_only"
    BM25_ONLY = "bm25_only"
    HYBRID = "hybrid"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ModelTier(str, Enum):
    """Model size tiers for routing decisions."""

    SMALL = "small"  # Fast, low capability
    MEDIUM = "medium"  # Balanced
    LARGE = "large"  # Maximum capability
    CODE = "code"  # Code-specialised
    VISION = "vision"  # Vision-capable


class ModelProvider(str, Enum):
    """Model serving provider."""

    OLLAMA = "ollama"
    HUGGINGFACE = "huggingface"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class ToolName(str, Enum):
    """Canonical tool identifiers used in the ToolRegistry."""

    PYTHON_EXECUTOR = "python_executor"
    SQL_EXECUTOR = "sql_executor"
    VISION = "vision"
    FILESYSTEM = "filesystem"
    CHART_GENERATOR = "chart_generator"
    REPORT_GENERATOR = "report_generator"


class ToolStatus(str, Enum):
    """Runtime status of a tool execution call."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    PERMISSION_DENIED = "permission_denied"


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


class WebSocketEventType(str, Enum):
    """All possible event types emitted over the streaming WebSocket channel."""

    TOKEN = "TOKEN"
    AGENT_START = "AGENT_START"
    AGENT_COMPLETE = "AGENT_COMPLETE"
    TOOL_CALL = "TOOL_CALL"
    TOOL_RESULT = "TOOL_RESULT"
    MEMORY_READ = "MEMORY_READ"
    RETRIEVAL = "RETRIEVAL"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ERROR = "ERROR"
    DONE = "DONE"
    HEARTBEAT = "HEARTBEAT"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class EvaluationRunType(str, Enum):
    """Type of evaluation pipeline run."""

    RAG = "rag"
    AGENT = "agent"
    HALLUCINATION = "hallucination"
    CONFIDENCE = "confidence"
    FULL = "full"


class EvaluationStatus(str, Enum):
    """Status of an evaluation pipeline run."""

    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# ML Pipeline
# ---------------------------------------------------------------------------


class MLModelFamily(str, Enum):
    """ML model family / algorithm type."""

    SKLEARN = "sklearn"
    XGBOOST = "xgboost"
    CATBOOST = "catboost"
    LIGHTGBM = "lightgbm"
    PYTORCH = "pytorch"


class MLTaskType(str, Enum):
    """Supervised learning task type."""

    CLASSIFICATION = "classification"
    REGRESSION = "regression"
    CLUSTERING = "clustering"
    RANKING = "ranking"


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class SessionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class MessageRole(str, Enum):
    """Message roles in a conversation thread."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"
