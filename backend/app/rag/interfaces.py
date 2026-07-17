"""IOS RAG — Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.rag.types import (
    AssembledPrompt,
    CitedResponse,
    GroundingResult,
    HallucinationReport,
    RAGRequest,
    ValidationResult,
)
from app.retrieval.types import BuiltContext, ContextBudget, RerankedItem


class IPromptAssembler(ABC):
    """Contract for prompt assembly implementations."""

    @abstractmethod
    async def assemble(
        self,
        request: RAGRequest,
        context: BuiltContext,
    ) -> AssembledPrompt:
        """Build the final system/user prompt with injected context."""


class IGroundingChecker(ABC):
    """Contract for grounding verification implementations."""

    @abstractmethod
    async def check_grounding(
        self,
        response_text: str,
        context: BuiltContext,
    ) -> GroundingResult:
        """Verify that claims in response_text are supported by context."""


class ICitationManager(ABC):
    """Contract for citation tracking and injection implementations."""

    @abstractmethod
    async def inject_citations(
        self,
        response_text: str,
        context: BuiltContext,
        grounding: GroundingResult | None = None,
    ) -> CitedResponse:
        """Annotate response_text with inline citation markers."""


class IHallucinationChecker(ABC):
    """Contract for hallucination detection implementations."""

    @abstractmethod
    async def check(
        self,
        response_text: str,
        context: BuiltContext,
        grounding: GroundingResult | None = None,
    ) -> HallucinationReport:
        """Detect unsupported or contradicted claims in response_text."""


class IResponseValidator(ABC):
    """Contract for structural response validation implementations."""

    @abstractmethod
    def validate(
        self,
        response_text: str,
        cited_response: CitedResponse | None,
        request: RAGRequest,
    ) -> ValidationResult:
        """Validate structure, completeness, and citation consistency."""


class IContextOptimizer(ABC):
    """Contract for context compression / optimisation implementations."""

    @abstractmethod
    async def optimize(
        self,
        items: list[RerankedItem],
        budget: ContextBudget,
        query: str,
    ) -> list[RerankedItem]:
        """Compress or filter items to maximise semantic coverage under budget."""