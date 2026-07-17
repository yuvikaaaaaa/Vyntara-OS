"""IOS RAG — Base Component."""
from __future__ import annotations

import re

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.rag.types import Claim


class BaseRAGComponent:
    """
    Shared foundation for all RAG engine components.

    Provides:
    - Named structured logger
    - OTel async span factory
    - Sentence/claim splitting (shared by Grounding and HallucinationChecker)
    - Token estimation (chars-per-token heuristic, consistent across the module)
    """

    #: Characters-per-token heuristic used across all RAG components.
    CHARS_PER_TOKEN: float = 4.0

    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"rag.{operation}",
            attributes={"rag.component": self.__class__.__name__, **attrs},
        )

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Approximate token count using a ~4 chars/token heuristic."""
        return max(1, int(len(text) / cls.CHARS_PER_TOKEN))

    @staticmethod
    def split_claims(text: str) -> list[Claim]:
        """
        Split response text into individual claim (sentence) units.

        Uses a lightweight regex-based sentence boundary heuristic —
        splits on '.', '!', '?' followed by whitespace and a capital
        letter, while avoiding common abbreviation false-positives.
        """
        # Normalise whitespace first
        normalised = re.sub(r"\s+", " ", text).strip()
        if not normalised:
            return []

        # Sentence boundary heuristic: punctuation + space + uppercase/quote/digit
        boundaries = list(
            re.finditer(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u2018\u201c])", normalised)
        )

        claims: list[Claim] = []
        start = 0
        sentence_index = 0
        for m in boundaries:
            end = m.start()
            sentence = normalised[start:end].strip()
            if sentence:
                claims.append(
                    Claim(
                        text=sentence,
                        start_char=start,
                        end_char=end,
                        sentence_index=sentence_index,
                    )
                )
                sentence_index += 1
            start = m.end()

        tail = normalised[start:].strip()
        if tail:
            claims.append(
                Claim(
                    text=tail,
                    start_char=start,
                    end_char=len(normalised),
                    sentence_index=sentence_index,
                )
            )
        return claims

    @staticmethod
    def token_overlap_ratio(a: str, b: str) -> float:
        """
        Jaccard token-overlap ratio between two strings, case-insensitive.

        Used as a fast heuristic for claim-to-evidence matching when no
        embedding-based similarity is available.
        """
        tokens_a = {t.lower() for t in re.findall(r"\w+", a) if len(t) > 2}
        tokens_b = {t.lower() for t in re.findall(r"\w+", b) if len(t) > 2}
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)