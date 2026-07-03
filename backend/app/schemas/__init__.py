"""IOS — Schemas Package.

Re-exports every public schema class.  Import from here::

    from app.schemas import UserRead, TaskSubmit, Page, ProblemDetail
"""
from __future__ import annotations

# Base
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema

# Common value objects
from app.schemas.common import (
    EmailStr,
    HealthStatus,
    IDResponse,
    NonEmptyStr,
    PasswordStr,
    ShortStr,
    SuccessResponse,
    TagList,
    TokenPair,
    UsernameStr,
)

# Pagination
from app.schemas.pagination import Page, PageMeta, PaginationParams

# Response / error
from app.schemas.response import ApiResponse, ProblemDetail, ValidationErrorDetail, ValidationProblem

# User / Auth
from app.schemas.user import (
    AdminUserUpdate,
    APIKeyCreate,
    APIKeyCreated,
    APIKeyRead,
    LoginRequest,
    OAuthAccountRead,
    OAuthCallbackRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    UserCreate,
    UserPreferenceRead,
    UserPreferenceSet,
    UserRead,
    UserSessionRead,
    UserSummary,
    UserUpdate,
)

# Conversation
from app.schemas.conversation import (
    AttachmentRead,
    AttachmentUploadResponse,
    ConversationCreate,
    ConversationRead,
    ConversationSummary,
    ConversationUpdate,
    MessageCreate,
    MessageRead,
    MessageSummary,
)

# Knowledge / RAG
from app.schemas.knowledge import (
    ChunkSearchResult,
    CitationCreate,
    CitationRead,
    DocumentIngestRequest,
    DocumentSummary,
    DocumentUpdate,
    EmbeddingMetadataRead,
    KnowledgeChunkRead,
    KnowledgeDocumentRead,
)

# Memory
from app.schemas.memory import (
    EpisodicMemoryCreate,
    EpisodicMemoryRead,
    EpisodicMemorySummary,
    MemorySearchRequest,
    MemorySearchResult,
    MemorySnapshotRead,
    SemanticMemoryCreate,
    SemanticMemoryRead,
    SemanticMemorySummary,
    SemanticMemoryUpdate,
    WorkingMemoryRead,
    WorkingMemoryUpdate,
)

# Agent
from app.schemas.agent import (
    AgentExecutionRead,
    AgentExecutionSummary,
    AgentTaskRead,
    AgentTaskSummary,
    ApprovalRequest,
    TaskSubmit,
    TaskSubmitResponse,
    TaskUpdate,
)

# Workflow
from app.schemas.workflow import (
    DAGEdge,
    DAGNode,
    ExecutionDAG,
    PlannerStateFull,
    PlannerStateRead,
    WorkflowApproval,
    WorkflowRead,
    WorkflowSummary,
)

# Tool
from app.schemas.tool import (
    ToolCreate,
    ToolExecutionRead,
    ToolExecutionSummary,
    ToolInvokeRequest,
    ToolRead,
    ToolResultRead,
    ToolSummary,
    ToolUpdate,
)

# Evaluation
from app.schemas.evaluation import (
    BenchmarkCreate,
    BenchmarkRead,
    BenchmarkSummary,
    BenchmarkUpdate,
    EvaluationCreate,
    EvaluationMetricsUpdate,
    EvaluationRead,
    EvaluationSummary,
    FeedbackCreate,
    FeedbackRead,
    FeedbackReview,
)

# Audit
from app.schemas.audit import (
    AuditLogFilter,
    AuditLogRead,
    EventLogCreate,
    EventLogFilter,
    EventLogRead,
)

# Configuration
from app.schemas.configuration import (
    ModelConfigCreate,
    ModelConfigRead,
    ModelConfigSummary,
    ModelConfigUpdate,
    PromptRenderRequest,
    PromptRenderResponse,
    PromptTemplateCreate,
    PromptTemplateRead,
    PromptTemplateSummary,
    PromptTemplateUpdate,
)

__all__ = [
    # Base
    "AppModel", "OrmModel", "TimestampedSchema", "AuditedSchema",
    # Common
    "NonEmptyStr", "ShortStr", "EmailStr", "UsernameStr", "PasswordStr",
    "TagList", "HealthStatus", "TokenPair", "IDResponse", "SuccessResponse",
    # Pagination
    "PaginationParams", "PageMeta", "Page",
    # Response
    "ProblemDetail", "ValidationErrorDetail", "ValidationProblem", "ApiResponse",
    # User
    "UserCreate", "UserUpdate", "UserRead", "UserSummary", "AdminUserUpdate",
    "LoginRequest", "RegisterRequest", "PasswordChangeRequest",
    "RefreshRequest", "OAuthCallbackRequest",
    "UserSessionRead",
    "APIKeyCreate", "APIKeyRead", "APIKeyCreated",
    "UserPreferenceSet", "UserPreferenceRead",
    "OAuthAccountRead",
    # Conversation
    "ConversationCreate", "ConversationUpdate", "ConversationRead", "ConversationSummary",
    "MessageCreate", "MessageRead", "MessageSummary",
    "AttachmentRead", "AttachmentUploadResponse",
    # Knowledge
    "DocumentIngestRequest", "DocumentUpdate", "KnowledgeDocumentRead", "DocumentSummary",
    "KnowledgeChunkRead", "ChunkSearchResult",
    "EmbeddingMetadataRead",
    "CitationCreate", "CitationRead",
    # Memory
    "WorkingMemoryRead", "WorkingMemoryUpdate",
    "EpisodicMemoryCreate", "EpisodicMemoryRead", "EpisodicMemorySummary",
    "SemanticMemoryCreate", "SemanticMemoryUpdate", "SemanticMemoryRead", "SemanticMemorySummary",
    "MemorySnapshotRead",
    "MemorySearchRequest", "MemorySearchResult",
    # Agent
    "TaskSubmit", "TaskUpdate", "AgentTaskRead", "AgentTaskSummary",
    "TaskSubmitResponse", "ApprovalRequest",
    "AgentExecutionRead", "AgentExecutionSummary",
    # Workflow
    "DAGNode", "DAGEdge", "ExecutionDAG",
    "WorkflowRead", "WorkflowSummary", "WorkflowApproval",
    "PlannerStateRead", "PlannerStateFull",
    # Tool
    "ToolCreate", "ToolUpdate", "ToolRead", "ToolSummary", "ToolInvokeRequest",
    "ToolExecutionRead", "ToolExecutionSummary",
    "ToolResultRead",
    # Evaluation
    "EvaluationCreate", "EvaluationRead", "EvaluationSummary", "EvaluationMetricsUpdate",
    "BenchmarkCreate", "BenchmarkUpdate", "BenchmarkRead", "BenchmarkSummary",
    "FeedbackCreate", "FeedbackRead", "FeedbackReview",
    # Audit
    "AuditLogRead", "AuditLogFilter",
    "EventLogCreate", "EventLogRead", "EventLogFilter",
    # Configuration
    "PromptTemplateCreate", "PromptTemplateUpdate", "PromptTemplateRead", "PromptTemplateSummary",
    "PromptRenderRequest", "PromptRenderResponse",
    "ModelConfigCreate", "ModelConfigUpdate", "ModelConfigRead", "ModelConfigSummary",
]