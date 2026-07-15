"""IOS Retrieval — Vector Retriever."""
from __future__ import annotations

from datetime import datetime, timezone

from app.ai_core.router.model_router import ModelRouter
from app.ai_core.types import EmbeddingRequest
from app.core.constants import QDRANT_COLLECTION_DOCUMENT_CHUNKS
from app.retrieval.base import BaseRetriever
from app.retrieval.exceptions import VectorRetrievalError
from app.retrieval.types import RetrievalRequest, RetrievalSource, RetrievedItem


class VectorRetriever(BaseRetriever):
    """
    Dense vector retrieval over the ``document_chunks`` Qdrant collection.

    Uses AI Core's ModelRouter for query embedding, keeping this retriever
    decoupled from any specific embedding model or provider.  Never raises
    on Qdrant unavailability — returns an empty list and logs a warning,
    so hybrid retrieval degrades gracefully.
    """

    def __init__(
        self,
        qdrant_client,
        model_router: ModelRouter,
        *,
        embedding_model_id: str,
        collection_name: str = QDRANT_COLLECTION_DOCUMENT_CHUNKS,
    ) -> None:
        super().__init__()
        self._qdrant = qdrant_client
        self._router = model_router
        self._embedding_model_id = embedding_model_id
        self._collection = collection_name

    @property
    def source(self) -> RetrievalSource:
        return RetrievalSource.VECTOR

    async def retrieve(self, request: RetrievalRequest) -> list[RetrievedItem]:
        async with self._span("retrieve", top_k=str(request.top_k)):
            try:
                vector = await self._embed_query(request.query)
            except Exception as exc:
                self._log.warning("vector_query_embed_failed", exc=str(exc))
                return []

            try:
                from app.database.qdrant import filter_by_user, search_dense

                payload_filter = filter_by_user(str(request.user_id))
                hits = await search_dense(
                    self._qdrant,
                    self._collection,
                    vector,
                    top_k=request.top_k * 2,
                    score_threshold=request.min_score,
                    payload_filter=payload_filter,
                )
            except Exception as exc:
                self._log.warning("vector_search_failed", exc=str(exc))
                return []

            items = [self._hit_to_item(hit) for hit in hits]
            items = self.deduplicate(items)
            items = self.apply_metadata_filter(items, request.metadata_filter)
            items.sort(key=lambda i: i.score, reverse=True)

            self._log.info(
                "vector_retrieval_complete",
                query_len=len(request.query),
                results=len(items),
            )
            return items[: request.top_k]

    async def health_check(self) -> bool:
        try:
            from app.database.qdrant import ping_qdrant
            return await ping_qdrant(self._qdrant)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _embed_query(self, query: str) -> list[float]:
        response = await self._router.embed(
            EmbeddingRequest(texts=[query], model_id=self._embedding_model_id)
        )
        if not response.embeddings:
            raise VectorRetrievalError("Embedding gateway returned no vectors.")
        return response.embeddings[0]

    @staticmethod
    def _hit_to_item(hit) -> RetrievedItem:
        payload = hit.payload or {}
        created_raw = payload.get("created_at")
        created_at = None
        if created_raw:
            try:
                created_at = datetime.fromisoformat(created_raw)
            except (ValueError, TypeError):
                created_at = None

        return RetrievedItem(
            id=str(hit.id),
            content=payload.get("content", ""),
            source=RetrievalSource.VECTOR,
            score=float(hit.score),
            confidence=float(hit.score),
            title=payload.get("section_path") or payload.get("heading"),
            metadata={
                "document_id": payload.get("document_id"),
                "page_number": payload.get("page_number"),
                "chunk_index": payload.get("chunk_index"),
                "source_type": "document",
            },
            tags=payload.get("tags") or [],
            created_at=created_at,
            parent_id=payload.get("document_id"),
        )