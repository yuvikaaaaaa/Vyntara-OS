"""IOS RAG — RAG Pipeline."""
from __future__ import annotations

import time

from app.ai_core.router.model_router import ModelRouter
from app.ai_core.types import ChatMessage, ChatRequest, GenerationConfig
from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.rag.citation_manager import CitationManager
from app.rag.context_optimizer import ContextOptimizer
from app.rag.exceptions import GenerationFailedError, NoContextAvailableError, RAGPipelineError
from app.rag.grounding import Grounding
from app.rag.hallucination_checker import HallucinationChecker
from app.rag.interfaces import (
    ICitationManager,
    IContextOptimizer,
    IGroundingChecker,
    IHallucinationChecker,
    IPromptAssembler,
    IResponseValidator,
)
from app.rag.prompt_assembler import PromptAssembler
from app.rag.response_builder import ResponseBuilder
from app.rag.response_validator import ResponseValidator
from app.rag.types import RAGRequest, RAGResponse
from app.retrieval.context_builder import ContextBuilder as RetrievalContextBuilder
from app.retrieval.retrieval_manager import RetrievalManager
from app.retrieval.types import ContextBudget, RetrievalRequest, RetrievalSource

logger = get_logger(__name__)


class RAGPipeline:
    """
    Single orchestration entry point for the complete Retrieval-Augmented
    Generation flow.

    Pipeline stages:
      1. Retrieve raw candidates via RetrievalManager (Retrieval Engine)
      2. Optimize context (dedupe / compress) via ContextOptimizer
      3. Build the final token-budgeted context (delegated back into
         RetrievalManager's ContextBuilder through retrieve_with_context,
         or built directly here from optimized items)
      4. Assemble the grounded prompt via PromptAssembler
      5. Generate via AI Core's ModelRouter
      6. Check grounding via Grounding
      7. Inject citations via CitationManager
      8. Check for hallucinations via HallucinationChecker
      9. Validate the final response via ResponseValidator
      10. Build the final RAGResponse via ResponseBuilder

    Every stage communicates through an interface — RAGPipeline itself
    contains no retrieval logic, no LLM-calling logic beyond dispatch to
    ModelRouter, and no SQL/HTTP.  It coordinates only.
    """

    def __init__(
        self,
        retrieval_manager: RetrievalManager,
        model_router: ModelRouter,
        *,
        prompt_assembler: IPromptAssembler | None = None,
        context_optimizer: IContextOptimizer | None = None,
        grounding_checker: IGroundingChecker | None = None,
        citation_manager: ICitationManager | None = None,
        hallucination_checker: IHallucinationChecker | None = None,
        response_validator: IResponseValidator | None = None,
        response_builder: ResponseBuilder | None = None,
    ) -> None:
        self._retrieval = retrieval_manager
        self._router = model_router
        self._prompt_assembler = prompt_assembler or PromptAssembler()
        self._context_optimizer = context_optimizer or ContextOptimizer()
        self._grounding = grounding_checker or Grounding()
        self._citations = citation_manager or CitationManager()
        self._hallucination = hallucination_checker or HallucinationChecker(self._grounding)
        self._validator = response_validator or ResponseValidator()
        self._builder = response_builder or ResponseBuilder()
        self._retrieval_context_builder = RetrievalContextBuilder()
        self._log = logger

    # ------------------------------------------------------------------
    # Primary pipeline API
    # ------------------------------------------------------------------

    async def run(self, request: RAGRequest) -> RAGResponse:
        """
        Execute the complete RAG pipeline for a single request.

        Never raises for recoverable conditions (no context found, weak
        grounding) — instead returns a degraded-but-valid RAGResponse via
        ResponseBuilder.build_error_response().  Only raises RAGPipelineError
        for truly unexpected failures.
        """
        async with create_async_span(
            "rag_pipeline.run", attributes={"query_len": str(len(request.query))}
        ):
            start = time.perf_counter()
            try:
                retrieval_response, built_context = await self._retrieve_and_optimize(request)

                if not built_context.included_items:
                    self._log.warning("rag_no_context_available", query=request.query[:80])
                    return self._builder.build_error_response(
                        request=request,
                        error_message=(
                            "I don't have enough information in the available "
                            "context to answer this question."
                        ),
                        latency_ms=self._elapsed_ms(start),
                    )

                prompt = await self._prompt_assembler.assemble(request, built_context)

                chat_response = await self._generate(prompt, request)

                grounding_result = None
                if request.hallucination_check or request.require_citations:
                    grounding_result = await self._grounding.check_grounding(
                        chat_response.content, built_context
                    )

                cited_response = await self._citations.inject_citations(
                    chat_response.content, built_context, grounding_result
                )

                hallucination_report = None
                if request.hallucination_check:
                    hallucination_report = await self._hallucination.check(
                        cited_response.text, built_context, grounding_result
                    )

                validation_result = self._validator.validate(
                    cited_response.text, cited_response, request
                )

                response = self._builder.build(
                    request=request,
                    chat_response=chat_response,
                    cited_response=cited_response,
                    grounding=grounding_result,
                    hallucination=hallucination_report,
                    validation=validation_result,
                    latency_ms=self._elapsed_ms(start),
                    context_items_used=len(built_context.included_items),
                )
                self._log.info(
                    "rag_pipeline_complete",
                    query_len=len(request.query),
                    latency_ms=response.latency_ms,
                    confidence=round(response.confidence_score, 3),
                )
                return response

            except (NoContextAvailableError, GenerationFailedError) as exc:
                self._log.warning("rag_pipeline_recoverable_failure", exc=str(exc))
                return self._builder.build_error_response(
                    request=request,
                    error_message=f"Unable to generate a grounded response: {exc.message}",
                    latency_ms=self._elapsed_ms(start),
                )
            except Exception as exc:
                raise RAGPipelineError(f"RAG pipeline failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal stages
    # ------------------------------------------------------------------

    async def _retrieve_and_optimize(self, request: RAGRequest):
        sources = [RetrievalSource.VECTOR, RetrievalSource.GRAPH]
        retrieval_request = RetrievalRequest(
            query=request.query,
            user_id=request.user_id,
            sources=sources,
            top_k=20,
            max_context_tokens=request.max_context_tokens,
        )
        retrieval_response = await self._retrieval.retrieve(retrieval_request)

        budget = ContextBudget(max_tokens=request.max_context_tokens, reserve_tokens=256)
        optimized_items = await self._context_optimizer.optimize(
            retrieval_response.items, budget, request.query
        )

        # Assemble the final token-budgeted text/citations from the
        # optimized item set using our own ContextBuilder instance —
        # RAG communicates only through Retrieval's public interfaces,
        # never through RetrievalManager's internal state.
        built_context = await self._retrieval_context_builder.build(
            optimized_items, budget
        )
        return retrieval_response, built_context

    async def _generate(self, prompt, request: RAGRequest):
        chat_request = ChatRequest(
            messages=[ChatMessage(role="user", content=prompt.user_prompt)],
            model_id=request.model_id or "",
            system_prompt=prompt.system_prompt,
            config=GenerationConfig(
                temperature=0.3,
                max_tokens=request.max_response_tokens,
            ),
            timeout_seconds=120.0,
        )
        try:
            return await self._router.chat(chat_request)
        except Exception as exc:
            raise GenerationFailedError(
                f"Generation failed: {exc}",
                details={"model_id": request.model_id},
            ) from exc

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.perf_counter() - start) * 1000)