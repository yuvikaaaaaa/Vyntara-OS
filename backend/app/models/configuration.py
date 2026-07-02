"""
Intelligence Operating System — Configuration Models
=====================================================
ORM models for the system configuration domain:

``PromptTemplate``    — Versioned prompt templates for every agent and pipeline
                        component.  Each template has a name, a body with
                        ``{variable}`` placeholders, and a version history.
                        The active version is identified by ``is_current``.

``ModelConfiguration`` — Per-deployment model routing configuration.  Stores
                         which Ollama model handles each capability tier,
                         context-window limits, generation parameters
                         (temperature, top_p), and load-balancing weights.
                         Supports A/B testing via the ``ab_weight`` field.

Architecture alignment:
- ``PromptTemplate`` maps to the YAML files under
  ``intelligence/prompts/templates/`` in the SDD.  Those files are the
  source of truth at startup; the service layer syncs them into this table
  on first run and on version change.  Operators can then override values
  via the UI without touching YAML.
- ``ModelConfiguration`` provides runtime overrides for the ``ModelRouter``
  without requiring a code deploy.  The router reads the active configuration
  rows at startup (and caches with a 60-second TTL).
- Both models are soft-deleted via ``AuditMixin``; hard deletion is never
  performed (preserves configuration history).

Cascade policy:
    Neither model cascades — configurations are shared resources not owned
    by a single user or task.  Soft-delete via ``deleted_at``.
"""

from __future__ import annotations

import uuid
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

from app.core.enums import AgentType, ModelProvider, ModelTier
from app.models.base import AuditMixin, Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


# ---------------------------------------------------------------------------
# PromptTemplate
# ---------------------------------------------------------------------------


class PromptTemplate(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    A versioned prompt template for an agent or pipeline component.

    Versioning model:
        - ``template_key`` is the stable identifier (e.g. ``"planner_v1"``).
        - Each record has a ``version`` integer (1, 2, 3 …).
        - Exactly one record per ``template_key`` has ``is_current = True``.
        - Retiring a version: set ``is_current = False``; create a new
          record with incremented ``version`` and ``is_current = True``.
        - Old versions are soft-deleted after the configured grace period.

    Variable substitution:
        Template bodies use ``{variable_name}`` syntax.
        ``variables_schema`` is a JSON Schema describing required and optional
        variables, their types, and default values::

            {
              "type": "object",
              "properties": {
                "task_description": {"type": "string"},
                "memory_context":   {"type": "string", "default": ""}
              },
              "required": ["task_description"]
            }

    Performance tracking:
        ``avg_quality_score`` and ``avg_token_count`` are updated
        by the evaluation pipeline so that prompt improvements are
        measurable without opening MLflow.
    """

    __tablename__ = "prompt_templates"
    __table_args__ = (
        Index("ix_prompt_templates_template_key", "template_key"),
        Index("ix_prompt_templates_agent_type", "agent_type"),
        Index("ix_prompt_templates_is_current", "is_current"),
        Index("ix_prompt_templates_is_enabled", "is_enabled"),
        UniqueConstraint(
            "template_key", "version",
            name="uq_prompt_templates_key_version",
        ),
        {"comment": "Versioned prompt templates for agents and pipeline components."},
    )

    # Identity
    template_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Stable identifier for the template family, e.g. 'planner', 'reflection'.",
    )
    display_name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        doc="Human-readable name shown in the prompt management UI.",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="What this prompt does and which agent / pipeline uses it.",
    )
    # Agent association
    agent_type: Mapped[AgentType | None] = mapped_column(
        SAEnum(AgentType, name="agent_type_enum_pt", create_type=False),
        nullable=True,
        doc="Agent type this template belongs to (NULL for pipeline / RAG prompts).",
    )
    pipeline_component: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Pipeline component key for non-agent prompts "
            "(e.g. 'rag.query_expansion', 'rag.context_compression').",
    )
    # Versioning
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="True for the active version. Only one row per template_key should be current.",
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="Disabled templates are excluded from routing even if current.",
    )
    # Prompt body
    system_prompt: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="System-role prompt body (used for LLMs supporting system messages).",
    )
    user_prompt_template: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="User-role prompt body with {variable_name} placeholders.",
    )
    assistant_prefix: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Optional assistant-role prefix to steer generation (for few-shot).",
    )
    # Variable schema
    variables_schema: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="JSON Schema describing required/optional template variables.",
    )
    few_shot_examples: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
        doc="Optional few-shot examples injected into the prompt.",
    )
    # Generation parameters (can be overridden per agent call)
    default_temperature: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.7,
        server_default=text("0.7"),
    )
    default_top_p: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.9,
        server_default=text("0.9"),
    )
    default_max_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Maximum completion tokens. NULL = model default.",
    )
    # Authorship
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="User who created this template version.",
    )
    change_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="What changed in this version vs the previous.",
    )
    source_file: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Path to the source YAML file this was loaded from (for traceability).",
    )
    # Performance tracking (updated by evaluation pipeline)
    avg_quality_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Rolling average quality score from EvaluationAgent across all uses.",
    )
    avg_token_count: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Rolling average total token consumption per invocation.",
    )
    use_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Total number of times this template has been used.",
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
    creator: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by],
    )

    def __repr__(self) -> str:
        return (
            f"<PromptTemplate key={self.template_key!r} v{self.version} "
            f"current={self.is_current} agent={self.agent_type}>"
        )


# ---------------------------------------------------------------------------
# ModelConfiguration
# ---------------------------------------------------------------------------


class ModelConfiguration(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Per-deployment model routing configuration row.

    The ``ModelRouter`` reads all active ``ModelConfiguration`` rows at
    startup to build its routing table.  One row per logical model slot.

    Routing table design:
        - Each row represents one *slot* in the routing table, identified
          by the ``(tier, provider)`` combination.
        - The ``model_id`` is the Ollama or HuggingFace model identifier.
        - Multiple active rows with the same tier are load-balanced using
          ``ab_weight`` (higher weight = more traffic fraction).
        - ``is_active`` disables a model without deleting the row (e.g.
          temporarily pulling a model for maintenance).

    A/B testing:
        Create two rows with the same ``tier`` and different ``model_id``
        values.  Set ``ab_weight = 0.8`` on the control and ``ab_weight = 0.2``
        on the experiment.  The router samples proportionally.

    Soft-delete via ``AuditMixin`` preserves the configuration history.
    """

    __tablename__ = "model_configurations"
    __table_args__ = (
        Index("ix_model_configurations_tier", "tier"),
        Index("ix_model_configurations_provider", "provider"),
        Index("ix_model_configurations_is_active", "is_active"),
        Index("ix_model_configurations_model_id", "model_id"),
        {"comment": "Model routing configuration for the ModelRouter."},
    )

    # Slot identity
    slot_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Human-readable slot name, e.g. 'Primary Large', 'Code Specialist'.",
    )
    tier: Mapped[ModelTier] = mapped_column(
        SAEnum(ModelTier, name="model_tier_enum", create_type=True),
        nullable=False,
        doc="Capability tier this slot fills.",
    )
    provider: Mapped[ModelProvider] = mapped_column(
        SAEnum(ModelProvider, name="model_provider_enum", create_type=True),
        nullable=False,
        doc="Model serving provider.",
    )
    # Model identity
    model_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        doc="Provider-specific model identifier (e.g. 'llama3.1:70b', "
            "'BAAI/bge-large-en-v1.5').",
    )
    model_display_name: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        doc="Human-readable model name for UI display.",
    )
    model_version: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="Model version or revision tag.",
    )
    # Status
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="False = this slot is disabled; router skips it.",
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True for the fallback slot when no other routing rule matches.",
    )
    # Load balancing
    ab_weight: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
        server_default=text("1.0"),
        doc="Relative weight for A/B traffic splitting (0.0–1.0). "
            "Normalised across active slots with the same tier.",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=10,
        server_default=text("10"),
        doc="Lower value = higher priority when multiple slots match. "
            "Used for failover ordering.",
    )
    # Context window
    context_window_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=8192,
        server_default=text("8192"),
        doc="Maximum context window in tokens for this model.",
    )
    max_output_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Maximum output tokens (NULL = model default).",
    )
    # Default generation parameters
    default_temperature: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.7,
        server_default=text("0.7"),
    )
    default_top_p: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.9,
        server_default=text("0.9"),
    )
    default_top_k: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Top-K sampling parameter (NULL = model default).",
    )
    repeat_penalty: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Repetition penalty factor.",
    )
    seed: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Fixed seed for deterministic outputs (None = non-deterministic).",
    )
    # Capabilities declared by this model (used by capability-based routing)
    capabilities: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="Model capabilities: 'vision', 'code', 'math', 'long_context', 'json_mode'.",
    )
    supported_languages: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="ISO 639-1 language codes this model supports well.",
    )
    # Infrastructure
    ollama_base_url: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Override Ollama base URL for this slot (for multi-instance Ollama setups).",
    )
    request_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=300,
        server_default=text("300"),
        doc="HTTP request timeout for calls to this model.",
    )
    keep_alive: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="5m",
        server_default=text("'5m'"),
        doc="Ollama keep_alive parameter to prevent model unloading between calls.",
    )
    # Performance tracking (updated by ModelRouter)
    avg_latency_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Exponential moving average of first-token latency in ms.",
    )
    avg_tokens_per_second: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Exponential moving average of generation speed.",
    )
    total_requests: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    error_rate: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
        server_default=text("0.0"),
        doc="Rolling error rate (0.0–1.0). High rates trigger automatic failover.",
    )
    last_health_check_at: Mapped["datetime | None"] = mapped_column(  # type: ignore[name-defined]
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp of the last health check against this model.",
    )
    last_health_status: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        doc="Result of the last health check: 'healthy', 'degraded', 'unhealthy'.",
    )
    # Authorship
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Operator notes about this configuration slot.",
    )
    extra_params: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Provider-specific extra parameters passed verbatim to the model API.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    creator: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[created_by],
    )

    def __repr__(self) -> str:
        return (
            f"<ModelConfiguration id={self.id} tier={self.tier} "
            f"model={self.model_id!r} active={self.is_active} "
            f"weight={self.ab_weight}>"
        )


# Resolve forward-reference for datetime
from datetime import datetime as _dt  # noqa: E402
from sqlalchemy import DateTime  # noqa: E402

ModelConfiguration.last_health_check_at = mapped_column(  # type: ignore[assignment]
    DateTime(timezone=True),
    nullable=True,
)