"""IOS — Evaluation & Feedback Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.core.enums import EvaluationRunType, EvaluationStatus
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class EvaluationCreate(AppModel):
    """Trigger an evaluation run (admin / evaluation service)."""

    task_id: UUID | None = None
    benchmark_id: UUID | None = None
    run_type: EvaluationRunType
    pipeline_version: str | None = Field(default=None, max_length=50)
    embedding_model: str | None = None
    reranker_model: str | None = None
    llm_model: str | None = None
    prompt_versions: dict = Field(default_factory=dict)
    baseline_run_id: UUID | None = None


class EvaluationRead(TimestampedSchema):
    id: UUID
    task_id: UUID | None
    benchmark_id: UUID | None
    run_type: EvaluationRunType
    status: EvaluationStatus
    metrics_json: dict
    metric_version: str
    overall_score: float | None
    retrieval_score: float | None
    generation_score: float | None
    hallucination_rate: float | None
    confidence_calibration_error: float | None
    baseline_run_id: UUID | None
    score_delta: float | None
    mlflow_run_id: str | None
    mlflow_experiment_name: str | None
    mlflow_artifact_uri: str | None
    pipeline_version: str | None
    embedding_model: str | None
    reranker_model: str | None
    llm_model: str | None
    prompt_versions: dict
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    error_message: str | None
    notes: str | None


class EvaluationSummary(OrmModel):
    id: UUID
    run_type: EvaluationRunType
    status: EvaluationStatus
    overall_score: float | None
    hallucination_rate: float | None
    mlflow_run_id: str | None
    created_at: datetime


class EvaluationMetricsUpdate(AppModel):
    """Internal: EvaluationAgent pushes metrics after computation."""

    status: EvaluationStatus
    metrics_json: dict = Field(default_factory=dict)
    overall_score: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_score: float | None = Field(default=None, ge=0.0, le=1.0)
    generation_score: float | None = Field(default=None, ge=0.0, le=1.0)
    hallucination_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_calibration_error: float | None = None
    score_delta: float | None = None
    mlflow_run_id: str | None = None
    mlflow_artifact_uri: str | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


class BenchmarkCreate(AppModel):
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(default="1.0", max_length=20)
    description: str = Field(min_length=1)
    benchmark_type: EvaluationRunType
    test_cases_json: list = Field(default_factory=list)
    primary_metric: str = Field(default="overall_score", max_length=100)
    passing_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    extra_config: dict = Field(default_factory=dict)

    @field_validator("test_cases_json")
    @classmethod
    def non_empty_cases(cls, v: list) -> list:
        if not v:
            raise ValueError("test_cases_json must contain at least one test case.")
        return v


class BenchmarkUpdate(AppModel):
    description: str | None = None
    is_active: bool | None = None
    passing_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] | None = None


class BenchmarkRead(AuditedSchema):
    id: UUID
    name: str
    version: str
    description: str
    benchmark_type: EvaluationRunType
    is_active: bool
    test_case_count: int
    primary_metric: str
    passing_threshold: float | None
    best_score: float | None
    best_score_evaluation_id: UUID | None
    tags: list[str]


class BenchmarkSummary(OrmModel):
    id: UUID
    name: str
    version: str
    benchmark_type: EvaluationRunType
    is_active: bool
    test_case_count: int
    best_score: float | None


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


class FeedbackCreate(AppModel):
    message_id: UUID | None = None
    task_id: UUID | None = None
    thumbs_up: bool | None = None
    star_rating: int | None = Field(default=None, ge=1, le=5)
    categories: list[str] = Field(default_factory=list)
    comment: str | None = Field(default=None, max_length=5000)
    corrected_output: str | None = None
    feedback_source: str = "ui"


class FeedbackRead(TimestampedSchema):
    id: UUID
    user_id: UUID | None
    message_id: UUID | None
    task_id: UUID | None
    thumbs_up: bool | None
    star_rating: int | None
    sentiment: str
    categories: list[str]
    comment: str | None
    corrected_output: str | None
    is_reviewed: bool
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    review_notes: str | None
    feedback_source: str


class FeedbackReview(AppModel):
    """Operator marks feedback as reviewed."""

    review_notes: str | None = Field(default=None, max_length=2000)