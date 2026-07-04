"""IOS — Knowledge Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import DocumentStatus, FileType
from app.core.exceptions import AuthorizationError, DocumentNotFoundError, NotFoundError
from app.models.knowledge import Citation, EmbeddingMetadata, KnowledgeChunk, KnowledgeDocument
from app.schemas.knowledge import (
    CitationCreate,
    DocumentIngestRequest,
    DocumentUpdate,
)
from app.services.base import BaseService

try:
    from app.core.exceptions import DocumentNotFoundError  # type: ignore[attr-defined]
except ImportError:
    pass  # type: ignore[assignment]


class KnowledgeService(BaseService):
    """Orchestrates document ingestion lifecycle and chunk management."""

    async def register_document(
        self,
        user_id: UUID,
        filename: str,
        file_type: FileType,
        mime_type: str,
        file_size_bytes: int,
        storage_path: str,
        checksum: str | None,
        data: DocumentIngestRequest,
    ) -> KnowledgeDocument:
        """Create the document metadata record (ingestion pipeline runs separately)."""
        async with self._span("register_document"):
            async with self._transaction() as uow:
                # Deduplication check
                if checksum:
                    existing = await uow.knowledge.get_by_checksum(checksum)
                    if existing and existing.user_id == user_id:
                        return existing

                doc = KnowledgeDocument(
                    user_id=user_id,
                    filename=filename,
                    title=data.title,
                    file_type=file_type,
                    mime_type=mime_type,
                    file_size_bytes=file_size_bytes,
                    storage_path=storage_path,
                    checksum_sha256=checksum,
                    status=DocumentStatus.PENDING,
                    chunking_strategy=data.chunking_strategy,
                    chunk_size=data.chunk_size,
                    chunk_overlap=data.chunk_overlap,
                    source_url=data.source_url,
                    tags=data.tags,
                    extra_metadata=data.extra_metadata,
                )
                await uow.knowledge.create(doc)
                self._log.info("document_registered", doc_id=str(doc.id))
                return doc

    async def get_document(self, doc_id: UUID, user_id: UUID) -> KnowledgeDocument:
        async with self._transaction() as uow:
            doc = await uow.knowledge.get_by_id(doc_id)
            if not doc or doc.is_deleted:
                raise NotFoundError("Document not found.")
            if doc.user_id != user_id:
                raise AuthorizationError("Access denied.")
            return doc

    async def list_documents(
        self,
        user_id: UUID,
        *,
        status: DocumentStatus | None = None,
        tags: list[str] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[KnowledgeDocument], int]:
        async with self._transaction() as uow:
            return await uow.knowledge.list_for_user(
                user_id, status=status, tags=tags, page=page, page_size=page_size
            )

    async def update_document(
        self, doc_id: UUID, user_id: UUID, data: DocumentUpdate
    ) -> KnowledgeDocument:
        async with self._transaction() as uow:
            doc = await self._get_owned(uow, doc_id, user_id)
            updates = data.model_dump(exclude_none=True)
            await uow.knowledge.update(doc, updates)
            return doc

    async def delete_document(self, doc_id: UUID, user_id: UUID) -> None:
        async with self._transaction() as uow:
            doc = await self._get_owned(uow, doc_id, user_id)
            await uow.knowledge.soft_delete(doc)

    # ------------------------------------------------------------------
    # Ingestion pipeline integration points (called by background workers)
    # ------------------------------------------------------------------

    async def mark_processing(self, doc_id: UUID) -> None:
        async with self._transaction() as uow:
            await uow.knowledge.update_status(doc_id, DocumentStatus.PROCESSING)

    async def mark_indexed(self, doc_id: UUID, embedding_model: str) -> None:
        async with self._transaction() as uow:
            await uow.knowledge.update_status(doc_id, DocumentStatus.INDEXED)
            doc = await uow.knowledge.get_by_id(doc_id)
            if doc:
                doc.embedding_model = embedding_model

    async def mark_failed(self, doc_id: UUID, error: str) -> None:
        async with self._transaction() as uow:
            await uow.knowledge.update_status(
                doc_id, DocumentStatus.FAILED, error_message=error
            )

    async def save_chunks(
        self,
        doc_id: UUID,
        chunks: list[KnowledgeChunk],
    ) -> list[KnowledgeChunk]:
        async with self._transaction() as uow:
            saved = await uow.knowledge.bulk_create_chunks(chunks)
            await uow.knowledge.increment_chunk_count(doc_id, len(saved))
            return saved

    async def mark_chunks_embedded(
        self,
        chunk_ids: list[UUID],
        model_name: str,
        qdrant_collection: str,
    ) -> None:
        async with self._transaction() as uow:
            await uow.knowledge.mark_chunks_embedded(chunk_ids, model_name)
            meta_records = [
                EmbeddingMetadata(
                    chunk_id=cid,
                    model_name=model_name,
                    model_version="latest",
                    vector_dimension=1024,
                    qdrant_collection=qdrant_collection,
                    is_current=True,
                )
                for cid in chunk_ids
            ]
            for m in meta_records:
                uow.knowledge._session.add(m)
            await uow.knowledge.flush()

    async def get_chunks(
        self, doc_id: UUID, user_id: UUID, *, embedded_only: bool = False
    ) -> list[KnowledgeChunk]:
        async with self._transaction() as uow:
            await self._get_owned(uow, doc_id, user_id)
            return await uow.knowledge.get_chunks_for_document(
                doc_id, embedded_only=embedded_only
            )

    async def get_chunks_by_ids(self, chunk_ids: list[UUID]) -> list[KnowledgeChunk]:
        async with self._transaction() as uow:
            return await uow.knowledge.get_chunks_by_ids(chunk_ids)

    # ------------------------------------------------------------------
    # Citations
    # ------------------------------------------------------------------

    async def save_citations(
        self, message_id: UUID, citations: list[CitationCreate]
    ) -> list[Citation]:
        async with self._transaction() as uow:
            records = [
                Citation(
                    message_id=message_id,
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    retrieval_rank=c.retrieval_rank,
                    relevance_score=c.relevance_score,
                    vector_score=c.vector_score,
                    bm25_score=c.bm25_score,
                    cited_span=c.cited_span,
                    citation_label=c.citation_label,
                )
                for c in citations
            ]
            return await uow.knowledge.bulk_create_citations(records)

    async def get_citations(self, message_id: UUID) -> list[Citation]:
        async with self._transaction() as uow:
            return await uow.knowledge.get_citations_for_message(message_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get_owned(self, uow, doc_id: UUID, user_id: UUID) -> KnowledgeDocument:
        doc = await uow.knowledge.get_by_id(doc_id)
        if not doc or doc.is_deleted:
            raise NotFoundError("Document not found.")
        if doc.user_id != user_id:
            raise AuthorizationError("Access denied.")
        return doc