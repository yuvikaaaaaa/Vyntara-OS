"""IOS AI Core — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator


# ---------------------------------------------------------------------------
# Capability flags
# ---------------------------------------------------------------------------


class ModelCapability(str, Enum):
    TEXT_GENERATION = "text_generation"
    CODE_GENERATION = "code_generation"
    VISION = "vision"
    EMBEDDING = "embedding"
    FUNCTION_CALLING = "function_calling"
    JSON_MODE = "json_mode"
    LONG_CONTEXT = "long_context"
    MATH = "math"


class ModelTierLabel(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    CODE = "code"
    VISION = "vision"


# ---------------------------------------------------------------------------
# Message / conversation
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """A single turn in a chat conversation."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None
    tool_call_id: str | None = None


# ---------------------------------------------------------------------------
# Generation parameters
# ---------------------------------------------------------------------------


@dataclass
class GenerationConfig:
    """Sampling and generation parameters."""

    temperature: float = 0.7
    top_p: float = 0.9
    top_k: int | None = None
    max_tokens: int | None = None
    stop: list[str] = field(default_factory=list)
    repeat_penalty: float | None = None
    seed: int | None = None
    stream: bool = False


# ---------------------------------------------------------------------------
# Chat requests / responses
# ---------------------------------------------------------------------------


@dataclass
class ChatRequest:
    """Provider-independent chat completion request."""

    messages: list[ChatMessage]
    model_id: str
    config: GenerationConfig = field(default_factory=GenerationConfig)
    system_prompt: str | None = None
    context_id: str | None = None          # for request-level tracing
    timeout_seconds: float = 120.0


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass
class ChatResponse:
    """Provider-independent chat completion response."""

    content: str
    model_id: str
    usage: TokenUsage
    finish_reason: str                      # "stop" | "length" | "tool_calls"
    provider: str
    latency_ms: int
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    """A single streaming token chunk."""

    content: str
    is_final: bool = False
    finish_reason: str | None = None
    usage: TokenUsage | None = None        # only on final chunk


# Type alias — providers yield this from stream_chat()
ChatStream = AsyncIterator[StreamChunk]


# ---------------------------------------------------------------------------
# Embedding requests / responses
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingRequest:
    texts: list[str]
    model_id: str
    timeout_seconds: float = 60.0


@dataclass
class EmbeddingResponse:
    embeddings: list[list[float]]
    model_id: str
    dimension: int
    provider: str
    latency_ms: int
    token_count: int | None = None


# ---------------------------------------------------------------------------
# Model / provider metadata
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Describes a model available from a provider."""

    model_id: str
    display_name: str
    provider: str
    tier: ModelTierLabel
    capabilities: list[ModelCapability]
    context_window_tokens: int
    max_output_tokens: int | None = None
    is_available: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderHealth:
    provider: str
    is_healthy: bool
    latency_ms: float | None = None
    error: str | None = None
    available_models: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Routing hint (passed by ModelRouter to provider selection)
# ---------------------------------------------------------------------------


@dataclass
class RoutingContext:
    """Hints that guide model selection within the router."""

    required_capabilities: list[ModelCapability] = field(default_factory=list)
    preferred_tier: ModelTierLabel | None = None
    max_latency_ms: int | None = None
    min_context_tokens: int | None = None
    task_hint: str | None = None           # "code", "analysis", "creative", etc.