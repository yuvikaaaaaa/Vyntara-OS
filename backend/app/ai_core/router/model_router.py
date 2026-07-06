"""IOS AI Core — Model Router."""
from __future__ import annotations

import asyncio
from typing import Any

from app.ai_core.base import AICoreMixin
from app.ai_core.exceptions import (
    AllProvidersUnhealthyError,
    ModelNotAvailableError,
    NoCapableProviderError,
    ProviderUnavailableError,
    RoutingError,
)
from app.ai_core.interfaces import IEmbeddingProvider, ILanguageModelProvider
from app.ai_core.types import (
    ChatRequest,
    ChatResponse,
    ChatStream,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelCapability,
    ModelInfo,
    ModelTierLabel,
    ProviderHealth,
    RoutingContext,
    StreamChunk,
)


class ProviderRegistry:
    """
    Thread-safe registry of LLM and embedding providers.

    Providers are registered once at application startup.
    Adding a new provider requires only a single register() call.
    """

    def __init__(self) -> None:
        self._llm: dict[str, ILanguageModelProvider] = {}
        self._embedding: dict[str, IEmbeddingProvider] = {}

    def register_llm(self, provider: ILanguageModelProvider) -> None:
        """Register an LLM provider under its provider_name."""
        self._llm[provider.provider_name] = provider

    def register_embedding(self, provider: IEmbeddingProvider) -> None:
        """Register an embedding provider under its provider_name."""
        self._embedding[provider.provider_name] = provider

    def get_llm(self, name: str) -> ILanguageModelProvider | None:
        return self._llm.get(name)

    def get_embedding(self, name: str) -> IEmbeddingProvider | None:
        return self._embedding.get(name)

    @property
    def llm_providers(self) -> list[ILanguageModelProvider]:
        return list(self._llm.values())

    @property
    def embedding_providers(self) -> list[IEmbeddingProvider]:
        return list(self._embedding.values())


class ModelRouter(AICoreMixin):
    """
    Single entry point for all AI generation and embedding requests.

    Implements:
    - Provider registration via ProviderRegistry
    - Capability-aware model selection
    - Health-aware provider fallback
    - Direct model_id routing (bypasses capability selection)
    - Parallel health checks
    - Structured logging and OTel instrumentation

    Usage::

        router = ModelRouter()
        router.register_provider(OllamaProvider())

        response = await router.chat(request)
        stream   = await router.stream_chat(request)
        embs     = await router.embed(embedding_request)
    """

    def __init__(self) -> None:
        AICoreMixin.__init__(self)
        self._registry = ProviderRegistry()
        # Health cache: provider_name → last known health
        self._health_cache: dict[str, ProviderHealth] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_provider(self, provider: ILanguageModelProvider) -> None:
        """
        Register an LLM provider.

        If the provider also implements IEmbeddingProvider it is registered
        for embedding routing as well.

        Args:
            provider: Concrete provider instance.
        """
        self._registry.register_llm(provider)
        if isinstance(provider, IEmbeddingProvider):
            self._registry.register_embedding(provider)
        self._log.info(
            "provider_registered",
            provider=provider.provider_name,
            models=[m.model_id for m in provider.supported_models],
        )

    def register_embedding_provider(self, provider: IEmbeddingProvider) -> None:
        """Register a standalone embedding provider."""
        self._registry.register_embedding(provider)
        self._log.info("embedding_provider_registered", provider=provider.provider_name)

    # ------------------------------------------------------------------
    # Chat (non-streaming)
    # ------------------------------------------------------------------

    async def chat(
        self,
        request: ChatRequest,
        *,
        routing_context: RoutingContext | None = None,
    ) -> ChatResponse:
        """
        Execute a non-streaming chat completion.

        If request.model_id is set, routes directly to the provider that owns
        that model.  Otherwise, uses routing_context to select the best model.

        Args:
            request: Chat completion request.
            routing_context: Optional capability hints for model selection.

        Returns:
            ChatResponse from the selected provider.

        Raises:
            NoCapableProviderError: No provider can satisfy the request.
            RoutingError: Provider selected but request fails after retries.
        """
        async with self._span(
            "chat",
            model=request.model_id,
            stream="false",
        ):
            provider, model_info = await self._resolve(request, routing_context)
            # Ensure request carries the resolved model_id
            resolved_request = self._with_model(request, model_info.model_id)
            self._log.info(
                "routing_chat",
                provider=provider.provider_name,
                model=model_info.model_id,
            )
            try:
                return await provider.chat(resolved_request)
            except ProviderUnavailableError:
                # Attempt fallback to next capable provider
                return await self._fallback_chat(
                    resolved_request, routing_context, exclude=provider.provider_name
                )

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def stream_chat(
        self,
        request: ChatRequest,
        *,
        routing_context: RoutingContext | None = None,
    ) -> ChatStream:
        """
        Execute a streaming chat completion.

        Yields StreamChunk objects; the final chunk has is_final=True.

        Args:
            request: Chat completion request (config.stream is forced True).
            routing_context: Optional capability hints.

        Yields:
            StreamChunk
        """
        async with self._span(
            "stream_chat",
            model=request.model_id,
            stream="true",
        ):
            provider, model_info = await self._resolve(request, routing_context)
            resolved_request = self._with_model(request, model_info.model_id)
            self._log.info(
                "routing_stream",
                provider=provider.provider_name,
                model=model_info.model_id,
            )
            async for chunk in await provider.stream_chat(resolved_request):
                yield chunk

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embed(
        self,
        request: EmbeddingRequest,
        *,
        preferred_provider: str | None = None,
    ) -> EmbeddingResponse:
        """
        Generate embeddings for a batch of texts.

        Args:
            request: Embedding request with texts and model_id.
            preferred_provider: Optional provider name override.

        Returns:
            EmbeddingResponse with all embeddings.

        Raises:
            NoCapableProviderError: No embedding provider available.
        """
        async with self._span("embed", model=request.model_id):
            providers = self._registry.embedding_providers
            if not providers:
                raise NoCapableProviderError("No embedding providers registered.")

            # Try preferred provider first
            if preferred_provider:
                p = self._registry.get_embedding(preferred_provider)
                if p:
                    providers = [p] + [x for x in providers if x.provider_name != preferred_provider]

            last_exc: Exception | None = None
            for ep in providers:
                try:
                    result = await ep.embed(request)
                    self._log.info(
                        "embedding_complete",
                        provider=ep.provider_name,
                        model=request.model_id,
                        texts=len(request.texts),
                        dimension=result.dimension,
                    )
                    return result
                except Exception as exc:
                    self._log.warning(
                        "embedding_provider_failed",
                        provider=ep.provider_name,
                        exc=str(exc),
                    )
                    last_exc = exc

            raise NoCapableProviderError(
                f"All embedding providers failed for model '{request.model_id}'.",
                details={"last_error": str(last_exc)},
            )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def check_all_health(self) -> list[ProviderHealth]:
        """Run health checks on all registered LLM providers concurrently."""
        async with self._span("health_check_all"):
            results = await asyncio.gather(
                *[p.health_check() for p in self._registry.llm_providers],
                return_exceptions=False,
            )
            for health in results:
                self._health_cache[health.provider] = health
                self._log.info(
                    "provider_health",
                    provider=health.provider,
                    healthy=health.is_healthy,
                    latency_ms=health.latency_ms,
                )
            return list(results)

    async def check_provider_health(self, provider_name: str) -> ProviderHealth:
        """Health-check a specific provider by name."""
        p = self._registry.get_llm(provider_name)
        if p is None:
            from app.core.exceptions import NotFoundError
            raise NotFoundError(f"Provider '{provider_name}' not registered.")
        health = await p.health_check()
        self._health_cache[provider_name] = health
        return health

    def get_cached_health(self, provider_name: str) -> ProviderHealth | None:
        return self._health_cache.get(provider_name)

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------

    async def list_all_models(self) -> list[ModelInfo]:
        """Return all models across all registered providers (live query)."""
        all_models: list[ModelInfo] = []
        for provider in self._registry.llm_providers:
            try:
                models = await provider.list_available_models()
                all_models.extend(models)
            except Exception as exc:
                self._log.warning(
                    "list_models_failed",
                    provider=provider.provider_name,
                    exc=str(exc),
                )
        return all_models

    def list_static_models(self) -> list[ModelInfo]:
        """Return static model metadata without querying providers."""
        all_models: list[ModelInfo] = []
        for provider in self._registry.llm_providers:
            all_models.extend(provider.supported_models)
        return all_models

    def get_model_info(self, model_id: str) -> ModelInfo | None:
        """Find a ModelInfo by model_id across all providers."""
        for provider in self._registry.llm_providers:
            for m in provider.supported_models:
                if m.model_id == model_id:
                    return m
        return None

    def list_registered_providers(self) -> list[str]:
        return [p.provider_name for p in self._registry.llm_providers]

    # ------------------------------------------------------------------
    # Resolution and fallback internals
    # ------------------------------------------------------------------

    async def _resolve(
        self,
        request: ChatRequest,
        ctx: RoutingContext | None,
    ) -> tuple[ILanguageModelProvider, ModelInfo]:
        """
        Select provider and model for a request.

        Priority:
        1. If request.model_id is set → find the provider that owns it.
        2. Otherwise use routing_context for capability-based selection.
        3. Fallback to first healthy provider with any available model.
        """
        providers = self._registry.llm_providers
        if not providers:
            raise NoCapableProviderError("No LLM providers registered.")

        # Direct model_id routing
        if request.model_id:
            return self._resolve_by_model_id(request.model_id, providers)

        # Capability-based routing
        if ctx:
            return self._resolve_by_context(ctx, providers)

        # Default: pick first provider, first available model
        for provider in providers:
            models = [m for m in provider.supported_models if m.is_available]
            if models:
                return provider, models[0]

        raise NoCapableProviderError("No available models found across all providers.")

    def _resolve_by_model_id(
        self,
        model_id: str,
        providers: list[ILanguageModelProvider],
    ) -> tuple[ILanguageModelProvider, ModelInfo]:
        for provider in providers:
            for m in provider.supported_models:
                if m.model_id == model_id:
                    return provider, m
        raise ModelNotAvailableError(
            f"Model '{model_id}' not found in any registered provider.",
            details={"model_id": model_id},
        )

    def _resolve_by_context(
        self,
        ctx: RoutingContext,
        providers: list[ILanguageModelProvider],
    ) -> tuple[ILanguageModelProvider, ModelInfo]:
        # Sort providers by cached health (healthy first)
        ordered = self._sort_by_health(providers)
        for provider in ordered:
            model = provider.get_model_for(ctx)
            if model and model.is_available:
                return provider, model

        raise NoCapableProviderError(
            "No provider can satisfy the required capabilities.",
            details={
                "required_capabilities": [c.value for c in ctx.required_capabilities],
                "preferred_tier": ctx.preferred_tier,
            },
        )

    async def _fallback_chat(
        self,
        request: ChatRequest,
        ctx: RoutingContext | None,
        exclude: str,
    ) -> ChatResponse:
        """Attempt chat on any other capable provider when the primary fails."""
        providers = [
            p for p in self._registry.llm_providers
            if p.provider_name != exclude
        ]
        if not providers:
            raise AllProvidersUnhealthyError(
                "Primary provider unavailable and no fallback providers registered."
            )
        ctx_fallback = ctx or RoutingContext()
        try:
            fallback_provider, model_info = self._resolve_by_context(
                ctx_fallback, providers
            )
        except (NoCapableProviderError, RoutingError):
            raise AllProvidersUnhealthyError(
                f"Provider '{exclude}' failed and no capable fallback found."
            )
        resolved = self._with_model(request, model_info.model_id)
        self._log.warning(
            "provider_fallback",
            excluded=exclude,
            fallback=fallback_provider.provider_name,
            model=model_info.model_id,
        )
        return await fallback_provider.chat(resolved)

    def _sort_by_health(
        self, providers: list[ILanguageModelProvider]
    ) -> list[ILanguageModelProvider]:
        """Sort providers: healthy (cached) first, then unknown, then unhealthy."""
        def _key(p: ILanguageModelProvider) -> int:
            h = self._health_cache.get(p.provider_name)
            if h is None:
                return 1          # unknown — try second
            return 0 if h.is_healthy else 2

        return sorted(providers, key=_key)

    @staticmethod
    def _with_model(request: ChatRequest, model_id: str) -> ChatRequest:
        """Return a copy of request with model_id set."""
        from dataclasses import replace
        return replace(request, model_id=model_id)