"""IOS AI Core — BaseProvider."""
from __future__ import annotations

from app.ai_core.base import AICoreMixin
from app.ai_core.interfaces import IEmbeddingProvider, ILanguageModelProvider


class BaseProvider(AICoreMixin, ILanguageModelProvider):
    """
    Base class for all LLM providers.

    Subclasses must implement:
    - provider_name (property)
    - supported_models (property)
    - chat()
    - stream_chat()
    - health_check()
    - list_available_models()

    Inherits retry, timeout, and telemetry utilities from AICoreMixin.
    """

    def __init__(self) -> None:
        AICoreMixin.__init__(self)

    # Default can_handle / get_model_for are inherited from ILanguageModelProvider.
    # Override per-provider only when the default logic is insufficient.


class BaseEmbeddingProvider(AICoreMixin, IEmbeddingProvider):
    """
    Base class for embedding-capable providers.

    Subclasses must implement:
    - provider_name (property)
    - embed()
    - health_check()
    """

    def __init__(self) -> None:
        AICoreMixin.__init__(self)