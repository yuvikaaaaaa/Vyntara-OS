"""
Intelligence Operating System — System-Wide Constants
======================================================
All immutable, project-wide constants are defined here.
No magic strings or magic numbers should exist anywhere else in the codebase.
Import from this module; never hard-code values inline.
"""

from typing import Final

# ---------------------------------------------------------------------------
# Application Identity
# ---------------------------------------------------------------------------

APP_NAME: Final[str] = "Intelligence Operating System"
APP_CODENAME: Final[str] = "IOS"
APP_VERSION: Final[str] = "1.0.0"
APP_DESCRIPTION: Final[str] = (
    "Production-grade AI orchestration platform — Linux for Intelligence."
)

# ---------------------------------------------------------------------------
# API Versioning
# ---------------------------------------------------------------------------

API_V1_PREFIX: Final[str] = "/api/v1"
OPENAPI_URL: Final[str] = "/api/openapi.json"
DOCS_URL: Final[str] = "/api/docs"
REDOC_URL: Final[str] = "/api/redoc"

# ---------------------------------------------------------------------------
# Authentication & Security
# ---------------------------------------------------------------------------

# JWT
JWT_ALGORITHM: Final[str] = "HS256"
JWT_TOKEN_TYPE: Final[str] = "bearer"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES: Final[int] = 15
JWT_REFRESH_TOKEN_EXPIRE_DAYS: Final[int] = 7
JWT_REFRESH_TOKEN_COOKIE_NAME: Final[str] = "ios_refresh_token"

# API Key prefix stored in DB (first 8 chars visible for identification)
API_KEY_PREFIX_LENGTH: Final[int] = 8
API_KEY_TOTAL_LENGTH: Final[int] = 64

# Password hashing
BCRYPT_ROUNDS: Final[int] = 12

# OAuth state parameter length (CSRF protection)
OAUTH_STATE_LENGTH: Final[int] = 32

# ---------------------------------------------------------------------------
# Rate Limiting
# ---------------------------------------------------------------------------

# Sliding-window counters (requests / window_seconds)
RATE_LIMIT_DEFAULT_REQUESTS: Final[int] = 100
RATE_LIMIT_DEFAULT_WINDOW_SECONDS: Final[int] = 60

RATE_LIMIT_AUTH_REQUESTS: Final[int] = 10
RATE_LIMIT_AUTH_WINDOW_SECONDS: Final[int] = 60

RATE_LIMIT_TASK_REQUESTS: Final[int] = 20
RATE_LIMIT_TASK_WINDOW_SECONDS: Final[int] = 60

# ---------------------------------------------------------------------------
# Agent Configuration
# ---------------------------------------------------------------------------

# Maximum retry attempts per agent step before marking failed
AGENT_MAX_RETRIES: Final[int] = 3

# Default agent execution timeout in seconds
AGENT_DEFAULT_TIMEOUT_SECONDS: Final[int] = 120

# Reflection quality thresholds (0.0 – 1.0)
REFLECTION_PASS_THRESHOLD: Final[float] = 0.70
REFLECTION_DEBATE_THRESHOLD: Final[float] = 0.50

# Maximum number of parallel agent steps
AGENT_MAX_PARALLEL_STEPS: Final[int] = 5

# ---------------------------------------------------------------------------
# Memory Configuration
# ---------------------------------------------------------------------------

# Working memory (Redis)
WORKING_MEMORY_TTL_SECONDS: Final[int] = 86_400  # 24 hours
WORKING_MEMORY_MAX_TOKENS: Final[int] = 32_768

# Episodic memory
EPISODIC_MEMORY_MAX_RESULTS: Final[int] = 5

# Semantic memory
SEMANTIC_MEMORY_MAX_RESULTS: Final[int] = 10
SEMANTIC_MEMORY_IMPORTANCE_DECAY_DAYS: Final[int] = 90

# Knowledge graph
KG_MAX_TRAVERSAL_DEPTH: Final[int] = 3
KG_MAX_NEIGHBOUR_NODES: Final[int] = 50

# Conversation sliding window (in messages)
CONVERSATION_WINDOW_SIZE: Final[int] = 50
CONVERSATION_SUMMARY_TRIGGER: Final[int] = (
    40  # Summarise when this many messages reached
)

# ---------------------------------------------------------------------------
# RAG Pipeline
# ---------------------------------------------------------------------------

# Chunking defaults
CHUNK_SIZE_DEFAULT: Final[int] = 512
CHUNK_OVERLAP_DEFAULT: Final[int] = 64
CHUNK_MIN_LENGTH: Final[int] = 50

# Retrieval
RAG_TOP_K_VECTOR: Final[int] = 20  # candidates from vector search
RAG_TOP_K_BM25: Final[int] = 20  # candidates from BM25
RAG_TOP_K_RERANKED: Final[int] = 5  # after cross-encoder re-ranking
RAG_CONFIDENCE_THRESHOLD: Final[float] = 0.35  # minimum confidence to include chunk

# Hallucination detection
HALLUCINATION_NLI_THRESHOLD: Final[float] = 0.50

# ---------------------------------------------------------------------------
# Embedding Models
# ---------------------------------------------------------------------------

EMBEDDING_MODEL_DEFAULT: Final[str] = "BAAI/bge-large-en-v1.5"
EMBEDDING_MODEL_DIMENSION_DEFAULT: Final[int] = 1024
EMBEDDING_BATCH_SIZE: Final[int] = 64

RERANKER_MODEL_DEFAULT: Final[str] = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# ---------------------------------------------------------------------------
# Model Routing
# ---------------------------------------------------------------------------

# Ollama model identifiers (must match models available in the Ollama instance)
OLLAMA_MODEL_LARGE: Final[str] = "llama3.1:70b"
OLLAMA_MODEL_MEDIUM: Final[str] = "llama3.1:8b"
OLLAMA_MODEL_CODE: Final[str] = "deepseek-coder-v2:16b"
OLLAMA_MODEL_VISION: Final[str] = "llava:13b"
OLLAMA_MODEL_SMALL: Final[str] = "llama3.2:3b"

# Context window sizes (in tokens) for each model
OLLAMA_CONTEXT_LARGE: Final[int] = 128_000
OLLAMA_CONTEXT_MEDIUM: Final[int] = 128_000
OLLAMA_CONTEXT_CODE: Final[int] = 65_536
OLLAMA_CONTEXT_SMALL: Final[int] = 8_192

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

WS_HEARTBEAT_INTERVAL_SECONDS: Final[int] = 30
WS_MESSAGE_MAX_SIZE_BYTES: Final[int] = 1_048_576  # 1 MB
WS_RECONNECT_DELAY_SECONDS: Final[int] = 5

# WebSocket event types (string literals for fast path comparison)
WS_EVENT_TOKEN: Final[str] = "TOKEN"
WS_EVENT_AGENT_START: Final[str] = "AGENT_START"
WS_EVENT_AGENT_COMPLETE: Final[str] = "AGENT_COMPLETE"
WS_EVENT_TOOL_CALL: Final[str] = "TOOL_CALL"
WS_EVENT_TOOL_RESULT: Final[str] = "TOOL_RESULT"
WS_EVENT_MEMORY_READ: Final[str] = "MEMORY_READ"
WS_EVENT_RETRIEVAL: Final[str] = "RETRIEVAL"
WS_EVENT_APPROVAL_REQUIRED: Final[str] = "APPROVAL_REQUIRED"
WS_EVENT_ERROR: Final[str] = "ERROR"
WS_EVENT_DONE: Final[str] = "DONE"
WS_EVENT_HEARTBEAT: Final[str] = "HEARTBEAT"

# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

# LLM response semantic cache TTL
CACHE_LLM_RESPONSE_TTL_SECONDS: Final[int] = 3_600  # 1 hour
# Embedding cache TTL
CACHE_EMBEDDING_TTL_SECONDS: Final[int] = 604_800  # 7 days
# Session state cache TTL
CACHE_SESSION_STATE_TTL_SECONDS: Final[int] = 3_600  # 1 hour

# ---------------------------------------------------------------------------
# Redis Key Namespace
# ---------------------------------------------------------------------------

REDIS_NS_WORKING_MEMORY: Final[str] = "ios:session:working_memory"
REDIS_NS_SESSION_STATE: Final[str] = "ios:session:state"
REDIS_NS_LLM_CACHE: Final[str] = "ios:cache:llm"
REDIS_NS_EMBED_CACHE: Final[str] = "ios:cache:embedding"
REDIS_NS_RATE_LIMIT: Final[str] = "ios:rate_limit"
REDIS_NS_AGENT_LOCK: Final[str] = "ios:lock:agent"
REDIS_NS_APPROVAL: Final[str] = "ios:approval:pending"
REDIS_NS_STREAM: Final[str] = "ios:stream:task"

# ---------------------------------------------------------------------------
# Qdrant Collections
# ---------------------------------------------------------------------------

QDRANT_COLLECTION_DOCUMENT_CHUNKS: Final[str] = "document_chunks"
QDRANT_COLLECTION_SEMANTIC_MEMORIES: Final[str] = "semantic_memories"
QDRANT_COLLECTION_EPISODIC_MEMORIES: Final[str] = "episodic_memories"
QDRANT_COLLECTION_ENTITY_EMBEDDINGS: Final[str] = "entity_embeddings"

# ---------------------------------------------------------------------------
# Tool Permissions
# ---------------------------------------------------------------------------

TOOL_PYTHON_EXECUTE: Final[str] = "tools:python:execute"
TOOL_SQL_READ: Final[str] = "tools:sql:read"
TOOL_SQL_WRITE: Final[str] = "tools:sql:write"
TOOL_VISION_ANALYZE: Final[str] = "tools:vision:analyze"
TOOL_FS_READ: Final[str] = "tools:filesystem:read"
TOOL_FS_WRITE: Final[str] = "tools:filesystem:write"
TOOL_CHART_GENERATE: Final[str] = "tools:chart:generate"
TOOL_REPORT_GENERATE: Final[str] = "tools:report:generate"

# ---------------------------------------------------------------------------
# User Roles
# ---------------------------------------------------------------------------

ROLE_ADMIN: Final[str] = "admin"
ROLE_OPERATOR: Final[str] = "operator"
ROLE_ANALYST: Final[str] = "analyst"
ROLE_VIEWER: Final[str] = "viewer"
ROLE_API_CLIENT: Final[str] = "api_client"

# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

PAGINATION_DEFAULT_PAGE: Final[int] = 1
PAGINATION_DEFAULT_PAGE_SIZE: Final[int] = 20
PAGINATION_MAX_PAGE_SIZE: Final[int] = 100

# ---------------------------------------------------------------------------
# File Upload
# ---------------------------------------------------------------------------

UPLOAD_MAX_SIZE_BYTES: Final[int] = 104_857_600  # 100 MB
UPLOAD_ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".pdf", ".docx", ".txt", ".md", ".html", ".csv"}
)
UPLOAD_STORAGE_PATH: Final[str] = "/data/uploads"

# ---------------------------------------------------------------------------
# Task Lifecycle
# ---------------------------------------------------------------------------

TASK_STATUS_PENDING: Final[str] = "pending"
TASK_STATUS_PLANNING: Final[str] = "planning"
TASK_STATUS_EXECUTING: Final[str] = "executing"
TASK_STATUS_AWAITING_APPROVAL: Final[str] = "awaiting_approval"
TASK_STATUS_COMPLETE: Final[str] = "complete"
TASK_STATUS_FAILED: Final[str] = "failed"
TASK_STATUS_CANCELLED: Final[str] = "cancelled"

# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------

OTEL_SERVICE_NAME: Final[str] = "ios-backend"
OTEL_SPAN_AGENT_EXECUTE: Final[str] = "agent.execute"
OTEL_SPAN_RAG_RETRIEVE: Final[str] = "rag.retrieve"
OTEL_SPAN_MEMORY_READ: Final[str] = "memory.read"
OTEL_SPAN_MEMORY_WRITE: Final[str] = "memory.write"
OTEL_SPAN_LLM_GENERATE: Final[str] = "llm.generate"
OTEL_SPAN_TOOL_EXECUTE: Final[str] = "tool.execute"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
LOG_CORRELATION_ID_HEADER: Final[str] = "X-Correlation-ID"

# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

HEALTH_CHECK_PATH: Final[str] = "/health"
READINESS_CHECK_PATH: Final[str] = "/health/ready"
LIVENESS_CHECK_PATH: Final[str] = "/health/live"
