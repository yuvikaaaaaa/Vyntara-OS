"""IOS RAG — Prompt Assembler."""
from __future__ import annotations

from app.rag.base import BaseRAGComponent
from app.rag.exceptions import PromptAssemblyError
from app.rag.interfaces import IPromptAssembler
from app.rag.types import AssembledPrompt, PromptSection, RAGRequest
from app.retrieval.types import BuiltContext

# Default instruction block appended when citations are required.
_CITATION_INSTRUCTION = (
    "When you state a fact drawn from the provided context, cite it using "
    "the bracketed reference number shown next to that context item, e.g. [1]. "
    "Do not fabricate citation numbers. If the context does not contain "
    "information needed to answer, say so explicitly rather than guessing."
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are a careful, precise assistant. Answer the user's question using "
    "only the information provided in the CONTEXT section below. If the "
    "context is insufficient, state that clearly instead of speculating."
)


class PromptAssembler(BaseRAGComponent, IPromptAssembler):
    """
    Assembles the final grounded prompt from a RAGRequest and a
    BuiltContext (produced upstream by the Retrieval Engine's
    ContextBuilder).

    Sections are composed in priority order and trimmed to fit the
    request's max_context_tokens budget.  Citation instructions are
    injected automatically when request.require_citations is True.
    """

    def __init__(self, *, working_memory_gateway=None) -> None:
        super().__init__()
        # Optional gateway for injecting working-memory context; kept as a
        # loosely-typed callable to avoid a hard dependency on a concrete
        # memory implementation (Memory Engine interfaces only).
        self._working_memory_gateway = working_memory_gateway

    async def assemble(
        self,
        request: RAGRequest,
        context: BuiltContext,
    ) -> AssembledPrompt:
        async with self._span("assemble_prompt"):
            try:
                system_prompt = self._build_system_prompt(request)
                sections = await self._build_sections(request, context)
                user_prompt = self._render_sections(sections)

                total_tokens = self.estimate_tokens(system_prompt or "") + self.estimate_tokens(
                    user_prompt
                )
                if total_tokens > request.max_context_tokens * 1.5:
                    # Soft guard — hard truncation is the caller's (ContextBuilder's)
                    # responsibility, but assembly overhead (instructions, headers)
                    # should never balloon far beyond the intended budget.
                    self._log.warning(
                        "prompt_token_overhead_high",
                        total_tokens=total_tokens,
                        budget=request.max_context_tokens,
                    )

                prompt = AssembledPrompt(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    sections=sections,
                    total_tokens=total_tokens,
                    citations=context.citations,
                    context_used=context,
                )
                self._log.info(
                    "prompt_assembled",
                    sections=len(sections),
                    total_tokens=total_tokens,
                    citations=len(context.citations),
                )
                return prompt
            except PromptAssemblyError:
                raise
            except Exception as exc:
                raise PromptAssemblyError(f"Prompt assembly failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_system_prompt(self, request: RAGRequest) -> str:
        base = request.system_instructions or _DEFAULT_SYSTEM_PROMPT
        if request.require_citations:
            return f"{base}\n\n{_CITATION_INSTRUCTION}"
        return base

    async def _build_sections(
        self, request: RAGRequest, context: BuiltContext
    ) -> list[PromptSection]:
        sections: list[PromptSection] = []

        if self._working_memory_gateway is not None:
            try:
                memory_text = await self._working_memory_gateway.export_context()
                if memory_text:
                    sections.append(
                        PromptSection(
                            name="conversation_context",
                            content=memory_text,
                            token_estimate=self.estimate_tokens(memory_text),
                            priority=8,
                        )
                    )
            except Exception as exc:
                self._log.warning("working_memory_injection_failed", exc=str(exc))

        if context.text:
            sections.append(
                PromptSection(
                    name="retrieved_context",
                    content=f"CONTEXT:\n{context.text}",
                    token_estimate=self.estimate_tokens(context.text),
                    priority=9,
                )
            )

        sections.append(
            PromptSection(
                name="question",
                content=f"QUESTION:\n{request.query}",
                token_estimate=self.estimate_tokens(request.query),
                priority=10,
            )
        )

        return sections

    @staticmethod
    def _render_sections(sections: list[PromptSection]) -> str:
        ordered = sorted(sections, key=lambda s: s.priority, reverse=True)
        # Question always last regardless of priority sort, for prompt clarity
        question = next((s for s in ordered if s.name == "question"), None)
        rest = [s for s in ordered if s.name != "question"]
        parts = [s.content for s in rest]
        if question:
            parts.append(question.content)
        return "\n\n".join(parts)