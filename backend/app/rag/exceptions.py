"""IOS RAG — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class RAGError(IosBaseException):
    http_status = 500
    code = "RAG_ERROR"


class PromptAssemblyError(RAGError):
    code = "PROMPT_ASSEMBLY_ERROR"


class PromptBudgetExceededError(PromptAssemblyError):
    http_status = 422
    code = "PROMPT_BUDGET_EXCEEDED"


class GroundingError(RAGError):
    code = "GROUNDING_ERROR"


class CitationError(RAGError):
    code = "CITATION_ERROR"


class MissingCitationError(CitationError):
    http_status = 422
    code = "MISSING_CITATION"


class HallucinationDetectionError(RAGError):
    code = "HALLUCINATION_DETECTION_ERROR"


class HallucinationDetectedError(RAGError):
    http_status = 422
    code = "HALLUCINATION_DETECTED"


class ResponseValidationError(RAGError):
    http_status = 422
    code = "RESPONSE_VALIDATION_ERROR"


class ResponseBuildError(RAGError):
    code = "RESPONSE_BUILD_ERROR"


class ContextOptimizationError(RAGError):
    code = "CONTEXT_OPTIMIZATION_ERROR"


class RAGPipelineError(RAGError):
    code = "RAG_PIPELINE_ERROR"


class NoContextAvailableError(RAGError):
    http_status = 422
    code = "NO_CONTEXT_AVAILABLE"


class GenerationFailedError(RAGError):
    http_status = 502
    code = "GENERATION_FAILED"