"""
Intelligence Operating System — Knowledge Models
=================================================
ORM models for the RAG (Retrieval-Augmented Generation) domain:

``KnowledgeDocument``  — Metadata for an ingested document.
``KnowledgeChunk``     — A chunked segment of a document with positional info.
``EmbeddingMetadata``  — Tracks which model and version produced each embedding.
``Citation``           — Links a generated ``Message`` claim to a source ``Chunk``.

Architecture notes:
- Actual embedding vectors are stored in Qdrant (not PostgreSQL).
  ``KnowledgeChunk.qdrant_point_id`` is the foreign reference into Qdrant.
- ``EmbeddingMetadata`` enables re-embedding when the embedding model changes —
  old embeddings are identified by ``model_name`` + ``model_version`` and
  can be bulk-reprocessed.
- ``Citation`` creates an auditable link between every generated claim and
  its source chunk, enabling hallucination audit trails.

Cascade policy:
    KnowledgeChunk → KnowledgeDocument (cascade delete)
    EmbeddingMetadata → KnowledgeChunk (cascade delete)
    Citation → KnowledgeChunk (SET NULL — citation survives chunk deletion)
    Citation → Message (cascade delete)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
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

from app.core.enums import ChunkingStrategy, DocumentStatus, FileType
from app.models.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.conversation import Message
    from app.models.user import User


# ---------------------------------------------------------------------------
# KnowledgeDocument
# ---------------------------------------------------------------------------


class KnowledgeDocument(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Metadata record for a document ingested into the RAG pipeline.

    The document's binary content is stored in the file system
    (``storage_path``); only metadata is persisted in PostgreSQL.

    Lifecycle (``DocumentStatus``):
        PENDING     → queued for processing
        PROCESSING  → chunking / embedding in progress
        INDEXED     → all chunks embedded and searchable
        FAILED      → ingestion failed (``error_message`` populated)
        DELETED     → soft-deleted

    Re-ingestion: update ``status`` to ``PENDING``; the ingestion worker
    detects this and re-processes the document, replacing existing chunks.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_user_id", "user_id"),
        Index("ix_knowledge_documents_status", "status"),
        Index("ix_knowledge_documents_file_type", "file_type"),
        Index("ix_knowledge_documents_created_at", "created_at"),
        Index(
            "ix_knowledge_documents_checksum",
            "checksum_sha256",
            postgresql_where=text("checksum_sha256 IS NOT NULL"),
        ),
        {"comment": "Documents ingested into the hybrid RAG pipeline."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="User who uploaded / owns this document.",
    )
    filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="Original filename as uploaded.",
    )
    title: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        doc="Document title extracted from metadata or set by user.",
    )
    file_type: Mapped[FileType] = mapped_column(
        SAEnum(FileType, name="file_type_enum", create_type=True),
        nullable=False,
    )
    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        doc="Absolute filesystem path or object-storage key.",
    )
    checksum_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="SHA-256 of the file content for deduplication.",
    )
    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus, name="document_status_enum", create_type=True),
        nullable=False,
        default=DocumentStatus.PENDING,
        server_default=text("'pending'"),
    )
    # Content statistics (populated after processing)
    page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    word_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    char_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Denormalised chunk count (updated by ingestion pipeline).",
    )
    language: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        doc="Detected document language (ISO 639-1 code, e.g. 'en').",
    )
    # Ingestion configuration snapshot
    chunking_strategy: Mapped[ChunkingStrategy] = mapped_column(
        SAEnum(ChunkingStrategy, name="chunking_strategy_enum", create_type=True),
        nullable=False,
        default=ChunkingStrategy.RECURSIVE,
        server_default=text("'recursive'"),
    )
    chunk_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=512,
        server_default=text("512"),
    )
    chunk_overlap: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=64,
        server_default=text("64"),
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Name of the embedding model used (e.g. 'BAAI/bge-large-en-v1.5').",
    )
    # Error tracking
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Error detail if status is FAILED.",
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp when indexing completed successfully.",
    )
    # Organisation
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
    )
    source_url: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
        doc="Original URL if the document was fetched from the web.",
    )
    extra_metadata: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Arbitrary extra metadata (author, publication date, DOI, etc.).",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User"] = relationship("User")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="KnowledgeChunk.chunk_index",
        doc="All chunks produced from this document.",
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeDocument id={self.id} filename={self.filename!r} "
            f"status={self.status} chunks={self.chunk_count}>"
        )


# ---------------------------------------------------------------------------
# KnowledgeChunk
# ---------------------------------------------------------------------------


class KnowledgeChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A single chunk produced by splitting a ``KnowledgeDocument``.

    The actual embedding vector is stored in Qdrant; this record holds
    positional and textual metadata required for:
    - Citation rendering (page number, section heading)
    - Re-embedding on model change (via ``qdrant_point_id``)
    - BM25 index construction (stored in Qdrant sparse index)

    ``qdrant_point_id`` is the UUID used as the Qdrant point ID — it matches
    this record's ``id`` for simplicity and consistency.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("ix_knowledge_chunks_document_id", "document_id"),
        Index("ix_knowledge_chunks_chunk_index", "chunk_index"),
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_knowledge_chunks_doc_idx"
        ),
        {"comment": "Text chunks produced by the document splitter."},
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Zero-based position of this chunk within its parent document.",
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Raw text content of the chunk.",
    )
    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Estimated token count of the chunk content.",
    )
    char_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    # Source position
    page_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Page number in the source document (for PDFs/DOCX).",
    )
    start_char: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Character offset of the chunk start in the raw document text.",
    )
    end_char: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Character offset of the chunk end in the raw document text.",
    )
    section_path: Mapped[str | None] = mapped_column(
        String(1000),
        nullable=True,
        doc="Hierarchical section heading path, e.g. 'Chapter 2 > Section 3.1'.",
    )
    heading: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Nearest heading above this chunk in the document structure.",
    )
    # Qdrant reference
    qdrant_point_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        unique=True,
        doc="Qdrant point UUID — matches this chunk's id for 1:1 lookup.",
    )
    is_embedded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True once the embedding has been written to Qdrant.",
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Embedding model used for this chunk's vector.",
    )
    extra_metadata: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    document: Mapped["KnowledgeDocument"] = relationship(
        "KnowledgeDocument",
        back_populates="chunks",
    )
    embedding_metadata: Mapped[list["EmbeddingMetadata"]] = relationship(
        "EmbeddingMetadata",
        back_populates="chunk",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    citations: Mapped[list["Citation"]] = relationship(
        "Citation",
        back_populates="chunk",
    )

    def __repr__(self) -> str:
        preview = self.content[:40].replace("\n", " ") if self.content else ""
        return (
            f"<KnowledgeChunk id={self.id} doc={self.document_id} "
            f"idx={self.chunk_index} content={preview!r}>"
        )


# ---------------------------------------------------------------------------
# EmbeddingMetadata
# ---------------------------------------------------------------------------


class EmbeddingMetadata(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Audit record for each embedding computation applied to a ``KnowledgeChunk``.

    Enables:
    - Detecting stale embeddings when the model changes
    - Tracking embedding costs and latency
    - Rolling re-embeddings by model version

    Multiple records per chunk are possible (one per model version used).
    The ``is_current`` flag identifies which embedding is active in Qdrant.
    """

    __tablename__ = "embedding_metadata"
    __table_args__ = (
        Index("ix_embedding_metadata_chunk_id", "chunk_id"),
        Index("ix_embedding_metadata_model_name", "model_name"),
        Index("ix_embedding_metadata_is_current", "is_current"),
        UniqueConstraint(
            "chunk_id", "model_name", "model_version",
            name="uq_embedding_metadata_chunk_model_version",
        ),
        {"comment": "Audit trail of embedding computations per chunk."},
    )

    chunk_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Embedding model name (e.g. 'BAAI/bge-large-en-v1.5').",
    )
    model_version: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="latest",
        doc="Model version string or commit hash.",
    )
    vector_dimension: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Dimension of the produced embedding vector.",
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="True if this is the active embedding stored in Qdrant.",
    )
    latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Time taken to compute the embedding in milliseconds.",
    )
    qdrant_collection: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Qdrant collection where this embedding is stored.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    chunk: Mapped["KnowledgeChunk"] = relationship(
        "KnowledgeChunk",
        back_populates="embedding_metadata",
    )

    def __repr__(self) -> str:
        return (
            f"<EmbeddingMetadata chunk={self.chunk_id} "
            f"model={self.model_name!r} current={self.is_current}>"
        )


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------


class Citation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Links a factual claim in a generated ``Message`` to its source ``KnowledgeChunk``.

    The citation pipeline records:
    - Which chunk supported which message
    - The relevance score computed by the cross-encoder
    - The specific text span within the chunk (if context compression was applied)
    - The rank of the chunk in the retrieval result set

    This creates a complete audit trail from generated text back to source
    material, supporting hallucination detection and compliance review.
    """

    __tablename__ = "citations"
    __table_args__ = (
        Index("ix_citations_message_id", "message_id"),
        Index("ix_citations_chunk_id", "chunk_id"),
        Index("ix_citations_relevance_score", "relevance_score"),
        {"comment": "Source citations linking generated claims to knowledge chunks."},
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL"),
        nullable=True,
        doc="Source chunk. SET NULL on chunk deletion to preserve citation record.",
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        nullable=True,
        doc="Denormalised parent document reference for direct lookup.",
    )
    retrieval_rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Position of this chunk in the final re-ranked retrieval result (1-based).",
    )
    relevance_score: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc="Cross-encoder relevance score (0.0–1.0).",
    )
    vector_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Dense vector cosine similarity score (0.0–1.0).",
    )
    bm25_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="BM25 sparse retrieval score.",
    )
    cited_span: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Specific text span from the chunk used as evidence (after compression).",
    )
    citation_label: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        doc="Short reference label shown to the user, e.g. '[1]', '[Source A]'.",
    )
    is_hallucination_suspect: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True if the NLI verifier found the claim unsupported by this chunk.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    message: Mapped["Message"] = relationship(
        "Message",
        back_populates="citations",
    )
    chunk: Mapped["KnowledgeChunk | None"] = relationship(
        "KnowledgeChunk",
        back_populates="citations",
    )

    def __repr__(self) -> str:
        return (
            f"<Citation message={self.message_id} chunk={self.chunk_id} "
            f"score={self.relevance_score:.3f} rank={self.retrieval_rank}>"
        )