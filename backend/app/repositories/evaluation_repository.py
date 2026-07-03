"""IOS — Evaluation Repository."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.core.enums import EvaluationRunType, EvaluationStatus
from app.models.evaluation import Benchmark, Evaluation, Feedback
from app.repositories.base import BaseRepository


class EvaluationRepository(BaseRepository[Evaluation]):
    model = Evaluation

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    async def list_for_task(self, task_id: UUID) -> list[Evaluation]:
        stmt = (
            select(Evaluation)
            .where(Evaluation.task_id == task_id)
            .order_by(Evaluation.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_type(
        self,
        run_type: EvaluationRunType,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Evaluation], int]:
        return await self.paginate(
            page=page,
            page_size=page_size,
            filters=[Evaluation.run_type == run_type],
            order_by=Evaluation.created_at,
            descending=True,
        )

    async def update_metrics(
        self, evaluation_id: UUID, values: dict
    ) -> None:
        stmt = (
            update(Evaluation)
            .where(Evaluation.id == evaluation_id)
            .values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_running_evaluations(self) -> list[Evaluation]:
        stmt = select(Evaluation).where(
            Evaluation.status == EvaluationStatus.RUNNING
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_for_task(self, task_id: UUID) -> Evaluation | None:
        stmt = (
            select(Evaluation)
            .where(Evaluation.task_id == task_id)
            .order_by(Evaluation.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def average_score_by_model(self) -> list[dict]:
        stmt = (
            select(
                Evaluation.llm_model,
                func.avg(Evaluation.overall_score).label("avg_score"),
                func.count().label("count"),
            )
            .where(
                Evaluation.status == EvaluationStatus.COMPLETE,
                Evaluation.overall_score.is_not(None),
                Evaluation.llm_model.is_not(None),
            )
            .group_by(Evaluation.llm_model)
            .order_by(func.avg(Evaluation.overall_score).desc())
        )
        result = await self._session.execute(stmt)
        return [
            {"llm_model": row.llm_model, "avg_score": row.avg_score, "count": row.count}
            for row in result.all()
        ]

    # ------------------------------------------------------------------
    # Benchmark
    # ------------------------------------------------------------------

    async def get_benchmark_by_name_version(
        self, name: str, version: str
    ) -> Benchmark | None:
        stmt = select(Benchmark).where(
            Benchmark.name == name,
            Benchmark.version == version,
            Benchmark.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_active_benchmarks(
        self,
        run_type: EvaluationRunType | None = None,
    ) -> list[Benchmark]:
        filters: list = [
            Benchmark.is_active.is_(True),
            Benchmark.deleted_at.is_(None),
        ]
        if run_type:
            filters.append(Benchmark.benchmark_type == run_type)
        stmt = (
            select(Benchmark)
            .where(and_(*filters))
            .order_by(Benchmark.name.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_best_score(
        self,
        benchmark_id: UUID,
        score: float,
        evaluation_id: UUID,
    ) -> None:
        stmt = (
            update(Benchmark)
            .where(Benchmark.id == benchmark_id)
            .values(best_score=score, best_score_evaluation_id=evaluation_id)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def create_feedback(self, feedback: Feedback) -> Feedback:
        self._session.add(feedback)
        await self._session.flush()
        await self._session.refresh(feedback)
        return feedback

    async def list_feedback(
        self,
        *,
        task_id: UUID | None = None,
        is_reviewed: bool | None = None,
        sentiment: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Feedback], int]:
        filters: list = []
        if task_id:
            filters.append(Feedback.task_id == task_id)
        if is_reviewed is not None:
            filters.append(Feedback.is_reviewed == is_reviewed)
        if sentiment:
            filters.append(Feedback.sentiment == sentiment)
        return await self.paginate(
            page=page,
            page_size=page_size,
            filters=filters or None,
            order_by=Feedback.created_at,
            descending=True,
        )

    async def mark_reviewed(
        self,
        feedback: Feedback,
        reviewed_by: UUID,
        notes: str | None,
    ) -> None:
        from datetime import datetime, timezone
        feedback.is_reviewed = True
        feedback.reviewed_by = reviewed_by
        feedback.reviewed_at = datetime.now(tz=timezone.utc)
        feedback.review_notes = notes
        await self._session.flush()

    async def sentiment_summary(self) -> dict[str, int]:
        stmt = (
            select(Feedback.sentiment, func.count().label("cnt"))
            .group_by(Feedback.sentiment)
        )
        result = await self._session.execute(stmt)
        return {row.sentiment: row.cnt for row in result.all()}