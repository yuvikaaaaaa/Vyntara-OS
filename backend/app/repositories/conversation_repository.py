"""IOS — Conversation Repository."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.core.enums import MessageRole, SessionStatus
from app.models.conversation import Attachment, Conversation, Message
from app.repositories.base import BaseRepository


class ConversationRepository(BaseRepository[Conversation]):
    model = Conversation

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        status: SessionStatus | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Conversation], int]:
        filters = [Conversation.user_id == user_id]
        if status:
            filters.append(Conversation.status == status)
        return await self.paginate(
            page=page,
            page_size=page_size,
            filters=filters,
            order_by=Conversation.updated_at,
            descending=True,
        )

    async def increment_message_count(
        self, conversation_id: UUID, token_delta: int = 0
    ) -> None:
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(
                message_count=Conversation.message_count + 1,
                total_tokens=Conversation.total_tokens + token_delta,
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Message
    # ------------------------------------------------------------------

    async def add_message(self, message: Message) -> Message:
        self._session.add(message)
        await self._session.flush()
        await self._session.refresh(message)
        return message

    async def get_messages(
        self,
        conversation_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
        role: MessageRole | None = None,
    ) -> list[Message]:
        filters: list = [Message.conversation_id == conversation_id]
        if role:
            filters.append(Message.role == role)
        stmt = (
            select(Message)
            .where(and_(*filters))
            .order_by(Message.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_messages(self, conversation_id: UUID) -> int:
        stmt = select(func.count()).select_from(Message).where(
            Message.conversation_id == conversation_id
        )
        return (await self._session.execute(stmt)).scalar() or 0

    async def get_message_by_id(self, message_id: UUID) -> Message | None:
        return await self._session.get(Message, message_id)

    async def get_recent_messages(
        self, conversation_id: UUID, *, limit: int = 20
    ) -> list[Message]:
        """Return the most recent ``limit`` messages, oldest-first."""
        subq = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
            .subquery()
        )
        stmt = select(Message).from_statement(
            select(subq).order_by(subq.c.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Attachment
    # ------------------------------------------------------------------

    async def add_attachment(self, attachment: Attachment) -> Attachment:
        self._session.add(attachment)
        await self._session.flush()
        await self._session.refresh(attachment)
        return attachment

    async def get_attachments_for_message(
        self, message_id: UUID
    ) -> list[Attachment]:
        stmt = select(Attachment).where(Attachment.message_id == message_id)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_unprocessed_attachments(self, limit: int = 50) -> list[Attachment]:
        stmt = (
            select(Attachment)
            .where(Attachment.is_processed.is_(False))
            .order_by(Attachment.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_attachment_processed(
        self, attachment: Attachment, knowledge_document_id: UUID
    ) -> None:
        attachment.is_processed = True
        attachment.knowledge_document_id = knowledge_document_id
        await self._session.flush()