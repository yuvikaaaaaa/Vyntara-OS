"""IOS — Configuration Schemas."""
from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.core.enums import AgentType, ModelProvider, ModelTier
from app.schemas.base import AppModel, AuditedSchema, OrmModel


# ---------------------------------------------------------------------------
# PromptTemplate
# ---------------------------------------------------------------------------


class PromptTemplateCreate(AppModel):
    template_key: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    agent_type: AgentType | None = None
    pipeline_component: str | None = Field(default=None, max_length=100)
    system_prompt: str | None = None
    user_prompt_template: str = Field(min_length=1)
    assistant_prefix: str | None = None
    variables_schema: dict = Field(default_factory=dict)
    few_shot_examples: list = Field(default_factory=list)
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    default_top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    default_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    change_notes: str | None = None
    source_file: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list)
    extra_config: dict = Field(default_factory=dict)


class PromptTemplateUpdate(AppModel):
    display_name: str | None = Field(default=None, max_length=200)
    description: str | None = None
    system_prompt: str | None = None
    user_prompt_template: str | None = None
    assistant_prefix: str | None = None
    variables_schema: dict | None = None
    few_shot_examples: list | None = None
    default_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    default_top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    default_max_tokens: int | None = Field(default=None, ge=1, le=32768)
    is_enabled: bool | None = None
    change_notes: str | None = None
    tags: list[str] | None = None


class PromptTemplateRead(AuditedSchema):
    id: UUID
    template_key: str
    display_name: str
    description: str
    agent_type: AgentType | None
    pipeline_component: str | None
    version: int
    is_current: bool
    is_enabled: bool
    system_prompt: str | None
    user_prompt_template: str
    assistant_prefix: str | None
    variables_schema: dict
    few_shot_examples: list
    default_temperature: float
    default_top_p: float
    default_max_tokens: int | None
    created_by: UUID | None
    change_notes: str | None
    source_file: str | None
    avg_quality_score: float | None
    avg_token_count: float | None
    use_count: int
    tags: list[str]


class PromptTemplateSummary(OrmModel):
    id: UUID
    template_key: str
    display_name: str
    agent_type: AgentType | None
    version: int
    is_current: bool
    is_enabled: bool
    avg_quality_score: float | None
    use_count: int


class PromptRenderRequest(AppModel):
    """Preview a rendered prompt by substituting variables."""

    template_id: UUID
    variables: dict = Field(default_factory=dict)


class PromptRenderResponse(AppModel):
    template_key: str
    version: int
    rendered_system: str | None
    rendered_user: str


# ---------------------------------------------------------------------------
# ModelConfiguration
# ---------------------------------------------------------------------------


class ModelConfigCreate(AppModel):
    slot_name: str = Field(min_length=1, max_length=100)
    tier: ModelTier
    provider: ModelProvider
    model_id: str = Field(min_length=1, max_length=200)
    model_display_name: str | None = Field(default=None, max_length=200)
    model_version: str | None = Field(default=None, max_length=50)
    is_default: bool = False
    ab_weight: float = Field(default=1.0, ge=0.0, le=1.0)
    priority: int = Field(default=10, ge=1)
    context_window_tokens: int = Field(default=8192, ge=512)
    max_output_tokens: int | None = None
    default_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    default_top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    default_top_k: int | None = None
    repeat_penalty: float | None = None
    capabilities: list[str] = Field(default_factory=list)
    supported_languages: list[str] = Field(default_factory=list)
    ollama_base_url: str | None = Field(default=None, max_length=500)
    request_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    keep_alive: str = Field(default="5m", max_length=20)
    notes: str | None = None
    extra_params: dict = Field(default_factory=dict)


class ModelConfigUpdate(AppModel):
    slot_name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None
    is_default: bool | None = None
    ab_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    priority: int | None = Field(default=None, ge=1)
    default_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    default_top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    capabilities: list[str] | None = None
    request_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    keep_alive: str | None = Field(default=None, max_length=20)
    notes: str | None = None
    extra_params: dict | None = None


class ModelConfigRead(AuditedSchema):
    id: UUID
    slot_name: str
    tier: ModelTier
    provider: ModelProvider
    model_id: str
    model_display_name: str | None
    model_version: str | None
    is_active: bool
    is_default: bool
    ab_weight: float
    priority: int
    context_window_tokens: int
    max_output_tokens: int | None
    default_temperature: float
    default_top_p: float
    default_top_k: int | None
    repeat_penalty: float | None
    capabilities: list[str]
    supported_languages: list[str]
    ollama_base_url: str | None
    request_timeout_seconds: int
    keep_alive: str
    avg_latency_ms: float | None
    avg_tokens_per_second: float | None
    total_requests: int
    error_rate: float
    last_health_status: str | None
    notes: str | None


class ModelConfigSummary(OrmModel):
    id: UUID
    slot_name: str
    tier: ModelTier
    model_id: str
    is_active: bool
    is_default: bool
    ab_weight: float
    avg_latency_ms: float | None
    error_rate: float
    last_health_status: str | None