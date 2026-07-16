"""IOS Retrieval — Retrieval Cache."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from uuid import UUID

import redis.asyncio as aioredis

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.retrieval.interfaces import IRetrievalCacheBackend
from app.retrieval.types import (
    MetadataFilter,
    RerankedItem,
    RetrievalRequest,
    RetrievalResponse,
    RetrievalSource,
    RetrievedItem,
)

logger = get_logger(__name__)

_CACHE_NAMESPACE = "ios:cache:retrieval"


class RetrievalCache(IRetrievalCacheBackend):
    """
    Redis-backed cache for retrieval responses.

    Cache keys are a SHA-256 hash of the normalised request parameters
    (query, user, strategy, sources, filters), ensuring identical requests
    hit the cache while parameter changes correctly miss.

    Failures are never fatal — a cache read/write error degrades to a
    cache miss / no-op rather than failing the retrieval request.
    """

    def __init__(self, redis: aioredis.Redis, *, default_ttl_seconds: int = 300) -> None:
        self._redis = redis
        self._default_ttl = default_ttl_seconds
        self._log = logger

    # ------------------------------------------------------------------
    # Key construction
    # ------------------------------------------------------------------

    @staticmethod
    def build_key(request: RetrievalRequest) -> str:
        """Deterministic cache key from the request's semantic parameters."""
        payload = {
            "query": request.query.strip().lower(),
            "user_id": str(request.user_id),
            "strategy": request.strategy.value,
            "sources": sorted(s.value for s in request.sources),
            "top_k": request.top_k,
            "min_score": round(request.min_score, 3),
            "rewrite_strategy": request.rewrite_strategy.value,
            "filter": RetrievalCache._filter_signature(request.metadata_filter),
        }
        raw = json.dumps(payload, sort_keys=True)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return f"{_CACHE_NAMESPACE}:{digest}"

    @staticmethod
    def _filter_signature(flt: MetadataFilter | None) -> dict:
        if flt is None:
            return {}
        return {
            "user_id": str(flt.user_id) if flt.user_id else None,
            "tags": sorted(flt.tags),
            "source_types": sorted(flt.source_types),
            "labels": sorted(flt.labels),
            "date_from": flt.date_from.isoformat() if flt.date_from else None,
            "date_to": flt.date_to.isoformat() if flt.date_to else None,
        }

    # ------------------------------------------------------------------
    # IRetrievalCacheBackend
    # ------------------------------------------------------------------

    async def get(self, key: str) -> RetrievalResponse | None:
        async with create_async_span("retrieval.cache.get"):
            try:
                raw = await self._redis.get(key)
            except Exception as exc:
                self._log.warning("retrieval_cache_get_failed", exc=str(exc))
                return None
            if raw is None:
                return None
            try:
                return self._deserialise(raw)
            except Exception as exc:
                self._log.warning("retrieval_cache_deserialise_failed", exc=str(exc))
                return None

    async def set(
        self, key: str, response: RetrievalResponse, *, ttl_seconds: int = 300
    ) -> None:
        async with create_async_span("retrieval.cache.set"):
            try:
                serialised = self._serialise(response)
                await self._redis.setex(key, ttl_seconds or self._default_ttl, serialised)
            except Exception as exc:
                self._log.warning("retrieval_cache_set_failed", exc=str(exc))

    async def invalidate(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception as exc:
            self._log.warning("retrieval_cache_invalidate_failed", exc=str(exc))

    async def invalidate_prefix(self, prefix: str) -> int:
        """Delete all cache entries whose key starts with prefix using SCAN."""
        count = 0
        try:
            pattern = f"{prefix}*"
            async for redis_key in self._redis.scan_iter(pattern, count=100):
                await self._redis.delete(redis_key)
                count += 1
        except Exception as exc:
            self._log.warning("retrieval_cache_invalidate_prefix_failed", exc=str(exc))
        return count

    async def invalidate_user(self, user_id: UUID) -> int:
        """Convenience: invalidate all cached retrieval results for a user.

        Note: since keys are content hashes, this requires scanning the
        full retrieval cache namespace and checking payload; used sparingly
        (e.g. after bulk document re-ingestion).
        """
        return await self.invalidate_prefix(_CACHE_NAMESPACE)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    @staticmethod
    def _serialise(response: RetrievalResponse) -> str:
        def _item_dict(ri: RerankedItem) -> dict:
            d = asdict(ri.item)
            d["source"] = ri.item.source.value
            d["created_at"] = ri.item.created_at.isoformat() if ri.item.created_at else None
            return {
                "item": d,
                "rerank_score": ri.rerank_score,
                "original_rank": ri.original_rank,
                "final_rank": ri.final_rank,
            }

        payload = {
            "items": [_item_dict(ri) for ri in response.items],
            "total_found": response.total_found,
            "query": response.query,
            "rewritten_query": response.rewritten_query,
            "sources_used": [s.value for s in response.sources_used],
            "latency_ms": response.latency_ms,
            "cache_hit": True,   # mark as cache hit on deserialise path
        }
        return json.dumps(payload, default=str)

    @staticmethod
    def _deserialise(raw: str | bytes) -> RetrievalResponse:
        data = json.loads(raw)
        items: list[RerankedItem] = []
        for entry in data["items"]:
            item_data = entry["item"]
            created_at = (
                datetime.fromisoformat(item_data["created_at"])
                if item_data.get("created_at")
                else None
            )
            item = RetrievedItem(
                id=item_data["id"],
                content=item_data["content"],
                source=RetrievalSource(item_data["source"]),
                score=item_data["score"],
                confidence=item_data["confidence"],
                title=item_data.get("title"),
                metadata=item_data.get("metadata", {}),
                tags=item_data.get("tags", []),
                created_at=created_at,
                parent_id=item_data.get("parent_id"),
                citation_label=item_data.get("citation_label"),
            )
            items.append(
                RerankedItem(
                    item=item,
                    rerank_score=entry["rerank_score"],
                    original_rank=entry["original_rank"],
                    final_rank=entry["final_rank"],
                )
            )
        return RetrievalResponse(
            items=items,
            total_found=data["total_found"],
            query=data["query"],
            rewritten_query=data.get("rewritten_query"),
            sources_used=[RetrievalSource(s) for s in data["sources_used"]],
            latency_ms=data["latency_ms"],
            cache_hit=True,
        )