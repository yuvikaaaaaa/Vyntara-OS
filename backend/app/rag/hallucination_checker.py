"""IOS RAG — Hallucination Checker."""
from __future__ import annotations

import re

from app.rag.base import BaseRAGComponent
from app.rag.exceptions import HallucinationDetectionError
from app.rag.grounding import Grounding
from app.rag.interfaces import IGroundingChecker, IHallucinationChecker
from app.rag.types import (
    ClaimVerdict,
    GroundingLevel,
    GroundingResult,
    HallucinationFlag,
    HallucinationReport,
    ValidationSeverity,
)
from app.retrieval.types import BuiltContext

_CITATION_MARKER_PATTERN = re.compile(r"\[(\d+)\]")

# Phrases that signal the model is hedging/refusing, which should not be
# flagged as hallucination candidates even if grounding is weak.
_HEDGE_PATTERNS = (
    "i don't have", "i do not have", "not mentioned in the",
    "the context does not", "context doesn't", "unable to find",
    "no information", "cannot determine",
)


class HallucinationChecker(BaseRAGComponent, IHallucinationChecker):
    """
    Detects unsupported or contradicted claims and computes an overall
    hallucination risk profile for a generated response.

    Delegates claim-level grounding analysis to an IGroundingChecker
    (typically the Grounding component) and layers additional checks on
    top:
    - Fabricated citation markers (referencing a [N] not present in context)
    - Claims with SUPPORTED verdict but suspiciously low confidence
    - Overall hallucination rate against a configurable threshold

    Hedging/refusal language is excluded from flagging since a model
    correctly admitting insufficient context is the desired behaviour,
    not a hallucination.
    """

    def __init__(
        self,
        grounding_checker: IGroundingChecker | None = None,
        *,
        low_confidence_threshold: float = 0.35,
        rate_threshold_for_flag: float = 0.25,
    ) -> None:
        super().__init__()
        self._grounding = grounding_checker or Grounding()
        self._low_confidence_threshold = low_confidence_threshold
        self._rate_threshold = rate_threshold_for_flag

    async def check(
        self,
        response_text: str,
        context: BuiltContext,
        grounding: GroundingResult | None = None,
    ) -> HallucinationReport:
        async with self._span("check_hallucination"):
            try:
                grounding_result = grounding or await self._grounding.check_grounding(
                    response_text, context
                )

                flags: list[HallucinationFlag] = []
                flags.extend(self._flag_unsupported_claims(grounding_result))
                flags.extend(self._flag_fabricated_citations(response_text, context))
                flags.extend(self._flag_low_confidence_supported(grounding_result))

                total_claims = max(len(grounding_result.claims), 1)
                hallucination_rate = len(
                    [f for f in flags if f.severity == ValidationSeverity.ERROR]
                    + [
                        f for f in flags
                        if f.severity == ValidationSeverity.WARNING
                        and "unsupported" in f.reason.lower()
                    ]
                ) / total_claims

                has_hallucinations = hallucination_rate >= self._rate_threshold or any(
                    f.severity == ValidationSeverity.ERROR for f in flags
                )

                report = HallucinationReport(
                    has_hallucinations=has_hallucinations,
                    flags=flags,
                    hallucination_rate=min(1.0, hallucination_rate),
                    grounding_result=grounding_result,
                )
                self._log.info(
                    "hallucination_check_complete",
                    flags=len(flags),
                    rate=round(report.hallucination_rate, 3),
                    has_hallucinations=has_hallucinations,
                )
                return report
            except Exception as exc:
                raise HallucinationDetectionError(
                    f"Hallucination check failed: {exc}"
                ) from exc

    # ------------------------------------------------------------------
    # Flag generators
    # ------------------------------------------------------------------

    def _flag_unsupported_claims(
        self, grounding: GroundingResult
    ) -> list[HallucinationFlag]:
        flags: list[HallucinationFlag] = []
        for evidence in grounding.claims:
            if evidence.verdict == ClaimVerdict.SUPPORTED:
                continue
            if self._is_hedge(evidence.claim.text):
                continue
            severity = (
                ValidationSeverity.ERROR
                if evidence.verdict == ClaimVerdict.CONTRADICTED
                else ValidationSeverity.WARNING
            )
            reason = (
                "Claim contradicts retrieved context."
                if evidence.verdict == ClaimVerdict.CONTRADICTED
                else f"Claim is unsupported by retrieved context "
                     f"(verdict={evidence.verdict.value})."
            )
            flags.append(
                HallucinationFlag(claim=evidence.claim, reason=reason, severity=severity)
            )
        return flags

    def _flag_fabricated_citations(
        self, response_text: str, context: BuiltContext
    ) -> list[HallucinationFlag]:
        valid_labels = {c["label"] for c in context.citations}
        flags: list[HallucinationFlag] = []
        for match in _CITATION_MARKER_PATTERN.finditer(response_text):
            label = f"[{match.group(1)}]"
            if label not in valid_labels:
                # Locate the sentence containing this fabricated marker for context
                from app.rag.types import Claim
                start = max(0, match.start() - 80)
                end = min(len(response_text), match.end() + 20)
                snippet = response_text[start:end].strip()
                flags.append(
                    HallucinationFlag(
                        claim=Claim(
                            text=snippet,
                            start_char=match.start(),
                            end_char=match.end(),
                        ),
                        reason=f"Citation marker {label} does not correspond to any "
                               f"retrieved context item.",
                        severity=ValidationSeverity.ERROR,
                    )
                )
        return flags

    def _flag_low_confidence_supported(
        self, grounding: GroundingResult
    ) -> list[HallucinationFlag]:
        flags: list[HallucinationFlag] = []
        for evidence in grounding.claims:
            if (
                evidence.verdict == ClaimVerdict.SUPPORTED
                and evidence.confidence < self._low_confidence_threshold
            ):
                flags.append(
                    HallucinationFlag(
                        claim=evidence.claim,
                        reason=f"Claim marked supported but with low confidence "
                               f"({evidence.confidence:.2f}); recommend manual review.",
                        severity=ValidationSeverity.INFO,
                    )
                )
        return flags

    @staticmethod
    def _is_hedge(text: str) -> bool:
        lowered = text.lower()
        return any(phrase in lowered for phrase in _HEDGE_PATTERNS)

    # ------------------------------------------------------------------
    # Risk scoring
    # ------------------------------------------------------------------

    @staticmethod
    def compute_risk_score(report: HallucinationReport) -> float:
        """
        Compute a single 0.0-1.0 risk score from a HallucinationReport,
        weighting error-severity flags more heavily than warnings.
        """
        if not report.flags:
            return 0.0
        weight = {
            ValidationSeverity.ERROR: 1.0,
            ValidationSeverity.WARNING: 0.5,
            ValidationSeverity.INFO: 0.15,
        }
        total = sum(weight[f.severity] for f in report.flags)
        normalised = total / max(len(report.flags), 1)
        return min(1.0, normalised * (0.5 + report.hallucination_rate))