"""IOS RAG — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from app.retrieval.types import BuiltContext


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class GroundingLevel(str, Enum):
    FULLY_GROUNDED = "fully_grounded"
    PARTIALLY_GROUNDED = "partially_grounded"
    UNGROUNDED = "ungrounded"


class ClaimVerdict(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    UNVERIFIABLE = "unverifiable"


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ResponseFormat(str, Enum):
    TEXT = "text"
    MARKDOWN = "markdown"
    JSON = "json"


class CompressionStrategy(str, Enum):
    NONE = "none"
    TRUNCATE = "truncate"
    EXTRACTIVE_SUMMARY = "extractive_summary"   # sentence-selection based
    DEDUPLICATE_ONLY = "deduplicate_only"


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


@dataclass
class PromptSection:
    """A single named section within the assembled prompt."""
    name: str
    content: str
    token_estimate: int = 0
    priority: int = 5     # higher = more important, kept first under budget pressure


@dataclass
class AssembledPrompt:
    """Final assembled prompt ready for AI Core submission."""
    system_prompt: str | None
    user_prompt: str
    sections: list[PromptSection] = field(default_factory=list)
    total_tokens: int = 0
    citations: list[dict[str, Any]] = field(default_factory=list)
    context_used: BuiltContext | None = None


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


@dataclass
class Claim:
    """A single factual statement extracted from a generated response."""
    text: str
    start_char: int = 0
    end_char: int = 0
    sentence_index: int = 0


@dataclass
class ClaimEvidence:
    """Evidence linking a claim to a retrieved context item."""
    claim: Claim
    verdict: ClaimVerdict
    supporting_item_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    explanation: str | None = None


@dataclass
class GroundingResult:
    """Full grounding analysis for a generated response."""
    level: GroundingLevel
    overall_confidence: float
    claims: list[ClaimEvidence] = field(default_factory=list)
    ungrounded_claims: list[Claim] = field(default_factory=list)
    grounding_ratio: float = 0.0   # fraction of claims that are SUPPORTED


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


@dataclass
class Citation:
    label: str                    # e.g. "[1]"
    item_id: str
    source: str                   # RetrievalSource value
    title: str | None = None
    parent_id: str | None = None
    confidence: float = 1.0
    cited_span: str | None = None


@dataclass
class CitedResponse:
    """A response text with resolved inline citations."""
    text: str
    citations: list[Citation] = field(default_factory=list)
    uncited_claim_count: int = 0


# ---------------------------------------------------------------------------
# Hallucination detection
# ---------------------------------------------------------------------------


@dataclass
class HallucinationFlag:
    claim: Claim
    reason: str
    severity: ValidationSeverity = ValidationSeverity.WARNING


@dataclass
class HallucinationReport:
    has_hallucinations: bool
    flags: list[HallucinationFlag] = field(default_factory=list)
    hallucination_rate: float = 0.0   # fraction of claims flagged
    grounding_result: GroundingResult | None = None


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    field: str | None = None


@dataclass
class ValidationResult:
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]


# ---------------------------------------------------------------------------
# Final RAG response
# ---------------------------------------------------------------------------


@dataclass
class RAGRequest:
    query: str
    user_id: UUID
    conversation_id: UUID | None = None
    system_instructions: str | None = None
    response_format: ResponseFormat = ResponseFormat.MARKDOWN
    max_context_tokens: int = 4096
    max_response_tokens: int | None = None
    model_id: str | None = None
    require_citations: bool = True
    hallucination_check: bool = True
    compression_strategy: CompressionStrategy = CompressionStrategy.DEDUPLICATE_ONLY
    stream: bool = False


@dataclass
class RAGResponse:
    """The final, validated, citation-annotated RAG output."""
    query: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    grounding: GroundingResult | None = None
    hallucination: HallucinationReport | None = None
    validation: ValidationResult | None = None
    model_id: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0
    context_items_used: int = 0
    confidence_score: float = 0.0
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class RAGStreamChunk:
    """A single streaming chunk from the RAG pipeline."""
    content: str
    is_final: bool = False
    citations: list[Citation] = field(default_factory=list)
    finish_reason: str | None = None
