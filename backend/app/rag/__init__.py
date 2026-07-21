"""IOS RAG — Public API.

The RAG Engine transforms retrieved knowledge into grounded prompts and
validated, citation-annotated responses.

It does NOT perform retrieval (delegated to the Retrieval Engine) and
does NOT execute agents.  It communicates only with the Retrieval
Engine, AI Core, and Memory interfaces.

Usage::

    from app.rag import RAGPipeline, RAGRequest, RAGResponse
    from app.rag import PromptAssembler, Grounding, CitationManager
    from app.rag import HallucinationChecker, ResponseValidator, ResponseBuilder
    from app.rag import ContextOptimizer
"""

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------
from app.rag.types import (
    AssembledPrompt,
    Citation,
    CitedResponse,
    Claim,
    ClaimEvidence,
    ClaimVerdict,
    CompressionStrategy,
    GroundingLevel,
    GroundingResult,
    HallucinationFlag,
    HallucinationReport,
    PromptSection,
    RAGRequest,
    RAGResponse,
    RAGStreamChunk,
    ResponseFormat,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
from app.rag.exceptions import (
    CitationError,
    ContextOptimizationError,
    GenerationFailedError,
    GroundingError,
    HallucinationDetectedError,
    HallucinationDetectionError,
    MissingCitationError,
    NoContextAvailableError,
    PromptAssemblyError,
    PromptBudgetExceededError,
    RAGError,
    RAGPipelineError,
    ResponseBuildError,
    ResponseValidationError,
)

# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
from app.rag.interfaces import (
    ICitationManager,
    IContextOptimizer,
    IGroundingChecker,
    IHallucinationChecker,
    IPromptAssembler,
    IResponseValidator,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
from app.rag.base import BaseRAGComponent

# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
from app.rag.prompt_assembler import PromptAssembler
from app.rag.grounding import Grounding
from app.rag.citation_manager import CitationManager
from app.rag.hallucination_checker import HallucinationChecker
from app.rag.response_validator import ResponseValidator
from app.rag.response_builder import ResponseBuilder
from app.rag.context_optimizer import ContextOptimizer

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
from app.rag.rag_pipeline import RAGPipeline

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Types
    "GroundingLevel",
    "ClaimVerdict",
    "ValidationSeverity",
    "ResponseFormat",
    "CompressionStrategy",
    "PromptSection",
    "AssembledPrompt",
    "Claim",
    "ClaimEvidence",
    "GroundingResult",
    "Citation",
    "CitedResponse",
    "HallucinationFlag",
    "HallucinationReport",
    "ValidationIssue",
    "ValidationResult",
    "RAGRequest",
    "RAGResponse",
    "RAGStreamChunk",
    # Exceptions
    "RAGError",
    "PromptAssemblyError",
    "PromptBudgetExceededError",
    "GroundingError",
    "CitationError",
    "MissingCitationError",
    "HallucinationDetectionError",
    "HallucinationDetectedError",
    "ResponseValidationError",
    "ResponseBuildError",
    "ContextOptimizationError",
    "RAGPipelineError",
    "NoContextAvailableError",
    "GenerationFailedError",
    # Interfaces
    "IPromptAssembler",
    "IGroundingChecker",
    "ICitationManager",
    "IHallucinationChecker",
    "IResponseValidator",
    "IContextOptimizer",
    # Base
    "BaseRAGComponent",
    # Components
    "PromptAssembler",
    "Grounding",
    "CitationManager",
    "HallucinationChecker",
    "ResponseValidator",
    "ResponseBuilder",
    "ContextOptimizer",
    # Orchestrator
    "RAGPipeline",
]