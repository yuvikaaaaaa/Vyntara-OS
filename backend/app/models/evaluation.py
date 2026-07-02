"""
Intelligence Operating System — Evaluation Models
==================================================
ORM models for the quality measurement and experiment tracking domain:

``Evaluation``  — A single evaluation run attached to an ``AgentTask``.
                  Stores the full metric suite and links to an MLflow run.
``Benchmark``   — A named benchmark dataset used for offline evaluation.
                  Stores the canonical ground-truth Q&A pairs or task specs
                  against which IOS is scored.
``Feedback``    — Explicit human feedback (thumbs up/down, star rating,
                  free-text critique) attached to a ``Message`` or task.

Design decisions:
- ``Evaluation`` rows are created automatically by the ``EvaluationAgent``
  after task completion.  They are read by the evaluation dashboard and the
  MLflow integration.
- Individual metric values are stored as a flat JSONB ``metrics_json`` dict
  rather than typed columns because the metric set evolves as new evaluation
  techniques are added.  A ``metric_version`` field identifies which metric
  schema version was used.
- ``Benchmark`` holds the test suite definition only.  Results from running
  IOS against a benchmark are stored as ``Evaluation`` rows with
  ``benchmark_id`` set.
- ``Feedback`` is the primary source of RLHF-style human signal.  It can
  be attached at the message level (granular) or task level (aggregate).

Cascade policy:
    Evaluation → AgentTask    (cascade delete)
    Feedback   → Message      (SET NULL — feedback survives message deletion)
    Feedback   → AgentTask    (SET NULL)
    Feedback   → User         (SET NULL — preserve feedback after user deletion)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import EvaluationRunType, EvaluationStatus
from app.models.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.agent import AgentTask
    from app.models.conversation import Message
    from app.models.user import User


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class Evaluation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A quality evaluation run for an ``AgentTask`` or benchmark comparison.

    Created by the ``EvaluationAgent`` after task completion, or triggered
    manually by operators via the evaluation API.

    Metric storage:
        All metrics are stored in ``metrics_json`` as a flat dict, e.g.::

            {
              "rouge_l": 0.72,
              "bert_score_f1": 0.81,
              "hallucination_rate": 0.05,
              "retrieval_ndcg": 0.68,
              "retrieval_mrr": 0.74,
              "answer_relevance": 0.89,
              "confidence_calibration_error": 0.12
            }

        The ``metric_version`` field (e.g. ``"v1.2"``) records which set of
        metrics were computed so that dashboards can handle schema evolution.

    MLflow integration:
        ``mlflow_run_id`` links this record to the corresponding MLflow run
        where full metric history, artifacts, and parameters are stored.
        If MLflow is unavailable at evaluation time, this field is NULL and
        metrics are stored only in ``metrics_json``.
    """

    __tablename__ = "evaluations"
    __table_args__ = (
        Index("ix_evaluations_task_id", "task_id"),
        Index("ix_evaluations_run_type", "run_type"),
        Index("ix_evaluations_status", "status"),
        Index("ix_evaluations_created_at", "created_at"),
        Index("ix_evaluations_benchmark_id", "benchmark_id"),
        Index("ix_evaluations_mlflow_run_id", "mlflow_run_id"),
        {"comment": "Quality evaluation runs for agent tasks and benchmarks."},
    )

    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=True,
        doc="Task being evaluated (NULL for stand-alone benchmark runs).",
    )
    benchmark_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("benchmarks.id", ondelete="SET NULL"),
        nullable=True,
        doc="If this is a benchmark run, links to the benchmark definition.",
    )
    run_type: Mapped[EvaluationRunType] = mapped_column(
        SAEnum(EvaluationRunType, name="evaluation_run_type_enum", create_type=True),
        nullable=False,
        doc="What was evaluated: rag, agent, hallucination, confidence, or full.",
    )
    status: Mapped[EvaluationStatus] = mapped_column(
        SAEnum(EvaluationStatus, name="evaluation_status_enum", create_type=True),
        nullable=False,
        default=EvaluationStatus.RUNNING,
        server_default=text("'running'"),
    )
    # Metrics
    metrics_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Full metric result dict keyed by metric name.",
    )
    metric_version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="v1",
        server_default=text("'v1'"),
        doc="Metric schema version for backward-compatible dashboards.",
    )
    # Key headline scores (denormalised for fast filtering and sorting)
    overall_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Composite overall quality score (0.0–1.0).",
    )
    retrieval_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Retrieval quality sub-score (NDCG / MRR average).",
    )
    generation_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Generation quality sub-score (ROUGE-L / BERTScore average).",
    )
    hallucination_rate: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Fraction of claims flagged as potentially hallucinated (0.0–1.0).",
    )
    confidence_calibration_error: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Expected Calibration Error between predicted and actual confidence.",
    )
    # Comparison baseline
    baseline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("evaluations.id", ondelete="SET NULL"),
        nullable=True,
        doc="Previous evaluation run used as the comparison baseline.",
    )
    score_delta: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Difference in overall_score vs the baseline (positive = improvement).",
    )
    # MLflow integration
    mlflow_run_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="MLflow run ID where full metric history and artifacts are stored.",
    )
    mlflow_experiment_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    mlflow_artifact_uri: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="URI prefix for MLflow artifacts (evaluation reports, charts).",
    )
    # Pipeline configuration snapshot (what was evaluated)
    pipeline_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="IOS version tag at time of evaluation.",
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    reranker_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    llm_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    prompt_versions: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Snapshot of prompt template versions used during the evaluated task.",
    )
    # Timing
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Error detail if status is FAILED.",
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Operator notes attached to this evaluation run.",
    )
    extra_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    task: Mapped["AgentTask | None"] = relationship(
        "AgentTask",
        back_populates="evaluations",
    )
    benchmark: Mapped["Benchmark | None"] = relationship(
        "Benchmark",
        back_populates="evaluations",
    )
    baseline: Mapped["Evaluation | None"] = relationship(
        "Evaluation",
        foreign_keys=[baseline_run_id],
        remote_side="Evaluation.id",
        doc="The baseline evaluation this run is compared against.",
    )

    def __repr__(self) -> str:
        return (
            f"<Evaluation id={self.id} type={self.run_type} "
            f"status={self.status} score={self.overall_score}>"
        )


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


class Benchmark(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    A named benchmark dataset used for systematic offline evaluation.

    A benchmark defines a curated set of test cases — each case has an input
    (question, task description, or document) and ground-truth expected output.
    IOS is run against the inputs and the outputs are scored against the
    ground truth using the ``EvaluationAgent`` pipeline.

    ``test_cases_json`` structure::

        [
          {
            "id": "tc_001",
            "input": "What is the capital of France?",
            "expected_output": "Paris",
            "context_docs": ["doc-uuid-1"],
            "tags": ["geography", "factual"]
          },
          ...
        ]

    Versioning:
        When test cases change, the ``version`` is incremented and a new
        ``Benchmark`` record is created.  Old records are soft-deleted.
        ``Evaluation`` rows retain their ``benchmark_id`` pointer to the
        exact version that was evaluated.
    """

    __tablename__ = "benchmarks"
    __table_args__ = (
        Index("ix_benchmarks_name", "name"),
        Index("ix_benchmarks_benchmark_type", "benchmark_type"),
        Index("ix_benchmarks_is_active", "is_active"),
        UniqueConstraint("name", "version", name="uq_benchmarks_name_version"),
        {"comment": "Named benchmark datasets for offline quality evaluation."},
    )

    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        doc="Human-readable benchmark name (e.g. 'RAG Factuality v1').",
    )
    version: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="1.0",
        doc="Version tag (semver or date-based).",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="What this benchmark measures and how it should be interpreted.",
    )
    benchmark_type: Mapped[EvaluationRunType] = mapped_column(
        SAEnum(EvaluationRunType, name="evaluation_run_type_enum_bm", create_type=False),
        nullable=False,
        doc="Evaluation type: rag, agent, hallucination, confidence, or full.",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="Only active benchmarks appear in the evaluation UI.",
    )
    # Test cases
    test_cases_json: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
        doc="Array of test case objects (input, expected_output, context_docs, tags).",
    )
    test_case_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Denormalised count of test_cases_json array length.",
    )
    # Scoring configuration
    primary_metric: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="overall_score",
        doc="The metric used to rank runs of this benchmark.",
    )
    passing_threshold: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Minimum primary_metric value to consider the run 'passing'.",
    )
    # Best known score (updated whenever a new top score is set)
    best_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Best primary_metric score achieved across all evaluation runs.",
    )
    best_score_evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("evaluations.id", ondelete="SET NULL"),
        nullable=True,
        doc="Evaluation run that achieved best_score.",
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
    )
    extra_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    evaluations: Mapped[list["Evaluation"]] = relationship(
        "Evaluation",
        back_populates="benchmark",
        foreign_keys="Evaluation.benchmark_id",
        doc="All evaluation runs against this benchmark.",
    )

    def __repr__(self) -> str:
        return (
            f"<Benchmark name={self.name!r} v{self.version} "
            f"cases={self.test_case_count} best={self.best_score}>"
        )


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


class Feedback(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Explicit human feedback signal on a ``Message`` or ``AgentTask``.

    Feedback is the primary source of RLHF-style human preference signal
    within IOS.  It can be collected at two granularities:

    1. **Message-level** (``message_id`` set) — thumbs up/down or star rating
       on an individual assistant response.
    2. **Task-level** (``task_id`` set, ``message_id`` NULL) — aggregate
       rating of the entire task completion.

    ``sentiment`` encodes the coarse direction:
        ``positive``  — user found the output helpful
        ``negative``  — user found the output unhelpful, wrong, or harmful
        ``neutral``   — user provided a neutral rating (e.g. 3/5 stars)

    ``categories`` allows users to tag *why* feedback is negative:
        ``["hallucination", "incomplete", "off_topic", "format", "slow"]``

    Feedback rows are immutable once created — users cannot edit feedback,
    only add new feedback entries.  The ``is_reviewed`` flag allows operators
    to mark feedback that has been actioned.
    """

    __tablename__ = "feedback"
    __table_args__ = (
        Index("ix_feedback_user_id", "user_id"),
        Index("ix_feedback_message_id", "message_id"),
        Index("ix_feedback_task_id", "task_id"),
        Index("ix_feedback_sentiment", "sentiment"),
        Index("ix_feedback_created_at", "created_at"),
        Index("ix_feedback_is_reviewed", "is_reviewed"),
        {"comment": "Explicit human feedback signals for RLHF and quality improvement."},
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="User who provided the feedback (SET NULL if user is deleted).",
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        doc="Specific message being rated (NULL for task-level feedback).",
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_tasks.id", ondelete="SET NULL"),
        nullable=True,
        doc="Task being rated (always set; message_id narrows to specific turn).",
    )
    # Rating signals
    thumbs_up: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        doc="Simple thumbs-up (True) / thumbs-down (False) signal. NULL = not given.",
    )
    star_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="1–5 star rating. NULL = not given.",
    )
    sentiment: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="neutral",
        server_default=text("'neutral'"),
        doc="Derived sentiment: 'positive', 'negative', 'neutral'.",
    )
    # Categorisation
    categories: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="Feedback tags: 'hallucination', 'incomplete', 'off_topic', 'format', 'slow', 'harmful'.",
    )
    # Free-text
    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional free-text comment from the user.",
    )
    corrected_output: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="If the user provides a corrected version of the output, stored here.",
    )
    # Operator review
    is_reviewed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True once an operator has reviewed and actioned this feedback.",
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    review_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    # Source context
    feedback_source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="ui",
        server_default=text("'ui'"),
        doc="How feedback was collected: 'ui', 'api', 'annotation_tool'.",
    )
    extra_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[user_id],
    )
    message: Mapped["Message | None"] = relationship(
        "Message",
    )

    def __repr__(self) -> str:
        rating = (
            f"{'👍' if self.thumbs_up else '👎'}"
            if self.thumbs_up is not None
            else f"{self.star_rating}⭐" if self.star_rating else "?"
        )
        return (
            f"<Feedback id={self.id} sentiment={self.sentiment} "
            f"rating={rating} reviewed={self.is_reviewed}>"
        )