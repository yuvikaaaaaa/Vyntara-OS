"""IOS — Configuration Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import AgentType, ModelTier
from app.core.exceptions import NotFoundError
from app.models.configuration import ModelConfiguration, PromptTemplate
from app.schemas.configuration import (
    ModelConfigCreate,
    ModelConfigUpdate,
    PromptRenderRequest,
    PromptRenderResponse,
    PromptTemplateCreate,
    PromptTemplateUpdate,
)
from app.services.base import BaseService


class ConfigurationService(BaseService):
    """Versioned prompt templates and model routing configuration."""

    # ------------------------------------------------------------------
    # PromptTemplate
    # ------------------------------------------------------------------

    async def create_template(
        self, data: PromptTemplateCreate, created_by: UUID
    ) -> PromptTemplate:
        async with self._span("create_template"):
            async with self._transaction() as uow:
                await uow.configuration.supersede(data.template_key)
                version = await uow.configuration.next_version(data.template_key)
                tmpl = PromptTemplate(
                    template_key=data.template_key,
                    display_name=data.display_name,
                    description=data.description,
                    agent_type=data.agent_type,
                    pipeline_component=data.pipeline_component,
                    version=version,
                    is_current=True,
                    is_enabled=True,
                    system_prompt=data.system_prompt,
                    user_prompt_template=data.user_prompt_template,
                    assistant_prefix=data.assistant_prefix,
                    variables_schema=data.variables_schema,
                    few_shot_examples=data.few_shot_examples,
                    default_temperature=data.default_temperature,
                    default_top_p=data.default_top_p,
                    default_max_tokens=data.default_max_tokens,
                    created_by=created_by,
                    change_notes=data.change_notes,
                    source_file=data.source_file,
                    tags=data.tags,
                    extra_config=data.extra_config,
                )
                saved = await uow.configuration.create(tmpl)
                self._log.info(
                    "prompt_template_created",
                    key=data.template_key,
                    version=version,
                )
                return saved

    async def get_current_template(self, template_key: str) -> PromptTemplate:
        async with self._transaction() as uow:
            tmpl = await uow.configuration.get_current(template_key)
            if not tmpl:
                raise NotFoundError(f"Prompt template '{template_key}' not found.")
            return tmpl

    async def get_template_by_version(
        self, template_key: str, version: int
    ) -> PromptTemplate:
        async with self._transaction() as uow:
            tmpl = await uow.configuration.get_by_key_version(template_key, version)
            if not tmpl:
                raise NotFoundError(
                    f"Prompt template '{template_key}' v{version} not found."
                )
            return tmpl

    async def list_templates(
        self,
        agent_type: AgentType | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[PromptTemplate], int]:
        async with self._transaction() as uow:
            return await uow.configuration.list_current_templates(
                agent_type=agent_type, page=page, page_size=page_size
            )

    async def list_versions(self, template_key: str) -> list[PromptTemplate]:
        async with self._transaction() as uow:
            return await uow.configuration.list_versions(template_key)

    async def update_template(
        self, template_id: UUID, data: PromptTemplateUpdate
    ) -> PromptTemplate:
        """Update fields on the current template version (no new version created)."""
        async with self._transaction() as uow:
            tmpl = await uow.configuration.get_by_id(template_id, raise_not_found=True)
            updates = data.model_dump(exclude_none=True)
            await uow.configuration.update(tmpl, updates)
            return tmpl

    async def delete_template(self, template_id: UUID) -> None:
        async with self._transaction() as uow:
            tmpl = await uow.configuration.get_by_id(template_id, raise_not_found=True)
            await uow.configuration.soft_delete(tmpl)

    async def render_template(self, data: PromptRenderRequest) -> PromptRenderResponse:
        """Render a template by substituting supplied variables (preview)."""
        async with self._transaction() as uow:
            tmpl = await uow.configuration.get_by_id(data.template_id, raise_not_found=True)

        def _render(text: str | None, variables: dict) -> str | None:
            if not text:
                return text
            try:
                return text.format_map(variables)
            except KeyError as e:
                raise NotFoundError(f"Missing template variable: {e}") from e

        return PromptRenderResponse(
            template_key=tmpl.template_key,
            version=tmpl.version,
            rendered_system=_render(tmpl.system_prompt, data.variables),
            rendered_user=_render(tmpl.user_prompt_template, data.variables) or "",
        )

    async def record_usage(
        self,
        template_id: UUID,
        quality_score: float,
        token_count: int,
    ) -> None:
        """Update usage stats after a template is used by an agent."""
        async with self._transaction() as uow:
            await uow.configuration.increment_use_count(template_id)
            await uow.configuration.update_performance(
                template_id, quality_score, token_count
            )

    # ------------------------------------------------------------------
    # ModelConfiguration
    # ------------------------------------------------------------------

    async def create_model_config(
        self, data: ModelConfigCreate, created_by: UUID
    ) -> ModelConfiguration:
        async with self._span("create_model_config"):
            async with self._transaction() as uow:
                if data.is_default:
                    await uow.configuration.clear_default_for_tier(data.tier)
                cfg = ModelConfiguration(
                    slot_name=data.slot_name,
                    tier=data.tier,
                    provider=data.provider,
                    model_id=data.model_id,
                    model_display_name=data.model_display_name,
                    model_version=data.model_version,
                    is_default=data.is_default,
                    ab_weight=data.ab_weight,
                    priority=data.priority,
                    context_window_tokens=data.context_window_tokens,
                    max_output_tokens=data.max_output_tokens,
                    default_temperature=data.default_temperature,
                    default_top_p=data.default_top_p,
                    default_top_k=data.default_top_k,
                    repeat_penalty=data.repeat_penalty,
                    capabilities=data.capabilities,
                    supported_languages=data.supported_languages,
                    ollama_base_url=data.ollama_base_url,
                    request_timeout_seconds=data.request_timeout_seconds,
                    keep_alive=data.keep_alive,
                    notes=data.notes,
                    extra_params=data.extra_params,
                    created_by=created_by,
                )
                saved = await uow.configuration.create_model_config(cfg)
                self._log.info(
                    "model_config_created",
                    slot=data.slot_name,
                    tier=data.tier,
                    model=data.model_id,
                )
                return saved

    async def get_model_config(self, config_id: UUID) -> ModelConfiguration:
        async with self._transaction() as uow:
            cfg = await uow.configuration.get_model_config(config_id)
            if not cfg or cfg.is_deleted:
                raise NotFoundError("Model configuration not found.")
            return cfg

    async def list_model_configs(
        self,
        tier: ModelTier | None = None,
        active_only: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ModelConfiguration], int]:
        async with self._transaction() as uow:
            if active_only:
                configs = await uow.configuration.list_active_configs(tier=tier)
                return configs, len(configs)
            return await uow.configuration.paginate_configs(
                page=page, page_size=page_size, tier=tier
            )

    async def get_default_for_tier(self, tier: ModelTier) -> ModelConfiguration | None:
        async with self._transaction() as uow:
            return await uow.configuration.get_default_config(tier)

    async def update_model_config(
        self, config_id: UUID, data: ModelConfigUpdate
    ) -> ModelConfiguration:
        async with self._transaction() as uow:
            cfg = await uow.configuration.get_model_config(config_id)
            if not cfg or cfg.is_deleted:
                raise NotFoundError("Model configuration not found.")
            updates = data.model_dump(exclude_none=True)
            if updates.get("is_default"):
                await uow.configuration.clear_default_for_tier(cfg.tier)
            await uow.configuration.update_model_config(config_id, updates)
            return await uow.configuration.get_model_config(config_id)

    async def record_health_status(
        self, config_id: UUID, status: str
    ) -> None:
        async with self._transaction() as uow:
            await uow.configuration.update_health_status(config_id, status)

    async def record_routing_stats(
        self,
        config_id: UUID,
        *,
        latency_ms: float,
        tokens_per_second: float,
        error: bool = False,
    ) -> None:
        async with self._transaction() as uow:
            await uow.configuration.update_routing_stats(
                config_id,
                latency_ms=latency_ms,
                tokens_per_second=tokens_per_second,
                error=error,
            )

    async def delete_model_config(self, config_id: UUID) -> None:
        async with self._transaction() as uow:
            cfg = await uow.configuration.get_model_config(config_id)
            if not cfg:
                raise NotFoundError("Model configuration not found.")
            cfg.soft_delete()
            await uow.configuration.flush()