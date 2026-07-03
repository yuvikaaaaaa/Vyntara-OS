"""IOS — Knowledge Repository."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.core.enums import DocumentStatus
from app.models.knowledge import Citation, EmbeddingMetadata, KnowledgeChunk, KnowledgeDocument
from app.repositories.base import BaseRepository


class KnowledgeRepository(BaseRepository[KnowledgeDocument]):
    model = KnowledgeDocument

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        status: DocumentStatus | None = None,
        tags: list[str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[KnowledgeDocument], int]:
        filters = [KnowledgeDocument.user_id == user_id]
        if status:
            filters.append(KnowledgeDocument.status == status)
        if tags:
            filters.append(KnowledgeDocument.tags.overlap(tags))  # type: ignore[attr-defined]
        return await self.paginate(
            page=page,
            page_size=page_size,
            filters=filters,
            order_by=KnowledgeDocument.created_at,
            descending=True,
        )

    async def get_by_checksum(self, checksum: str) -> KnowledgeDocument | None:
        stmt = select(KnowledgeDocument).where(
            KnowledgeDocument.checksum_sha256 == checksum,
            KnowledgeDocument.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def get_pending_documents(self, limit: int = 10) -> list[KnowledgeDocument]:
        stmt = (
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.status == DocumentStatus.PENDING,
                KnowledgeDocument.deleted_at.is_(None),
            )
            .order_by(KnowledgeDocument.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        document_id: UUID,
        status: DocumentStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        values: dict = {"status": status}
        if error_message is not None:
            values["error_message"] = error_message
        if status == DocumentStatus.INDEXED:
            from datetime import datetime, timezone
            values["indexed_at"] = datetime.now(tz=timezone.utc)
        stmt = (
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def increment_chunk_count(self, document_id: UUID, delta: int) -> None:
        stmt = (
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id)
            .values(chunk_count=KnowledgeDocument.chunk_count + delta)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # KnowledgeChunk
    # ------------------------------------------------------------------

    async def create_chunk(self, chunk: KnowledgeChunk) -> KnowledgeChunk:
        self._session.add(chunk)
        await self._session.flush()
        await self._session.refresh(chunk)
        return chunk

    async def bulk_create_chunks(
        self, chunks: list[KnowledgeChunk]
    ) -> list[KnowledgeChunk]:
        self._session.add_all(chunks)
        await self._session.flush()
        return chunks

    async def get_chunks_for_document(
        self,
        document_id: UUID,
        *,
        embedded_only: bool = False,
    ) -> list[KnowledgeChunk]:
        filters: list = [KnowledgeChunk.document_id == document_id]
        if embedded_only:
            filters.append(KnowledgeChunk.is_embedded.is_(True))
        stmt = (
            select(KnowledgeChunk)
            .where(and_(*filters))
            .order_by(KnowledgeChunk.chunk_index.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_chunk_by_id(self, chunk_id: UUID) -> KnowledgeChunk | None:
        return await self._session.get(KnowledgeChunk, chunk_id)

    async def get_chunks_by_ids(self, chunk_ids: list[UUID]) -> list[KnowledgeChunk]:
        if not chunk_ids:
            return []
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.id.in_(chunk_ids))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def mark_chunks_embedded(
        self, chunk_ids: list[UUID], model_name: str
    ) -> None:
        stmt = (
            update(KnowledgeChunk)
            .where(KnowledgeChunk.id.in_(chunk_ids))
            .values(is_embedded=True, embedding_model=model_name)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def delete_chunks_for_document(self, document_id: UUID) -> int:
        from sqlalchemy import delete
        stmt = delete(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document_id
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.rowcount  # type: ignore[return-value]

    async def count_chunks(self, document_id: UUID) -> int:
        stmt = select(func.count()).select_from(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document_id
        )
        return (await self._session.execute(stmt)).scalar() or 0

    # ------------------------------------------------------------------
    # EmbeddingMetadata
    # ------------------------------------------------------------------

    async def create_embedding_metadata(
        self, meta: EmbeddingMetadata
    ) -> EmbeddingMetadata:
        self._session.add(meta)
        await self._session.flush()
        await self._session.refresh(meta)
        return meta

    async def deactivate_embeddings(
        self, chunk_id: UUID, keep_model: str
    ) -> None:
        """Set is_current=False for all embeddings except the given model."""
        stmt = (
            update(EmbeddingMetadata)
            .where(
                EmbeddingMetadata.chunk_id == chunk_id,
                EmbeddingMetadata.model_name != keep_model,
            )
            .values(is_current=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # Citation
    # ------------------------------------------------------------------

    async def create_citation(self, citation: Citation) -> Citation:
        self._session.add(citation)
        await self._session.flush()
        await self._session.refresh(citation)
        return citation

    async def bulk_create_citations(
        self, citations: list[Citation]
    ) -> list[Citation]:
        self._session.add_all(citations)
        await self._session.flush()
        return citations

    async def get_citations_for_message(self, message_id: UUID) -> list[Citation]:
        stmt = (
            select(Citation)
            .where(Citation.message_id == message_id)
            .order_by(Citation.retrieval_rank.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())