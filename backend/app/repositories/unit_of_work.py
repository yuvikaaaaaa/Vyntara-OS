"""IOS — Unit of Work."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.repositories.agent_repository import AgentRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.configuration_repository import ConfigurationRepository
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.evaluation_repository import EvaluationRepository
from app.repositories.knowledge_repository import KnowledgeRepository
from app.repositories.memory_repository import MemoryRepository
from app.repositories.tool_repository import ToolRepository
from app.repositories.user_repository import UserRepository
from app.repositories.workflow_repository import WorkflowRepository

logger = get_logger(__name__)


class UnitOfWork:
    """
    Coordinates multiple repositories within one database transaction.

    Repositories never commit.  UoW is the sole owner of commit/rollback.

    Usage::

        async with UnitOfWork(session) as uow:
            user = await uow.users.get_by_id(user_id)
            task = AgentTask(user_id=user.id, ...)
            await uow.agents.create(task)
            # commit happens automatically on clean __aexit__
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.users = UserRepository(session)
        self.conversations = ConversationRepository(session)
        self.knowledge = KnowledgeRepository(session)
        self.memory = MemoryRepository(session)
        self.agents = AgentRepository(session)
        self.workflows = WorkflowRepository(session)
        self.tools = ToolRepository(session)
        self.evaluations = EvaluationRepository(session)
        self.audit = AuditRepository(session)
        self.configuration = ConfigurationRepository(session)

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()

    async def flush(self) -> None:
        await self._session.flush()

    async def __aenter__(self) -> "UnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is None:
            try:
                await self.commit()
            except Exception:
                await self.rollback()
                raise
        else:
            await self.rollback()


@asynccontextmanager
async def get_unit_of_work(session: AsyncSession) -> AsyncIterator[UnitOfWork]:
    """
    Async context manager yielding a UnitOfWork for a given session.

    Preferred over the class ``async with`` syntax when used outside
    of dependency injection (e.g. background tasks, scripts).
    """
    uow = UnitOfWork(session)
    async with uow:
        yield uow