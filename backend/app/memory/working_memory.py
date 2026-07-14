"""IOS Memory — Working Memory Layer."""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis

from app.core.constants import (
    REDIS_NS_WORKING_MEMORY,
    WORKING_MEMORY_MAX_TOKENS,
    WORKING_MEMORY_TTL_SECONDS,
)
from app.memory.base import BaseMemoryLayer
from app.memory.exceptions import WorkingMemoryError, WorkingMemoryFullError
from app.memory.types import (
    MemoryLayerType,
    MemoryPriority,
    MemoryRecord,
    MemorySearchRequest,
    ScoredMemory,
    WorkingMemorySlot,
    WorkingMemoryState,
)


class WorkingMemory(BaseMemoryLayer):
    """Redis-backed per-conversation ephemeral context store."""

    @property
    def layer_type(self) -> MemoryLayerType:
        return MemoryLayerType.WORKING

    def __init__(
        self,
        redis: aioredis.Redis,
        conversation_id: UUID,
        user_id: UUID,
        *,
        token_budget: int = WORKING_MEMORY_MAX_TOKENS,
        ttl_seconds: int = WORKING_MEMORY_TTL_SECONDS,
    ) -> None:
        super().__init__()
        self._redis = redis
        self._conversation_id = conversation_id
        self._user_id = user_id
        self._token_budget = token_budget
        self._ttl = ttl_seconds
        self._hash_key = f"{REDIS_NS_WORKING_MEMORY}:{conversation_id}"

    async def set_slot(
        self,
        key: str,
        value: Any,
        *,
        priority: MemoryPriority = MemoryPriority.NORMAL,
        pinned: bool = False,
        token_override: int | None = None,
    ) -> WorkingMemorySlot:
        async with self._span("set_slot", key=key):
            serialised = json.dumps(value, default=str)
            token_count = token_override or self.estimate_tokens(serialised)
            state = await self._load_state()
            if not pinned:
                projected = state.total_tokens + token_count
                existing = state.slots.get(key)
                if existing:
                    projected -= existing.token_estimate
                if projected > self._token_budget:
                    raise WorkingMemoryFullError(
                        f"Token budget exceeded ({projected}/{self._token_budget}).",
                        details={"key": key, "budget": self._token_budget, "projected": projected},
                    )
            slot = WorkingMemorySlot(key=key, value=value, token_estimate=token_count, priority=priority, pinned=pinned)
            state.slots[key] = slot
            await self._save_state(state)
            return slot

    async def get_slot(self, key: str) -> Any | None:
        state = await self._load_state()
        slot = state.slots.get(key)
        return slot.value if slot else None

    async def delete_slot(self, key: str) -> None:
        state = await self._load_state()
        state.slots.pop(key, None)
        await self._save_state(state)

    async def get_state(self) -> WorkingMemoryState:
        return await self._load_state()

    async def clear(self, *, preserve_pinned: bool = True) -> int:
        async with self._span("clear"):
            state = await self._load_state()
            before = len(state.slots)
            state.slots = {k: v for k, v in state.slots.items() if v.pinned} if preserve_pinned else {}
            state.total_tokens = sum(s.token_estimate for s in state.slots.values())
            await self._save_state(state)
            return before - len(state.slots)

    async def set_summary(self, summary: str, token_count: int) -> None:
        state = await self._load_state()
        state.summary = summary
        state.compression_count += 1
        state.total_tokens = token_count
        await self._save_state(state)

    async def token_usage(self) -> tuple[int, int]:
        state = await self._load_state()
        return state.total_tokens, self._token_budget

    async def evict_lowest_priority(self, target_tokens: int) -> list[str]:
        state = await self._load_state()
        evictable = sorted(
            [(k, v) for k, v in state.slots.items() if not v.pinned],
            key=lambda kv: (kv[1].priority, -kv[1].token_estimate),
        )
        freed, evicted = 0, []
        for key, slot in evictable:
            if freed >= target_tokens:
                break
            del state.slots[key]
            freed += slot.token_estimate
            evicted.append(key)
        state.total_tokens -= freed
        await self._save_state(state)
        return evicted

    async def export_context(self, max_tokens: int | None = None) -> str:
        state = await self._load_state()
        budget = max_tokens or self._token_budget
        parts: list[str] = []
        tokens_used = 0
        if state.summary:
            parts.append(f"[Context Summary]\n{state.summary}")
            tokens_used += self.estimate_tokens(state.summary)
        for slot in sorted(state.slots.values(), key=lambda s: s.priority, reverse=True):
            if tokens_used + slot.token_estimate > budget:
                break
            val_str = json.dumps(slot.value, default=str) if not isinstance(slot.value, str) else slot.value
            parts.append(f"[{slot.key}]\n{val_str}")
            tokens_used += slot.token_estimate
        return "\n\n".join(parts)

    # IMemoryLayer stubs
    async def write(self, record: MemoryRecord) -> MemoryRecord:
        await self.set_slot(record.id.hex, record.content, priority=MemoryPriority(record.priority))
        return record

    async def read(self, record_id: UUID, user_id: UUID) -> MemoryRecord:
        raise NotImplementedError("Use get_slot() for working memory.")

    async def delete(self, record_id: UUID, user_id: UUID) -> None:
        await self.delete_slot(record_id.hex)

    async def search(self, request: MemorySearchRequest) -> list[ScoredMemory]:
        import uuid as _uuid
        state = await self._load_state()
        results = []
        for slot in state.slots.values():
            rec = MemoryRecord(id=_uuid.uuid4(), layer=MemoryLayerType.WORKING, user_id=request.user_id, content=str(slot.value), importance=slot.priority / 10.0)
            results.append(self.composite_score(rec))
        results.sort(key=lambda s: s.score, reverse=True)
        return results[: request.top_k]

    async def list_recent(self, user_id: UUID, *, limit: int = 20, offset: int = 0) -> list[MemoryRecord]:
        return []

    async def count(self, user_id: UUID) -> int:
        state = await self._load_state()
        return len(state.slots)

    async def ping(self) -> bool:
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def _load_state(self) -> WorkingMemoryState:
        try:
            raw = await self._redis.get(self._hash_key)
        except Exception as exc:
            raise WorkingMemoryError(f"Redis read failed: {exc}") from exc
        if raw is None:
            return WorkingMemoryState(conversation_id=self._conversation_id, user_id=self._user_id, token_budget=self._token_budget)
        try:
            data = json.loads(raw)
            state = WorkingMemoryState(
                conversation_id=UUID(data["conversation_id"]),
                user_id=UUID(data["user_id"]),
                token_budget=data.get("token_budget", self._token_budget),
                total_tokens=data.get("total_tokens", 0),
                compression_count=data.get("compression_count", 0),
                summary=data.get("summary"),
            )
            for key, sd in data.get("slots", {}).items():
                state.slots[key] = WorkingMemorySlot(key=key, value=sd["value"], token_estimate=sd.get("token_estimate", 0), priority=MemoryPriority(sd.get("priority", 5)), pinned=sd.get("pinned", False))
            return state
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise WorkingMemoryError(f"Corrupted working memory: {exc}") from exc

    async def _save_state(self, state: WorkingMemoryState) -> None:
        state.total_tokens = sum(s.token_estimate for s in state.slots.values())
        payload = {
            "conversation_id": str(state.conversation_id),
            "user_id": str(state.user_id),
            "token_budget": state.token_budget,
            "total_tokens": state.total_tokens,
            "compression_count": state.compression_count,
            "summary": state.summary,
            "slots": {k: {"value": v.value, "token_estimate": v.token_estimate, "priority": v.priority, "pinned": v.pinned} for k, v in state.slots.items()},
        }
        try:
            await self._redis.setex(self._hash_key, self._ttl, json.dumps(payload, default=str))
        except Exception as exc:
            raise WorkingMemoryError(f"Redis write failed: {exc}") from exc