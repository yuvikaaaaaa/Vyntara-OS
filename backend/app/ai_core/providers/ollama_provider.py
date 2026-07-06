"""IOS AI Core — Ollama Provider."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from app.ai_core.exceptions import (
    ContextLengthExceededError,
    EmbeddingError,
    ModelNotAvailableError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StreamingError,
)
from app.ai_core.providers.base_provider import BaseEmbeddingProvider, BaseProvider
from app.ai_core.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatStream,
    EmbeddingRequest,
    EmbeddingResponse,
    ModelCapability,
    ModelInfo,
    ModelTierLabel,
    ProviderHealth,
    StreamChunk,
    TokenUsage,
)
from app.core.config import get_settings


class OllamaProvider(BaseProvider, BaseEmbeddingProvider):
    """
    Provider implementation for Ollama local LLM server.

    Handles both chat completion and embeddings through Ollama's REST API.
    Implements streaming via server-sent events (NDJSON).
    """

    _PROVIDER_NAME = "ollama"

    # Static capability map — overridden at runtime by list_available_models()
    _STATIC_MODELS: list[ModelInfo] = []

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        max_retries: int = 3,
    ) -> None:
        BaseProvider.__init__(self)
        BaseEmbeddingProvider.__init__(self)
        settings = get_settings()
        self._base_url = (base_url or str(settings.ollama.base_url)).rstrip("/")
        self._timeout = timeout or settings.ollama.timeout
        self._max_retries = max_retries
        self._keep_alive = settings.ollama.keep_alive

        # Pre-populate from settings
        self._static_models: list[ModelInfo] = self._build_static_models(settings)

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def provider_name(self) -> str:
        return self._PROVIDER_NAME

    @property
    def supported_models(self) -> list[ModelInfo]:
        return self._static_models

    # ------------------------------------------------------------------
    # Chat (non-streaming)
    # ------------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        async with self._span(
            "chat",
            provider=self._PROVIDER_NAME,
            model=request.model_id,
        ):
            start = self._now_ms()

            async def _call() -> ChatResponse:
                payload = self._build_chat_payload(request, stream=False)
                try:
                    async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                        resp = await client.post(
                            f"{self._base_url}/api/chat", json=payload
                        )
                        resp.raise_for_status()
                        data = resp.json()
                except httpx.TimeoutException as exc:
                    raise ProviderTimeoutError(
                        f"Ollama timeout for model '{request.model_id}'.",
                        details={"model_id": request.model_id},
                    ) from exc
                except httpx.HTTPStatusError as exc:
                    self._raise_from_http(exc, request.model_id)
                except httpx.ConnectError as exc:
                    raise ProviderUnavailableError(
                        f"Cannot connect to Ollama at {self._base_url}.",
                        details={"url": self._base_url},
                    ) from exc

                msg = data.get("message", {})
                usage = self._extract_usage(data)
                return ChatResponse(
                    content=msg.get("content", ""),
                    model_id=data.get("model", request.model_id),
                    usage=usage,
                    finish_reason=data.get("done_reason", "stop"),
                    provider=self._PROVIDER_NAME,
                    latency_ms=self._elapsed_ms(start),
                    raw_response=data,
                )

            return await self._with_retry(
                _call,
                max_retries=self._max_retries,
                provider=self._PROVIDER_NAME,
                operation="chat",
            )

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------

    async def stream_chat(self, request: ChatRequest) -> ChatStream:
        """
        Yields StreamChunk objects as Ollama streams NDJSON lines.
        The final chunk carries usage and is_final=True.
        """
        async with self._span(
            "stream_chat",
            provider=self._PROVIDER_NAME,
            model=request.model_id,
        ):
            payload = self._build_chat_payload(request, stream=True)
            try:
                async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                    async with client.stream(
                        "POST", f"{self._base_url}/api/chat", json=payload
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise StreamingError(
                                    f"Failed to parse Ollama stream chunk: {line[:200]}"
                                ) from exc

                            is_done = data.get("done", False)
                            token = data.get("message", {}).get("content", "")
                            usage: TokenUsage | None = None
                            if is_done:
                                usage = self._extract_usage(data)
                            yield StreamChunk(
                                content=token,
                                is_final=is_done,
                                finish_reason=data.get("done_reason") if is_done else None,
                                usage=usage,
                            )
                            if is_done:
                                break
            except httpx.TimeoutException as exc:
                raise ProviderTimeoutError(
                    f"Ollama stream timeout for '{request.model_id}'.",
                    details={"model_id": request.model_id},
                ) from exc
            except httpx.ConnectError as exc:
                raise ProviderUnavailableError(
                    f"Cannot connect to Ollama at {self._base_url}."
                ) from exc

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        async with self._span(
            "embed",
            provider=self._PROVIDER_NAME,
            model=request.model_id,
        ):
            start = self._now_ms()
            all_embeddings: list[list[float]] = []
            total_tokens = 0

            # Ollama embed endpoint accepts one input at a time; batch serially.
            try:
                async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
                    for text in request.texts:
                        resp = await client.post(
                            f"{self._base_url}/api/embeddings",
                            json={
                                "model": request.model_id,
                                "prompt": text,
                                "keep_alive": self._keep_alive,
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        embedding = data.get("embedding", [])
                        if not embedding:
                            raise EmbeddingError(
                                f"Ollama returned empty embedding for model '{request.model_id}'."
                            )
                        all_embeddings.append(embedding)
                        total_tokens += len(text.split())
            except httpx.HTTPStatusError as exc:
                raise EmbeddingError(
                    f"Ollama embedding error: {exc.response.status_code}",
                    details={"model_id": request.model_id},
                ) from exc
            except httpx.ConnectError as exc:
                raise ProviderUnavailableError(
                    f"Cannot connect to Ollama at {self._base_url}."
                ) from exc

            dimension = len(all_embeddings[0]) if all_embeddings else 0
            return EmbeddingResponse(
                embeddings=all_embeddings,
                model_id=request.model_id,
                dimension=dimension,
                provider=self._PROVIDER_NAME,
                latency_ms=self._elapsed_ms(start),
                token_count=total_tokens,
            )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                start = self._now_ms()
                resp = await client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                latency = float(self._elapsed_ms(start))
                models = [m.get("name", "") for m in data.get("models", [])]
                return ProviderHealth(
                    provider=self._PROVIDER_NAME,
                    is_healthy=True,
                    latency_ms=latency,
                    available_models=models,
                )
        except Exception as exc:
            return ProviderHealth(
                provider=self._PROVIDER_NAME,
                is_healthy=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Model listing
    # ------------------------------------------------------------------

    async def list_available_models(self) -> list[ModelInfo]:
        """Query Ollama for currently loaded/available models."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
            models: list[ModelInfo] = []
            for m in data.get("models", []):
                name: str = m.get("name", "")
                tier = self._infer_tier(name)
                caps = self._infer_capabilities(name)
                models.append(
                    ModelInfo(
                        model_id=name,
                        display_name=name,
                        provider=self._PROVIDER_NAME,
                        tier=tier,
                        capabilities=caps,
                        context_window_tokens=self._infer_context(name),
                        is_available=True,
                        extra={"size": m.get("size", 0)},
                    )
                )
            # Update static cache
            self._static_models = models or self._static_models
            return self._static_models
        except Exception:
            return self._static_models

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_chat_payload(self, request: ChatRequest, *, stream: bool) -> dict:
        messages = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        for m in request.messages:
            messages.append({"role": m.role, "content": m.content})

        payload: dict = {
            "model": request.model_id,
            "messages": messages,
            "stream": stream,
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": request.config.temperature,
                "top_p": request.config.top_p,
            },
        }
        if request.config.top_k is not None:
            payload["options"]["top_k"] = request.config.top_k
        if request.config.max_tokens is not None:
            payload["options"]["num_predict"] = request.config.max_tokens
        if request.config.stop:
            payload["options"]["stop"] = request.config.stop
        if request.config.repeat_penalty is not None:
            payload["options"]["repeat_penalty"] = request.config.repeat_penalty
        if request.config.seed is not None:
            payload["options"]["seed"] = request.config.seed
        return payload

    @staticmethod
    def _extract_usage(data: dict) -> TokenUsage:
        prompt_tokens = data.get("prompt_eval_count", 0) or 0
        completion_tokens = data.get("eval_count", 0) or 0
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )

    @staticmethod
    def _raise_from_http(exc: httpx.HTTPStatusError, model_id: str) -> None:
        status = exc.response.status_code
        if status == 404:
            raise ModelNotAvailableError(
                f"Model '{model_id}' not found on Ollama.",
                details={"model_id": model_id},
            ) from exc
        if status == 400:
            body = exc.response.text.lower()
            if "context" in body or "too long" in body:
                raise ContextLengthExceededError(
                    f"Input exceeds context window for '{model_id}'.",
                    details={"model_id": model_id},
                ) from exc
        raise ProviderUnavailableError(
            f"Ollama HTTP {status}: {exc.response.text[:200]}",
            details={"status_code": status},
        ) from exc

    @staticmethod
    def _infer_tier(name: str) -> ModelTierLabel:
        n = name.lower()
        if any(k in n for k in ("70b", "65b", "34b", "mixtral")):
            return ModelTierLabel.LARGE
        if any(k in n for k in ("code", "coder", "starcoder")):
            return ModelTierLabel.CODE
        if any(k in n for k in ("llava", "vision", "bakllava")):
            return ModelTierLabel.VISION
        if any(k in n for k in ("7b", "8b", "13b", "14b")):
            return ModelTierLabel.MEDIUM
        return ModelTierLabel.SMALL

    @staticmethod
    def _infer_capabilities(name: str) -> list[ModelCapability]:
        n = name.lower()
        caps = [ModelCapability.TEXT_GENERATION]
        if any(k in n for k in ("code", "coder", "starcoder", "deepseek")):
            caps.append(ModelCapability.CODE_GENERATION)
        if any(k in n for k in ("llava", "vision", "bakllava")):
            caps.append(ModelCapability.VISION)
        if any(k in n for k in ("70b", "mixtral", "llama3.1")):
            caps.append(ModelCapability.LONG_CONTEXT)
        return caps

    @staticmethod
    def _infer_context(name: str) -> int:
        n = name.lower()
        if "llama3.1" in n or "mixtral" in n:
            return 128_000
        if any(k in n for k in ("70b", "34b")):
            return 32_768
        return 8_192

    def _build_static_models(self, settings) -> list[ModelInfo]:
        """Build initial ModelInfo list from settings configuration."""
        from app.core.constants import (
            OLLAMA_CONTEXT_CODE,
            OLLAMA_CONTEXT_LARGE,
            OLLAMA_CONTEXT_MEDIUM,
            OLLAMA_CONTEXT_SMALL,
            OLLAMA_MODEL_CODE,
            OLLAMA_MODEL_LARGE,
            OLLAMA_MODEL_MEDIUM,
            OLLAMA_MODEL_SMALL,
            OLLAMA_MODEL_VISION,
        )
        return [
            ModelInfo(
                model_id=settings.ollama.model_large,
                display_name=settings.ollama.model_large,
                provider=self._PROVIDER_NAME,
                tier=ModelTierLabel.LARGE,
                capabilities=[
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.LONG_CONTEXT,
                ],
                context_window_tokens=OLLAMA_CONTEXT_LARGE,
            ),
            ModelInfo(
                model_id=settings.ollama.model_medium,
                display_name=settings.ollama.model_medium,
                provider=self._PROVIDER_NAME,
                tier=ModelTierLabel.MEDIUM,
                capabilities=[ModelCapability.TEXT_GENERATION],
                context_window_tokens=OLLAMA_CONTEXT_MEDIUM,
            ),
            ModelInfo(
                model_id=settings.ollama.model_code,
                display_name=settings.ollama.model_code,
                provider=self._PROVIDER_NAME,
                tier=ModelTierLabel.CODE,
                capabilities=[
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.CODE_GENERATION,
                ],
                context_window_tokens=OLLAMA_CONTEXT_CODE,
            ),
            ModelInfo(
                model_id=settings.ollama.model_vision,
                display_name=settings.ollama.model_vision,
                provider=self._PROVIDER_NAME,
                tier=ModelTierLabel.VISION,
                capabilities=[
                    ModelCapability.TEXT_GENERATION,
                    ModelCapability.VISION,
                ],
                context_window_tokens=8_192,
            ),
            ModelInfo(
                model_id=settings.ollama.model_small,
                display_name=settings.ollama.model_small,
                provider=self._PROVIDER_NAME,
                tier=ModelTierLabel.SMALL,
                capabilities=[ModelCapability.TEXT_GENERATION],
                context_window_tokens=OLLAMA_CONTEXT_SMALL,
            ),
        ]