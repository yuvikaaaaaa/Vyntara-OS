"""IOS AI Core — Public API.

The AI Core is the ONLY layer that communicates with LLM providers.
Business Services communicate with AI Core only; never with providers directly.

Usage::

    from app.ai_core import ModelRouter, OllamaProvider
    from app.ai_core import ChatRequest, ChatResponse, RoutingContext
    from app.ai_core import ModelCapability, ModelTierLabel
"""

# Types
from app.ai_core.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatStream,
    EmbeddingRequest,
    EmbeddingResponse,
    GenerationConfig,
    ModelCapability,
    ModelInfo,
    ModelTierLabel,
    ProviderHealth,
    RoutingContext,
    StreamChunk,
    TokenUsage,
)

# Exceptions
from app.ai_core.exceptions import (
    AIProviderError,
    AllProvidersUnhealthyError,
    ContextLengthExceededError,
    EmbeddingError,
    ModelNotAvailableError,
    NoCapableProviderError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RoutingError,
    StreamingError,
)

# Interfaces
from app.ai_core.interfaces import IEmbeddingProvider, ILanguageModelProvider

# Providers
from app.ai_core.providers.base_provider import BaseEmbeddingProvider, BaseProvider
from app.ai_core.providers.ollama_provider import OllamaProvider
"""IOS AI Core — Providers sub-package."""
from app.ai_core.providers.base_provider import BaseEmbeddingProvider, BaseProvider
from app.ai_core.providers.ollama_provider import OllamaProvider

__all__ = ["BaseProvider", "BaseEmbeddingProvider", "OllamaProvider"]

# Router
from app.ai_core.router.model_router import ModelRouter, ProviderRegistry

__all__ = [
    # Types
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ChatStream",
    "EmbeddingRequest",
    "EmbeddingResponse",
    "GenerationConfig",
    "ModelCapability",
    "ModelInfo",
    "ModelTierLabel",
    "ProviderHealth",
    "RoutingContext",
    "StreamChunk",
    "TokenUsage",
    # Exceptions
    "AIProviderError",
    "AllProvidersUnhealthyError",
    "ContextLengthExceededError",
    "EmbeddingError",
    "ModelNotAvailableError",
    "NoCapableProviderError",
    "ProviderAuthError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "RoutingError",
    "StreamingError",
    # Interfaces
    "ILanguageModelProvider",
    "IEmbeddingProvider",
    # Providers
    "BaseProvider",
    "BaseEmbeddingProvider",
    "OllamaProvider",
    # Router
    "ModelRouter",
    "ProviderRegistry",
]