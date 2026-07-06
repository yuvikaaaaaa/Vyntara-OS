"""IOS AI Core — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class AIProviderError(IosBaseException):
    """Base for all AI provider errors."""
    http_status = 502
    code = "AI_PROVIDER_ERROR"


class ProviderUnavailableError(AIProviderError):
    """Provider is offline or returning 5xx errors."""
    code = "PROVIDER_UNAVAILABLE"


class ModelNotAvailableError(AIProviderError):
    """Requested model is not loaded or not found on the provider."""
    code = "MODEL_NOT_AVAILABLE"


class ProviderTimeoutError(AIProviderError):
    """Provider did not respond within the configured timeout."""
    code = "PROVIDER_TIMEOUT"


class ContextLengthExceededError(AIProviderError):
    """Input exceeds the model's maximum context window."""
    http_status = 422
    code = "CONTEXT_LENGTH_EXCEEDED"


class ProviderRateLimitError(AIProviderError):
    """Provider rate limit reached."""
    http_status = 429
    code = "PROVIDER_RATE_LIMIT"


class ProviderAuthError(AIProviderError):
    """Invalid API key or missing credentials for the provider."""
    http_status = 401
    code = "PROVIDER_AUTH_ERROR"


class StreamingError(AIProviderError):
    """Error during streaming response consumption."""
    code = "STREAMING_ERROR"


class RoutingError(IosBaseException):
    """Model router could not select a suitable provider/model."""
    http_status = 503
    code = "ROUTING_ERROR"


class NoCapableProviderError(RoutingError):
    """No registered provider can satisfy the required capabilities."""
    code = "NO_CAPABLE_PROVIDER"


class AllProvidersUnhealthyError(RoutingError):
    """Every provider failed the health check."""
    code = "ALL_PROVIDERS_UNHEALTHY"


class EmbeddingError(AIProviderError):
    """Error generating embeddings."""
    code = "EMBEDDING_ERROR"