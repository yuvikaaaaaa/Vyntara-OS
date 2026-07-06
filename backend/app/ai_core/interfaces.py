"""IOS AI Core — Provider Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.ai_core.types import (
    ChatRequest,
    ChatResponse,
    ChatStream,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelInfo,
    ProviderHealth,
    RoutingContext,
)


class ILanguageModelProvider(ABC):
    """
    Contract every LLM provider must satisfy.

    Adding a new provider (OpenAI, Anthropic, Gemini …) requires:
      1. Subclass this interface.
      2. Register via ProviderRegistry.
      3. Zero changes anywhere else.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable lowercase provider identifier, e.g. 'ollama', 'openai'."""

    @property
    @abstractmethod
    def supported_models(self) -> list[ModelInfo]:
        """Return metadata for every model this provider knows about."""

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """
        Execute a non-streaming chat completion.

        Raises:
            ProviderUnavailableError: Provider is offline.
            ModelNotAvailableError: Model is not loaded.
            ProviderTimeoutError: Response exceeded timeout.
            ContextLengthExceededError: Prompt too long for model.
        """

    @abstractmethod
    async def stream_chat(self, request: ChatRequest) -> ChatStream:
        """
        Execute a streaming chat completion.

        Yields:
            StreamChunk objects; the final chunk has is_final=True.

        Raises same exceptions as chat().
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """
        Probe the provider and return its health status.
        Must never raise — return ProviderHealth(is_healthy=False) on error.
        """

    @abstractmethod
    async def list_available_models(self) -> list[ModelInfo]:
        """
        Query the provider at runtime for the currently available models.
        May differ from supported_models if models are dynamically loaded.
        """

    # ------------------------------------------------------------------
    # Capability query
    # ------------------------------------------------------------------

    def can_handle(self, ctx: RoutingContext) -> bool:
        """
        Return True if this provider can satisfy all required capabilities.
        Default implementation checks supported_models metadata.
        """
        for cap in ctx.required_capabilities:
            if not any(cap in m.capabilities for m in self.supported_models):
                return False
        return True

    def get_model_for(self, ctx: RoutingContext) -> ModelInfo | None:
        """
        Return the best model for the given routing context, or None.
        Default: first model satisfying all required capabilities.
        """
        candidates = [
            m for m in self.supported_models
            if m.is_available
            and all(c in m.capabilities for c in ctx.required_capabilities)
        ]
        if ctx.preferred_tier:
            tiered = [m for m in candidates if m.tier == ctx.preferred_tier]
            if tiered:
                candidates = tiered
        if ctx.min_context_tokens:
            candidates = [
                m for m in candidates
                if m.context_window_tokens >= ctx.min_context_tokens
            ]
        return candidates[0] if candidates else None


class IEmbeddingProvider(ABC):
    """Contract for embedding-capable providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str: ...

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """
        Generate embeddings for a batch of texts.

        Raises:
            EmbeddingError: On any embedding failure.
            ProviderUnavailableError: Provider is offline.
        """

    @abstractmethod
    async def health_check(self) -> ProviderHealth: ...