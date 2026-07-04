"""IOS — Conversation Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import MessageRole, SessionStatus
from app.core.exceptions import AuthorizationError, ConversationNotFoundError, NotFoundError
from app.models.conversation import Conversation, Message
from app.models.memory import WorkingMemory
from app.schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    MessageCreate,
)
from app.services.base import BaseService


class ConversationService(BaseService):
    """Manages conversation sessions, messages, and working-memory linkage."""

    async def create_conversation(
        self, user_id: UUID, data: ConversationCreate
    ) -> Conversation:
        async with self._span("create_conversation"):
            async with self._transaction() as uow:
                conv = Conversation(
                    user_id=user_id,
                    title=data.title,
                    tags=data.tags,
                    metadata_=data.metadata_,
                )
                await uow.conversations.create(conv)

                # Provision working memory record
                from app.core.constants import WORKING_MEMORY_MAX_TOKENS, REDIS_NS_WORKING_MEMORY
                redis_key = f"{REDIS_NS_WORKING_MEMORY}:{conv.id}"
                wm = WorkingMemory(
                    conversation_id=conv.id,
                    user_id=user_id,
                    redis_key=redis_key,
                    max_token_budget=WORKING_MEMORY_MAX_TOKENS,
                )
                await uow.memory.create(wm)
                self._log.info("conversation_created", conv_id=str(conv.id))
                return conv

    async def get_conversation(self, conv_id: UUID, user_id: UUID) -> Conversation:
        async with self._transaction() as uow:
            conv = await uow.conversations.get_by_id(conv_id)
            if not conv or conv.is_deleted:
                raise NotFoundError("Conversation not found.")
            if conv.user_id != user_id:
                raise AuthorizationError("Access denied.")
            return conv

    async def list_conversations(
        self,
        user_id: UUID,
        *,
        status: SessionStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Conversation], int]:
        async with self._transaction() as uow:
            return await uow.conversations.list_for_user(
                user_id, status=status, page=page, page_size=page_size
            )

    async def update_conversation(
        self, conv_id: UUID, user_id: UUID, data: ConversationUpdate
    ) -> Conversation:
        async with self._transaction() as uow:
            conv = await self._get_owned(uow, conv_id, user_id)
            updates = data.model_dump(exclude_none=True)
            await uow.conversations.update(conv, updates)
            return conv

    async def archive_conversation(self, conv_id: UUID, user_id: UUID) -> None:
        async with self._transaction() as uow:
            conv = await self._get_owned(uow, conv_id, user_id)
            conv.status = SessionStatus.ARCHIVED
            await uow.conversations.flush()

    async def delete_conversation(self, conv_id: UUID, user_id: UUID) -> None:
        async with self._transaction() as uow:
            conv = await self._get_owned(uow, conv_id, user_id)
            await uow.conversations.soft_delete(conv)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def add_message(
        self, conv_id: UUID, user_id: UUID, data: MessageCreate
    ) -> Message:
        async with self._span("add_message"):
            async with self._transaction() as uow:
                conv = await self._get_owned(uow, conv_id, user_id)
                if conv.status != SessionStatus.ACTIVE:
                    raise AuthorizationError("Cannot add messages to a non-active conversation.")
                msg = Message(
                    conversation_id=conv_id,
                    role=data.role,
                    content=data.content,
                    content_type=data.content_type,
                    tool_name=data.tool_name,
                    tool_call_id=data.tool_call_id,
                    extra_data=data.extra_data,
                )
                await uow.conversations.add_message(msg)
                await uow.conversations.increment_message_count(
                    conv_id, token_delta=msg.total_tokens or 0
                )
                return msg

    async def get_messages(
        self,
        conv_id: UUID,
        user_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        role: MessageRole | None = None,
    ) -> list[Message]:
        async with self._transaction() as uow:
            await self._get_owned(uow, conv_id, user_id)
            return await uow.conversations.get_messages(
                conv_id, limit=limit, offset=offset, role=role
            )

    async def get_recent_context(
        self, conv_id: UUID, user_id: UUID, *, limit: int = 20
    ) -> list[Message]:
        """Return the most recent messages for LLM context injection."""
        async with self._transaction() as uow:
            await self._get_owned(uow, conv_id, user_id)
            return await uow.conversations.get_recent_messages(conv_id, limit=limit)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_owned(self, uow, conv_id: UUID, user_id: UUID) -> Conversation:
        conv = await uow.conversations.get_by_id(conv_id)
        if not conv or conv.is_deleted:
            raise NotFoundError("Conversation not found.")
        if conv.user_id != user_id:
            raise AuthorizationError("Access denied.")
        return conv