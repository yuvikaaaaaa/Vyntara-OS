"""IOS — Configuration Repository."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.core.enums import AgentType, ModelProvider, ModelTier
from app.models.configuration import ModelConfiguration, PromptTemplate
from app.repositories.base import BaseRepository


class ConfigurationRepository(BaseRepository[PromptTemplate]):
    model = PromptTemplate

    # ------------------------------------------------------------------
    # PromptTemplate
    # ------------------------------------------------------------------

    async def get_current(self, template_key: str) -> PromptTemplate | None:
        """Return the active (is_current=True) version of a template."""
        stmt = select(PromptTemplate).where(
            PromptTemplate.template_key == template_key,
            PromptTemplate.is_current.is_(True),
            PromptTemplate.is_enabled.is_(True),
            PromptTemplate.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_by_key_version(
        self, template_key: str, version: int
    ) -> PromptTemplate | None:
        stmt = select(PromptTemplate).where(
            PromptTemplate.template_key == template_key,
            PromptTemplate.version == version,
            PromptTemplate.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_versions(self, template_key: str) -> list[PromptTemplate]:
        """Return all versions for a template key, newest first."""
        stmt = (
            select(PromptTemplate)
            .where(
                PromptTemplate.template_key == template_key,
                PromptTemplate.deleted_at.is_(None),
            )
            .order_by(PromptTemplate.version.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_current_templates(
        self,
        agent_type: AgentType | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[PromptTemplate], int]:
        filters = [
            PromptTemplate.is_current.is_(True),
            PromptTemplate.deleted_at.is_(None),
        ]
        if agent_type is not None:
            filters.append(PromptTemplate.agent_type == agent_type)
        return await self.paginate(
            page=page,
            page_size=page_size,
            filters=filters,
            order_by=PromptTemplate.template_key,
            descending=False,
        )

    async def next_version(self, template_key: str) -> int:
        stmt = select(func.max(PromptTemplate.version)).where(
            PromptTemplate.template_key == template_key
        )
        result = await self._session.execute(stmt)
        return (result.scalar() or 0) + 1

    async def supersede(self, template_key: str) -> None:
        """Mark all existing versions of a template as not current."""
        stmt = (
            update(PromptTemplate)
            .where(
                PromptTemplate.template_key == template_key,
                PromptTemplate.is_current.is_(True),
            )
            .values(is_current=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def increment_use_count(self, template_id: UUID) -> None:
        stmt = (
            update(PromptTemplate)
            .where(PromptTemplate.id == template_id)
            .values(use_count=PromptTemplate.use_count + 1)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def update_performance(
        self,
        template_id: UUID,
        quality_score: float,
        token_count: int,
    ) -> None:
        """Update exponential moving averages for quality and token counts."""
        stmt = (
            update(PromptTemplate)
            .where(PromptTemplate.id == template_id)
            .values(
                avg_quality_score=(
                    PromptTemplate.avg_quality_score * 0.9 + quality_score * 0.1
                ),
                avg_token_count=(
                    PromptTemplate.avg_token_count * 0.9 + token_count * 0.1
                ),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def list_by_agent_type(
        self, agent_type: AgentType
    ) -> list[PromptTemplate]:
        stmt = (
            select(PromptTemplate)
            .where(
                PromptTemplate.agent_type == agent_type,
                PromptTemplate.is_current.is_(True),
                PromptTemplate.is_enabled.is_(True),
                PromptTemplate.deleted_at.is_(None),
            )
            .order_by(PromptTemplate.template_key.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # ModelConfiguration
    # ------------------------------------------------------------------

    async def get_model_config(
        self, config_id: UUID
    ) -> ModelConfiguration | None:
        return await self._session.get(ModelConfiguration, config_id)

    async def list_active_configs(
        self,
        tier: ModelTier | None = None,
        provider: ModelProvider | None = None,
    ) -> list[ModelConfiguration]:
        """Return all active (non-deleted, is_active=True) model configs."""
        filters: list = [
            ModelConfiguration.is_active.is_(True),
            ModelConfiguration.deleted_at.is_(None),
        ]
        if tier is not None:
            filters.append(ModelConfiguration.tier == tier)
        if provider is not None:
            filters.append(ModelConfiguration.provider == provider)
        stmt = (
            select(ModelConfiguration)
            .where(and_(*filters))
            .order_by(
                ModelConfiguration.tier.asc(),
                ModelConfiguration.priority.asc(),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_default_config(
        self, tier: ModelTier
    ) -> ModelConfiguration | None:
        stmt = select(ModelConfiguration).where(
            ModelConfiguration.tier == tier,
            ModelConfiguration.is_default.is_(True),
            ModelConfiguration.is_active.is_(True),
            ModelConfiguration.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def create_model_config(
        self, config: ModelConfiguration
    ) -> ModelConfiguration:
        self._session.add(config)
        await self._session.flush()
        await self._session.refresh(config)
        return config

    async def update_model_config(
        self, config_id: UUID, values: dict
    ) -> None:
        stmt = (
            update(ModelConfiguration)
            .where(ModelConfiguration.id == config_id)
            .values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def update_health_status(
        self,
        config_id: UUID,
        status: str,
    ) -> None:
        from datetime import datetime, timezone
        stmt = (
            update(ModelConfiguration)
            .where(ModelConfiguration.id == config_id)
            .values(
                last_health_status=status,
                last_health_check_at=datetime.now(tz=timezone.utc),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def update_routing_stats(
        self,
        config_id: UUID,
        *,
        latency_ms: float,
        tokens_per_second: float,
        error: bool = False,
    ) -> None:
        """Update EMA latency, TPS, total request count, and rolling error rate."""
        stmt = (
            update(ModelConfiguration)
            .where(ModelConfiguration.id == config_id)
            .values(
                total_requests=ModelConfiguration.total_requests + 1,
                avg_latency_ms=(
                    ModelConfiguration.avg_latency_ms * 0.9 + latency_ms * 0.1
                ),
                avg_tokens_per_second=(
                    ModelConfiguration.avg_tokens_per_second * 0.9
                    + tokens_per_second * 0.1
                ),
                # Exponential moving average of error rate
                error_rate=(
                    ModelConfiguration.error_rate * 0.95
                    + (1.0 if error else 0.0) * 0.05
                ),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def clear_default_for_tier(self, tier: ModelTier) -> None:
        """Unset is_default on all configs for a tier before setting a new one."""
        stmt = (
            update(ModelConfiguration)
            .where(
                ModelConfiguration.tier == tier,
                ModelConfiguration.is_default.is_(True),
            )
            .values(is_default=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def paginate_configs(
        self,
        page: int = 1,
        page_size: int = 20,
        tier: ModelTier | None = None,
    ) -> tuple[list[ModelConfiguration], int]:
        filters = [ModelConfiguration.deleted_at.is_(None)]
        if tier:
            filters.append(ModelConfiguration.tier == tier)

        total_stmt = select(func.count()).select_from(ModelConfiguration).where(
            and_(*filters)
        )
        total: int = (await self._session.execute(total_stmt)).scalar() or 0

        stmt = (
            select(ModelConfiguration)
            .where(and_(*filters))
            .order_by(
                ModelConfiguration.tier.asc(),
                ModelConfiguration.priority.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total