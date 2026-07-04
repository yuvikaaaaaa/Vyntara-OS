"""IOS — Evaluation Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import EvaluationRunType, EvaluationStatus
from app.core.exceptions import NotFoundError
from app.models.evaluation import Benchmark, Evaluation, Feedback
from app.schemas.evaluation import (
    BenchmarkCreate,
    BenchmarkUpdate,
    EvaluationCreate,
    EvaluationMetricsUpdate,
    FeedbackCreate,
    FeedbackReview,
)
from app.services.base import BaseService


class EvaluationService(BaseService):
    """Orchestrates evaluation runs, benchmarks, and human feedback."""

    # ------------------------------------------------------------------
    # Evaluation runs
    # ------------------------------------------------------------------

    async def create_evaluation(
        self, data: EvaluationCreate
    ) -> Evaluation:
        async with self._span("create_evaluation"):
            async with self._transaction() as uow:
                eval_run = Evaluation(
                    task_id=data.task_id,
                    benchmark_id=data.benchmark_id,
                    run_type=data.run_type,
                    status=EvaluationStatus.RUNNING,
                    pipeline_version=data.pipeline_version,
                    embedding_model=data.embedding_model,
                    reranker_model=data.reranker_model,
                    llm_model=data.llm_model,
                    prompt_versions=data.prompt_versions,
                    baseline_run_id=data.baseline_run_id,
                    metric_version="v1",
                )
                saved = await uow.evaluations.create(eval_run)
                self._log.info(
                    "evaluation_created",
                    eval_id=str(saved.id),
                    run_type=data.run_type,
                )
                return saved

    async def get_evaluation(self, eval_id: UUID) -> Evaluation:
        async with self._transaction() as uow:
            ev = await uow.evaluations.get_by_id(eval_id)
            if not ev:
                raise NotFoundError("Evaluation not found.")
            return ev

    async def update_metrics(
        self, eval_id: UUID, data: EvaluationMetricsUpdate
    ) -> Evaluation:
        async with self._span("update_metrics", eval_id=str(eval_id)):
            async with self._transaction() as uow:
                ev = await uow.evaluations.get_by_id(eval_id, raise_not_found=True)
                updates = data.model_dump(exclude_none=True)
                await uow.evaluations.update_metrics(eval_id, updates)

                # Update benchmark best score if applicable
                if (
                    ev.benchmark_id
                    and data.overall_score is not None
                    and data.status == EvaluationStatus.COMPLETE
                ):
                    bm = await uow.evaluations._session.get(Benchmark, ev.benchmark_id)
                    if bm and (bm.best_score is None or data.overall_score > bm.best_score):
                        await uow.evaluations.update_best_score(
                            ev.benchmark_id, data.overall_score, eval_id
                        )
                await uow.evaluations.flush()
                return await uow.evaluations.get_by_id(eval_id)

    async def list_for_task(self, task_id: UUID) -> list[Evaluation]:
        async with self._transaction() as uow:
            return await uow.evaluations.list_for_task(task_id)

    async def list_by_type(
        self,
        run_type: EvaluationRunType,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Evaluation], int]:
        async with self._transaction() as uow:
            return await uow.evaluations.list_by_type(
                run_type, page=page, page_size=page_size
            )

    async def get_average_scores_by_model(self) -> list[dict]:
        async with self._transaction() as uow:
            return await uow.evaluations.average_score_by_model()

    # ------------------------------------------------------------------
    # Benchmarks
    # ------------------------------------------------------------------

    async def create_benchmark(self, data: BenchmarkCreate) -> Benchmark:
        async with self._span("create_benchmark"):
            async with self._transaction() as uow:
                existing = await uow.evaluations.get_benchmark_by_name_version(
                    data.name, data.version
                )
                if existing:
                    raise NotFoundError(
                        f"Benchmark '{data.name}' v{data.version} already exists."
                    )
                bm = Benchmark(
                    name=data.name,
                    version=data.version,
                    description=data.description,
                    benchmark_type=data.benchmark_type,
                    test_cases_json=data.test_cases_json,
                    test_case_count=len(data.test_cases_json),
                    primary_metric=data.primary_metric,
                    passing_threshold=data.passing_threshold,
                    tags=data.tags,
                    extra_config=data.extra_config,
                )
                saved = await uow.evaluations.create(bm)
                return saved

    async def get_benchmark(self, benchmark_id: UUID) -> Benchmark:
        async with self._transaction() as uow:
            bm = await uow.evaluations._session.get(Benchmark, benchmark_id)
            if not bm or bm.is_deleted:
                raise NotFoundError("Benchmark not found.")
            return bm

    async def list_benchmarks(
        self,
        run_type: EvaluationRunType | None = None,
        active_only: bool = True,
    ) -> list[Benchmark]:
        async with self._transaction() as uow:
            if active_only:
                return await uow.evaluations.list_active_benchmarks(run_type)
            filters = []
            if run_type:
                filters.append(Benchmark.benchmark_type == run_type)
            return await uow.evaluations._session.scalars(
                __import__("sqlalchemy").select(Benchmark).where(*filters)
            ).then(list)

    async def update_benchmark(
        self, benchmark_id: UUID, data: BenchmarkUpdate
    ) -> Benchmark:
        async with self._transaction() as uow:
            bm = await uow.evaluations._session.get(Benchmark, benchmark_id)
            if not bm or bm.is_deleted:
                raise NotFoundError("Benchmark not found.")
            updates = data.model_dump(exclude_none=True)
            for k, v in updates.items():
                setattr(bm, k, v)
            await uow.evaluations.flush()
            return bm

    async def delete_benchmark(self, benchmark_id: UUID) -> None:
        async with self._transaction() as uow:
            bm = await uow.evaluations._session.get(Benchmark, benchmark_id)
            if not bm:
                raise NotFoundError("Benchmark not found.")
            bm.soft_delete()
            await uow.evaluations.flush()

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def submit_feedback(
        self, user_id: UUID, data: FeedbackCreate
    ) -> Feedback:
        """Derive sentiment from thumbs/star rating and persist."""
        async with self._span("submit_feedback"):
            sentiment = self._derive_sentiment(data.thumbs_up, data.star_rating)
            async with self._transaction() as uow:
                fb = Feedback(
                    user_id=user_id,
                    message_id=data.message_id,
                    task_id=data.task_id,
                    thumbs_up=data.thumbs_up,
                    star_rating=data.star_rating,
                    sentiment=sentiment,
                    categories=data.categories,
                    comment=data.comment,
                    corrected_output=data.corrected_output,
                    feedback_source=data.feedback_source,
                )
                saved = await uow.evaluations.create_feedback(fb)
                self._log.info(
                    "feedback_submitted",
                    feedback_id=str(saved.id),
                    sentiment=sentiment,
                )
                return saved

    async def list_feedback(
        self,
        *,
        task_id: UUID | None = None,
        is_reviewed: bool | None = None,
        sentiment: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Feedback], int]:
        async with self._transaction() as uow:
            return await uow.evaluations.list_feedback(
                task_id=task_id,
                is_reviewed=is_reviewed,
                sentiment=sentiment,
                page=page,
                page_size=page_size,
            )

    async def review_feedback(
        self, feedback_id: UUID, reviewer_id: UUID, data: FeedbackReview
    ) -> Feedback:
        async with self._transaction() as uow:
            fb = await uow.evaluations._session.get(Feedback, feedback_id)
            if not fb:
                raise NotFoundError("Feedback not found.")
            await uow.evaluations.mark_reviewed(fb, reviewer_id, data.review_notes)
            return fb

    async def get_sentiment_summary(self) -> dict[str, int]:
        async with self._transaction() as uow:
            return await uow.evaluations.sentiment_summary()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_sentiment(
        thumbs_up: bool | None, star_rating: int | None
    ) -> str:
        if thumbs_up is True:
            return "positive"
        if thumbs_up is False:
            return "negative"
        if star_rating is not None:
            if star_rating >= 4:
                return "positive"
            if star_rating <= 2:
                return "negative"
        return "neutral"