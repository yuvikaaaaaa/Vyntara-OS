"""
Intelligence Operating System — Custom Exception Hierarchy
==========================================================
All application exceptions extend ``IosBaseException`` which carries:
  - ``message``:   human-readable description
  - ``code``:      machine-readable error code (e.g. "AGENT_TIMEOUT")
  - ``http_status``: default HTTP status code for this exception class
  - ``details``:   optional dict of structured context

HTTP API layer maps these to RFC 7807-style JSON Problem responses.
Domain and service layers should raise these typed exceptions rather than
generic ``Exception`` or ``ValueError``.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class IosBaseException(Exception):
    """
    Root exception for all application-specific errors.

    Attributes:
        message: Human-readable error description.
        code: Machine-readable error code (SCREAMING_SNAKE_CASE).
        http_status: Default HTTP status code when this error is surfaced via API.
        details: Optional structured context dictionary.
    """

    http_status: int = HTTPStatus.INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details: dict[str, Any] = details or {}

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.code!r}, "
            f"message={self.message!r}, "
            f"details={self.details!r})"
        )


# ---------------------------------------------------------------------------
# Authentication & Authorisation
# ---------------------------------------------------------------------------


class AuthenticationError(IosBaseException):
    """Raised when a request cannot be authenticated."""

    http_status = HTTPStatus.UNAUTHORIZED
    code = "AUTHENTICATION_FAILED"


class TokenExpiredError(AuthenticationError):
    """JWT or session token has expired."""

    code = "TOKEN_EXPIRED"


class TokenInvalidError(AuthenticationError):
    """JWT signature is invalid or malformed."""

    code = "TOKEN_INVALID"


class RefreshTokenRevokedError(AuthenticationError):
    """Refresh token has been revoked."""

    code = "REFRESH_TOKEN_REVOKED"


class OAuthError(AuthenticationError):
    """OAuth2 provider returned an error during flow."""

    code = "OAUTH_ERROR"


class AuthorizationError(IosBaseException):
    """Raised when an authenticated user lacks permission."""

    http_status = HTTPStatus.FORBIDDEN
    code = "FORBIDDEN"


class InsufficientPermissionsError(AuthorizationError):
    """User does not have the required permissions for this operation."""

    code = "INSUFFICIENT_PERMISSIONS"


class ToolPermissionDeniedError(AuthorizationError):
    """User does not have permission to execute this tool."""

    code = "TOOL_PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------


class NotFoundError(IosBaseException):
    """Generic not-found exception."""

    http_status = HTTPStatus.NOT_FOUND
    code = "NOT_FOUND"


class UserNotFoundError(NotFoundError):
    code = "USER_NOT_FOUND"


class SessionNotFoundError(NotFoundError):
    code = "SESSION_NOT_FOUND"


class TaskNotFoundError(NotFoundError):
    code = "TASK_NOT_FOUND"


class DocumentNotFoundError(NotFoundError):
    code = "DOCUMENT_NOT_FOUND"


class MemoryNotFoundError(NotFoundError):
    code = "MEMORY_NOT_FOUND"


class PromptTemplateNotFoundError(NotFoundError):
    code = "PROMPT_TEMPLATE_NOT_FOUND"


class ModelNotFoundError(NotFoundError):
    code = "MODEL_NOT_FOUND"


# ---------------------------------------------------------------------------
# Conflict
# ---------------------------------------------------------------------------


class ConflictError(IosBaseException):
    """Raised when an operation conflicts with existing state."""

    http_status = HTTPStatus.CONFLICT
    code = "CONFLICT"


class DuplicateEmailError(ConflictError):
    code = "DUPLICATE_EMAIL"


class DuplicateUsernameError(ConflictError):
    code = "DUPLICATE_USERNAME"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationError(IosBaseException):
    """Domain-level validation failure (distinct from Pydantic schema errors)."""

    http_status = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "VALIDATION_ERROR"


class InvalidFileTypeError(ValidationError):
    """Uploaded file has an unsupported extension or MIME type."""

    code = "INVALID_FILE_TYPE"


class FileTooLargeError(ValidationError):
    """Uploaded file exceeds the maximum permitted size."""

    code = "FILE_TOO_LARGE"


class InvalidQueryError(ValidationError):
    """SQL query contains invalid or prohibited syntax."""

    code = "INVALID_QUERY"


class InvalidPromptVariablesError(ValidationError):
    """Prompt template variables do not match the provided values."""

    code = "INVALID_PROMPT_VARIABLES"


# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------


class RateLimitExceededError(IosBaseException):
    """Request rate limit has been exceeded."""

    http_status = HTTPStatus.TOO_MANY_REQUESTS
    code = "RATE_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# Agent Errors
# ---------------------------------------------------------------------------


class AgentError(IosBaseException):
    """Base class for agent-related errors."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "AGENT_ERROR"


class AgentTimeoutError(AgentError):
    """Agent execution exceeded its configured timeout."""

    code = "AGENT_TIMEOUT"


class AgentMaxRetriesExceededError(AgentError):
    """Agent exceeded maximum retry attempts."""

    code = "AGENT_MAX_RETRIES_EXCEEDED"


class AgentPermissionError(AgentError):
    """Agent attempted an operation outside its permission scope."""

    http_status = HTTPStatus.FORBIDDEN
    code = "AGENT_PERMISSION_DENIED"


class AgentCapabilityError(AgentError):
    """Agent does not have the capability required for this task."""

    http_status = HTTPStatus.BAD_REQUEST
    code = "AGENT_CAPABILITY_MISSING"


class PlanGenerationError(AgentError):
    """Planner agent failed to produce a valid execution plan."""

    code = "PLAN_GENERATION_FAILED"


class PlanValidationError(AgentError):
    """Generated plan failed structural or feasibility validation."""

    code = "PLAN_VALIDATION_FAILED"


class WorkflowAbortedError(AgentError):
    """Workflow was explicitly aborted by user or system policy."""

    http_status = HTTPStatus.CONFLICT
    code = "WORKFLOW_ABORTED"


class ApprovalTimeoutError(AgentError):
    """Human-in-the-loop approval request timed out."""

    code = "APPROVAL_TIMEOUT"


class ApprovalRejectedError(AgentError):
    """Human operator rejected the pending approval request."""

    http_status = HTTPStatus.CONFLICT
    code = "APPROVAL_REJECTED"


# ---------------------------------------------------------------------------
# Tool Errors
# ---------------------------------------------------------------------------


class ToolError(IosBaseException):
    """Base class for tool execution errors."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "TOOL_ERROR"


class PythonExecutionError(ToolError):
    """Error during sandboxed Python code execution."""

    code = "PYTHON_EXECUTION_ERROR"


class PythonSandboxViolationError(ToolError):
    """Code attempted to violate sandbox constraints."""

    http_status = HTTPStatus.FORBIDDEN
    code = "PYTHON_SANDBOX_VIOLATION"


class SQLExecutionError(ToolError):
    """Error during SQL query execution."""

    code = "SQL_EXECUTION_ERROR"


class SQLWriteNotPermittedError(ToolError):
    """Write operation attempted in read-only SQL mode."""

    http_status = HTTPStatus.FORBIDDEN
    code = "SQL_WRITE_NOT_PERMITTED"


class VisionProcessingError(ToolError):
    """Error during image analysis or OCR processing."""

    code = "VISION_PROCESSING_ERROR"


class FileSystemError(ToolError):
    """Error during file system operation."""

    code = "FILESYSTEM_ERROR"


class FileSystemAccessDeniedError(ToolError):
    """File path is outside the permitted sandbox directory."""

    http_status = HTTPStatus.FORBIDDEN
    code = "FILESYSTEM_ACCESS_DENIED"


class ChartGenerationError(ToolError):
    """Error generating a chart or visualisation."""

    code = "CHART_GENERATION_ERROR"


class ReportGenerationError(ToolError):
    """Error generating a report document."""

    code = "REPORT_GENERATION_ERROR"


# ---------------------------------------------------------------------------
# Memory Errors
# ---------------------------------------------------------------------------


class MemoryError(IosBaseException):
    """Base class for memory layer errors."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "MEMORY_ERROR"


class WorkingMemoryError(MemoryError):
    """Error in Redis-backed working memory."""

    code = "WORKING_MEMORY_ERROR"


class EpisodicMemoryError(MemoryError):
    """Error in episodic memory store."""

    code = "EPISODIC_MEMORY_ERROR"


class SemanticMemoryError(MemoryError):
    """Error in Qdrant-backed semantic memory."""

    code = "SEMANTIC_MEMORY_ERROR"


class KnowledgeGraphError(MemoryError):
    """Error in Neo4j knowledge graph."""

    code = "KNOWLEDGE_GRAPH_ERROR"


class MemoryConsolidationError(MemoryError):
    """Error during scheduled memory consolidation."""

    code = "MEMORY_CONSOLIDATION_ERROR"


# ---------------------------------------------------------------------------
# RAG / Retrieval Errors
# ---------------------------------------------------------------------------


class RAGError(IosBaseException):
    """Base class for RAG pipeline errors."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "RAG_ERROR"


class DocumentIngestionError(RAGError):
    """Error during document parsing or pre-processing."""

    code = "DOCUMENT_INGESTION_ERROR"


class ChunkingError(RAGError):
    """Error during document chunking."""

    code = "CHUNKING_ERROR"


class EmbeddingError(RAGError):
    """Error generating embeddings."""

    code = "EMBEDDING_ERROR"


class RetrievalError(RAGError):
    """Error during hybrid retrieval."""

    code = "RETRIEVAL_ERROR"


class RerankingError(RAGError):
    """Error during cross-encoder re-ranking."""

    code = "RERANKING_ERROR"


class HallucinationDetectionError(RAGError):
    """Error during NLI-based hallucination detection."""

    code = "HALLUCINATION_DETECTION_ERROR"


# ---------------------------------------------------------------------------
# Model / LLM Errors
# ---------------------------------------------------------------------------


class ModelError(IosBaseException):
    """Base class for model-layer errors."""

    http_status = HTTPStatus.SERVICE_UNAVAILABLE
    code = "MODEL_ERROR"


class ModelUnavailableError(ModelError):
    """Target model is not available (Ollama offline, not loaded, etc.)."""

    code = "MODEL_UNAVAILABLE"


class ModelRoutingError(ModelError):
    """Model router could not find a suitable model for the task."""

    code = "MODEL_ROUTING_FAILED"


class ModelContextLengthExceededError(ModelError):
    """Input exceeds the model's maximum context window."""

    http_status = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "MODEL_CONTEXT_EXCEEDED"


class EmbeddingModelError(ModelError):
    """Error loading or running the embedding model."""

    code = "EMBEDDING_MODEL_ERROR"


# ---------------------------------------------------------------------------
# Infrastructure / External Service Errors
# ---------------------------------------------------------------------------


class InfrastructureError(IosBaseException):
    """Base class for infrastructure/external-service errors."""

    http_status = HTTPStatus.SERVICE_UNAVAILABLE
    code = "INFRASTRUCTURE_ERROR"


class DatabaseConnectionError(InfrastructureError):
    """Cannot connect to PostgreSQL."""

    code = "DATABASE_CONNECTION_ERROR"


class DatabaseQueryError(InfrastructureError):
    """Database query failed at the driver level."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "DATABASE_QUERY_ERROR"


class RedisConnectionError(InfrastructureError):
    """Cannot connect to Redis."""

    code = "REDIS_CONNECTION_ERROR"


class QdrantConnectionError(InfrastructureError):
    """Cannot connect to Qdrant."""

    code = "QDRANT_CONNECTION_ERROR"


class Neo4jConnectionError(InfrastructureError):
    """Cannot connect to Neo4j."""

    code = "NEO4J_CONNECTION_ERROR"


class ExternalServiceError(InfrastructureError):
    """Generic external HTTP service error."""

    code = "EXTERNAL_SERVICE_ERROR"


# ---------------------------------------------------------------------------
# WebSocket Errors
# ---------------------------------------------------------------------------


class WebSocketError(IosBaseException):
    """Base class for WebSocket communication errors."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "WEBSOCKET_ERROR"


class WebSocketAuthError(WebSocketError):
    """WebSocket connection rejected due to authentication failure."""

    http_status = HTTPStatus.UNAUTHORIZED
    code = "WEBSOCKET_AUTH_ERROR"


class WebSocketConnectionError(WebSocketError):
    """WebSocket connection dropped unexpectedly."""

    code = "WEBSOCKET_CONNECTION_ERROR"


# ---------------------------------------------------------------------------
# Evaluation Errors
# ---------------------------------------------------------------------------


class EvaluationError(IosBaseException):
    """Base class for evaluation pipeline errors."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "EVALUATION_ERROR"


class MetricComputationError(EvaluationError):
    """Error computing an evaluation metric."""

    code = "METRIC_COMPUTATION_ERROR"


class MLflowTrackingError(EvaluationError):
    """Error logging to MLflow."""

    code = "MLFLOW_TRACKING_ERROR"


# ---------------------------------------------------------------------------
# Configuration Errors
# ---------------------------------------------------------------------------


class ConfigurationError(IosBaseException):
    """Raised when application configuration is invalid or incomplete."""

    http_status = HTTPStatus.INTERNAL_SERVER_ERROR
    code = "CONFIGURATION_ERROR"
