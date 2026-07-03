"""IOS — Knowledge & RAG Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.core.enums import ChunkingStrategy, DocumentStatus, FileType
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema


# ---------------------------------------------------------------------------
# KnowledgeDocument
# ---------------------------------------------------------------------------


class DocumentIngestRequest(AppModel):
    """Submitted alongside a file upload to configure ingestion."""

    title: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    chunking_strategy: ChunkingStrategy = ChunkingStrategy.RECURSIVE
    chunk_size: int = Field(default=512, ge=64, le=4096)
    chunk_overlap: int = Field(default=64, ge=0, le=512)
    source_url: str | None = Field(default=None, max_length=2048)
    extra_metadata: dict = Field(default_factory=dict)

    @field_validator("chunk_overlap")
    @classmethod
    def overlap_less_than_size(cls, v: int, info) -> int:
        chunk_size = info.data.get("chunk_size", 512)
        if v >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size.")
        return v


class DocumentUpdate(AppModel):
    title: str | None = Field(default=None, max_length=1000)
    tags: list[str] | None = None
    extra_metadata: dict | None = None


class KnowledgeDocumentRead(AuditedSchema):
    id: UUID
    user_id: UUID
    filename: str
    title: str | None
    file_type: FileType
    mime_type: str
    file_size_bytes: int
    status: DocumentStatus
    page_count: int | None
    word_count: int | None
    chunk_count: int
    language: str | None
    chunking_strategy: ChunkingStrategy
    chunk_size: int
    chunk_overlap: int
    embedding_model: str | None
    error_message: str | None
    indexed_at: datetime | None
    tags: list[str]
    source_url: str | None
    extra_metadata: dict


class DocumentSummary(OrmModel):
    id: UUID
    filename: str
    title: str | None
    status: DocumentStatus
    chunk_count: int
    updated_at: datetime


# ---------------------------------------------------------------------------
# KnowledgeChunk
# ---------------------------------------------------------------------------


class KnowledgeChunkRead(TimestampedSchema):
    id: UUID
    document_id: UUID
    chunk_index: int
    content: str
    token_count: int | None
    page_number: int | None
    section_path: str | None
    heading: str | None
    qdrant_point_id: UUID | None
    is_embedded: bool
    embedding_model: str | None


class ChunkSearchResult(OrmModel):
    """Augmented chunk result from RAG retrieval — includes similarity score."""

    chunk: KnowledgeChunkRead
    relevance_score: float
    vector_score: float | None
    bm25_score: float | None
    retrieval_rank: int


# ---------------------------------------------------------------------------
# EmbeddingMetadata
# ---------------------------------------------------------------------------


class EmbeddingMetadataRead(TimestampedSchema):
    id: UUID
    chunk_id: UUID
    model_name: str
    model_version: str
    vector_dimension: int
    is_current: bool
    latency_ms: int | None
    qdrant_collection: str


# ---------------------------------------------------------------------------
# Citation
# ---------------------------------------------------------------------------


class CitationRead(TimestampedSchema):
    id: UUID
    message_id: UUID
    chunk_id: UUID | None
    document_id: UUID | None
    retrieval_rank: int
    relevance_score: float
    vector_score: float | None
    bm25_score: float | None
    cited_span: str | None
    citation_label: str | None
    is_hallucination_suspect: bool


class CitationCreate(AppModel):
    chunk_id: UUID | None
    document_id: UUID | None
    retrieval_rank: int
    relevance_score: float = Field(ge=0.0, le=1.0)
    vector_score: float | None = None
    bm25_score: float | None = None
    cited_span: str | None = None
    citation_label: str | None = Field(default=None, max_length=20)