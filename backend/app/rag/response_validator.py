"""IOS RAG — Response Validator."""
from __future__ import annotations

from app.rag.base import BaseRAGComponent
from app.rag.interfaces import IResponseValidator
from app.rag.types import (
    CitedResponse,
    GroundingLevel,
    RAGRequest,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


class ResponseValidator(BaseRAGComponent, IResponseValidator):
    """
    Final quality gate for a generated RAG response before it is returned
    to the caller.

    Validates:
    - Structural integrity (non-empty, not truncated mid-sentence)
    - Completeness relative to the original query
    - Citation consistency (citations required but absent, or present but
      the response claims require_citations=False)
    - Minimum grounding level
    - Token limit compliance
    """

    def __init__(
        self,
        *,
        min_response_chars: int = 10,
        min_grounding_level: GroundingLevel = GroundingLevel.PARTIALLY_GROUNDED,
        max_uncited_claims: int = 3,
    ) -> None:
        super().__init__()
        self._min_response_chars = min_response_chars
        self._min_grounding_level = min_grounding_level
        self._max_uncited_claims = max_uncited_claims

    def validate(
        self,
        response_text: str,
        cited_response: CitedResponse | None,
        request: RAGRequest,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []

        issues.extend(self._validate_structure(response_text))
        issues.extend(self._validate_completeness(response_text, request))
        if cited_response is not None:
            issues.extend(self._validate_citations(cited_response, request))
        if request.max_response_tokens:
            issues.extend(
                self._validate_token_limit(response_text, request.max_response_tokens)
            )

        has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
        result = ValidationResult(is_valid=not has_errors, issues=issues)

        self._log.info(
            "response_validated",
            is_valid=result.is_valid,
            errors=len(result.errors),
            warnings=len(result.warnings),
        )
        return result

    def validate_grounding_level(
        self, level: GroundingLevel
    ) -> ValidationIssue | None:
        """
        Standalone helper: check a GroundingLevel against the configured
        minimum, returning an issue if below threshold or None if acceptable.
        """
        order = {
            GroundingLevel.UNGROUNDED: 0,
            GroundingLevel.PARTIALLY_GROUNDED: 1,
            GroundingLevel.FULLY_GROUNDED: 2,
        }
        if order[level] < order[self._min_grounding_level]:
            return ValidationIssue(
                severity=ValidationSeverity.ERROR,
                code="GROUNDING_BELOW_THRESHOLD",
                message=f"Response grounding level '{level.value}' is below the "
                        f"required minimum '{self._min_grounding_level.value}'.",
                field="grounding",
            )
        return None

    # ------------------------------------------------------------------
    # Internal validators
    # ------------------------------------------------------------------

    def _validate_structure(self, response_text: str) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        stripped = response_text.strip()

        if not stripped:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="EMPTY_RESPONSE",
                    message="Response text is empty.",
                    field="answer",
                )
            )
            return issues

        if len(stripped) < self._min_response_chars:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="RESPONSE_TOO_SHORT",
                    message=f"Response is only {len(stripped)} characters; "
                            f"expected at least {self._min_response_chars}.",
                    field="answer",
                )
            )

        # Detect obvious mid-sentence truncation (no terminal punctuation
        # and ends with a lowercase word/comma — common truncation signature)
        if stripped and stripped[-1] not in ".!?\"')]}`" and not stripped.endswith("```"):
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="POSSIBLE_TRUNCATION",
                    message="Response does not end with terminal punctuation; "
                            "may have been truncated mid-generation.",
                    field="answer",
                )
            )
        return issues

    def _validate_completeness(
        self, response_text: str, request: RAGRequest
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        lowered = response_text.lower()

        # Detect if the model produced only a refusal with no substantive
        # content — acceptable but worth surfacing as an INFO-level issue
        # for downstream quality tracking, not an ERROR.
        refusal_markers = (
            "i cannot answer", "i don't know", "no relevant information",
            "unable to provide an answer",
        )
        if any(m in lowered for m in refusal_markers) and len(response_text.strip()) < 200:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.INFO,
                    code="REFUSAL_RESPONSE",
                    message="Response appears to be a refusal/insufficient-context "
                            "answer rather than a substantive one.",
                    field="answer",
                )
            )
        return issues

    def _validate_citations(
        self, cited_response: CitedResponse, request: RAGRequest
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        if request.require_citations and not cited_response.citations:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    code="MISSING_REQUIRED_CITATIONS",
                    message="Citations are required by the request but none were "
                            "found in the response.",
                    field="citations",
                )
            )

        if cited_response.uncited_claim_count > self._max_uncited_claims:
            issues.append(
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="EXCESSIVE_UNCITED_CLAIMS",
                    message=f"{cited_response.uncited_claim_count} claims lack "
                            f"citation support (threshold: {self._max_uncited_claims}).",
                    field="citations",
                )
            )
        return issues

    def _validate_token_limit(
        self, response_text: str, max_tokens: int
    ) -> list[ValidationIssue]:
        estimated = self.estimate_tokens(response_text)
        if estimated > max_tokens:
            return [
                ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    code="TOKEN_LIMIT_EXCEEDED",
                    message=f"Response (~{estimated} tokens) exceeds the requested "
                            f"max_response_tokens ({max_tokens}).",
                    field="answer",
                )
            ]
        return []